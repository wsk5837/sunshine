from fastapi.testclient import TestClient

from app import db
from app.main import app


def h(role):
    return {"X-Role": role, "X-User": role}


def test_project_progress_follows_real_task_progress(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "project-progress.db")
    with TestClient(app) as client:
        created = client.post("/api/projects", headers=h("project_manager"), json={
            "name": "项目进度汇总测试", "manager": "项目经理", "department": "数字化管理部",
            "total_budget": 100000, "status": "实施中", "progress": 0,
            "start_date": "2026-08-01", "end_date": "2026-12-31",
        })
        assert created.status_code == 200, created.text
        project_id = created.json()["data"]["id"]
        task_ids = []
        for title, progress in (("任务A", 20), ("任务B", 80)):
            task = client.post(f"/api/projects/{project_id}/tasks", headers=h("project_manager"), json={
                "title": title, "owner": "工程师", "status": "进行中", "priority": "中", "progress": progress,
                "start_date": "2026-08-01", "end_date": "2026-09-30",
            })
            assert task.status_code == 200, task.text
            task_ids.append(task.json()["data"]["id"])
        detail = client.get(f"/api/projects/{project_id}").json()["data"]
        assert detail["progress"] == 50

        updated = client.put(f"/api/project-tasks/{task_ids[0]}", headers=h("project_manager"), json={
            "title": "任务A", "owner": "工程师", "status": "已完成", "priority": "中", "progress": 100,
            "start_date": "2026-08-01", "end_date": "2026-09-30",
        })
        assert updated.status_code == 200, updated.text
        assert client.get(f"/api/projects/{project_id}").json()["data"]["progress"] == 90


def test_financial_constraints_block_false_success(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "financial-integrity.db")
    with TestClient(app) as client:
        invalid_budget = client.post("/api/budgets", headers=h("finance"), json={
            "budget_name": "无效预算", "total_budget": 100, "used_budget": 120,
            "internal_total": 60, "internal_used": 0, "digital_total": 40, "digital_used": 0, "year": 2026,
        })
        assert invalid_budget.status_code == 422

        budget = client.get("/api/budget-ledger", headers=h("finance")).json()["data"][0]
        with db.connect() as conn:
            conn.execute("UPDATE budgets SET used_budget=total_budget-100 WHERE id=?", (budget["id"],))
        settlement = client.post("/api/settlements", headers=h("finance"), json={
            "budget_id": budget["id"], "amount": 1000, "settlement_type": "项目费用结算",
            "applicant": "财务测试", "description": "预算不足时不得假成功",
        })
        assert settlement.status_code == 200, settlement.text
        settlement_id = settlement.json()["data"]["id"]
        assert client.post(f"/api/settlements/{settlement_id}/submit", headers=h("finance")).status_code == 200
        approval = client.post(
            f"/api/settlements/{settlement_id}/approve", headers=h("finance"),
            json={"action": "通过", "comment": "尝试通过"},
        )
        assert approval.status_code == 422
        detail = client.get(f"/api/settlements/{settlement_id}/detail").json()["data"]
        assert detail["status"] == "审批中"
        assert detail["current_node"] == "财务审批"
        assert detail["approvals"] == []


def test_payment_update_cannot_exceed_contract_total(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "payment-plan-total.db")
    with TestClient(app) as client:
        contract = client.post("/api/contracts", headers=h("project_manager"), json={
            "name": "付款计划校验合同", "supplier": "供应商", "total_amount": 100,
            "start_date": "2026-08-01", "end_date": "2026-12-31",
        })
        assert contract.status_code == 200, contract.text
        contract_id = contract.json()["data"]["id"]
        first = client.post(f"/api/contracts/{contract_id}/payments", headers=h("finance"), json={
            "payment_type": "首付款", "amount": 60, "status": "待支付",
        })
        second = client.post(f"/api/contracts/{contract_id}/payments", headers=h("finance"), json={
            "payment_type": "尾款", "amount": 40, "status": "待支付",
        })
        assert first.status_code == 200 and second.status_code == 200
        update = client.put(f"/api/payment-plans/{first.json()['data']['id']}", headers=h("finance"), json={
            "payment_type": "首付款", "amount": 80, "status": "待支付",
        })
        assert update.status_code == 422

