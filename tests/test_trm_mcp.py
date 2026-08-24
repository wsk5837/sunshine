import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from mcp import Client

from app import db
from app.auth import init_auth_db, issue_ai_delegation
from app.db import init_db
from app.extended import init_extended_db
from app.main import app
from app.poc import init_poc_db
from app.trm_mcp import init_trm_mcp_db, trm_mcp
from app.v4 import init_v4_db


TEST_TOKEN = "test-token-for-trm-mcp-1234567890"


def delegation_for(username: str) -> str:
    session_token = f"test-session-{username}-{uuid.uuid4()}"
    now = datetime.now(timezone.utc).astimezone()
    with db.connect() as conn:
        user = conn.execute("SELECT id FROM system_users WHERE username=?", (username,)).fetchone()
        assert user
        conn.execute(
            "INSERT INTO auth_sessions(token,user_id,created_at,expires_at,last_seen) VALUES(?,?,?,?,?)",
            (
                session_token,
                user["id"],
                now.isoformat(timespec="seconds"),
                (now + timedelta(hours=1)).isoformat(timespec="seconds"),
                now.isoformat(timespec="seconds"),
            ),
        )
    return issue_ai_delegation(session_token, source="test")


def test_mcp_http_requires_bearer_and_negotiates_protocol(monkeypatch):
    monkeypatch.setenv("TRM_MCP_API_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("TRM_MCP_ALLOWED_HOSTS", "testserver")
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "trm-test", "version": "1.0"},
        },
    }
    with TestClient(app) as client:
        denied = client.post("/mcp/", json=body, headers={"Accept": "application/json, text/event-stream"})
        assert denied.status_code == 401
        response = client.post(
            "/mcp/",
            json=body,
            headers={
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {TEST_TOKEN}",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["result"]["capabilities"]["tools"] == {"listChanged": False}


def test_mcp_prepare_create_and_idempotency(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "mcp-test.db")
    monkeypatch.setenv("TRM_MCP_API_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("TRM_MCP_WRITE_ENABLED", "true")
    init_db()
    init_extended_db()
    init_v4_db()
    init_poc_db()
    init_auth_db()
    init_trm_mcp_db()
    project_manager_token = delegation_for("wangwj")
    applicant_token = delegation_for("lili11-ghq")
    product_manager_token = delegation_for("zhaomin")

    async def scenario():
        async with Client(trm_mcp) as client:
            listed = await client.list_tools()
            names = {tool.name for tool in listed.tools}
            assert len(names) == 13
            assert {"trm_prepare_create_project", "trm_create_project"} <= names
            assert {"trm_prepare_update_work_plan", "trm_update_work_plan", "trm_prepare_log_work_hours", "trm_log_work_hours"} <= names
            for tool in listed.tools:
                schema = tool.model_dump(by_alias=True)["inputSchema"]
                assert "delegation_token" in schema.get("required", [])

            args = {
                "delegation_token": project_manager_token,
                "name": "MCP幂等联调项目",
                "manager": "王卫嘉",
                "department": "数字化管理部",
                "budget_id": 1,
                "total_budget": 120000,
                "status": "规划中",
                "progress": 0,
                "start_date": "2026-09-01",
                "end_date": "2026-12-31",
                "description": "验证企业智能体通过MCP受控创建项目。",
            }
            prepared = await client.call_tool("trm_prepare_create_project", args)
            assert not prepared.is_error
            preview = prepared.structured_content
            assert preview["will_write"] is False

            create_args = {
                **args,
                "confirmation_token": preview["confirmation_token"],
                "idempotency_key": "test-project-create-001",
            }
            created = await client.call_tool("trm_create_project", create_args)
            assert not created.is_error
            first = created.structured_content
            assert first["created"] is True
            assert first["project_no"].startswith("PRJ-")
            assert first["idempotent_replay"] is False

            replayed = await client.call_tool("trm_create_project", create_args)
            assert not replayed.is_error
            assert replayed.structured_content["id"] == first["id"]
            assert replayed.structured_content["idempotent_replay"] is True

            applicant_demand = await client.call_tool("trm_prepare_create_demand", {
                "delegation_token": applicant_token,
                "title": "申请人AI需求权限测试",
                "description": "验证申请人可通过AI创建需求草稿，且身份由服务端绑定。",
                "demand_type": "系统功能新增",
                "budget_sources": [],
                "priority": "中",
                "budget_amount": 0,
            })
            assert not applicant_demand.is_error
            assert applicant_demand.structured_content["preview"]["applicant_code"] == "lili11-ghq"

            applicant_project = await client.call_tool("trm_prepare_create_project", {
                **args,
                "delegation_token": applicant_token,
                "name": "申请人同步立项权限测试",
            })
            assert not applicant_project.is_error
            assert applicant_project.structured_content["preview"]["name"] == "申请人同步立项权限测试"
            applicant_created = await client.call_tool("trm_create_project", {
                **args,
                "delegation_token": applicant_token,
                "name": "申请人同步立项权限测试",
                "confirmation_token": applicant_project.structured_content["confirmation_token"],
                "idempotency_key": "applicant-project-create-001",
            })
            assert not applicant_created.is_error
            assert applicant_created.structured_content["created"] is True

            # 工时与页面能力共用实时权限，并通过预览、确认、幂等写入形成闭环。
            # prepare 本身不创建，先使用前面的需求参数完成草稿创建。
            demand_created = await client.call_tool("trm_create_demand", {
                "delegation_token": applicant_token,
                "title": "申请人AI需求权限测试",
                "description": "验证申请人可通过AI创建需求草稿，且身份由服务端绑定。",
                "demand_type": "系统功能新增",
                "budget_sources": [],
                "priority": "中",
                "budget_amount": 0,
                "confirmation_token": applicant_demand.structured_content["confirmation_token"],
                "idempotency_key": "applicant-demand-create-001",
            })
            assert not demand_created.is_error
            demand_id = demand_created.structured_content["id"]
            plan_args = {
                "delegation_token": product_manager_token,
                "identifier": str(demand_id),
                "estimated_hours": 40,
                "expected_completion_date": "2099-12-31",
                "note": "AI协助评估",
            }
            plan_preview = await client.call_tool("trm_prepare_update_work_plan", plan_args)
            assert not plan_preview.is_error
            plan_saved = await client.call_tool("trm_update_work_plan", {
                **plan_args,
                "confirmation_token": plan_preview.structured_content["confirmation_token"],
                "idempotency_key": "work-plan-update-001",
            })
            assert not plan_saved.is_error
            assert plan_saved.structured_content["estimated_hours"] == 40

            today = datetime.now().strftime("%Y-%m-%d")
            log_args = {
                "delegation_token": product_manager_token,
                "identifier": str(demand_id),
                "work_date": today,
                "hours": 6,
                "worker": "赵敏",
                "task_name": "需求评审",
                "description": "评审并拆分功能点",
                "replace_external": False,
            }
            log_preview = await client.call_tool("trm_prepare_log_work_hours", log_args)
            assert not log_preview.is_error
            log_saved = await client.call_tool("trm_log_work_hours", {
                **log_args,
                "confirmation_token": log_preview.structured_content["confirmation_token"],
                "idempotency_key": "work-log-create-001",
            })
            assert not log_saved.is_error
            assert log_saved.structured_content["actual_hours_total"] == 6
            log_replay = await client.call_tool("trm_log_work_hours", {
                **log_args,
                "confirmation_token": log_preview.structured_content["confirmation_token"],
                "idempotency_key": "work-log-create-001",
            })
            assert log_replay.structured_content["idempotent_replay"] is True

            applicant_work = await client.call_tool("trm_prepare_update_work_plan", {
                **plan_args,
                "delegation_token": applicant_token,
            })
            assert applicant_work.is_error
            assert "demand.evaluate" in str(applicant_work.content)

            # 申请人页面的“新建立项”权限一旦撤销，AI立即同步失去创建项目能力。
            with db.connect() as conn:
                role = conn.execute("SELECT permissions FROM system_roles WHERE code='applicant'").fetchone()
                permissions = json.loads(role["permissions"])
                permissions.remove("initiative.create")
                conn.execute(
                    "UPDATE system_roles SET permissions=? WHERE code='applicant'",
                    (json.dumps(permissions, ensure_ascii=False),),
                )
            applicant_revoked = await client.call_tool("trm_prepare_create_project", {
                **args,
                "delegation_token": applicant_token,
                "name": "申请人撤权后不应创建的项目",
            })
            assert applicant_revoked.is_error
            assert "initiative.create" in str(applicant_revoked.content)

            # 后台撤销角色权限后，已签发的委托令牌也要立即失去该能力。
            with db.connect() as conn:
                role = conn.execute("SELECT permissions FROM system_roles WHERE code='project_manager'").fetchone()
                permissions = json.loads(role["permissions"])
                permissions.remove("project")
                conn.execute(
                    "UPDATE system_roles SET permissions=? WHERE code='project_manager'",
                    (json.dumps(permissions, ensure_ascii=False),),
                )
            revoked = await client.call_tool("trm_prepare_create_project", {
                **args,
                "name": "实时撤权测试项目",
            })
            assert revoked.is_error
            assert "initiative.create" in str(revoked.content)
            assert "project" in str(revoked.content)

    asyncio.run(scenario())
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) c FROM projects WHERE name=?", ("MCP幂等联调项目",)).fetchone()["c"] == 1
        assert conn.execute("SELECT COUNT(*) c FROM mcp_tool_calls WHERE tool_name='trm_create_project'").fetchone()["c"] == 3
        assert conn.execute("SELECT COUNT(*) c FROM audit_logs WHERE action='MCP创建项目'").fetchone()["c"] == 2
        audit_row = conn.execute(
            "SELECT actor,role,user_id,service_actor,required_permission FROM mcp_tool_calls WHERE tool_name='trm_create_project' ORDER BY id LIMIT 1"
        ).fetchone()
        assert audit_row["actor"] == "wangwj"
        assert audit_row["role"] == "project_manager"
        assert audit_row["user_id"]
        assert audit_row["service_actor"] == "gaios-mcp-agent"
        assert audit_row["required_permission"] == "initiative.create|project"
