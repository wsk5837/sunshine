import json

from fastapi.testclient import TestClient

from app.ai_gateway import _event_text
from app.main import app


def test_runtime_parser_hides_model_thoughts():
    event = {
        "content": {
            "role": "model",
            "parts": [
                {"text": "internal reasoning", "thought": True},
                {"text": "给用户的最终答案"},
            ],
        }
    }
    assert _event_text(event) == "给用户的最终答案"


def test_public_ai_config_contains_no_admin_credentials():
    with TestClient(app) as client:
        response = client.get("/api/ai/config")
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["provider"] == "Gazellio G.AIOS"
        assert data["base_url"].startswith("https://")
        assert data["agent_id"]
        serialized = str(data).lower()
        assert "password" not in serialized
        assert "admin_token" not in serialized


def test_ai_chat_proxies_session_project_context_and_live_permissions(monkeypatch):
    captured = {}
    monkeypatch.setenv("TRM_MCP_API_TOKEN", "test-token-for-ai-delegation-123456789")
    monkeypatch.setenv("TRM_MCP_WRITE_ENABLED", "true")

    async def fake_run_agent_message(**kwargs):
        captured.update(kwargs)
        return {
            "answer": "项目当前总体进度为64%，请重点关注逾期任务。",
            "session_id": "session-from-gaios",
            "provider": "Gazellio G.AIOS",
            "agent_id": "default",
        }

    monkeypatch.setattr("app.main.run_agent_message", fake_run_agent_message)
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": "wangwj", "password": "Demo@123"})
        assert login.status_code == 200, login.text
        session_token = login.json()["data"]["token"]
        headers = {"X-Session": session_token}
        projects = client.get("/api/projects", headers=headers).json()["data"]
        assert projects
        response = client.post(
            "/api/ai/chat",
            headers=headers,
            json={
                "question": "这个项目当前有哪些风险？",
                "session_id": "existing-session",
                "project_id": projects[0]["id"],
                "source": "project360",
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["session_id"] == "session-from-gaios"
        assert data["agent_id"] == "default"
        assert captured["session_id"] == "existing-session"
        assert captured["source"] == "project360"
        assert projects[0]["project_no"] in captured["context"]
        context = json.loads(captured["context"])
        assert "ai.create.project" in context["ai_permissions"]
        assert context["mcp_action_capabilities"]["supported_writes"] == ["创建项目"]
        assert len(context["mcp_authorization"]["delegation_token"]) >= 40
        assert context["mcp_authorization"]["delegation_token"] not in response.text


def test_ai_chat_rejects_empty_question():
    with TestClient(app) as client:
        response = client.post("/api/ai/chat", json={"question": "   "})
        assert response.status_code == 400
        assert response.json()["code"] == "REQ-4002"
