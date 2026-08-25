from pathlib import Path

from fastapi.testclient import TestClient

from app import db
from app.main import app


BASE_DIR = Path(__file__).resolve().parents[1]


def h(role="project_manager"):
    return {"X-Role": role, "X-User": role}


def create_project(client, name):
    response = client.post("/api/projects", headers=h(), json={
        "name": name,
        "manager": "项目经理",
        "department": "数字化管理部",
        "total_budget": 100000,
        "status": "规划中",
        "progress": 0,
        "start_date": "2026-09-01",
        "end_date": "2026-12-31",
        "description": "关联工作台测试",
    })
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


def test_project_relations_sync_to_contract_and_settlement_ledgers(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "project-relations.db")
    with TestClient(app) as client:
        project_id = create_project(client, "关联工作台项目")
        other_project_id = create_project(client, "其他项目")

        contract = client.post("/api/contracts", headers=h(), json={
            "name": "待关联实施合同",
            "supplier": "测试供应商",
            "total_amount": 50000,
            "start_date": "2026-09-01",
            "end_date": "2026-12-31",
            "owner": "合同经理",
            "description": "从项目内关联",
        })
        assert contract.status_code == 200, contract.text
        contract_id = contract.json()["data"]["id"]

        settlement = client.post("/api/settlements", headers=h(), json={
            "amount": 12000,
            "settlement_type": "项目结算",
            "applicant": "项目经理",
            "description": "从项目内关联",
        })
        assert settlement.status_code == 200, settlement.text
        settlement_id = settlement.json()["data"]["id"]

        linked = client.put(f"/api/projects/{project_id}/relations", headers=h(), json={
            "contract_ids": [contract_id],
            "settlement_ids": [settlement_id],
        })
        assert linked.status_code == 200, linked.text
        assert linked.json()["data"] == {"contract_count": 1, "settlement_count": 1}

        detail = client.get(f"/api/projects/{project_id}", headers=h()).json()["data"]
        assert [item["id"] for item in detail["contracts"]] == [contract_id]
        assert [item["id"] for item in detail["settlements"]] == [settlement_id]
        assert next(item for item in client.get("/api/contracts", headers=h()).json()["data"] if item["id"] == contract_id)["project_id"] == project_id
        assert next(item for item in client.get("/api/settlements", headers=h()).json()["data"] if item["id"] == settlement_id)["project_id"] == project_id

        occupied = client.put(f"/api/projects/{other_project_id}/relations", headers=h(), json={
            "contract_ids": [contract_id],
            "settlement_ids": None,
        })
        assert occupied.status_code == 409

        unlinked = client.put(f"/api/projects/{project_id}/relations", headers=h(), json={
            "contract_ids": [],
            "settlement_ids": [],
        })
        assert unlinked.status_code == 200, unlinked.text
        detail = client.get(f"/api/projects/{project_id}", headers=h()).json()["data"]
        assert detail["contracts"] == []
        assert detail["settlements"] == []


def test_project_ui_distinguishes_initiation_from_legacy_entry_and_manages_relations():
    script = (BASE_DIR / "app" / "static" / "app.js").read_text(encoding="utf-8")
    stylesheet = (BASE_DIR / "app" / "static" / "app.css").read_text(encoding="utf-8")
    assert "发起立项" in script
    assert "录入存量项目" in script
    assert "仅用于已完成线下立项审批或历史数据迁移" in script
    assert "生成项目','btn primary','iniConvert" not in script
    assert "立项审批生成" in script
    assert "pContractLink" in script
    assert "pSettlementLink" in script
    assert "pCreateContract" in script
    assert "pCreateSettlement" in script
    assert "/relations" in script
    assert ".project-relation-workbench" in stylesheet
