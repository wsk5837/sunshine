import asyncio

from fastapi.testclient import TestClient
from mcp import Client

from app import db
from app.db import init_db
from app.extended import init_extended_db
from app.main import app
from app.trm_mcp import init_trm_mcp_db, trm_mcp
from app.v4 import init_v4_db


TEST_TOKEN = "test-token-for-trm-mcp-1234567890"


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
    init_trm_mcp_db()

    async def scenario():
        async with Client(trm_mcp) as client:
            listed = await client.list_tools()
            names = {tool.name for tool in listed.tools}
            assert len(names) == 9
            assert {"trm_prepare_create_project", "trm_create_project"} <= names

            args = {
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

    asyncio.run(scenario())
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) c FROM projects WHERE name=?", ("MCP幂等联调项目",)).fetchone()["c"] == 1
        assert conn.execute("SELECT COUNT(*) c FROM mcp_tool_calls WHERE tool_name='trm_create_project'").fetchone()["c"] == 1
        assert conn.execute("SELECT COUNT(*) c FROM audit_logs WHERE action='MCP创建项目'").fetchone()["c"] == 1
