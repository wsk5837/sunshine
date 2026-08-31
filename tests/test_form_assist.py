from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.ai_gateway import AIServiceError
from app.main import app


BASE_DIR = Path(__file__).resolve().parents[1]


def login_headers(client):
    response = client.post("/api/auth/login", json={"username": "wangwj", "password": "Demo@123"})
    assert response.status_code == 200, response.text
    return {"X-Session": response.json()["data"]["token"]}


def test_form_assist_calls_configured_agent_and_returns_clean_preview(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "form-assist-agent.db")
    captured = {}

    async def fake_agent(**kwargs):
        captured.update(kwargs)
        return {
            "answer": "## 优化后\n建立统一、规范、可追溯的需求管理机制。",
            "provider": "Gazellio G.AIOS",
            "agent_id": "form-agent",
            "session_id": "form-session",
        }

    monkeypatch.setattr("app.main.run_agent_message", fake_agent)
    with TestClient(app) as client:
        response = client.post(
            "/api/ai/form-assist",
            headers=login_headers(client),
            json={
                "field_label": "需求描述",
                "content": "需求流程不够清楚",
                "context": "需求标题：需求全生命周期优化",
                "mode": "polish",
            },
        )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["text"] == "建立统一、规范、可追溯的需求管理机制。"
    assert data["provider"] == "Gazellio G.AIOS"
    assert captured["source"] == "form-assist"
    assert "不得编造金额、日期" in captured["question"]


def test_form_assist_has_local_draft_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "form-assist-fallback.db")

    async def unavailable_agent(**kwargs):
        raise AIServiceError("模拟智能体不可用")

    monkeypatch.setattr("app.main.run_agent_message", unavailable_agent)
    with TestClient(app) as client:
        response = client.post(
            "/api/ai/form-assist",
            headers=login_headers(client),
            json={
                "field_label": "建设目标",
                "content": "",
                "context": "立项名称：科技资源平台优化",
                "mode": "draft",
            },
        )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["text"]
    assert "统一的业务流程" in data["text"]
    assert data["provider"] == "TRM本地填写助手"


def test_frontend_has_global_date_picker_and_permission_synced_ai_helper():
    script = (BASE_DIR / "app" / "static" / "app.js").read_text(encoding="utf-8")
    stylesheet = (BASE_DIR / "app" / "static" / "app.css").read_text(encoding="utf-8")
    assert "function enhanceDateInput" in script
    assert "field.showPicker()" in script
    assert 'input[type="date"],input[type="datetime-local"],input[type="time"]' in script
    assert "function enhanceAiTextField" in script
    assert "if (!hasPermission('ai')) return" in script
    assert "/api/ai/form-assist" in script
    assert "AI草拟" in script and "AI润色" in script and "使用此内容" in script
    assert "AI_ASSIST_EXCLUDED_IDS" in script
    assert ".date-assist-trigger" in stylesheet
    assert ".ai-assist-preview" in stylesheet

