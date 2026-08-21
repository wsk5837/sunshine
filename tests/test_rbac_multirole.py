import json

import pytest
from fastapi.testclient import TestClient

from app import db
from app.auth import validate_ai_delegation
from app.main import app


def test_multi_role_permissions_are_real_and_ai_mcp_stay_in_sync(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "rbac-multirole.db")
    monkeypatch.setenv("TRM_MCP_API_TOKEN", "rbac-mcp-token-with-at-least-24-characters")
    captured_contexts = []

    async def fake_run_agent_message(**kwargs):
        captured_contexts.append(json.loads(kwargs["context"]))
        return {
            "answer": "已按当前账号权限回答。",
            "session_id": "rbac-session",
            "provider": "Gazellio G.AIOS",
            "agent_id": "rbac-test-agent",
        }

    monkeypatch.setattr("app.main.run_agent_message", fake_run_agent_message)

    with TestClient(app) as client:
        admin_login = client.post("/api/auth/login", json={"username": "admin", "password": "Admin@123"})
        assert admin_login.status_code == 200
        admin_headers = {"X-Session": admin_login.json()["data"]["token"]}

        demand_role = {
            "code": "rbac_demand_reader",
            "label": "RBAC需求查询",
            "description": "用于验证实时撤权",
            "permissions": ["dashboard", "demand.list", "ai"],
            "status": "启用",
        }
        budget_role = {
            "code": "rbac_budget_reader",
            "label": "RBAC预算查询",
            "description": "用于验证多角色并集",
            "permissions": ["budget"],
            "status": "启用",
        }
        assert client.post("/api/system/roles", headers=admin_headers, json=demand_role).status_code == 200
        assert client.post("/api/system/roles", headers=admin_headers, json=budget_role).status_code == 200

        user_payload = {
            "username": "rbac_multi_user",
            "display_name": "RBAC多角色用户",
            "department": "数字化管理部",
            "email": "rbac@example.test",
            "phone": "",
            "role_codes": ["rbac_demand_reader", "rbac_budget_reader"],
            "status": "启用",
            "password": "Rbac@123456",
        }
        created = client.post("/api/system/users", headers=admin_headers, json=user_payload)
        assert created.status_code == 200, created.text

        login = client.post("/api/auth/login", json={"username": "rbac_multi_user", "password": "Rbac@123456"})
        assert login.status_code == 200, login.text
        token = login.json()["data"]["token"]
        headers = {"X-Session": token}

        me = client.get("/api/auth/me", headers=headers)
        assert me.status_code == 200
        data = me.json()["data"]
        assert data["role_codes"] == ["rbac_demand_reader", "rbac_budget_reader"]
        assert {"dashboard", "demand.list", "ai", "budget"} <= set(data["permissions"])

        # 后端 API 真实强制权限：有权限可访问，没有权限直接 403，不依赖菜单隐藏。
        assert client.get("/api/demands", headers=headers).status_code == 200
        assert client.get("/api/budget-ledger", headers=headers).status_code == 200
        assert client.get("/api/system/users", headers=headers).status_code == 403

        first_ai = client.post("/api/ai/chat", headers=headers, json={"question": "需求和预算情况？", "source": "rbac-test"})
        assert first_ai.status_code == 200, first_ai.text
        first_context = captured_contexts[-1]
        assert {"query.demand", "query.budget"} <= set(first_context["effective_ai_capabilities"])
        delegation = first_context["mcp_authorization"]["delegation_token"]
        assert validate_ai_delegation(delegation, "query.demand").username == "rbac_multi_user"
        assert validate_ai_delegation(delegation, "query.budget").username == "rbac_multi_user"

        # 管理员撤掉角色的需求查询权限：不重新登录，旧会话和旧AI委托立即失去该权限。
        demand_role["permissions"] = ["dashboard", "ai"]
        changed = client.put(
            "/api/system/roles/rbac_demand_reader",
            headers=admin_headers,
            json=demand_role,
        )
        assert changed.status_code == 200, changed.text

        denied = client.get("/api/demands", headers=headers)
        assert denied.status_code == 403
        assert denied.json()["code"] == "AUTH-4030"
        assert client.get("/api/budget-ledger", headers=headers).status_code == 200
        live_me = client.get("/api/auth/me", headers=headers).json()["data"]
        assert "demand.list" not in live_me["permissions"]
        assert "budget" in live_me["permissions"]

        with pytest.raises(ValueError, match="demand.list"):
            validate_ai_delegation(delegation, "query.demand")
        assert validate_ai_delegation(delegation, "query.budget").username == "rbac_multi_user"

        second_ai = client.post("/api/ai/chat", headers=headers, json={"question": "预算情况？", "source": "rbac-test"})
        assert second_ai.status_code == 200, second_ai.text
        second_context = captured_contexts[-1]
        assert "query.demand" not in second_context["effective_ai_capabilities"]
        assert "query.budget" in second_context["effective_ai_capabilities"]
