import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import uuid
from dataclasses import dataclass
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

AI_CAPABILITY_RULES: dict[str, tuple[str, ...]] = {
    "query.budget": ("budget",),
    "query.demand": ("demand.list",),
    "query.project": ("project360", "project"),
    "create.demand": ("demand.create",),
    "create.project": ("initiative.create", "project"),
}


def has_permission(permissions: list[str] | tuple[str, ...] | set[str], code: str) -> bool:
    return "*" in permissions or code in permissions


def has_any_permission(permissions: list[str] | tuple[str, ...] | set[str], codes: tuple[str, ...]) -> bool:
    return "*" in permissions or any(code in permissions for code in codes)


def has_ai_capability(permissions: list[str] | tuple[str, ...] | set[str], capability: str) -> bool:
    required = AI_CAPABILITY_RULES.get(capability, ())
    return bool(required) and has_any_permission(permissions, required)


def get_ai_capabilities(permissions: list[str] | tuple[str, ...] | set[str]) -> list[str]:
    return [capability for capability in AI_CAPABILITY_RULES if has_ai_capability(permissions, capability)]


@dataclass(frozen=True)
class AIPrincipal:
    user_id: int
    username: str
    display_name: str
    department: str
    role_code: str
    role_label: str
    role_codes: tuple[str, ...]
    role_labels: tuple[str, ...]
    permissions: tuple[str, ...]
    delegation_id: str


def _delegation_secret() -> bytes:
    raw = (
        os.getenv("TRM_AI_DELEGATION_SECRET")
        or os.getenv("TRM_MCP_CONFIRMATION_SECRET")
        or os.getenv("TRM_MCP_API_TOKEN")
        or ""
    )
    if len(raw) < 24:
        raise ValueError("AI委托令牌密钥未配置（TRM_AI_DELEGATION_SECRET / TRM_MCP_API_TOKEN 至少24字符）")
    return raw.encode("utf-8")


def _delegation_ttl() -> int:
    try:
        value = int(os.getenv("TRM_AI_DELEGATION_TTL_SECONDS", "900"))
    except ValueError:
        value = 900
    return max(60, min(value, 1800))


