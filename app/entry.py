from __future__ import annotations

import os
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Header, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from . import main as core, poc
from .db import connect, now_iso
from .extended import BudgetPayload, _actor, _audit, _next_no, _validate_budget_payload
from .rules import BusinessError

app = core.app
STATIC_DIR = Path(__file__).resolve().parent / "static"


def _add_column(conn, table, column, ddl):
    if column not in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_patch_db(conn):
    for column, ddl in {
        "status": "TEXT NOT NULL DEFAULT '已生效'",
        "current_node": "TEXT NOT NULL DEFAULT '已生效'",
        "applicant": "TEXT DEFAULT ''",
        "submitted_at": "TEXT",
        "approved_at": "TEXT",
        "updated_at": "TEXT",
    }.items():
        _add_column(conn, "budgets", column, ddl)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS budget_approvals(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            budget_id INTEGER NOT NULL,node TEXT NOT NULL,role TEXT NOT NULL,
            approver TEXT NOT NULL,action TEXT NOT NULL,comment TEXT DEFAULT '',created_at TEXT NOT NULL,
            FOREIGN KEY(budget_id) REFERENCES budgets(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_budget_approvals_budget ON budget_approvals(budget_id,id);
    """)
    conn.execute("UPDATE budgets SET status='已生效' WHERE status IS NULL OR status=''")
    conn.execute("UPDATE budgets SET current_node='已生效' WHERE current_node IS NULL OR current_node=''")


_original_tapd_runtime_config = poc.tapd_runtime_config

def tapd_runtime_config_env_first(conn):
    """Render/local env overrides stale persistent system_settings values."""
    cfg = dict(_original_tapd_runtime_config(conn))
    if os.getenv("TRM_TAPD_MODE", "").strip():
        cfg["mode"] = os.environ["TRM_TAPD_MODE"].strip().lower()
    if os.getenv("TRM_TAPD_WORKSPACE_ID", "").strip():
        cfg["workspace_id"] = os.environ["TRM_TAPD_WORKSPACE_ID"].strip()
    if os.getenv("TRM_TAPD_BASE_URL", "").strip():
        cfg["base_url"] = os.environ["TRM_TAPD_BASE_URL"].strip().rstrip("/")
    user = os.getenv("TRM_TAPD_API_USER", "").strip()
    password = os.getenv("TRM_TAPD_API_PASSWORD", "").strip()
    cfg["credentials_ready"] = bool(user and password)
    cfg["api_user_masked"] = (user[:2] + "***" + user[-2:]) if len(user) >= 5 else ("已配置" if user else "未配置")
    return cfg

poc.tapd_runtime_config = tapd_runtime_config_env_first
core.tapd_runtime_config = tapd_runtime_config_env_first


def multi_budget_snapshot(conn, demand):
    """Validate allocations grouped by the real expense source budget."""
    rows = list(conn.execute("""
        SELECT a.*,COALESCE(a.budget_id,b.id) resolved_budget_id
        FROM allocations a LEFT JOIN budgets b ON b.budget_name=a.expense_source
        WHERE a.demand_id=? ORDER BY a.id
    """, (demand["id"],)))
    grouped = defaultdict(float)
    if rows:
        for row in rows:
            if row["resolved_budget_id"]:
                grouped[int(row["resolved_budget_id"])] += float(row["amount"] or 0)
    else:
        sources = demand.get("budget_sources") or []
        if sources:
            b = conn.execute("SELECT * FROM budgets WHERE budget_name=? AND COALESCE(status,'已生效')='已生效'", (sources[0],)).fetchone()
            if b:
                grouped[int(b["id"])] = float(demand.get("estimated_amount") or demand.get("budget_amount") or 0)
    items = []
    for budget_id, amount in grouped.items():
        b = conn.execute("SELECT * FROM budgets WHERE id=?", (budget_id,)).fetchone()
        if not b:
            continue
        total, used = float(b["total_budget"] or 0), float(b["used_budget"] or 0)
        remaining = total - used
        committed = float(conn.execute("""
            SELECT COALESCE(SUM(a.amount),0) v FROM allocations a JOIN demands d ON d.id=a.demand_id
            WHERE d.id<>? AND d.status NOT IN ('草稿','已驳回','已终止')
              AND COALESCE(a.budget_id,(SELECT id FROM budgets x WHERE x.budget_name=a.expense_source LIMIT 1))=?
              AND COALESCE(a.ledger_status,'待占用')<>'已释放'
        """, (demand["id"], budget_id)).fetchone()["v"] or 0)
        after = used + amount
        item = dict(b)
        item.update({
            "remaining_budget": round(remaining, 2),
            "execution_rate": round(used / total * 100, 2) if total else 0,
            "after_execution_rate": round(after / total * 100, 2) if total else 0,
            "current_demand_amount": round(amount, 2),
            "committed_demand_amount": round(committed, 2),
            "commitment_after": round(committed + amount, 2),
            "sufficient": amount <= remaining + .01 and committed + amount <= total + .01,
            "warning": total > 0 and after / total >= .95,
            "internal_remaining": float(b["internal_total"] or 0) - float(b["internal_used"] or 0),
            "digital_remaining": float(b["digital_total"] or 0) - float(b["digital_used"] or 0),
        })
        items.append(item)
    if not items:
        return None
    result = {
        "items": items,
        "budget_count": len(items),
        "current_demand_total": round(sum(i["current_demand_amount"] for i in items), 2),
        "all_sufficient": all(i["sufficient"] for i in items),
        "sufficient": all(i["sufficient"] for i in items),
        "warning": any(i["warning"] for i in items),
    }
    result.update({k: v for k, v in items[0].items() if k not in result})
    return result

core.budget_snapshot = multi_budget_snapshot


def _remove_route(path, method=None):
    keep = []
    for route in app.router.routes:
        if getattr(route, "path", None) != path:
            keep.append(route); continue
        if method and method.upper() not in (getattr(route, "methods", None) or set()):
            keep.append(route)
    app.router.routes[:] = keep


_remove_route("/", "GET")
@app.get("/", response_class=HTMLResponse)
def index_v5():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html.replace("</body>", '<script src="/static/v5_fixes.js?v=5.0.0"></script>\n</body>'))

_remove_route("/api/meta", "GET")
@app.get("/api/meta")
def meta_v5():
    with connect() as conn:
        budgets = [dict(r) for r in conn.execute("SELECT * FROM budgets WHERE COALESCE(status,'已生效')='已生效' ORDER BY id")]
    return {"code": 0, "data": {
        "roles": core.get_role_labels(), "demoUsers": core.get_demo_users(),
        "demandTypes": core.DEMAND_TYPES, "priorities": core.PRIORITIES,
        "budgets": budgets, "tapdStatusMap": core.TAPD_STATUS_MAP,
    }}

_remove_route("/api/budgets", "POST")
@app.post("/api/budgets")
def create_budget_v5(payload: BudgetPayload, request: Request, x_user: Optional[str] = Header(None), x_role: Optional[str] = Header(None)):
    actor, role = _actor(x_user, x_role); _validate_budget_payload(payload)
    with connect() as conn:
        no = payload.budget_no or _next_no(conn, "budgets", "budget_no", f"BUD-{payload.year}-")
        cur = conn.execute("""
            INSERT INTO budgets(budget_no,budget_name,total_budget,used_budget,internal_total,internal_used,digital_total,digital_used,year,status,current_node,applicant,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (no,payload.budget_name,payload.total_budget,0,payload.internal_total,0,payload.digital_total,0,payload.year,"草稿","草稿",actor,now_iso()))
        _audit(conn, request, actor, role, "创建预算草稿", "budget", cur.lastrowid)
        return {"code":0,"message":"预算草稿已创建，请提交审批","data":{"id":cur.lastrowid,"budget_no":no,"status":"草稿"}}

@app.post("/api/budgets/{budget_id}/submit")
def submit_budget(budget_id: int, request: Request, x_user: Optional[str] = Header(None), x_role: Optional[str] = Header(None)):
    actor, role = _actor(x_user, x_role)
    with connect() as conn:
        b = conn.execute("SELECT * FROM budgets WHERE id=?", (budget_id,)).fetchone()
        if not b: raise BusinessError(404,"BUD-4040","预算不存在")
        if b["status"] not in ("草稿","已驳回"): raise BusinessError(409,"BUD-4091","当前预算状态不能提交审批")
        now=now_iso(); conn.execute("UPDATE budgets SET status='审批中',current_node='财务审批',submitted_at=?,updated_at=? WHERE id=?",(now,now,budget_id))
        _audit(conn,request,actor,role,"提交预算审批","budget",budget_id)
    return {"code":0,"message":"预算已提交财务审批","data":{"id":budget_id,"status":"审批中"}}

@app.get("/api/budget-approvals/pending")
def budget_pending(x_role: Optional[str] = Header(None)):
    if (x_role or "") not in ("finance","admin"): raise BusinessError(403,"AUTH-4030","仅财务人员或管理员可审批预算")
    with connect() as conn:
        rows=[dict(r) for r in conn.execute("SELECT * FROM budgets WHERE status='审批中' AND current_node='财务审批' ORDER BY submitted_at,id")]
    return {"code":0,"data":rows}

class BudgetApprovalPayload(BaseModel):
    action: str
    comment: str = ""

@app.post("/api/budgets/{budget_id}/approve")
def budget_approve(budget_id:int,payload:BudgetApprovalPayload,request:Request,x_user:Optional[str]=Header(None),x_role:Optional[str]=Header(None)):
    actor,role=_actor(x_user,x_role)
    if role not in ("finance","admin"): raise BusinessError(403,"AUTH-4030","仅财务人员或管理员可审批预算")
    if payload.action not in ("通过","驳回"): raise BusinessError(400,"BUD-4001","审批动作仅支持通过或驳回")
    with connect() as conn:
        b=conn.execute("SELECT * FROM budgets WHERE id=?",(budget_id,)).fetchone()
        if not b: raise BusinessError(404,"BUD-4040","预算不存在")
        if b["status"]!="审批中": raise BusinessError(409,"BUD-4091","当前预算不在审批中")
        now=now_iso(); status="已生效" if payload.action=="通过" else "已驳回"
        conn.execute("UPDATE budgets SET status=?,current_node=?,approved_at=?,updated_at=? WHERE id=?",(status,status,now if status=="已生效" else None,now,budget_id))
        conn.execute("INSERT INTO budget_approvals(budget_id,node,role,approver,action,comment,created_at) VALUES (?,?,?,?,?,?,?)",(budget_id,"财务审批",role,actor,payload.action,payload.comment,now))
        _audit(conn,request,actor,role,f"预算审批{payload.action}","budget",budget_id)
    return {"code":0,"message":f"预算已{payload.action}","data":{"id":budget_id,"status":status}}

_original_lifespan = app.router.lifespan_context
@asynccontextmanager
async def lifespan_v5(app_instance):
    async with _original_lifespan(app_instance):
        with connect() as conn: init_patch_db(conn)
        yield
app.router.lifespan_context = lifespan_v5
