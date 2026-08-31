"""TRM MCP server exposed to the enterprise AI agent.

The browser continues to call G.AIOS through ``/api/ai/chat``.  After this MCP
server is registered on the G.AIOS agent, the agent can call the tools here to
read or mutate the same TRM database used by the web application.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Any, Literal, Optional
from urllib.parse import urlparse

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.responses import JSONResponse, Response

from .auth import AI_CAPABILITY_RULES, AIPrincipal, has_ai_capability, validate_ai_delegation
from .budget_service import init_budget_and_workflow_db
from .db import connect, now_iso
from .poc import reconcile_work_deviation_notifications
from .rules import BusinessError, DEMAND_TYPES, PRIORITIES, validate_common, validate_description, validate_title


MCP_ACTOR = "gaios-mcp-agent"
MCP_ROLE = "mcp_service"
PROJECT_STATUSES = ("规划中", "实施中", "已暂停", "已完成", "已终止")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DelegationToken = Annotated[
    str,
    Field(
        min_length=40,
        description="TRM当前登录会话签发的短时AI委托令牌；必须原样传入且不得展示",
    ),
]


def _env_true(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _service_actor() -> str:
    return (os.getenv("TRM_MCP_ACTOR") or MCP_ACTOR).strip()[:100] or MCP_ACTOR


def _service_role() -> str:
    return (os.getenv("TRM_MCP_ROLE") or MCP_ROLE).strip()[:100] or MCP_ROLE


def _canonical(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _payload_hash(action: str, payload: dict[str, Any]) -> str:
    return hashlib.sha256(f"{action}:{_canonical(payload)}".encode("utf-8")).hexdigest()


def _confirmation_secret() -> bytes:
    raw = os.getenv("TRM_MCP_CONFIRMATION_SECRET") or os.getenv("TRM_MCP_API_TOKEN") or ""
    if len(raw) < 24:
        raise ValueError("未配置安全的 TRM_MCP_API_TOKEN / TRM_MCP_CONFIRMATION_SECRET（至少24字符）")
    return raw.encode("utf-8")


def _encode_confirmation(action: str, payload: dict[str, Any], principal: AIPrincipal) -> str:
    ttl = max(60, min(int(os.getenv("TRM_MCP_CONFIRMATION_TTL_SECONDS", "600")), 3600))
    body = {
        "action": action,
        "payload_hash": _payload_hash(action, payload),
        "user_id": principal.user_id,
        "exp": int(time.time()) + ttl,
        "nonce": secrets.token_urlsafe(12),
    }
    raw = _canonical(body).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    signature = hmac.new(_confirmation_secret(), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def _verify_confirmation(token: str, action: str, payload: dict[str, Any], principal: AIPrincipal) -> None:
    try:
        encoded, signature = token.split(".", 1)
        expected = hmac.new(_confirmation_secret(), encoded.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        padding = "=" * (-len(encoded) % 4)
        body = json.loads(base64.urlsafe_b64decode(encoded + padding).decode("utf-8"))
    except Exception as exc:
        raise ValueError("确认令牌无效，请重新调用 prepare 工具生成预览") from exc
    if body.get("action") != action or body.get("payload_hash") != _payload_hash(action, payload):
        raise ValueError("确认令牌与当前创建参数不一致，请重新预览并让用户确认")
    if int(body.get("user_id") or 0) != principal.user_id:
        raise ValueError("确认令牌不属于当前登录用户，请重新预览")
    if int(body.get("exp") or 0) < int(time.time()):
        raise ValueError("确认令牌已过期，请重新调用 prepare 工具")


def _ensure_write_enabled() -> None:
    if not _env_true("TRM_MCP_WRITE_ENABLED", False):
        raise ValueError("MCP 写操作未启用；由管理员在服务端设置 TRM_MCP_WRITE_ENABLED=true 后再试")


def _authorize(delegation_token: str, required_capability: str) -> AIPrincipal:
    return validate_ai_delegation(delegation_token, required_capability)


def _business_permission_text(capability: str) -> str:
    return "|".join(AI_CAPABILITY_RULES.get(capability, (capability,)))


def _validate_date(value: Optional[str], label: str) -> Optional[str]:
    value = (value or "").strip() or None
    if value and not DATE_RE.fullmatch(value):
        raise ValueError(f"{label}必须为 YYYY-MM-DD")
    if value:
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(f"{label}不是有效日期") from exc
    return value


def _safe_json(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except Exception:
        return fallback


def _audit_tool(
    conn,
    *,
    tool_name: str,
    operation: str,
    success: bool,
    request_id: str,
    principal: AIPrincipal,
    required_permission: str,
    idempotency_key: str = "",
    object_type: str = "",
    object_id: str = "",
    arguments: Optional[dict[str, Any]] = None,
    result: Optional[dict[str, Any]] = None,
    error: str = "",
) -> None:
    conn.execute(
        """INSERT INTO mcp_tool_calls
        (tool_name,operation,actor,role,user_id,service_actor,required_permission,delegation_id,
         success,request_id,idempotency_key,object_type,object_id,arguments_json,result_json,error,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            tool_name,
            operation,
            principal.username,
            ",".join(principal.role_codes),
            principal.user_id,
            _service_actor(),
            _business_permission_text(required_permission),
            principal.delegation_id,
            1 if success else 0,
            request_id,
            idempotency_key,
            object_type,
            object_id,
            _canonical(arguments or {}),
            _canonical(result or {}),
            error[:1000],
            now_iso(),
        ),
    )


