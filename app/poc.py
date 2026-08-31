import asyncio
import hmac
import json
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Header, Request, Query
from pydantic import BaseModel, Field

from .db import connect, now_iso, row_to_dict
from .rules import APPROVAL_FLOW, BusinessError, ROLE_LABELS, TAPD_STATUS_MAP
from .auth import get_role_labels

router = APIRouter(prefix="/api", tags=["POC完整能力"])
_RECONCILE_LOCK = threading.RLock()

NODE_TIMEOUT_HOURS = {
    "直属领导审批": 24,
    "产品经理审批": 48,
    "财务审批": 24,
    "分管总审批": 24,
    "终审": 72,
}

PREVIOUS_NODES = {
    "直属领导审批": ["需求申请"],
    "产品经理审批": ["直属领导审批", "需求申请"],
    "财务审批": ["产品经理审批", "直属领导审批", "需求申请"],
    "分管总审批": ["财务审批", "产品经理审批", "直属领导审批", "需求申请"],
    "终审": ["分管总审批", "财务审批", "产品经理审批", "直属领导审批", "需求申请"],
}


def _add_column(conn, table: str, column: str, ddl: str):
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _parse_iso(value: Optional[str]):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _now_dt():
    return datetime.now(timezone.utc).astimezone()


def _actor(x_user: Optional[str], x_role: Optional[str]):
    role = x_role or "applicant"
    if role not in get_role_labels():
        raise BusinessError(403, "AUTH-4030", "无效角色或无权限")
    return x_user or "lili11-ghq", role


def _integration_log(conn, code, direction, business_type, business_id, success, message, request_id=""):
    conn.execute(
        """INSERT INTO integration_logs(integration_code,direction,business_type,business_id,success,message,request_id,created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (code, direction, business_type, str(business_id or ""), 1 if success else 0, message, request_id or "", now_iso()),
    )


def _notification(conn, demand_id, level, title, content, target_role, event_key=""):
    conn.execute(
        "INSERT INTO notifications(demand_id,level,title,content,target_role,event_key,created_at) VALUES (?,?,?,?,?,?,?)",
        (demand_id, level, title, content, target_role, event_key, now_iso()),
    )


def init_poc_db():
    with connect() as conn:
        for col, ddl in {
            "tapd_description": "TEXT DEFAULT ''",
            "rd_owner": "TEXT DEFAULT ''",
            "rd_department": "TEXT DEFAULT ''",
            "user_test_date": "TEXT",
            "test_complete_date": "TEXT",
            "demand_confirm_date": "TEXT",
            "expected_completion_date": "TEXT",
            "oa_sync_status": "TEXT DEFAULT '未推送'",
            "work_hour_source": "TEXT DEFAULT '人工维护'",
            "work_plan_source": "TEXT DEFAULT '人工维护'",
            "actual_hours_source": "TEXT DEFAULT '未登记'",
            "work_plan_updated_by": "TEXT DEFAULT ''",
            "work_plan_updated_at": "TEXT",
        }.items():
            _add_column(conn, "demands", col, ddl)
        _add_column(conn, "approval_records", "return_to", "TEXT DEFAULT ''")
        _add_column(conn, "budget_transactions", "department", "TEXT DEFAULT ''")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS oa_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                demand_id INTEGER NOT NULL,
                node TEXT NOT NULL,
                role TEXT NOT NULL,
                external_task_id TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT '待处理',
                due_at TEXT,
                pushed_at TEXT NOT NULL,
                completed_at TEXT,
                reminder_sent_at TEXT,
                reminder_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(demand_id) REFERENCES demands(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_oa_tasks_pending ON oa_tasks(status,due_at);

            CREATE TABLE IF NOT EXISTS tapd_requirements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                demand_id INTEGER NOT NULL,
                split_key TEXT NOT NULL,
                system_name TEXT NOT NULL,
                allocation_id INTEGER,
                tapd_id TEXT UNIQUE NOT NULL,
                tapd_url TEXT NOT NULL,
                tapd_status TEXT DEFAULT '新',
                sync_status TEXT DEFAULT '成功',
                payload_json TEXT DEFAULT '{}',
                created_at TEXT NOT NULL,
                last_sync_at TEXT,
                UNIQUE(demand_id,split_key),
                FOREIGN KEY(demand_id) REFERENCES demands(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS tapd_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                demand_id INTEGER NOT NULL,
                tapd_requirement_id INTEGER,
                external_task_id TEXT NOT NULL,
                title TEXT DEFAULT '',
                description TEXT DEFAULT '',
                task_type TEXT DEFAULT '',
                planned_start TEXT,
                planned_end TEXT,
                estimated_hours REAL DEFAULT 0,
                creator TEXT DEFAULT '',
                external_created_at TEXT,
                completed_at TEXT,
                completed_hours REAL DEFAULT 0,
                remaining_hours REAL DEFAULT 0,
                overrun_hours REAL DEFAULT 0,
                updated_at TEXT NOT NULL,
                UNIQUE(demand_id,external_task_id),
                FOREIGN KEY(demand_id) REFERENCES demands(id) ON DELETE CASCADE,
                FOREIGN KEY(tapd_requirement_id) REFERENCES tapd_requirements(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS tapd_costs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                demand_id INTEGER NOT NULL,
                task_external_id TEXT DEFAULT '',
                spent_date TEXT,
                hours REAL DEFAULT 0,
                creator TEXT DEFAULT '',
                description TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY(demand_id) REFERENCES demands(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS demand_work_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                demand_id INTEGER NOT NULL,
                work_date TEXT NOT NULL,
                hours REAL NOT NULL,
                worker TEXT NOT NULL,
                task_name TEXT DEFAULT '',
                description TEXT DEFAULT '',
                source TEXT NOT NULL DEFAULT '人工登记',
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(demand_id) REFERENCES demands(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_demand_work_logs_demand_date
            ON demand_work_logs(demand_id,work_date,id);

            CREATE TABLE IF NOT EXISTS tapd_sync_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                demand_id INTEGER NOT NULL,
                source TEXT NOT NULL,
                changed_count INTEGER DEFAULT 0,
                success INTEGER DEFAULT 1,
                message TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY(demand_id) REFERENCES demands(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS tapd_retry_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                demand_id INTEGER NOT NULL,
                attempt_count INTEGER DEFAULT 1,
                next_retry_at TEXT,
                force_fail INTEGER DEFAULT 1,
                status TEXT DEFAULT '等待重试',
                last_error TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(demand_id) REFERENCES demands(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS system_settings (
                code TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                description TEXT DEFAULT '',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS budget_execution_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                budget_id INTEGER,
                department TEXT NOT NULL,
                period_type TEXT NOT NULL,
                period TEXT NOT NULL,
                used_amount REAL NOT NULL,
                total_budget REAL NOT NULL,
                recorded_at TEXT NOT NULL,
                UNIQUE(budget_id,department,period_type,period),
                FOREIGN KEY(budget_id) REFERENCES budgets(id) ON DELETE CASCADE
            );
            """
        )
        _add_column(conn, "tapd_costs", "tapd_requirement_id", "INTEGER")
        _add_column(conn, "tapd_requirements", "function_point_id", "INTEGER")
        # 历史“按分摊行”记录可通过allocation_id无歧义恢复功能点关联。
        conn.execute(
            """UPDATE tapd_requirements SET function_point_id=(
                   SELECT a.function_point_id FROM allocations a
                    WHERE a.id=tapd_requirements.allocation_id
               )
               WHERE function_point_id IS NULL AND allocation_id IS NOT NULL
                 AND (SELECT a.function_point_id FROM allocations a
                       WHERE a.id=tapd_requirements.allocation_id) IS NOT NULL
                 AND 1=(SELECT COUNT(*) FROM tapd_requirements tr
                          JOIN allocations a ON a.id=tr.allocation_id
                         WHERE tr.demand_id=tapd_requirements.demand_id
                           AND a.function_point_id=(SELECT a2.function_point_id FROM allocations a2
                                                    WHERE a2.id=tapd_requirements.allocation_id))"""
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_tapd_requirements_function_point "
            "ON tapd_requirements(demand_id,function_point_id) WHERE function_point_id IS NOT NULL"
        )
        # 旧版本按系统拆分。只有系统下恰好一个功能点时才安全补齐关联，
        # 避免把历史上一条TAPD记录错误绑定到多个功能点。
        conn.execute(
            """UPDATE tapd_requirements SET function_point_id=(
                   SELECT MIN(fp.id) FROM function_points fp
                    WHERE fp.demand_id=tapd_requirements.demand_id
                      AND fp.system_name=tapd_requirements.system_name
               )
               WHERE function_point_id IS NULL
                 AND 1=(SELECT COUNT(*) FROM function_points fp
                         WHERE fp.demand_id=tapd_requirements.demand_id
                           AND fp.system_name=tapd_requirements.system_name)
                 AND 1=(SELECT COUNT(*) FROM tapd_requirements tr
                         WHERE tr.demand_id=tapd_requirements.demand_id
                           AND tr.system_name=tapd_requirements.system_name)"""
        )
        now = now_iso()
        conn.execute(
            "INSERT OR IGNORE INTO system_settings(code,value,description,updated_at) VALUES (?,?,?,?)",
            ("tapd_split_strategy", "function_point", "TAPD按功能点一对一生成需求", now),
        )
        conn.execute(
            "UPDATE system_settings SET value='function_point',description='TAPD按功能点一对一生成需求',updated_at=? WHERE code='tapd_split_strategy'",
            (now,),
        )
        conn.execute(
            "INSERT OR IGNORE INTO system_settings(code,value,description,updated_at) VALUES (?,?,?,?)",
            ("tapd_sync_interval_seconds", str(int(os.getenv("TRM_TAPD_SYNC_INTERVAL_SECONDS", "1800"))), "TAPD定时回读间隔，默认30分钟", now),
        )
        conn.execute(
            "INSERT OR IGNORE INTO system_settings(code,value,description,updated_at) VALUES (?,?,?,?)",
            ("tapd_retry_seconds", str(int(float(os.getenv("TRM_TAPD_RETRY_SECONDS", "30")))), "TAPD失败重试间隔，默认30秒", now),
        )
        conn.execute(
            "INSERT OR IGNORE INTO system_settings(code,value,description,updated_at) VALUES (?,?,?,?)",
            ("tapd_mode", os.getenv("TRM_TAPD_MODE", "mock"), "TAPD运行模式：mock / live", now),
        )
        conn.execute(
            "INSERT OR IGNORE INTO system_settings(code,value,description,updated_at) VALUES (?,?,?,?)",
            ("tapd_workspace_id", os.getenv("TRM_TAPD_WORKSPACE_ID", ""), "TAPD项目workspace_id", now),
        )
        conn.execute(
            "INSERT OR IGNORE INTO system_settings(code,value,description,updated_at) VALUES (?,?,?,?)",
            ("tapd_base_url", os.getenv("TRM_TAPD_BASE_URL", "https://api.tapd.cn"), "TAPD开放平台API地址", now),
        )

        # 用已有预算形成可查询的月度/季度执行快照；后续预算流水会持续更新当前周期。
        if conn.execute("SELECT COUNT(*) c FROM budget_execution_snapshots").fetchone()["c"] == 0:
            budgets = list(conn.execute("SELECT * FROM budgets ORDER BY id"))
            depts = ["数字化管理部", "产品研发部", "科技管理部"]
            for idx, b in enumerate(budgets):
                dept = depts[idx % len(depts)]
                for period, ratio in [("2026-06", 0.72), ("2026-07", 0.86), ("2026-08", 1.0)]:
                    conn.execute(
                        """INSERT OR IGNORE INTO budget_execution_snapshots
                        (budget_id,department,period_type,period,used_amount,total_budget,recorded_at)
                        VALUES (?,?,?,?,?,?,?)""",
                        (b["id"], dept, "month", period, round(float(b["used_budget"]) * ratio, 2), float(b["total_budget"]), now),
                    )
                q_used = float(b["used_budget"])
                conn.execute(
                    """INSERT OR IGNORE INTO budget_execution_snapshots
                    (budget_id,department,period_type,period,used_amount,total_budget,recorded_at)
                    VALUES (?,?,?,?,?,?,?)""",
                    (b["id"], dept, "quarter", "2026Q3", q_used, float(b["total_budget"]), now),
                )

        # 对历史/种子数据补齐当前审批节点的OA待办，保证打开系统即可验证OA审批链路。
        for d in conn.execute("SELECT id,current_node FROM demands WHERE current_node IN ('直属领导审批','产品经理审批','财务审批','分管总审批','终审')"):
            create_oa_task(conn, d["id"], d["current_node"], "bootstrap")


