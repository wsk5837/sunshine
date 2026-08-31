from fastapi.testclient import TestClient

from app import db
from app.main import app


def h(role):
    return {"X-Role": role, "X-User": role}


def test_finance_approval_reserves_and_rejection_releases_budget(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "budget-accounting.db")
    with TestClient(app) as client:
        budget = client.get("/api/budget-ledger", headers=h("finance")).json()["data"][0]
        used_before = float(budget["used_budget"])
        created = client.post("/api/demands", headers=h("applicant"), json={
            "title": "预算分摊占用与释放测试",
            "description": "验证财务审批逐行占用预算，后续审批驳回后自动释放。",
            "demand_type": "系统功能新增",
            "budget_sources": [budget["budget_name"]],
            "priority": "中",
            "applicant": "预算测试人",
            "applicant_code": "budget-tester",
            "applicant_dept": "数字化管理部",
            "budget_amount": 12000,
        }).json()["data"]
        demand_id = created["id"]
        assert client.post(f"/api/demands/{demand_id}/submit", headers=h("applicant")).status_code == 200
        assert client.post(f"/api/demands/{demand_id}/approve", headers=h("department_head"), json={"action": "通过"}).status_code == 200
        fp = client.post(f"/api/demands/{demand_id}/function-points", headers=h("product_manager"), json={
            "demand_summary": "预算闭环", "name": "分摊占用", "system_name": "TRM",
            "evaluator": "产品经理", "department": "产品研发部", "team": "平台团队",
            "evaluation_date": "2026-08-26", "fp_count": 10, "unit_price": 1200,
        }).json()["data"]["function_points"][-1]
        assert client.put(f"/api/demands/{demand_id}/allocations", headers=h("product_manager"), json={"rows": [{
            "function_point_id": fp["id"], "system_name": "TRM", "expense_subject": "集团",
            "expense_source": budget["budget_name"], "ratio": 100, "department": "数字化管理部",
        }]}).status_code == 200
        assert client.post(f"/api/demands/{demand_id}/approve", headers=h("product_manager"), json={"action": "通过"}).status_code == 200

        finance = client.post(f"/api/demands/{demand_id}/approve", headers=h("finance"), json={"action": "通过", "comment": "占用确认"})
        assert finance.status_code == 200, finance.text
        ledger = client.get("/api/budget-allocations", headers=h("finance")).json()["data"]
        row = next(item for item in ledger if item["demand_id"] == demand_id)
        assert row["ledger_status"] == "已占用"
        assert row["amount"] == 12000
        budget_after = next(item for item in client.get("/api/budget-ledger", headers=h("finance")).json()["data"] if item["id"] == budget["id"])
        assert budget_after["used_budget"] == used_before + 12000

        rejected = client.post(f"/api/demands/{demand_id}/approve", headers=h("business_owner"), json={
            "action": "驳回", "comment": "需补充材料", "return_to": "产品经理审批",
        })
        assert rejected.status_code == 200, rejected.text
        row = next(item for item in client.get("/api/budget-allocations", headers=h("finance")).json()["data"] if item["demand_id"] == demand_id)
        assert row["ledger_status"] == "已释放"
        budget_released = next(item for item in client.get("/api/budget-ledger", headers=h("finance")).json()["data"] if item["id"] == budget["id"])
        assert budget_released["used_budget"] == used_before


def test_budget_used_amount_cannot_be_overwritten_by_budget_edit(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "budget-edit.db")
    with TestClient(app) as client:
        budget = client.get("/api/budget-ledger", headers=h("finance")).json()["data"][0]
        payload = {
            "budget_no": budget["budget_no"], "budget_name": budget["budget_name"],
            "total_budget": budget["total_budget"], "used_budget": 1,
            "internal_total": budget["internal_total"], "internal_used": 0,
            "digital_total": budget["digital_total"], "digital_used": 0, "year": budget["year"],
        }
        response = client.put(f"/api/budgets/{budget['id']}", headers=h("finance"), json=payload)
        assert response.status_code == 200, response.text
        refreshed = next(item for item in client.get("/api/budget-ledger", headers=h("finance")).json()["data"] if item["id"] == budget["id"])
        assert refreshed["used_budget"] == budget["used_budget"]


