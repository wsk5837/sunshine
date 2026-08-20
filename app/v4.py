import csv
import io
import json
import os
from datetime import datetime
from typing import Optional
from urllib.parse import unquote

import httpx
from fastapi import APIRouter, Header, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .db import connect, now_iso, row_to_dict
from .rules import BusinessError, ROLE_LABELS
from .auth import get_role_labels
from .trm_mcp import public_mcp_status

router = APIRouter(prefix="/api", tags=["V4完整平台能力"])


def _actor(x_user: Optional[str], x_role: Optional[str]):
    role = x_role or "applicant"
    if role not in get_role_labels():
        raise BusinessError(403, "AUTH-4030", "无效角色或无权限")
    return unquote(x_user) if x_user else "lili11-ghq", role


def _audit(conn, request: Request, actor, role, action, object_type, object_id, result="成功", details=None):
    conn.execute(
        """INSERT INTO audit_logs(actor,role,action,object_type,object_id,result,request_id,details,created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (actor, role, action, object_type, str(object_id) if object_id is not None else None,
         result, getattr(request.state, "request_id", None), json.dumps(details or {}, ensure_ascii=False), now_iso())
    )


def _add_column(conn, table, column, ddl):
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_v4_db():
    with connect() as conn:
        # 立项/项目/合同在原有可用数据模型上补全正常企业级字段。
        for col, ddl in {
            "project_type": "TEXT DEFAULT '系统建设'",
            "background": "TEXT DEFAULT ''",
            "objectives": "TEXT DEFAULT ''",
            "scope": "TEXT DEFAULT ''",
            "expected_benefit": "TEXT DEFAULT ''",
            "sponsor": "TEXT DEFAULT ''",
            "urgency": "TEXT DEFAULT '中'",
        }.items():
            _add_column(conn, "initiatives", col, ddl)
        for col, ddl in {
            "project_type": "TEXT DEFAULT '系统建设'",
            "sponsor": "TEXT DEFAULT ''",
            "health": "TEXT DEFAULT '正常'",
        }.items():
            _add_column(conn, "projects", col, ddl)
        for col, ddl in {
            "category": "TEXT DEFAULT '项目合同'",
            "sign_date": "TEXT",
            "archive_status": "TEXT DEFAULT '未归档'",
        }.items():
            _add_column(conn, "contracts", col, ddl)
        for col, ddl in {
            "invoice_no": "TEXT DEFAULT ''",
            "payee": "TEXT DEFAULT ''",
        }.items():
            _add_column(conn, "settlements", col, ddl)

        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS project_risks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                risk_no TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                category TEXT DEFAULT '进度风险',
                probability TEXT DEFAULT '中',
                impact TEXT DEFAULT '中',
                level TEXT DEFAULT '中',
                owner TEXT DEFAULT '',
                response_plan TEXT DEFAULT '',
                status TEXT DEFAULT '跟踪中',
                due_date TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS project_deliverables (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                deliverable_no TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                type TEXT DEFAULT '文档',
                milestone_id INTEGER,
                owner TEXT DEFAULT '',
                planned_date TEXT,
                actual_date TEXT,
                version TEXT DEFAULT 'V1.0',
                status TEXT DEFAULT '未提交',
                url TEXT DEFAULT '',
                description TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS contract_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract_id INTEGER NOT NULL,
                change_no TEXT UNIQUE NOT NULL,
                change_type TEXT DEFAULT '范围变更',
                reason TEXT NOT NULL,
                amount_delta REAL DEFAULT 0,
                before_amount REAL DEFAULT 0,
                after_amount REAL DEFAULT 0,
                owner TEXT DEFAULT '',
                status TEXT DEFAULT '草稿',
                effective_date TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(contract_id) REFERENCES contracts(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS settlement_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                settlement_id INTEGER NOT NULL,
                item_name TEXT NOT NULL,
                item_type TEXT DEFAULT '服务费',
                quantity REAL DEFAULT 1,
                unit_price REAL DEFAULT 0,
                amount REAL DEFAULT 0,
                description TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(settlement_id) REFERENCES settlements(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS integration_configs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                mode TEXT DEFAULT 'mock',
                base_url TEXT DEFAULT '',
                enabled INTEGER DEFAULT 1,
                last_check_at TEXT,
                status TEXT DEFAULT '正常',
                description TEXT DEFAULT '',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS integration_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                integration_code TEXT NOT NULL,
                direction TEXT NOT NULL,
                business_type TEXT NOT NULL,
                business_id TEXT DEFAULT '',
                success INTEGER DEFAULT 1,
                message TEXT DEFAULT '',
                request_id TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            """
        )

        # V4.9：AI集成增加公开智能体标识。这里只保存非敏感 agent_id，
        # 不保存后台管理员账号、密码或管理端 Token。
        _add_column(conn, "integration_configs", "agent_id", "TEXT DEFAULT ''")

        # 固定集成能力配置。AI运行时使用已核对的 G.AIOS 公共运行接口，
        # 默认调用已发布的 default 智能体；可在集成配置或环境变量中替换。
        now = now_iso()
        public_base = os.getenv("TRM_PUBLIC_BASE_URL", "").rstrip("/")
        if not public_base and os.getenv("RENDER_EXTERNAL_HOSTNAME"):
            public_base = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME')}"
        if not public_base:
            public_base = "http://127.0.0.1:8000"
        for code, name, mode, base_url, agent_id, desc in [
            ("oa", "OA审批集成", "mock", "", "", "立项、需求、合同、结算审批待办推送与状态回写"),
            ("tapd", "TAPD需求集成", "mock", "", "", "终审后创建需求、Webhook/定时/手动回读"),
            (
                "ai", "AI问答服务", "live",
                os.getenv("TRM_AI_BASE_URL", "https://adk.gazellio.com"),
                os.getenv("TRM_AI_AGENT_ID", "default"),
                "项目360机器人、AI问答与悬浮助手统一接入 Gazellio G.AIOS",
            ),
            (
                "mcp", "TRM MCP工具服务", "live",
                f"{public_base}/mcp/",
                "",
                "供G.AIOS智能体查询TRM数据，并在用户确认后幂等创建项目或需求草稿",
            ),
        ]:
            conn.execute(
                """INSERT OR IGNORE INTO integration_configs(code,name,mode,base_url,agent_id,enabled,status,description,updated_at)
                   VALUES (?,?,?,?,?,1,'正常',?,?)""",
                (code, name, mode, base_url, agent_id, desc, now),
            )
        # 从 V4.8 升级且仍保持旧版默认 Mock/空地址时，自动启用本次新增的真实AI适配器。
        conn.execute(
            """UPDATE integration_configs
               SET mode='live',base_url=?,agent_id=CASE WHEN COALESCE(agent_id,'')='' THEN ? ELSE agent_id END,
                   description=?,updated_at=?
               WHERE code='ai' AND mode='mock' AND COALESCE(base_url,'')=''""",
            (
                os.getenv("TRM_AI_BASE_URL", "https://adk.gazellio.com"),
                os.getenv("TRM_AI_AGENT_ID", "default"),
                "项目360机器人、AI问答与悬浮助手统一接入 Gazellio G.AIOS",
                now,
            ),
        )
        conn.execute(
            "UPDATE integration_configs SET agent_id=? WHERE code='ai' AND COALESCE(agent_id,'')=''",
            (os.getenv("TRM_AI_AGENT_ID", "default"),),
        )

        # 为种子项目补一条风险与交付物，让首次打开不是空壳。
        p = conn.execute("SELECT id FROM projects ORDER BY id LIMIT 1").fetchone()
        if p:
            pid = p["id"]
            if conn.execute("SELECT COUNT(*) c FROM project_risks WHERE project_id=?", (pid,)).fetchone()["c"] == 0:
                conn.execute(
                    """INSERT INTO project_risks(project_id,risk_no,title,category,probability,impact,level,owner,response_plan,status,due_date,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (pid, f"RSK-{datetime.now().year}-0001", "核心接口联调窗口偏紧", "进度风险", "中", "高", "高", "王卫嘉", "提前锁定接口人并每日跟踪联调清单", "跟踪中", "2026-09-10", now, now),
                )
            if conn.execute("SELECT COUNT(*) c FROM project_deliverables WHERE project_id=?", (pid,)).fetchone()["c"] == 0:
                conn.execute(
                    """INSERT INTO project_deliverables(project_id,deliverable_no,name,type,owner,planned_date,version,status,description,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (pid, f"DLV-{datetime.now().year}-0001", "需求规格说明书", "文档", "王卫嘉", "2026-09-05", "V1.0", "编制中", "项目需求基线文档", now, now),
                )


def _next_no(conn, table, field, prefix):
    row = conn.execute(f"SELECT {field} FROM {table} WHERE {field} LIKE ? ORDER BY {field} DESC LIMIT 1", (f"{prefix}%",)).fetchone()
    seq = int(row[field].split("-")[-1]) + 1 if row and row[field] else 1
    return f"{prefix}{seq:04d}"


class InitiativeProfile(BaseModel):
    project_type: str = "系统建设"
    background: str = ""
    objectives: str = ""
    scope: str = ""
    expected_benefit: str = ""
    sponsor: str = ""
    urgency: str = "中"


class RiskPayload(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    category: str = "进度风险"
    probability: str = "中"
    impact: str = "中"
    level: str = "中"
    owner: str = ""
    response_plan: str = ""
    status: str = "跟踪中"
    due_date: Optional[str] = None


class DeliverablePayload(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    type: str = "文档"
    milestone_id: Optional[int] = None
    owner: str = ""
    planned_date: Optional[str] = None
    actual_date: Optional[str] = None
    version: str = "V1.0"
    status: str = "未提交"
    url: str = ""
    description: str = ""


class ContractChangePayload(BaseModel):
    change_type: str = "范围变更"
    reason: str = Field(min_length=1, max_length=1000)
    amount_delta: float = 0
    owner: str = ""
    status: str = "草稿"
    effective_date: Optional[str] = None


class SettlementItemPayload(BaseModel):
    item_name: str = Field(min_length=1, max_length=200)
    item_type: str = "服务费"
    quantity: float = 1
    unit_price: float = 0
    description: str = ""


class IntegrationPayload(BaseModel):
    mode: str = "mock"
    base_url: str = ""
    agent_id: str = ""
    enabled: bool = True


@router.get("/v4/meta")
def v4_meta():
    return {
        "code": 0,
        "data": {
            "project_types": ["系统建设", "智能化改造", "基础设施", "咨询服务", "研发创新"],
            "urgencies": ["高", "中", "低"],
            "risk_levels": ["高", "中", "低"],
            "risk_categories": ["进度风险", "成本风险", "资源风险", "质量风险", "技术风险", "合规风险"],
            "deliverable_types": ["文档", "系统版本", "源代码", "配置项", "培训材料", "验收材料", "其他"],
            "contract_change_types": ["范围变更", "金额变更", "周期变更", "主体变更", "其他"],
        },
    }


@router.put("/initiatives/{item_id}/profile")
def update_initiative_profile(item_id: int, payload: InitiativeProfile, request: Request,
                              x_user: Optional[str] = Header(None), x_role: Optional[str] = Header(None)):
    actor, role = _actor(x_user, x_role)
    with connect() as conn:
        row = conn.execute("SELECT status FROM initiatives WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise BusinessError(404, "REQ-4040", "立项申请不存在")
        if row["status"] not in ("草稿", "已驳回"):
            raise BusinessError(409, "REQ-4091", "当前状态不允许修改立项内容")
        conn.execute(
            """UPDATE initiatives SET project_type=?,background=?,objectives=?,scope=?,expected_benefit=?,sponsor=?,urgency=?,updated_at=? WHERE id=?""",
            (payload.project_type, payload.background, payload.objectives, payload.scope, payload.expected_benefit,
             payload.sponsor, payload.urgency, now_iso(), item_id),
        )
        _audit(conn, request, actor, role, "维护立项扩展信息", "initiative", item_id)
        return {"code": 0, "message": "立项信息已更新"}


@router.delete("/initiatives/{item_id}")
def delete_initiative(item_id: int, request: Request, x_user: Optional[str] = Header(None), x_role: Optional[str] = Header(None)):
    actor, role = _actor(x_user, x_role)
    if role not in ("applicant", "admin"):
        raise BusinessError(403, "AUTH-4030", "当前角色无删除立项权限")
    with connect() as conn:
        row = conn.execute("SELECT status,project_id FROM initiatives WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise BusinessError(404, "REQ-4040", "立项申请不存在")
        if row["status"] not in ("草稿", "已驳回") or row["project_id"]:
            raise BusinessError(409, "REQ-4091", "仅未转项目的草稿/驳回立项可删除")
        conn.execute("DELETE FROM initiatives WHERE id=?", (item_id,))
        _audit(conn, request, actor, role, "删除立项申请", "initiative", item_id)
        return {"code": 0, "message": "立项申请已删除"}


@router.get("/projects/{project_id}/risks")
def list_project_risks(project_id: int):
    with connect() as conn:
        return {"code": 0, "data": [dict(x) for x in conn.execute("SELECT * FROM project_risks WHERE project_id=? ORDER BY id DESC", (project_id,))]}


@router.post("/projects/{project_id}/risks")
def create_project_risk(project_id: int, payload: RiskPayload, request: Request,
                        x_user: Optional[str] = Header(None), x_role: Optional[str] = Header(None)):
    actor, role = _actor(x_user, x_role)
    with connect() as conn:
        if not conn.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone():
            raise BusinessError(404, "REQ-4040", "项目不存在")
        no = _next_no(conn, "project_risks", "risk_no", f"RSK-{datetime.now().year}-")
        now = now_iso()
        cur = conn.execute(
            """INSERT INTO project_risks(project_id,risk_no,title,category,probability,impact,level,owner,response_plan,status,due_date,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (project_id, no, payload.title, payload.category, payload.probability, payload.impact, payload.level,
             payload.owner, payload.response_plan, payload.status, payload.due_date, now, now),
        )
        _audit(conn, request, actor, role, "新增项目风险", "project_risk", cur.lastrowid)
        return {"code": 0, "message": "风险已新增", "data": {"id": cur.lastrowid, "risk_no": no}}


@router.put("/project-risks/{risk_id}")
def update_project_risk(risk_id: int, payload: RiskPayload, request: Request,
                        x_user: Optional[str] = Header(None), x_role: Optional[str] = Header(None)):
    actor, role = _actor(x_user, x_role)
    with connect() as conn:
        if not conn.execute("SELECT id FROM project_risks WHERE id=?", (risk_id,)).fetchone():
            raise BusinessError(404, "REQ-4040", "风险不存在")
        conn.execute(
            """UPDATE project_risks SET title=?,category=?,probability=?,impact=?,level=?,owner=?,response_plan=?,status=?,due_date=?,updated_at=? WHERE id=?""",
            (payload.title, payload.category, payload.probability, payload.impact, payload.level, payload.owner,
             payload.response_plan, payload.status, payload.due_date, now_iso(), risk_id),
        )
        _audit(conn, request, actor, role, "更新项目风险", "project_risk", risk_id)
        return {"code": 0, "message": "风险已更新"}


@router.delete("/project-risks/{risk_id}")
def delete_project_risk(risk_id: int, request: Request, x_user: Optional[str] = Header(None), x_role: Optional[str] = Header(None)):
    actor, role = _actor(x_user, x_role)
    with connect() as conn:
        conn.execute("DELETE FROM project_risks WHERE id=?", (risk_id,))
        _audit(conn, request, actor, role, "删除项目风险", "project_risk", risk_id)
        return {"code": 0, "message": "风险已删除"}


@router.get("/projects/{project_id}/deliverables")
def list_project_deliverables(project_id: int):
    with connect() as conn:
        return {"code": 0, "data": [dict(x) for x in conn.execute("SELECT * FROM project_deliverables WHERE project_id=? ORDER BY planned_date,id", (project_id,))]}


@router.post("/projects/{project_id}/deliverables")
def create_deliverable(project_id: int, payload: DeliverablePayload, request: Request,
                       x_user: Optional[str] = Header(None), x_role: Optional[str] = Header(None)):
    actor, role = _actor(x_user, x_role)
    with connect() as conn:
        no = _next_no(conn, "project_deliverables", "deliverable_no", f"DLV-{datetime.now().year}-")
        now = now_iso()
        cur = conn.execute(
            """INSERT INTO project_deliverables(project_id,deliverable_no,name,type,milestone_id,owner,planned_date,actual_date,version,status,url,description,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (project_id, no, payload.name, payload.type, payload.milestone_id, payload.owner, payload.planned_date,
             payload.actual_date, payload.version, payload.status, payload.url, payload.description, now, now),
        )
        _audit(conn, request, actor, role, "新增项目交付物", "deliverable", cur.lastrowid)
        return {"code": 0, "message": "交付物已新增", "data": {"id": cur.lastrowid, "deliverable_no": no}}


@router.put("/deliverables/{item_id}")
def update_deliverable(item_id: int, payload: DeliverablePayload, request: Request,
                       x_user: Optional[str] = Header(None), x_role: Optional[str] = Header(None)):
    actor, role = _actor(x_user, x_role)
    with connect() as conn:
        conn.execute(
            """UPDATE project_deliverables SET name=?,type=?,milestone_id=?,owner=?,planned_date=?,actual_date=?,version=?,status=?,url=?,description=?,updated_at=? WHERE id=?""",
            (payload.name, payload.type, payload.milestone_id, payload.owner, payload.planned_date, payload.actual_date,
             payload.version, payload.status, payload.url, payload.description, now_iso(), item_id),
        )
        _audit(conn, request, actor, role, "更新项目交付物", "deliverable", item_id)
        return {"code": 0, "message": "交付物已更新"}


@router.delete("/deliverables/{item_id}")
def delete_deliverable(item_id: int, request: Request, x_user: Optional[str] = Header(None), x_role: Optional[str] = Header(None)):
    actor, role = _actor(x_user, x_role)
    with connect() as conn:
        conn.execute("DELETE FROM project_deliverables WHERE id=?", (item_id,))
        _audit(conn, request, actor, role, "删除项目交付物", "deliverable", item_id)
        return {"code": 0, "message": "交付物已删除"}


@router.get("/settlements/{item_id}/detail")
def settlement_detail(item_id: int):
    with connect() as conn:
        row = conn.execute(
            """SELECT s.*,p.project_no,p.name project_name,c.contract_no,c.name contract_name,b.budget_no,b.budget_name
               FROM settlements s LEFT JOIN projects p ON p.id=s.project_id LEFT JOIN contracts c ON c.id=s.contract_id
               LEFT JOIN budgets b ON b.id=s.budget_id WHERE s.id=?""", (item_id,)
        ).fetchone()
        if not row:
            raise BusinessError(404, "REQ-4040", "结算单不存在")
        d = dict(row)
        d["items"] = [dict(x) for x in conn.execute("SELECT * FROM settlement_items WHERE settlement_id=? ORDER BY id", (item_id,))]
        d["approvals"] = [dict(x) for x in conn.execute("SELECT * FROM settlement_approvals WHERE settlement_id=? ORDER BY id", (item_id,))]
        return {"code": 0, "data": d}


@router.post("/settlements/{item_id}/items")
def create_settlement_item(item_id: int, payload: SettlementItemPayload, request: Request,
                           x_user: Optional[str] = Header(None), x_role: Optional[str] = Header(None)):
    actor, role = _actor(x_user, x_role)
    amount = round(payload.quantity * payload.unit_price, 2)
    with connect() as conn:
        row = conn.execute("SELECT status FROM settlements WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise BusinessError(404, "REQ-4040", "结算单不存在")
        if row["status"] not in ("草稿", "已驳回"):
            raise BusinessError(409, "REQ-4091", "结算审批中不可修改明细")
        now = now_iso()
        cur = conn.execute(
            """INSERT INTO settlement_items(settlement_id,item_name,item_type,quantity,unit_price,amount,description,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (item_id, payload.item_name, payload.item_type, payload.quantity, payload.unit_price, amount, payload.description, now, now),
        )
        total = conn.execute("SELECT COALESCE(SUM(amount),0) v FROM settlement_items WHERE settlement_id=?", (item_id,)).fetchone()["v"]
        conn.execute("UPDATE settlements SET amount=?,updated_at=? WHERE id=?", (total, now, item_id))
        _audit(conn, request, actor, role, "新增结算明细", "settlement_item", cur.lastrowid)
        return {"code": 0, "message": "结算明细已新增", "data": {"id": cur.lastrowid, "amount": amount, "settlement_total": total}}


@router.delete("/settlement-items/{item_id}")
def delete_settlement_item(item_id: int, request: Request, x_user: Optional[str] = Header(None), x_role: Optional[str] = Header(None)):
    actor, role = _actor(x_user, x_role)
    with connect() as conn:
        row = conn.execute("SELECT settlement_id FROM settlement_items WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise BusinessError(404, "REQ-4040", "结算明细不存在")
        sid = row["settlement_id"]
        conn.execute("DELETE FROM settlement_items WHERE id=?", (item_id,))
        total = conn.execute("SELECT COALESCE(SUM(amount),0) v FROM settlement_items WHERE settlement_id=?", (sid,)).fetchone()["v"]
        conn.execute("UPDATE settlements SET amount=?,updated_at=? WHERE id=?", (total, now_iso(), sid))
        _audit(conn, request, actor, role, "删除结算明细", "settlement_item", item_id)
        return {"code": 0, "message": "结算明细已删除", "data": {"settlement_total": total}}


@router.get("/contracts/{item_id}/changes")
def list_contract_changes(item_id: int):
    with connect() as conn:
        return {"code": 0, "data": [dict(x) for x in conn.execute("SELECT * FROM contract_changes WHERE contract_id=? ORDER BY id DESC", (item_id,))]}


@router.post("/contracts/{item_id}/changes")
def create_contract_change(item_id: int, payload: ContractChangePayload, request: Request,
                           x_user: Optional[str] = Header(None), x_role: Optional[str] = Header(None)):
    actor, role = _actor(x_user, x_role)
    with connect() as conn:
        c = conn.execute("SELECT total_amount FROM contracts WHERE id=?", (item_id,)).fetchone()
        if not c:
            raise BusinessError(404, "REQ-4040", "合同不存在")
        before = float(c["total_amount"])
        after = round(before + payload.amount_delta, 2)
        if after < 0:
            raise BusinessError(400, "REQ-4001", "变更后合同金额不能小于0")
        no = _next_no(conn, "contract_changes", "change_no", f"CHG-{datetime.now().year}-")
        now = now_iso()
        cur = conn.execute(
            """INSERT INTO contract_changes(contract_id,change_no,change_type,reason,amount_delta,before_amount,after_amount,owner,status,effective_date,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (item_id, no, payload.change_type, payload.reason, payload.amount_delta, before, after, payload.owner, payload.status,
             payload.effective_date, now, now),
        )
        if payload.status == "已生效":
            conn.execute("UPDATE contracts SET total_amount=?,updated_at=? WHERE id=?", (after, now, item_id))
        _audit(conn, request, actor, role, "新增合同变更", "contract_change", cur.lastrowid)
        return {"code": 0, "message": "合同变更已保存", "data": {"id": cur.lastrowid, "change_no": no, "after_amount": after}}


@router.put("/contract-changes/{change_id}/effective")
def effective_contract_change(change_id: int, request: Request,
                              x_user: Optional[str] = Header(None), x_role: Optional[str] = Header(None)):
    actor, role = _actor(x_user, x_role)
    if role not in ("business_owner", "admin"):
        raise BusinessError(403, "AUTH-4030", "仅业务负责人或管理员可确认合同变更生效")
    with connect() as conn:
        ch = conn.execute("SELECT * FROM contract_changes WHERE id=?", (change_id,)).fetchone()
        if not ch:
            raise BusinessError(404, "REQ-4040", "合同变更不存在")
        if ch["status"] == "已生效":
            return {"code": 0, "message": "合同变更已生效"}
        conn.execute("UPDATE contract_changes SET status='已生效',effective_date=COALESCE(effective_date,?),updated_at=? WHERE id=?", (datetime.now().strftime("%Y-%m-%d"), now_iso(), change_id))
        conn.execute("UPDATE contracts SET total_amount=?,updated_at=? WHERE id=?", (ch["after_amount"], now_iso(), ch["contract_id"]))
        _audit(conn, request, actor, role, "合同变更生效", "contract_change", change_id)
        return {"code": 0, "message": "合同变更已生效"}


@router.delete("/payment-plans/{item_id}")
def delete_payment_plan(item_id: int, request: Request,
                        x_user: Optional[str] = Header(None), x_role: Optional[str] = Header(None)):
    actor, role = _actor(x_user, x_role)
    with connect() as conn:
        row = conn.execute("SELECT status FROM payment_plans WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise BusinessError(404, "REQ-4040", "付款计划不存在")
        if row["status"] == "已支付":
            raise BusinessError(409, "REQ-4091", "已支付计划不能删除")
        conn.execute("DELETE FROM payment_plans WHERE id=?", (item_id,))
        _audit(conn, request, actor, role, "删除付款计划", "payment_plan", item_id)
        return {"code": 0, "message": "付款计划已删除"}


@router.get("/budgets/{budget_id}/detail")
def budget_detail(budget_id: int):
    with connect() as conn:
        row = conn.execute("SELECT * FROM budgets WHERE id=?", (budget_id,)).fetchone()
        if not row:
            raise BusinessError(404, "REQ-4040", "预算不存在")
        d = dict(row)
        d["remaining"] = d["total_budget"] - d["used_budget"]
        d["execution_rate"] = round(d["used_budget"] / d["total_budget"] * 100, 2) if d["total_budget"] else 0
        d["transactions"] = [dict(x) for x in conn.execute("SELECT * FROM budget_transactions WHERE budget_id=? ORDER BY id DESC", (budget_id,))]
        d["demands"] = [dict(x) for x in conn.execute("SELECT id,demand_no,title,estimated_amount,status,budget_sources FROM demands ORDER BY id DESC") if d["budget_name"] in (x["budget_sources"] or "")]
        d["projects"] = [dict(x) for x in conn.execute("SELECT id,project_no,name,status,total_budget,progress FROM projects WHERE budget_id=? ORDER BY id DESC", (budget_id,))]
        d["contracts"] = [dict(x) for x in conn.execute("SELECT id,contract_no,name,status,total_amount FROM contracts WHERE budget_id=? ORDER BY id DESC", (budget_id,))]
        return {"code": 0, "data": d}


@router.get("/integrations")
def integrations():
    with connect() as conn:
        rows = [dict(x) for x in conn.execute("SELECT * FROM integration_configs ORDER BY id")]
        for r in rows:
            r["enabled"] = bool(r["enabled"])
            r["recent_logs"] = [dict(x) for x in conn.execute("SELECT * FROM integration_logs WHERE integration_code=? ORDER BY id DESC LIMIT 5", (r["code"],))]
        return {"code": 0, "data": rows}


@router.put("/integrations/{code}")
def update_integration(code: str, payload: IntegrationPayload, request: Request,
                       x_user: Optional[str] = Header(None), x_role: Optional[str] = Header(None)):
    actor, role = _actor(x_user, x_role)
    if role != "admin":
        raise BusinessError(403, "AUTH-4030", "仅系统管理员可维护集成配置")
    if payload.mode not in ("mock", "live"):
        raise BusinessError(400, "REQ-4001", "集成模式仅支持 mock/live")
    if payload.mode == "live" and not payload.base_url:
        raise BusinessError(400, "REQ-4002", "live 模式必须配置服务地址")
    with connect() as conn:
        if not conn.execute("SELECT id FROM integration_configs WHERE code=?", (code,)).fetchone():
            raise BusinessError(404, "REQ-4040", "集成配置不存在")
        agent_id = payload.agent_id.strip() if code == "ai" else ""
        if code == "ai" and payload.mode == "live" and not agent_id:
            raise BusinessError(400, "REQ-4002", "AI Live模式必须配置智能体标识")
        conn.execute(
            "UPDATE integration_configs SET mode=?,base_url=?,agent_id=?,enabled=?,updated_at=? WHERE code=?",
            (payload.mode, payload.base_url.rstrip("/"), agent_id, 1 if payload.enabled else 0, now_iso(), code),
        )
        _audit(conn, request, actor, role, "更新集成配置", "integration", code)
        return {"code": 0, "message": "集成配置已更新"}


@router.post("/integrations/{code}/check")
def check_integration(code: str, request: Request,
                      x_user: Optional[str] = Header(None), x_role: Optional[str] = Header(None)):
    actor, role = _actor(x_user, x_role)
    with connect() as conn:
        cfg = conn.execute("SELECT * FROM integration_configs WHERE code=?", (code,)).fetchone()
        if not cfg:
            raise BusinessError(404, "REQ-4040", "集成配置不存在")
        if not cfg["enabled"]:
            status, message, success = "停用", "当前集成已停用", 0
        elif cfg["mode"] == "mock":
            status, message, success = "正常", "Mock适配器可用，业务链路可完整演示", 1
        elif code == "ai":
            try:
                url = f"{str(cfg['base_url']).rstrip('/')}/docs/openapi/agent-runtime.yaml"
                response = httpx.get(url, timeout=8, follow_redirects=False)
                response.raise_for_status()
                if "/adk/run_stream" not in response.text:
                    raise ValueError("OpenAPI中缺少运行接口")
                status, message, success = "正常", f"G.AIOS运行接口可达，智能体标识：{cfg['agent_id']}", 1
            except Exception as exc:
                status, message, success = "异常", f"G.AIOS连通性检查失败：{str(exc)[:160]}", 0
        elif code == "mcp":
            state = public_mcp_status()
            if not state["enabled"]:
                status, message, success = "未配置", "MCP端点已封装，但未配置TRM_MCP_API_TOKEN", 0
            elif not state["write_enabled"]:
                status, message, success = "只读", "MCP鉴权已就绪，9个工具可发现；写操作开关当前关闭", 1
            else:
                status, message, success = "正常", "MCP鉴权与写操作已就绪，支持受控创建项目/需求", 1
        else:
            # 其他第三方系统没有提供只读健康端点时，不伪造真实调用成功。
            status, message, success = "待验证", "已配置Live地址；真实鉴权凭据由部署环境注入后执行连通性验证", 1
        conn.execute("UPDATE integration_configs SET last_check_at=?,status=?,updated_at=? WHERE code=?", (now_iso(), status, now_iso(), code))
        conn.execute("INSERT INTO integration_logs(integration_code,direction,business_type,business_id,success,message,request_id,created_at) VALUES (?,?,?,?,?,?,?,?)", (code, "out", "health_check", "", success, message, getattr(request.state, "request_id", ""), now_iso()))
        _audit(conn, request, actor, role, "检查集成状态", "integration", code, result="成功" if success else "失败")
        return {"code": 0, "message": message, "data": {"status": status}}


@router.get("/exports/platform-summary.csv")
def export_platform_summary():
    with connect() as conn:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["模块", "编号", "名称", "状态", "金额/进度"])
        for r in conn.execute("SELECT project_no,name,status,progress FROM projects ORDER BY id"):
            w.writerow(["项目", r["project_no"], r["name"], r["status"], f"{r['progress']}%"])
        for r in conn.execute("SELECT demand_no,title,status,estimated_amount FROM demands WHERE demand_no IS NOT NULL ORDER BY id"):
            w.writerow(["需求", r["demand_no"], r["title"], r["status"], r["estimated_amount"]])
        for r in conn.execute("SELECT contract_no,name,status,total_amount FROM contracts ORDER BY id"):
            w.writerow(["合同", r["contract_no"], r["name"], r["status"], r["total_amount"]])
        data = ("\ufeff" + buf.getvalue()).encode("utf-8")
        return StreamingResponse(io.BytesIO(data), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=trm-platform-summary.csv"})
