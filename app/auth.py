import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, Field

from .db import connect, now_iso
from .rules import BusinessError, ROLE_LABELS

router = APIRouter()

DEFAULT_ADMIN_PASSWORD = os.getenv("TRM_INITIAL_ADMIN_PASSWORD", "Admin@123")
DEFAULT_DEMO_PASSWORD = os.getenv("TRM_INITIAL_DEMO_PASSWORD", "Demo@123")
SESSION_HOURS = int(os.getenv("TRM_SESSION_HOURS", "12"))

PERMISSION_CATALOG = {
    "dashboard": "首页驾驶舱",
    "project360": "项目360视图",
    "value": "业务价值总览",
    "budget": "预算管理",
    "initiative.list": "立项列表",
    "initiative.create": "新建立项",
    "initiative.approve": "立项审批",
    "demand.list": "需求列表",
    "demand.create": "新建需求",
    "demand.approve": "需求审批",
    "demand.evaluate": "费用评估与预算",
    "function_points": "功能点管理",
    "tapd": "TAPD同步",
    "ai": "AI智能问答",
    "project": "项目管理",
    "settlement": "结算管理",
    "indicator": "指标库",
    "contract": "合同管理",
    "system.users": "用户管理",
    "system.roles": "角色管理",
    "system.integrations": "集成配置",
    "system.audit": "审计日志",
}

DEFAULT_ROLE_PERMISSIONS = {
    "applicant": ["dashboard", "project360", "value", "budget", "initiative.list", "initiative.create", "demand.list", "demand.create", "tapd", "ai"],
    "department_head": ["dashboard", "project360", "value", "budget", "initiative.list", "initiative.approve", "demand.list", "demand.approve", "tapd", "ai"],
    "product_manager": ["dashboard", "project360", "value", "budget", "initiative.list", "demand.list", "demand.approve", "demand.evaluate", "function_points", "tapd", "ai", "project"],
    "finance": ["dashboard", "project360", "value", "budget", "initiative.list", "initiative.approve", "demand.list", "demand.approve", "tapd", "ai", "settlement", "contract"],
    "vp": ["dashboard", "project360", "value", "budget", "initiative.list", "initiative.approve", "demand.list", "demand.approve", "tapd", "ai", "project"],
    "business_owner": ["dashboard", "project360", "value", "budget", "initiative.list", "initiative.approve", "demand.list", "demand.approve", "tapd", "ai", "project", "settlement", "contract", "indicator"],
    "project_manager": ["dashboard", "project360", "value", "budget", "initiative.list", "demand.list", "tapd", "ai", "project", "settlement", "indicator", "contract"],
    "admin": ["*"],
}