def test_function_point_catalog_and_per_point_budget_allocation(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "feature-point-allocation.db")
    with TestClient(app) as client:
        budget = client.get("/api/budget-ledger", headers=h("product_manager")).json()["data"][0]
        demand = client.post("/api/demands", headers=h("applicant"), json={
            "title": "功能点独立台账与预算分摊测试",
            "description": "验证功能点新增后进入统一管理，并按每个功能点金额进行预算分摊。",
            "demand_type": "系统功能新增", "budget_sources": [budget["budget_name"]], "priority": "中",
            "applicant": "测试申请人", "applicant_code": "fp-budget-tester", "applicant_dept": "数字化管理部",
            "budget_amount": 30000,
        }).json()["data"]
        demand_id = demand["id"]
        assert client.post(f"/api/demands/{demand_id}/submit", headers=h("applicant")).status_code == 200
        assert client.post(f"/api/demands/{demand_id}/approve", headers=h("department_head"), json={"action": "通过"}).status_code == 200

        first = client.post(f"/api/demands/{demand_id}/function-points", headers=h("product_manager"), json={
            "demand_summary": "认证接口", "name": "统一登录", "system_name": "TRM", "evaluator": "产品经理",
            "department": "产品研发部", "team": "平台团队", "evaluation_date": "2026-08-27",
            "fp_count": 10, "unit_price": 1000,
        }).json()["data"]["function_points"][-1]
        second = client.post(f"/api/demands/{demand_id}/function-points", headers=h("product_manager"), json={
            "demand_summary": "预算接口", "name": "预算占用", "system_name": "TRM", "evaluator": "产品经理",
            "department": "产品研发部", "team": "平台团队", "evaluation_date": "2026-08-27",
            "fp_count": 20, "unit_price": 1000,
        }).json()["data"]["function_points"][-1]
        catalog = client.get("/api/function-point-catalog?q=统一登录", headers=h("product_manager")).json()["data"]["items"]
        assert len(catalog) == 1
        assert catalog[0]["linked_count"] == 1
        assert catalog[0]["latest_fp_no"] == first["fp_no"]

        incomplete = client.put(f"/api/demands/{demand_id}/allocations", headers=h("product_manager"), json={"rows": [
            {"function_point_id": first["id"], "expense_subject": "集团", "expense_source": budget["budget_name"], "ratio": 100, "department": "数字化管理部"},
            {"function_point_id": second["id"], "expense_subject": "集团", "expense_source": budget["budget_name"], "ratio": 40, "department": "数字化管理部"},
        ]})
        assert incomplete.status_code == 200
        rejected = client.post(f"/api/demands/{demand_id}/approve", headers=h("product_manager"), json={"action": "通过"})
        assert rejected.status_code == 422
        assert second["fp_no"] in rejected.json()["message"]

        saved = client.put(f"/api/demands/{demand_id}/allocations", headers=h("product_manager"), json={"rows": [
            {"function_point_id": first["id"], "expense_subject": "集团", "expense_source": budget["budget_name"], "ratio": 60, "department": "数字化管理部"},
            {"function_point_id": first["id"], "expense_subject": "产险", "expense_source": budget["budget_name"], "ratio": 40, "department": "数字化管理部"},
            {"function_point_id": second["id"], "expense_subject": "集团", "expense_source": budget["budget_name"], "ratio": 100, "department": "数字化管理部"},
        ]}).json()
        amounts = [row["amount"] for row in saved["data"]["allocations"]]
        assert amounts == [6000, 4000, 20000]
        assert saved["coverage"]["complete"] is True
