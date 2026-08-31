import json
import os
import re
import sqlite3
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = Path(os.getenv("TRM_DB_PATH", DATA_DIR / "trm_system.db"))


def database_backend() -> str:
    """Return the selected database backend without exposing credentials."""
    configured = os.getenv("TRM_DATABASE_BACKEND", "").strip().lower()
    if configured in {"sqlite", "sqlite3"}:
        return "sqlite"
    if configured in {"postgres", "postgresql", "neon"}:
        return "postgresql"
    return "postgresql" if os.getenv("DATABASE_URL", "").strip() else "sqlite"


def is_postgres_backend() -> bool:
    return database_backend() == "postgresql"


class CompatRow(Mapping):
    """sqlite3.Row compatible mapping for PostgreSQL result rows."""

    def __init__(self, columns: Iterable[str], values: Iterable[Any]):
        self._columns = tuple(columns)
        self._values = tuple(values)
        self._data = dict(zip(self._columns, self._values))

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return self._data[key]

    def __iter__(self):
        return iter(self._columns)

    def __len__(self):
        return len(self._columns)


class _MemoryCursor:
    def __init__(self, rows=(), *, lastrowid=None, rowcount=-1):
        self._rows = list(rows)
        self._index = 0
        self.lastrowid = lastrowid
        self.rowcount = rowcount

    def fetchone(self):
        if self._index >= len(self._rows):
            return None
        row = self._rows[self._index]
        self._index += 1
        return row

    def fetchall(self):
        rows = self._rows[self._index :]
        self._index = len(self._rows)
        return rows

    def __iter__(self):
        return iter(self.fetchall())


class _PostgresCursor:
    def __init__(self, cursor, *, lastrowid=None):
        self._cursor = cursor
        self.lastrowid = lastrowid
        self.rowcount = cursor.rowcount

    def _convert(self, row):
        if row is None:
            return None
        columns = [item.name for item in self._cursor.description]
        return CompatRow(columns, row)

    def fetchone(self):
        return self._convert(self._cursor.fetchone())

    def fetchall(self):
        return [self._convert(row) for row in self._cursor.fetchall()]

    def __iter__(self):
        while True:
            row = self.fetchone()
            if row is None:
                break
            yield row


_INSERT_TABLE_RE = re.compile(r"^\s*INSERT\s+(?:OR\s+IGNORE\s+)?INTO\s+([A-Za-z_][A-Za-z0-9_]*)", re.I | re.S)


def _translate_postgres_sql(sql: str) -> tuple[str, bool]:
    """Translate the small SQLite SQL subset used by this application."""
    translated = sql.strip()
    ignored_insert = bool(re.match(r"^\s*INSERT\s+OR\s+IGNORE\s+INTO\b", translated, re.I))
    translated = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", translated, flags=re.I)
    translated = re.sub(
        r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b",
        "SERIAL PRIMARY KEY",
        translated,
        flags=re.I,
    )
    translated = re.sub(r"\bREAL\b", "DOUBLE PRECISION", translated, flags=re.I)
    # psycopg uses percent-style binding; literal SQL percent signs must be escaped.
    translated = translated.replace("%", "%%")
    translated = translated.replace("?", "%s")
    if ignored_insert and not re.search(r"\bON\s+CONFLICT\b", translated, re.I):
        translated = translated.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    return translated, ignored_insert


