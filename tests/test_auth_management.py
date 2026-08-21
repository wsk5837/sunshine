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
        assert 'ai.create.demand' in role_map['applicant']['permissions']
        assert 'ai.create.project' not in role_map['applicant']['permissions']
        assert 'ai.create.project' in role_map['project_manager']['permissions']
        catalog = client.get('/api/system/permissions', headers=headers)
        assert catalog.status_code == 200
        assert catalog.json()['data']['ai.query.budget'] == 'AI查询预算'
        assert catalog.json()['data']['ai.create.project'] == 'AI创建项目'