def _password_hash(password: str, salt_hex: Optional[str] = None) -> str:
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 160_000)
    return f"pbkdf2_sha256${salt.hex()}${digest.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt_hex, expected = stored.split("$", 2)
        if algo != "pbkdf2_sha256":
            return False
        actual = _password_hash(password, salt_hex).split("$", 2)[2]
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def init_auth_db():
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS system_roles (
                code TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                description TEXT DEFAULT '',
                permissions TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT '启用',
                built_in INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS system_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                display_name TEXT NOT NULL,
                department TEXT DEFAULT '',
                email TEXT DEFAULT '',
                phone TEXT DEFAULT '',
                role_code TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT '启用',
                last_login TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(role_code) REFERENCES system_roles(code)
            );
            CREATE TABLE IF NOT EXISTS auth_sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES system_users(id) ON DELETE CASCADE
            );
            """
        )
        ts = now_iso()
        for code, label in ROLE_LABELS.items():
            permissions = json.dumps(DEFAULT_ROLE_PERMISSIONS.get(code, []), ensure_ascii=False)
            conn.execute(
                """INSERT INTO system_roles(code,label,description,permissions,status,built_in,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(code) DO NOTHING""",
                (code, label, f"系统内置{label}角色", permissions, "启用", 1, ts, ts),
            )
        seed_users = [
            ("admin", "系统管理员", "平台运维", "admin@trm.local", "", "admin", DEFAULT_ADMIN_PASSWORD),
            ("lili11-ghq", "李莉", "数字化管理部", "lili@trm.local", "", "applicant", DEFAULT_DEMO_PASSWORD),
            ("wangzg", "王志刚", "数字化管理部", "wangzg@trm.local", "", "department_head", DEFAULT_DEMO_PASSWORD),
            ("zhaomin", "赵敏", "产品研发部", "zhaomin@trm.local", "", "product_manager", DEFAULT_DEMO_PASSWORD),
            ("chenacct", "陈会计", "财务部", "chenacct@trm.local", "", "finance", DEFAULT_DEMO_PASSWORD),
            ("liuvp", "刘总", "分管领导", "liuvp@trm.local", "", "vp", DEFAULT_DEMO_PASSWORD),
            ("zhouowner", "周总", "业务管理部", "zhouowner@trm.local", "", "business_owner", DEFAULT_DEMO_PASSWORD),
            ("wangwj", "王卫嘉", "项目管理部", "wangwj@trm.local", "", "project_manager", DEFAULT_DEMO_PASSWORD),
        ]
        for username, display, dept, email, phone, role, password in seed_users:
            conn.execute(
                """INSERT INTO system_users(username,display_name,department,email,phone,role_code,password_hash,status,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(username) DO NOTHING""",
                (username, display, dept, email, phone, role, _password_hash(password), "启用", ts, ts),
            )
        # remove expired sessions
        conn.execute("DELETE FROM auth_sessions WHERE expires_at < ?", (now_iso(),))


def _role_dict(row):
    d = dict(row)
    try:
        d["permissions"] = json.loads(d.get("permissions") or "[]")
    except Exception:
        d["permissions"] = []
    return d


def get_role_labels():
    with connect() as conn:
        return {r["code"]: r["label"] for r in conn.execute("SELECT code,label FROM system_roles WHERE status='启用'")}


def get_demo_users():
    with connect() as conn:
        rows = conn.execute("SELECT username,display_name,department,role_code FROM system_users WHERE status='启用' ORDER BY id").fetchall()
    out = {}
    for r in rows:
        if r["role_code"] not in out:
            out[r["role_code"]] = {"id": r["username"], "name": f"{r['display_name']} {r['username']}", "dept": r["department"]}
    return out


def resolve_session(token: str):
    if not token:
        return None
    with connect() as conn:
        row = conn.execute(
            """SELECT s.token,s.expires_at,u.id,u.username,u.display_name,u.department,u.email,u.phone,u.role_code,u.status,
                      r.label role_label,r.permissions,r.status role_status
               FROM auth_sessions s JOIN system_users u ON u.id=s.user_id JOIN system_roles r ON r.code=u.role_code
               WHERE s.token=?""",
            (token,),
        ).fetchone()
        if not row:
            return None
        if row["status"] != "启用" or row["role_status"] != "启用" or row["expires_at"] < now_iso():
            conn.execute("DELETE FROM auth_sessions WHERE token=?", (token,))
            return None
        conn.execute("UPDATE auth_sessions SET last_seen=? WHERE token=?", (now_iso(), token))
        d = dict(row)
        try:
            d["permissions"] = json.loads(d.get("permissions") or "[]")
        except Exception:
            d["permissions"] = []
        return d


def require_admin(x_role: Optional[str]):
    if x_role != "admin":
        raise BusinessError(403, "AUTH-4030", "仅系统管理员可以执行该操作")


class LoginPayload(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class ChangePasswordPayload(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8, max_length=128)


class UserPayload(BaseModel):
    username: str = Field(min_length=2, max_length=80)
    display_name: str = Field(min_length=1, max_length=80)
    department: str = ""
    email: str = ""
    phone: str = ""
    role_code: str
    status: str = "启用"
    password: Optional[str] = None


class RolePayload(BaseModel):
    code: str = Field(min_length=2, max_length=50, pattern=r"^[a-zA-Z0-9_.-]+$")
    label: str = Field(min_length=1, max_length=80)
    description: str = ""
    permissions: list[str] = []
    status: str = "启用"


@router.post("/api/auth/login")
def login(payload: LoginPayload, request: Request):
    username = payload.username.strip()
    with connect() as conn:
        row = conn.execute(
            """SELECT u.*,r.label role_label,r.permissions,r.status role_status FROM system_users u
               JOIN system_roles r ON r.code=u.role_code WHERE u.username=?""",
            (username,),
        ).fetchone()
        if not row or not _verify_password(payload.password, row["password_hash"]):
            raise BusinessError(401, "AUTH-4010", "账号或密码错误")
        if row["status"] != "启用":
            raise BusinessError(403, "AUTH-4030", "账号已停用，请联系系统管理员")
        if row["role_status"] != "启用":
            raise BusinessError(403, "AUTH-4030", "当前角色已停用，请联系系统管理员")
        token = secrets.token_urlsafe(36)
        now = datetime.now(timezone.utc).astimezone()
        expires = now + timedelta(hours=SESSION_HOURS)
        conn.execute(
            "INSERT INTO auth_sessions(token,user_id,created_at,expires_at,last_seen) VALUES(?,?,?,?,?)",
            (token, row["id"], now.isoformat(timespec="seconds"), expires.isoformat(timespec="seconds"), now.isoformat(timespec="seconds")),
        )
        conn.execute("UPDATE system_users SET last_login=?,updated_at=? WHERE id=?", (now_iso(), now_iso(), row["id"]))
        try:
            permissions = json.loads(row["permissions"] or "[]")
        except Exception:
            permissions = []
        return {"code": 0, "message": "登录成功", "data": {
            "token": token,
            "user": {
                "id": row["id"], "username": row["username"], "display_name": row["display_name"],
                "department": row["department"], "email": row["email"], "phone": row["phone"],
                "role_code": row["role_code"], "role_label": row["role_label"], "permissions": permissions,
            }
        }}


@router.get("/api/auth/me")
def me(x_session: Optional[str] = Header(None)):
    user = resolve_session(x_session or "")
    if not user:
        raise BusinessError(401, "AUTH-4010", "登录已失效，请重新登录")
    return {"code": 0, "data": {
        "id": user["id"], "username": user["username"], "display_name": user["display_name"],
        "department": user["department"], "email": user["email"], "phone": user["phone"],
        "role_code": user["role_code"], "role_label": user["role_label"], "permissions": user["permissions"],
    }}


@router.post("/api/auth/logout")
def logout(x_session: Optional[str] = Header(None)):
    if x_session:
        with connect() as conn:
            conn.execute("DELETE FROM auth_sessions WHERE token=?", (x_session,))
    return {"code": 0, "message": "已退出登录"}


@router.post("/api/auth/change-password")
def change_password(payload: ChangePasswordPayload, x_session: Optional[str] = Header(None)):
    user = resolve_session(x_session or "")
    if not user:
        raise BusinessError(401, "AUTH-4010", "登录已失效，请重新登录")
    with connect() as conn:
        row = conn.execute("SELECT password_hash FROM system_users WHERE id=?", (user["id"],)).fetchone()
        if not row or not _verify_password(payload.old_password, row["password_hash"]):
            raise BusinessError(400, "AUTH-4001", "原密码不正确")
        conn.execute("UPDATE system_users SET password_hash=?,updated_at=? WHERE id=?", (_password_hash(payload.new_password), now_iso(), user["id"]))
        conn.execute("DELETE FROM auth_sessions WHERE user_id=? AND token<>?", (user["id"], x_session))
    return {"code": 0, "message": "密码修改成功"}


@router.get("/api/system/permissions")
def permissions(x_role: Optional[str] = Header(None)):
    require_admin(x_role)
    return {"code": 0, "data": PERMISSION_CATALOG}


@router.get("/api/system/users")
def list_users(x_role: Optional[str] = Header(None)):
    require_admin(x_role)
    with connect() as conn:
        rows = conn.execute(
            """SELECT u.id,u.username,u.display_name,u.department,u.email,u.phone,u.role_code,u.status,u.last_login,u.created_at,
                      r.label role_label FROM system_users u JOIN system_roles r ON r.code=u.role_code ORDER BY u.id"""
        ).fetchall()
    return {"code": 0, "data": [dict(r) for r in rows]}


@router.post("/api/system/users")
def create_user(payload: UserPayload, x_role: Optional[str] = Header(None)):
    require_admin(x_role)
    password = payload.password or DEFAULT_DEMO_PASSWORD
    if len(password) < 8:
        raise BusinessError(400, "AUTH-4001", "初始密码至少8位")
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM system_roles WHERE code=? AND status='启用'", (payload.role_code,)).fetchone():
            raise BusinessError(400, "AUTH-4001", "角色不存在或已停用")
        try:
            cur = conn.execute(
                """INSERT INTO system_users(username,display_name,department,email,phone,role_code,password_hash,status,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (payload.username.strip(), payload.display_name.strip(), payload.department, payload.email, payload.phone,
                 payload.role_code, _password_hash(password), payload.status, now_iso(), now_iso()),
            )
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                raise BusinessError(409, "AUTH-4090", "账号已存在")
            raise
    return {"code": 0, "message": "用户创建成功", "data": {"id": cur.lastrowid}}


@router.put("/api/system/users/{user_id}")
def update_user(user_id: int, payload: UserPayload, x_role: Optional[str] = Header(None)):
    require_admin(x_role)
    with connect() as conn:
        old = conn.execute("SELECT * FROM system_users WHERE id=?", (user_id,)).fetchone()
        if not old:
            raise BusinessError(404, "REQ-4040", "用户不存在")
        if not conn.execute("SELECT 1 FROM system_roles WHERE code=?", (payload.role_code,)).fetchone():
            raise BusinessError(400, "AUTH-4001", "角色不存在")
        conn.execute(
            """UPDATE system_users SET username=?,display_name=?,department=?,email=?,phone=?,role_code=?,status=?,updated_at=? WHERE id=?""",
            (payload.username.strip(), payload.display_name.strip(), payload.department, payload.email, payload.phone,
             payload.role_code, payload.status, now_iso(), user_id),
        )
        if payload.password:
            if len(payload.password) < 8:
                raise BusinessError(400, "AUTH-4001", "密码至少8位")
            conn.execute("UPDATE system_users SET password_hash=? WHERE id=?", (_password_hash(payload.password), user_id))
        if payload.status != "启用":
            conn.execute("DELETE FROM auth_sessions WHERE user_id=?", (user_id,))
    return {"code": 0, "message": "用户信息已更新"}


@router.post("/api/system/users/{user_id}/reset-password")
def reset_password(user_id: int, x_role: Optional[str] = Header(None)):
    require_admin(x_role)
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM system_users WHERE id=?", (user_id,)).fetchone():
            raise BusinessError(404, "REQ-4040", "用户不存在")
        conn.execute("UPDATE system_users SET password_hash=?,updated_at=? WHERE id=?", (_password_hash(DEFAULT_DEMO_PASSWORD), now_iso(), user_id))
        conn.execute("DELETE FROM auth_sessions WHERE user_id=?", (user_id,))
    return {"code": 0, "message": f"密码已重置为 {DEFAULT_DEMO_PASSWORD}"}


@router.get("/api/system/roles")
def list_roles(x_role: Optional[str] = Header(None)):
    require_admin(x_role)
    with connect() as conn:
        rows = conn.execute("SELECT * FROM system_roles ORDER BY built_in DESC, code").fetchall()
        users = {r["role_code"]: r["c"] for r in conn.execute("SELECT role_code,COUNT(*) c FROM system_users GROUP BY role_code")}
    data = []
    for row in rows:
        d = _role_dict(row)
        d["user_count"] = users.get(d["code"], 0)
        data.append(d)
    return {"code": 0, "data": data}


@router.post("/api/system/roles")
def create_role(payload: RolePayload, x_role: Optional[str] = Header(None)):
    require_admin(x_role)
    invalid = [p for p in payload.permissions if p != "*" and p not in PERMISSION_CATALOG]
    if invalid:
        raise BusinessError(400, "AUTH-4001", f"存在无效权限：{', '.join(invalid)}")
    with connect() as conn:
        try:
            conn.execute(
                "INSERT INTO system_roles(code,label,description,permissions,status,built_in,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (payload.code, payload.label, payload.description, json.dumps(payload.permissions, ensure_ascii=False), payload.status, 0, now_iso(), now_iso()),
            )
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                raise BusinessError(409, "AUTH-4090", "角色编码已存在")
            raise
    return {"code": 0, "message": "角色创建成功"}


@router.put("/api/system/roles/{code}")
def update_role(code: str, payload: RolePayload, x_role: Optional[str] = Header(None)):
    require_admin(x_role)
    invalid = [p for p in payload.permissions if p != "*" and p not in PERMISSION_CATALOG]
    if invalid:
        raise BusinessError(400, "AUTH-4001", f"存在无效权限：{', '.join(invalid)}")
    with connect() as conn:
        old = conn.execute("SELECT * FROM system_roles WHERE code=?", (code,)).fetchone()
        if not old:
            raise BusinessError(404, "REQ-4040", "角色不存在")
        conn.execute(
            "UPDATE system_roles SET label=?,description=?,permissions=?,status=?,updated_at=? WHERE code=?",
            (payload.label, payload.description, json.dumps(payload.permissions, ensure_ascii=False), payload.status, now_iso(), code),
        )
        if payload.status != "启用":
            conn.execute("DELETE FROM auth_sessions WHERE user_id IN (SELECT id FROM system_users WHERE role_code=?)", (code,))
    return {"code": 0, "message": "角色权限已更新"}