def _sign_delegation(body: dict) -> str:
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    signature = hmac.new(_delegation_secret(), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


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
            CREATE TABLE IF NOT EXISTS system_user_roles (
                user_id INTEGER NOT NULL,
                role_code TEXT NOT NULL,
                is_primary INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                PRIMARY KEY(user_id, role_code),
                FOREIGN KEY(user_id) REFERENCES system_users(id) ON DELETE CASCADE,
                FOREIGN KEY(role_code) REFERENCES system_roles(code) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_system_user_roles_role ON system_user_roles(role_code, user_id);
            CREATE TABLE IF NOT EXISTS ai_delegations (
                id TEXT PRIMARY KEY,
                auth_session_token TEXT NOT NULL,
                source TEXT DEFAULT '',
                project_id INTEGER,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                FOREIGN KEY(auth_session_token) REFERENCES auth_sessions(token) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS auth_permission_migrations (
                name TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ai_delegations_session ON ai_delegations(auth_session_token);
            CREATE INDEX IF NOT EXISTS idx_ai_delegations_expires ON ai_delegations(expires_at);
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
        conn.execute(
            """INSERT OR IGNORE INTO system_user_roles(user_id,role_code,is_primary,created_at)
               SELECT id,role_code,1,? FROM system_users""",
            (ts,),
        )
        # V4.9.1: AI no longer has a second permission system. Remove the
        # previously introduced ai.* grants; MCP derives capabilities directly
        # from the same business permissions used by pages and API actions.
        migration_name = "ai_permissions_unified_v2"
        if not conn.execute("SELECT 1 FROM auth_permission_migrations WHERE name=?", (migration_name,)).fetchone():
            for row in conn.execute("SELECT code,permissions FROM system_roles").fetchall():
                try:
                    current = json.loads(row["permissions"] or "[]")
                except Exception:
                    current = []
                cleaned = [code for code in current if not str(code).startswith("ai.")]
                if cleaned != current:
                    conn.execute(
                        "UPDATE system_roles SET permissions=?,updated_at=? WHERE code=?",
                        (json.dumps(cleaned, ensure_ascii=False), ts, row["code"]),
                    )
            conn.execute(
                "INSERT INTO auth_permission_migrations(name,applied_at) VALUES(?,?)",
                (migration_name, ts),
            )
        # remove expired sessions
        conn.execute("DELETE FROM auth_sessions WHERE expires_at < ?", (now_iso(),))
        conn.execute("DELETE FROM ai_delegations WHERE expires_at < ?", (now_iso(),))
        conn.execute("PRAGMA optimize")


def _role_dict(row):
    d = dict(row)
    try:
        d["permissions"] = json.loads(d.get("permissions") or "[]")
    except Exception:
        d["permissions"] = []
    return d


def _parse_permissions(raw: str | None) -> list[str]:
    try:
        value = json.loads(raw or "[]")
        return [str(item) for item in value] if isinstance(value, list) else []
    except Exception:
        return []


def _load_user_roles(conn, user_id: int, fallback_role: str = "") -> dict:
    rows = conn.execute(
        """SELECT ur.role_code,ur.is_primary,r.label,r.permissions
           FROM system_user_roles ur
           JOIN system_roles r ON r.code=ur.role_code
           WHERE ur.user_id=? AND r.status='启用'
           ORDER BY ur.is_primary DESC, ur.rowid""",
        (user_id,),
    ).fetchall()
    if not rows and fallback_role:
        fallback = conn.execute(
            "SELECT code role_code,1 is_primary,label,permissions FROM system_roles WHERE code=? AND status='启用'",
            (fallback_role,),
        ).fetchone()
        rows = [fallback] if fallback else []
    role_codes = [row["role_code"] for row in rows]
    role_labels = [row["label"] for row in rows]
    merged: list[str] = []
    for row in rows:
        for permission in _parse_permissions(row["permissions"]):
            if permission == "*":
                merged = ["*"]
                break
            if permission not in merged:
                merged.append(permission)
        if merged == ["*"]:
            break
    return {
        "role_codes": role_codes,
        "role_labels": role_labels,
        "role_code": role_codes[0] if role_codes else "",
        "role_label": role_labels[0] if role_labels else "",
        "permissions": merged,
    }


def request_role_codes(request: Request) -> set[str]:
    user = getattr(request.state, "auth_user", None) or {}
    if user:
        return set(user.get("role_codes") or ([user.get("role_code")] if user.get("role_code") else []))
    # 仅供本地 TestClient 的旧测试用例兼容；真实 HTTP 请求会在中间件中
    # 被强制登录，且 X-Role/X-Roles 会被服务端会话数据覆盖。
    raw = request.headers.get("X-Roles") or request.headers.get("X-Role") or ""
    return {code.strip() for code in raw.split(",") if code.strip()}


def request_has_role(request: Request, *role_codes: str) -> bool:
    roles = request_role_codes(request)
    return "admin" in roles or bool(roles.intersection(role_codes))


def permissions_for_api(method: str, path: str) -> tuple[str, ...]:
    """Return any-of permissions required by a protected API route.

    Authentication is handled separately. An empty tuple means that every
    authenticated user may call the route.
    """
    method = method.upper()
    if path.startswith("/api/auth/") or path in {"/api/meta", "/api/v4/meta", "/api/notifications"}:
        return ()
    if re.fullmatch(r"/api/notifications/\d+/read", path):
        return ()
    if path == "/api/mcp/status":
        return ("system.integrations",)
    if path.startswith("/api/system/users"):
        return ("system.users",)
    if path.startswith("/api/system/roles") or path == "/api/system/permissions":
        return ("system.roles",)
    if path.startswith("/api/integrations") or path.startswith("/api/poc/settings") or path.startswith("/api/poc/jobs"):
        return ("system.integrations",)
    if path == "/api/tapd/test-connection":
        return ("system.integrations",)
    if path == "/api/audit":
        return ("system.audit",)
    if path.startswith("/api/ai/"):
        return ("ai",)
    if path in {"/api/dashboard", "/api/platform-dashboard"} or path.startswith("/api/exports/"):
        return ("dashboard",)
    if path.startswith("/api/initiative-approvals"):
        return ("initiative.approve",)
    if path.startswith("/api/initiatives"):
        if path.endswith("/approve"):
            return ("initiative.approve",)
        if path.endswith("/convert-project"):
            return ("project",)
        return ("initiative.list",) if method == "GET" else ("initiative.create",)
    if path.startswith("/api/approvals/"):
        return ("demand.approve",)
    if path.startswith("/api/attachments/"):
        return ("demand.list",) if method == "GET" else ("demand.create",)
    if path.startswith("/api/function-point"):
        return ("function_points", "demand.evaluate")
    if path.startswith("/api/demands"):
        if path.endswith("/approve") or path.endswith("/oa-tasks"):
            return ("demand.approve",)
        if "/function-points" in path:
            return ("function_points", "demand.evaluate")
        if path.endswith("/allocations"):
            return ("demand.evaluate",)
        if path.endswith("/budget-check"):
            return ("budget", "demand.evaluate")
        if "/tapd/" in path:
            return ("tapd",)
        if method == "GET":
            return ("demand.list",)
        return ("demand.create",)
    if path.startswith("/api/oa/tasks"):
        return ("demand.approve", "demand.list")
    if path.startswith("/api/tapd"):
        return ("tapd",)
    if path.startswith("/api/project360"):
        return ("project360", "project")
    if path.startswith("/api/projects") or path.startswith("/api/project-") or path.startswith("/api/milestones") or path.startswith("/api/deliverables"):
        return ("project360", "project") if method == "GET" else ("project",)
    if path.startswith("/api/budget") or path.startswith("/api/budgets"):
        return ("budget",)
    if path.startswith("/api/business-values"):
        return ("value",)
    if path.startswith("/api/settlement"):
        return ("settlement",)
    if path.startswith("/api/indicators"):
        return ("indicator",)
    if path.startswith("/api/contracts") or path.startswith("/api/contract-") or path.startswith("/api/payment-plans"):
        return ("contract",)
    return ()


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
            """SELECT s.token,s.expires_at,u.id,u.username,u.display_name,u.department,u.email,u.phone,u.role_code,u.status
               FROM auth_sessions s JOIN system_users u ON u.id=s.user_id
               WHERE s.token=?""",
            (token,),
        ).fetchone()
        if not row:
            return None
        role_context = _load_user_roles(conn, int(row["id"]), row["role_code"])
        if row["status"] != "启用" or not role_context["role_codes"] or row["expires_at"] < now_iso():
            conn.execute("DELETE FROM auth_sessions WHERE token=?", (token,))
            return None
        conn.execute("UPDATE auth_sessions SET last_seen=? WHERE token=?", (now_iso(), token))
        d = dict(row)
        d.update(role_context)
        return d


def issue_ai_delegation(session_token: str, source: str = "", project_id: Optional[int] = None) -> str:
    """Issue an opaque, short-lived capability bound to the real login session.

    The token contains no role or permission claims. MCP always reloads the
    current user and role from the database, so role edits and account/session
    revocation take effect immediately.
    """
    user = resolve_session(session_token)
    if not user:
        raise BusinessError(401, "AUTH-4010", "登录已失效，无法授权AI调用业务工具")
    permissions = user.get("permissions") or []
    if not has_permission(permissions, "ai"):
        raise BusinessError(403, "AUTH-4030", "当前角色未授权使用AI助手")
    now = datetime.now(timezone.utc).astimezone()
    expires = now + timedelta(seconds=_delegation_ttl())
    delegation_id = str(uuid.uuid4())
    with connect() as conn:
        conn.execute(
            """INSERT INTO ai_delegations(id,auth_session_token,source,project_id,created_at,expires_at)
               VALUES(?,?,?,?,?,?)""",
            (
                delegation_id,
                session_token,
                (source or "")[:100],
                project_id,
                now.isoformat(timespec="seconds"),
                expires.isoformat(timespec="seconds"),
            ),
        )
    return _sign_delegation({"jti": delegation_id, "exp": int(expires.timestamp()), "v": 1})


def validate_ai_delegation(token: str, required_capability: str) -> AIPrincipal:
    """Validate one MCP call against live backend role permissions."""
    try:
        encoded, signature = (token or "").split(".", 1)
        expected = hmac.new(_delegation_secret(), encoded.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        padding = "=" * (-len(encoded) % 4)
        body = json.loads(base64.urlsafe_b64decode(encoded + padding).decode("utf-8"))
        if body.get("v") != 1 or int(body.get("exp") or 0) < int(time.time()):
            raise ValueError
        delegation_id = str(body["jti"])
    except Exception as exc:
        raise ValueError("当前登录用户的AI委托令牌无效或已过期，请在TRM内重新发起对话") from exc

    with connect() as conn:
        row = conn.execute(
            """SELECT d.id delegation_id,d.expires_at delegation_expires,
                      s.expires_at session_expires,u.id user_id,u.username,u.display_name,u.department,
                      u.status user_status,u.role_code
               FROM ai_delegations d
               JOIN auth_sessions s ON s.token=d.auth_session_token
               JOIN system_users u ON u.id=s.user_id
               WHERE d.id=?""",
            (delegation_id,),
        ).fetchone()
        if not row:
            raise ValueError("AI委托令牌已撤销，请重新登录或重新发起对话")
        role_context = _load_user_roles(conn, int(row["user_id"]), row["role_code"])
        if (row["user_status"] != "启用" or not role_context["role_codes"]
                or row["delegation_expires"] < now_iso() or row["session_expires"] < now_iso()):
            conn.execute("DELETE FROM ai_delegations WHERE id=?", (delegation_id,))
            raise ValueError("用户会话、角色或AI委托已失效")
        permissions = tuple(role_context["permissions"])

    if not has_permission(permissions, "ai"):
        raise ValueError("当前角色未授权使用AI助手")
    required_permissions = AI_CAPABILITY_RULES.get(required_capability)
    if not required_permissions:
        raise ValueError(f"AI能力映射未配置：{required_capability}")
    if not has_any_permission(permissions, required_permissions):
        labels = " / ".join(PERMISSION_CATALOG.get(code, code) for code in required_permissions)
        codes = " | ".join(required_permissions)
        raise ValueError(f"当前角色缺少对应业务权限：{labels}（{codes}）")
    return AIPrincipal(
        user_id=int(row["user_id"]),
        username=row["username"],
        display_name=row["display_name"],
        department=row["department"] or "",
        role_code=role_context["role_code"],
        role_label=role_context["role_label"],
        role_codes=tuple(role_context["role_codes"]),
        role_labels=tuple(role_context["role_labels"]),
        permissions=permissions,
        delegation_id=delegation_id,
    )


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
    role_code: Optional[str] = None
    role_codes: list[str] = Field(default_factory=list, max_length=20)
    status: str = "启用"
    password: Optional[str] = None


class RolePayload(BaseModel):
    code: str = Field(min_length=2, max_length=50, pattern=r"^[a-zA-Z0-9_.-]+$")
    label: str = Field(min_length=1, max_length=80)
    description: str = ""
    permissions: list[str] = []
    status: str = "启用"


def _selected_role_codes(payload: UserPayload) -> list[str]:
    raw = payload.role_codes or ([payload.role_code] if payload.role_code else [])
    roles = list(dict.fromkeys(str(code).strip() for code in raw if str(code).strip()))
    if not roles:
        raise BusinessError(400, "AUTH-4001", "用户至少需要分配一个角色")
    return roles


def _assert_role_codes(conn, role_codes: list[str]) -> None:
    placeholders = ",".join("?" for _ in role_codes)
    active = {
        row["code"] for row in conn.execute(
            f"SELECT code FROM system_roles WHERE status='启用' AND code IN ({placeholders})",
            role_codes,
        )
    }
    invalid = [code for code in role_codes if code not in active]
    if invalid:
        raise BusinessError(400, "AUTH-4001", f"角色不存在或已停用：{', '.join(invalid)}")


def _replace_user_roles(conn, user_id: int, role_codes: list[str], created_at: str) -> None:
    _assert_role_codes(conn, role_codes)
    conn.execute("DELETE FROM system_user_roles WHERE user_id=?", (user_id,))
    for index, code in enumerate(role_codes):
        conn.execute(
            "INSERT INTO system_user_roles(user_id,role_code,is_primary,created_at) VALUES(?,?,?,?)",
            (user_id, code, 1 if index == 0 else 0, created_at),
        )


@router.post("/api/auth/login")
def login(payload: LoginPayload, request: Request):
    username = payload.username.strip()
    with connect() as conn:
        row = conn.execute("SELECT * FROM system_users WHERE username=?", (username,)).fetchone()
        if not row or not _verify_password(payload.password, row["password_hash"]):
            raise BusinessError(401, "AUTH-4010", "账号或密码错误")
        if row["status"] != "启用":
            raise BusinessError(403, "AUTH-4030", "账号已停用，请联系系统管理员")
        role_context = _load_user_roles(conn, int(row["id"]), row["role_code"])
        if not role_context["role_codes"]:
            raise BusinessError(403, "AUTH-4030", "当前账号没有可用角色，请联系系统管理员")
        token = secrets.token_urlsafe(36)
        now = datetime.now(timezone.utc).astimezone()
        expires = now + timedelta(hours=SESSION_HOURS)
        conn.execute(
            "INSERT INTO auth_sessions(token,user_id,created_at,expires_at,last_seen) VALUES(?,?,?,?,?)",
            (token, row["id"], now.isoformat(timespec="seconds"), expires.isoformat(timespec="seconds"), now.isoformat(timespec="seconds")),
        )
        conn.execute("UPDATE system_users SET last_login=?,updated_at=? WHERE id=?", (now_iso(), now_iso(), row["id"]))
        return {"code": 0, "message": "登录成功", "data": {
            "token": token,
            "user": {
                "id": row["id"], "username": row["username"], "display_name": row["display_name"],
                "department": row["department"], "email": row["email"], "phone": row["phone"],
                "role_code": role_context["role_code"], "role_label": role_context["role_label"],
                "role_codes": role_context["role_codes"], "role_labels": role_context["role_labels"],
                "permissions": role_context["permissions"],
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
        "role_code": user["role_code"], "role_label": user["role_label"],
        "role_codes": user["role_codes"], "role_labels": user["role_labels"],
        "permissions": user["permissions"],
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
    return {"code": 0, "data": PERMISSION_CATALOG}


@router.get("/api/system/users")
def list_users(x_role: Optional[str] = Header(None)):
    with connect() as conn:
        rows = conn.execute(
            """SELECT u.id,u.username,u.display_name,u.department,u.email,u.phone,u.role_code,u.status,u.last_login,u.created_at
               FROM system_users u ORDER BY u.id"""
        ).fetchall()
        data = []
        for row in rows:
            item = dict(row)
            item.update(_load_user_roles(conn, int(row["id"]), row["role_code"]))
            data.append(item)
    return {"code": 0, "data": data}


@router.post("/api/system/users")
def create_user(payload: UserPayload, x_role: Optional[str] = Header(None)):
    role_codes = _selected_role_codes(payload)
    password = payload.password or DEFAULT_DEMO_PASSWORD
    if len(password) < 8:
        raise BusinessError(400, "AUTH-4001", "初始密码至少8位")
    with connect() as conn:
        _assert_role_codes(conn, role_codes)
        try:
            cur = conn.execute(
                """INSERT INTO system_users(username,display_name,department,email,phone,role_code,password_hash,status,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (payload.username.strip(), payload.display_name.strip(), payload.department, payload.email, payload.phone,
                 role_codes[0], _password_hash(password), payload.status, now_iso(), now_iso()),
            )
            _replace_user_roles(conn, int(cur.lastrowid), role_codes, now_iso())
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                raise BusinessError(409, "AUTH-4090", "账号已存在")
            raise
    return {"code": 0, "message": "用户创建成功", "data": {"id": cur.lastrowid}}


@router.put("/api/system/users/{user_id}")
def update_user(user_id: int, payload: UserPayload, x_role: Optional[str] = Header(None)):
    role_codes = _selected_role_codes(payload)
    with connect() as conn:
        _assert_role_codes(conn, role_codes)
        old = conn.execute("SELECT * FROM system_users WHERE id=?", (user_id,)).fetchone()
        if not old:
            raise BusinessError(404, "REQ-4040", "用户不存在")
        conn.execute(
            """UPDATE system_users SET username=?,display_name=?,department=?,email=?,phone=?,role_code=?,status=?,updated_at=? WHERE id=?""",
            (payload.username.strip(), payload.display_name.strip(), payload.department, payload.email, payload.phone,
             role_codes[0], payload.status, now_iso(), user_id),
        )
        _replace_user_roles(conn, user_id, role_codes, now_iso())
        if payload.password:
            if len(payload.password) < 8:
                raise BusinessError(400, "AUTH-4001", "密码至少8位")
            conn.execute("UPDATE system_users SET password_hash=? WHERE id=?", (_password_hash(payload.password), user_id))
        if payload.status != "启用":
            conn.execute("DELETE FROM auth_sessions WHERE user_id=?", (user_id,))
    return {"code": 0, "message": "用户信息已更新"}


@router.post("/api/system/users/{user_id}/reset-password")
def reset_password(user_id: int, x_role: Optional[str] = Header(None)):
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM system_users WHERE id=?", (user_id,)).fetchone():
            raise BusinessError(404, "REQ-4040", "用户不存在")
        conn.execute("UPDATE system_users SET password_hash=?,updated_at=? WHERE id=?", (_password_hash(DEFAULT_DEMO_PASSWORD), now_iso(), user_id))
        conn.execute("DELETE FROM auth_sessions WHERE user_id=?", (user_id,))
    return {"code": 0, "message": f"密码已重置为 {DEFAULT_DEMO_PASSWORD}"}


@router.get("/api/system/roles")
def list_roles(x_role: Optional[str] = Header(None)):
    with connect() as conn:
        rows = conn.execute("SELECT * FROM system_roles ORDER BY built_in DESC, code").fetchall()
        users = {r["role_code"]: r["c"] for r in conn.execute("SELECT role_code,COUNT(*) c FROM system_user_roles GROUP BY role_code")}
    data = []
    for row in rows:
        d = _role_dict(row)
        d["user_count"] = users.get(d["code"], 0)
        data.append(d)
    return {"code": 0, "data": data}


@router.post("/api/system/roles")
def create_role(payload: RolePayload, x_role: Optional[str] = Header(None)):
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
        # 不需要批量删除会话：resolve_session 每次都会重新合并“当前启用角色”。
        # 多角色用户停用其中一个角色后仍可使用其他角色；若已无任何启用角色，
        # resolve_session 会立即撤销该用户会话。
    return {"code": 0, "message": "角色权限已更新"}