class PostgresConnection:
    """Compatibility facade exposing the sqlite3 methods used by TRM."""

    backend = "postgresql"

    def __init__(self, raw_connection):
        self._connection = raw_connection
        self.total_changes = 0
        self._id_column_cache: dict[str, bool] = {}

    def _table_has_id(self, table: str) -> bool:
        cached = self._id_column_cache.get(table)
        if cached is not None:
            return cached
        cur = self._connection.cursor()
        cur.execute(
            """SELECT 1 FROM information_schema.columns
               WHERE table_schema=current_schema() AND table_name=%s AND column_name='id'""",
            (table,),
        )
        found = cur.fetchone() is not None
        cur.close()
        self._id_column_cache[table] = found
        return found

    def execute(self, sql: str, parameters: Optional[Iterable[Any]] = None):
        stripped = sql.strip()
        if re.match(r"^PRAGMA\s+foreign_keys\b", stripped, re.I) or re.match(r"^PRAGMA\s+optimize\b", stripped, re.I):
            return _MemoryCursor()
        table_info = re.match(r"^PRAGMA\s+table_info\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)", stripped, re.I)
        if table_info:
            table = table_info.group(1)
            cur = self._connection.cursor()
            cur.execute(
                """SELECT ordinal_position-1 AS cid,column_name AS name,data_type AS type,
                          CASE WHEN is_nullable='NO' THEN 1 ELSE 0 END AS notnull,
                          column_default AS dflt_value,
                          CASE WHEN column_name IN (
                              SELECT kcu.column_name
                              FROM information_schema.table_constraints tc
                              JOIN information_schema.key_column_usage kcu
                                ON tc.constraint_name=kcu.constraint_name AND tc.table_schema=kcu.table_schema
                              WHERE tc.constraint_type='PRIMARY KEY' AND tc.table_schema=current_schema()
                                AND tc.table_name=%s
                          ) THEN 1 ELSE 0 END AS pk
                   FROM information_schema.columns
                   WHERE table_schema=current_schema() AND table_name=%s
                   ORDER BY ordinal_position""",
                (table, table),
            )
            rows = [CompatRow(("cid", "name", "type", "notnull", "dflt_value", "pk"), row) for row in cur.fetchall()]
            cur.close()
            return _MemoryCursor(rows)
        if re.match(r"^BEGIN\s+IMMEDIATE\b", stripped, re.I):
            # Preserve SQLite's serialized-write intent for MCP idempotency and
            # number generation while using PostgreSQL transaction semantics.
            cur = self._connection.cursor()
            cur.execute("SELECT pg_advisory_xact_lock(846721904)")
            cur.fetchone()
            return _MemoryCursor()

        translated, _ = _translate_postgres_sql(sql)
        insert_match = _INSERT_TABLE_RE.match(sql)
        return_id = False
        if insert_match and not re.search(r"\bRETURNING\b", translated, re.I):
            table = insert_match.group(1)
            if self._table_has_id(table):
                translated = translated.rstrip().rstrip(";") + " RETURNING id"
                return_id = True

        cursor = self._connection.cursor()
        cursor.execute(translated, tuple(parameters or ()))
        lastrowid = None
        if return_id:
            returned = cursor.fetchone()
            lastrowid = returned[0] if returned else None
        if cursor.rowcount and cursor.rowcount > 0 and re.match(r"^(INSERT|UPDATE|DELETE)\b", stripped, re.I):
            self.total_changes += cursor.rowcount
        return _PostgresCursor(cursor, lastrowid=lastrowid)

    def executemany(self, sql: str, seq_of_parameters):
        translated, _ = _translate_postgres_sql(sql)
        cursor = self._connection.cursor()
        cursor.executemany(translated, list(seq_of_parameters))
        if cursor.rowcount and cursor.rowcount > 0:
            self.total_changes += cursor.rowcount
        return _PostgresCursor(cursor)

    def executescript(self, script: str):
        statement = ""
        for line in script.splitlines(keepends=True):
            statement += line
            if sqlite3.complete_statement(statement):
                if statement.strip():
                    self.execute(statement)
                statement = ""
        if statement.strip():
            self.execute(statement)
        return _MemoryCursor()

    def commit(self):
        self._connection.commit()

    def rollback(self):
        self._connection.rollback()

    def close(self):
        self._connection.close()


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@contextmanager
def connect():
    if is_postgres_backend():
        database_url = os.getenv("DATABASE_URL", "").strip()
        if not database_url:
            raise RuntimeError("已选择 PostgreSQL，但未设置 DATABASE_URL")
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - deployment dependency guard
            raise RuntimeError("PostgreSQL 模式需要安装 psycopg[binary]") from exc
        raw = psycopg.connect(database_url, autocommit=False)
        conn = PostgresConnection(raw)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
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
        if not is_postgres_backend():
            # WAL允许页面读取和后台TAPD同步并发进行；busy_timeout负责短暂写锁等待。
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
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
            cur = conn.execute(
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
            did = cur.lastrowid
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

        # 旧版本把需求功能点和功能点库分开维护，导致新增功能点无法在“功能点管理”中看到。
        # 启动时为历史功能点补齐可复用的目录记录，并建立稳定关联。
        legacy_points = list(conn.execute(
            "SELECT * FROM function_points WHERE catalog_id IS NULL ORDER BY id"
        ))
        for point in legacy_points:
            catalog = conn.execute(
                """SELECT id FROM function_point_catalog
                    WHERE name=? AND system_name=? AND demand_summary=? LIMIT 1""",
                (point["name"], point["system_name"], point["demand_summary"]),
            ).fetchone()
            if catalog:
                catalog_id = int(catalog["id"])
            else:
                year = (point["fp_no"].split("-")[1] if point["fp_no"] and "-" in point["fp_no"] else datetime.now().strftime("%Y"))
                prefix = f"FPC-{year}-"
                last = conn.execute(
                    "SELECT catalog_no FROM function_point_catalog WHERE catalog_no LIKE ? ORDER BY catalog_no DESC LIMIT 1",
                    (f"{prefix}%",),
                ).fetchone()
                seq = int(last["catalog_no"].split("-")[-1]) + 1 if last else 1
                cur = conn.execute(
                    """INSERT INTO function_point_catalog
                       (catalog_no,demand_summary,name,system_name,default_fp_count,unit_price,department,team,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (f"{prefix}{seq:04d}", point["demand_summary"], point["name"] or point["demand_summary"] or "未命名功能点",
                     point["system_name"], point["fp_count"], point["unit_price"], point["department"], point["team"],
                     point["created_at"], now_iso()),
                )
                catalog_id = int(cur.lastrowid)
            conn.execute("UPDATE function_points SET catalog_id=? WHERE id=?", (catalog_id, point["id"]))

        conn.execute("UPDATE demands SET applicant_code='lili11-ghq' WHERE applicant_code IS NULL OR applicant_code=''")
        conn.execute("PRAGMA optimize")


def get_budget_by_name(conn, name: str):
    return row_to_dict(conn.execute("SELECT * FROM budgets WHERE budget_name=?", (name,)).fetchone())
