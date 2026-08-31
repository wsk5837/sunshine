from fastapi.testclient import TestClient
from app.main import app


def test_health_and_meta():
    with TestClient(app) as client:
        assert client.get('/api/health').status_code == 200
        assert client.get('/api/meta').json()['code'] == 0


def test_root_page_assets_load():
    with TestClient(app) as client:
        page = client.get('/')
        css = client.get('/static/app.css')
        js = client.get('/static/app.js')
        assert page.status_code == 200
        assert './static/app.css' in page.text and './static/app.js' in page.text
        assert css.status_code == 200 and css.headers['content-type'].startswith('text/css')
        assert js.status_code == 200 and js.headers['content-type'].split(';')[0] in {'application/javascript', 'text/javascript'}
        # Preserve the aliases for clients that still have the previous HTML.
        assert client.get('/app.css').content == css.content
        assert client.get('/app.js').content == js.content


def test_list_demands():
    with TestClient(app) as client:
        result = client.get('/api/demands').json()
        assert result['code'] == 0
        assert result['data']['total'] >= 1
