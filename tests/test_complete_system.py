from fastapi.testclient import TestClient
from app.main import app


def h(role, user=None):
    return {"X-Role": role, "X-User": user or role}


def test_complete_business_modules():
    with TestClient(app) as c:
        budgets = c.get('/api/budget-ledger').json()['data']
        budget_id = budgets[0]['id']

        # 立项：创建 -> 扩展信息 -> 提交 -> 三级审批 -> 转项目
        r = c.post('/api/initiatives', headers=h('applicant'), json={
            'title':'V4自动化测试立项','description':'用于验证完整立项流程','applicant':'测试申请人',
            'department':'数字化管理部','owner':'测试项目经理','estimated_budget':120000,
            'budget_id':budget_id,'planned_start':'2026-09-01','planned_end':'2026-12-31'
        }); assert r.status_code == 200, r.text
        iid = r.json()['data']['id']
        r = c.put(f'/api/initiatives/{iid}/profile', headers=h('applicant'), json={
            'project_type':'系统建设','background':'业务发展需要','objectives':'提升效率','scope':'平台建设',
            'expected_benefit':'缩短周期','sponsor':'测试Sponsor','urgency':'高'
        }); assert r.status_code == 200, r.text
        assert c.post(f'/api/initiatives/{iid}/submit', headers=h('applicant')).status_code == 200
        for role in ['department_head','finance','vp']:
            rr=c.post(f'/api/initiatives/{iid}/approve',headers=h(role),json={'action':'通过','comment':'同意'})
            assert rr.status_code == 200, rr.text
        rr=c.post(f'/api/initiatives/{iid}/convert-project',headers=h('project_manager'))
        assert rr.status_code == 200, rr.text
        pid=rr.json()['data']['project_id']

        # 项目：任务、风险、交付物
        rr=c.post(f'/api/projects/{pid}/tasks',headers=h('project_manager'),json={
            'title':'接口联调','owner':'工程师A','status':'进行中','priority':'高','progress':35,
            'start_date':'2026-09-05','end_date':'2026-09-20','parent_id':None
        }); assert rr.status_code == 200, rr.text
        rr=c.post(f'/api/projects/{pid}/risks',headers=h('project_manager'),json={
            'title':'联调资源冲突','category':'资源风险','probability':'中','impact':'高','level':'高',
            'owner':'测试项目经理','response_plan':'提前协调','status':'跟踪中','due_date':'2026-09-15'
        }); assert rr.status_code == 200, rr.text
        rr=c.post(f'/api/projects/{pid}/deliverables',headers=h('project_manager'),json={
            'name':'接口设计说明书','type':'文档','owner':'工程师A','planned_date':'2026-09-10',
            'version':'V1.0','status':'编制中','url':'','description':'接口基线'
        }); assert rr.status_code == 200, rr.text

        # 业务价值与指标
        rr=c.post('/api/business-values',headers=h('project_manager'),json={
            'project_id':pid,'value_type':'效率','metric_name':'需求交付周期缩短','planned_value':20,
            'realized_value':8,'unit':'%','period':'2026Q3','owner':'测试项目经理','status':'跟踪中'
        }); assert rr.status_code == 200, rr.text
        rr=c.post('/api/indicators',headers=h('admin'),json={
            'name':'需求按期交付率','category':'交付质量','unit':'%','formula':'按期完成/总量',
            'target_value':95,'current_value':90,'data_source':'需求系统','frequency':'月度','owner':'PMO','status':'启用'
        }); assert rr.status_code == 200, rr.text
        kid=rr.json()['data']['id']
        assert c.post(f'/api/indicators/{kid}/records',headers=h('admin'),json={'period':'2026-08','value':92,'source':'月度统计'}).status_code == 200

        # 合同：创建 -> 审批 -> 付款计划 -> 变更生效
        rr=c.post('/api/contracts',headers=h('project_manager'),json={
            'name':'V4测试实施合同','project_id':pid,'budget_id':budget_id,'supplier':'测试供应商',
            'total_amount':100000,'start_date':'2026-09-01','end_date':'2026-12-31','owner':'合同经理','description':'实施服务'
        }); assert rr.status_code == 200, rr.text
        cid=rr.json()['data']['id']
        assert c.post(f'/api/contracts/{cid}/submit',headers=h('project_manager')).status_code == 200
        assert c.post(f'/api/contracts/{cid}/approve',headers=h('finance'),json={'action':'通过','comment':'预算正常'}).status_code == 200
        assert c.post(f'/api/contracts/{cid}/approve',headers=h('business_owner'),json={'action':'通过','comment':'同意'}).status_code == 200
        rr=c.post(f'/api/contracts/{cid}/payments',headers=h('finance'),json={
            'payment_type':'首付款','amount':30000,'planned_date':'2026-09-30','actual_date':None,'status':'待支付','description':'首付款'
        }); assert rr.status_code == 200, rr.text
        rr=c.post(f'/api/contracts/{cid}/changes',headers=h('project_manager'),json={
            'change_type':'金额变更','reason':'新增实施范围','amount_delta':10000,'owner':'合同经理','status':'待确认','effective_date':None
        }); assert rr.status_code == 200, rr.text
        chid=rr.json()['data']['id']
        assert c.put(f'/api/contract-changes/{chid}/effective',headers=h('business_owner')).status_code == 200

        # 结算：创建 -> 明细 -> 提交 -> 两级审批 -> 自动记预算流水
        rr=c.post('/api/settlements',headers=h('applicant'),json={
            'project_id':pid,'contract_id':cid,'budget_id':budget_id,'amount':1,
            'settlement_type':'合同付款结算','applicant':'测试申请人','description':'阶段验收结算'
        }); assert rr.status_code == 200, rr.text
        sid=rr.json()['data']['id']
        rr=c.post(f'/api/settlements/{sid}/items',headers=h('applicant'),json={
            'item_name':'第一阶段实施费','item_type':'实施费','quantity':1,'unit_price':20000,'description':'阶段验收通过'
        }); assert rr.status_code == 200, rr.text
        assert c.post(f'/api/settlements/{sid}/submit',headers=h('applicant')).status_code == 200
        assert c.post(f'/api/settlements/{sid}/approve',headers=h('finance'),json={'action':'通过','comment':'核对无误'}).status_code == 200
        assert c.post(f'/api/settlements/{sid}/approve',headers=h('business_owner'),json={'action':'通过','comment':'确认结算'}).status_code == 200
        assert c.get(f'/api/settlements/{sid}/detail').json()['data']['status'] == '已完成'