def get_setting(conn, code: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM system_settings WHERE code=?", (code,)).fetchone()
    return row["value"] if row else default


def create_oa_task(conn, demand_id: int, node: str, request_id: str = ""):
    if node not in NODE_TIMEOUT_HOURS:
        return None
    demand = conn.execute("SELECT demand_no,title FROM demands WHERE id=?", (demand_id,)).fetchone()
    if not demand:
        return None
    # 同一节点已有待办则不重复生成。
    existing = conn.execute(
        "SELECT * FROM oa_tasks WHERE demand_id=? AND node=? AND status='待处理' ORDER BY id DESC LIMIT 1",
        (demand_id, node),
    ).fetchone()
    if existing:
        return dict(existing)
    role = APPROVAL_FLOW[node][0]
    now_dt = _now_dt()
    due_at = (now_dt + timedelta(hours=NODE_TIMEOUT_HOURS[node])).isoformat(timespec="seconds")
    external_task_id = f"OA-{demand_id}-{uuid.uuid4().hex[:10].upper()}"
    url = f"/#/approval/{demand_id}"
    now = now_dt.isoformat(timespec="seconds")
    conn.execute(
        """INSERT INTO oa_tasks(demand_id,node,role,external_task_id,title,url,status,due_at,pushed_at,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (demand_id, node, role, external_task_id, f"{demand['demand_no'] or '草稿'} · {demand['title']}", url, "待处理", due_at, now, now, now),
    )
    conn.execute("UPDATE demands SET oa_sync_status='已推送' WHERE id=?", (demand_id,))
    _integration_log(conn, "oa", "out", "todo_create", external_task_id, True, f"已推送OA待办：{node}，超时{NODE_TIMEOUT_HOURS[node]}h", request_id)
    return dict(conn.execute("SELECT * FROM oa_tasks WHERE external_task_id=?", (external_task_id,)).fetchone())


def complete_oa_task(conn, demand_id: int, node: str, result: str, request_id: str = ""):
    row = conn.execute(
        "SELECT * FROM oa_tasks WHERE demand_id=? AND node=? AND status='待处理' ORDER BY id DESC LIMIT 1",
        (demand_id, node),
    ).fetchone()
    if not row:
        return
    now = now_iso()
    conn.execute("UPDATE oa_tasks SET status=?,completed_at=?,updated_at=? WHERE id=?", (result, now, now, row["id"]))
    _integration_log(conn, "oa", "in", "todo_complete", row["external_task_id"], True, f"OA待办已{result}", request_id)


def scan_oa_timeouts(force: bool = False):
    now = _now_dt()
    with connect() as conn:
        rows = list(conn.execute("SELECT * FROM oa_tasks WHERE status='待处理'"))
        count = 0
        for row in rows:
            due = _parse_iso(row["due_at"])
            if not due:
                continue
            if not force and due > now:
                continue
            if row["reminder_sent_at"] and not force:
                continue
            role = row["role"]
            _notification(
                conn,
                row["demand_id"],
                "warning",
                "审批超时提醒",
                f"{row['node']}已达到{NODE_TIMEOUT_HOURS.get(row['node'], 24)}小时处理时限，请尽快处理。",
                role,
            )
            conn.execute(
                "UPDATE oa_tasks SET reminder_count=reminder_count+1,reminder_sent_at=?,updated_at=? WHERE id=?",
                (now_iso(), now_iso(), row["id"]),
            )
            _integration_log(conn, "oa", "out", "timeout_reminder", row["external_task_id"], True, "审批超时自动提醒")
            count += 1
        return count


def previous_nodes_for(current_node: str):
    return PREVIOUS_NODES.get(current_node, [])



def tapd_runtime_config(conn):
    mode = get_setting(conn, "tapd_mode", os.getenv("TRM_TAPD_MODE", "mock")).strip().lower() or "mock"
    workspace_id = get_setting(conn, "tapd_workspace_id", os.getenv("TRM_TAPD_WORKSPACE_ID", "")).strip()
    base_url = get_setting(conn, "tapd_base_url", os.getenv("TRM_TAPD_BASE_URL", "https://api.tapd.cn")).strip().rstrip("/")
    api_user = os.getenv("TRM_TAPD_API_USER", "").strip()
    api_password = os.getenv("TRM_TAPD_API_PASSWORD", "").strip()
    webhook_secret = os.getenv("TRM_TAPD_WEBHOOK_SECRET", "").strip()
    return {
        "mode": mode if mode in ("mock", "live") else "mock",
        "workspace_id": workspace_id,
        "base_url": base_url or "https://api.tapd.cn",
        "credentials_ready": bool(api_user and api_password),
        "webhook_secret_ready": bool(webhook_secret),
        "api_user_masked": (api_user[:2] + "***" + api_user[-2:]) if len(api_user) >= 5 else ("已配置" if api_user else "未配置"),
    }


def _tapd_live_ready(conn):
    cfg = tapd_runtime_config(conn)
    if cfg["mode"] != "live":
        return cfg
    if not cfg["workspace_id"]:
        raise BusinessError(502, "TAPD-5020", "Live模式未配置TAPD workspace_id")
    if not cfg["credentials_ready"]:
        raise BusinessError(502, "TAPD-5020", "Live模式未配置TAPD API账号/口令，请在部署环境变量中设置TRM_TAPD_API_USER与TRM_TAPD_API_PASSWORD")
    return cfg


def _tapd_request(conn, method: str, path: str, *, params=None, data=None, timeout: float = 10.0):
    cfg = _tapd_live_ready(conn)
    api_user = os.getenv("TRM_TAPD_API_USER", "").strip()
    api_password = os.getenv("TRM_TAPD_API_PASSWORD", "").strip()
    url = f"{cfg['base_url']}/{path.lstrip('/')}"
    try:
        with httpx.Client(auth=(api_user, api_password), timeout=timeout, headers={"Accept": "application/json"}) as client:
            response = client.request(method.upper(), url, params=params, data=data)
        response.raise_for_status()
        body = response.json()
    except httpx.TimeoutException as exc:
        raise BusinessError(504, "TAPD-5040", "TAPD调用超时", {"endpoint": path}) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise BusinessError(502, "TAPD-5020", "TAPD服务不可用或返回异常", {"endpoint": path, "error": str(exc)[:300]}) from exc
    if isinstance(body, dict) and body.get("status") not in (None, 1, "1", True):
        raise BusinessError(502, "TAPD-5020", "TAPD接口返回失败", {"endpoint": path, "info": str(body.get("info", ""))[:300]})
    return body


def _tapd_list_data(body, entity_key: str):
    data = body.get("data", []) if isinstance(body, dict) else []
    if isinstance(data, dict) and entity_key in data:
        data = data[entity_key]
    if not isinstance(data, list):
        data = [data] if data else []
    result = []
    for item in data:
        if isinstance(item, dict) and entity_key in item and isinstance(item[entity_key], dict):
            result.append(item[entity_key])
        elif isinstance(item, dict):
            result.append(item)
    return result


def _tapd_status_to_poc(story: dict, fallback: str = "新"):
    raw = str(story.get("v_status") or story.get("status") or "").strip()
    low = raw.lower()
    pairs = [
        (("已拒绝", "拒绝", "rejected", "reject"), "已拒绝"),
        (("已关闭", "已完成", "closed", "done", "resolved"), "已关闭"),
        (("已验收", "验收", "accepted", "verified", "release"), "已验收"),
        (("测试中", "测试", "testing", "test"), "测试中"),
        (("开发中", "进行中", "实现中", "developing", "progressing", "in progress"), "开发中"),
        (("新", "规划中", "未开始", "planning", "open", "new"), "新"),
    ]
    for keys, mapped in pairs:
        if any(k.lower() in low for k in keys):
            return mapped
    return fallback if fallback in TAPD_STATUS_MAP else "新"


def _as_float(value):
    try:
        if value in (None, ""):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def build_live_sync_payload(conn, demand_id: int, tapd_id: Optional[str] = None):
    cfg = _tapd_live_ready(conn)
    d = conn.execute("SELECT * FROM demands WHERE id=?", (demand_id,)).fetchone()
    if not d:
        raise BusinessError(404, "REQ-4040", "需求不存在")
    req = None
    if tapd_id:
        req = conn.execute("SELECT * FROM tapd_requirements WHERE demand_id=? AND tapd_id=?", (demand_id, tapd_id)).fetchone()
    if not req:
        req = conn.execute("SELECT * FROM tapd_requirements WHERE demand_id=? ORDER BY id LIMIT 1", (demand_id,)).fetchone()
    story_id = tapd_id or (req["tapd_id"] if req else d["tapd_id"])
    if not story_id:
        raise BusinessError(409, "REQ-4091", "尚未创建TAPD需求")

    story_body = _tapd_request(conn, "GET", "/stories", params={
        "workspace_id": cfg["workspace_id"], "id": story_id, "with_v_status": 1, "limit": 1
    })
    stories = _tapd_list_data(story_body, "Story")
    if not stories:
        raise BusinessError(404, "REQ-4040", "TAPD未返回对应需求", {"tapdId": story_id})
    story = stories[0]
    mapped_status = _tapd_status_to_poc(story, d["tapd_status"] or "新")

    tasks = []
    page = 1
    while page <= 50:
        task_body = _tapd_request(conn, "GET", "/tasks", params={
            "workspace_id": cfg["workspace_id"], "story_id": story_id, "page": page, "limit": 200
        })
        batch = _tapd_list_data(task_body, "Task")
        for t in batch:
            tasks.append(TapdTaskPayload(
                task_id=str(t.get("id") or ""),
                title=str(t.get("name") or ""),
                description=str(t.get("description") or ""),
                task_type=str(t.get("label") or "TAPD任务"),
                planned_start=t.get("begin"), planned_end=t.get("due"),
                estimated_hours=_as_float(t.get("effort")),
                creator=str(t.get("creator") or t.get("owner") or ""),
                created_at=t.get("created"), completed_at=t.get("completed"),
                completed_hours=_as_float(t.get("effort_completed")),
                remaining_hours=_as_float(t.get("remain")), overrun_hours=_as_float(t.get("exceed")),
            ))
        if len(batch) < 200:
            break
        page += 1

    costs = []
    for task in tasks[:200]:
        page = 1
        while page <= 20:
            cost_body = _tapd_request(conn, "GET", "/timesheets", params={
                "workspace_id": cfg["workspace_id"], "entity_type": "task", "entity_id": task.task_id,
                "page": page, "limit": 200
            })
            batch = _tapd_list_data(cost_body, "Timesheet")
            if not batch:
                batch = _tapd_list_data(cost_body, "TimeSheet")
            for c in batch:
                costs.append(TapdCostPayload(
                    task_id=task.task_id, spent_date=c.get("spentdate"), hours=_as_float(c.get("timespent")),
                    creator=str(c.get("owner") or ""), description=str(c.get("memo") or ""),
                ))
            if len(batch) < 200:
                break
            page += 1

    return TapdWebhookPayload(
        tapd_id=str(story_id), demand_no=d["demand_no"], status=mapped_status,
        demand_description=str(story.get("description") or d["description"] or ""),
        rd_owner=str(story.get("developer") or story.get("owner") or ""),
        rd_department=d["rd_department"] or "",
        internal_days=float(d["internal_days"] or 0), external_days=float(d["external_days"] or 0),
        planned_online_date=story.get("due") or d["planned_online_date"],
        actual_online_date=story.get("completed") or d["actual_online_date"],
        user_test_date=d["user_test_date"], test_complete_date=d["test_complete_date"], demand_confirm_date=d["demand_confirm_date"],
        tasks=tasks, costs=costs,
    )


def test_tapd_connection(conn):
    cfg = tapd_runtime_config(conn)
    if cfg["mode"] == "mock":
        return {**cfg, "connected": True, "message": "Mock模式运行正常，切换Live后将调用TAPD开放平台。", "story_count": None, "task_count": None}
    cfg = _tapd_live_ready(conn)
    story_body = _tapd_request(conn, "GET", "/stories/count", params={"workspace_id": cfg["workspace_id"]})
    task_body = _tapd_request(conn, "GET", "/tasks/count", params={"workspace_id": cfg["workspace_id"]})
    story_count = ((story_body.get("data") or {}).get("count") if isinstance(story_body, dict) else None)
    task_count = ((task_body.get("data") or {}).get("count") if isinstance(task_body, dict) else None)
    return {**cfg, "connected": True, "message": "TAPD连接成功", "story_count": story_count, "task_count": task_count}


def _tapd_payload(conn, demand_id: int, system_name: str, allocation_id=None, function_point=None):
    d = conn.execute("SELECT * FROM demands WHERE id=?", (demand_id,)).fetchone()
    attachments = [r["original_name"] for r in conn.execute("SELECT original_name FROM attachments WHERE demand_id=? ORDER BY id", (demand_id,))]
    try:
        budget_sources = json.loads(d["budget_sources"] or "[]")
    except Exception:
        budget_sources = []
    fp = dict(function_point) if function_point else {}
    fp_no = fp.get("fp_no") or ""
    fp_name = fp.get("name") or ""
    title = d["title"]
    if fp_no or fp_name:
        title = f"{d['title']} - {' '.join(v for v in (fp_no, fp_name) if v)}"
    return {
        "外部ID": d["demand_no"],
        "标题": title,
        "描述": d["description"],
        "需求类型": d["demand_type"],
        "预算出处": budget_sources,
        "优先级": {"高": "High", "中": "Medium", "低": "Low"}.get(d["priority"], d["priority"]),
        "申请人": d["applicant"],
        "附件上传": attachments,
        "归属系统": system_name,
        "分摊记录ID": allocation_id,
        "功能点ID": fp.get("id"),
        "功能点编号": fp_no,
        "功能点名称": fp_name,
    }


def _tapd_splits(conn, demand_id: int, strategy: str = "function_point"):
    """TAPD拆分的业务主键固定为功能点，保证状态和工时可以一对一回写。"""
    points = list(conn.execute(
        "SELECT * FROM function_points WHERE demand_id=? ORDER BY fp_no,id", (demand_id,)
    ))
    if not points:
        return [("demand:default", "默认系统", None, None)]
    result = []
    for fp in points:
        allocation = conn.execute(
            "SELECT id FROM allocations WHERE demand_id=? AND function_point_id=? ORDER BY id LIMIT 1",
            (demand_id, fp["id"]),
        ).fetchone()
        result.append((f"fp:{fp['id']}", fp["system_name"] or "默认系统", allocation["id"] if allocation else None, fp))
    return result


def create_tapd_requirements(conn, demand_id: int, request_id: str = ""):
    existing = conn.execute("SELECT COUNT(*) c FROM tapd_requirements WHERE demand_id=?", (demand_id,)).fetchone()["c"]
    if existing:
        raise BusinessError(409, "REQ-4090", "该REQ编号已创建TAPD需求，请勿重复推送")
    strategy = "function_point"
    mode = tapd_runtime_config(conn)["mode"]
    splits = _tapd_splits(conn, demand_id, strategy)
    created = []
    for idx, (split_key, system_name, allocation_id, function_point) in enumerate(splits, start=1):
        payload = _tapd_payload(conn, demand_id, system_name, allocation_id, function_point)
        now = now_iso()
        if mode == "live":
            cfg = _tapd_live_ready(conn)
            post_data = {
                "workspace_id": cfg["workspace_id"],
                "name": payload["标题"],
                "description": f"{payload['描述']}\n\n[TRM外部ID] {payload['外部ID']}\n[功能点] {payload['功能点编号']} {payload['功能点名称']}\n[归属系统] {system_name}",
                "priority_label": payload["优先级"],
            }
            body = _tapd_request(conn, "POST", "/stories", data=post_data)
            data = body.get("data", {}) if isinstance(body, dict) else {}
            story = data.get("Story", data) if isinstance(data, dict) else {}
            tapd_id = str(story.get("id") or "")
            if not tapd_id:
                raise BusinessError(502, "TAPD-5020", "TAPD创建需求成功响应中缺少需求ID")
            tapd_url = f"https://www.tapd.cn/{cfg['workspace_id']}/prong/stories/view/{tapd_id}"
            tapd_status = _tapd_status_to_poc(story, "新")
        else:
            tapd_id = f"TAPD-{datetime.now().strftime('%Y%m%d')}-{demand_id:05d}-{idx:02d}"
            tapd_url = f"https://tapd.example.local/requirements/{tapd_id}"
            tapd_status = "新"
        conn.execute(
            """INSERT INTO tapd_requirements
            (demand_id,split_key,system_name,allocation_id,function_point_id,tapd_id,tapd_url,tapd_status,sync_status,payload_json,created_at,last_sync_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (demand_id, split_key, system_name, allocation_id, payload["功能点ID"], tapd_id, tapd_url, tapd_status, "成功", json.dumps(payload, ensure_ascii=False), now, now),
        )
        _integration_log(conn, "tapd", "out", "create_requirement", tapd_id, True, f"{mode}模式按功能点创建TAPD需求：{payload['功能点编号'] or system_name}", request_id)
        created.append(dict(conn.execute("SELECT * FROM tapd_requirements WHERE tapd_id=?", (tapd_id,)).fetchone()))
    first = created[0]
    conn.execute(
        """UPDATE demands SET status='已创建',current_node='TAPD已创建',tapd_id=?,tapd_url=?,tapd_status=?,
           tapd_sync_status='成功',tapd_last_sync_at=?,updated_at=? WHERE id=?""",
        (first["tapd_id"], first["tapd_url"], first["tapd_status"], now_iso(), now_iso(), demand_id),
    )
    return created


def push_demand_update_to_tapd(conn, demand_id: int, request_id: str = ""):
    """把TRM中已维护的需求和工时计划回写到已建立的TAPD需求。"""
    cfg = _tapd_live_ready(conn)
    demand = conn.execute("SELECT * FROM demands WHERE id=?", (demand_id,)).fetchone()
    if not demand:
        raise BusinessError(404, "REQ-4040", "需求不存在")
    requirements = list(conn.execute(
        "SELECT * FROM tapd_requirements WHERE demand_id=? ORDER BY id", (demand_id,)
    ))
    if not requirements:
        raise BusinessError(409, "REQ-4091", "尚未创建TAPD需求，无法回写")
    updated = []
    for requirement in requirements:
        system_name = requirement["system_name"] or "默认系统"
        function_point = None
        if requirement["function_point_id"]:
            function_point = conn.execute("SELECT * FROM function_points WHERE id=?", (requirement["function_point_id"],)).fetchone()
        payload = _tapd_payload(conn, demand_id, system_name, requirement["allocation_id"], function_point)
        post_data = {
            "id": requirement["tapd_id"],
            "workspace_id": cfg["workspace_id"],
            "name": payload["标题"],
            "description": f"{payload['描述']}\n\n[TRM外部ID] {payload['外部ID']}\n[功能点] {payload['功能点编号']} {payload['功能点名称']}\n[归属系统] {system_name}",
            "priority_label": payload["优先级"],
        }
        if demand["expected_completion_date"]:
            post_data["due"] = demand["expected_completion_date"]
        if float(demand["estimated_hours"] or 0) > 0:
            post_data["effort"] = round(float(demand["estimated_hours"]), 2)
        body = _tapd_request(conn, "POST", "/stories", data=post_data)
        response_data = body.get("data", {}) if isinstance(body, dict) else {}
        story = response_data.get("Story", response_data) if isinstance(response_data, dict) else {}
        tapd_status = _tapd_status_to_poc(story, requirement["tapd_status"] or "新")
        url = requirement["tapd_url"] or f"https://www.tapd.cn/{cfg['workspace_id']}/prong/stories/view/{requirement['tapd_id']}"
        conn.execute(
            """UPDATE tapd_requirements SET tapd_url=?,tapd_status=?,sync_status='成功',payload_json=?,last_sync_at=?
               WHERE id=?""",
            (url, tapd_status, json.dumps(post_data, ensure_ascii=False), now_iso(), requirement["id"]),
        )
        _integration_log(conn, "tapd", "out", "update_requirement", requirement["tapd_id"], True,
                         "TRM需求和工时计划已回写TAPD", request_id)
        updated.append({"tapd_id": requirement["tapd_id"], "tapd_status": tapd_status, "tapd_url": url})
    conn.execute(
        """UPDATE demands SET tapd_sync_status='成功',tapd_last_sync_at=?,last_sync_source='TRM回写',
           tapd_url=COALESCE(NULLIF(tapd_url,''),?),updated_at=? WHERE id=?""",
        (now_iso(), updated[0]["tapd_url"], now_iso(), demand_id),
    )
    conn.execute(
        "INSERT INTO tapd_sync_runs(demand_id,source,changed_count,success,message,created_at) VALUES (?,?,?,?,?,?)",
        (demand_id, "TRM回写", len(updated), 1, f"已更新TAPD需求{len(updated)}条", now_iso()),
    )
    return updated

def schedule_tapd_retry(conn, demand_id: int, request_id: str = ""):
    existing = conn.execute("SELECT * FROM tapd_retry_jobs WHERE demand_id=? AND status='等待重试' ORDER BY id DESC LIMIT 1", (demand_id,)).fetchone()
    if existing:
        return dict(existing)
    retry_seconds = int(float(get_setting(conn, "tapd_retry_seconds", "30")))
    now = _now_dt()
    next_retry = (now + timedelta(seconds=retry_seconds)).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO tapd_events(demand_id,event_type,success,attempt,request_id,message,created_at) VALUES (?,?,?,?,?,?,?)",
        (demand_id, "CREATE", 0, 1, request_id, f"第1次调用失败；{retry_seconds}秒后自动重试", now.isoformat(timespec="seconds")),
    )
    cur = conn.execute(
        """INSERT INTO tapd_retry_jobs(demand_id,attempt_count,next_retry_at,force_fail,status,last_error,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (demand_id, 1, next_retry, 1, "等待重试", "模拟上游连接失败", now.isoformat(timespec="seconds"), now.isoformat(timespec="seconds")),
    )
    conn.execute("UPDATE demands SET status='TAPD同步重试中',current_node='TAPD同步重试中',tapd_sync_status='重试中',updated_at=? WHERE id=?", (now_iso(), demand_id))
    _integration_log(conn, "tapd", "out", "create_requirement", demand_id, False, f"第1次失败，{retry_seconds}秒后自动重试", request_id)
    return dict(conn.execute("SELECT * FROM tapd_retry_jobs WHERE id=?", (cur.lastrowid,)).fetchone())


def process_retry_jobs(force: bool = False):
    now = _now_dt()
    processed = 0
    with connect() as conn:
        rows = list(conn.execute("SELECT * FROM tapd_retry_jobs WHERE status='等待重试' ORDER BY id"))
        for row in rows:
            due = _parse_iso(row["next_retry_at"])
            if not force and due and due > now:
                continue
            attempt = int(row["attempt_count"]) + 1
            retry_seconds = int(float(get_setting(conn, "tapd_retry_seconds", "30")))
            # POC失败演示任务保持失败，直到第3次；真实成功路径由正常创建接口完成。
            success = not bool(row["force_fail"])
            conn.execute(
                "INSERT INTO tapd_events(demand_id,event_type,success,attempt,request_id,message,created_at) VALUES (?,?,?,?,?,?,?)",
                (row["demand_id"], "CREATE", 1 if success else 0, attempt, "background", "创建成功" if success else f"第{attempt}次调用失败", now_iso()),
            )
            if success:
                create_tapd_requirements(conn, row["demand_id"], "background")
                conn.execute("UPDATE tapd_retry_jobs SET status='成功',attempt_count=?,updated_at=? WHERE id=?", (attempt, now_iso(), row["id"]))
            elif attempt >= 3:
                conn.execute("UPDATE tapd_retry_jobs SET status='最终失败',attempt_count=?,updated_at=? WHERE id=?", (attempt, now_iso(), row["id"]))
                conn.execute("UPDATE demands SET status='TAPD同步失败',current_node='TAPD同步失败',tapd_sync_status='失败',updated_at=? WHERE id=?", (now_iso(), row["demand_id"]))
                _notification(conn, row["demand_id"], "error", "TAPD同步失败", "已按30秒间隔重试3次仍失败，请项目经理处理。", "project_manager")
                _integration_log(conn, "tapd", "out", "create_requirement", row["demand_id"], False, "第3次失败，已标记同步失败并告警", "background")
            else:
                next_retry = (now + timedelta(seconds=retry_seconds)).isoformat(timespec="seconds")
                conn.execute("UPDATE tapd_retry_jobs SET attempt_count=?,next_retry_at=?,updated_at=? WHERE id=?", (attempt, next_retry, now_iso(), row["id"]))
            processed += 1
    return processed


class TapdTaskPayload(BaseModel):
    task_id: str
    title: str = ""
    description: str = ""
    task_type: str = ""
    planned_start: Optional[str] = None
    planned_end: Optional[str] = None
    estimated_hours: float = 0
    creator: str = ""
    created_at: Optional[str] = None
    completed_at: Optional[str] = None
    completed_hours: float = 0
    remaining_hours: float = 0
    overrun_hours: float = 0


class TapdCostPayload(BaseModel):
    task_id: str = ""
    spent_date: Optional[str] = None
    hours: float = 0
    creator: str = ""
    description: str = ""


class TapdWebhookPayload(BaseModel):
    tapd_id: Optional[str] = None
    demand_no: Optional[str] = None
    status: str = "新"
    demand_description: str = ""
    rd_owner: str = ""
    rd_department: str = ""
    internal_days: float = 0
    external_days: float = 0
    planned_online_date: Optional[str] = None
    actual_online_date: Optional[str] = None
    user_test_date: Optional[str] = None
    test_complete_date: Optional[str] = None
    demand_confirm_date: Optional[str] = None
    tasks: list[TapdTaskPayload] = Field(default_factory=list)
    costs: list[TapdCostPayload] = Field(default_factory=list)


def _notify_deviation(conn, demand_id: int, estimated_hours: float, actual_hours: float):
    if estimated_hours <= 0:
        conn.execute(
            "UPDATE notifications SET resolved_at=COALESCE(resolved_at,?) WHERE demand_id=? AND title IN ('工时偏差预警','工时超支预警') AND resolved_at IS NULL",
            (now_iso(), demand_id),
        )
        return None
    # “超支”只在实际工时超过预估时成立；实际工时较少不应误报为超时。
    deviation = max(0.0, (actual_hours - estimated_hours) / estimated_hours * 100)
    if deviation > 30:
        content = f"实际工时 {actual_hours:.1f}h，预估工时 {estimated_hours:.1f}h，超支率 {deviation:.1f}% > 30%，请及时处理。"
        event_key = f"work_deviation:{demand_id}:{estimated_hours:.2f}:{actual_hours:.2f}"
        # 旧版本的两条角色消息没有事件编号，升级时自动归并到同一业务事件。
        conn.execute(
            """UPDATE notifications SET event_key=? WHERE demand_id=?
               AND title IN ('工时偏差预警','工时超支预警') AND content=? AND COALESCE(event_key,'')=''""",
            (event_key, demand_id, content),
        )
        conn.execute(
            """UPDATE notifications SET resolved_at=COALESCE(resolved_at,?) WHERE demand_id=?
               AND title IN ('工时偏差预警','工时超支预警') AND COALESCE(event_key,'')<>? AND resolved_at IS NULL""",
            (now_iso(), demand_id, event_key),
        )
        for role in ("product_manager", "project_manager"):
            exists = conn.execute(
                """SELECT 1 FROM notifications
                   WHERE event_key=? AND target_role=? AND resolved_at IS NULL
                   LIMIT 1""",
                (event_key, role),
            ).fetchone()
            if not exists:
                _notification(conn, demand_id, "warning", "工时偏差预警", content, role, event_key)
    else:
        conn.execute(
            """UPDATE notifications SET resolved_at=COALESCE(resolved_at,?) WHERE demand_id=?
               AND title IN ('工时偏差预警','工时超支预警') AND resolved_at IS NULL""",
            (now_iso(), demand_id),
        )
    return deviation


def _notify_work_overdue(conn, demand_id: int, due_date: Optional[str], status: str):
    closed = status in ("已完成", "已终止", "已验收", "已关闭", "已拒绝")
    due = _parse_iso(due_date)
    overdue = bool(due and not closed and due.date() < _now_dt().date())
    if not overdue:
        conn.execute(
            "UPDATE notifications SET resolved_at=COALESCE(resolved_at,?) WHERE demand_id=? AND title='工时计划逾期预警' AND resolved_at IS NULL",
            (now_iso(), demand_id),
        )
        return False
    content = f"计划完成日期 {due.date().isoformat()} 已超期，需要更新计划或推动任务闭环。"
    event_key = f"work_overdue:{demand_id}:{due.date().isoformat()}"
    conn.execute(
        """UPDATE notifications SET event_key=? WHERE demand_id=? AND title='工时计划逾期预警'
           AND content=? AND COALESCE(event_key,'')=''""",
        (event_key, demand_id, content),
    )
    conn.execute(
        """UPDATE notifications SET resolved_at=COALESCE(resolved_at,?) WHERE demand_id=?
           AND title='工时计划逾期预警' AND COALESCE(event_key,'')<>? AND resolved_at IS NULL""",
        (now_iso(), demand_id, event_key),
    )
    for role in ("product_manager", "project_manager"):
        exists = conn.execute(
            """SELECT 1 FROM notifications WHERE event_key=? AND target_role=? AND resolved_at IS NULL LIMIT 1""",
            (event_key, role),
        ).fetchone()
        if not exists:
            _notification(conn, demand_id, "warning", "工时计划逾期预警", content, role, event_key)
    return True


def reconcile_work_deviation_notifications(conn, demand_id: Optional[int] = None) -> int:
    """为历史数据和非TAPD写入的工时数据补齐真实预警消息。

    相同需求、相同偏差值、相同目标角色只写入一次，避免用户刷新详情或
    消息中心时重复刷屏。
    """
    # 消息中心可能并行刷新；单进程内串行执行补偿扫描，避免两个读事务同时升级为写事务。
    with _RECONCILE_LOCK:
        sql = "SELECT id,estimated_hours,actual_hours,expected_completion_date,status FROM demands"
        params: tuple = ()
        if demand_id is not None:
            sql += " WHERE id=?"
            params = (demand_id,)
        before = conn.total_changes
        for row in conn.execute(sql, params):
            _notify_deviation(
                conn,
                int(row["id"]),
                float(row["estimated_hours"] or 0),
                float(row["actual_hours"] or 0),
            )
            _notify_work_overdue(conn, int(row["id"]), row["expected_completion_date"], row["status"])
        return conn.total_changes - before


def _aggregate_tapd_state(conn, demand_id: int):
    rows = list(conn.execute(
        "SELECT tapd_status FROM tapd_requirements WHERE demand_id=? ORDER BY id", (demand_id,)
    ))
    statuses = [r["tapd_status"] or "新" for r in rows]
    if not statuses:
        return "新", "已创建"
    if len(set(statuses)) == 1:
        status = statuses[0]
        return status, TAPD_STATUS_MAP.get(status, "已创建")
    if all(s == "已关闭" for s in statuses):
        return "已关闭", "已完成"
    if all(s == "已拒绝" for s in statuses):
        return "已拒绝", "已终止"
    active = [s for s in statuses if s != "已拒绝"]
    rank = {"新": 0, "开发中": 1, "测试中": 2, "已验收": 3, "已关闭": 4}
    base = min(active, key=lambda s: rank.get(s, 0)) if active else "新"
    system_status = TAPD_STATUS_MAP.get(base, "开发中")
    closed = sum(1 for s in statuses if s == "已关闭")
    rejected = sum(1 for s in statuses if s == "已拒绝")
    suffix = f"，{rejected}条拒绝" if rejected else ""
    return f"多状态（{closed}/{len(statuses)}已关闭{suffix}）", system_status


def apply_tapd_payload(conn, demand_id: int, payload: TapdWebhookPayload, source: str, request_id: str = ""):
    if payload.status not in TAPD_STATUS_MAP:
        raise BusinessError(400, "REQ-4001", "无效TAPD状态")
    requirement = None
    if payload.tapd_id:
        requirement = conn.execute("SELECT * FROM tapd_requirements WHERE tapd_id=? AND demand_id=?", (payload.tapd_id, demand_id)).fetchone()
        if not requirement:
            raise BusinessError(404, "REQ-4040", "TAPD需求ID未绑定到该需求，已拒绝回写", {"tapdId": payload.tapd_id})
    else:
        requirements = list(conn.execute("SELECT * FROM tapd_requirements WHERE demand_id=? ORDER BY id", (demand_id,)))
        if len(requirements) > 1:
            raise BusinessError(400, "REQ-4001", "一对多需求回写必须携带TAPD需求ID")
        requirement = requirements[0] if requirements else None
    req_id = requirement["id"] if requirement else None

    # 回读是该TAPD需求当前任务的完整快照，移除上次已存在、本次已删除的任务。
    incoming_task_ids = [t.task_id for t in payload.tasks if t.task_id]
    if req_id is not None:
        if incoming_task_ids:
            placeholders = ",".join("?" for _ in incoming_task_ids)
            conn.execute(
                f"DELETE FROM tapd_tasks WHERE demand_id=? AND tapd_requirement_id=? AND external_task_id NOT IN ({placeholders})",
                (demand_id, req_id, *incoming_task_ids),
            )
        else:
            conn.execute("DELETE FROM tapd_tasks WHERE demand_id=? AND tapd_requirement_id=?", (demand_id, req_id))

    for t in payload.tasks:
        conn.execute(
            """INSERT INTO tapd_tasks(demand_id,tapd_requirement_id,external_task_id,title,description,task_type,planned_start,planned_end,
               estimated_hours,creator,external_created_at,completed_at,completed_hours,remaining_hours,overrun_hours,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(demand_id,external_task_id) DO UPDATE SET
               tapd_requirement_id=excluded.tapd_requirement_id,title=excluded.title,description=excluded.description,task_type=excluded.task_type,
               planned_start=excluded.planned_start,planned_end=excluded.planned_end,estimated_hours=excluded.estimated_hours,creator=excluded.creator,
               external_created_at=excluded.external_created_at,completed_at=excluded.completed_at,completed_hours=excluded.completed_hours,
               remaining_hours=excluded.remaining_hours,overrun_hours=excluded.overrun_hours,updated_at=excluded.updated_at""",
            (demand_id, req_id, t.task_id, t.title, t.description, t.task_type, t.planned_start, t.planned_end,
             t.estimated_hours, t.creator, t.created_at, t.completed_at, t.completed_hours, t.remaining_hours, t.overrun_hours, now_iso()),
        )
    if req_id is None:
        conn.execute("DELETE FROM tapd_costs WHERE demand_id=?", (demand_id,))
    else:
        conn.execute(
            "DELETE FROM tapd_costs WHERE demand_id=? AND (tapd_requirement_id=? OR tapd_requirement_id IS NULL)",
            (demand_id, req_id),
        )
    if payload.costs:
        for c in payload.costs:
            conn.execute(
                "INSERT INTO tapd_costs(demand_id,tapd_requirement_id,task_external_id,spent_date,hours,creator,description,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (demand_id, req_id, c.task_id, c.spent_date, c.hours, c.creator, c.description, now_iso()),
            )

    sums = conn.execute(
        "SELECT COALESCE(SUM(estimated_hours),0) e,COALESCE(SUM(completed_hours),0) a FROM tapd_tasks WHERE demand_id=?",
        (demand_id,),
    ).fetchone()
    estimated_hours = float(sums["e"] or 0)
    cost_sum = conn.execute(
        "SELECT COALESCE(SUM(hours),0) a,COUNT(*) c FROM tapd_costs WHERE demand_id=?",
        (demand_id,),
    ).fetchone()
    # TAPD工时填报是实际投入的权威来源；无填报时才回退到任务已完成工时。
    actual_hours = float(cost_sum["a"] or 0) if int(cost_sum["c"] or 0) else float(sums["a"] or 0)
    work_hour_source = "TAPD工时填报" if int(cost_sum["c"] or 0) else "TAPD任务进度"
    if requirement:
        conn.execute(
            "UPDATE tapd_requirements SET tapd_status=?,sync_status='成功',last_sync_at=? WHERE id=?",
            (payload.status, now_iso(), requirement["id"]),
        )
    aggregate_tapd_status, sys_status = _aggregate_tapd_state(conn, demand_id)
    closed_at = now_iso() if sys_status in ("已完成", "已终止") else None
    conn.execute(
        """UPDATE demands SET tapd_description=?,rd_owner=?,rd_department=?,internal_days=?,external_days=?,planned_online_date=?,actual_online_date=?,
           user_test_date=?,test_complete_date=?,demand_confirm_date=?,expected_completion_date=?,tapd_status=?,status=?,current_node=?,tapd_sync_status='成功',tapd_last_sync_at=?,
           last_sync_source=?,estimated_hours=?,actual_hours=?,work_hour_source=?,work_plan_source='TAPD任务',actual_hours_source=?,work_plan_updated_at=?,closed_at=COALESCE(?,closed_at),updated_at=? WHERE id=?""",
        (payload.demand_description, payload.rd_owner, payload.rd_department, payload.internal_days, payload.external_days,
         payload.planned_online_date, payload.actual_online_date, payload.user_test_date, payload.test_complete_date, payload.demand_confirm_date,
         payload.planned_online_date, aggregate_tapd_status, sys_status, sys_status, now_iso(), source, estimated_hours, actual_hours,
         work_hour_source, work_hour_source, now_iso(), closed_at, now_iso(), demand_id),
    )
    changed = len(payload.tasks) + len(payload.costs) + 1
    conn.execute(
        "INSERT INTO tapd_sync_runs(demand_id,source,changed_count,success,message,created_at) VALUES (?,?,?,?,?,?)",
        (demand_id, source, changed, 1, f"TAPD {payload.tapd_id or '需求'} 状态 {payload.status} 已同步，任务{len(payload.tasks)}条、花费{len(payload.costs)}条", now_iso()),
    )
    _integration_log(conn, "tapd", "in", "readback", payload.tapd_id or demand_id, True, f"{source}回读完成", request_id)
    deviation = _notify_deviation(conn, demand_id, estimated_hours, actual_hours)
    _notify_work_overdue(conn, demand_id, payload.planned_online_date, sys_status)
    return {"system_status": sys_status, "tapd_status": aggregate_tapd_status, "deviation": deviation, "changed_count": changed}


def build_mock_sync_payload(conn, demand_id: int, status: Optional[str] = None, tapd_id: Optional[str] = None):
    d = conn.execute("SELECT * FROM demands WHERE id=?", (demand_id,)).fetchone()
    if not d:
        raise BusinessError(404, "REQ-4040", "需求不存在")
    current = status or d["tapd_status"] or "新"
    if current not in TAPD_STATUS_MAP:
        current = "新"
    req = None
    if tapd_id:
        req = conn.execute("SELECT * FROM tapd_requirements WHERE demand_id=? AND tapd_id=?", (demand_id, tapd_id)).fetchone()
    if not req:
        req = conn.execute("SELECT * FROM tapd_requirements WHERE demand_id=? ORDER BY id LIMIT 1", (demand_id,)).fetchone()
    tapd_id = tapd_id or (req["tapd_id"] if req else d["tapd_id"])
    progress = {"新": 0.0, "开发中": 0.35, "测试中": 0.75, "已验收": 1.05, "已关闭": 1.12, "已拒绝": 0.0}[current]
    fp_sum = conn.execute("SELECT COALESCE(SUM(fp_count),0) s FROM function_points WHERE demand_id=?", (demand_id,)).fetchone()["s"]
    estimated = max(16.0, float(fp_sum or 0) * 2.5)
    completed = round(estimated * progress, 2)
    remaining = max(0, round(estimated - completed, 2))
    overrun = max(0, round(completed - estimated, 2))
    req_suffix = req["id"] if req else 1
    task_id = f"TASK-{demand_id:05d}-{req_suffix:02d}"
    today = datetime.now().strftime("%Y-%m-%d")
    return TapdWebhookPayload(
        tapd_id=tapd_id,
        demand_no=d["demand_no"],
        status=current,
        demand_description=d["description"],
        rd_owner="研发负责人-赵敏",
        rd_department="产品研发部",
        internal_days=round(estimated / 8 / 2, 1),
        external_days=round(estimated / 8 / 4, 1),
        planned_online_date=d["planned_online_date"] or "2026-09-30",
        actual_online_date=today if current == "已关闭" else d["actual_online_date"],
        user_test_date="2026-09-20" if current in ("测试中", "已验收", "已关闭") else None,
        test_complete_date="2026-09-24" if current in ("已验收", "已关闭") else None,
        demand_confirm_date="2026-09-25" if current in ("已验收", "已关闭") else None,
        tasks=[TapdTaskPayload(
            task_id=task_id,
            title=f"{d['title']} - 开发任务",
            description="由TAPD回读的关联研发任务",
            task_type="开发任务",
            planned_start="2026-09-01",
            planned_end="2026-09-25",
            estimated_hours=estimated,
            creator="赵敏",
            created_at="2026-09-01T09:00:00+08:00",
            completed_at=today if current in ("已验收", "已关闭") else None,
            completed_hours=completed,
            remaining_hours=remaining,
            overrun_hours=overrun,
        )],
        costs=[TapdCostPayload(task_id=task_id, spent_date=today, hours=max(0, completed), creator="研发工程师", description="研发任务工时回填")],
    )


def run_scheduled_tapd_sync(force: bool = False):
    now = _now_dt()
    synced = 0
    with connect() as conn:
        interval = int(float(get_setting(conn, "tapd_sync_interval_seconds", "1800")))
        rows = list(conn.execute(
            """SELECT tr.*,d.status demand_status FROM tapd_requirements tr
                 JOIN demands d ON d.id=tr.demand_id
                WHERE tr.tapd_id IS NOT NULL AND tr.tapd_id<>'' AND d.status NOT IN ('已终止')
                ORDER BY tr.id"""
        ))
        for requirement in rows:
            last = _parse_iso(requirement["last_sync_at"])
            if not force and last and (now - last).total_seconds() < interval:
                continue
            if tapd_runtime_config(conn)["mode"] == "live":
                payload = build_live_sync_payload(conn, requirement["demand_id"], requirement["tapd_id"])
            else:
                payload = build_mock_sync_payload(
                    conn, requirement["demand_id"], requirement["tapd_status"] or "新", requirement["tapd_id"]
                )
            apply_tapd_payload(conn, requirement["demand_id"], payload, "定时任务", "background")
            synced += 1
    return synced


async def background_worker():
    while True:
        try:
            scan_oa_timeouts(False)
            process_retry_jobs(False)
            run_scheduled_tapd_sync(False)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(float(os.getenv("TRM_BACKGROUND_SCAN_SECONDS", "30")))


class SettingsPayload(BaseModel):
    tapd_split_strategy: Optional[str] = None
    tapd_sync_interval_seconds: Optional[int] = None
    tapd_retry_seconds: Optional[int] = None
    tapd_mode: Optional[str] = None
    tapd_workspace_id: Optional[str] = None
    tapd_base_url: Optional[str] = None


@router.get("/poc/settings")
def poc_settings():
    with connect() as conn:
        rows = {r["code"]: r["value"] for r in conn.execute("SELECT code,value FROM system_settings")}
        rows.update({"tapd_credentials_ready": tapd_runtime_config(conn)["credentials_ready"], "tapd_api_user_masked": tapd_runtime_config(conn)["api_user_masked"]})
        return {"code": 0, "data": rows}


@router.put("/poc/settings")
def update_poc_settings(payload: SettingsPayload, x_role: Optional[str] = Header(None)):
    with connect() as conn:
        now = now_iso()
        if payload.tapd_split_strategy is not None:
            if payload.tapd_split_strategy != "function_point":
                raise BusinessError(400, "REQ-4001", "TAPD需求固定按功能点一对一生成")
            conn.execute("UPDATE system_settings SET value=?,updated_at=? WHERE code='tapd_split_strategy'", (payload.tapd_split_strategy, now))
        if payload.tapd_sync_interval_seconds is not None:
            if payload.tapd_sync_interval_seconds < 60:
                raise BusinessError(400, "REQ-4001", "TAPD定时回读间隔不能小于60秒")
            conn.execute("UPDATE system_settings SET value=?,updated_at=? WHERE code='tapd_sync_interval_seconds'", (str(payload.tapd_sync_interval_seconds), now))
        if payload.tapd_retry_seconds is not None:
            if payload.tapd_retry_seconds < 1:
                raise BusinessError(400, "REQ-4001", "TAPD重试间隔不能小于1秒")
            conn.execute("UPDATE system_settings SET value=?,updated_at=? WHERE code='tapd_retry_seconds'", (str(payload.tapd_retry_seconds), now))
        if payload.tapd_mode is not None:
            if payload.tapd_mode not in ("mock", "live"):
                raise BusinessError(400, "REQ-4001", "TAPD运行模式仅支持mock/live")
            conn.execute("UPDATE system_settings SET value=?,updated_at=? WHERE code='tapd_mode'", (payload.tapd_mode, now))
        if payload.tapd_workspace_id is not None:
            conn.execute("UPDATE system_settings SET value=?,updated_at=? WHERE code='tapd_workspace_id'", (payload.tapd_workspace_id.strip(), now))
        if payload.tapd_base_url is not None:
            base = payload.tapd_base_url.strip().rstrip("/") or "https://api.tapd.cn"
            if not base.startswith("https://"):
                raise BusinessError(400, "REQ-4001", "TAPD API地址必须使用HTTPS")
            conn.execute("UPDATE system_settings SET value=?,updated_at=? WHERE code='tapd_base_url'", (base, now))
    return {"code": 0, "message": "POC集成策略已更新"}


@router.post("/tapd/test-connection")
def tapd_test_connection(x_role: Optional[str] = Header(None)):
    with connect() as conn:
        return {"code": 0, "message": "TAPD连接测试完成", "data": test_tapd_connection(conn)}


@router.get("/tapd/overview")
def tapd_overview():
    with connect() as conn:
        cfg = tapd_runtime_config(conn)
        split_strategy = get_setting(conn, "tapd_split_strategy", "system")
        sync_interval_seconds = int(float(get_setting(conn, "tapd_sync_interval_seconds", "1800")))
        retry_seconds = int(float(get_setting(conn, "tapd_retry_seconds", "30")))
        req_count = conn.execute("SELECT COUNT(*) c FROM tapd_requirements").fetchone()["c"]
        success_runs = conn.execute("SELECT COUNT(*) c FROM tapd_sync_runs WHERE success=1").fetchone()["c"]
        failed_runs = conn.execute("SELECT COUNT(*) c FROM tapd_sync_runs WHERE success=0").fetchone()["c"]
        waiting_retry = conn.execute("SELECT COUNT(*) c FROM tapd_retry_jobs WHERE status='等待重试'").fetchone()["c"]
        tasks = [dict(r) for r in conn.execute("""SELECT t.*,d.demand_no,d.title demand_title FROM tapd_tasks t JOIN demands d ON d.id=t.demand_id ORDER BY t.id DESC LIMIT 12""")]
        runs = [dict(r) for r in conn.execute("""SELECT r.*,d.demand_no,d.title demand_title FROM tapd_sync_runs r JOIN demands d ON d.id=r.demand_id ORDER BY r.id DESC LIMIT 12""")]
        return {"code": 0, "data": {
            "config": cfg,
            "split_strategy": split_strategy,
            "sync_interval_seconds": sync_interval_seconds,
            "retry_seconds": retry_seconds,
            "requirement_count": req_count,
            "success_runs": success_runs,
            "failed_runs": failed_runs,
            "waiting_retry": waiting_retry,
            "recent_tasks": tasks,
            "recent_runs": runs,
        }}


@router.get("/demands/{demand_id}/oa-tasks")
def demand_oa_tasks(demand_id: int):
    with connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM oa_tasks WHERE demand_id=? ORDER BY id", (demand_id,))]
    return {"code": 0, "data": rows}


@router.get("/oa/tasks")
def oa_tasks(status: str = "", role: str = ""):
    sql = "SELECT o.*,d.demand_no,d.title demand_title FROM oa_tasks o JOIN demands d ON d.id=o.demand_id WHERE 1=1"
    args = []
    if status:
        sql += " AND o.status=?"; args.append(status)
    if role:
        sql += " AND o.role=?"; args.append(role)
    sql += " ORDER BY o.id DESC"
    with connect() as conn:
        return {"code": 0, "data": [dict(r) for r in conn.execute(sql, args)]}


@router.post("/poc/jobs/scan")
def run_poc_jobs(force: bool = Query(False), x_role: Optional[str] = Header(None)):
    return {
        "code": 0,
        "data": {
            "oaReminders": scan_oa_timeouts(force),
            "tapdRetries": process_retry_jobs(force),
            "tapdScheduledSync": run_scheduled_tapd_sync(force),
        },
    }


@router.post("/tapd/webhook")
def tapd_webhook(payload: TapdWebhookPayload, request: Request, token: str = Query(""),
                 x_tapd_webhook_secret: Optional[str] = Header(None, alias="X-TAPD-Webhook-Secret")):
    with connect() as conn:
        cfg = tapd_runtime_config(conn)
        if cfg["mode"] == "live":
            expected = os.getenv("TRM_TAPD_WEBHOOK_SECRET", "").strip()
            supplied = (x_tapd_webhook_secret or token or "").strip()
            if not expected:
                raise BusinessError(503, "TAPD-5030", "Live模式未配置TRM_TAPD_WEBHOOK_SECRET，已拒绝无校验Webhook")
            if not supplied or not hmac.compare_digest(supplied, expected):
                raise BusinessError(401, "AUTH-4010", "TAPD Webhook校验失败")
        demand = None
        if payload.tapd_id:
            demand = conn.execute(
                "SELECT d.* FROM demands d JOIN tapd_requirements tr ON tr.demand_id=d.id WHERE tr.tapd_id=?",
                (payload.tapd_id,),
            ).fetchone()
        if not demand and payload.demand_no:
            demand = conn.execute("SELECT * FROM demands WHERE demand_no=?", (payload.demand_no,)).fetchone()
        if not demand:
            raise BusinessError(404, "REQ-4040", "Webhook未找到对应需求")
        result = apply_tapd_payload(conn, demand["id"], payload, "Webhook", getattr(request.state, "request_id", ""))
        return {"code": 0, "message": "Webhook回读成功", "data": result}


@router.post("/demands/{demand_id}/tapd/push-update")
def tapd_push_update(demand_id: int, request: Request,
                     x_user: Optional[str] = Header(None), x_role: Optional[str] = Header(None)):
    actor, role = _actor(x_user, x_role)
    with connect() as conn:
        records = push_demand_update_to_tapd(
            conn, demand_id, getattr(request.state, "request_id", "")
        )
        conn.execute(
            """INSERT INTO audit_logs(actor,role,action,object_type,object_id,result,request_id,details,created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (actor, role, "回写TAPD需求", "demand", str(demand_id), "成功",
             getattr(request.state, "request_id", ""), json.dumps({"count": len(records)}, ensure_ascii=False), now_iso()),
        )
        return {"code": 0, "message": f"已将TRM最新信息回写到TAPD（{len(records)}条）", "data": records}


@router.get("/budget-execution-trend")
def budget_execution_trend(department: str = "", period_type: str = "month"):
    if period_type not in ("month", "quarter"):
        raise BusinessError(400, "REQ-4001", "period_type仅支持month/quarter")
    sql = """SELECT s.period,SUM(s.used_amount) used_amount,SUM(s.total_budget) total_budget
             FROM budget_execution_snapshots s WHERE s.period_type=?"""
    args = [period_type]
    if department:
        sql += " AND s.department=?"; args.append(department)
    sql += " GROUP BY s.period ORDER BY s.period"
    with connect() as conn:
        rows = []
        for r in conn.execute(sql, args):
            d = dict(r)
            d["execution_rate"] = round(d["used_amount"] / d["total_budget"] * 100, 2) if d["total_budget"] else 0
            rows.append(d)
        return {"code": 0, "data": rows}
