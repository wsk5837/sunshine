from fastapi.testclient import TestClient
from app.main import app


def test_health_and_meta():
    with TestClient(app) as client:
        assert client.get('/api/health').status_code == 200
        assert client.get('/api/meta').json()['code'] == 0


def test_list_demands():
    with TestClient(app) as client:
        result = client.get('/api/demands').json()
        assert result['code'] == 0
        assert result['data']['total'] >= 1
