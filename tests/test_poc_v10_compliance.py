from fastapi.testclient import TestClient
from app.main import app


def h(role, user=None):
    return {"X-Role": role, "X-User": user or role}


def _create_and_reach_product(c, title="POC V1.0完整性测试"):
    budget = c.get('/api/budget-ledger').json()['data'][0]
    r = c.post('/api/demands', headers=h('applicant'), json={
        'title': title,
        'description': '验证OA待办、任意前置节点退回、多系统TAPD、回读任务花费、偏差告警和AI五类问答。',
        'demand_type': '系统功能新增',
        'budget_sources': [budget['budget_name']],
        'priority': '中',
        'applicant': 'POC测试申请人',
        'applicant_code': 'poc-tester',
        'applicant_dept': '数字化管理部',
        'budget_amount': 30000,
    })
    assert r.status_code == 200, r.text
    did = r.json()['data']['id']
    r = c.post(f'/api/demands/{did}/submit', headers=h('applicant'))
    assert r.status_code == 200, r.text
    d = c.get(f'/api/demands/{did}').json()['data']
    assert d['demand_no'].startswith('REQ-')
    assert d['oa_tasks'][-1]['node'] == '直属领导审批'
    assert d['oa_tasks'][-1]['status'] == '待处理'
    assert d['oa_tasks'][-1]['due_at']
    r = c.post(f'/api/demands/{did}/approve', headers=h('department_head'), json={'action':'通过','comment':'需求合理'})
    assert r.status_code == 200, r.text
    return did, budget


def test_poc_oa_flexible_return_multi_system_tapd_webhook_ai():
    with TestClient(app) as c:
        did, budget = _create_and_reach_product(c)

        # POC：审批驳回可退回任意前置节点。产品经理退回直属领导，而不是只能退申请人。
        r = c.post(f'/api/demands/{did}/approve', headers=h('product_manager'), json={
            'action':'驳回', 'comment':'请直属领导补充确认', 'return_to':'直属领导审批'
        })
        assert r.status_code == 200, r.text
        d = c.get(f'/api/demands/{did}').json()['data']
        assert d['current_node'] == '直属领导审批'
        assert d['approvals'][-1]['return_to'] == '直属领导审批'
        assert d['oa_tasks'][-1]['node'] == '直属领导审批'
        assert d['oa_tasks'][-1]['status'] == '待处理'
        assert c.post(f'/api/demands/{did}/approve', headers=h('department_head'), json={'action':'通过','comment':'已补充确认'}).status_code == 200

        # 两个归属系统，终审后应按系统拆分为两条TAPD需求。
        fp_ids = []
        for system, name, count in [('费用预算管理服务平台','预算单据查询',10), ('AIP稽核智能平台','风险指标回传',8)]:
            r = c.post(f'/api/demands/{did}/function-points', headers=h('product_manager'), json={
                'demand_summary': name, 'name': name, 'system_name': system,
                'evaluator':'赵敏','department':'产品研发部','team':'平台团队','evaluation_date':'2026-08-19',
                'fp_count':count,'unit_price':1200,
            })
            assert r.status_code == 200, r.text
            fp_ids.append(r.json()['data']['id'])
        r = c.put(f'/api/demands/{did}/allocations', headers=h('product_manager'), json={'rows':[
            {'function_point_id':fp_ids[0], 'system_name':'费用预算管理服务平台', 'expense_subject':'集团', 'expense_source':budget['budget_name'], 'ratio':60, 'department':'数字化管理部'},
            {'function_point_id':fp_ids[1], 'system_name':'AIP稽核智能平台', 'expense_subject':'集团', 'expense_source':budget['budget_name'], 'ratio':40, 'department':'数字化管理部'},
        ]})
        assert r.status_code == 200, r.text
        assert c.post(f'/api/demands/{did}/approve', headers=h('product_manager'), json={'action':'通过','comment':'功能点评估及费用分摊完成'}).status_code == 200
        assert c.post(f'/api/demands/{did}/approve', headers=h('finance'), json={'action':'通过','comment':'预算校验通过'}).status_code == 200
        r = c.post(f'/api/demands/{did}/approve', headers=h('business_owner'), json={'action':'通过','comment':'终审通过'})
        assert r.status_code == 200, r.text
        d = c.get(f'/api/demands/{did}').json()['data']
        assert len(d['tapd_requirements']) == 2
        systems = {x['system_name'] for x in d['tapd_requirements']}
        assert systems == {'费用预算管理服务平台','AIP稽核智能平台'}
        # 字段映射含申请人、预算、优先级和附件字段。
        payload = d['tapd_requirements'][0]['payload']
        assert payload['外部ID'] == d['demand_no']
        assert payload['优先级'] == 'Medium'
        assert payload['申请人'] == 'POC测试申请人'
        assert '附件上传' in payload

        # Webhook回读需求、任务、花费，并用40%工时偏差触发产品经理+项目经理双预警。
        tapd_id = d['tapd_requirements'][0]['tapd_id']
        webhook = {
            'tapd_id': tapd_id,
            'demand_no': d['demand_no'],
            'status': '已关闭',
            'demand_description': 'TAPD回读后的需求描述',
            'rd_owner': '研发主体A',
            'rd_department': '产品研发部',
            'internal_days': 10,
            'external_days': 2,
            'planned_online_date': '2026-09-30',
            'actual_online_date': '2026-09-28',
            'user_test_date': '2026-09-20',
            'test_complete_date': '2026-09-24',
            'demand_confirm_date': '2026-09-25',
            'tasks': [{
                'task_id':'TASK-POC-001','title':'接口开发','description':'接口研发任务','task_type':'开发任务',
                'planned_start':'2026-09-01','planned_end':'2026-09-20','estimated_hours':100,'creator':'研发工程师A',
                'created_at':'2026-09-01T09:00:00+08:00','completed_at':'2026-09-18T18:00:00+08:00',
                'completed_hours':140,'remaining_hours':0,'overrun_hours':40,
            }],
            'costs': [{'task_id':'TASK-POC-001','spent_date':'2026-09-18','hours':140,'creator':'研发工程师A','description':'开发工时'}],
        }
        r = c.post('/api/tapd/webhook', json=webhook)
        assert r.status_code == 200, r.text
        d = c.get(f'/api/demands/{did}').json()['data']
        assert d['status'] == '已完成'
        assert d['tapd_status'] == '已关闭'
        assert d['rd_owner'] == '研发主体A'
        assert d['planned_online_date'] == '2026-09-30'
        assert d['actual_online_date'] == '2026-09-28'
        assert d['user_test_date'] == '2026-09-20'
        assert len(d['tapd_tasks']) >= 1
        assert len(d['tapd_costs']) >= 1
        assert d['closed_at']
        for role in ('product_manager','project_manager'):
            notes = c.get('/api/notifications', headers=h(role)).json()['data']
            assert any(n['demand_id'] == did and n['title'] == '工时偏差预警' for n in notes)

        # AI问答五类POC场景均有事实型响应。
        questions = [
            f"{d['demand_no']} 的完整全生命周期信息是什么？",
            f"{budget['budget_name']}所有需求的状态分布和工时汇总",
            "数字化管理部月度预算执行趋势怎么样？",
            f"{d['demand_no']} 当前卡在哪个环节，预计何时完成？",
            f"{d['demand_no']} 历史同类需求怎么处理，平均交付周期是多少？",
        ]
        for q in questions:
            r = c.post('/api/ai/query', headers=h('project_manager'), json={'question':q})
            assert r.status_code == 200, r.text
            assert len(r.json()['data']['answer']) > 20


