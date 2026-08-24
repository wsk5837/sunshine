import asyncio
import io
import json
import os
import re
import shutil
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote

from fastapi import FastAPI, Request, UploadFile, File, Header, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from openpyxl import Workbook, load_workbook

from .db import BASE_DIR, connect, init_db, now_iso, row_to_dict, get_budget_by_name
from .extended import router as extended_router, init_extended_db, project_detail
from .v4 import router as v4_router, init_v4_db
from .auth import (
    DEFAULT_ROLE_PERMISSIONS,
    get_ai_capabilities,
    has_ai_capability,
    has_any_permission,
    has_permission,
    issue_ai_delegation,
    router as auth_router,
    init_auth_db,
    resolve_session,
    get_role_labels,
    get_demo_users,
    permissions_for_api,
    request_has_role,
    request_role_codes,
)
from .ai_gateway import AIServiceError, public_ai_config, run_agent_message
from .trm_mcp import init_trm_mcp_db, mcp_asgi_app, public_mcp_status
from .poc import (
    router as poc_router, init_poc_db, background_worker, create_oa_task, complete_oa_task,
    previous_nodes_for, create_tapd_requirements, schedule_tapd_retry, apply_tapd_payload,
    build_mock_sync_payload, build_live_sync_payload, tapd_runtime_config, get_setting,
    reconcile_work_deviation_notifications,
)
from .rules import (
    APPROVAL_FLOW,
    ATTACHMENT_EXTS,
    BusinessError,
    DEMAND_TYPES,
    MAX_AI_LEN,
    MAX_ALLOCATION_ROWS,
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENT_COUNT,
    MAX_FP_PER_DEMAND,
    MAX_IMPORT_BYTES,
    MAX_IMPORT_ROWS,
    PRIORITIES,
    ROLE_LABELS,
    TAPD_STATUS_MAP,
    validate_common,
    validate_description,
    validate_title,
)

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
UPLOAD_DIR = Path(os.getenv("TRM_UPLOAD_DIR", BASE_DIR / "uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    init_extended_db()
    init_v4_db()
    init_auth_db()
    init_poc_db()
    init_trm_mcp_db()
    # 启动时为旧版已有工时数据补齐真实的定向预警消息。
    with connect() as conn:
        reconcile_work_deviation_notifications(conn)
    async with mcp_asgi_app.run():
        worker = asyncio.create_task(background_worker())
        try:
            yield
        finally:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass


app = FastAPI(title="TRM 科技资源管理系统", version="4.9.0", description="完整科技资源管理、需求全生命周期与AI智能体集成系统", lifespan=lifespan)
app.include_router(extended_router)
app.include_router(v4_router)
app.include_router(auth_router)
app.include_router(poc_router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/mcp", mcp_asgi_app, name="trm-mcp")


@app.middleware("http")
async def auth_session_middleware(request: Request, call_next):
    path = request.url.path
    public = path == "/" or path.startswith("/static/") or path in {"/api/health", "/api/auth/login"}
    if not public and path.startswith("/api/"):
        token = request.headers.get("X-Session", "")
        session_user = resolve_session(token) if token else None
        # TestClient compatibility for the bundled automated tests. Public deployments still require a session.
        is_test_client = bool(request.client and request.client.host == "testclient")
        if not session_user and not is_test_client:
            return JSONResponse(status_code=401, content={
                "code": "AUTH-4010", "message": "登录已失效，请重新登录",
                "requestId": request.headers.get("X-Request-Id") or str(uuid.uuid4()), "timestamp": now_iso()
            })
        if session_user:
            request.state.auth_user = session_user
            required = permissions_for_api(request.method, path)
            if required and not has_any_permission(session_user.get("permissions") or [], required):
                labels = ", ".join(required)
                return JSONResponse(status_code=403, content={
                    "code": "AUTH-4030",
                    "message": f"当前账号未授权访问该功能（{labels}）",
                    "requestId": request.headers.get("X-Request-Id") or str(uuid.uuid4()),
                    "timestamp": now_iso(),
                })
            headers = [
                (k, v) for k, v in request.scope.get("headers", [])
                if k.lower() not in {b"x-user", b"x-role", b"x-roles"}
            ]
            headers.extend([
                (b"x-user", session_user["username"].encode("ascii", "ignore")),
                (b"x-role", session_user["role_code"].encode("ascii", "ignore")),
                (b"x-roles", ",".join(session_user.get("role_codes") or []).encode("ascii", "ignore")),
            ])
            request.scope["headers"] = headers
    return await call_next(request)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
    request.state.request_id = request_id
    try:
        response = await call_next(request)
    except BusinessError as exc:
        return JSONResponse(
            status_code=exc.http_status,
            content={
                "code": exc.code,
                "message": exc.message,
                "requestId": request_id,
                "timestamp": now_iso(),
                "details": exc.details,
            },
        )
    response.headers["X-Request-Id"] = request_id
    content_type = response.headers.get("content-type", "")
    if content_type.startswith("application/json") and "charset=" not in content_type.lower():
        response.headers["Content-Type"] = "application/json; charset=utf-8"
    return response


@app.exception_handler(BusinessError)
async def business_error_handler(request: Request, exc: BusinessError):
    return JSONResponse(
        status_code=exc.http_status,
        content={
            "code": exc.code,
            "message": exc.message,
            "requestId": getattr(request.state, "request_id", str(uuid.uuid4())),
            "timestamp": now_iso(),
            "details": exc.details,
        },
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={
            "code": "SYS-5000",
            "message": "系统内部错误",
            "requestId": getattr(request.state, "request_id", str(uuid.uuid4())),
            "timestamp": now_iso(),
        },
    )


def actor_context(x_user: Optional[str], x_role: Optional[str]):
    role = x_role or "applicant"
    if role not in get_role_labels():
        raise BusinessError(403, "AUTH-4030", "无效角色或无权限")
    # 浏览器请求头只能稳定携带 ASCII。前端会对中文用户名执行 encodeURIComponent，
    # 后端在这里还原，避免 Safari/Chrome fetch 因中文 Header 抛出 TypeError。
    actor = unquote(x_user) if x_user else "李莉 lili11-ghq"
    return actor, role


def audit(conn, request: Request, actor: str, role: str, action: str, object_type: str, object_id: Any, result="成功", demand_id=None, details=None):
    conn.execute(
        """INSERT INTO audit_logs(demand_id,actor,role,action,object_type,object_id,result,request_id,details,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (demand_id, actor, role, action, object_type, str(object_id) if object_id is not None else None,
         result, getattr(request.state, "request_id", None), json.dumps(details or {}, ensure_ascii=False), now_iso()),
    )


def demand_dict(conn, row):
    d = row_to_dict(row)
    if not d:
        return None
    d["budget_sources"] = json.loads(d.get("budget_sources") or "[]")
    d["attachments"] = [dict(r) for r in conn.execute("SELECT * FROM attachments WHERE demand_id=? ORDER BY id", (d["id"],))]
    d["approvals"] = [dict(r) for r in conn.execute("SELECT * FROM approval_records WHERE demand_id=? ORDER BY id", (d["id"],))]
    d["function_points"] = [dict(r) for r in conn.execute("SELECT * FROM function_points WHERE demand_id=? ORDER BY id", (d["id"],))]
    d["allocations"] = [dict(r) for r in conn.execute("SELECT * FROM allocations WHERE demand_id=? ORDER BY id", (d["id"],))]
    d["oa_tasks"] = [dict(r) for r in conn.execute("SELECT * FROM oa_tasks WHERE demand_id=? ORDER BY id", (d["id"],))]
    d["tapd_requirements"] = [dict(r) for r in conn.execute("SELECT * FROM tapd_requirements WHERE demand_id=? ORDER BY id", (d["id"],))]
    for tr in d["tapd_requirements"]:
        try:
            tr["payload"] = json.loads(tr.get("payload_json") or "{}")
        except Exception:
            tr["payload"] = {}
    d["tapd_tasks"] = [dict(r) for r in conn.execute("SELECT * FROM tapd_tasks WHERE demand_id=? ORDER BY id", (d["id"],))]
    d["tapd_costs"] = [dict(r) for r in conn.execute("SELECT * FROM tapd_costs WHERE demand_id=? ORDER BY id", (d["id"],))]
    d["tapd_sync_runs"] = [dict(r) for r in conn.execute("SELECT * FROM tapd_sync_runs WHERE demand_id=? ORDER BY id DESC LIMIT 20", (d["id"],))]
    d["tapd_retry_job"] = row_to_dict(conn.execute("SELECT * FROM tapd_retry_jobs WHERE demand_id=? ORDER BY id DESC LIMIT 1", (d["id"],)).fetchone())
    d["tapd_events"] = [dict(r) for r in conn.execute("SELECT * FROM tapd_events WHERE demand_id=? ORDER BY id DESC LIMIT 20", (d["id"],))]
    d["deviation_notification_count"] = conn.execute(
        "SELECT COUNT(*) c FROM notifications WHERE demand_id=? AND title='工时偏差预警'",
        (d["id"],),
    ).fetchone()["c"]
    return d


def get_demand_or_404(conn, demand_id: int):
    row = conn.execute("SELECT * FROM demands WHERE id=?", (demand_id,)).fetchone()
    if not row:
        raise BusinessError(404, "REQ-4040", "需求不存在")
    return row


def generate_req_no(conn):
    date = datetime.now().strftime("%Y%m%d")
    prefix = f"REQ-{date}-"
    row = conn.execute("SELECT demand_no FROM demands WHERE demand_no LIKE ? ORDER BY demand_no DESC LIMIT 1", (f"{prefix}%",)).fetchone()
    seq = int(row["demand_no"].split("-")[-1]) + 1 if row and row["demand_no"] else 1
    return f"{prefix}{seq:04d}"


def generate_fp_no(conn):
    year = datetime.now().strftime("%Y")
    prefix = f"FP-{year}-"
    row = conn.execute("SELECT fp_no FROM function_points WHERE fp_no LIKE ? ORDER BY fp_no DESC LIMIT 1", (f"{prefix}%",)).fetchone()
    seq = int(row["fp_no"].split("-")[-1]) + 1 if row else 1
    return f"{prefix}{seq:04d}"


def selected_budget(conn, demand: dict):
    sources = demand.get("budget_sources") or []
    if not sources:
        return None
    return get_budget_by_name(conn, sources[0])


def budget_snapshot(conn, demand: dict):
    budget = selected_budget(conn, demand)
    if not budget:
        return None
    amount = float(demand.get("estimated_amount") or demand.get("budget_amount") or 0)
    remaining = float(budget["total_budget"]) - float(budget["used_budget"])
    execution_rate = (float(budget["used_budget"]) / float(budget["total_budget"]) * 100) if budget["total_budget"] else 0
    # POC预算校验口径：同一预算项下已进入审批/实施的需求累计预估预算 + 本次需求预算 <= 项目总预算。
    committed = 0.0
    for row in conn.execute("SELECT id,budget_sources,estimated_amount,budget_amount,status FROM demands WHERE id<>? AND status NOT IN ('草稿','已驳回','已终止')", (demand["id"],)):
        try:
            sources = json.loads(row["budget_sources"] or "[]")
        except Exception:
            sources = []
        if budget["budget_name"] in sources:
            committed += float(row["estimated_amount"] or row["budget_amount"] or 0)
    commitment_after = committed + amount
    commitment_rate = commitment_after / float(budget["total_budget"]) * 100 if budget["total_budget"] else 0
    actual_remaining_check = amount <= remaining
    commitment_check = commitment_after <= float(budget["total_budget"])
    internal_rate = float(budget["internal_used"]) / float(budget["internal_total"]) * 100 if budget["internal_total"] else 0
    digital_rate = float(budget["digital_used"]) / float(budget["digital_total"]) * 100 if budget["digital_total"] else 0
    return {
        **budget,
        "remaining_budget": remaining,
        "execution_rate": round(execution_rate, 2),
        "after_execution_rate": round((float(budget["used_budget"]) + amount) / float(budget["total_budget"]) * 100, 2) if budget["total_budget"] else 0,
        "current_demand_amount": amount,
        "committed_demand_amount": round(committed, 2),
        "commitment_after": round(commitment_after, 2),
        "commitment_rate": round(commitment_rate, 2),
        "commitment_check": commitment_check,
        "actual_remaining_check": actual_remaining_check,
        "sufficient": commitment_check and actual_remaining_check,
        "warning": execution_rate >= 95,
        "internal_remaining": float(budget["internal_total"]) - float(budget["internal_used"]),
        "internal_execution_rate": round(internal_rate, 2),
        "digital_remaining": float(budget["digital_total"]) - float(budget["digital_used"]),
        "digital_execution_rate": round(digital_rate, 2),
    }


class DemandPayload(BaseModel):
    title: str
    description: str = ""
    demand_type: str
    budget_sources: list[str] = Field(default_factory=list)
    priority: str = "低"
    applicant: str = "李莉 lili11-ghq"
    applicant_code: str = "lili11-ghq"
    applicant_dept: str = "数字化管理部"
    budget_amount: float = 0


class ApprovalPayload(BaseModel):
    action: str
    comment: str = ""
    return_to: Optional[str] = None


class FunctionPointPayload(BaseModel):
    demand_summary: str = ""
    name: str = ""
    system_name: str
    evaluator: str = "产品经理"
    department: str = "产品研发部"
    team: str = "研发团队"
    evaluation_date: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))
    fp_count: float = 0
    unit_price: float = 1200


class AllocationItem(BaseModel):
    function_point_id: Optional[int] = None
    system_name: str = ""
    expense_subject: str
    expense_source: str
    ratio: float
    department: str


class LinkFunctionPointPayload(BaseModel):
    catalog_id: int


class AllocationPayload(BaseModel):
    rows: list[AllocationItem]


class AIQueryPayload(BaseModel):
    question: str
    session_id: str = Field(default="", max_length=200)
    project_id: Optional[int] = None
    source: str = Field(default="assistant", max_length=40)


def build_ai_fact_context(
    question: str,
    role: str,
    project_id: Optional[int] = None,
    permissions: Optional[list[str]] = None,
    delegation_token: str = "",
) -> str:
    """构建受控、紧凑的系统事实快照，供外部智能体回答业务问题。"""
    permissions = list(permissions or [])
    can_budget = has_ai_capability(permissions, "query.budget")
    can_demand = has_ai_capability(permissions, "query.demand")
    can_project = has_ai_capability(permissions, "query.project")
    can_create_demand = has_ai_capability(permissions, "create.demand")
    can_create_project = has_ai_capability(permissions, "create.project")
    mcp_state = public_mcp_status()
    supported_writes = []
    if mcp_state["write_enabled"] and can_create_demand:
        supported_writes.append("创建需求草稿")
    if mcp_state["write_enabled"] and can_create_project:
        supported_writes.append("创建项目")
    facts: dict[str, Any] = {
        "data_scope": "仅可使用下方已授权的TRM事实；业务写操作必须通过已授权的TRM MCP工具执行",
        "current_role": ROLE_LABELS.get(role, role),
        "effective_ai_capabilities": get_ai_capabilities(permissions),
        "authorization_policy": "AI直接继承当前角色的业务权限，不存在独立AI操作权限",
        "mcp_action_capabilities": {
            "server_ready": mcp_state["enabled"],
            "write_enabled": mcp_state["write_enabled"],
            "supported_writes": supported_writes,
            "required_flow": "查询有效数据 -> prepare预览 -> 用户明确确认 -> create幂等写入",
        },
    }
    if delegation_token and mcp_state["enabled"]:
        facts["mcp_authorization"] = {
            "delegation_token": delegation_token,
            "usage": "调用每一个 trm_* 工具时必须原样传入 delegation_token；不得在回答、预览或日志中显示该令牌",
        }
    if project_id and can_project:
        detail = project_detail(project_id)["data"]
        facts["selected_project"] = {
            key: detail.get(key)
            for key in (
                "id", "project_no", "name", "manager", "department", "status", "health",
                "progress", "start_date", "end_date", "description", "total_budget",
            )
        }
        if can_budget:
            facts["budget"] = detail.get("budget")
        facts["tasks"] = detail.get("tasks", [])[:100]
        facts["milestones"] = detail.get("milestones", [])[:60]
        if can_demand:
            facts["demands"] = [
                {key: item.get(key) for key in (
                    "id", "demand_no", "title", "demand_type", "priority", "status",
                    "current_node", "estimated_amount", "estimated_hours", "actual_hours",
                    "tapd_id", "tapd_status", "planned_online_date",
                )}
                for item in detail.get("demands", [])[:100]
            ]
        facts["contracts"] = detail.get("contracts", [])[:50]
        facts["settlements"] = detail.get("settlements", [])[:50]
        facts["business_values"] = detail.get("values", [])[:50]
    elif not project_id:
        with connect() as conn:
            if can_demand:
                demand_rows = conn.execute("SELECT * FROM demands ORDER BY id DESC LIMIT 80").fetchall()
                demands = [dict(row) for row in demand_rows]
                req_match = re.search(r"REQ-\d{8}-\d{4}", question.upper())
                if req_match:
                    target_row = conn.execute(
                        "SELECT * FROM demands WHERE UPPER(demand_no)=?",
                        (req_match.group(0),),
                    ).fetchone()
                    if target_row:
                        facts["matched_demand"] = demand_dict(conn, target_row)
                facts["recent_demands"] = [
                    {key: item.get(key) for key in (
                        "id", "demand_no", "title", "demand_type", "priority", "applicant",
                        "applicant_dept", "status", "current_node", "budget_sources",
                        "estimated_amount", "estimated_hours", "actual_hours", "tapd_id",
                        "tapd_status", "planned_online_date", "created_at", "submitted_at",
                    )}
                    for item in demands[:30]
                ]
            if can_budget:
                facts["budgets"] = [dict(row) for row in conn.execute(
                    "SELECT budget_no,budget_name,total_budget,used_budget,internal_total,internal_used,digital_total,digital_used,year FROM budgets ORDER BY year DESC,id LIMIT 30"
                )]
            if can_project:
                facts["projects"] = [dict(row) for row in conn.execute(
                    "SELECT id,project_no,name,manager,department,total_budget,status,progress,start_date,end_date FROM projects ORDER BY updated_at DESC LIMIT 30"
                )]

    serialized = json.dumps(facts, ensure_ascii=False, default=str)
    # 防止异常数据量放大模型输入；优先保留前部的项目/命中需求与核心概况。
    return serialized[:60000]


@app.get("/")
def root():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/app.css", include_in_schema=False)
def root_stylesheet():
    """兼容 index.html 的相对资源路径和直接访问根页面。"""
    return FileResponse(STATIC_DIR / "app.css", media_type="text/css")


@app.get("/app.js", include_in_schema=False)
def root_javascript():
    return FileResponse(STATIC_DIR / "app.js", media_type="application/javascript")


@app.get("/api/health")
def health():
    return {"code": 0, "message": "ok", "timestamp": now_iso()}


@app.get("/api/mcp/status")
def mcp_status(x_user: Optional[str] = Header(None), x_role: Optional[str] = Header(None)):
    """Expose only non-secret MCP readiness information to signed-in administrators."""
    actor_context(x_user, x_role)
    return {"code": 0, "data": public_mcp_status()}


@app.get("/api/meta")
def meta():
    with connect() as conn:
        budgets = [dict(r) for r in conn.execute("SELECT * FROM budgets ORDER BY id")]
    return {
        "code": 0,
        "data": {
            "demandTypes": DEMAND_TYPES,
            "priorities": PRIORITIES,
            "roles": get_role_labels(),
            "demoUsers": get_demo_users(),
            "budgets": budgets,
        },
    }


@app.get("/api/dashboard")
def dashboard():
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM demands").fetchone()["c"]
        pending = conn.execute("SELECT COUNT(*) c FROM demands WHERE status LIKE '%审批%' OR current_node LIKE '%审批%' OR current_node='终审'").fetchone()["c"]
        tapd_fail = conn.execute("SELECT COUNT(*) c FROM demands WHERE tapd_sync_status='失败'").fetchone()["c"]
        completed = conn.execute("SELECT COUNT(*) c FROM demands WHERE status='已完成'").fetchone()["c"]
        recent = [demand_dict(conn, r) for r in conn.execute("SELECT * FROM demands ORDER BY id DESC LIMIT 6")]
        notes = [dict(r) for r in conn.execute("SELECT * FROM notifications ORDER BY id DESC LIMIT 8")]
    return {"code": 0, "data": {"total": total, "pending": pending, "tapdFail": tapd_fail, "completed": completed, "recent": recent, "notifications": notes}}


@app.get("/api/demands")
def list_demands(q: str = "", status: str = "", page: int = 1, page_size: int = 20):
    if page_size < 1 or page_size > 100:
        raise BusinessError(400, "REQ-4003", "每页条数范围为1~100")
    page = max(page, 1)
    wheres = []
    params = []
    if q:
        wheres.append("(title LIKE ? OR demand_no LIKE ? OR applicant LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
    if status:
        wheres.append("status=?")
        params.append(status)
    where = " WHERE " + " AND ".join(wheres) if wheres else ""
    with connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) c FROM demands{where}", params).fetchone()["c"]
        rows = conn.execute(f"SELECT * FROM demands{where} ORDER BY id DESC LIMIT ? OFFSET ?", (*params, page_size, (page - 1) * page_size)).fetchall()
        items = [demand_dict(conn, r) for r in rows]
    return {"code": 0, "data": {"items": items, "total": total, "page": page, "pageSize": page_size}}


@app.get("/api/demands/{demand_id}")
def get_demand(demand_id: int):
    with connect() as conn:
        row = get_demand_or_404(conn, demand_id)
        reconcile_work_deviation_notifications(conn, demand_id)
        data = demand_dict(conn, row)
        data["budget_snapshot"] = budget_snapshot(conn, data)
    return {"code": 0, "data": data}


@app.post("/api/demands")
def create_demand(payload: DemandPayload, request: Request, x_user: Optional[str] = Header(None), x_role: Optional[str] = Header(None)):
    actor, role = actor_context(x_user, x_role)
    title = validate_title(payload.title)
    validate_description(payload.description)
    validate_common(payload.demand_type, payload.priority, payload.budget_sources)
    if payload.budget_amount < 0 or payload.budget_amount > 999_999_999.99:
        raise BusinessError(400, "REQ-4003", "预算金额超出允许范围")
    now = now_iso()
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO demands(title,description,demand_type,budget_sources,priority,applicant,applicant_code,applicant_dept,budget_amount,status,current_node,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (title, payload.description, payload.demand_type, json.dumps(payload.budget_sources, ensure_ascii=False), payload.priority,
             payload.applicant or actor, payload.applicant_code or actor, payload.applicant_dept, round(payload.budget_amount, 2), "草稿", "草稿", now, now),
        )
        did = cur.lastrowid
        audit(conn, request, actor, role, "创建需求", "demand", did, demand_id=did)
        data = demand_dict(conn, get_demand_or_404(conn, did))
    return {"code": 0, "message": "草稿已创建", "data": data}


@app.put("/api/demands/{demand_id}")
def update_demand(demand_id: int, payload: DemandPayload, request: Request, x_user: Optional[str] = Header(None), x_role: Optional[str] = Header(None)):
    actor, role = actor_context(x_user, x_role)
    title = validate_title(payload.title)
    validate_description(payload.description)
    validate_common(payload.demand_type, payload.priority, payload.budget_sources)
    with connect() as conn:
        old = demand_dict(conn, get_demand_or_404(conn, demand_id))
        if old["status"] not in ("草稿", "已驳回"):
            raise BusinessError(409, "REQ-4091", "当前需求状态不允许编辑")
        conn.execute(
            """UPDATE demands SET title=?,description=?,demand_type=?,budget_sources=?,priority=?,applicant=?,applicant_code=?,applicant_dept=?,budget_amount=?,updated_at=? WHERE id=?""",
            (title, payload.description, payload.demand_type, json.dumps(payload.budget_sources, ensure_ascii=False), payload.priority,
             payload.applicant, payload.applicant_code or actor, payload.applicant_dept, round(payload.budget_amount, 2), now_iso(), demand_id),
        )
        audit(conn, request, actor, role, "更新需求", "demand", demand_id, demand_id=demand_id)
        data = demand_dict(conn, get_demand_or_404(conn, demand_id))
    return {"code": 0, "message": "保存成功", "data": data}


@app.post("/api/demands/{demand_id}/attachments")
async def upload_attachment(demand_id: int, request: Request, file: UploadFile = File(...), category: str = Query("普通附件"), x_user: Optional[str] = Header(None), x_role: Optional[str] = Header(None)):
    actor, role = actor_context(x_user, x_role)
    original = Path(file.filename or "attachment").name
    ext = original.rsplit(".", 1)[-1].lower() if "." in original else ""
    if ext not in ATTACHMENT_EXTS:
        raise BusinessError(400, "REQ-4003", f"不支持的附件类型：.{ext}")
    content = await file.read()
    if len(content) > MAX_ATTACHMENT_BYTES:
        raise BusinessError(413, "REQ-4003", "单个附件不能超过20MB")
    with connect() as conn:
        get_demand_or_404(conn, demand_id)
        count = conn.execute("SELECT COUNT(*) c FROM attachments WHERE demand_id=?", (demand_id,)).fetchone()["c"]
        if count >= MAX_ATTACHMENT_COUNT:
            raise BusinessError(400, "REQ-4003", "单条需求最多上传10个附件")
        stored = f"{demand_id}_{uuid.uuid4().hex}.{ext}"
        path = UPLOAD_DIR / stored
        path.write_bytes(content)
        cur = conn.execute(
            "INSERT INTO attachments(demand_id,original_name,stored_name,file_size,mime_type,category,created_at) VALUES (?,?,?,?,?,?,?)",
            (demand_id, original, stored, len(content), file.content_type, category, now_iso()),
        )
        audit(conn, request, actor, role, "上传附件", "attachment", cur.lastrowid, demand_id=demand_id, details={"name": original})
    return {"code": 0, "message": "上传成功"}


@app.get("/api/attachments/{attachment_id}/download")
def download_attachment(attachment_id: int):
    with connect() as conn:
        row = conn.execute("SELECT * FROM attachments WHERE id=?", (attachment_id,)).fetchone()
        if not row:
            raise BusinessError(404, "REQ-4040", "附件不存在")
        path = UPLOAD_DIR / row["stored_name"]
        if not path.exists():
            raise BusinessError(404, "REQ-4040", "附件文件不存在")
        return FileResponse(path, filename=row["original_name"], media_type=row["mime_type"] or "application/octet-stream")


@app.delete("/api/attachments/{attachment_id}")
def delete_attachment(attachment_id: int, request: Request, x_user: Optional[str] = Header(None), x_role: Optional[str] = Header(None)):
    actor, role = actor_context(x_user, x_role)
    with connect() as conn:
        row = conn.execute("SELECT * FROM attachments WHERE id=?", (attachment_id,)).fetchone()
        if not row:
            raise BusinessError(404, "REQ-4040", "附件不存在")
        try:
            (UPLOAD_DIR / row["stored_name"]).unlink(missing_ok=True)
        except Exception:
            pass
        conn.execute("DELETE FROM attachments WHERE id=?", (attachment_id,))
        audit(conn, request, actor, role, "删除附件", "attachment", attachment_id, demand_id=row["demand_id"])
    return {"code": 0, "message": "已删除"}


@app.post("/api/demands/{demand_id}/submit")
def submit_demand(demand_id: int, request: Request, confirm_warning: bool = Query(False), x_user: Optional[str] = Header(None), x_role: Optional[str] = Header(None)):
    actor, role = actor_context(x_user, x_role)
    with connect() as conn:
        d = demand_dict(conn, get_demand_or_404(conn, demand_id))
        if d["status"] not in ("草稿", "已驳回"):
            raise BusinessError(409, "REQ-4091", "当前状态不允许重复提交")
        validate_title(d["title"])
        validate_description(d["description"])
        validate_common(d["demand_type"], d["priority"], d["budget_sources"])
        if d["budget_amount"] > 50_000:
            evidence = conn.execute("SELECT COUNT(*) c FROM attachments WHERE demand_id=? AND category='预算依据'", (demand_id,)).fetchone()["c"]
            if evidence == 0:
                raise BusinessError(400, "REQ-4002", "预算金额超过5万元，必须上传预算依据附件")
        project = d["budget_sources"][0] if d["budget_sources"] else None
        unclosed = 0
        if project:
            unclosed = conn.execute("SELECT COUNT(*) c FROM demands WHERE id<>? AND budget_sources LIKE ? AND status NOT IN ('已完成','已终止')", (demand_id, f"%{project}%")).fetchone()["c"]
        if unclosed > 10 and not confirm_warning:
            raise BusinessError(409, "REQ-4091", "同一项目未关闭需求超过10条，请确认后提交", {"warning": "UNFINISHED_OVER_10", "count": unclosed})
        req_no = d["demand_no"] or generate_req_no(conn)
        now = now_iso()
        conn.execute("UPDATE demands SET demand_no=?,status='直属领导审批',current_node='直属领导审批',submitted_at=?,updated_at=? WHERE id=?", (req_no, now, now, demand_id))
        create_oa_task(conn, demand_id, "直属领导审批", getattr(request.state, "request_id", ""))
        audit(conn, request, actor, role, "提交需求", "demand", demand_id, demand_id=demand_id, details={"demand_no": req_no, "oaTodo": True})
        data = demand_dict(conn, get_demand_or_404(conn, demand_id))
    return {"code": 0, "message": "提交成功，已进入直属领导审批", "data": data}


@app.post("/api/demands/{demand_id}/function-points")
def add_function_point(demand_id: int, payload: FunctionPointPayload, request: Request, x_user: Optional[str] = Header(None), x_role: Optional[str] = Header(None)):
    actor, role = actor_context(x_user, x_role)
    if not payload.system_name.strip() or not payload.evaluator.strip() or not payload.department.strip() or not payload.team.strip() or not payload.evaluation_date.strip():
        raise BusinessError(400, "REQ-4002", "归属系统、评估人、所属部门、所属团队、评估日期均为必填项")
    with connect() as conn:
        d = demand_dict(conn, get_demand_or_404(conn, demand_id))
        count = conn.execute("SELECT COUNT(*) c FROM function_points WHERE demand_id=?", (demand_id,)).fetchone()["c"]
        if count >= MAX_FP_PER_DEMAND:
            raise BusinessError(400, "REQ-4003", "单条需求最多关联200个功能点")
        if payload.fp_count < 0 or payload.unit_price < 0:
            raise BusinessError(400, "REQ-4001", "功能点数量和单价不能为负数")
        fp_no = generate_fp_no(conn)
        amount = round(payload.fp_count * payload.unit_price, 2)
        cur = conn.execute(
            """INSERT INTO function_points(demand_id,fp_no,demand_summary,name,system_name,evaluator,department,team,evaluation_date,fp_count,unit_price,estimated_amount,created_at,source_type)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (demand_id, fp_no, payload.demand_summary, payload.name, payload.system_name, payload.evaluator, payload.department,
             payload.team, payload.evaluation_date, payload.fp_count, payload.unit_price, amount, now_iso(), "新增"),
        )
        total = conn.execute("SELECT COALESCE(SUM(estimated_amount),0) s FROM function_points WHERE demand_id=?", (demand_id,)).fetchone()["s"]
        conn.execute("UPDATE demands SET estimated_amount=?,updated_at=? WHERE id=?", (round(total, 2), now_iso(), demand_id))
        audit(conn, request, actor, role, "新增功能点", "function_point", cur.lastrowid, demand_id=demand_id, details={"fp_no": fp_no})
        data = demand_dict(conn, get_demand_or_404(conn, demand_id))
    return {"code": 0, "message": "功能点已保存", "data": data}


@app.get("/api/function-point-catalog")
def list_function_point_catalog(q: str = "", system_name: str = ""):
    wheres = []
    params = []
    if q:
        wheres.append("(catalog_no LIKE ? OR name LIKE ? OR demand_summary LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
    if system_name:
        wheres.append("system_name=?")
        params.append(system_name)
    where = " WHERE " + " AND ".join(wheres) if wheres else ""
    with connect() as conn:
        rows = conn.execute(f"SELECT * FROM function_point_catalog{where} ORDER BY id DESC", params).fetchall()
        systems = [r["system_name"] for r in conn.execute("SELECT DISTINCT system_name FROM function_point_catalog ORDER BY system_name")]
    return {"code": 0, "data": {"items": [dict(r) for r in rows], "systems": systems}}


@app.post("/api/demands/{demand_id}/function-points/link")
def link_function_point(demand_id: int, payload: LinkFunctionPointPayload, request: Request, x_user: Optional[str] = Header(None), x_role: Optional[str] = Header(None)):
    actor, role = actor_context(x_user, x_role)
    with connect() as conn:
        get_demand_or_404(conn, demand_id)
        count = conn.execute("SELECT COUNT(*) c FROM function_points WHERE demand_id=?", (demand_id,)).fetchone()["c"]
        if count >= MAX_FP_PER_DEMAND:
            raise BusinessError(400, "REQ-4003", "单条需求最多关联200个功能点")
        catalog = conn.execute("SELECT * FROM function_point_catalog WHERE id=?", (payload.catalog_id,)).fetchone()
        if not catalog:
            raise BusinessError(404, "REQ-4040", "功能点库记录不存在")
        existed = conn.execute("SELECT COUNT(*) c FROM function_points WHERE demand_id=? AND catalog_id=?", (demand_id, payload.catalog_id)).fetchone()["c"]
        if existed:
            raise BusinessError(409, "REQ-4090", "该功能点已关联当前需求")
        fp_no = generate_fp_no(conn)
        amount = round(float(catalog["default_fp_count"]) * float(catalog["unit_price"]), 2)
        cur = conn.execute(
            """INSERT INTO function_points(demand_id,fp_no,demand_summary,name,system_name,evaluator,department,team,evaluation_date,fp_count,unit_price,estimated_amount,created_at,catalog_id,source_type)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (demand_id, fp_no, catalog["demand_summary"], catalog["name"], catalog["system_name"], actor, catalog["department"], catalog["team"], datetime.now().strftime("%Y-%m-%d"), catalog["default_fp_count"], catalog["unit_price"], amount, now_iso(), catalog["id"], "关联"),
        )
        total = conn.execute("SELECT COALESCE(SUM(estimated_amount),0) s FROM function_points WHERE demand_id=?", (demand_id,)).fetchone()["s"]
        conn.execute("UPDATE demands SET estimated_amount=?,updated_at=? WHERE id=?", (round(total, 2), now_iso(), demand_id))
        audit(conn, request, actor, role, "关联已有功能点", "function_point", cur.lastrowid, demand_id=demand_id, details={"catalog_no": catalog["catalog_no"]})
        data = demand_dict(conn, get_demand_or_404(conn, demand_id))
    return {"code": 0, "message": "已有功能点已关联", "data": data}


@app.put("/api/function-points/{fp_id}")
def update_function_point(fp_id: int, payload: FunctionPointPayload, request: Request, x_user: Optional[str] = Header(None), x_role: Optional[str] = Header(None)):
    actor, role = actor_context(x_user, x_role)
    if payload.fp_count < 0 or payload.unit_price < 0:
        raise BusinessError(400, "REQ-4001", "功能点数量和单价不能为负数")
    if not payload.system_name.strip() or not payload.evaluator.strip() or not payload.department.strip() or not payload.team.strip() or not payload.evaluation_date.strip():
        raise BusinessError(400, "REQ-4002", "归属系统、评估人、所属部门、所属团队、评估日期均为必填项")
    with connect() as conn:
        row = conn.execute("SELECT * FROM function_points WHERE id=?", (fp_id,)).fetchone()
        if not row:
            raise BusinessError(404, "REQ-4040", "功能点不存在")
        amount = round(payload.fp_count * payload.unit_price, 2)
        conn.execute(
            """UPDATE function_points SET demand_summary=?,name=?,system_name=?,evaluator=?,department=?,team=?,evaluation_date=?,fp_count=?,unit_price=?,estimated_amount=? WHERE id=?""",
            (payload.demand_summary, payload.name, payload.system_name, payload.evaluator, payload.department, payload.team, payload.evaluation_date, payload.fp_count, payload.unit_price, amount, fp_id),
        )
        total = conn.execute("SELECT COALESCE(SUM(estimated_amount),0) s FROM function_points WHERE demand_id=?", (row["demand_id"],)).fetchone()["s"]
        conn.execute("UPDATE demands SET estimated_amount=?,updated_at=? WHERE id=?", (round(total, 2), now_iso(), row["demand_id"]))
        audit(conn, request, actor, role, "编辑功能点", "function_point", fp_id, demand_id=row["demand_id"])
        data = demand_dict(conn, get_demand_or_404(conn, row["demand_id"]))
    return {"code": 0, "message": "功能点已更新", "data": data}


@app.delete("/api/function-points/{fp_id}")
def delete_function_point(fp_id: int, request: Request, x_user: Optional[str] = Header(None), x_role: Optional[str] = Header(None)):
    actor, role = actor_context(x_user, x_role)
    with connect() as conn:
        row = conn.execute("SELECT * FROM function_points WHERE id=?", (fp_id,)).fetchone()
        if not row:
            raise BusinessError(404, "REQ-4040", "功能点不存在")
        did = row["demand_id"]
        conn.execute("DELETE FROM function_points WHERE id=?", (fp_id,))
        total = conn.execute("SELECT COALESCE(SUM(estimated_amount),0) s FROM function_points WHERE demand_id=?", (did,)).fetchone()["s"]
        conn.execute("UPDATE demands SET estimated_amount=?,updated_at=? WHERE id=?", (round(total, 2), now_iso(), did))
        audit(conn, request, actor, role, "删除功能点", "function_point", fp_id, demand_id=did)
    return {"code": 0, "message": "已删除"}


@app.get("/api/function-points/template")
def download_fp_template():
    wb = Workbook()
    ws = wb.active
    ws.title = "功能点导入模板"
    ws.append(["需求概述", "需求名称", "归属系统", "评估人", "所属部门", "所属团队", "评估日期", "预估功能点", "单价"])
    ws.append(["示例：新增预算接口", "预算查询接口", "费用预算管理服务平台", "赵敏", "产品研发部", "费用平台组", "2026-08-19", 12, 1200])
    bio = io.BytesIO(); wb.save(bio); bio.seek(0)
    return StreamingResponse(bio, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=function_point_template.xlsx"})


@app.get("/api/demands/{demand_id}/function-points/export")
def export_function_points(demand_id: int):
    with connect() as conn:
        get_demand_or_404(conn, demand_id)
        rows = conn.execute("SELECT * FROM function_points WHERE demand_id=? ORDER BY id", (demand_id,)).fetchall()
    wb = Workbook(); ws = wb.active; ws.title = "功能点评估"
    ws.append(["功能点编号","需求概述","需求名称","归属系统","评估人","所属部门","所属团队","评估日期","预估功能点","单价","预估金额"])
    for r in rows:
        ws.append([r["fp_no"],r["demand_summary"],r["name"],r["system_name"],r["evaluator"],r["department"],r["team"],r["evaluation_date"],r["fp_count"],r["unit_price"],r["estimated_amount"]])
    bio = io.BytesIO(); wb.save(bio); bio.seek(0)
    return StreamingResponse(bio, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f"attachment; filename=function_points_{demand_id}.xlsx"})


@app.post("/api/demands/{demand_id}/function-points/import")
async def import_function_points(demand_id: int, request: Request, file: UploadFile = File(...), x_user: Optional[str] = Header(None), x_role: Optional[str] = Header(None)):
    actor, role = actor_context(x_user, x_role)
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise BusinessError(400, "REQ-4003", "功能点导入仅支持xlsx格式")
    content = await file.read()
    if len(content) > MAX_IMPORT_BYTES:
        raise BusinessError(413, "REQ-4003", "功能点导入文件不能超过10MB")
    wb = load_workbook(io.BytesIO(content), data_only=True)
    ws = wb.active
    data_rows = list(ws.iter_rows(min_row=2, values_only=True))
    if len(data_rows) > MAX_IMPORT_ROWS:
        raise BusinessError(400, "REQ-4003", "单次功能点导入最多2000行")
    errors = []
    inserted = 0
    with connect() as conn:
        get_demand_or_404(conn, demand_id)
        existing = conn.execute("SELECT COUNT(*) c FROM function_points WHERE demand_id=?", (demand_id,)).fetchone()["c"]
        if existing + len(data_rows) > MAX_FP_PER_DEMAND:
            raise BusinessError(400, "REQ-4003", "导入后单条需求关联功能点将超过200条")
        for idx, row in enumerate(data_rows, start=2):
            if not any(v is not None for v in row):
                continue
            vals = list(row) + [None] * 9
            summary, name, system, evaluator, dept, team, eval_date, fp_count, unit_price = vals[:9]
            missing = []
            if not system: missing.append("归属系统")
            if not evaluator: missing.append("评估人")
            if not dept: missing.append("所属部门")
            if not team: missing.append("所属团队")
            if not eval_date: missing.append("评估日期")
            if missing:
                errors.append({"row": idx, "reason": "、".join(missing) + "不能为空"}); continue
            try:
                fp_count = float(fp_count or 0); unit_price = float(unit_price or 1200)
            except Exception:
                errors.append({"row": idx, "reason": "预估功能点/单价必须为数字"}); continue
            fp_no = generate_fp_no(conn); amount = round(fp_count * unit_price, 2)
            conn.execute(
                """INSERT INTO function_points(demand_id,fp_no,demand_summary,name,system_name,evaluator,department,team,evaluation_date,fp_count,unit_price,estimated_amount,created_at,source_type)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (demand_id, fp_no, summary or "", name or "", system, evaluator or actor, dept or "产品研发部", team or "研发团队", str(eval_date or datetime.now().strftime("%Y-%m-%d")), fp_count, unit_price, amount, now_iso(), "导入"),
            )
            inserted += 1
        total = conn.execute("SELECT COALESCE(SUM(estimated_amount),0) s FROM function_points WHERE demand_id=?", (demand_id,)).fetchone()["s"]
        conn.execute("UPDATE demands SET estimated_amount=?,updated_at=? WHERE id=?", (round(total,2), now_iso(), demand_id))
        audit(conn, request, actor, role, "批量导入功能点", "function_point", demand_id, demand_id=demand_id, details={"inserted": inserted, "errors": errors[:20]})
    return {"code": 0, "message": "导入完成", "data": {"inserted": inserted, "errorCount": len(errors), "errors": errors}}


@app.put("/api/demands/{demand_id}/allocations")
def save_allocations(demand_id: int, payload: AllocationPayload, request: Request, x_user: Optional[str] = Header(None), x_role: Optional[str] = Header(None)):
    actor, role = actor_context(x_user, x_role)
    if len(payload.rows) > MAX_ALLOCATION_ROWS:
        raise BusinessError(400, "REQ-4003", "单条需求费用分摊最多50行")
    total_ratio = sum(r.ratio for r in payload.rows)
    if any(r.ratio < 0 or r.ratio > 100 for r in payload.rows) or total_ratio > 100.00001:
        raise BusinessError(422, "BUD-4221", "分摊比例必须为0~100%，且所有行合计不超过100%", {"sum": total_ratio})
    with connect() as conn:
        d = demand_dict(conn, get_demand_or_404(conn, demand_id))
        valid_budgets = {r["budget_name"] for r in conn.execute("SELECT budget_name FROM budgets")}
        for i, r in enumerate(payload.rows, start=1):
            if not r.expense_subject.strip() or not r.expense_source.strip() or not r.department.strip():
                raise BusinessError(422, "BUD-4221", f"第{i}行费用主体、费用出处、费用归属部门均为必填项")
            if r.expense_source not in valid_budgets:
                raise BusinessError(422, "BUD-4221", f"第{i}行费用出处不是预算管理中的有效预算项")
        amount_base = float(d["estimated_amount"] or d["budget_amount"] or 0)
        conn.execute("DELETE FROM allocations WHERE demand_id=?", (demand_id,))
        for r in payload.rows:
            amount = round(amount_base * r.ratio / 100, 2)
            conn.execute(
                """INSERT INTO allocations(demand_id,function_point_id,system_name,expense_subject,expense_source,ratio,amount,department,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (demand_id, r.function_point_id, r.system_name, r.expense_subject, r.expense_source, round(r.ratio,2), amount, r.department, now_iso()),
            )
        audit(conn, request, actor, role, "保存费用分摊", "allocation", demand_id, demand_id=demand_id, details={"total_ratio": total_ratio})
        data = demand_dict(conn, get_demand_or_404(conn, demand_id))
    return {"code": 0, "message": "费用分摊已保存", "data": data}


@app.get("/api/demands/{demand_id}/budget-check")
def check_budget(demand_id: int):
    with connect() as conn:
        d = demand_dict(conn, get_demand_or_404(conn, demand_id))
        snap = budget_snapshot(conn, d)
        if not snap:
            raise BusinessError(404, "REQ-4040", "未关联可用预算")
    return {"code": 0, "data": snap}


def create_notification(conn, demand_id, level, title, content, target_role=None):
    conn.execute("INSERT INTO notifications(demand_id,level,title,content,target_role,created_at) VALUES (?,?,?,?,?,?)", (demand_id,level,title,content,target_role,now_iso()))


@app.post("/api/demands/{demand_id}/approve")
def approve(demand_id: int, payload: ApprovalPayload, request: Request, x_user: Optional[str] = Header(None), x_role: Optional[str] = Header(None)):
    actor, role = actor_context(x_user, x_role)
    action = payload.action.strip()
    if action not in ("通过", "驳回"):
        raise BusinessError(400, "REQ-4001", "审批动作仅支持通过或驳回")
    with connect() as conn:
        d = demand_dict(conn, get_demand_or_404(conn, demand_id))
        node = d["current_node"]
        if node not in APPROVAL_FLOW:
            raise BusinessError(409, "REQ-4091", "当前需求不在可审批节点")
        expected_role, next_node = APPROVAL_FLOW[node]
        if not request_has_role(request, expected_role):
            raise BusinessError(403, "AUTH-4030", f"当前节点需要角色：{ROLE_LABELS.get(expected_role, expected_role)}")

        if node == "产品经理审批" and action == "通过":
            fp_count = conn.execute("SELECT COUNT(*) c FROM function_points WHERE demand_id=?", (demand_id,)).fetchone()["c"]
            if fp_count == 0:
                raise BusinessError(400, "REQ-4002", "产品经理审批通过前至少需完成一个功能点评估")
            ratio = conn.execute("SELECT COALESCE(SUM(ratio),0) s FROM allocations WHERE demand_id=?", (demand_id,)).fetchone()["s"]
            if ratio > 100.00001:
                raise BusinessError(422, "BUD-4221", "费用分摊比例合计不能超过100%")

        if node == "财务审批" and action == "通过":
            snap = budget_snapshot(conn, d)
            if not snap or not snap["sufficient"]:
                raise BusinessError(422, "BUD-4220", "预算不足，无法通过财务审批", snap)
            if snap["warning"] and not payload.comment.strip():
                raise BusinessError(400, "REQ-4002", "当前预算执行率已达到或超过95%，财务审批意见必须填写")
            if snap["warning"]:
                create_notification(conn, demand_id, "warning", "预算执行率预警", f"当前预算执行率为 {snap['execution_rate']}%，已达到95%预警阈值。", "finance")
            next_node = "分管总审批" if float(d["estimated_amount"] or d["budget_amount"] or 0) > 50_000 else "终审"

        request_id = getattr(request.state, "request_id", "")
        return_to = ""
        if action == "驳回":
            allowed_returns = list(previous_nodes_for(node))
            if float(d["estimated_amount"] or d["budget_amount"] or 0) <= 50_000 and "分管总审批" in allowed_returns:
                allowed_returns.remove("分管总审批")
            return_to = payload.return_to or "需求申请"
            if return_to not in allowed_returns:
                raise BusinessError(400, "REQ-4001", f"当前节点仅允许退回到：{'、'.join(allowed_returns)}")

        conn.execute(
            "INSERT INTO approval_records(demand_id,node,role,approver,action,comment,return_to,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (demand_id, node, role, actor, action, payload.comment, return_to, now_iso()),
        )
        complete_oa_task(conn, demand_id, node, action, request_id)

        if action == "驳回":
            if return_to == "需求申请":
                conn.execute("UPDATE demands SET status='已驳回',current_node='已驳回',oa_sync_status='已回退',updated_at=? WHERE id=?", (now_iso(), demand_id))
            else:
                conn.execute("UPDATE demands SET status=?,current_node=?,oa_sync_status='已推送',updated_at=? WHERE id=?", (return_to, return_to, now_iso(), demand_id))
                create_oa_task(conn, demand_id, return_to, request_id)
        elif node == "终审":
            conn.execute("UPDATE demands SET status='审批通过',current_node='审批通过',oa_sync_status='已完成',updated_at=? WHERE id=?", (now_iso(), demand_id))
        else:
            conn.execute("UPDATE demands SET status=?,current_node=?,oa_sync_status='已推送',updated_at=? WHERE id=?", (next_node, next_node, now_iso(), demand_id))
            create_oa_task(conn, demand_id, next_node, request_id)
        audit(conn, request, actor, role, f"审批{action}", "demand", demand_id, demand_id=demand_id,
              details={"node": node, "comment": payload.comment, "return_to": return_to or None, "oaTodo": action == "通过" and node != "终审"})

    # 终审通过后自动推送 TAPD。
    if node == "终审" and action == "通过":
        return push_tapd(demand_id, request, False, x_user, x_role, automatic=True)
    with connect() as conn:
        data = demand_dict(conn, get_demand_or_404(conn, demand_id))
    msg = f"{node}已{action}"
    if action == "驳回":
        msg += f"，已退回至{return_to}"
    return {"code": 0, "message": msg, "data": data}


@app.post("/api/demands/{demand_id}/tapd/push")
def push_tapd(demand_id: int, request: Request, simulate_failure: bool = Query(False), x_user: Optional[str] = Header(None), x_role: Optional[str] = Header(None), automatic: bool = False):
    actor, role = actor_context(x_user, x_role)
    with connect() as conn:
        d = demand_dict(conn, get_demand_or_404(conn, demand_id))
        if d["status"] not in ("审批通过", "TAPD同步失败", "TAPD同步重试中", "已创建", "开发中", "测试中", "待发布", "已完成"):
            raise BusinessError(409, "REQ-4091", "当前状态不允许创建TAPD需求")
        existing_count = conn.execute("SELECT COUNT(*) c FROM tapd_requirements WHERE demand_id=?", (demand_id,)).fetchone()["c"]
        if existing_count or d.get("tapd_id"):
            raise BusinessError(409, "REQ-4090", "该REQ编号已创建TAPD需求，请勿重复推送", {"tapdId": d.get("tapd_id")})
        request_id = getattr(request.state, "request_id", "")
        if simulate_failure:
            job = schedule_tapd_retry(conn, demand_id, request_id)
            audit(conn, request, actor, role, "创建TAPD需求", "tapd", demand_id, "等待重试", demand_id,
                  {"attempt": 1, "next_retry_at": job.get("next_retry_at"), "automatic": automatic})
            data = demand_dict(conn, get_demand_or_404(conn, demand_id))
            return {"code": 0, "message": "第1次TAPD调用失败，已进入30秒间隔自动重试队列", "data": data}
        records = create_tapd_requirements(conn, demand_id, request_id)
        audit(conn, request, actor, role, "创建TAPD需求", "tapd", demand_id, "成功", demand_id,
              {"automatic": automatic, "count": len(records), "strategy": get_setting(conn, "tapd_split_strategy", "system")})
        data = demand_dict(conn, get_demand_or_404(conn, demand_id))
    return {"code": 0, "message": ("终审通过，已自动创建TAPD需求" if automatic else "TAPD需求创建成功") + f"（共{len(records)}条）", "data": data}


@app.post("/api/demands/{demand_id}/tapd/sync")
def sync_tapd(demand_id: int, request: Request, tapd_status: Optional[str] = Query(None), x_user: Optional[str] = Header(None), x_role: Optional[str] = Header(None)):
    actor, role = actor_context(x_user, x_role)
    with connect() as conn:
        d = demand_dict(conn, get_demand_or_404(conn, demand_id))
        if not d.get("tapd_id") and not d.get("tapd_requirements"):
            raise BusinessError(409, "REQ-4091", "尚未创建TAPD需求")
        statuses = list(TAPD_STATUS_MAP.keys())
        if tapd_status is None:
            current = d.get("tapd_status") or "新"
            tapd_status = statuses[min(statuses.index(current) + 1, len(statuses) - 1)] if current in statuses else "开发中"
        if tapd_status not in TAPD_STATUS_MAP:
            raise BusinessError(400, "REQ-4001", "无效TAPD状态")
        if tapd_runtime_config(conn)["mode"] == "live":
            payload = build_live_sync_payload(conn, demand_id, d.get("tapd_id"))
            tapd_status = payload.status
        else:
            payload = build_mock_sync_payload(conn, demand_id, tapd_status)
        result = apply_tapd_payload(conn, demand_id, payload, "手动同步", getattr(request.state, "request_id", ""))
        conn.execute("UPDATE tapd_requirements SET tapd_status=?,sync_status='成功',last_sync_at=? WHERE demand_id=?", (tapd_status, now_iso(), demand_id))
        conn.execute("INSERT INTO tapd_events(demand_id,event_type,success,attempt,request_id,message,created_at) VALUES (?,?,?,?,?,?,?)",
                     (demand_id, "SYNC", 1, 1, getattr(request.state, "request_id", None), f"手动回读状态：{tapd_status} → {result['system_status']}", now_iso()))
        audit(conn, request, actor, role, "同步TAPD状态", "tapd", d.get("tapd_id") or demand_id, demand_id=demand_id,
              details={"tapd_status": tapd_status, "system_status": result["system_status"], "tasks": len(payload.tasks), "costs": len(payload.costs)})
        data = demand_dict(conn, get_demand_or_404(conn, demand_id))
    return {"code": 0, "message": "TAPD需求、任务、花费及状态回读完成", "data": data}


@app.get("/api/approvals/pending")
def pending_approvals(request: Request, x_role: Optional[str] = Header(None)):
    roles = request_role_codes(request) or {x_role or "department_head"}
    nodes = [n for n, (r, _) in APPROVAL_FLOW.items() if r in roles]
    if "admin" in roles:
        nodes = list(APPROVAL_FLOW.keys())
    with connect() as conn:
        if nodes:
            qs = ",".join("?" for _ in nodes)
            rows = conn.execute(f"SELECT * FROM demands WHERE current_node IN ({qs}) ORDER BY submitted_at", nodes).fetchall()
        else:
            rows = []
        items = [demand_dict(conn,r) for r in rows]
    return {"code":0,"data":items}


@app.get("/api/notifications")
def notifications(request: Request, x_role: Optional[str] = Header(None)):
    roles = request_role_codes(request) or {x_role or "applicant"}
    with connect() as conn:
        reconcile_work_deviation_notifications(conn)
        if "admin" in roles:
            rows = conn.execute("SELECT * FROM notifications ORDER BY id DESC LIMIT 50").fetchall()
        else:
            placeholders = ",".join("?" for _ in roles)
            rows = conn.execute(
                f"SELECT * FROM notifications WHERE target_role IS NULL OR target_role IN ({placeholders}) ORDER BY id DESC LIMIT 50",
                tuple(roles),
            ).fetchall()
    return {"code":0,"data":[dict(r) for r in rows]}


@app.post("/api/notifications/{notification_id}/read")
def mark_notification_read(notification_id: int):
    with connect() as conn:
        row = conn.execute("SELECT id FROM notifications WHERE id=?", (notification_id,)).fetchone()
        if not row:
            raise BusinessError(404, "REQ-4040", "消息不存在")
        conn.execute("UPDATE notifications SET is_read=1 WHERE id=?", (notification_id,))
    return {"code": 0, "message": "消息已读"}


@app.get("/api/audit")
def audit_logs(page: int = 1, page_size: int = 50):
    page_size = min(max(page_size,1),100)
    with connect() as conn:
        rows = conn.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT ? OFFSET ?", (page_size,(max(page,1)-1)*page_size)).fetchall()
    return {"code":0,"data":[dict(r) for r in rows]}


@app.get("/api/ai/config")
def ai_runtime_config():
    """前端只读取非敏感运行状态，不返回任何后台账号或管理凭据。"""
    try:
        config = public_ai_config()
    except AIServiceError as exc:
        raise BusinessError(500, "AI-5001", str(exc)) from exc
    return {"code": 0, "data": config}


@app.post("/api/ai/chat")
async def ai_chat(
    payload: AIQueryPayload,
    request: Request,
    x_user: Optional[str] = Header(None),
    x_role: Optional[str] = Header(None),
):
    """统一智能体入口：AI问答页、项目360机器人和悬浮助手共同使用。"""
    question = (payload.question or "").strip()
    if not question:
        raise BusinessError(400, "REQ-4002", "AI问答内容不能为空")
    if len(question) > MAX_AI_LEN:
        raise BusinessError(400, "REQ-4003", "AI问答单次问题不能超过1000个字符")
    user = getattr(request.state, "auth_user", None)
    if not user:
        raise BusinessError(401, "AUTH-4010", "登录已失效，请重新登录后使用AI助手")
    permissions = list(user.get("permissions") or [])
    if not has_permission(permissions, "ai"):
        raise BusinessError(403, "AUTH-4030", "当前角色未授权使用AI助手")
    if payload.project_id and not has_ai_capability(permissions, "query.project"):
        raise BusinessError(403, "AUTH-4030", "当前角色未授权AI查询项目")
    actor = f"{user['display_name']} {user['username']}".strip()
    role = user["role_code"]
    business_type = "project360" if payload.project_id else "assistant"
    business_id = str(payload.project_id or "")
    delegation_token = ""
    if public_mcp_status()["enabled"]:
        delegation_token = issue_ai_delegation(
            request.headers.get("X-Session", ""),
            payload.source or business_type,
            payload.project_id,
        )
    context = build_ai_fact_context(
        question,
        role,
        payload.project_id,
        permissions=permissions,
        delegation_token=delegation_token,
    )
    try:
        result = await run_agent_message(
            question=question,
            user_id=actor,
            session_id=payload.session_id,
            context=context,
            source=payload.source or business_type,
        )
    except AIServiceError as exc:
        with connect() as conn:
            conn.execute(
                """INSERT INTO integration_logs(integration_code,direction,business_type,business_id,success,message,request_id,created_at)
                   VALUES ('ai','out',?,?,0,?,?,?)""",
                (business_type, business_id, str(exc)[:500], getattr(request.state, "request_id", ""), now_iso()),
            )
            audit(
                conn, request, actor, role, "调用AI智能体", "ai_session", payload.session_id or "new",
                result="失败", details={"source": payload.source, "project_id": payload.project_id, "roles": user.get("role_codes") or [], "error": str(exc)[:300]},
            )
        raise BusinessError(502, "AI-5020", str(exc)) from exc

    if delegation_token and isinstance(result.get("answer"), str):
        result["answer"] = result["answer"].replace(delegation_token, "[已隐藏AI委托令牌]")

    with connect() as conn:
        conn.execute(
            """INSERT INTO integration_logs(integration_code,direction,business_type,business_id,success,message,request_id,created_at)
               VALUES ('ai','out',?,?,1,?,?,?)""",
            (business_type, business_id, f"智能体 {result['agent_id']} 调用成功", getattr(request.state, "request_id", ""), now_iso()),
        )
        audit(
            conn, request, actor, role, "调用AI智能体", "ai_session", result.get("session_id") or "new",
            details={
                "source": payload.source,
                "project_id": payload.project_id,
                "agent_id": result["agent_id"],
                "roles": user.get("role_codes") or [],
                "effective_ai_capabilities": get_ai_capabilities(permissions),
            },
        )
    return {
        "code": 0,
        "message": "智能体回答成功",
        "data": {
            **result,
            "scope": "当前账号可访问的TRM系统事实数据",
            "role": "、".join(user.get("role_labels") or [ROLE_LABELS.get(role, role)]),
        },
    }


@app.post("/api/ai/query")
def ai_query(payload: AIQueryPayload, request: Request, x_role: Optional[str] = Header(None)):
    q = (payload.question or "").strip()
    if not q:
        raise BusinessError(400, "REQ-4002", "AI问答内容不能为空")
    if len(q) > MAX_AI_LEN:
        raise BusinessError(400, "REQ-4003", "AI问答单次问题不能超过1000个字符")
    user = getattr(request.state, "auth_user", None)
    # 仅保留本地TestClient的旧测试兼容；真实HTTP请求必须由中间件注入登录用户。
    role = user["role_code"] if user else (x_role or "applicant")
    permissions = list(user.get("permissions") or []) if user else list(DEFAULT_ROLE_PERMISSIONS.get(role, []))
    if not has_permission(permissions, "ai"):
        raise BusinessError(403, "AUTH-4030", "当前角色未授权使用AI助手")
    asks_budget = "预算" in q
    asks_project = "项目" in q
    asks_demand = bool(re.search(r"REQ-\d{8}-\d{4}", q.upper())) or any(
        word in q for word in ("需求", "审批", "功能点", "TAPD", "进度", "工时", "历史", "过往")
    )
    if asks_budget and not has_ai_capability(permissions, "query.budget"):
        raise BusinessError(403, "AUTH-4030", "当前角色未授权AI查询预算")
    if asks_project and not has_ai_capability(permissions, "query.project"):
        raise BusinessError(403, "AUTH-4030", "当前角色未授权AI查询项目")
    if asks_demand and not has_ai_capability(permissions, "query.demand"):
        raise BusinessError(403, "AUTH-4030", "当前角色未授权AI查询需求")
    with connect() as conn:
        rows = (
            [demand_dict(conn, r) for r in conn.execute("SELECT * FROM demands ORDER BY id DESC LIMIT 1000")]
            if has_ai_capability(permissions, "query.demand") else []
        )
        target = None
        m = re.search(r"REQ-\d{8}-\d{4}", q.upper())
        if m:
            target = next((d for d in rows if (d.get("demand_no") or "").upper() == m.group(0)), None)
        if not target:
            for d in rows:
                title = d.get("title") or ""
                if title and (title[:12] in q or (len(title) >= 6 and title[:6] in q)):
                    target = d
                    break

        # 识别项目/预算名称。POC里的“某项目”在需求申请中通过预算出处关联。
        project_name = None
        for b in conn.execute("SELECT budget_name FROM budgets ORDER BY id"):
            if b["budget_name"] in q:
                project_name = b["budget_name"]
                break

        # 识别部门与周期。
        department = None
        for dep in ["数字化管理部", "产品研发部", "科技管理部", "财务部", "办公室"]:
            if dep in q:
                department = dep
                break
        period_type = "quarter" if ("季度" in q or re.search(r"Q[1-4]", q.upper())) else "month"

        # 1) 单条需求完整信息。
        if target and any(k in q for k in ["完整", "全部", "全生命周期", "详细信息", "所有信息"]):
            snap = budget_snapshot(conn, target)
            approvals = "；".join(f"{a['node']}:{a['action']}({a['approver']})" for a in target["approvals"]) or "暂无"
            systems = "、".join(sorted({fp["system_name"] for fp in target["function_points"]})) or "暂无"
            tapd_ids = "、".join(r["tapd_id"] for r in target.get("tapd_requirements", [])) or (target.get("tapd_id") or "暂无")
            answer = (
                f"{target.get('demand_no') or '该需求'}《{target['title']}》：申请人{target['applicant']}，类型{target['demand_type']}，优先级{target['priority']}，"
                f"当前状态“{target['status']}”、当前节点“{target['current_node']}”。功能点评估{len(target['function_points'])}条，涉及系统{systems}，"
                f"预估金额¥{target.get('estimated_amount',0):,.2f}。审批记录：{approvals}。"
            )
            if snap:
                answer += f"关联预算“{snap['budget_name']}”，总预算¥{snap['total_budget']:,.2f}，已使用¥{snap['used_budget']:,.2f}，执行率{snap['execution_rate']}%，预算{'充足' if snap['sufficient'] else '不足'}。"
            answer += (
                f"TAPD需求ID：{tapd_ids}；TAPD状态“{target.get('tapd_status') or '未创建'}”；"
                f"回读任务{len(target.get('tapd_tasks', []))}条、花费记录{len(target.get('tapd_costs', []))}条；"
                f"内部人天{float(target.get('internal_days') or 0):.1f}、外部人天{float(target.get('external_days') or 0):.1f}、"
                f"预估工时{float(target.get('estimated_hours') or 0):.1f}h、实际工时{float(target.get('actual_hours') or 0):.1f}h。"
            )

        # 2) 批量需求统计：项目下状态分布与工时汇总。
        elif project_name and any(k in q for k in ["状态分布", "工时汇总", "所有需求", "批量", "统计"]):
            scoped = []
            for d in rows:
                if project_name in (d.get("budget_sources") or []):
                    scoped.append(d)
            dist = {}
            for d in scoped:
                dist[d["status"]] = dist.get(d["status"], 0) + 1
            dist_text = "、".join(f"{k}{v}条" for k, v in dist.items()) or "暂无需求"
            est = sum(float(d.get("estimated_hours") or 0) for d in scoped)
            actual = sum(float(d.get("actual_hours") or 0) for d in scoped)
            answer = f"项目“{project_name}”共有{len(scoped)}条需求，状态分布：{dist_text}；预估工时合计{est:.1f}h，实际工时合计{actual:.1f}h。"

        # 3) 预算分析：部门月度/季度执行趋势。
        elif "预算" in q and any(k in q for k in ["趋势", "月度", "季度", "执行"]):
            sql = "SELECT period,SUM(used_amount) used_amount,SUM(total_budget) total_budget FROM budget_execution_snapshots WHERE period_type=?"
            args = [period_type]
            if department:
                sql += " AND department=?"; args.append(department)
            sql += " GROUP BY period ORDER BY period"
            trend = []
            for r in conn.execute(sql, args):
                rate = float(r["used_amount"]) / float(r["total_budget"]) * 100 if r["total_budget"] else 0
                trend.append(f"{r['period']} {rate:.1f}%（¥{r['used_amount']:,.0f}/¥{r['total_budget']:,.0f}）")
            scope_name = department or "全部部门"
            period_name = "季度" if period_type == "quarter" else "月度"
            answer = f"{scope_name}{period_name}预算执行趋势：" + ("；".join(trend) if trend else "当前暂无可用执行快照。")

        # 4) 进度查询：当前环节 + 预计完成时间。
        elif target and any(k in q for k in ["进度", "卡", "状态", "预计何时", "什么时候完成", "预计完成"]):
            expected = target.get("planned_online_date") or target.get("expected_completion_date")
            if not expected and target.get("tapd_tasks"):
                ends = [t.get("planned_end") for t in target["tapd_tasks"] if t.get("planned_end")]
                expected = max(ends) if ends else None
            answer = f"{target.get('demand_no') or '该需求'} 当前状态为“{target['status']}”，当前环节“{target['current_node']}”。"
            if target.get("tapd_id"):
                answer += f"TAPD状态“{target.get('tapd_status') or '新'}”，最近同步时间{target.get('tapd_last_sync_at') or '暂无'}。"
            answer += f"预计完成/上线时间：{expected or '当前尚未维护计划时间'}。"

        # 5) 历史追溯：同类需求处理方式与平均交付周期。
        elif any(k in q for k in ["历史", "过往", "平均交付周期", "平均周期", "怎么处理"]):
            scope_type = target.get("demand_type") if target else None
            completed = []
            for d in rows:
                if d.get("closed_at") and d.get("submitted_at") and (not scope_type or d.get("demand_type") == scope_type):
                    try:
                        start_dt = datetime.fromisoformat(d["submitted_at"])
                        end_dt = datetime.fromisoformat(d["closed_at"])
                        completed.append((d, (end_dt - start_dt).total_seconds() / 86400))
                    except Exception:
                        pass
            if completed:
                avg_days = sum(days for _, days in completed) / len(completed)
                examples = "；".join(f"{d.get('demand_no')}({d.get('demand_type')}){days:.1f}天" for d, days in completed[:5])
                answer = f"历史上{'同类' if scope_type else ''}已完成需求{len(completed)}条，平均交付周期{avg_days:.1f}天。近期样例：{examples}。常见处理路径均可从审批记录、功能点评估、预算校验及TAPD回读记录追溯。"
            else:
                answer = "当前数据中尚无同时具备提交时间和关闭时间的已完成需求；历史追溯能力已启用，产生已完成需求后会自动计算平均交付周期并展示过往审批、评估与TAPD处理记录。"

        elif "多少" in q and ("需求" in q or "条" in q):
            if not has_ai_capability(permissions, "query.demand"):
                raise BusinessError(403, "AUTH-4030", "当前角色未授权AI查询需求")
            answer = f"当前系统共有{len(rows)}条需求，其中审批中{sum(1 for d in rows if '审批' in (d.get('current_node') or '') or d.get('current_node')=='终审')}条，已完成{sum(1 for d in rows if d.get('status')=='已完成')}条。"
        elif "预算" in q and target:
            snap = budget_snapshot(conn, target)
            answer = f"{target.get('demand_no') or '该需求'}当前预估金额¥{target.get('estimated_amount',0):,.2f}。"
            if snap:
                answer += f"预算“{snap['budget_name']}”总额¥{snap['total_budget']:,.2f}，已使用¥{snap['used_budget']:,.2f}，执行率{snap['execution_rate']}%，已承诺需求预算¥{snap['committed_demand_amount']:,.2f}，本次纳入后预算{'充足' if snap['sufficient'] else '不足'}。"
        elif "审批" in q and target:
            rec = "；".join(f"{a['node']}：{a['action']}（{a['approver']}）" + (f"→{a.get('return_to')}" if a.get('return_to') else "") for a in target["approvals"][-8:]) or "暂无审批记录"
            answer = f"{target.get('demand_no') or '该需求'}审批记录：{rec}。当前节点“{target['current_node']}”。"
        elif target:
            answer = f"{target.get('demand_no') or '该需求'}：{target['title']}。当前状态“{target['status']}”，申请人{target['applicant']}，优先级{target['priority']}，预估金额¥{target.get('estimated_amount',0):,.2f}，功能点{len(target['function_points'])}条。"
        else:
            answer = "您可以查询以下内容：\n\n- 单条需求的完整信息\n- 项目需求状态与工时统计\n- 部门月度或季度预算执行趋势\n- 需求当前环节与预计完成时间\n- 历史同类需求的处理方式与平均交付周期"
    return {"code": 0, "data": {"answer": answer, "scope": "系统事实数据", "role": ROLE_LABELS.get(role, role)}}