def test_demand_poc_end_to_end():
    with TestClient(app) as c:
        budget = c.get('/api/budget-ledger').json()['data'][0]
        r=c.post('/api/demands',headers=h('applicant'),json={
            'title':'V4需求闭环自动化测试','description':'验证需求申请、审批、预算、TAPD和AI链路',
            'demand_type':'系统功能新增','budget_sources':[budget['budget_name']], 'priority':'中',
            'applicant':'测试申请人','applicant_code':'tester','applicant_dept':'数字化管理部','budget_amount':30000
        }); assert r.status_code == 200, r.text
        did=r.json()['data']['id']
        assert c.post(f'/api/demands/{did}/submit',headers=h('applicant')).status_code == 200
        assert c.post(f'/api/demands/{did}/approve',headers=h('department_head'),json={'action':'通过','comment':'合理'}).status_code == 200
        rr=c.post(f'/api/demands/{did}/function-points',headers=h('product_manager'),json={
            'demand_summary':'自动化测试','name':'查询与处理','system_name':'科技资源管理系统','evaluator':'测试产品经理',
            'department':'产品研发部','team':'平台团队','evaluation_date':'2026-08-19','fp_count':10,'unit_price':1200
        }); assert rr.status_code == 200, rr.text
        fp=rr.json()['data']
        rr=c.put(f'/api/demands/{did}/allocations',headers=h('product_manager'),json={'rows':[
            {'function_point_id':fp['id'],'system_name':'科技资源管理系统','expense_subject':'集团','expense_source':budget['budget_name'],'ratio':100,'department':'数字化管理部'}
        ]}); assert rr.status_code == 200, rr.text
        assert c.post(f'/api/demands/{did}/approve',headers=h('product_manager'),json={'action':'通过','comment':'评估完成'}).status_code == 200
        assert c.post(f'/api/demands/{did}/approve',headers=h('finance'),json={'action':'通过','comment':'预算充足'}).status_code == 200
        # 3万元跳过分管总，直接终审
        rr=c.post(f'/api/demands/{did}/approve',headers=h('business_owner'),json={'action':'通过','comment':'终审通过'})
        assert rr.status_code == 200, rr.text
        d=c.get(f'/api/demands/{did}').json()['data']
        assert d['tapd_id']
        assert d['tapd_sync_status'] == '成功'
        rr=c.post('/api/ai/query',headers=h('project_manager'),json={'question':f"{d['demand_no']} 当前状态和预算怎么样？"})
        assert rr.status_code == 200, rr.text
        assert d['demand_no'] in rr.json()['data']['answer']