def test_poc_tapd_retry_queue_is_persistent_three_attempts():
    with TestClient(app) as c:
        did, budget = _create_and_reach_product(c, 'POC TAPD重试队列测试')
        r = c.post(f'/api/demands/{did}/function-points', headers=h('product_manager'), json={
            'demand_summary':'重试测试','name':'重试测试','system_name':'TAPD测试系统','evaluator':'赵敏',
            'department':'产品研发部','team':'研发效能组','evaluation_date':'2026-08-19','fp_count':5,'unit_price':1200,
        })
        assert r.status_code == 200
        fp_id = r.json()['data']['id']
        assert c.put(f'/api/demands/{did}/allocations',headers=h('product_manager'),json={'rows':[
            {'function_point_id':fp_id,'system_name':'TAPD测试系统','expense_subject':'集团','expense_source':budget['budget_name'],'ratio':100,'department':'数字化管理部'}
        ]}).status_code == 200
        assert c.post(f'/api/demands/{did}/approve',headers=h('product_manager'),json={'action':'通过','comment':'完成'}).status_code == 200
        assert c.post(f'/api/demands/{did}/approve',headers=h('finance'),json={'action':'通过','comment':'预算通过'}).status_code == 200
        # 为单独测试失败重试，先让终审记录通过但阻止自动成功创建：直接到审批通过状态。
        from app.db import connect, now_iso
        with connect() as conn:
            conn.execute("UPDATE demands SET status='审批通过',current_node='审批通过',updated_at=? WHERE id=?", (now_iso(), did))
        r = c.post(f'/api/demands/{did}/tapd/push?simulate_failure=true', headers=h('project_manager'))
        assert r.status_code == 200, r.text
        d = c.get(f'/api/demands/{did}').json()['data']
        assert d['tapd_retry_job']['attempt_count'] == 1
        assert d['tapd_retry_job']['status'] == '等待重试'
        assert d['tapd_retry_job']['next_retry_at']
        # 强制后台扫描用于测试，无需真实等待60秒；实际后台按配置时间执行。
        r = c.post('/api/poc/jobs/scan?force=true', headers=h('admin')); assert r.status_code == 200
        r = c.post('/api/poc/jobs/scan?force=true', headers=h('admin')); assert r.status_code == 200
        d = c.get(f'/api/demands/{did}').json()['data']
        assert d['tapd_retry_job']['attempt_count'] == 3
        assert d['tapd_retry_job']['status'] == '最终失败'
        assert d['status'] == 'TAPD同步失败'
        events = d['tapd_events']
        create_attempts = [e['attempt'] for e in events if e['event_type']=='CREATE']
        assert {1,2,3}.issubset(set(create_attempts))
