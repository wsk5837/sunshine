from fastapi.testclient import TestClient

from app import db, poc
from app.main import app


ADMIN = {"X-Role": "admin", "X-User": "admin"}


def test_live_tapd_push_update_and_protected_webhook(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "tapd-live.db")
    monkeypatch.setenv("TRM_TAPD_API_USER", "test-api-user")
    monkeypatch.setenv("TRM_TAPD_API_PASSWORD", "test-api-password")
    monkeypatch.setenv("TRM_TAPD_WEBHOOK_SECRET", "test-webhook-secret")
    calls = []

    def fake_request(conn, method, path, *, params=None, data=None, timeout=10.0):
        calls.append({"method": method, "path": path, "params": params, "data": data})
        return {"status": 1, "data": {"Story": {"id": str((data or {}).get("id") or "123456"), "v_status": "开发中"}}}

    monkeypatch.setattr(poc, "_tapd_request", fake_request)
    with TestClient(app) as client:
        with db.connect() as conn:
            conn.execute("UPDATE system_settings SET value='live' WHERE code='tapd_mode'")
            conn.execute("UPDATE system_settings SET value='47402834' WHERE code='tapd_workspace_id'")
            demand = conn.execute("SELECT * FROM demands ORDER BY id LIMIT 1").fetchone()
            conn.execute("DELETE FROM tapd_requirements WHERE demand_id=?", (demand["id"],))
            conn.execute(
                """INSERT INTO tapd_requirements
                   (demand_id,split_key,system_name,tapd_id,tapd_url,tapd_status,sync_status,payload_json,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (demand["id"], "system:TRM", "TRM", "123456", "", "新", "成功", "{}", db.now_iso()),
            )
            conn.execute("UPDATE demands SET tapd_id='123456',expected_completion_date='2099-12-31',estimated_hours=40 WHERE id=?", (demand["id"],))

        pushed = client.post(f"/api/demands/{demand['id']}/tapd/push-update", headers=ADMIN)
        assert pushed.status_code == 200, pushed.text
        assert calls[-1]["method"] == "POST"
        assert calls[-1]["path"] == "/stories"
        assert calls[-1]["data"]["id"] == "123456"
        assert calls[-1]["data"]["workspace_id"] == "47402834"
        assert calls[-1]["data"]["effort"] == 40

        webhook = {"tapd_id": "123456", "status": "开发中", "planned_online_date": "2099-12-31"}
        denied = client.post("/api/tapd/webhook", headers=ADMIN, json=webhook)
        assert denied.status_code == 401
        accepted = client.post(
            "/api/tapd/webhook",
            headers={**ADMIN, "X-TAPD-Webhook-Secret": "test-webhook-secret"},
            json=webhook,
        )
        assert accepted.status_code == 200, accepted.text
        detail = client.get(f"/api/demands/{demand['id']}", headers=ADMIN).json()["data"]
        assert detail["tapd_status"] == "开发中"
        assert detail["status"] == "开发中"
