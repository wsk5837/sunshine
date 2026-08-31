from fastapi.testclient import TestClient
from concurrent.futures import ThreadPoolExecutor

from app import db
from app.main import app


def h(role="admin"):
    return {"X-Role": role, "X-User": role}


def test_admin_delete_demand_releases_budget_and_non_admin_is_denied(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "admin-demand-delete.db")
    with TestClient(app) as client:
        budget = client.get("/api/budget-ledger", headers=h()).json()["data"][0]
        used_before = float(budget["used_budget"])
        demand = client.post("/api/demands", headers=h("applicant"), json={
            "title": "管理员删除需求预算释放测试", "description": "验证删除时释放预算并清理关联数据。",
            "demand_type": "系统功能新增", "budget_sources": [budget["budget_name"]], "priority": "中",
            "applicant": "测试人", "applicant_code": "delete-test", "applicant_dept": "数字化管理部",
            "budget_amount": 1000,
        }).json()["data"]
        demand_id = demand["id"]
        fp = client.post(f"/api/demands/{demand_id}/function-points", headers=h("product_manager"), json={
            "demand_summary": "删除测试", "name": "删除测试功能点", "system_name": "TRM",
            "evaluator": "产品经理", "department": "产品研发部", "team": "平台团队",
            "evaluation_date": "2026-08-27", "fp_count": 1, "unit_price": 1000,
        }).json()["data"]["function_points"][-1]
        client.put(f"/api/demands/{demand_id}/allocations", headers=h("product_manager"), json={"rows": [{
            "function_point_id": fp["id"], "expense_subject": "集团", "expense_source": budget["budget_name"],
            "ratio": 100, "department": "数字化管理部",
        }]})
        with db.connect() as conn:
            allocation = conn.execute("SELECT * FROM allocations WHERE demand_id=?", (demand_id,)).fetchone()
            conn.execute("UPDATE allocations SET ledger_status='已占用',budget_id=? WHERE id=?", (budget["id"], allocation["id"]))
            conn.execute("UPDATE budgets SET used_budget=used_budget+1000 WHERE id=?", (budget["id"],))

        assert client.delete(f"/api/demands/{demand_id}", headers=h("product_manager")).status_code == 403
        deleted = client.delete(f"/api/demands/{demand_id}", headers=h())
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["data"]["released_budget"] == 1000
        assert client.get(f"/api/demands/{demand_id}", headers=h()).status_code == 404
        refreshed = next(x for x in client.get("/api/budget-ledger", headers=h()).json()["data"] if x["id"] == budget["id"])
        assert refreshed["used_budget"] == used_before


def test_admin_delete_project_and_function_point_catalog(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "admin-project-delete.db")
    with TestClient(app) as client:
        project = client.post("/api/projects", headers=h("project_manager"), json={
            "name": "待删除项目", "manager": "测试经理", "department": "测试部", "budget_id": None,
            "total_budget": 10000, "status": "规划中", "progress": 0, "start_date": None,
            "end_date": None, "description": "管理员删除测试",
        }).json()["data"]
        assert client.delete(f"/api/projects/{project['id']}", headers=h("project_manager")).status_code == 403
        assert client.delete(f"/api/projects/{project['id']}", headers=h()).status_code == 200
        assert client.get(f"/api/projects/{project['id']}", headers=h()).status_code == 404

        demand_id = client.get("/api/demands?page_size=100", headers=h()).json()["data"]["items"][0]["id"]
        created = client.post(f"/api/demands/{demand_id}/function-points", headers=h("product_manager"), json={
            "demand_summary": "目录删除", "name": "管理员目录删除", "system_name": "TRM",
            "evaluator": "产品经理", "department": "产品研发部", "team": "平台团队",
            "evaluation_date": "2026-08-27", "fp_count": 2, "unit_price": 100,
        }).json()["data"]["function_points"][-1]
        with db.connect() as conn:
            catalog_id = conn.execute("SELECT catalog_id FROM function_points WHERE id=?", (created["id"],)).fetchone()["catalog_id"]
        assert client.delete(f"/api/function-point-catalog/{catalog_id}", headers=h("product_manager")).status_code == 403
        result = client.delete(f"/api/function-point-catalog/{catalog_id}", headers=h())
        assert result.status_code == 200, result.text
        assert result.json()["data"]["deleted_points"] == 1


def test_product_evaluation_empty_allocation_has_safe_dom_guard():
    source = (db.BASE_DIR / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert "const body = $('#allocBody');\n  if (!body) return;" in source
    assert "const ratioSum = $('#ratioSum');\n    if (!ratioSum) return;" in source


def test_parallel_notification_refresh_does_not_lock_sqlite(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "parallel-notifications.db")
    with TestClient(app) as client:
        def refresh(_):
            return client.get("/api/notifications", headers=h()).status_code

        with ThreadPoolExecutor(max_workers=6) as pool:
            statuses = list(pool.map(refresh, range(18)))
        assert statuses == [200] * 18
