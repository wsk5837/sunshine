from datetime import date

from fastapi.testclient import TestClient

from app.main import app


def h(role, user=None):
    return {"X-Role": role, "X-User": user or role}


def test_investment_closed_loop_and_controls():
    year = date.today().year
    with TestClient(app) as client:
        categories = client.get("/api/investments/categories", headers=h("admin")).json()["data"]
        assert len(categories) >= 6
        category_id = categories[0]["id"]

        created = client.post(
            "/api/investments/plans",
            headers=h("applicant", "investment-applicant"),
            json={
                "plan_name": f"{year}年自动化投入计划",
                "plan_year": year,
                "department": "自动化测试部",
                "description": "验证编制、审批、执行、核销和调整基线。",
                "prior_year_budget": 300000,
                "prior_year_actual": 260000,
                "current_year_budget": 400000,
                "current_year_actual": 100000,
            },
        )
        assert created.status_code == 200, created.text
        plan_id = created.json()["data"]["id"]

        item = client.post(
            f"/api/investments/plans/{plan_id}/items",
            headers=h("applicant"),
            json={
                "item_name": "投入闭环自动化测试项",
                "is_new": True,
                "category_id": category_id,
                "custom_tags": ["自动化", "重点"],
                "quantity": 2,
                "unit": "套",
                "application_amount": 200000,
                "payer": "自动化测试部",
                "business_purpose": "用于验证数字化投入管理闭环",
                "is_unplanned_reserve": False,
                "start_date": f"{year}-01-01",
                "end_date": f"{year}-12-31",
                "planned_payment_amount": 160000,
            },
        )
        assert item.status_code == 200, item.text
        item_id = item.json()["data"]["id"]
        detail = client.get(f"/api/investments/plans/{plan_id}", headers=h("applicant")).json()["data"]
        assert detail["application_total"] == 200000
        assert detail["items"][0]["custom_tags"] == ["自动化", "重点"]

        assert client.post(f"/api/investments/plans/{plan_id}/submit", headers=h("applicant")).status_code == 200
        for role in ("department_head", "finance", "vp"):
            approved = client.post(
                f"/api/investments/plans/{plan_id}/approve",
                headers=h(role),
                json={"action": "通过", "comment": "自动化测试通过"},
            )
            assert approved.status_code == 200, approved.text
        assert approved.json()["data"]["status"] == "待财务确认"

        finance = client.post(
            "/api/investments/finance/confirm-batch",
            headers=h("finance"),
            json={"ids": [plan_id], "action": "通过", "comment": "财务复核完成"},
        )
        assert finance.status_code == 200, finance.text
        assert client.get(f"/api/investments/plans/{plan_id}").json()["data"]["status"] == "已生效"

        projects = client.get("/api/projects").json()["data"]
        project_id = projects[0]["id"]
        bound = client.put(
            f"/api/investments/items/{item_id}/binding",
            headers=h("project_manager"),
            json={"project_id": project_id, "contract_id": None, "planned_payment_amount": 160000},
        )
        assert bound.status_code == 200, bound.text

        # Ordinary expenses must match the investment year and are immediately
        # registered in the write-off ledger.
        payment = client.post(
            "/api/investments/payments",
            headers=h("finance"),
            json={
                "item_id": item_id,
                "payment_type": "普通费用",
                "payment_year": year,
                "amount": 50000,
                "payment_date": f"{year}-06-30",
                "document_no": f"TEST-INV-PAY-{plan_id}",
                "payer": "自动化测试部",
                "description": "首次核销",
            },
        )
        assert payment.status_code == 200, payment.text
        execution = client.get("/api/investments/execution").json()["data"]
        current = next(row for row in execution if row["id"] == item_id)
        assert current["written_off_amount"] == 50000
        assert current["remaining_amount"] == 150000

        invalid_cross_year = client.post(
            "/api/investments/payments",
            headers=h("finance"),
            json={
                "item_id": item_id,
                "payment_type": "普通费用",
                "payment_year": year + 1,
                "amount": 1000,
                "payment_date": f"{year + 1}-01-10",
                "document_no": f"TEST-INV-BAD-{plan_id}",
                "payer": "自动化测试部",
            },
        )
        assert invalid_cross_year.status_code == 422

        adjustment = client.post(
            "/api/investments/adjustments",
            headers=h("applicant"),
            json={
                "plan_id": plan_id,
                "item_id": item_id,
                "adjustment_type": "金额与范围调整",
                "requested_amount": 280000,
                "scope_after": "扩大自动化验证范围",
                "reason": "业务范围扩展",
            },
        )
        assert adjustment.status_code == 200, adjustment.text
        adjustment_id = adjustment.json()["data"]["id"]
        assert client.post(f"/api/investments/adjustments/{adjustment_id}/submit", headers=h("applicant")).status_code == 200
        # 80,000 yuan increase requires department, finance and VP approval.
        for role in ("department_head", "finance", "vp"):
            result = client.post(
                f"/api/investments/adjustments/{adjustment_id}/approve",
                headers=h(role),
                json={"action": "通过", "comment": "同意调整"},
            )
            assert result.status_code == 200, result.text
        assert result.json()["data"]["status"] == "已生效"
        updated = client.get(f"/api/investments/plans/{plan_id}").json()["data"]["items"][0]
        assert updated["approved_amount"] == 280000
        assert updated["baseline_version"] == 2

        analytics = client.get("/api/investments/analytics").json()["data"]
        assert analytics["totals"]["item_count"] >= 1
        assert "category" in analytics["groups"]
        export = client.get("/api/investments/finance/export", headers=h("finance"))
        assert export.status_code == 200
        assert export.headers["content-type"].startswith("application/vnd.openxmlformats")


def test_investment_permissions_are_in_real_rbac_catalog():
    with TestClient(app) as client:
        catalog = client.get("/api/system/permissions", headers=h("admin")).json()["data"]
        assert catalog["investment.view"] == "数字化投入查看"
        assert catalog["investment.finance"] == "投入财务确认与核销"
        roles = {row["code"]: row for row in client.get("/api/system/roles", headers=h("admin")).json()["data"]}
        assert "investment.create" in roles["applicant"]["permissions"]
        assert "investment.finance" in roles["finance"]["permissions"]
