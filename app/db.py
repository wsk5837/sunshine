import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = Path(os.getenv("TRM_DB_PATH", DATA_DIR / "trm_system.db"))


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def row_to_dict(row):
    return dict(row) if row else None


def init_db():
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS demands (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                demand_no TEXT UNIQUE,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                demand_type TEXT NOT NULL,
                budget_sources TEXT NOT NULL DEFAULT '[]',
                priority TEXT NOT NULL DEFAULT '低',
                applicant TEXT NOT NULL,
                applicant_dept TEXT DEFAULT '数字化管理部',
                budget_amount REAL NOT NULL DEFAULT 0,
                estimated_amount REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT '草稿',
                current_node TEXT DEFAULT '草稿',
                tapd_id TEXT,
                tapd_url TEXT,
                tapd_status TEXT,
                tapd_sync_status TEXT DEFAULT '未同步',
                tapd_last_sync_at TEXT,
                planned_online_date TEXT,
                actual_online_date TEXT,
                internal_days REAL DEFAULT 0,
                external_days REAL DEFAULT 0,
                estimated_hours REAL DEFAULT 0,
                actual_hours REAL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                submitted_at TEXT,
                closed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                demand_id INTEGER NOT NULL,
                original_name TEXT NOT NULL,
                stored_name TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                mime_type TEXT,
                category TEXT DEFAULT '普通附件',
                created_at TEXT NOT NULL,
                FOREIGN KEY(demand_id) REFERENCES demands(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS approval_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                demand_id INTEGER NOT NULL,
                node TEXT NOT NULL,
                role TEXT NOT NULL,
                approver TEXT NOT NULL,
                action TEXT NOT NULL,
                comment TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY(demand_id) REFERENCES demands(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS function_points (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                demand_id INTEGER NOT NULL,
                fp_no TEXT UNIQUE NOT NULL,
                demand_summary TEXT DEFAULT '',
                name TEXT DEFAULT '',
                system_name TEXT NOT NULL,
                evaluator TEXT NOT NULL,
                department TEXT NOT NULL,
                team TEXT NOT NULL,
                evaluation_date TEXT NOT NULL,
                fp_count REAL NOT NULL DEFAULT 0,
                unit_price REAL NOT NULL DEFAULT 1200,
                estimated_amount REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY(demand_id) REFERENCES demands(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS allocations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                demand_id INTEGER NOT NULL,
                function_point_id INTEGER,
                expense_subject TEXT NOT NULL,
                expense_source TEXT NOT NULL,
                ratio REAL NOT NULL,
                amount REAL NOT NULL,
                department TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(demand_id) REFERENCES demands(id) ON DELETE CASCADE,
                FOREIGN KEY(function_point_id) REFERENCES function_points(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS budgets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                budget_no TEXT UNIQUE NOT NULL,
                budget_name TEXT NOT NULL,
                total_budget REAL NOT NULL,
                used_budget REAL NOT NULL,
                internal_total REAL NOT NULL DEFAULT 0,
                internal_used REAL NOT NULL DEFAULT 0,
                digital_total REAL NOT NULL DEFAULT 0,
                digital_used REAL NOT NULL DEFAULT 0,
                year INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tapd_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                demand_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                success INTEGER NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 1,
                request_id TEXT,
                message TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(demand_id) REFERENCES demands(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                demand_id INTEGER,
                level TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                target_role TEXT,
                is_read INTEGER NOT NULL DEFAULT 0,
                event_key TEXT DEFAULT '',
                resolved_at TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(demand_id) REFERENCES demands(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_notifications_demand_title_role
            ON notifications(demand_id,title,target_role);

            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                demand_id INTEGER,
                actor TEXT NOT NULL,
                role TEXT NOT NULL,
                action TEXT NOT NULL,
                object_type TEXT NOT NULL,
                object_id TEXT,
                result TEXT NOT NULL,
                request_id TEXT,
                details TEXT,
                created_at TEXT NOT NULL
            );


            CREATE TABLE IF NOT EXISTS function_point_catalog (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                catalog_no TEXT UNIQUE NOT NULL,
                demand_summary TEXT DEFAULT '',
                name TEXT NOT NULL,
                system_name TEXT NOT NULL,
                default_fp_count REAL NOT NULL DEFAULT 0,
                unit_price REAL NOT NULL DEFAULT 1200,
                department TEXT DEFAULT '产品研发部',
                team TEXT DEFAULT '研发团队',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )


        # 兼容旧数据库：按需补充 V2 字段。
        demand_cols = {r[1] for r in conn.execute("PRAGMA table_info(demands)")}
        if "applicant_code" not in demand_cols:
            conn.execute("ALTER TABLE demands ADD COLUMN applicant_code TEXT DEFAULT 'lili11-ghq'")
        if "last_sync_source" not in demand_cols:
            conn.execute("ALTER TABLE demands ADD COLUMN last_sync_source TEXT")

        fp_cols = {r[1] for r in conn.execute("PRAGMA table_info(function_points)")}
        if "catalog_id" not in fp_cols:
            conn.execute("ALTER TABLE function_points ADD COLUMN catalog_id INTEGER")
        if "source_type" not in fp_cols:
            conn.execute("ALTER TABLE function_points ADD COLUMN source_type TEXT DEFAULT '新增'")

        alloc_cols = {r[1] for r in conn.execute("PRAGMA table_info(allocations)")}
        if "system_name" not in alloc_cols:
            conn.execute("ALTER TABLE allocations ADD COLUMN system_name TEXT DEFAULT ''")

        notification_cols = {r[1] for r in conn.execute("PRAGMA table_info(notifications)")}
        if "event_key" not in notification_cols:
            conn.execute("ALTER TABLE notifications ADD COLUMN event_key TEXT DEFAULT ''")
        if "resolved_at" not in notification_cols:
            conn.execute("ALTER TABLE notifications ADD COLUMN resolved_at TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_notifications_event_role ON notifications(event_key,target_role,resolved_at)"
        )

        if conn.execute("SELECT COUNT(*) c FROM function_point_catalog").fetchone()["c"] == 0:
            created = now_iso()
            conn.executemany(
                """INSERT INTO function_point_catalog
                (catalog_no,demand_summary,name,system_name,default_fp_count,unit_price,department,team,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                [
                    ("FPC-2026-0001", "预算单据查询及附件回传", "预算单据查询服务", "费用预算管理服务平台", 38, 1200, "产品研发部", "费用平台组", created, created),
                    ("FPC-2026-0002", "稽核风险指标数据回传", "风险指标回传服务", "AIP稽核智能平台", 28, 1200, "产品研发部", "智能稽核组", created, created),
                    ("FPC-2026-0003", "OA待办推送与审批结果回写", "OA审批集成", "OA流程平台", 22, 1200, "集成研发部", "企业集成组", created, created),
                    ("FPC-2026-0004", "需求状态、任务与工时增量同步", "TAPD状态回读", "TAPD", 26, 1200, "产品研发部", "研发效能组", created, created),
                ],
            )

        if conn.execute("SELECT COUNT(*) c FROM budgets").fetchone()["c"] == 0:
            conn.executemany(
                """INSERT INTO budgets
                (budget_no,budget_name,total_budget,used_budget,internal_total,internal_used,digital_total,digital_used,year)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                [
                    ("BUD-2026-0032", "机构透视管理机器人项目", 5_000_000, 3_200_000, 2_800_000, 1_760_000, 2_200_000, 1_440_000, 2026),
                    ("BUD-2026-0046", "科技运营平台年度常规预算", 2_000_000, 1_020_000, 1_100_000, 580_000, 900_000, 440_000, 2026),
                    ("BUD-2026-0061", "数字化创新专项预算", 1_500_000, 620_000, 900_000, 380_000, 600_000, 240_000, 2026),
                ],
            )

        if conn.execute("SELECT COUNT(*) c FROM demands").fetchone()["c"] == 0:
            created = now_iso()
            conn.execute(
                """INSERT INTO demands
                (demand_no,title,description,demand_type,budget_sources,priority,applicant,applicant_dept,
                 budget_amount,estimated_amount,status,current_node,tapd_id,tapd_url,tapd_status,tapd_sync_status,
                 internal_days,external_days,estimated_hours,actual_hours,created_at,updated_at,submitted_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    "REQ-20260817-0001",
                    "关于AIP稽核智能平台与费用预算管理服务平台对接的需求申请",
                    "稽核人员需要费控系统能够根据稽核智能平台提供的预付款单单号，返回对应的单据附件下载地址及合同号，用以支撑风险指标建模与分析。",
                    "智能化改造项目",
                    json.dumps(["机构透视管理机器人项目"], ensure_ascii=False),
                    "中",
                    "李莉 lili11-ghq",
                    "数字化管理部",
                    86000,
                    79200,
                    "产品经理审批",
                    "产品经理审批",
                    None,
                    None,
                    None,
                    "未同步",
                    18,
                    5,
                    184,
                    0,
                    created,
                    created,
                    created,
                ),
            )
            did = conn.execute("SELECT last_insert_rowid() id").fetchone()["id"]
            conn.execute(
                """INSERT INTO approval_records(demand_id,node,role,approver,action,comment,created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (did, "直属领导审批", "department_head", "王主任", "通过", "需求目标明确，同意进入产品评估。", created),
            )
            conn.executemany(
                """INSERT INTO function_points
                (demand_id,fp_no,demand_summary,name,system_name,evaluator,department,team,evaluation_date,fp_count,unit_price,estimated_amount,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (did, "FP-2026-0001", "预算单据接口查询", "预算单据查询服务", "费用预算管理服务平台", "赵敏", "产品研发部", "费用平台组", "2026-08-17", 38, 1200, 45600, created),
                    (did, "FP-2026-0002", "风险指标数据回传", "稽核风险指标回传", "AIP稽核智能平台", "赵敏", "产品研发部", "智能稽核组", "2026-08-17", 28, 1200, 33600, created),
                ],
            )

        conn.execute("UPDATE demands SET applicant_code='lili11-ghq' WHERE applicant_code IS NULL OR applicant_code=''")
        conn.execute("PRAGMA optimize")


def get_budget_by_name(conn, name: str):
    return row_to_dict(conn.execute("SELECT * FROM budgets WHERE budget_name=?", (name,)).fetchone())
