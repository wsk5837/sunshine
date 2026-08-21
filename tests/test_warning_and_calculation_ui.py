from fastapi.testclient import TestClient

from app import db
from app.main import app


def h(role):
    return {"X-Role": role, "X-User": role}


def test_historical_work_deviation_creates_real_deduped_role_notifications(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "warning-reconcile.db")

    with TestClient(app) as client:
        with db.connect() as conn:
            demand_id = conn.execute("SELECT id FROM demands ORDER BY id LIMIT 1").fetchone()["id"]
            conn.execute(
                "UPDATE demands SET estimated_hours=100, actual_hours=140 WHERE id=?",
                (demand_id,),
            )
            conn.execute(
                "DELETE FROM notifications WHERE demand_id=? AND title='工时偏差预警'",
                (demand_id,),
            )

        first = client.get(f"/api/demands/{demand_id}")
        second = client.get(f"/api/demands/{demand_id}")
        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["data"]["deviation_notification_count"] == 2

        for role in ("product_manager", "project_manager"):
            notices = client.get("/api/notifications", headers=h(role)).json()["data"]
            matching = [
                notice for notice in notices
                if notice["demand_id"] == demand_id and notice["title"] == "工时偏差预警"
            ]
            assert len(matching) == 1
            assert matching[0]["target_role"] == role

        applicant_notices = client.get("/api/notifications", headers=h("applicant")).json()["data"]
        assert not any(
            notice["demand_id"] == demand_id and notice["title"] == "工时偏差预警"
            for notice in applicant_notices
        )


def test_function_point_amount_uses_hover_breakdown_and_clickable_detail():
    with TestClient(app) as client:
        javascript = client.get("/app.js").text
        stylesheet = client.get("/app.css").text

    assert "amountCalculationTooltip" in javascript
    assert "功能点金额计算" in javascript
    assert "go-fp-detail" in javascript
    assert "预警已真实写入消息中心" in javascript
    assert ".calc-tooltip:hover .calc-tooltip-panel" in stylesheet
    assert ".calc-tooltip:focus .calc-tooltip-panel" in stylesheet


def test_dashboard_skips_and_hides_modules_without_permission(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "dashboard-permission.db")
    with TestClient(app) as client:
        javascript = client.get("/app.js").text
        login = client.post(
            "/api/auth/login",
            json={"username": "lili11-ghq", "password": "Demo@123"},
        )
        assert login.status_code == 200
        headers = {"X-Session": login.json()["data"]["token"]}
        me = client.get("/api/auth/me", headers=headers).json()["data"]
        assert "dashboard" in me["permissions"]
        assert "demand.approve" not in me["permissions"]
        for path in ("/api/platform-dashboard", "/api/dashboard", "/api/notifications", "/api/tapd/overview"):
            assert client.get(path, headers=headers).status_code == 200
        assert client.get("/api/approvals/pending", headers=headers).status_code == 403

    assert "canApprove ? api('/api/approvals/pending')" in javascript
    assert "canViewTapd ? api('/api/tapd/overview')" in javascript
    assert "canApprove ? `<div class=\"section\"><div class=\"toolbar\"><div><div class=\"section-title\">我的需求审批待办" in javascript
    assert "hasPermission('contract') ? btn('合同台账'" in javascript
    assert "if ($(`#${id}`))" in javascript
