from datetime import date

from fastapi.testclient import TestClient

from app.runtime import app
from app.db import connect


ADMIN = {"X-Role": "admin", "X-User": "workbench-test"}


def create_plan(client, amount=100000):
    plan = client.post("/api/investments/plans", headers=ADMIN, json={
        "plan_name": "工作台回归计划", "plan_year": date.today().year,
        "department": "测试部门", "description": "独立测试数据",
    }).json()["data"]["id"]
    category = client.get("/api/investments/categories", headers=ADMIN).json()["data"][0]["id"]
    item = client.post(f"/api/investments/plans/{plan}/items", headers=ADMIN, json={
        "item_name": "工作台回归投入", "category_id": category,
        "quantity": 1, "application_amount": amount, "payer": "测试部门",
        "business_purpose": "测试工作台", "planned_payment_amount": amount,
    }).json()["data"]["id"]
    return plan, item


def make_effective(client, plan):
    assert client.post(f"/api/investments/plans/{plan}/submit", headers=ADMIN).status_code == 200
    for _ in range(3):
        assert client.post(f"/api/investments/plans/{plan}/approve", headers=ADMIN, json={"action": "通过"}).status_code == 200
    assert client.post("/api/investments/finance/confirm-batch", headers=ADMIN, json={"ids": [plan], "action": "通过"}).status_code == 200


def test_item_review_amounts_and_duplicate_batch_cannot_skip_nodes():
    with TestClient(app) as c:
        plan, item = create_plan(c)
        c.post(f"/api/investments/plans/{plan}/submit", headers=ADMIN)
        invalid = c.post(f"/api/investments/plans/{plan}/approve", headers=ADMIN,
                         json={"action": "通过", "reviewed_amounts": {str(item): 100001}})
        assert invalid.status_code == 422
        detail = c.get(f"/api/investments/plans/{plan}", headers=ADMIN).json()["data"]
        assert detail["approvals"] == []
        valid = c.post(f"/api/investments/plans/{plan}/approve", headers=ADMIN,
                       json={"action": "通过", "reviewed_amounts": {str(item): 80000}, "comment": "逐项核定"})
        assert valid.status_code == 200, valid.text
        detail = c.get(f"/api/investments/plans/{plan}", headers=ADMIN).json()["data"]
        assert detail["approved_total"] == 80000
        assert detail["application_total"] == 100000
        result = c.post("/api/investments/approvals/batch", headers=ADMIN, json={"ids": [plan, plan], "action": "通过"})
        assert result.json()["data"]["succeeded"] == [plan]
        assert c.get(f"/api/investments/plans/{plan}", headers=ADMIN).json()["data"]["current_node"] == "分管领导审批"
        c.post(f"/api/investments/plans/{plan}/approve", headers=ADMIN, json={"action": "通过"})
        result = c.post("/api/investments/finance/confirm-batch", headers=ADMIN,
                        json={"ids": [plan], "action": "通过", "reviewed_amounts": {str(item): 75000}})
        assert result.status_code == 200
        detail = c.get(f"/api/investments/plans/{plan}", headers=ADMIN).json()["data"]
        assert detail["status"] == "已生效" and detail["approved_total"] == 75000


def test_adjustment_can_be_edited_resubmitted_and_history_read():
    with TestClient(app) as c:
        plan, item = create_plan(c)
        make_effective(c, plan)
        payload = {"plan_id": plan, "item_id": item, "requested_amount": 120000, "scope_after": "新范围", "reason": "测试调整"}
        adjustment = c.post("/api/investments/adjustments", headers=ADMIN, json=payload).json()["data"]["id"]
        endpoint = f"/api/investments/adjustments/{adjustment}"
        assert c.get(endpoint, headers=ADMIN).json()["data"]["approvals"] == []
        payload["requested_amount"] = 130000
        assert c.put(endpoint, headers=ADMIN, json=payload).status_code == 200
        c.post(endpoint + "/submit", headers=ADMIN)
        assert c.put(endpoint, headers=ADMIN, json=payload).status_code == 409
        c.post(endpoint + "/approve", headers=ADMIN, json={"action": "驳回", "comment": "补充依据"})
        payload["reason"] = "补充依据后重新提交"
        assert c.put(endpoint, headers=ADMIN, json=payload).status_code == 200
        c.post(endpoint + "/submit", headers=ADMIN)
        for _ in range(2):
            result = c.post(endpoint + "/approve", headers=ADMIN, json={"action": "通过"})
            assert result.status_code == 200
        detail = c.get(endpoint, headers=ADMIN).json()["data"]
        assert detail["status"] == "已生效"
        assert [r["action"] for r in detail["approvals"]] == ["驳回", "通过", "通过"]
        assert detail["scope_before"] == "测试工作台"
        assert detail["scope_after"] == "新范围"


