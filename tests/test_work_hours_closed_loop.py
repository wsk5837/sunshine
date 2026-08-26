from fastapi.testclient import TestClient

from app import db
from app.main import app


def h(role="project_manager"):
    return {"X-Role": role, "X-User": role}


def test_manual_work_plan_logs_require_function_point_and_approval(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "manual-work-hours.db")
    with TestClient(app) as client:
        demand_id = client.get("/api/demands?page_size=1").json()["data"]["items"][0]["id"]
        detail = client.get(f"/api/demands/{demand_id}").json()["data"]
        function_point_id = detail["function_points"][0]["id"]
        with db.connect() as conn:
            conn.execute("DELETE FROM demand_work_logs WHERE demand_id=?", (demand_id,))
            conn.execute("UPDATE demands SET actual_hours=0 WHERE id=?", (demand_id,))

        plan = client.put(
            f"/api/demands/{demand_id}/work-plan",
            headers=h("product_manager"),
            json={"estimated_hours": 100, "expected_completion_date": "2099-12-31", "note": "产品评估"},
        )
        assert plan.status_code == 200, plan.text
        assert plan.json()["data"]["estimated_hours"] == 100
        assert plan.json()["data"]["work_plan_source"] == "人工维护"

        log_ids = []
        for index in range(6):
            response = client.post(
                f"/api/demands/{demand_id}/work-logs",
                headers=h(),
                json={
                    "function_point_id": function_point_id,
                    "work_date": "2026-08-20",
                    "hours": 24,
                    "worker": f"工程师{index + 1}",
                    "task_name": "接口开发",
                    "description": "实际投入",
                },
            )
            assert response.status_code == 200, response.text
            log_ids.append(response.json()["data"]["work_logs"][0]["id"])

        detail = client.get(f"/api/demands/{demand_id}").json()["data"]
        assert detail["actual_hours"] == 0
        assert all(item["approval_status"] == "待审批" for item in detail["work_logs"])

        for work_log_id in log_ids:
            approved = client.post(
                f"/api/work-logs/{work_log_id}/approve",
                headers=h("product_manager"),
                json={"action": "通过", "comment": "工时与功能点投入匹配"},
            )
            assert approved.status_code == 200, approved.text

        detail = client.get(f"/api/demands/{demand_id}").json()["data"]
        assert detail["actual_hours"] == 144
        assert detail["actual_hours_source"] == "审批工时"
        assert len(detail["work_logs"]) == 6
        assert detail["deviation_notification_count"] == 2

        deleted = client.delete(f"/api/work-logs/{log_ids[0]}", headers=h())
        assert deleted.status_code == 409
        detail = client.get(f"/api/demands/{demand_id}").json()["data"]
        assert detail["actual_hours"] == 144
        assert detail["deviation_notification_count"] == 2


def test_tapd_timesheets_are_authoritative_for_actual_hours(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "tapd-timesheet-hours.db")
    with TestClient(app) as client:
        with db.connect() as conn:
            seed = conn.execute("SELECT id,demand_no FROM demands ORDER BY id LIMIT 1").fetchone()
            tapd_id = "TAPD-AUTH-HOURS"
            conn.execute(
                """INSERT INTO tapd_requirements
                   (demand_id,split_key,system_name,tapd_id,tapd_url,tapd_status,sync_status,payload_json,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (seed["id"], "default", "工时测试系统", tapd_id, "https://tapd.example/story/1", "新", "成功", "{}", db.now_iso()),
            )
            conn.execute("UPDATE demands SET tapd_id=? WHERE id=?", (tapd_id, seed["id"]))
            demand = {"id": seed["id"], "demand_no": seed["demand_no"], "tapd_id": tapd_id}
        payload = {
            "tapd_id": demand["tapd_id"],
            "demand_no": demand["demand_no"],
            "status": "开发中",
            "planned_online_date": "2099-12-31",
            "tasks": [{
                "task_id": "TASK-AUTH-HOURS",
                "title": "工时口径测试",
                "estimated_hours": 100,
                "completed_hours": 10,
            }],
            "costs": [
                {"task_id": "TASK-AUTH-HOURS", "spent_date": "2026-08-20", "hours": 30, "creator": "A"},
                {"task_id": "TASK-AUTH-HOURS", "spent_date": "2026-08-21", "hours": 35, "creator": "B"},
            ],
        }
        response = client.post("/api/tapd/webhook", json=payload)
        assert response.status_code == 200, response.text
        detail = client.get(f"/api/demands/{demand['id']}").json()["data"]
        assert detail["estimated_hours"] == 100
        assert detail["actual_hours"] == 65
        assert detail["actual_hours_source"] == "TAPD工时填报"
        assert detail["deviation_notification_count"] == 0


def test_actual_hours_below_estimate_does_not_create_overrun_alert(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "no-false-overrun.db")
    with TestClient(app) as client:
        with db.connect() as conn:
            demand_id = conn.execute("SELECT id FROM demands ORDER BY id LIMIT 1").fetchone()["id"]
            conn.execute("UPDATE demands SET estimated_hours=100,actual_hours=20 WHERE id=?", (demand_id,))
            conn.execute("DELETE FROM notifications WHERE demand_id=? AND title='工时偏差预警'", (demand_id,))
        detail = client.get(f"/api/demands/{demand_id}").json()["data"]
        assert detail["deviation_notification_count"] == 0


def test_work_hour_write_permissions_follow_business_roles(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "work-hour-permissions.db")
    with TestClient(app) as client:
        demand_id = client.get("/api/demands?page_size=1").json()["data"]["items"][0]["id"]
        applicant_login = client.post(
            "/api/auth/login", json={"username": "lili11-ghq", "password": "Demo@123"}
        ).json()["data"]["token"]
        denied = client.put(
            f"/api/demands/{demand_id}/work-plan",
            headers={"X-Session": applicant_login},
            json={"estimated_hours": 80, "expected_completion_date": "2099-12-31"},
        )
        assert denied.status_code == 403

        product_login = client.post(
            "/api/auth/login", json={"username": "zhaomin", "password": "Demo@123"}
        ).json()["data"]["token"]
        allowed = client.put(
            f"/api/demands/{demand_id}/work-plan",
            headers={"X-Session": product_login},
            json={"estimated_hours": 80, "expected_completion_date": "2099-12-31"},
        )
        assert allowed.status_code == 200, allowed.text