def _audit_business(
    conn,
    *,
    action: str,
    object_type: str,
    object_id: Any,
    request_id: str,
    principal: AIPrincipal,
    required_permission: str,
    demand_id: Optional[int] = None,
    details: Optional[dict[str, Any]] = None,
) -> None:
    conn.execute(
        """INSERT INTO audit_logs
        (demand_id,actor,role,action,object_type,object_id,result,request_id,details,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            demand_id,
            f"{principal.display_name} {principal.username}".strip(),
            ",".join(principal.role_codes),
            action,
            object_type,
            str(object_id),
            "成功",
            request_id,
            _canonical({
                "channel": "mcp",
                "user_id": principal.user_id,
                "roles": list(principal.role_codes),
                "delegation_id": principal.delegation_id,
                "required_permission": _business_permission_text(required_permission),
                "service_actor": _service_actor(),
                **(details or {}),
            }),
            now_iso(),
        ),
    )


def init_trm_mcp_db() -> None:
    """Create MCP-specific audit/idempotency tables without duplicating business data."""
    with connect() as conn:
        # MCP可以独立启动，也必须先具备与页面API相同的工时审批/预算台账字段。
        init_budget_and_workflow_db(conn)
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS mcp_tool_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tool_name TEXT NOT NULL,
                operation TEXT NOT NULL,
                actor TEXT NOT NULL,
                role TEXT NOT NULL,
                user_id INTEGER,
                service_actor TEXT DEFAULT '',
                required_permission TEXT DEFAULT '',
                delegation_id TEXT DEFAULT '',
                success INTEGER NOT NULL,
                request_id TEXT NOT NULL,
                idempotency_key TEXT DEFAULT '',
                object_type TEXT DEFAULT '',
                object_id TEXT DEFAULT '',
                arguments_json TEXT DEFAULT '{}',
                result_json TEXT DEFAULT '{}',
                error TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mcp_idempotency (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tool_name TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(tool_name, idempotency_key)
            );
            CREATE INDEX IF NOT EXISTS idx_mcp_tool_calls_created ON mcp_tool_calls(created_at DESC);
            """
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(mcp_tool_calls)")}
        for name, definition in (
            ("user_id", "INTEGER"),
            ("service_actor", "TEXT DEFAULT ''"),
            ("required_permission", "TEXT DEFAULT ''"),
            ("delegation_id", "TEXT DEFAULT ''"),
        ):
            if name not in columns:
                conn.execute(f"ALTER TABLE mcp_tool_calls ADD COLUMN {name} {definition}")


def _demand_payload(
    title: str,
    description: str,
    demand_type: str,
    budget_sources: list[str],
    priority: str,
    principal: AIPrincipal,
    budget_amount: float,
) -> dict[str, Any]:
    try:
        title = validate_title(title)
        validate_description(description)
        validate_common(demand_type, priority, budget_sources)
    except BusinessError as exc:
        raise ValueError(exc.message) from exc
    if budget_amount < 0 or budget_amount > 999_999_999.99:
        raise ValueError("预算金额必须在0~999999999.99之间")
    applicant = f"{principal.display_name} {principal.username}".strip()
    applicant_code = principal.username
    applicant_dept = principal.department
    sources = list(dict.fromkeys(x.strip() for x in budget_sources if x.strip()))
    with connect() as conn:
        known = {row["budget_name"] for row in conn.execute("SELECT budget_name FROM budgets")}
    unknown = [x for x in sources if x not in known]
    if unknown:
        raise ValueError(f"预算出处不存在：{' 、'.join(unknown)}；请先调用 trm_list_budgets")
    return {
        "title": title,
        "description": description,
        "demand_type": demand_type,
        "budget_sources": sources,
        "priority": priority,
        "applicant": applicant,
        "applicant_code": applicant_code,
        "applicant_dept": applicant_dept,
        "budget_amount": round(float(budget_amount), 2),
    }


def _project_payload(
    name: str,
    manager: str,
    department: str,
    budget_id: Optional[int],
    total_budget: float,
    status: str,
    progress: float,
    start_date: Optional[str],
    end_date: Optional[str],
    description: str,
) -> dict[str, Any]:
    name = name.strip()
    manager = manager.strip()
    if not name or len(name) > 100:
        raise ValueError("项目名称必填且不超过100字")
    if not manager or len(manager) > 100:
        raise ValueError("项目经理必填且不超过100字")
    if len(department) > 100 or len(description) > 5000:
        raise ValueError("所属部门不超过100字，项目描述不超过5000字")
    if status not in PROJECT_STATUSES:
        raise ValueError(f"项目状态必须为：{'/'.join(PROJECT_STATUSES)}")
    if total_budget < 0 or total_budget > 999_999_999.99:
        raise ValueError("项目预算必须在0~999999999.99之间")
    if progress < 0 or progress > 100:
        raise ValueError("项目进度必须在0~100之间")
    start_date = _validate_date(start_date, "计划开始日期")
    end_date = _validate_date(end_date, "计划结束日期")
    if start_date and end_date and start_date > end_date:
        raise ValueError("计划结束日期不能早于计划开始日期")
    if budget_id is not None:
        with connect() as conn:
            if not conn.execute("SELECT id FROM budgets WHERE id=?", (budget_id,)).fetchone():
                raise ValueError("预算ID不存在，请先调用 trm_list_budgets")
    return {
        "name": name,
        "manager": manager,
        "department": department.strip(),
        "budget_id": budget_id,
        "total_budget": round(float(total_budget), 2),
        "status": status,
        "progress": round(float(progress), 2),
        "start_date": start_date,
        "end_date": end_date,
        "description": description,
    }


def _resolve_demand(conn, identifier: str):
    value = identifier.strip()
    if value.isdigit():
        row = conn.execute("SELECT * FROM demands WHERE id=?", (int(value),)).fetchone()
    else:
        row = conn.execute("SELECT * FROM demands WHERE UPPER(demand_no)=UPPER(?)", (value,)).fetchone()
    if not row:
        raise ValueError("需求不存在")
    return row


def _work_plan_payload(identifier: str, estimated_hours: float, expected_completion_date: Optional[str], note: str) -> dict[str, Any]:
    if estimated_hours <= 0 or estimated_hours > 1_000_000:
        raise ValueError("预估工时必须大于0且不超过1000000小时")
    if len(note) > 500:
        raise ValueError("计划说明不能超过500字")
    due_date = _validate_date(expected_completion_date, "计划完成日期")
    with connect() as conn:
        demand = _resolve_demand(conn, identifier)
        if demand["status"] in ("已完成", "已终止"):
            raise ValueError("已关闭需求不能修改工时计划")
        return {
            "demand_id": int(demand["id"]),
            "demand_no": demand["demand_no"],
            "title": demand["title"],
            "estimated_hours": round(float(estimated_hours), 2),
            "expected_completion_date": due_date,
            "note": note.strip(),
        }


def _work_log_payload(
    identifier: str,
    function_point_id: int,
    work_date: str,
    hours: float,
    worker: str,
    task_name: str,
    description: str,
    replace_external: bool,
) -> dict[str, Any]:
    if hours <= 0 or hours > 24:
        raise ValueError("单次登记工时必须大于0且不超过24小时")
    date_value = _validate_date(work_date, "工时日期")
    if date_value and date_value > datetime.now().strftime("%Y-%m-%d"):
        raise ValueError("工时日期不能晚于今天")
    worker = worker.strip()
    if not worker:
        raise ValueError("工时登记人不能为空")
    if len(worker) > 100 or len(task_name) > 200 or len(description) > 500:
        raise ValueError("登记人不超过100字、任务不超过200字、说明不超过500字")
    with connect() as conn:
        demand = _resolve_demand(conn, identifier)
        if demand["status"] == "已终止":
            raise ValueError("已终止需求不能登记工时")
        function_point = conn.execute(
            "SELECT id,fp_no,name,system_name FROM function_points WHERE id=? AND demand_id=?",
            (function_point_id, demand["id"]),
        ).fetchone()
        if not function_point:
            raise ValueError("所选功能点不属于当前需求")
        manual_count = conn.execute(
            "SELECT COUNT(*) c FROM demand_work_logs WHERE demand_id=?", (demand["id"],)
        ).fetchone()["c"]
        external_source = str(demand["actual_hours_source"] or demand["work_hour_source"] or "").startswith("TAPD")
        if external_source and not manual_count and not replace_external:
            raise ValueError("当前实际工时来自TAPD；如需改为人工维护，请明确确认替换外部工时")
        return {
            "demand_id": int(demand["id"]),
            "demand_no": demand["demand_no"],
            "title": demand["title"],
            "function_point_id": int(function_point["id"]),
            "fp_no": function_point["fp_no"],
            "function_point_name": function_point["name"],
            "work_date": date_value,
            "hours": round(float(hours), 2),
            "worker": worker,
            "task_name": task_name.strip(),
            "description": description.strip(),
            "replace_external": bool(replace_external),
        }


trm_mcp = MCPServer(
    name="trm-technology-resource-management",
    title="TRM 科技资源管理工具",
    version="1.2.0",
    description="供企业智能体查询 TRM 数据，并在用户确认后创建项目、需求草稿或维护需求工时。",
    instructions=(
        "每个 trm_* 工具都必须传入TRM事实上下文提供的 delegation_token；"
        "该令牌代表当前登录用户，不得向用户显示、转述或写入日志。"
        "AI不使用独立操作权限；工具会实时读取后台角色的同一套业务权限，用户在页面能做的对应操作，AI才能做。"
        "先查询后操作。创建需求前先调用 trm_list_budgets 核对预算名称，创建项目前先核对预算ID。"
        "任何创建或工时变更都必须先调用对应的 trm_prepare_* 获取预览，将预览展示给用户；"
        "仅在用户明确确认后，用完全相同的字段、确认令牌和唯一幂等键调用 trm_create_*。"
        "不得自行猜测预算项、人员、金额或日期。写操作是服务端受控的。"
        "工具返回的是结构化业务数据，不得将原始JSON直接复制给用户。"
        "调用完成后必须重新组织为简洁中文：结论在前，关键数据在后；列表优先，只在对比多条数据时使用带表头和分隔行的标准Markdown表格。"
        "正常回答不提及MCP、工具名、权限校验、后端实现或委托令牌。"
    ),
)


@trm_mcp.tool(
    title="查询TRM预算",
    description="只读查询预算。返回预算ID、名称、总预算、已使用、剩余可用和执行率等结构化字段。",
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
)
def trm_list_budgets(
    delegation_token: DelegationToken,
    year: Annotated[Optional[int], Field(description="可选预算年份，如2026；不传表示全部")] = None,
) -> dict[str, Any]:
    principal = _authorize(delegation_token, "query.budget")
    request_id = str(uuid.uuid4())
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM budgets WHERE (? IS NULL OR year=?) ORDER BY year DESC,id",
            (year, year),
        ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            total = float(item["total_budget"] or 0)
            used = float(item["used_budget"] or 0)
            item["remaining_budget"] = round(total - used, 2)
            item["execution_rate"] = round(used / total * 100, 2) if total else 0
            item["warning"] = "预算不足" if item["remaining_budget"] < 0 else ("执行率已达95%" if item["execution_rate"] >= 95 else "")
            items.append(item)
        result = {"count": len(items), "items": items}
        _audit_tool(conn, tool_name="trm_list_budgets", operation="read", success=True, request_id=request_id,
                    principal=principal, required_permission="query.budget", arguments={"year": year}, result={"count": len(items)})
    return result


@trm_mcp.tool(
    title="搜索TRM需求",
    description="只读搜索需求。按需求编号、标题、申请人或状态返回结构化列表。",
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
)
def trm_search_demands(
    delegation_token: DelegationToken,
    query: Annotated[str, Field(max_length=100, description="需求编号、标题或申请人关键词；空字符串表示最近需求")] = "",
    status: Annotated[str, Field(max_length=40, description="可选精确状态，如草稿、产品经理审批、已完成")] = "",
    limit: Annotated[int, Field(ge=1, le=100, description="返回条数，1~100")] = 20,
) -> dict[str, Any]:
    principal = _authorize(delegation_token, "query.demand")
    request_id = str(uuid.uuid4())
    wheres: list[str] = []
    params: list[Any] = []
    if query.strip():
        wheres.append("(demand_no LIKE ? OR title LIKE ? OR applicant LIKE ?)")
        like = f"%{query.strip()}%"
        params.extend([like, like, like])
    if status.strip():
        wheres.append("status=?")
        params.append(status.strip())
    where = f" WHERE {' AND '.join(wheres)}" if wheres else ""
    with connect() as conn:
        rows = conn.execute(
            f"""SELECT id,demand_no,title,demand_type,priority,applicant,applicant_dept,
                budget_amount,estimated_amount,status,current_node,tapd_id,tapd_status,
                estimated_hours,actual_hours,created_at,updated_at
                FROM demands{where} ORDER BY id DESC LIMIT ?""",
            (*params, limit),
        ).fetchall()
        result = {"count": len(rows), "items": [dict(row) for row in rows]}
        _audit_tool(conn, tool_name="trm_search_demands", operation="read", success=True, request_id=request_id,
                    principal=principal, required_permission="query.demand",
                    arguments={"query": query, "status": status, "limit": limit}, result={"count": len(rows)})
    return result


@trm_mcp.tool(
    title="获取TRM需求详情",
    description="只读获取需求全生命周期详情，包含附件、审批、功能点、费用分摊和工时偏差。",
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
)
def trm_get_demand(
    delegation_token: DelegationToken,
    identifier: Annotated[str, Field(min_length=1, max_length=40, description="REQ-YYYYMMDD-XXXX 或草稿数字ID")],
) -> dict[str, Any]:
    principal = _authorize(delegation_token, "query.demand")
    request_id = str(uuid.uuid4())
    with connect() as conn:
        if identifier.strip().isdigit():
            row = conn.execute("SELECT * FROM demands WHERE id=?", (int(identifier),)).fetchone()
        else:
            row = conn.execute("SELECT * FROM demands WHERE UPPER(demand_no)=UPPER(?)", (identifier.strip(),)).fetchone()
        if not row:
            raise ValueError("需求不存在")
        item = dict(row)
        item["budget_sources"] = _safe_json(item.get("budget_sources"), [])
        item["attachments"] = [
            {k: x[k] for k in ("id", "original_name", "file_size", "mime_type", "category", "created_at")}
            for x in conn.execute("SELECT * FROM attachments WHERE demand_id=? ORDER BY id", (item["id"],))
        ]
        item["approvals"] = [dict(x) for x in conn.execute("SELECT * FROM approval_records WHERE demand_id=? ORDER BY id", (item["id"],))]
        item["function_points"] = [dict(x) for x in conn.execute("SELECT * FROM function_points WHERE demand_id=? ORDER BY id", (item["id"],))]
        item["allocations"] = [dict(x) for x in conn.execute("SELECT * FROM allocations WHERE demand_id=? ORDER BY id", (item["id"],))]
        estimated = float(item.get("estimated_hours") or 0)
        actual = float(item.get("actual_hours") or 0)
        deviation = max(0.0, (actual - estimated) / estimated * 100) if estimated else 0
        item["work_hour_overrun_rate"] = round(deviation, 2)
        item["work_hour_deviation_rate"] = round(deviation, 2)
        item["work_hour_warning"] = deviation > 30
        item["work_logs"] = [dict(x) for x in conn.execute(
            "SELECT * FROM demand_work_logs WHERE demand_id=? ORDER BY work_date DESC,id DESC", (item["id"],)
        )]
        due_date = item.get("expected_completion_date")
        item["work_plan_overdue"] = bool(
            due_date and due_date < datetime.now().strftime("%Y-%m-%d") and item.get("status") not in ("已完成", "已终止")
        )
        _audit_tool(conn, tool_name="trm_get_demand", operation="read", success=True, request_id=request_id,
                    principal=principal, required_permission="query.demand",
                    arguments={"identifier": identifier}, result={"id": item["id"], "demand_no": item.get("demand_no")})
    return item


@trm_mcp.tool(
    title="预览调整需求工时计划",
    description="只校验、不写入。继承当前用户页面上的功能评估/项目管理权限，返回工时计划变更预览和确认令牌。",
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
)
def trm_prepare_update_work_plan(
    delegation_token: DelegationToken,
    identifier: Annotated[str, Field(min_length=1, max_length=40, description="需求编号或草稿数字ID")],
    estimated_hours: Annotated[float, Field(gt=0, le=1_000_000, description="新的预估工时")],
    expected_completion_date: Annotated[Optional[str], Field(description="计划完成日期 YYYY-MM-DD；可为空")]=None,
    note: Annotated[str, Field(max_length=500, description="计划调整说明")]= "",
) -> dict[str, Any]:
    principal = _authorize(delegation_token, "manage.work_hours")
    request_id = str(uuid.uuid4())
    payload = _work_plan_payload(identifier, estimated_hours, expected_completion_date, note)
    result = {
        "operation": "update_work_plan",
        "will_write": False,
        "preview": payload,
        "warnings": [],
        "confirmation_token": _encode_confirmation("update_work_plan", payload, principal),
        "next_step": "先向用户展示计划变更；用户明确确认后再调用 trm_update_work_plan",
    }
    with connect() as conn:
        _audit_tool(conn, tool_name="trm_prepare_update_work_plan", operation="preview", success=True,
                    request_id=request_id, principal=principal, required_permission="manage.work_hours",
                    arguments=payload, result={"will_write": False})
    return result


@trm_mcp.tool(
    title="调整需求工时计划",
    description="写入TRM真实数据库。必须先预览并取得用户明确确认；同一操作重试必须复用幂等键。",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
def trm_update_work_plan(
    delegation_token: DelegationToken,
    identifier: Annotated[str, Field(min_length=1, max_length=40, description="与预览相同的需求编号或ID")],
    estimated_hours: Annotated[float, Field(gt=0, le=1_000_000, description="与预览相同的预估工时")],
    confirmation_token: Annotated[str, Field(min_length=40, description="预览工具返回的确认令牌")],
    idempotency_key: Annotated[str, Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9._:-]+$", description="此次变更的唯一幂等键")],
    expected_completion_date: Annotated[Optional[str], Field(description="与预览相同的计划完成日期")]=None,
    note: Annotated[str, Field(max_length=500, description="与预览相同的说明")]= "",
) -> dict[str, Any]:
    principal = _authorize(delegation_token, "manage.work_hours")
    _ensure_write_enabled()
    payload = _work_plan_payload(identifier, estimated_hours, expected_completion_date, note)
    _verify_confirmation(confirmation_token, "update_work_plan", payload, principal)
    request_hash = _payload_hash("update_work_plan", payload)
    request_id = str(uuid.uuid4())
    storage_key = f"u{principal.user_id}:{idempotency_key}"
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        prior = conn.execute(
            "SELECT request_hash,result_json FROM mcp_idempotency WHERE tool_name=? AND idempotency_key=?",
            ("trm_update_work_plan", storage_key),
        ).fetchone()
        if prior:
            if prior["request_hash"] != request_hash:
                raise ValueError("该幂等键已用于不同参数，请勿复用")
            replay = json.loads(prior["result_json"])
            replay["idempotent_replay"] = True
            _audit_tool(conn, tool_name="trm_update_work_plan", operation="replay", success=True,
                        request_id=request_id, principal=principal, required_permission="manage.work_hours",
                        idempotency_key=idempotency_key, object_type="demand", object_id=str(payload["demand_id"]),
                        arguments=payload, result=replay)
            return replay
        actor = f"{principal.display_name} {principal.username}".strip()
        now = now_iso()
        conn.execute(
            """UPDATE demands SET estimated_hours=?,expected_completion_date=?,work_hour_source='人工维护',
               work_plan_source='人工维护',work_plan_updated_by=?,work_plan_updated_at=?,updated_at=? WHERE id=?""",
            (payload["estimated_hours"], payload["expected_completion_date"], actor, now, now, payload["demand_id"]),
        )
        reconcile_work_deviation_notifications(conn, payload["demand_id"])
        result = {
            "updated": True,
            "id": payload["demand_id"],
            "demand_no": payload["demand_no"],
            "estimated_hours": payload["estimated_hours"],
            "expected_completion_date": payload["expected_completion_date"],
            "work_plan_source": "人工维护",
            "message": "工时计划已更新并重新计算预警",
            "idempotent_replay": False,
        }
        conn.execute(
            "INSERT INTO mcp_idempotency(tool_name,idempotency_key,request_hash,result_json,created_at) VALUES (?,?,?,?,?)",
            ("trm_update_work_plan", storage_key, request_hash, _canonical(result), now),
        )
        _audit_business(conn, action="MCP维护工时计划", object_type="demand_work_plan",
                        object_id=payload["demand_id"], demand_id=payload["demand_id"], request_id=request_id,
                        principal=principal, required_permission="manage.work_hours",
                        details={"estimated_hours": payload["estimated_hours"], "expected_completion_date": payload["expected_completion_date"], "note": payload["note"]})
        _audit_tool(conn, tool_name="trm_update_work_plan", operation="update", success=True,
                    request_id=request_id, principal=principal, required_permission="manage.work_hours",
                    idempotency_key=idempotency_key, object_type="demand", object_id=str(payload["demand_id"]),
                    arguments=payload, result=result)
    return result


@trm_mcp.tool(
    title="预览登记需求实际工时",
    description="只校验、不写入。继承当前用户页面上的功能评估/项目管理权限，返回工时登记预览和确认令牌。",
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
)
def trm_prepare_log_work_hours(
    delegation_token: DelegationToken,
    identifier: Annotated[str, Field(min_length=1, max_length=40, description="需求编号或草稿数字ID")],
    function_point_id: Annotated[int, Field(gt=0, description="需求下的功能点数字ID，必填")],
    work_date: Annotated[str, Field(description="工时日期 YYYY-MM-DD，不能晚于今天")],
    hours: Annotated[float, Field(gt=0, le=24, description="本次工时，0~24小时")],
    worker: Annotated[str, Field(min_length=1, max_length=100, description="工时登记人")],
    task_name: Annotated[str, Field(max_length=200, description="关联任务")]= "",
    description: Annotated[str, Field(max_length=500, description="工作说明")]= "",
    replace_external: Annotated[bool, Field(description="实际工时来自TAPD时，是否明确切换为人工维护")]=False,
) -> dict[str, Any]:
    principal = _authorize(delegation_token, "manage.work_hours")
    request_id = str(uuid.uuid4())
    payload = _work_log_payload(identifier, function_point_id, work_date, hours, worker, task_name, description, replace_external)
    warnings = ["确认后将以人工登记汇总替代TAPD实际工时"] if replace_external else []
    result = {
        "operation": "log_work_hours",
        "will_write": False,
        "preview": payload,
        "warnings": warnings,
        "confirmation_token": _encode_confirmation("log_work_hours", payload, principal),
        "next_step": "先向用户展示工时登记内容；用户明确确认后再调用 trm_log_work_hours",
    }
    with connect() as conn:
        _audit_tool(conn, tool_name="trm_prepare_log_work_hours", operation="preview", success=True,
                    request_id=request_id, principal=principal, required_permission="manage.work_hours",
                    arguments=payload, result={"will_write": False, "warnings": warnings})
    return result


@trm_mcp.tool(
    title="登记需求实际工时",
    description="写入TRM真实工时明细并自动汇总实际工时、重算超支率与逾期预警。必须先预览并取得用户明确确认。",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
def trm_log_work_hours(
    delegation_token: DelegationToken,
    identifier: Annotated[str, Field(min_length=1, max_length=40, description="与预览相同的需求编号或ID")],
    function_point_id: Annotated[int, Field(gt=0, description="与预览相同的功能点ID")],
    work_date: Annotated[str, Field(description="与预览相同的工时日期")],
    hours: Annotated[float, Field(gt=0, le=24, description="与预览相同的工时")],
    worker: Annotated[str, Field(min_length=1, max_length=100, description="与预览相同的登记人")],
    confirmation_token: Annotated[str, Field(min_length=40, description="预览工具返回的确认令牌")],
    idempotency_key: Annotated[str, Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9._:-]+$", description="此次登记的唯一幂等键")],
    task_name: Annotated[str, Field(max_length=200, description="与预览相同的关联任务")]= "",
    description: Annotated[str, Field(max_length=500, description="与预览相同的工作说明")]= "",
    replace_external: Annotated[bool, Field(description="与预览相同的外部工时替换确认")]=False,
) -> dict[str, Any]:
    principal = _authorize(delegation_token, "manage.work_hours")
    _ensure_write_enabled()
    payload = _work_log_payload(identifier, function_point_id, work_date, hours, worker, task_name, description, replace_external)
    _verify_confirmation(confirmation_token, "log_work_hours", payload, principal)
    request_hash = _payload_hash("log_work_hours", payload)
    request_id = str(uuid.uuid4())
    storage_key = f"u{principal.user_id}:{idempotency_key}"
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        prior = conn.execute(
            "SELECT request_hash,result_json FROM mcp_idempotency WHERE tool_name=? AND idempotency_key=?",
            ("trm_log_work_hours", storage_key),
        ).fetchone()
        if prior:
            if prior["request_hash"] != request_hash:
                raise ValueError("该幂等键已用于不同参数，请勿复用")
            replay = json.loads(prior["result_json"])
            replay["idempotent_replay"] = True
            _audit_tool(conn, tool_name="trm_log_work_hours", operation="replay", success=True,
                        request_id=request_id, principal=principal, required_permission="manage.work_hours",
                        idempotency_key=idempotency_key, object_type="demand_work_log", object_id=str(replay.get("work_log_id") or ""),
                        arguments=payload, result=replay)
            return replay
        actor = f"{principal.display_name} {principal.username}".strip()
        now = now_iso()
        cur = conn.execute(
            """INSERT INTO demand_work_logs
               (demand_id,function_point_id,work_date,hours,worker,task_name,description,source,created_by,
                approval_status,submitted_by,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (payload["demand_id"], payload["function_point_id"], payload["work_date"], payload["hours"],
             payload["worker"], payload["task_name"], payload["description"], "AI人工登记", actor,
             "待审批", actor, now),
        )
        for target_role in ("product_manager", "project_manager"):
            conn.execute(
                """INSERT INTO notifications(demand_id,level,title,content,target_role,event_key,created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (payload["demand_id"], "info", "工时审批待办",
                 f"{actor}提交了{payload['hours']:.2f}小时工时，功能点{payload['fp_no']}，请审批。",
                 target_role, f"work_log_pending:{cur.lastrowid}:{target_role}", now),
            )
        actual = float(conn.execute("SELECT actual_hours FROM demands WHERE id=?", (payload["demand_id"],)).fetchone()["actual_hours"] or 0)
        result = {
            "created": True,
            "work_log_id": int(cur.lastrowid),
            "demand_id": payload["demand_id"],
            "demand_no": payload["demand_no"],
            "hours": payload["hours"],
            "actual_hours_total": round(actual, 2),
            "function_point_id": payload["function_point_id"],
            "fp_no": payload["fp_no"],
            "approval_status": "待审批",
            "actual_hours_source": "审批工时",
            "message": "工时已提交，审批通过后才计入实际工时",
            "idempotent_replay": False,
        }
        conn.execute(
            "INSERT INTO mcp_idempotency(tool_name,idempotency_key,request_hash,result_json,created_at) VALUES (?,?,?,?,?)",
            ("trm_log_work_hours", storage_key, request_hash, _canonical(result), now),
        )
        _audit_business(conn, action="MCP登记实际工时", object_type="demand_work_log",
                        object_id=cur.lastrowid, demand_id=payload["demand_id"], request_id=request_id,
                        principal=principal, required_permission="manage.work_hours",
                        details={"hours": payload["hours"], "work_date": payload["work_date"], "worker": payload["worker"],
                                 "function_point_id": payload["function_point_id"], "task_name": payload["task_name"],
                                 "approval_status": "待审批"})
        _audit_tool(conn, tool_name="trm_log_work_hours", operation="create", success=True,
                    request_id=request_id, principal=principal, required_permission="manage.work_hours",
                    idempotency_key=idempotency_key, object_type="demand_work_log", object_id=str(cur.lastrowid),
                    arguments=payload, result=result)
    return result


@trm_mcp.tool(
    title="搜索TRM项目",
    description="只读搜索项目。按编号、名称、项目经理、部门或状态返回结构化列表。",
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
)
def trm_list_projects(
    delegation_token: DelegationToken,
    query: Annotated[str, Field(max_length=100, description="项目编号/名称/项目经理/部门关键词")] = "",
    status: Annotated[str, Field(max_length=40, description="可选精确项目状态")] = "",
    limit: Annotated[int, Field(ge=1, le=100, description="返回条数，1~100")] = 20,
) -> dict[str, Any]:
    principal = _authorize(delegation_token, "query.project")
    request_id = str(uuid.uuid4())
    wheres: list[str] = []
    params: list[Any] = []
    if query.strip():
        like = f"%{query.strip()}%"
        wheres.append("(project_no LIKE ? OR name LIKE ? OR manager LIKE ? OR department LIKE ?)")
        params.extend([like, like, like, like])
    if status.strip():
        wheres.append("status=?")
        params.append(status.strip())
    where = f" WHERE {' AND '.join(wheres)}" if wheres else ""
    with connect() as conn:
        rows = conn.execute(
            f"""SELECT id,project_no,name,manager,department,budget_id,total_budget,status,
                progress,start_date,end_date,description,created_at,updated_at
                FROM projects{where} ORDER BY updated_at DESC LIMIT ?""",
            (*params, limit),
        ).fetchall()
        result = {"count": len(rows), "items": [dict(row) for row in rows]}
        _audit_tool(conn, tool_name="trm_list_projects", operation="read", success=True, request_id=request_id,
                    principal=principal, required_permission="query.project",
                    arguments={"query": query, "status": status, "limit": limit}, result={"count": len(rows)})
    return result


@trm_mcp.tool(
    title="获取TRM项目360详情",
    description="只读获取项目360详情，包含基本信息、任务、里程碑、风险以及已授权的预算和需求数据。",
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
)
def trm_get_project(
    delegation_token: DelegationToken,
    identifier: Annotated[str, Field(min_length=1, max_length=40, description="PRJ-YYYY-XXXX 或数字ID")],
) -> dict[str, Any]:
    principal = _authorize(delegation_token, "query.project")
    request_id = str(uuid.uuid4())
    with connect() as conn:
        if identifier.strip().isdigit():
            row = conn.execute("SELECT * FROM projects WHERE id=?", (int(identifier),)).fetchone()
        else:
            row = conn.execute("SELECT * FROM projects WHERE UPPER(project_no)=UPPER(?)", (identifier.strip(),)).fetchone()
        if not row:
            raise ValueError("项目不存在")
        item = dict(row)
        pid = item["id"]
        item["budget"] = dict(conn.execute("SELECT * FROM budgets WHERE id=?", (item["budget_id"],)).fetchone()) if has_ai_capability(principal.permissions, "query.budget") and item.get("budget_id") and conn.execute("SELECT 1 FROM budgets WHERE id=?", (item["budget_id"],)).fetchone() else None
        item["tasks"] = [dict(x) for x in conn.execute("SELECT * FROM project_tasks WHERE project_id=? ORDER BY id", (pid,))]
        item["milestones"] = [dict(x) for x in conn.execute("SELECT * FROM milestones WHERE project_id=? ORDER BY planned_date,id", (pid,))]
        item["risks"] = [dict(x) for x in conn.execute("SELECT * FROM project_risks WHERE project_id=? ORDER BY id DESC", (pid,))]
        demand_rows = [dict(x) for x in conn.execute("SELECT * FROM demands ORDER BY id DESC")] if has_ai_capability(principal.permissions, "query.demand") else []
        budget_name = item["budget"]["budget_name"] if item["budget"] else ""
        item["demands"] = [x for x in demand_rows if budget_name and budget_name in _safe_json(x.get("budget_sources"), [])]
        _audit_tool(conn, tool_name="trm_get_project", operation="read", success=True, request_id=request_id,
                    principal=principal, required_permission="query.project",
                    arguments={"identifier": identifier}, result={"id": item["id"], "project_no": item.get("project_no")})
    return item


@trm_mcp.tool(
    title="预览创建TRM需求",
    description=(
        "Validation/dry-run only，直接继承当前角色的 demand.create（新建需求）权限。验证需求字段和预算出处，返回预览与短期确认令牌。"
        "只有用户明确确认该预览后，才能调用 trm_create_demand。预算>5万元时会提示后续提交审批前补传预算依据。"
    ),
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
)
def trm_prepare_create_demand(
    delegation_token: DelegationToken,
    title: Annotated[str, Field(min_length=1, max_length=100, description="需求标题，1~100字")],
    description: Annotated[str, Field(max_length=5000, description="需求描述，最多5000字")],
    demand_type: Annotated[Literal["业务流程优化", "智能化改造项目", "系统功能新增"], Field(description="需求类型")],
    budget_sources: Annotated[list[str], Field(max_length=20, description="预算名称列表，名称必须来自 trm_list_budgets；可为空")],
    priority: Annotated[Literal["高", "中", "低"], Field(description="优先级")],
    budget_amount: Annotated[float, Field(ge=0, le=999999999.99, description="申请预算金额，元")] = 0,
) -> dict[str, Any]:
    principal = _authorize(delegation_token, "create.demand")
    request_id = str(uuid.uuid4())
    payload = _demand_payload(title, description, demand_type, budget_sources, priority, principal, budget_amount)
    warnings = []
    if payload["budget_amount"] > 50_000:
        warnings.append("预算超过5万元：草稿可创建，但提交审批前必须上传‘预算依据’附件")
    result = {
        "operation": "create_demand_draft",
        "will_write": False,
        "preview": payload,
        "warnings": warnings,
        "confirmation_token": _encode_confirmation("create_demand", payload, principal),
        "next_step": "先向用户展示 preview 和 warnings；用户明确确认后再调用 trm_create_demand",
    }
    with connect() as conn:
        _audit_tool(conn, tool_name="trm_prepare_create_demand", operation="preview", success=True,
                    request_id=request_id, principal=principal, required_permission="create.demand",
                    arguments=payload, result={"will_write": False, "warnings": warnings})
    return result


@trm_mcp.tool(
    title="创建TRM需求草稿",
    description=(
        "Write tool。在TRM真实数据库创建需求草稿。必须在同一轮操作中先调用 trm_prepare_create_demand，"
        "将预览展示给用户并获得明确确认。所有业务字段必须与预览完全一致。"
        "idempotency_key 必须在这次用户操作中唯一；重试时复用同一键可防止重复创建。提交模式由MCP服务端控制。"
    ),
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
def trm_create_demand(
    delegation_token: DelegationToken,
    title: Annotated[str, Field(min_length=1, max_length=100, description="与预览相同的需求标题")],
    description: Annotated[str, Field(max_length=5000, description="与预览相同的需求描述")],
    demand_type: Annotated[Literal["业务流程优化", "智能化改造项目", "系统功能新增"], Field(description="与预览相同的需求类型")],
    budget_sources: Annotated[list[str], Field(max_length=20, description="与预览相同的预算名称列表")],
    priority: Annotated[Literal["高", "中", "低"], Field(description="与预览相同的优先级")],
    confirmation_token: Annotated[str, Field(min_length=40, description="prepare 工具返回的未过期确认令牌")],
    idempotency_key: Annotated[str, Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9._:-]+$", description="此次用户创建操作的唯一幂等键；网络重试必须复用同一值")],
    budget_amount: Annotated[float, Field(ge=0, le=999999999.99, description="与预览相同的预算金额，元")] = 0,
) -> dict[str, Any]:
    principal = _authorize(delegation_token, "create.demand")
    _ensure_write_enabled()
    payload = _demand_payload(title, description, demand_type, budget_sources, priority, principal, budget_amount)
    _verify_confirmation(confirmation_token, "create_demand", payload, principal)
    request_hash = _payload_hash("create_demand", payload)
    request_id = str(uuid.uuid4())
    storage_key = f"u{principal.user_id}:{idempotency_key}"
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        prior = conn.execute(
            "SELECT request_hash,result_json FROM mcp_idempotency WHERE tool_name=? AND idempotency_key=?",
            ("trm_create_demand", storage_key),
        ).fetchone()
        if prior:
            if prior["request_hash"] != request_hash:
                raise ValueError("该幂等键已用于不同参数，请勿复用")
            replay = json.loads(prior["result_json"])
            replay["idempotent_replay"] = True
            _audit_tool(conn, tool_name="trm_create_demand", operation="replay", success=True,
                        request_id=request_id, principal=principal, required_permission="create.demand",
                        idempotency_key=idempotency_key, object_type="demand", object_id=str(replay.get("id") or ""),
                        arguments=payload, result=replay)
            return replay
        now = now_iso()
        cur = conn.execute(
            """INSERT INTO demands
            (title,description,demand_type,budget_sources,priority,applicant,applicant_code,
             applicant_dept,budget_amount,status,current_node,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                payload["title"], payload["description"], payload["demand_type"],
                json.dumps(payload["budget_sources"], ensure_ascii=False), payload["priority"],
                payload["applicant"], payload["applicant_code"], payload["applicant_dept"],
                payload["budget_amount"], "草稿", "草稿", now, now,
            ),
        )
        did = int(cur.lastrowid)
        result = {
            "created": True,
            "id": did,
            "demand_no": None,
            "title": payload["title"],
            "status": "草稿",
            "current_node": "草稿",
            "message": "需求草稿已创建；REQ-YYYYMMDD-XXXX 将在提交审批时生成",
            "warnings": (["预算超过5万元，提交前必须上传预算依据"] if payload["budget_amount"] > 50_000 else []),
            "idempotent_replay": False,
        }
        conn.execute(
            "INSERT INTO mcp_idempotency(tool_name,idempotency_key,request_hash,result_json,created_at) VALUES (?,?,?,?,?)",
            ("trm_create_demand", storage_key, request_hash, _canonical(result), now),
        )
        _audit_business(conn, action="MCP创建需求草稿", object_type="demand", object_id=did,
                        demand_id=did, request_id=request_id, principal=principal,
                        required_permission="create.demand", details={"idempotency_key": idempotency_key})
        _audit_tool(conn, tool_name="trm_create_demand", operation="create", success=True,
                    request_id=request_id, principal=principal, required_permission="create.demand",
                    idempotency_key=idempotency_key, object_type="demand", object_id=str(did), arguments=payload, result=result)
    return result


@trm_mcp.tool(
    title="预览创建TRM项目",
    description="Validation/dry-run only。直接继承当前角色的 initiative.create（新建立项）或 project（项目管理）权限。校验项目字段并返回确认预览。",
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
)
def trm_prepare_create_project(
    delegation_token: DelegationToken,
    name: Annotated[str, Field(min_length=1, max_length=100, description="项目名称")],
    manager: Annotated[str, Field(min_length=1, max_length=100, description="项目经理")],
    department: Annotated[str, Field(max_length=100, description="所属部门")] = "",
    budget_id: Annotated[Optional[int], Field(ge=1, description="预算ID，必须来自 trm_list_budgets；可不关联")] = None,
    total_budget: Annotated[float, Field(ge=0, le=999999999.99, description="项目总预算，元")] = 0,
    status: Annotated[Literal["规划中", "实施中", "已暂停", "已完成", "已终止"], Field(description="项目状态")] = "规划中",
    progress: Annotated[float, Field(ge=0, le=100, description="项目进度百分比")] = 0,
    start_date: Annotated[Optional[str], Field(description="计划开始日期 YYYY-MM-DD")] = None,
    end_date: Annotated[Optional[str], Field(description="计划结束日期 YYYY-MM-DD")] = None,
    description: Annotated[str, Field(max_length=5000, description="项目描述")] = "",
) -> dict[str, Any]:
    principal = _authorize(delegation_token, "create.project")
    request_id = str(uuid.uuid4())
    payload = _project_payload(name, manager, department, budget_id, total_budget, status, progress, start_date, end_date, description)
    result = {
        "operation": "create_project",
        "will_write": False,
        "preview": payload,
        "warnings": [],
        "confirmation_token": _encode_confirmation("create_project", payload, principal),
        "next_step": "先向用户展示 preview；用户明确确认后再调用 trm_create_project",
    }
    with connect() as conn:
        _audit_tool(conn, tool_name="trm_prepare_create_project", operation="preview", success=True,
                    request_id=request_id, principal=principal, required_permission="create.project",
                    arguments=payload, result={"will_write": False})
    return result


@trm_mcp.tool(
    title="创建TRM项目",
    description=(
        "Write tool。在TRM真实数据库创建项目。必须先调用 trm_prepare_create_project，将预览展示给用户并获得明确确认。"
        "所有业务字段必须与预览完全一致。idempotency_key 必须此次操作唯一，重试时复用同一值。"
        "提交模式由MCP服务端控制。"
    ),
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False),
)
def trm_create_project(
    delegation_token: DelegationToken,
    name: Annotated[str, Field(min_length=1, max_length=100, description="与预览相同的项目名称")],
    manager: Annotated[str, Field(min_length=1, max_length=100, description="与预览相同的项目经理")],
    confirmation_token: Annotated[str, Field(min_length=40, description="prepare 工具返回的未过期确认令牌")],
    idempotency_key: Annotated[str, Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9._:-]+$", description="此次用户创建操作唯一键；网络重试复用同一值")],
    department: Annotated[str, Field(max_length=100, description="与预览相同的所属部门")] = "",
    budget_id: Annotated[Optional[int], Field(ge=1, description="与预览相同的预算ID")] = None,
    total_budget: Annotated[float, Field(ge=0, le=999999999.99, description="与预览相同的项目总预算")]=0,
    status: Annotated[Literal["规划中", "实施中", "已暂停", "已完成", "已终止"], Field(description="与预览相同的项目状态")] = "规划中",
    progress: Annotated[float, Field(ge=0, le=100, description="与预览相同的项目进度")]=0,
    start_date: Annotated[Optional[str], Field(description="与预览相同的计划开始日期")]=None,
    end_date: Annotated[Optional[str], Field(description="与预览相同的计划结束日期")]=None,
    description: Annotated[str, Field(max_length=5000, description="与预览相同的项目描述")] = "",
) -> dict[str, Any]:
    principal = _authorize(delegation_token, "create.project")
    _ensure_write_enabled()
    payload = _project_payload(name, manager, department, budget_id, total_budget, status, progress, start_date, end_date, description)
    _verify_confirmation(confirmation_token, "create_project", payload, principal)
    request_hash = _payload_hash("create_project", payload)
    request_id = str(uuid.uuid4())
    storage_key = f"u{principal.user_id}:{idempotency_key}"
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        prior = conn.execute(
            "SELECT request_hash,result_json FROM mcp_idempotency WHERE tool_name=? AND idempotency_key=?",
            ("trm_create_project", storage_key),
        ).fetchone()
        if prior:
            if prior["request_hash"] != request_hash:
                raise ValueError("该幂等键已用于不同参数，请勿复用")
            replay = json.loads(prior["result_json"])
            replay["idempotent_replay"] = True
            _audit_tool(conn, tool_name="trm_create_project", operation="replay", success=True,
                        request_id=request_id, principal=principal, required_permission="create.project",
                        idempotency_key=idempotency_key, object_type="project", object_id=str(replay.get("id") or ""),
                        arguments=payload, result=replay)
            return replay
        year = datetime.now().year
        prefix = f"PRJ-{year}-"
        last = conn.execute("SELECT project_no FROM projects WHERE project_no LIKE ? ORDER BY project_no DESC LIMIT 1", (f"{prefix}%",)).fetchone()
        seq = int(last["project_no"].split("-")[-1]) + 1 if last and last["project_no"] else 1
        project_no = f"{prefix}{seq:04d}"
        now = now_iso()
        cur = conn.execute(
            """INSERT INTO projects
            (project_no,name,manager,department,budget_id,total_budget,status,progress,start_date,end_date,
             description,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                project_no, payload["name"], payload["manager"], payload["department"], payload["budget_id"],
                payload["total_budget"], payload["status"], payload["progress"], payload["start_date"],
                payload["end_date"], payload["description"], now, now,
            ),
        )
        pid = int(cur.lastrowid)
        result = {
            "created": True,
            "id": pid,
            "project_no": project_no,
            "name": payload["name"],
            "status": payload["status"],
            "message": "项目已创建",
            "idempotent_replay": False,
        }
        conn.execute(
            "INSERT INTO mcp_idempotency(tool_name,idempotency_key,request_hash,result_json,created_at) VALUES (?,?,?,?,?)",
            ("trm_create_project", storage_key, request_hash, _canonical(result), now),
        )
        _audit_business(conn, action="MCP创建项目", object_type="project", object_id=pid,
                        request_id=request_id, principal=principal, required_permission="create.project",
                        details={"idempotency_key": idempotency_key, "project_no": project_no})
        _audit_tool(conn, tool_name="trm_create_project", operation="create", success=True,
                    request_id=request_id, principal=principal, required_permission="create.project",
                    idempotency_key=idempotency_key, object_type="project", object_id=str(pid), arguments=payload, result=result)
    return result


class MCPBearerAuth:
    """Small ASGI guard for a static service token stored only in server env."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        if scope.get("method") == "OPTIONS":
            await Response(status_code=204)(scope, receive, send)
            return
        expected = os.getenv("TRM_MCP_API_TOKEN", "")
        if len(expected) < 24:
            await JSONResponse(
                {"error": "mcp_not_configured", "message": "服务端未配置 TRM_MCP_API_TOKEN（至少24字符）"},
                status_code=503,
            )(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        raw = headers.get(b"authorization", b"").decode("latin-1")
        supplied = raw[7:] if raw.lower().startswith("bearer ") else ""
        if not hmac.compare_digest(supplied, expected):
            await JSONResponse(
                {"error": "unauthorized", "message": "需要有效的 Authorization: Bearer <TRM_MCP_API_TOKEN>"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _allowed_hosts() -> list[str]:
    raw = os.getenv("TRM_MCP_ALLOWED_HOSTS", "127.0.0.1:*,localhost:*,[::1]:*")
    hosts = [item.strip() for item in raw.split(",") if item.strip()]
    render_host = os.getenv("RENDER_EXTERNAL_HOSTNAME", "").strip()
    public_url = os.getenv("TRM_PUBLIC_BASE_URL", "").strip()
    public_host = urlparse(public_url).netloc if public_url else ""
    for host in (render_host, public_host):
        if host and host not in hosts:
            hosts.append(host)
    return hosts


def _allowed_origins() -> list[str]:
    raw = os.getenv(
        "TRM_MCP_ALLOWED_ORIGINS",
        "http://127.0.0.1:*,http://localhost:*,http://[::1]:*,https://adk.gazellio.com",
    )
    origins = [item.strip() for item in raw.split(",") if item.strip()]
    public_url = os.getenv("TRM_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if public_url and public_url not in origins:
        origins.append(public_url)
    return origins


class RestartableMCPApplication:
    """Rebuild the SDK session manager for each ASGI lifespan.

    The SDK intentionally allows one ``run()`` per manager.  Uvicorn has one
    lifespan, while the bundled tests open multiple TestClient lifespans in one
    process, so the mounted delegate must be refreshed at each startup.
    """

    def __init__(self):
        self._app = None
        self._session_manager = None

    def _rebuild(self) -> None:
        starlette_app = trm_mcp.streamable_http_app(
            streamable_http_path="/",
            json_response=True,
            stateless_http=True,
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=_allowed_hosts(),
                allowed_origins=_allowed_origins(),
            ),
        )
        self._app = MCPBearerAuth(starlette_app)
        self._session_manager = trm_mcp.session_manager

    @asynccontextmanager
    async def run(self):
        self._rebuild()
        async with self._session_manager.run():
            yield

    async def __call__(self, scope, receive, send):
        if self._app is None:
            await JSONResponse(
                {"error": "mcp_starting", "message": "MCP服务正在启动"},
                status_code=503,
            )(scope, receive, send)
            return
        await self._app(scope, receive, send)


mcp_asgi_app = RestartableMCPApplication()


def public_mcp_status() -> dict[str, Any]:
    """Non-secret deployment status for the admin integration screen."""
    token_ready = len(os.getenv("TRM_MCP_API_TOKEN", "")) >= 24
    return {
        "enabled": token_ready,
        "write_enabled": token_ready and _env_true("TRM_MCP_WRITE_ENABLED", False),
        "transport": "http_streamable",
        "endpoint_path": "/mcp/",
        "authentication": "Bearer token" if token_ready else "未配置",
        "user_authorization": "live_role_delegation",
        "tool_count": 13,
        "service_actor": _service_actor(),
    }

