from fastapi.testclient import TestClient
from app.main import app


def test_login_and_system_user_management():
    with TestClient(app) as client:
        login = client.post('/api/auth/login', json={'username': 'admin', 'password': 'Admin@123'})
        assert login.status_code == 200
        token = login.json()['data']['token']
        headers = {'X-Session': token}
        me = client.get('/api/auth/me', headers=headers)
        assert me.status_code == 200
        assert me.json()['data']['role_code'] == 'admin'
        users = client.get('/api/system/users', headers=headers)
        assert users.status_code == 200
        assert any(u['username'] == 'lili11-ghq' for u in users.json()['data'])
        roles = client.get('/api/system/roles', headers=headers)
        assert roles.status_code == 200
        assert any(r['code'] == 'admin' for r in roles.json()['data'])
        role_map = {r['code']: r for r in roles.json()['data']}
        assert 'demand.create' in role_map['applicant']['permissions']
        assert 'initiative.create' in role_map['applicant']['permissions']
        assert all(not any(p.startswith('ai.') for p in role['permissions']) for role in role_map.values())
        catalog = client.get('/api/system/permissions', headers=headers)
        assert catalog.status_code == 200
        assert catalog.json()['data']['ai'] == 'AI智能问答'
        assert not any(code.startswith('ai.') for code in catalog.json()['data'])
