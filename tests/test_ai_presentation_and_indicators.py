from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


BASE_DIR = Path(__file__).resolve().parents[1]


def test_ai_answers_use_safe_rich_text_renderer():
    script = (BASE_DIR / "app" / "static" / "app.js").read_text(encoding="utf-8")
    stylesheet = (BASE_DIR / "app" / "static" / "app.css").read_text(encoding="utf-8")
    assert "function renderAiText" in script
    assert "function chatBubble" in script
    assert "state.chat.map(chatBubble)" in script
    assert "ai-table-wrap" in script
    assert ".ai-rich" in stylesheet
    assert ".ai-table" in stylesheet
    assert "POC五类推荐问题" not in script
    assert "回答基于当前账号可访问的系统事实数据" not in script


def test_login_page_does_not_expose_seed_credentials():
    page = (BASE_DIR / "app" / "static" / "index.html").read_text(encoding="utf-8")
    assert "初始管理员" not in page
    assert "业务演示账号初始密码" not in page


def test_indicator_library_has_categories_directions_and_history():
    with TestClient(app) as client:
        response = client.get("/api/indicators")
        assert response.status_code == 200, response.text
        items = response.json()["data"]
        assert len(items) >= 15
        assert len({item["category"] for item in items}) >= 7
        assert {item["direction"] for item in items} == {"higher", "lower"}
        seeded_numbers = {f"KPI-2026-{number:04d}" for number in range(1, 16)}
        assert all(len(item["records"]) >= 4 for item in items if item["indicator_no"] in seeded_numbers)


def test_indicator_direction_is_persisted():
    with TestClient(app) as client:
        response = client.post(
            "/api/indicators",
            headers={"X-Role": "admin", "X-User": "admin"},
            json={
                "name": "测试缺陷密度",
                "category": "质量管理",
                "unit": "个/百功能点",
                "formula": "缺陷数 ÷ 功能点 × 100",
                "target_value": 3,
                "current_value": 2.4,
                "data_source": "TAPD缺陷",
                "frequency": "月度",
                "owner": "质量管理组",
                "status": "启用",
                "direction": "lower",
            },
        )
        assert response.status_code == 200, response.text
        item_id = response.json()["data"]["id"]
        created = next(item for item in client.get("/api/indicators").json()["data"] if item["id"] == item_id)
        assert created["direction"] == "lower"
        assert client.delete(f"/api/indicators/{item_id}", headers={"X-Role": "admin", "X-User": "admin"}).status_code == 200


def test_indicator_board_uses_compact_command_center_layout():
    script = (BASE_DIR / "app" / "static" / "app.js").read_text(encoding="utf-8")
    stylesheet = (BASE_DIR / "app" / "static" / "app.css").read_text(encoding="utf-8")
    assert "科技资源指标运行态势" in script
    assert "indicator-summary-grid" in script
    assert "indicator-screen-main" in script
    assert "indicator-matrix" in script
    assert "indicator-board-mode" in script
    assert "groups.map(category=>`<div class=\"section\"" not in script
    assert ".indicator-screen" in stylesheet
    assert ".indicator-board-mode footer" in stylesheet
    assert "grid-template-columns:repeat(5,minmax(0,1fr))" in stylesheet

