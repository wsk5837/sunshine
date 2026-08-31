"""Regression for the actual HTML asset URLs (not just files on disk)."""
from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess
from urllib.parse import urljoin, urlsplit

from fastapi.testclient import TestClient
import pytest

from app.runtime import app


class Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("src"):
            self.urls.append((attrs["src"], "javascript"))
        if tag == "link" and attrs.get("rel") == "stylesheet":
            self.urls.append((attrs["href"], "text/css"))


def test_every_root_html_asset_is_served_as_code_not_html():
    # Use the same entrypoint as Render. Hash routes still request this HTML.
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assets = Assets()
        assets.feed(response.text)
        paths = {urlsplit(url).path for url, _ in assets.urls}
        assert "./static/investment.js" in paths
        assert "./static/investment.css" in paths
        assert "./static/v5_fixes.js" in paths
        for url, mime in assets.urls:
            result = client.get(urljoin(str(response.url), url))
            assert result.status_code == 200, (url, result.status_code)
            assert mime in result.headers["content-type"], url
            assert result.content, url
            assert not result.text.lstrip().lower().startswith("<!doctype html"), url


def test_optional_module_route_isolation_in_javascript():
    node = shutil.which("node")
    if not node:
        pytest.skip("Install Node.js to run the frontend route regression tests")
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_name("frontend_routes.test.mjs"))],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
