import re

from fastapi.testclient import TestClient

from app import db
from app.main import app


def login_headers(client, username):
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": "Demo@123"},
    )
    assert response.status_code == 200
    return {"X-Session": response.json()["data"]["token"]}


def test_protected_exports_return_files_with_real_session(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "protected-downloads.db")
    with TestClient(app) as client:
        applicant_headers = login_headers(client, "lili11-ghq")
        summary = client.get(
            "/api/exports/platform-summary.csv",
            headers=applicant_headers,
        )
        assert summary.status_code == 200
        assert "attachment" in summary.headers["content-disposition"]
        assert summary.content.startswith(b"\xef\xbb\xbf")

        product_headers = login_headers(client, "zhaomin")
        template = client.get(
            "/api/function-points/template",
            headers=product_headers,
        )
        assert template.status_code == 200
        assert "attachment" in template.headers["content-disposition"]
        assert template.content.startswith(b"PK")


def test_all_protected_downloads_stay_inside_authenticated_fetch_flow():
    with TestClient(app) as client:
        javascript = client.get("/app.js").text
        index = client.get("/").text

    source = javascript + "\n" + index
    assert "async function downloadProtectedFile" in javascript
    assert "'X-Session': state.sessionToken" in javascript
    assert "data-protected-download" in javascript
    assert "window.open('/api/" not in source
    assert 'href="/api/' not in source
    assert not re.search(r"location\.href\s*=\s*['\"`]\/api\/", source)


def test_literal_buttons_have_a_binding_or_delegated_action():
    with TestClient(app) as client:
        javascript = client.get("/app.js").text
        index = client.get("/").text

    source = javascript + "\n" + index
    literal_ids = set(re.findall(r'<button[^>]*\bid="([A-Za-z][\w-]*)"', source))
    literal_ids.update(
        re.findall(
            r"btn\([^\n]*?,\s*['\"][^'\"]*['\"]\s*,\s*['\"]([A-Za-z][\w-]*)['\"]",
            javascript,
        )
    )
    unbound = sorted(
        button_id
        for button_id in literal_ids
        if len(re.findall(re.escape(button_id), source)) < 2
    )
    assert not unbound, f"buttons without a binding: {unbound}"


def test_dashboard_header_has_no_business_action_buttons():
    with TestClient(app) as client:
        javascript = client.get("/app.js").text

    assert "'dashNew'" not in javascript
    assert "'dashExport'" not in javascript
    assert "title: '首页', iconName: 'home', crumbs: ['驾驶舱'], actions: ''" in javascript