def test_acknowledged_warning_stays_acknowledged_until_risk_changes():
    with TestClient(app) as c:
        plan, item = create_plan(c)
        make_effective(c, plan)
        # This plan has a payment deviation risk because planned payment is 100%.
        def warning():
            return next(w for w in c.get("/api/investments/warnings?status=", headers=ADMIN).json()["data"]
                        if w["item_id"] == item and w["rule_code"] == "payment_deviation")
        first = warning()
        assert first["status"] == "待处理"
        c.post(f"/api/investments/warnings/{first['id']}/resolve", headers=ADMIN)
        assert warning()["status"] == "已处理"
        assert warning()["status"] == "已处理"
        payment = c.post("/api/investments/payments", headers=ADMIN, json={
            "item_id": item, "payment_type": "普通费用", "payment_year": date.today().year,
            "amount": 10000, "payment_date": date.today().isoformat(),
            "document_no": f"WB-TEST-{item}", "payer": "测试部门", "description": "测试流水",
        })
        assert payment.status_code == 200, payment.text
        assert warning()["status"] == "待处理"
        history = c.get(f"/api/investments/payments?item_id={item}", headers=ADMIN).json()["data"]
        assert len(history) == 1 and history[0]["writeoff_amount"] == 10000
        assert history[0]["description"] == "测试流水"


def test_final_adjustment_rechecks_latest_payments_and_rolls_back_approval():
    with TestClient(app) as c:
        plan, item = create_plan(c)
        make_effective(c, plan)
        aid = c.post("/api/investments/adjustments", headers=ADMIN, json={
            "plan_id": plan, "item_id": item, "requested_amount": 60000, "reason": "缩减投入"
        }).json()["data"]["id"]
        endpoint = f"/api/investments/adjustments/{aid}"
        c.post(endpoint + "/submit", headers=ADMIN)
        c.post(endpoint + "/approve", headers=ADMIN, json={"action": "通过"})
        with connect() as conn:
            conn.execute("UPDATE investment_items SET written_off_amount=70000 WHERE id=?", (item,))
        result = c.post(endpoint + "/approve", headers=ADMIN, json={"action": "通过"})
        assert result.status_code == 422
        detail = c.get(endpoint, headers=ADMIN).json()["data"]
        assert detail["status"] == "审批中"
        assert len(detail["approvals"]) == 1


def test_reference_reads_effective_investment_ledger_and_is_department_scoped():
    with TestClient(app) as c:
        plan, item = create_plan(c, 50000)
        make_effective(c, plan)
        with connect() as conn:
            conn.execute("UPDATE investment_plans SET department='历史参考独立测试' WHERE id=?", (plan,))
            conn.execute("UPDATE investment_items SET written_off_amount=12000 WHERE id=?", (item,))
        data = c.get("/api/investments/reference", params={"plan_year": date.today().year + 1, "department": "历史参考独立测试"}, headers=ADMIN).json()["data"]
        assert data["current"]["budget"] == 50000
        assert data["current"]["actual"] == 12000
        assert data["current"]["plan_count"] == 1
        assert data["prior"]["plan_count"] == 0


def test_adjustment_and_warning_actions_use_distinct_permissions():
    from app.auth import permissions_for_api
    assert permissions_for_api("GET", "/api/investments/adjustments/1") == ("investment.view",)
    assert permissions_for_api("PUT", "/api/investments/adjustments/1") == ("investment.adjust",)
    assert permissions_for_api("POST", "/api/investments/adjustments/1/approve") == ("investment.approve",)
    assert "investment.view" not in permissions_for_api("POST", "/api/investments/warnings/1/resolve")
