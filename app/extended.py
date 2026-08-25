
import json
from datetime import datetime
from typing import Optional
from urllib.parse import unquote

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, Field

from .db import connect, now_iso, row_to_dict
from .rules import BusinessError, ROLE_LABELS
from .auth import get_role_labels, has_ai_capability, has_permission, request_has_role, request_role_codes

router = APIRouter(prefix="/api", tags=["完整平台功能"])


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


def _next_no(conn, table, field, prefix):
    row = conn.execute(f"SELECT {field} FROM {table} WHERE {field} LIKE ? ORDER BY {field} DESC LIMIT 1", (f"{prefix}%",)).fetchone()
    seq = int(row[field].split('-')[-1]) + 1 if row and row[field] else 1
    return f"{prefix}{seq:04d}"


def init_extended_db():
    with connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS initiatives (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            initiative_no TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            applicant TEXT NOT NULL,
            department TEXT DEFAULT '',
            owner TEXT DEFAULT '',
            estimated_budget REAL DEFAULT 0,
            budget_id INTEGER,
            planned_start TEXT,
            planned_end TEXT,
            status TEXT NOT NULL DEFAULT '草稿',
            current_node TEXT NOT NULL DEFAULT '草稿',
            project_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS initiative_approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            initiative_id INTEGER NOT NULL,
            node TEXT NOT NULL,
            role TEXT NOT NULL,
            approver TEXT NOT NULL,
            action TEXT NOT NULL,
            comment TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY(initiative_id) REFERENCES initiatives(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_no TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            initiative_id INTEGER,
            manager TEXT NOT NULL,
            department TEXT DEFAULT '',
            budget_id INTEGER,
            total_budget REAL DEFAULT 0,
            status TEXT NOT NULL DEFAULT '规划中',
            progress REAL NOT NULL DEFAULT 0,
            start_date TEXT,
            end_date TEXT,
            description TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS project_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            task_no TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            owner TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT '未开始',
            priority TEXT NOT NULL DEFAULT '中',
            progress REAL NOT NULL DEFAULT 0,
            start_date TEXT,
            end_date TEXT,
            parent_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS milestones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            planned_date TEXT,
            actual_date TEXT,
            status TEXT NOT NULL DEFAULT '未完成',
            owner TEXT DEFAULT '',
            description TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS budget_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            budget_id INTEGER NOT NULL,
            txn_type TEXT NOT NULL,
            amount REAL NOT NULL,
            reference_type TEXT DEFAULT '',
            reference_id TEXT DEFAULT '',
            description TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS business_values (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            value_type TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            planned_value REAL DEFAULT 0,
            realized_value REAL DEFAULT 0,
            unit TEXT DEFAULT '',
            period TEXT DEFAULT '',
            owner TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT '跟踪中',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settlements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            settlement_no TEXT UNIQUE NOT NULL,
            project_id INTEGER,
            contract_id INTEGER,
            budget_id INTEGER,
            amount REAL NOT NULL,
            settlement_type TEXT NOT NULL DEFAULT '项目结算',
            applicant TEXT NOT NULL,
            description TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT '草稿',
            current_node TEXT NOT NULL DEFAULT '草稿',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settlement_approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            settlement_id INTEGER NOT NULL,
            node TEXT NOT NULL,
            role TEXT NOT NULL,
            approver TEXT NOT NULL,
            action TEXT NOT NULL,
            comment TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY(settlement_id) REFERENCES settlements(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS indicators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            indicator_no TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            unit TEXT DEFAULT '',
            formula TEXT DEFAULT '',
            target_value REAL DEFAULT 0,
            current_value REAL DEFAULT 0,
            data_source TEXT DEFAULT '',
            frequency TEXT DEFAULT '月度',
            owner TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT '启用',
            direction TEXT NOT NULL DEFAULT 'higher',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS indicator_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            indicator_id INTEGER NOT NULL,
            period TEXT NOT NULL,
            value REAL NOT NULL,
            source TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY(indicator_id) REFERENCES indicators(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS contracts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contract_no TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            project_id INTEGER,
            budget_id INTEGER,
            supplier TEXT NOT NULL,
            total_amount REAL NOT NULL,
            start_date TEXT,
            end_date TEXT,
            owner TEXT DEFAULT '',
            description TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT '草稿',
            current_node TEXT NOT NULL DEFAULT '草稿',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS contract_approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contract_id INTEGER NOT NULL,
            node TEXT NOT NULL,
            role TEXT NOT NULL,
            approver TEXT NOT NULL,
            action TEXT NOT NULL,
            comment TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY(contract_id) REFERENCES contracts(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS payment_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contract_id INTEGER NOT NULL,
            plan_no TEXT UNIQUE NOT NULL,
            payment_type TEXT NOT NULL,
            amount REAL NOT NULL,
            planned_date TEXT,
            actual_date TEXT,
            status TEXT NOT NULL DEFAULT '待支付',
            description TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(contract_id) REFERENCES contracts(id) ON DELETE CASCADE
        );
        """)
        indicator_columns = {row["name"] for row in conn.execute("PRAGMA table_info(indicators)")}
        if "direction" not in indicator_columns:
            conn.execute("ALTER TABLE indicators ADD COLUMN direction TEXT NOT NULL DEFAULT 'higher'")
        now = now_iso()
        if conn.execute("SELECT COUNT(*) c FROM initiatives").fetchone()["c"] == 0:
            conn.executemany("""INSERT INTO initiatives(initiative_no,title,description,applicant,department,owner,estimated_budget,budget_id,planned_start,planned_end,status,current_node,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",[
                ("INI-2026-0001","机构透视管理机器人项目升级","建设统一的机构透视分析与智能问答能力。","李莉 lili11-ghq","数字化管理部","王卫嘉",860000,1,"2026-09-01","2027-01-31","审批中","财务评审",now,now),
                ("INI-2026-0002","科技运营平台能力扩展","扩展项目、合同、指标与结算的一体化管理能力。","赵敏 zhaomin","产品研发部","曾卫平",620000,2,"2026-10-01","2027-03-31","草稿","草稿",now,now)
            ])
            iid=conn.execute("SELECT id FROM initiatives WHERE initiative_no='INI-2026-0001'").fetchone()["id"]
            conn.execute("INSERT INTO initiative_approvals(initiative_id,node,role,approver,action,comment,created_at) VALUES (?,?,?,?,?,?,?)",(iid,"部门负责人审批","department_head","王主任","通过","业务价值清晰，同意进入财务评审。",now))
        if conn.execute("SELECT COUNT(*) c FROM projects").fetchone()["c"] == 0:
            conn.executemany("""INSERT INTO projects(project_no,name,manager,department,budget_id,total_budget,status,progress,start_date,end_date,description,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",[
                ("PRJ-2026-0018","机构透视管理机器人项目","王卫嘉","数字化管理部",1,5000000,"实施中",64,"2026-06-01","2027-01-31","机构透视、智能分析与预算联动建设。",now,now),
                ("PRJ-2026-0024","科技运营平台年度迭代","曾卫平","产品研发部",2,2000000,"实施中",42,"2026-07-01","2027-03-31","科技运营平台持续迭代与集成优化。",now,now),
                ("PRJ-2026-0031","数字化创新专项","刘玉节","科技管理部",3,1500000,"规划中",18,"2026-09-15","2027-04-30","数字化创新场景孵化与验证。",now,now)
            ])
            p1=conn.execute("SELECT id FROM projects WHERE project_no='PRJ-2026-0018'").fetchone()["id"]
            conn.executemany("""INSERT INTO project_tasks(project_id,task_no,title,owner,status,priority,progress,start_date,end_date,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",[
                (p1,"TSK-2026-0001","完成需求全生命周期POC","王卫嘉","进行中","高",72,"2026-08-10","2026-09-15",now,now),
                (p1,"TSK-2026-0002","预算平台接口联调","曾卫平","进行中","高",55,"2026-08-20","2026-09-10",now,now),
                (p1,"TSK-2026-0003","TAPD集成验证","赵敏","未开始","中",10,"2026-09-01","2026-09-20",now,now)
            ])
            conn.executemany("""INSERT INTO milestones(project_id,name,planned_date,actual_date,status,owner,description,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)""",[
                (p1,"POC方案确认","2026-08-25",None,"进行中","王卫嘉","完成业务流程与页面确认",now,now),
                (p1,"POC验收","2026-09-20",None,"未完成","王卫嘉","完成POC功能与性能验收",now,now),
                (p1,"一期上线","2027-01-15",None,"未完成","曾卫平","生产环境发布",now,now)
            ])
        if conn.execute("SELECT COUNT(*) c FROM business_values").fetchone()["c"] == 0:
            conn.executemany("""INSERT INTO business_values(project_id,value_type,metric_name,planned_value,realized_value,unit,period,owner,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",[
                (1,"效率","需求平均交付周期降低",30,18,"%","2026Q3","王卫嘉","跟踪中",now,now),
                (1,"质量","需求信息一致性",99,96.5,"%","2026Q3","赵敏","跟踪中",now,now),
                (2,"成本","人工操作工时节约",600,285,"小时/年","2026","曾卫平","跟踪中",now,now)
            ])
        indicator_seeds = [
            ("KPI-2026-0001","需求按期交付率","交付效率","%","按期完成需求数 ÷ 已完成需求数 × 100",95,92.4,"需求管理","月度","项目管理办公室","启用","higher",now,now),
            ("KPI-2026-0002","预算执行率","预算管理","%","已使用预算 ÷ 总预算 × 100",90,64,"预算管理","月度","财务部","启用","higher",now,now),
            ("KPI-2026-0003","审批平均时长","流程效率","小时","审批总耗时 ÷ 已完成审批单数",24,19.6,"审批日志","周度","产品运营部","启用","lower",now,now),
            ("KPI-2026-0004","需求一次通过率","质量管理","%","首次审批通过需求数 ÷ 已审批需求数 × 100",85,88.5,"审批日志","月度","产品运营部","启用","higher",now,now),
            ("KPI-2026-0005","工时估算偏差率","质量管理","%","|实际工时-预估工时| ÷ 预估工时 × 100",30,18.7,"TAPD工时回读","周度","产品研发部","启用","lower",now,now),
            ("KPI-2026-0006","TAPD同步成功率","系统运行","%","成功同步次数 ÷ 总同步次数 × 100",99,98.2,"TAPD同步记录","日度","平台运维组","启用","higher",now,now),
            ("KPI-2026-0007","需求关闭率","交付效率","%","已关闭需求数 ÷ 到期需求数 × 100",90,76,"需求管理","月度","项目管理办公室","启用","higher",now,now),
            ("KPI-2026-0008","项目里程碑按期率","项目管理","%","按期完成里程碑数 ÷ 已到期里程碑数 × 100",95,86.7,"项目里程碑","月度","项目管理办公室","启用","higher",now,now),
            ("KPI-2026-0009","合同付款计划达成率","合同结算","%","按计划完成付款数 ÷ 已到期付款数 × 100",98,100,"收付款计划","月度","财务部","启用","higher",now,now),
            ("KPI-2026-0010","预算预测偏差率","预算管理","%","|实际支出-预测支出| ÷ 预测支出 × 100",10,7.4,"预算执行快照","月度","财务部","启用","lower",now,now),
            ("KPI-2026-0011","关键风险关闭率","风险管理","%","已关闭关键风险数 ÷ 关键风险总数 × 100",90,83.3,"项目风险台账","周度","风险管理岗","启用","higher",now,now),
            ("KPI-2026-0012","AI问题解决率","智能化运营","%","有效解决会话数 ÷ AI会话总数 × 100",85,91.6,"AI会话反馈","周度","数字化管理部","启用","higher",now,now),
            ("KPI-2026-0013","系统可用率","系统运行","%","正常服务时长 ÷ 统计周期总时长 × 100",99.9,99.95,"服务监控","日度","平台运维组","启用","higher",now,now),
            ("KPI-2026-0014","需求平均交付周期","交付效率","天","需求交付总天数 ÷ 已交付需求数",20,23.5,"需求全生命周期","月度","项目管理办公室","启用","lower",now,now),
            ("KPI-2026-0015","业务价值达成率","项目管理","%","已实现价值 ÷ 计划价值 × 100",90,72.5,"业务价值台账","季度","项目管理办公室","启用","higher",now,now),
        ]
        conn.executemany("""INSERT OR IGNORE INTO indicators(indicator_no,name,category,unit,formula,target_value,current_value,data_source,frequency,owner,status,direction,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", indicator_seeds)
        conn.execute("UPDATE indicators SET direction='lower' WHERE indicator_no IN ('KPI-2026-0003','KPI-2026-0005','KPI-2026-0010','KPI-2026-0014')")
        indicator_histories = {
            "KPI-2026-0001": [91.0, 91.8, 92.1, 92.4], "KPI-2026-0002": [48.2, 53.6, 58.9, 64.0],
            "KPI-2026-0003": [27.8, 24.9, 21.2, 19.6], "KPI-2026-0004": [81.3, 83.8, 86.2, 88.5],
            "KPI-2026-0005": [28.4, 24.6, 21.1, 18.7], "KPI-2026-0006": [96.5, 97.1, 97.8, 98.2],
            "KPI-2026-0007": [68.0, 71.5, 73.2, 76.0], "KPI-2026-0008": [80.0, 82.5, 84.1, 86.7],
            "KPI-2026-0009": [96.0, 98.0, 100.0, 100.0], "KPI-2026-0010": [12.6, 10.8, 8.9, 7.4],
            "KPI-2026-0011": [70.0, 75.0, 80.0, 83.3], "KPI-2026-0012": [84.0, 87.2, 89.8, 91.6],
            "KPI-2026-0013": [99.82, 99.88, 99.92, 99.95], "KPI-2026-0014": [29.0, 27.2, 25.4, 23.5],
            "KPI-2026-0015": [58.0, 63.5, 68.0, 72.5],
        }
        periods = ["2026-05", "2026-06", "2026-07", "2026-08"]
        for indicator_no, values in indicator_histories.items():
            indicator = conn.execute("SELECT id,data_source FROM indicators WHERE indicator_no=?", (indicator_no,)).fetchone()
            if indicator and conn.execute("SELECT COUNT(*) c FROM indicator_records WHERE indicator_id=?", (indicator["id"],)).fetchone()["c"] == 0:
                conn.executemany(
                    "INSERT INTO indicator_records(indicator_id,period,value,source,created_at) VALUES (?,?,?,?,?)",
                    [(indicator["id"], period, value, indicator["data_source"], now) for period, value in zip(periods, values)],
                )
        if conn.execute("SELECT COUNT(*) c FROM contracts").fetchone()["c"] == 0:
            conn.executemany("""INSERT INTO contracts(contract_no,name,project_id,budget_id,supplier,total_amount,start_date,end_date,owner,description,status,current_node,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",[
                ("CT-2026-0088","机构透视管理机器人研发服务合同",1,1,"上海速擎软件有限公司",1280000,"2026-06-15","2027-01-31","王卫嘉","项目研发与实施服务。","执行中","已完成审批",now,now),
                ("CT-2026-0102","科技运营平台技术支持合同",2,2,"上海速邦咨询",560000,"2026-07-01","2027-06-30","曾卫平","平台技术支持与迭代服务。","审批中","财务会签",now,now)
            ])
            c1=conn.execute("SELECT id FROM contracts WHERE contract_no='CT-2026-0088'").fetchone()["id"]
            conn.executemany("""INSERT INTO payment_plans(contract_id,plan_no,payment_type,amount,planned_date,status,description,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)""",[
                (c1,"PAY-2026-0001","首付款",384000,"2026-07-15","已支付","合同签署后30%",now,now),
                (c1,"PAY-2026-0002","里程碑款",512000,"2026-10-31","待支付","POC验收后40%",now,now),
                (c1,"PAY-2027-0003","尾款",384000,"2027-02-15","待支付","最终验收后30%",now,now)
            ])
        if conn.execute("SELECT COUNT(*) c FROM settlements").fetchone()["c"] == 0:
            conn.executemany("""INSERT INTO settlements(settlement_no,project_id,contract_id,budget_id,amount,settlement_type,applicant,description,status,current_node,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",[
                ("SET-2026-0021",1,1,1,384000,"合同付款结算","李莉 lili11-ghq","首付款结算申请。","已完成","已完成",now,now),
                ("SET-2026-0028",2,2,2,120000,"项目费用结算","赵敏 zhaomin","阶段性技术服务费用结算。","审批中","财务审批",now,now)
            ])


class InitiativePayload(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    description: str = ""
    applicant: str = "李莉 lili11-ghq"
    department: str = "数字化管理部"
    owner: str = ""
    estimated_budget: float = Field(default=0, ge=0, le=999_999_999.99)
    budget_id: Optional[int] = None
    planned_start: Optional[str] = None
    planned_end: Optional[str] = None

class ProjectPayload(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    manager: str = Field(min_length=1, max_length=100)
    department: str = ""
    budget_id: Optional[int] = None
    total_budget: float = Field(default=0, ge=0, le=999_999_999.99)
    status: str = "规划中"
    progress: float = Field(default=0, ge=0, le=100)
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    description: str = ""

class ProjectRelationsPayload(BaseModel):
    contract_ids: Optional[list[int]] = None
    settlement_ids: Optional[list[int]] = None

class TaskPayload(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    owner: str = ""
    status: str = "未开始"
    priority: str = "中"
    progress: float = Field(default=0, ge=0, le=100)
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    parent_id: Optional[int] = None

class MilestonePayload(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    planned_date: Optional[str] = None
    actual_date: Optional[str] = None
    status: str = "未完成"
    owner: str = ""
    description: str = ""

class BudgetPayload(BaseModel):
    budget_no: Optional[str] = None
    budget_name: str = Field(min_length=1, max_length=200)
    total_budget: float = Field(ge=0, le=9_999_999_999.99)
    used_budget: float = Field(default=0, ge=0, le=9_999_999_999.99)
    internal_total: float = Field(default=0, ge=0, le=9_999_999_999.99)
    internal_used: float = Field(default=0, ge=0, le=9_999_999_999.99)
    digital_total: float = Field(default=0, ge=0, le=9_999_999_999.99)
    digital_used: float = Field(default=0, ge=0, le=9_999_999_999.99)
    year: int = 2026

class BudgetTxnPayload(BaseModel):
    txn_type: str
    amount: float
    reference_type: str = ""
    reference_id: str = ""
    description: str = ""
    department: str = ""

class ValuePayload(BaseModel):
    project_id: Optional[int] = None
    value_type: str
    metric_name: str
    planned_value: float = 0
    realized_value: float = 0
    unit: str = ""
    period: str = ""
    owner: str = ""
    status: str = "跟踪中"

class SettlementPayload(BaseModel):
    project_id: Optional[int] = None
    contract_id: Optional[int] = None
    budget_id: Optional[int] = None
    amount: float = Field(ge=0, le=999_999_999.99)
    settlement_type: str = "项目结算"
    applicant: str = "李莉 lili11-ghq"
    description: str = ""

class IndicatorPayload(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=100)
    unit: str = ""
    formula: str = ""
    target_value: float = 0
    current_value: float = 0
    data_source: str = ""
    frequency: str = "月度"
    owner: str = ""
    status: str = "启用"
    direction: str = Field(default="higher", pattern="^(higher|lower)$")

class IndicatorRecordPayload(BaseModel):
    period: str
    value: float
    source: str = ""

class ContractPayload(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    project_id: Optional[int] = None
    budget_id: Optional[int] = None
    supplier: str = Field(min_length=1, max_length=200)
    total_amount: float = Field(gt=0, le=999_999_999.99)
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    owner: str = ""
    description: str = ""

class PaymentPayload(BaseModel):
    payment_type: str = Field(min_length=1, max_length=100)
    amount: float = Field(gt=0, le=999_999_999.99)
    planned_date: Optional[str] = None
    actual_date: Optional[str] = None
    status: str = "待支付"
    description: str = ""

class ActionPayload(BaseModel):
    action: str = "通过"
    comment: str = ""


def _validate_date_range(start_date: Optional[str], end_date: Optional[str], label: str):
    if start_date and end_date and start_date > end_date:
        raise BusinessError(400, 'REQ-4001', f'{label}开始日期不能晚于结束日期')


def _validate_budget_payload(payload: BudgetPayload):
    if payload.used_budget > payload.total_budget:
        raise BusinessError(422, 'BUD-4220', '已使用预算不能超过总预算')
    if payload.internal_used > payload.internal_total:
        raise BusinessError(422, 'BUD-4220', '内部研发已使用预算不能超过内部研发预算')
    if payload.digital_used > payload.digital_total:
        raise BusinessError(422, 'BUD-4220', '数科已使用预算不能超过数科预算')
    if payload.internal_total + payload.digital_total > payload.total_budget + 0.01:
        raise BusinessError(422, 'BUD-4221', '内部研发与数科分项预算合计不能超过总预算')


def _recalculate_project_progress(conn, project_id: int):
    row = conn.execute(
        'SELECT COUNT(*) c,COALESCE(AVG(progress),0) p FROM project_tasks WHERE project_id=?',
        (project_id,),
    ).fetchone()
    if row and row['c']:
        progress = round(float(row['p'] or 0), 2)
        conn.execute('UPDATE projects SET progress=?,updated_at=? WHERE id=?', (progress, now_iso(), project_id))
        return progress
    return None


def _approval_action(value: str) -> str:
    action = str(value or '').strip()
    if action not in ('通过', '驳回'):
        raise BusinessError(400, 'REQ-4001', '审批动作仅支持通过或驳回')
    return action


@router.get('/platform-dashboard')
def platform_dashboard():
    with connect() as conn:
        result = {}
        for key, table in [('demands','demands'),('initiatives','initiatives'),('projects','projects'),('contracts','contracts'),('settlements','settlements')]:
            result[key] = conn.execute(f'SELECT COUNT(*) c FROM {table}').fetchone()['c']
        b=conn.execute('SELECT COALESCE(SUM(total_budget),0) total,COALESCE(SUM(used_budget),0) used FROM budgets').fetchone()
        result['budget_total']=b['total']; result['budget_used']=b['used']
        result['project_progress']=round(conn.execute("SELECT COALESCE(AVG(progress),0) v FROM projects").fetchone()['v'],2)
        result['pending_approvals']=conn.execute("SELECT COUNT(*) c FROM initiatives WHERE status='审批中'").fetchone()['c'] + conn.execute("SELECT COUNT(*) c FROM settlements WHERE status='审批中'").fetchone()['c'] + conn.execute("SELECT COUNT(*) c FROM contracts WHERE status='审批中'").fetchone()['c']
        result['recent_projects']=[dict(x) for x in conn.execute('SELECT * FROM projects ORDER BY updated_at DESC LIMIT 5')]
        result['recent_initiatives']=[dict(x) for x in conn.execute('SELECT * FROM initiatives ORDER BY updated_at DESC LIMIT 5')]
        return {'code':0,'data':result}

@router.get('/initiatives')
def list_initiatives(status: str=''):
    with connect() as conn:
        sql='SELECT * FROM initiatives'; args=[]
        if status: sql+=' WHERE status=?'; args.append(status)
        sql+=' ORDER BY updated_at DESC'
        return {'code':0,'data':[dict(x) for x in conn.execute(sql,args)]}

@router.get('/initiatives/{item_id}')
def initiative_detail(item_id:int):
    with connect() as conn:
        row=conn.execute('SELECT * FROM initiatives WHERE id=?',(item_id,)).fetchone()
        if not row: raise BusinessError(404,'REQ-4040','立项申请不存在')
        d=dict(row); d['approvals']=[dict(x) for x in conn.execute('SELECT * FROM initiative_approvals WHERE initiative_id=? ORDER BY id',(item_id,))]
        if d.get('project_id'): d['project']=row_to_dict(conn.execute('SELECT * FROM projects WHERE id=?',(d['project_id'],)).fetchone())
        return {'code':0,'data':d}

@router.post('/initiatives')
def create_initiative(payload:InitiativePayload, request:Request, x_user:Optional[str]=Header(None), x_role:Optional[str]=Header(None)):
    actor,role=_actor(x_user,x_role)
    _validate_date_range(payload.planned_start, payload.planned_end, '立项')
    with connect() as conn:
        no=_next_no(conn,'initiatives','initiative_no',f'INI-{datetime.now().year}-')
        now=now_iso(); cur=conn.execute("""INSERT INTO initiatives(initiative_no,title,description,applicant,department,owner,estimated_budget,budget_id,planned_start,planned_end,status,current_node,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(no,payload.title,payload.description,payload.applicant,payload.department,payload.owner,payload.estimated_budget,payload.budget_id,payload.planned_start,payload.planned_end,'草稿','草稿',now,now))
        iid=cur.lastrowid; _audit(conn,request,actor,role,'创建立项申请','initiative',iid)
        return {'code':0,'message':'立项申请已保存','data':{'id':iid,'initiative_no':no}}

@router.put('/initiatives/{item_id}')
def update_initiative(item_id:int,payload:InitiativePayload,request:Request,x_user:Optional[str]=Header(None),x_role:Optional[str]=Header(None)):
    actor,role=_actor(x_user,x_role)
    _validate_date_range(payload.planned_start, payload.planned_end, '立项')
    with connect() as conn:
        row=conn.execute('SELECT * FROM initiatives WHERE id=?',(item_id,)).fetchone()
        if not row: raise BusinessError(404,'REQ-4040','立项申请不存在')
        if row['status'] not in ('草稿','已驳回'): raise BusinessError(409,'REQ-4091','当前状态不允许编辑')
        conn.execute("""UPDATE initiatives SET title=?,description=?,applicant=?,department=?,owner=?,estimated_budget=?,budget_id=?,planned_start=?,planned_end=?,updated_at=? WHERE id=?""",(payload.title,payload.description,payload.applicant,payload.department,payload.owner,payload.estimated_budget,payload.budget_id,payload.planned_start,payload.planned_end,now_iso(),item_id))
        _audit(conn,request,actor,role,'更新立项申请','initiative',item_id)
        return {'code':0,'message':'立项申请已更新'}

@router.post('/initiatives/{item_id}/submit')
def submit_initiative(item_id:int,request:Request,x_user:Optional[str]=Header(None),x_role:Optional[str]=Header(None)):
    actor,role=_actor(x_user,x_role)
    with connect() as conn:
        row=conn.execute('SELECT * FROM initiatives WHERE id=?',(item_id,)).fetchone()
        if not row: raise BusinessError(404,'REQ-4040','立项申请不存在')
        if row['status'] not in ('草稿','已驳回'): raise BusinessError(409,'REQ-4091','当前状态不允许提交')
        if not row['title'] or row['estimated_budget']<=0: raise BusinessError(400,'REQ-4002','请完善立项名称与预估预算')
        conn.execute("UPDATE initiatives SET status='审批中',current_node='部门负责人审批',updated_at=? WHERE id=?",(now_iso(),item_id)); _audit(conn,request,actor,role,'提交立项申请','initiative',item_id)
        return {'code':0,'message':'已提交部门负责人审批'}

@router.get('/initiative-approvals/pending')
def pending_initiatives(request:Request,x_role:Optional[str]=Header(None)):
    roles=request_role_codes(request) or {x_role or 'applicant'}
    nodes=[node for role,node in {'department_head':'部门负责人审批','finance':'财务评审','vp':'分管领导审批'}.items() if role in roles]
    if 'admin' in roles: nodes=['部门负责人审批','财务评审','分管领导审批']
    with connect() as conn:
        if not nodes: rows=[]
        else:
            placeholders=','.join('?' for _ in nodes)
            rows=[dict(x) for x in conn.execute(f"SELECT * FROM initiatives WHERE status='审批中' AND current_node IN ({placeholders}) ORDER BY updated_at DESC",nodes)]
        return {'code':0,'data':rows}

@router.post('/initiatives/{item_id}/approve')
def approve_initiative(item_id:int,payload:ActionPayload,request:Request,x_user:Optional[str]=Header(None),x_role:Optional[str]=Header(None)):
    actor,role=_actor(x_user,x_role); mapping={'部门负责人审批':'department_head','财务评审':'finance','分管领导审批':'vp'}
    with connect() as conn:
        row=conn.execute('SELECT * FROM initiatives WHERE id=?',(item_id,)).fetchone()
        if not row: raise BusinessError(404,'REQ-4040','立项申请不存在')
        node=row['current_node']; expected=mapping.get(node)
        if not request_has_role(request,expected): raise BusinessError(403,'AUTH-4030',f'当前节点需要{expected}角色')
        action=_approval_action(payload.action)
        conn.execute('INSERT INTO initiative_approvals(initiative_id,node,role,approver,action,comment,created_at) VALUES (?,?,?,?,?,?,?)',(item_id,node,role,actor,action,payload.comment,now_iso()))
        if action=='驳回': status='已驳回'; next_node='草稿'
        else:
            next_node={'部门负责人审批':'财务评审','财务评审':'分管领导审批','分管领导审批':'已完成审批'}[node]; status='已通过' if next_node=='已完成审批' else '审批中'
        conn.execute('UPDATE initiatives SET status=?,current_node=?,updated_at=? WHERE id=?',(status,next_node,now_iso(),item_id)); _audit(conn,request,actor,role,f'立项{action}','initiative',item_id)
        return {'code':0,'message':f'立项{action}成功','data':{'status':status,'current_node':next_node}}

@router.post('/initiatives/{item_id}/convert-project')
def convert_initiative(item_id:int,request:Request,x_user:Optional[str]=Header(None),x_role:Optional[str]=Header(None)):
    actor,role=_actor(x_user,x_role)
    with connect() as conn:
        ini=conn.execute('SELECT * FROM initiatives WHERE id=?',(item_id,)).fetchone()
        if not ini: raise BusinessError(404,'REQ-4040','立项申请不存在')
        if ini['status']!='已通过': raise BusinessError(409,'REQ-4091','立项审批通过后才能生成项目')
        if ini['project_id']:
            return {'code':0,'message':'已生成项目','data':{'project_id':ini['project_id']}}
        no=_next_no(conn,'projects','project_no',f'PRJ-{datetime.now().year}-'); now=now_iso()
        cur=conn.execute("""INSERT INTO projects(project_no,name,initiative_id,manager,department,budget_id,total_budget,status,progress,start_date,end_date,description,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(no,ini['title'],item_id,ini['owner'] or actor,ini['department'],ini['budget_id'],ini['estimated_budget'],'规划中',0,ini['planned_start'],ini['planned_end'],ini['description'],now,now))
        pid=cur.lastrowid; conn.execute('UPDATE initiatives SET project_id=?,updated_at=? WHERE id=?',(pid,now,item_id)); _audit(conn,request,actor,role,'立项转项目','project',pid,details={'initiative_id':item_id})
        return {'code':0,'message':'项目已创建','data':{'project_id':pid,'project_no':no}}

@router.get('/projects')
def list_projects():
    with connect() as conn:
        rows=conn.execute("""SELECT p.*,i.initiative_no,i.title initiative_title
                             FROM projects p LEFT JOIN initiatives i ON i.id=p.initiative_id
                             ORDER BY p.updated_at DESC""")
        return {'code':0,'data':[dict(x) for x in rows]}

@router.get('/projects/{project_id}')
def project_detail(project_id:int):
    with connect() as conn:
        row=conn.execute('SELECT * FROM projects WHERE id=?',(project_id,)).fetchone()
        if not row: raise BusinessError(404,'REQ-4040','项目不存在')
        d=dict(row); d['tasks']=[dict(x) for x in conn.execute('SELECT * FROM project_tasks WHERE project_id=? ORDER BY id',(project_id,))]; d['milestones']=[dict(x) for x in conn.execute('SELECT * FROM milestones WHERE project_id=? ORDER BY planned_date,id',(project_id,))]
        d['contracts']=[dict(x) for x in conn.execute('SELECT * FROM contracts WHERE project_id=? ORDER BY id DESC',(project_id,))]; d['settlements']=[dict(x) for x in conn.execute('SELECT * FROM settlements WHERE project_id=? ORDER BY id DESC',(project_id,))]; d['values']=[dict(x) for x in conn.execute('SELECT * FROM business_values WHERE project_id=? ORDER BY id',(project_id,))]
        d['initiative']=row_to_dict(conn.execute('SELECT id,initiative_no,title,status,current_node FROM initiatives WHERE id=?',(row['initiative_id'],)).fetchone()) if row['initiative_id'] else None
        budget=row_to_dict(conn.execute('SELECT * FROM budgets WHERE id=?',(row['budget_id'],)).fetchone()) if row['budget_id'] else None; d['budget']=budget
        demand_rows=[dict(x) for x in conn.execute('SELECT * FROM demands ORDER BY id DESC')]
        if budget:
            d['demands']=[x for x in demand_rows if budget['budget_name'] in json.loads(x.get('budget_sources') or '[]')]
        else:d['demands']=[]
        return {'code':0,'data':d}

@router.post('/projects')
def create_project(payload:ProjectPayload,request:Request,x_user:Optional[str]=Header(None),x_role:Optional[str]=Header(None)):
    actor,role=_actor(x_user,x_role)
    _validate_date_range(payload.start_date, payload.end_date, '项目')
    with connect() as conn:
        no=_next_no(conn,'projects','project_no',f'PRJ-{datetime.now().year}-'); now=now_iso(); cur=conn.execute("""INSERT INTO projects(project_no,name,manager,department,budget_id,total_budget,status,progress,start_date,end_date,description,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",(no,payload.name,payload.manager,payload.department,payload.budget_id,payload.total_budget,payload.status,max(0,min(100,payload.progress)),payload.start_date,payload.end_date,payload.description,now,now)); pid=cur.lastrowid; _audit(conn,request,actor,role,'创建项目','project',pid); return {'code':0,'message':'项目已创建','data':{'id':pid,'project_no':no}}

@router.put('/projects/{project_id}')
def update_project(project_id:int,payload:ProjectPayload,request:Request,x_user:Optional[str]=Header(None),x_role:Optional[str]=Header(None)):
    actor,role=_actor(x_user,x_role)
    _validate_date_range(payload.start_date, payload.end_date, '项目')
    with connect() as conn:
        if not conn.execute('SELECT id FROM projects WHERE id=?',(project_id,)).fetchone(): raise BusinessError(404,'REQ-4040','项目不存在')
        conn.execute("""UPDATE projects SET name=?,manager=?,department=?,budget_id=?,total_budget=?,status=?,progress=?,start_date=?,end_date=?,description=?,updated_at=? WHERE id=?""",(payload.name,payload.manager,payload.department,payload.budget_id,payload.total_budget,payload.status,max(0,min(100,payload.progress)),payload.start_date,payload.end_date,payload.description,now_iso(),project_id)); _audit(conn,request,actor,role,'更新项目','project',project_id); return {'code':0,'message':'项目已更新'}

@router.put('/projects/{project_id}/relations')
def update_project_relations(project_id:int,payload:ProjectRelationsPayload,request:Request,x_user:Optional[str]=Header(None),x_role:Optional[str]=Header(None)):
    actor,role=_actor(x_user,x_role)
    user=getattr(request.state,'auth_user',None) or {}
    permissions=user.get('permissions') or []
    if user and payload.contract_ids is not None and not has_permission(permissions,'contract'):
        raise BusinessError(403,'AUTH-4030','当前账号无合同管理权限')
    if user and payload.settlement_ids is not None and not has_permission(permissions,'settlement'):
        raise BusinessError(403,'AUTH-4030','当前账号无结算管理权限')
    with connect() as conn:
        if not conn.execute('SELECT id FROM projects WHERE id=?',(project_id,)).fetchone():
            raise BusinessError(404,'REQ-4040','项目不存在')
        linked_contracts=linked_settlements=None
        if payload.contract_ids is not None:
            contract_ids=sorted(set(payload.contract_ids))
            if contract_ids:
                placeholders=','.join('?' for _ in contract_ids)
                rows=conn.execute(f'SELECT id,project_id FROM contracts WHERE id IN ({placeholders})',contract_ids).fetchall()
                if len(rows)!=len(contract_ids): raise BusinessError(404,'REQ-4040','包含不存在的合同')
                occupied=[row['id'] for row in rows if row['project_id'] not in (None,project_id)]
                if occupied: raise BusinessError(409,'REQ-4091','所选合同已关联其他项目，请先在原项目解除关联')
            conn.execute('UPDATE contracts SET project_id=NULL,updated_at=? WHERE project_id=?',(now_iso(),project_id))
            if contract_ids:
                placeholders=','.join('?' for _ in contract_ids)
                conn.execute(f'UPDATE contracts SET project_id=?,updated_at=? WHERE id IN ({placeholders})',(project_id,now_iso(),*contract_ids))
            linked_contracts=len(contract_ids)
        if payload.settlement_ids is not None:
            settlement_ids=sorted(set(payload.settlement_ids))
            if settlement_ids:
                placeholders=','.join('?' for _ in settlement_ids)
                rows=conn.execute(f'SELECT id,project_id FROM settlements WHERE id IN ({placeholders})',settlement_ids).fetchall()
                if len(rows)!=len(settlement_ids): raise BusinessError(404,'REQ-4040','包含不存在的结算单')
                occupied=[row['id'] for row in rows if row['project_id'] not in (None,project_id)]
                if occupied: raise BusinessError(409,'REQ-4091','所选结算单已关联其他项目，请先在原项目解除关联')
            conn.execute('UPDATE settlements SET project_id=NULL,updated_at=? WHERE project_id=?',(now_iso(),project_id))
            if settlement_ids:
                placeholders=','.join('?' for _ in settlement_ids)
                conn.execute(f'UPDATE settlements SET project_id=?,updated_at=? WHERE id IN ({placeholders})',(project_id,now_iso(),*settlement_ids))
            linked_settlements=len(settlement_ids)
        _audit(conn,request,actor,role,'维护项目合同与结算关联','project',project_id,details={'contract_count':linked_contracts,'settlement_count':linked_settlements})
        return {'code':0,'message':'项目关联信息已同步','data':{'contract_count':linked_contracts,'settlement_count':linked_settlements}}

@router.post('/projects/{project_id}/tasks')
def create_task(project_id:int,payload:TaskPayload,request:Request,x_user:Optional[str]=Header(None),x_role:Optional[str]=Header(None)):
    actor,role=_actor(x_user,x_role)
    _validate_date_range(payload.start_date, payload.end_date, '任务')
    with connect() as conn:
        if not conn.execute('SELECT id FROM projects WHERE id=?',(project_id,)).fetchone(): raise BusinessError(404,'REQ-4040','项目不存在')
        no=_next_no(conn,'project_tasks','task_no',f'TSK-{datetime.now().year}-'); now=now_iso(); cur=conn.execute("""INSERT INTO project_tasks(project_id,task_no,title,owner,status,priority,progress,start_date,end_date,parent_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",(project_id,no,payload.title,payload.owner,payload.status,payload.priority,payload.progress,payload.start_date,payload.end_date,payload.parent_id,now,now)); tid=cur.lastrowid; progress=_recalculate_project_progress(conn,project_id); _audit(conn,request,actor,role,'创建项目任务','project_task',tid); return {'code':0,'message':'任务已创建','data':{'id':tid,'task_no':no,'project_progress':progress}}

@router.put('/project-tasks/{task_id}')
def update_task(task_id:int,payload:TaskPayload,request:Request,x_user:Optional[str]=Header(None),x_role:Optional[str]=Header(None)):
    actor,role=_actor(x_user,x_role)
    _validate_date_range(payload.start_date, payload.end_date, '任务')
    with connect() as conn:
        row=conn.execute('SELECT project_id FROM project_tasks WHERE id=?',(task_id,)).fetchone()
        if not row: raise BusinessError(404,'REQ-4040','任务不存在')
        conn.execute("""UPDATE project_tasks SET title=?,owner=?,status=?,priority=?,progress=?,start_date=?,end_date=?,parent_id=?,updated_at=? WHERE id=?""",(payload.title,payload.owner,payload.status,payload.priority,payload.progress,payload.start_date,payload.end_date,payload.parent_id,now_iso(),task_id)); progress=_recalculate_project_progress(conn,row['project_id']); _audit(conn,request,actor,role,'更新项目任务','project_task',task_id); return {'code':0,'message':'任务已更新','data':{'project_progress':progress}}

@router.delete('/project-tasks/{task_id}')
def delete_task(task_id:int,request:Request,x_user:Optional[str]=Header(None),x_role:Optional[str]=Header(None)):
    actor,role=_actor(x_user,x_role)
    with connect() as conn:
        row=conn.execute('SELECT project_id FROM project_tasks WHERE id=?',(task_id,)).fetchone()
        if not row: raise BusinessError(404,'REQ-4040','任务不存在')
        conn.execute('DELETE FROM project_tasks WHERE id=?',(task_id,)); progress=_recalculate_project_progress(conn,row['project_id']); _audit(conn,request,actor,role,'删除项目任务','project_task',task_id); return {'code':0,'message':'任务已删除','data':{'project_progress':progress}}

@router.post('/projects/{project_id}/milestones')
def create_milestone(project_id:int,payload:MilestonePayload,request:Request,x_user:Optional[str]=Header(None),x_role:Optional[str]=Header(None)):
    actor,role=_actor(x_user,x_role)
    with connect() as conn:
        if not conn.execute('SELECT id FROM projects WHERE id=?',(project_id,)).fetchone(): raise BusinessError(404,'REQ-4040','项目不存在')
        now=now_iso(); cur=conn.execute("""INSERT INTO milestones(project_id,name,planned_date,actual_date,status,owner,description,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)""",(project_id,payload.name,payload.planned_date,payload.actual_date,payload.status,payload.owner,payload.description,now,now)); mid=cur.lastrowid; _audit(conn,request,actor,role,'创建里程碑','milestone',mid); return {'code':0,'message':'里程碑已创建','data':{'id':mid}}

@router.put('/milestones/{milestone_id}')
def update_milestone(milestone_id:int,payload:MilestonePayload,request:Request,x_user:Optional[str]=Header(None),x_role:Optional[str]=Header(None)):
    actor,role=_actor(x_user,x_role)
    with connect() as conn:
        if not conn.execute('SELECT id FROM milestones WHERE id=?',(milestone_id,)).fetchone(): raise BusinessError(404,'REQ-4040','里程碑不存在')
        conn.execute("""UPDATE milestones SET name=?,planned_date=?,actual_date=?,status=?,owner=?,description=?,updated_at=? WHERE id=?""",(payload.name,payload.planned_date,payload.actual_date,payload.status,payload.owner,payload.description,now_iso(),milestone_id)); _audit(conn,request,actor,role,'更新里程碑','milestone',milestone_id); return {'code':0,'message':'里程碑已更新'}

@router.delete('/milestones/{milestone_id}')
def delete_milestone(milestone_id:int,request:Request,x_user:Optional[str]=Header(None),x_role:Optional[str]=Header(None)):
    actor,role=_actor(x_user,x_role)
    with connect() as conn: conn.execute('DELETE FROM milestones WHERE id=?',(milestone_id,)); _audit(conn,request,actor,role,'删除里程碑','milestone',milestone_id); return {'code':0,'message':'里程碑已删除'}

@router.get('/project360/{project_id}')
def project360(project_id:int): return project_detail(project_id)

@router.post('/project360/{project_id}/query')
def project_robot_query(project_id:int,payload:dict,request:Request):
    user=getattr(request.state,'auth_user',None)
    if not user: raise BusinessError(401,'AUTH-4010','登录已失效，请重新登录后使用项目360机器人')
    permissions=user.get('permissions') or []
    if not has_permission(permissions,'ai') or not has_ai_capability(permissions,'query.project'):
        raise BusinessError(403,'AUTH-4030','当前角色未授权AI查询项目')
    q=str(payload.get('question','')).strip()
    detail=project_detail(project_id)['data']; task_count=len(detail['tasks']); done=sum(1 for x in detail['tasks'] if x['status']=='已完成'); overdue=sum(1 for x in detail['tasks'] if x.get('end_date') and x['status']!='已完成' and x['end_date'] < datetime.now().strftime('%Y-%m-%d'))
    if has_ai_capability(permissions,'query.budget'):
        budget=detail.get('budget'); budget_text='未关联预算' if not budget else f"预算{budget['total_budget']:,.0f}元，已使用{budget['used_budget']:,.0f}元，执行率{budget['used_budget']/budget['total_budget']*100:.1f}%"
    else: budget_text='当前角色无AI预算查询权限'
    demand_text=f"关联需求{len(detail['demands'])}条" if has_ai_capability(permissions,'query.demand') else '关联需求数已按权限隐藏'
    answer=f"{detail['project_no']} {detail['name']} 当前状态为{detail['status']}，总体进度{detail['progress']}%。共有{task_count}项任务，已完成{done}项，逾期未完成{overdue}项；{budget_text}；{demand_text}、合同{len(detail['contracts'])}份、结算{len(detail['settlements'])}笔。"
    if '风险' in q or '预警' in q:
        answer += ' 当前重点关注：逾期任务、预算执行率、未完成里程碑以及需求/TAPD同步异常。'
    return {'code':0,'data':{'answer':answer}}

@router.get('/budget-ledger')
def budget_ledger():
    with connect() as conn:
        rows=[]
        for r in conn.execute('SELECT * FROM budgets ORDER BY year DESC,id'):
            d=dict(r); d['transactions']=[dict(x) for x in conn.execute('SELECT * FROM budget_transactions WHERE budget_id=? ORDER BY id DESC LIMIT 20',(r['id'],))]; d['remaining']=d['total_budget']-d['used_budget']; d['execution_rate']=round(d['used_budget']/d['total_budget']*100,2) if d['total_budget'] else 0; rows.append(d)
        return {'code':0,'data':rows}

@router.post('/budgets')
def create_budget(payload:BudgetPayload,request:Request,x_user:Optional[str]=Header(None),x_role:Optional[str]=Header(None)):
    actor,role=_actor(x_user,x_role)
    _validate_budget_payload(payload)
    with connect() as conn:
        no=payload.budget_no or _next_no(conn,'budgets','budget_no',f'BUD-{payload.year}-'); cur=conn.execute("""INSERT INTO budgets(budget_no,budget_name,total_budget,used_budget,internal_total,internal_used,digital_total,digital_used,year) VALUES (?,?,?,?,?,?,?,?,?)""",(no,payload.budget_name,payload.total_budget,payload.used_budget,payload.internal_total,payload.internal_used,payload.digital_total,payload.digital_used,payload.year)); bid=cur.lastrowid; _audit(conn,request,actor,role,'创建预算','budget',bid); return {'code':0,'message':'预算已创建','data':{'id':bid,'budget_no':no}}

@router.put('/budgets/{budget_id}')
def update_budget(budget_id:int,payload:BudgetPayload,request:Request,x_user:Optional[str]=Header(None),x_role:Optional[str]=Header(None)):
    actor,role=_actor(x_user,x_role)
    _validate_budget_payload(payload)
    with connect() as conn:
        if not conn.execute('SELECT id FROM budgets WHERE id=?',(budget_id,)).fetchone(): raise BusinessError(404,'REQ-4040','预算不存在')
        conn.execute("""UPDATE budgets SET budget_name=?,total_budget=?,used_budget=?,internal_total=?,internal_used=?,digital_total=?,digital_used=?,year=? WHERE id=?""",(payload.budget_name,payload.total_budget,payload.used_budget,payload.internal_total,payload.internal_used,payload.digital_total,payload.digital_used,payload.year,budget_id)); _audit(conn,request,actor,role,'更新预算','budget',budget_id); return {'code':0,'message':'预算已更新'}

@router.post('/budgets/{budget_id}/transactions')
def budget_transaction(budget_id:int,payload:BudgetTxnPayload,request:Request,x_user:Optional[str]=Header(None),x_role:Optional[str]=Header(None)):
    actor,role=_actor(x_user,x_role)
    if payload.amount<=0: raise BusinessError(400,'REQ-4001','金额必须大于0')
    if payload.txn_type not in ('支出','占用','冲销','释放','追加预算','调减预算'):
        raise BusinessError(400,'REQ-4001','无效的预算流水类型')
    with connect() as conn:
        b=conn.execute('SELECT * FROM budgets WHERE id=?',(budget_id,)).fetchone();
        if not b: raise BusinessError(404,'REQ-4040','预算不存在')
        used=b['used_budget']; total=b['total_budget']
        if payload.txn_type in ('支出','占用'): used+=payload.amount
        elif payload.txn_type in ('冲销','释放'): used=max(0,used-payload.amount)
        elif payload.txn_type=='追加预算': total+=payload.amount
        elif payload.txn_type=='调减预算':
            if payload.amount>total: raise BusinessError(422,'BUD-4221','调减金额不能超过当前总预算')
            total-=payload.amount
        if used>total: raise BusinessError(422,'BUD-4220','预算不足')
        conn.execute('UPDATE budgets SET used_budget=?,total_budget=? WHERE id=?',(used,total,budget_id))
        cur=conn.execute('INSERT INTO budget_transactions(budget_id,txn_type,amount,reference_type,reference_id,description,department,created_at) VALUES (?,?,?,?,?,?,?,?)',(budget_id,payload.txn_type,payload.amount,payload.reference_type,payload.reference_id,payload.description,payload.department,now_iso()))
        # 同步刷新当前月/季度预算执行快照，供AI预算趋势查询使用。
        now_dt=datetime.now(); month=now_dt.strftime('%Y-%m'); quarter=f"{now_dt.year}Q{(now_dt.month-1)//3+1}"
        dep=payload.department or '未归属部门'
        for pt,period in [('month',month),('quarter',quarter)]:
            conn.execute('''INSERT INTO budget_execution_snapshots(budget_id,department,period_type,period,used_amount,total_budget,recorded_at)
                            VALUES (?,?,?,?,?,?,?) ON CONFLICT(budget_id,department,period_type,period)
                            DO UPDATE SET used_amount=excluded.used_amount,total_budget=excluded.total_budget,recorded_at=excluded.recorded_at''',(budget_id,dep,pt,period,used,total,now_iso()))
        _audit(conn,request,actor,role,'登记预算流水','budget_transaction',cur.lastrowid)
        return {'code':0,'message':'预算流水已登记','data':{'used_budget':used,'total_budget':total}}

@router.get('/business-values')
def list_values():
    with connect() as conn:
        rows=[dict(x) for x in conn.execute("""SELECT v.*,p.project_no,p.name project_name FROM business_values v LEFT JOIN projects p ON p.id=v.project_id ORDER BY v.updated_at DESC""")]; return {'code':0,'data':rows}

@router.post('/business-values')
def create_value(payload:ValuePayload,request:Request,x_user:Optional[str]=Header(None),x_role:Optional[str]=Header(None)):
    actor,role=_actor(x_user,x_role)
    with connect() as conn: now=now_iso(); cur=conn.execute("""INSERT INTO business_values(project_id,value_type,metric_name,planned_value,realized_value,unit,period,owner,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",(payload.project_id,payload.value_type,payload.metric_name,payload.planned_value,payload.realized_value,payload.unit,payload.period,payload.owner,payload.status,now,now)); _audit(conn,request,actor,role,'创建业务价值指标','business_value',cur.lastrowid); return {'code':0,'message':'业务价值指标已创建','data':{'id':cur.lastrowid}}

@router.put('/business-values/{item_id}')
def update_value(item_id:int,payload:ValuePayload,request:Request,x_user:Optional[str]=Header(None),x_role:Optional[str]=Header(None)):
    actor,role=_actor(x_user,x_role)
    with connect() as conn: conn.execute("""UPDATE business_values SET project_id=?,value_type=?,metric_name=?,planned_value=?,realized_value=?,unit=?,period=?,owner=?,status=?,updated_at=? WHERE id=?""",(payload.project_id,payload.value_type,payload.metric_name,payload.planned_value,payload.realized_value,payload.unit,payload.period,payload.owner,payload.status,now_iso(),item_id)); _audit(conn,request,actor,role,'更新业务价值指标','business_value',item_id); return {'code':0,'message':'业务价值指标已更新'}

@router.delete('/business-values/{item_id}')
def delete_value(item_id:int,request:Request,x_user:Optional[str]=Header(None),x_role:Optional[str]=Header(None)):
    actor,role=_actor(x_user,x_role)
    with connect() as conn: conn.execute('DELETE FROM business_values WHERE id=?',(item_id,)); _audit(conn,request,actor,role,'删除业务价值指标','business_value',item_id); return {'code':0,'message':'已删除'}

@router.get('/settlements')
def list_settlements(status:str=''):
    with connect() as conn:
        sql="""SELECT s.*,p.project_no,p.name project_name,c.contract_no,c.name contract_name,b.budget_no,b.budget_name FROM settlements s LEFT JOIN projects p ON p.id=s.project_id LEFT JOIN contracts c ON c.id=s.contract_id LEFT JOIN budgets b ON b.id=s.budget_id"""; args=[]
        if status: sql+=' WHERE s.status=?';args.append(status)
        sql+=' ORDER BY s.updated_at DESC'; return {'code':0,'data':[dict(x) for x in conn.execute(sql,args)]}

@router.post('/settlements')
def create_settlement(payload:SettlementPayload,request:Request,x_user:Optional[str]=Header(None),x_role:Optional[str]=Header(None)):
    actor,role=_actor(x_user,x_role)
    with connect() as conn:
        no=_next_no(conn,'settlements','settlement_no',f'SET-{datetime.now().year}-'); now=now_iso();cur=conn.execute("""INSERT INTO settlements(settlement_no,project_id,contract_id,budget_id,amount,settlement_type,applicant,description,status,current_node,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",(no,payload.project_id,payload.contract_id,payload.budget_id,payload.amount,payload.settlement_type,payload.applicant,payload.description,'草稿','草稿',now,now)); sid=cur.lastrowid;_audit(conn,request,actor,role,'创建结算申请','settlement',sid);return {'code':0,'message':'结算申请已创建','data':{'id':sid,'settlement_no':no}}

@router.put('/settlements/{item_id}')
def update_settlement(item_id:int,payload:SettlementPayload,request:Request,x_user:Optional[str]=Header(None),x_role:Optional[str]=Header(None)):
    actor,role=_actor(x_user,x_role)
    with connect() as conn:
        row=conn.execute('SELECT * FROM settlements WHERE id=?',(item_id,)).fetchone();
        if not row: raise BusinessError(404,'REQ-4040','结算单不存在')
        if row['status'] not in ('草稿','已驳回'): raise BusinessError(409,'REQ-4091','当前状态不允许编辑')
        conn.execute("""UPDATE settlements SET project_id=?,contract_id=?,budget_id=?,amount=?,settlement_type=?,applicant=?,description=?,updated_at=? WHERE id=?""",(payload.project_id,payload.contract_id,payload.budget_id,payload.amount,payload.settlement_type,payload.applicant,payload.description,now_iso(),item_id));_audit(conn,request,actor,role,'更新结算申请','settlement',item_id);return {'code':0,'message':'结算单已更新'}

@router.post('/settlements/{item_id}/submit')
def submit_settlement(item_id:int,request:Request,x_user:Optional[str]=Header(None),x_role:Optional[str]=Header(None)):
    actor,role=_actor(x_user,x_role)
    with connect() as conn:
        row=conn.execute('SELECT * FROM settlements WHERE id=?',(item_id,)).fetchone();
        if not row:raise BusinessError(404,'REQ-4040','结算单不存在')
        if row['amount']<=0:raise BusinessError(400,'REQ-4001','结算金额必须大于0')
        conn.execute("UPDATE settlements SET status='审批中',current_node='财务审批',updated_at=? WHERE id=?",(now_iso(),item_id));_audit(conn,request,actor,role,'提交结算申请','settlement',item_id);return {'code':0,'message':'结算申请已提交财务审批'}

@router.get('/settlement-approvals/pending')
def pending_settlements(request:Request,x_role:Optional[str]=Header(None)):
    roles=request_role_codes(request) or {x_role or ''}
    nodes=[node for role,node in {'finance':'财务审批','business_owner':'业务负责人确认'}.items() if role in roles]
    if 'admin' in roles:nodes=['财务审批','业务负责人确认']
    with connect() as conn:
        if not nodes:return {'code':0,'data':[]}
        placeholders=','.join('?' for _ in nodes)
        return {'code':0,'data':[dict(x) for x in conn.execute(f"SELECT * FROM settlements WHERE status='审批中' AND current_node IN ({placeholders}) ORDER BY updated_at DESC",nodes)]}

@router.post('/settlements/{item_id}/approve')
def approve_settlement(item_id:int,payload:ActionPayload,request:Request,x_user:Optional[str]=Header(None),x_role:Optional[str]=Header(None)):
    actor,role=_actor(x_user,x_role); mapping={'财务审批':'finance','业务负责人确认':'business_owner'}
    with connect() as conn:
        row=conn.execute('SELECT * FROM settlements WHERE id=?',(item_id,)).fetchone();
        if not row:raise BusinessError(404,'REQ-4040','结算单不存在')
        node=row['current_node']; expected=mapping.get(node)
        if not request_has_role(request,expected):raise BusinessError(403,'AUTH-4030','当前角色无该审批权限')
        action=_approval_action(payload.action)
        if action=='通过' and node=='财务审批':
            if not row['budget_id']: raise BusinessError(422,'BUD-4220','结算单未关联预算，财务无法通过')
            budget=conn.execute('SELECT * FROM budgets WHERE id=?',(row['budget_id'],)).fetchone()
            if not budget: raise BusinessError(422,'BUD-4220','关联预算不存在')
            if float(budget['used_budget'])+float(row['amount'])>float(budget['total_budget'])+0.01:
                raise BusinessError(422,'BUD-4220','预算不足，结算财务审批不可通过')
        conn.execute('INSERT INTO settlement_approvals(settlement_id,node,role,approver,action,comment,created_at) VALUES (?,?,?,?,?,?,?)',(item_id,node,role,actor,action,payload.comment,now_iso()))
        if action=='驳回':status='已驳回';next_node='草稿'
        elif node=='财务审批':status='审批中';next_node='业务负责人确认'
        else:status='已完成';next_node='已完成'
        conn.execute('UPDATE settlements SET status=?,current_node=?,updated_at=? WHERE id=?',(status,next_node,now_iso(),item_id))
        if status=='已完成' and row['budget_id']:
            b=conn.execute('SELECT * FROM budgets WHERE id=?',(row['budget_id'],)).fetchone()
            if not b or b['used_budget']+row['amount']>b['total_budget']+0.01:
                raise BusinessError(422,'BUD-4220','终审时预算已不足，未生成结算和预算流水')
            conn.execute('UPDATE budgets SET used_budget=used_budget+? WHERE id=?',(row['amount'],row['budget_id']));conn.execute('INSERT INTO budget_transactions(budget_id,txn_type,amount,reference_type,reference_id,description,created_at) VALUES (?,?,?,?,?,?,?)',(row['budget_id'],'支出',row['amount'],'结算',row['settlement_no'],'结算审批完成自动记账',now_iso()))
        _audit(conn,request,actor,role,f'结算{action}','settlement',item_id);return {'code':0,'message':f'结算{action}成功','data':{'status':status,'current_node':next_node}}

@router.get('/indicators')
def list_indicators():
    with connect() as conn:
        rows=[]
        for r in conn.execute('SELECT * FROM indicators ORDER BY category,id'):
            d=dict(r);d['records']=[dict(x) for x in conn.execute('SELECT * FROM indicator_records WHERE indicator_id=? ORDER BY period DESC,id DESC LIMIT 12',(r['id'],))];rows.append(d)
        return {'code':0,'data':rows}

@router.post('/indicators')
def create_indicator(payload:IndicatorPayload,request:Request,x_user:Optional[str]=Header(None),x_role:Optional[str]=Header(None)):
    actor,role=_actor(x_user,x_role)
    with connect() as conn:no=_next_no(conn,'indicators','indicator_no',f'KPI-{datetime.now().year}-');now=now_iso();cur=conn.execute("""INSERT INTO indicators(indicator_no,name,category,unit,formula,target_value,current_value,data_source,frequency,owner,status,direction,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(no,payload.name,payload.category,payload.unit,payload.formula,payload.target_value,payload.current_value,payload.data_source,payload.frequency,payload.owner,payload.status,payload.direction,now,now));_audit(conn,request,actor,role,'创建指标','indicator',cur.lastrowid);return {'code':0,'message':'指标已创建','data':{'id':cur.lastrowid,'indicator_no':no}}

@router.put('/indicators/{item_id}')
def update_indicator(item_id:int,payload:IndicatorPayload,request:Request,x_user:Optional[str]=Header(None),x_role:Optional[str]=Header(None)):
    actor,role=_actor(x_user,x_role)
    with connect() as conn:conn.execute("""UPDATE indicators SET name=?,category=?,unit=?,formula=?,target_value=?,current_value=?,data_source=?,frequency=?,owner=?,status=?,direction=?,updated_at=? WHERE id=?""",(payload.name,payload.category,payload.unit,payload.formula,payload.target_value,payload.current_value,payload.data_source,payload.frequency,payload.owner,payload.status,payload.direction,now_iso(),item_id));_audit(conn,request,actor,role,'更新指标','indicator',item_id);return {'code':0,'message':'指标已更新'}

@router.delete('/indicators/{item_id}')
def delete_indicator(item_id:int,request:Request,x_user:Optional[str]=Header(None),x_role:Optional[str]=Header(None)):
    actor,role=_actor(x_user,x_role)
    with connect() as conn:conn.execute('DELETE FROM indicator_records WHERE indicator_id=?',(item_id,));conn.execute('DELETE FROM indicators WHERE id=?',(item_id,));_audit(conn,request,actor,role,'删除指标','indicator',item_id);return {'code':0,'message':'指标已删除'}

@router.post('/indicators/{item_id}/records')
def add_indicator_record(item_id:int,payload:IndicatorRecordPayload,request:Request,x_user:Optional[str]=Header(None),x_role:Optional[str]=Header(None)):
    actor,role=_actor(x_user,x_role)
    with connect() as conn:cur=conn.execute('INSERT INTO indicator_records(indicator_id,period,value,source,created_at) VALUES (?,?,?,?,?)',(item_id,payload.period,payload.value,payload.source,now_iso()));conn.execute('UPDATE indicators SET current_value=?,updated_at=? WHERE id=?',(payload.value,now_iso(),item_id));_audit(conn,request,actor,role,'录入指标数据','indicator_record',cur.lastrowid);return {'code':0,'message':'指标数据已录入'}

@router.get('/contracts')
def list_contracts(status:str=''):
    with connect() as conn:
        sql="""SELECT c.*,p.project_no,p.name project_name,b.budget_no,b.budget_name FROM contracts c LEFT JOIN projects p ON p.id=c.project_id LEFT JOIN budgets b ON b.id=c.budget_id""";args=[]
        if status:sql+=' WHERE c.status=?';args.append(status)
        sql+=' ORDER BY c.updated_at DESC';return {'code':0,'data':[dict(x) for x in conn.execute(sql,args)]}

@router.get('/contracts/{item_id}')
def contract_detail(item_id:int):
    with connect() as conn:
        row=conn.execute('SELECT * FROM contracts WHERE id=?',(item_id,)).fetchone();
        if not row:raise BusinessError(404,'REQ-4040','合同不存在')
        d=dict(row);d['payments']=[dict(x) for x in conn.execute('SELECT * FROM payment_plans WHERE contract_id=? ORDER BY planned_date,id',(item_id,))];d['approvals']=[dict(x) for x in conn.execute('SELECT * FROM contract_approvals WHERE contract_id=? ORDER BY id',(item_id,))];return {'code':0,'data':d}

@router.post('/contracts')
def create_contract(payload:ContractPayload,request:Request,x_user:Optional[str]=Header(None),x_role:Optional[str]=Header(None)):
    actor,role=_actor(x_user,x_role)
    _validate_date_range(payload.start_date, payload.end_date, '合同')
    with connect() as conn:no=_next_no(conn,'contracts','contract_no',f'CT-{datetime.now().year}-');now=now_iso();cur=conn.execute("""INSERT INTO contracts(contract_no,name,project_id,budget_id,supplier,total_amount,start_date,end_date,owner,description,status,current_node,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(no,payload.name,payload.project_id,payload.budget_id,payload.supplier,payload.total_amount,payload.start_date,payload.end_date,payload.owner,payload.description,'草稿','草稿',now,now));_audit(conn,request,actor,role,'创建合同','contract',cur.lastrowid);return {'code':0,'message':'合同已创建','data':{'id':cur.lastrowid,'contract_no':no}}

@router.put('/contracts/{item_id}')
def update_contract(item_id:int,payload:ContractPayload,request:Request,x_user:Optional[str]=Header(None),x_role:Optional[str]=Header(None)):
    actor,role=_actor(x_user,x_role)
    _validate_date_range(payload.start_date, payload.end_date, '合同')
    with connect() as conn:
        row=conn.execute('SELECT * FROM contracts WHERE id=?',(item_id,)).fetchone();
        if not row:raise BusinessError(404,'REQ-4040','合同不存在')
        if row['status'] not in ('草稿','已驳回'):raise BusinessError(409,'REQ-4091','当前合同状态不允许编辑')
        conn.execute("""UPDATE contracts SET name=?,project_id=?,budget_id=?,supplier=?,total_amount=?,start_date=?,end_date=?,owner=?,description=?,updated_at=? WHERE id=?""",(payload.name,payload.project_id,payload.budget_id,payload.supplier,payload.total_amount,payload.start_date,payload.end_date,payload.owner,payload.description,now_iso(),item_id));_audit(conn,request,actor,role,'更新合同','contract',item_id);return {'code':0,'message':'合同已更新'}

@router.post('/contracts/{item_id}/submit')
def submit_contract(item_id:int,request:Request,x_user:Optional[str]=Header(None),x_role:Optional[str]=Header(None)):
    actor,role=_actor(x_user,x_role)
    with connect() as conn:
        row=conn.execute('SELECT * FROM contracts WHERE id=?',(item_id,)).fetchone();
        if not row:raise BusinessError(404,'REQ-4040','合同不存在')
        if row['total_amount']<=0:raise BusinessError(400,'REQ-4001','合同金额必须大于0')
        conn.execute("UPDATE contracts SET status='审批中',current_node='财务会签',updated_at=? WHERE id=?",(now_iso(),item_id));_audit(conn,request,actor,role,'提交合同审批','contract',item_id);return {'code':0,'message':'合同已提交财务会签'}

@router.get('/contract-approvals/pending')
def pending_contracts(request:Request,x_role:Optional[str]=Header(None)):
    roles=request_role_codes(request) or {x_role or ''}
    nodes=[node for role,node in {'finance':'财务会签','business_owner':'业务负责人终审'}.items() if role in roles]
    if 'admin' in roles:nodes=['财务会签','业务负责人终审']
    with connect() as conn:
        if not nodes:return {'code':0,'data':[]}
        placeholders=','.join('?' for _ in nodes)
        return {'code':0,'data':[dict(x) for x in conn.execute(f"SELECT * FROM contracts WHERE status='审批中' AND current_node IN ({placeholders}) ORDER BY updated_at DESC",nodes)]}

@router.post('/contracts/{item_id}/approve')
def approve_contract(item_id:int,payload:ActionPayload,request:Request,x_user:Optional[str]=Header(None),x_role:Optional[str]=Header(None)):
    actor,role=_actor(x_user,x_role);mapping={'财务会签':'finance','业务负责人终审':'business_owner'}
    with connect() as conn:
        row=conn.execute('SELECT * FROM contracts WHERE id=?',(item_id,)).fetchone();
        if not row:raise BusinessError(404,'REQ-4040','合同不存在')
        node=row['current_node'];expected=mapping.get(node)
        if not request_has_role(request,expected):raise BusinessError(403,'AUTH-4030','当前角色无该合同审批权限')
        action=_approval_action(payload.action);conn.execute('INSERT INTO contract_approvals(contract_id,node,role,approver,action,comment,created_at) VALUES (?,?,?,?,?,?,?)',(item_id,node,role,actor,action,payload.comment,now_iso()))
        if action=='驳回':status='已驳回';next_node='草稿'
        elif node=='财务会签':status='审批中';next_node='业务负责人终审'
        else:status='执行中';next_node='已完成审批'
        conn.execute('UPDATE contracts SET status=?,current_node=?,updated_at=? WHERE id=?',(status,next_node,now_iso(),item_id));_audit(conn,request,actor,role,f'合同{action}','contract',item_id);return {'code':0,'message':f'合同{action}成功'}

@router.post('/contracts/{item_id}/payments')
def create_payment(item_id:int,payload:PaymentPayload,request:Request,x_user:Optional[str]=Header(None),x_role:Optional[str]=Header(None)):
    actor,role=_actor(x_user,x_role)
    with connect() as conn:
        total=conn.execute('SELECT total_amount FROM contracts WHERE id=?',(item_id,)).fetchone();
        if not total:raise BusinessError(404,'REQ-4040','合同不存在')
        planned=conn.execute('SELECT COALESCE(SUM(amount),0) v FROM payment_plans WHERE contract_id=?',(item_id,)).fetchone()['v']
        if planned+payload.amount>total['total_amount']+0.01:raise BusinessError(422,'BUD-4221','付款计划累计金额不能超过合同总金额')
        no=_next_no(conn,'payment_plans','plan_no',f'PAY-{datetime.now().year}-');now=now_iso();cur=conn.execute("""INSERT INTO payment_plans(contract_id,plan_no,payment_type,amount,planned_date,actual_date,status,description,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",(item_id,no,payload.payment_type,payload.amount,payload.planned_date,payload.actual_date,payload.status,payload.description,now,now));_audit(conn,request,actor,role,'创建付款计划','payment_plan',cur.lastrowid);return {'code':0,'message':'付款计划已创建','data':{'id':cur.lastrowid,'plan_no':no}}

@router.put('/payment-plans/{item_id}')
def update_payment(item_id:int,payload:PaymentPayload,request:Request,x_user:Optional[str]=Header(None),x_role:Optional[str]=Header(None)):
    actor,role=_actor(x_user,x_role)
    with connect() as conn:
        current=conn.execute('SELECT * FROM payment_plans WHERE id=?',(item_id,)).fetchone()
        if not current: raise BusinessError(404,'REQ-4040','付款计划不存在')
        contract=conn.execute('SELECT total_amount FROM contracts WHERE id=?',(current['contract_id'],)).fetchone()
        other=conn.execute('SELECT COALESCE(SUM(amount),0) v FROM payment_plans WHERE contract_id=? AND id<>?',(current['contract_id'],item_id)).fetchone()['v']
        if not contract or other+payload.amount>contract['total_amount']+0.01: raise BusinessError(422,'BUD-4221','付款计划累计金额不能超过合同总金额')
        conn.execute("""UPDATE payment_plans SET payment_type=?,amount=?,planned_date=?,actual_date=?,status=?,description=?,updated_at=? WHERE id=?""",(payload.payment_type,payload.amount,payload.planned_date,payload.actual_date,payload.status,payload.description,now_iso(),item_id));_audit(conn,request,actor,role,'更新付款计划','payment_plan',item_id);return {'code':0,'message':'付款计划已更新'}
