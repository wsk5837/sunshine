/* Digital investment management workbench (V5.1). Loaded before app.js so the
   SPA renderer can reference these functions while sharing the same global scope. */

function invPct(value) { return `${Number(value || 0).toFixed(1)}%`; }
function invDate(value) { return value ? String(value).slice(0, 10) : '—'; }
function invStatus(value) { return `<span class="tag ${statusClass(value)}">${esc(value || '—')}</span>`; }
function invEmpty(label = '暂无数据') { return `<div class="empty investment-empty">${label}</div>`; }
function invBar(value, max, tone = '') {
  const rate = max > 0 ? Math.min(100, Number(value || 0) / Number(max) * 100) : 0;
  return `<div class="investment-bar ${tone}"><i style="width:${rate}%"></i></div>`;
}
function invOptions(rows, selected, label) {
  return `<option value="">${label}</option>${rows.map((row) => `<option value="${row.id}" ${Number(selected) === Number(row.id) ? 'selected' : ''}>${esc(row.name || row.item_name || row.plan_name || row.category_name)}</option>`).join('')}`;
}
function invCategoryOptions(rows, selected) {
  return `<option value="">请选择投入类别</option>${rows.filter((row) => row.status === '启用').map((row) => `<option value="${row.id}" ${Number(selected) === Number(row.id) ? 'selected' : ''}>${esc(row.category_name)} / ${esc(row.subcategory_name)}</option>`).join('')}`;
}
function invReadFormPlan() {
  return {
    plan_name: $('#invPlanName').value.trim(), plan_year: Number($('#invPlanYear').value), department: $('#invPlanDept').value.trim(),
    prior_year_budget: Number($('#invPriorBudget').value || 0), prior_year_actual: Number($('#invPriorActual').value || 0),
    current_year_budget: Number($('#invCurrentBudget').value || 0), current_year_actual: Number($('#invCurrentActual').value || 0),
    description: $('#invPlanDesc').value.trim()
  };
}

function openInvestmentPlanModal(plan = null) {
  const year = plan?.plan_year || new Date().getFullYear() + 1;
  showModal(`<h3 id="modalTitle">${plan ? '编辑投入计划' : '新建数字化投入计划'}</h3>
    <div class="grid-2"><div><label>计划名称 *</label><input class="field" id="invPlanName" value="${esc(plan?.plan_name || '')}" placeholder="如：${year}年数字化投入计划"></div><div><label>投入年度 *</label><input class="field" id="invPlanYear" type="number" min="2020" max="2100" value="${year}"></div></div>
    <div style="margin-top:10px"><label>提报部门 *</label><input class="field" id="invPlanDept" value="${esc(plan?.department || state.currentUserData?.department || '')}"></div>
    <div class="grid-4 investment-reference-grid" style="margin-top:10px"><div><label>上年预算</label><input class="field" id="invPriorBudget" type="number" min="0" value="${plan?.prior_year_budget || 0}"></div><div><label>上年实际</label><input class="field" id="invPriorActual" type="number" min="0" value="${plan?.prior_year_actual || 0}"></div><div><label>当年预算</label><input class="field" id="invCurrentBudget" type="number" min="0" value="${plan?.current_year_budget || 0}"></div><div><label>当年实际</label><input class="field" id="invCurrentActual" type="number" min="0" value="${plan?.current_year_actual || 0}"></div></div>
    <div class="investment-reference"><b>历史参考</b><span id="invReferenceText">填写上年与当年执行数据后，系统会计算执行率用于下一年规划。</span></div>
    <div style="margin-top:10px"><label>编制说明</label><textarea class="textarea" id="invPlanDesc" placeholder="说明规划依据、总体目标与资源边界">${esc(plan?.description || '')}</textarea></div>
    <div class="modal-actions">${btn('取消', 'btn', 'invPlanCancel')}${btn('保存', 'btn primary', 'invPlanSave')}</div>`, true);
  const refreshReference = () => {
    const priorBudget = Number($('#invPriorBudget').value || 0), priorActual = Number($('#invPriorActual').value || 0);
    const currentBudget = Number($('#invCurrentBudget').value || 0), currentActual = Number($('#invCurrentActual').value || 0);
    $('#invReferenceText').textContent = `上年执行率 ${priorBudget ? (priorActual / priorBudget * 100).toFixed(1) : '0.0'}%；当年执行率 ${currentBudget ? (currentActual / currentBudget * 100).toFixed(1) : '0.0'}%。`;
  };
  ['invPriorBudget','invPriorActual','invCurrentBudget','invCurrentActual'].forEach((id) => $(`#${id}`).addEventListener('input', refreshReference)); refreshReference();
  $('#invPlanCancel').addEventListener('click', closeModal);
  $('#invPlanSave').addEventListener('click', async () => {
    const payload = invReadFormPlan();
    if (!payload.plan_name || !payload.department || !payload.plan_year) return toast('请填写计划名称、年度和部门', 'error');
    try {
      const result = await api(plan ? `/api/investments/plans/${plan.id}` : '/api/investments/plans', {method: plan ? 'PUT' : 'POST', body: JSON.stringify(payload)});
      closeModal(); toast(result.message, 'success');
      if (plan) renderInvestmentPlanDetail(plan.id); else navigate(`investment-plan-detail/${result.data.id}`);
    } catch (error) { toast(error.message, 'error'); }
  });
}

async function renderInvestmentBoard() {
  setPage({title: '投入分析驾驶舱', iconName: 'grid', crumbs: ['投入管理']});
  const [result, itemResult] = await Promise.all([api('/api/investments/analytics'), api('/api/investments/items')]); const {totals, groups} = result.data; const allItems = itemResult.data;
  const categoryRows = Object.entries(groups.category).sort((a,b) => b[1].approved - a[1].approved);
  const departmentRows = Object.entries(groups.department).sort((a,b) => b[1].approved - a[1].approved);
  const maxCategory = Math.max(1, ...categoryRows.map(([,v]) => v.approved));
  const maxDepartment = Math.max(1, ...departmentRows.map(([,v]) => v.approved));
  appView.innerHTML = `<div class="investment-screen">
    <div class="investment-kpis">
      <div class="investment-kpi blue"><span>投入计划</span><strong>${totals.plan_count}</strong><small>${totals.item_count} 条明细</small></div>
      <div class="investment-kpi purple"><span>申请总金额</span><strong>¥${money(totals.application_total)}</strong><small>全部提报口径</small></div>
      <div class="investment-kpi cyan"><span>审核后总金额</span><strong>¥${money(totals.approved_total)}</strong><small>基线额度</small></div>
      <div class="investment-kpi green"><span>已核销</span><strong>¥${money(totals.written_off_total)}</strong><small>执行率 ${invPct(totals.execution_rate)}</small></div>
      <div class="investment-kpi orange"><span>剩余未核销</span><strong>¥${money(totals.remaining_total)}</strong><small>${totals.warning_count} 条待处理预警</small></div>
    </div>
    <div class="investment-dashboard-grid">
      <section class="section investment-chart"><div class="section-head"><h3>投入类别分布</h3><small>审核金额 / 已核销</small></div>${categoryRows.length ? categoryRows.map(([name,v]) => `<div class="investment-chart-row"><span>${esc(name)}</span><div>${invBar(v.approved,maxCategory)}<small>核销 ¥${money(v.written_off)}</small></div><b>¥${money(v.approved)}</b></div>`).join('') : invEmpty()}</section>
      <section class="section investment-chart"><div class="section-head"><h3>部门投入对比</h3><small>申请 / 审核</small></div>${departmentRows.length ? departmentRows.map(([name,v]) => `<div class="investment-chart-row"><span>${esc(name)}</span><div>${invBar(v.approved,maxDepartment,'purple')}<small>申请 ¥${money(v.application)}</small></div><b>¥${money(v.approved)}</b></div>`).join('') : invEmpty()}</section>
    </div>
    <section class="section investment-exec-card"><div class="section-head"><h3>总体执行差异</h3><button class="btn" id="invBoardExecution">查看执行明细</button></div><div class="investment-progress-summary"><div><span>核销进度</span><strong>${invPct(totals.execution_rate)}</strong></div>${invBar(totals.written_off_total, totals.approved_total, totals.execution_rate >= 90 ? 'orange' : 'green')}<p>审核后额度 ¥${money(totals.approved_total)}，已核销 ¥${money(totals.written_off_total)}，尚未核销 ¥${money(totals.remaining_total)}。</p></div></section>
    <section class="section"><div class="section-head"><h3>全量投入明细</h3><span>包含草稿、审批中、候补与已生效项</span></div><div class="table-wrap"><table><thead><tr><th>明细编号</th><th>投入项</th><th>年度 / 部门</th><th>分类</th><th>申请金额</th><th>审核金额</th><th>已核销</th><th>计划状态</th></tr></thead><tbody>${allItems.length?allItems.map(i=>`<tr><td><strong>${esc(i.item_no)}</strong><small class="cell-sub">${esc(i.plan_no)}</small></td><td>${esc(i.item_name)}${i.is_unplanned_reserve?'<span class="tag warn">候补</span>':''}</td><td>${i.plan_year}<small class="cell-sub">${esc(i.department)}</small></td><td>${esc(i.category_name)}<small class="cell-sub">${esc(i.subcategory_name)}</small></td><td>¥${money(i.application_amount)}</td><td>¥${money(i.approved_amount)}</td><td>¥${money(i.written_off_amount)}</td><td>${invStatus(i.plan_status)}</td></tr>`).join(''):`<tr><td colspan="8">${invEmpty('暂无投入明细')}</td></tr>`}</tbody></table></div></section>
  </div>`;
  $('#invBoardExecution').addEventListener('click', () => navigate('investment-execution'));
}

async function renderInvestmentPlans() {
  setPage({title: '投入计划编制', iconName: 'grid', crumbs: ['投入管理'], actions: hasPermission('investment.create') ? btn('新建投入计划','btn primary','invNewPlan') : ''});
  const rows = (await api('/api/investments/plans')).data;
  appView.innerHTML = `<section class="section"><div class="toolbar"><input class="field" id="invPlanSearch" placeholder="搜索计划编号、名称或部门"><select class="select" id="invPlanStatus"><option value="">全部状态</option>${['草稿','审批中','待财务确认','已生效','已驳回'].map((x)=>`<option>${x}</option>`).join('')}</select></div><div class="table-wrap"><table><thead><tr><th>计划编号</th><th>计划名称</th><th>年度 / 部门</th><th>申请金额</th><th>审核后金额</th><th>当前节点</th><th>状态</th><th>操作</th></tr></thead><tbody id="invPlanRows"></tbody></table></div></section>`;
  const draw = () => {
    const keyword = $('#invPlanSearch').value.trim().toLowerCase(), status = $('#invPlanStatus').value;
    const filtered = rows.filter((row) => (!keyword || `${row.plan_no}${row.plan_name}${row.department}`.toLowerCase().includes(keyword)) && (!status || row.status === status));
    $('#invPlanRows').innerHTML = filtered.length ? filtered.map((row) => `<tr><td><button class="link inv-plan-open" data-id="${row.id}">${esc(row.plan_no)}</button></td><td><strong>${esc(row.plan_name)}</strong><small class="cell-sub">${esc(row.applicant)}</small></td><td>${row.plan_year}<small class="cell-sub">${esc(row.department)}</small></td><td>¥${money(row.application_total)}</td><td>¥${money(row.approved_total)}</td><td>${esc(row.current_node)}</td><td>${invStatus(row.status)}</td><td><button class="link inv-plan-open" data-id="${row.id}">查看详情</button></td></tr>`).join('') : `<tr><td colspan="8">${invEmpty('暂无投入计划')}</td></tr>`;
    $$('.inv-plan-open').forEach((button) => button.addEventListener('click', () => navigate(`investment-plan-detail/${button.dataset.id}`)));
  };
  $('#invPlanSearch').addEventListener('input', draw); $('#invPlanStatus').addEventListener('change', draw); draw();
  $('#invNewPlan')?.addEventListener('click', () => openInvestmentPlanModal());
}

async function openInvestmentItemModal(plan, item = null) {
  const categories = (await api('/api/investments/categories')).data;
  showModal(`<h3 id="modalTitle">${item ? '编辑投入明细' : '新增投入明细'}</h3>
    <div class="grid-2"><div><label>投入项名称 *</label><input class="field" id="invItemName" value="${esc(item?.item_name || '')}"></div><div><label>投入分类 *</label><select class="select" id="invItemCategory">${invCategoryOptions(categories,item?.category_id)}</select></div></div>
    <div class="grid-4" style="margin-top:10px"><div><label>申请数量 *</label><input class="field" id="invItemQty" type="number" min="0.01" step="0.01" value="${item?.quantity || 1}"></div><div><label>单位</label><input class="field" id="invItemUnit" value="${esc(item?.unit || '项')}"></div><div><label>申请总金额 *</label><input class="field" id="invItemAmount" type="number" min="0.01" step="0.01" value="${item?.application_amount || ''}"></div><div><label>计划付款金额</label><input class="field" id="invItemPlanned" type="number" min="0" step="0.01" value="${item?.planned_payment_amount || 0}"></div></div>
    <div class="grid-2" style="margin-top:10px"><div><label>支付方 *</label><input class="field" id="invItemPayer" value="${esc(item?.payer || plan.department)}"></div><div><label>自定义标签（逗号分隔）</label><input class="field" id="invItemTags" value="${esc((item?.custom_tags || []).join(','))}" placeholder="核心系统,重点保障"></div></div>
    <div class="grid-2" style="margin-top:10px"><div><label>计划开始</label><input class="field" id="invItemStart" type="date" value="${invDate(item?.start_date).replace('—','')}"></div><div><label>计划结束</label><input class="field" id="invItemEnd" type="date" value="${invDate(item?.end_date).replace('—','')}"></div></div>
    <div style="margin-top:10px"><label>业务用途 *</label><textarea class="textarea" id="invItemPurpose">${esc(item?.business_purpose || '')}</textarea></div>
    <div class="investment-checks"><label><input type="checkbox" id="invItemNew" ${item?.is_new ? 'checked' : ''}> 新增投入项</label><label><input type="checkbox" id="invItemReserve" ${item?.is_unplanned_reserve ? 'checked' : ''}> 计划外候补投入</label></div>
    <div class="modal-actions">${btn('取消','btn','invItemCancel')}${btn('保存明细','btn primary','invItemSave')}</div>`, true);
  $('#invItemCancel').addEventListener('click', closeModal);
  $('#invItemSave').addEventListener('click', async () => {
    const categoryId = Number($('#invItemCategory').value || 0);
    const payload = {item_name:$('#invItemName').value.trim(),is_new:$('#invItemNew').checked,category_id:categoryId || null,category_name:'',subcategory_name:'',custom_tags:$('#invItemTags').value.split(/[,，]/).map(x=>x.trim()).filter(Boolean),quantity:Number($('#invItemQty').value),unit:$('#invItemUnit').value.trim(),application_amount:Number($('#invItemAmount').value),approved_amount:null,payer:$('#invItemPayer').value.trim(),business_purpose:$('#invItemPurpose').value.trim(),is_unplanned_reserve:$('#invItemReserve').checked,project_id:item?.project_id||null,contract_id:item?.contract_id||null,start_date:$('#invItemStart').value||null,end_date:$('#invItemEnd').value||null,planned_payment_amount:Number($('#invItemPlanned').value||0)};
    if (!payload.item_name || !payload.category_id || !payload.application_amount || !payload.payer || !payload.business_purpose) return toast('请完整填写投入项、分类、金额、支付方和用途','error');
    try { const result = await api(item ? `/api/investments/items/${item.id}` : `/api/investments/plans/${plan.id}/items`,{method:item?'PUT':'POST',body:JSON.stringify(payload)}); closeModal();toast(result.message,'success');renderInvestmentPlanDetail(plan.id); } catch(error){toast(error.message,'error');}
  });
}

async function renderInvestmentPlanDetail(id) {
  const plan = (await api(`/api/investments/plans/${id}`)).data;
  const editable = ['草稿','已驳回'].includes(plan.status) && hasPermission('investment.create');
  setPage({title:'投入计划详情',iconName:'grid',crumbs:['投入管理',plan.plan_no],actions:`${btn('返回列表','btn','invBack')}${editable?btn('编辑计划','btn','invEditPlan')+btn('新增明细','btn primary','invAddItem'):''}`});
  const referenceRate = plan.current_year_budget ? plan.current_year_actual / plan.current_year_budget * 100 : 0;
  appView.innerHTML = `<div class="investment-plan-head"><div><span>${esc(plan.plan_no)}</span><h2>${esc(plan.plan_name)}</h2><p>${plan.plan_year}年 · ${esc(plan.department)} · 申请人 ${esc(plan.applicant)}</p></div><div>${invStatus(plan.status)}<small>${esc(plan.current_node)}</small></div></div>
    <div class="investment-kpis compact"><div class="investment-kpi"><span>申请总金额</span><strong>¥${money(plan.application_total)}</strong></div><div class="investment-kpi"><span>审核后金额</span><strong>¥${money(plan.approved_total)}</strong></div><div class="investment-kpi"><span>上年执行</span><strong>${plan.prior_year_budget?invPct(plan.prior_year_actual/plan.prior_year_budget*100):'0.0%'}</strong></div><div class="investment-kpi"><span>当年执行</span><strong>${invPct(referenceRate)}</strong></div></div>
    <section class="section"><div class="section-head"><h3>投入明细</h3><span>${plan.items.length} 项</span></div><div class="table-wrap"><table><thead><tr><th>编号 / 名称</th><th>分类</th><th>标记</th><th>数量</th><th>申请金额</th><th>支付方</th><th>计划周期</th><th>操作</th></tr></thead><tbody>${plan.items.length?plan.items.map((item)=>`<tr><td><strong>${esc(item.item_no)}</strong><small class="cell-sub">${esc(item.item_name)}</small></td><td>${esc(item.category_name)}<small class="cell-sub">${esc(item.subcategory_name)}</small></td><td>${item.is_new?'<span class="tag success">新增</span>':''}${item.is_unplanned_reserve?'<span class="tag warn">候补</span>':''}</td><td>${item.quantity} ${esc(item.unit)}</td><td>¥${money(item.application_amount)}</td><td>${esc(item.payer)}</td><td>${invDate(item.start_date)} ~ ${invDate(item.end_date)}</td><td>${editable?`<button class="link inv-edit-item" data-id="${item.id}">编辑</button> <button class="link danger inv-delete-item" data-id="${item.id}">删除</button>`:'—'}</td></tr>`).join(''):`<tr><td colspan="8">${invEmpty('请新增至少一条投入明细')}</td></tr>`}</tbody></table></div></section>
    <div class="investment-detail-grid"><section class="section"><div class="section-head"><h3>编制说明</h3></div><p class="investment-description">${esc(plan.description||'—')}</p></section><section class="section"><div class="section-head"><h3>审批记录</h3></div>${plan.approvals.length?`<div class="investment-timeline">${plan.approvals.map(a=>`<div><i></i><strong>${esc(a.node)} · ${esc(a.action)}</strong><span>${esc(a.approver)} · ${invDate(a.created_at)}</span><p>${esc(a.comment||'无审批意见')}</p></div>`).join('')}</div>`:invEmpty('暂无审批记录')}</section></div>
    ${editable?`<div class="investment-submit-bar"><div><strong>提交前检查</strong><span>需要至少一条投入明细，提交后将进入部门负责人审批。</span></div>${btn('提交审批','btn primary','invSubmitPlan')}</div>`:''}`;
  $('#invBack').addEventListener('click',()=>navigate('investment-plans')); $('#invEditPlan')?.addEventListener('click',()=>openInvestmentPlanModal(plan)); $('#invAddItem')?.addEventListener('click',()=>openInvestmentItemModal(plan));
  $$('.inv-edit-item').forEach(b=>b.addEventListener('click',()=>openInvestmentItemModal(plan,plan.items.find(x=>x.id===Number(b.dataset.id)))));
  $$('.inv-delete-item').forEach(b=>b.addEventListener('click',async()=>{if(!confirm('确定删除该投入明细吗？'))return;try{const r=await api(`/api/investments/items/${b.dataset.id}`,{method:'DELETE'});toast(r.message,'success');renderInvestmentPlanDetail(id);}catch(e){toast(e.message,'error')}}));
  $('#invSubmitPlan')?.addEventListener('click',async()=>{if(!confirm('提交后将按审批流程流转，确认提交？'))return;try{const r=await api(`/api/investments/plans/${id}/submit`,{method:'POST'});toast(r.message,'success');renderInvestmentPlanDetail(id);}catch(e){toast(e.message,'error')}});
}

async function renderInvestmentApprovals() {
  setPage({title:'投入审批与财务确认',iconName:'approve',crumbs:['投入管理'],actions:hasPermission('investment.finance')?`<button class="btn" data-protected-download="/api/investments/finance/export" data-filename="数字化投入财务复核.xlsx">导出财务复核资料</button>`:''});
  const [pending, plans] = await Promise.all([api('/api/investments/approvals/pending'),api('/api/investments/plans')]);
  const finance = plans.data.filter(p=>p.status==='待财务确认');
  const table = (rows,type)=>`<div class="table-wrap"><table><thead><tr><th><input type="checkbox" class="inv-check-all" data-type="${type}"></th><th>计划</th><th>年度 / 部门</th><th>金额</th><th>当前节点</th><th>提报人</th><th>操作</th></tr></thead><tbody>${rows.length?rows.map(p=>`<tr><td><input type="checkbox" class="inv-approval-check" data-type="${type}" value="${p.id}"></td><td><strong>${esc(p.plan_no)}</strong><small class="cell-sub">${esc(p.plan_name)}</small></td><td>${p.plan_year}<small class="cell-sub">${esc(p.department)}</small></td><td>¥${money(p.application_total)}</td><td>${esc(p.current_node)}</td><td>${esc(p.applicant)}</td><td><button class="link" data-inv-detail="${p.id}">详情</button></td></tr>`).join(''):`<tr><td colspan="7">${invEmpty('暂无待处理数据')}</td></tr>`}</tbody></table></div>`;
  appView.innerHTML=`<section class="section"><div class="section-head"><h3>我的待办审批</h3><div>${btn('批量通过','btn primary','invBatchApprove')}${btn('批量驳回','btn danger','invBatchReject')}</div></div>${table(pending.data,'approval')}</section>${hasPermission('investment.finance')?`<section class="section"><div class="section-head"><h3>待财务复核确认</h3><div>${btn('批量完成财务确认','btn primary','invFinanceConfirm')}${btn('财务驳回','btn danger','invFinanceReject')}</div></div>${table(finance,'finance')}</section>`:''}`;
  $$('.inv-check-all').forEach(c=>c.addEventListener('change',()=>$$(`.inv-approval-check[data-type="${c.dataset.type}"]`).forEach(x=>x.checked=c.checked)));
  $$('[data-inv-detail]').forEach(b=>b.addEventListener('click',()=>navigate(`investment-plan-detail/${b.dataset.invDetail}`)));
  const batch = async(type,action)=>{const ids=$$(`.inv-approval-check[data-type="${type}"]:checked`).map(c=>Number(c.value));if(!ids.length)return toast('请先勾选要处理的计划','error');const comment=prompt(`请输入${action}意见`,action==='通过'?'已核对，同意。':'请补充修改。');if(comment===null)return;try{const url=type==='finance'?'/api/investments/finance/confirm-batch':'/api/investments/approvals/batch';const r=await api(url,{method:'POST',body:JSON.stringify({ids,action,comment})});toast(r.message,'success');renderInvestmentApprovals();}catch(e){toast(e.message,'error')}};
  $('#invBatchApprove').addEventListener('click',()=>batch('approval','通过'));$('#invBatchReject').addEventListener('click',()=>batch('approval','驳回'));$('#invFinanceConfirm')?.addEventListener('click',()=>batch('finance','通过'));$('#invFinanceReject')?.addEventListener('click',()=>batch('finance','驳回'));
}

async function openInvestmentAdjustmentModal() {
  const execution=(await api('/api/investments/execution')).data;
  if(!execution.length)return toast('当前没有可调整的已生效投入项','error');
  showModal(`<h3 id="modalTitle">新建投入调整申请</h3><div><label>选择已生效投入项 *</label><select class="select" id="invAdjItem"><option value="">请选择</option>${execution.map(i=>`<option value="${i.id}" data-plan="${i.plan_id}" data-amount="${i.approved_amount}" data-purpose="${esc(i.business_purpose)}">${esc(i.item_no)} · ${esc(i.item_name)}（基线 ¥${money(i.approved_amount)}）</option>`).join('')}</select></div><div class="grid-2" style="margin-top:10px"><div><label>调整类型</label><select class="select" id="invAdjType"><option>金额调整</option><option>范围调整</option><option>金额与范围调整</option></select></div><div><label>调整后基线金额 *</label><input class="field" id="invAdjAmount" type="number" min="0"></div></div><div style="margin-top:10px"><label>调整后业务范围</label><textarea class="textarea" id="invAdjScope"></textarea></div><div style="margin-top:10px"><label>调整原因 *</label><textarea class="textarea" id="invAdjReason"></textarea></div><div class="modal-actions">${btn('取消','btn','invAdjCancel')}${btn('创建申请','btn primary','invAdjSave')}</div>`,true);
  $('#invAdjItem').addEventListener('change',()=>{const o=$('#invAdjItem').selectedOptions[0];$('#invAdjAmount').value=o?.dataset.amount||'';$('#invAdjScope').value=o?.dataset.purpose||''});$('#invAdjCancel').addEventListener('click',closeModal);
  $('#invAdjSave').addEventListener('click',async()=>{const o=$('#invAdjItem').selectedOptions[0];const payload={plan_id:Number(o?.dataset.plan),item_id:Number(o?.value),adjustment_type:$('#invAdjType').value,requested_amount:Number($('#invAdjAmount').value),scope_after:$('#invAdjScope').value.trim(),reason:$('#invAdjReason').value.trim()};if(!payload.item_id||!payload.reason)return toast('请选择投入项并填写调整原因','error');try{const r=await api('/api/investments/adjustments',{method:'POST',body:JSON.stringify(payload)});await api(`/api/investments/adjustments/${r.data.id}/submit`,{method:'POST'});closeModal();toast('调整申请已创建并提交','success');renderInvestmentAdjustments();}catch(e){toast(e.message,'error')}});
}

async function renderInvestmentAdjustments(){
  setPage({title:'投入调整申请',iconName:'sync',crumbs:['投入管理'],actions:hasPermission('investment.adjust')?btn('新建调整申请','btn primary','invNewAdjustment'):''});
  const rows=(await api('/api/investments/adjustments')).data;
  appView.innerHTML=`<section class="section"><div class="table-wrap"><table><thead><tr><th>调整单号</th><th>投入项</th><th>类型</th><th>原基线</th><th>调整后</th><th>变动</th><th>当前节点</th><th>状态</th><th>操作</th></tr></thead><tbody>${rows.length?rows.map(a=>`<tr><td><strong>${esc(a.adjustment_no)}</strong><small class="cell-sub">${esc(a.plan_no)}</small></td><td>${esc(a.item_no)}<small class="cell-sub">${esc(a.item_name)}</small></td><td>${esc(a.adjustment_type)}</td><td>¥${money(a.original_amount)}</td><td>¥${money(a.requested_amount)}</td><td class="${a.amount_delta>=0?'amount-up':'amount-down'}">${a.amount_delta>=0?'+':''}¥${money(a.amount_delta)}</td><td>${esc(a.current_node)}</td><td>${invStatus(a.status)}</td><td>${a.status==='审批中'&&hasPermission('investment.approve')?`<button class="link inv-adj-action" data-id="${a.id}" data-action="通过">通过</button> <button class="link danger inv-adj-action" data-id="${a.id}" data-action="驳回">驳回</button>`:'—'}</td></tr>`).join(''):`<tr><td colspan="9">${invEmpty('暂无调整申请')}</td></tr>`}</tbody></table></div></section>`;
  $('#invNewAdjustment')?.addEventListener('click',openInvestmentAdjustmentModal);$$('.inv-adj-action').forEach(b=>b.addEventListener('click',async()=>{const comment=prompt('请输入审批意见',b.dataset.action==='通过'?'同意调整':'请修改');if(comment===null)return;try{const r=await api(`/api/investments/adjustments/${b.dataset.id}/approve`,{method:'POST',body:JSON.stringify({action:b.dataset.action,comment})});toast(r.message,'success');renderInvestmentAdjustments()}catch(e){toast(e.message,'error')}}));
}

async function openInvestmentBindingModal(item,meta){
  showModal(`<h3 id="modalTitle">关联项目成本与合同</h3><div class="investment-selected"><strong>${esc(item.item_no)} · ${esc(item.item_name)}</strong><span>审核后额度 ¥${money(item.approved_amount)}</span></div><div class="grid-2"><div><label>关联项目</label><select class="select" id="invBindProject">${invOptions(meta.projects.map(p=>({...p,name:`${p.project_no} · ${p.name}`})),item.project_id,'不关联项目')}</select></div><div><label>关联合同</label><select class="select" id="invBindContract">${invOptions(meta.contracts.map(c=>({...c,name:`${c.contract_no} · ${c.name}`})),item.contract_id,'不关联合同')}</select></div></div><div style="margin-top:10px"><label>计划付款金额</label><input class="field" id="invBindPlanned" type="number" min="0" value="${item.planned_payment_amount||0}"></div><div class="modal-actions">${btn('取消','btn','invBindCancel')}${btn('保存关联','btn primary','invBindSave')}</div>`);
  $('#invBindCancel').addEventListener('click',closeModal);$('#invBindSave').addEventListener('click',async()=>{try{const r=await api(`/api/investments/items/${item.id}/binding`,{method:'PUT',body:JSON.stringify({project_id:Number($('#invBindProject').value)||null,contract_id:Number($('#invBindContract').value)||null,planned_payment_amount:Number($('#invBindPlanned').value||0)})});closeModal();toast(r.message,'success');renderInvestmentExecution()}catch(e){toast(e.message,'error')}});
}

function openInvestmentPaymentModal(item){
  showModal(`<h3 id="modalTitle">登记付款并核销</h3><div class="investment-selected"><strong>${esc(item.item_no)} · ${esc(item.item_name)}</strong><span>剩余可核销 ¥${money(item.remaining_amount)}</span></div><div class="grid-2"><div><label>付款类型 *</label><select class="select" id="invPayType"><option>普通费用</option><option ${item.contract_id?'selected':''}>合同付款</option></select></div><div><label>付款金额 *</label><input class="field" id="invPayAmount" type="number" min="0.01" max="${item.remaining_amount}" step="0.01"></div></div><div class="grid-2" style="margin-top:10px"><div><label>付款日期 *</label><input class="field" id="invPayDate" type="date" value="${new Date().toISOString().slice(0,10)}"></div><div><label>付款年份 *</label><input class="field" id="invPayYear" type="number" value="${item.plan_year}"></div></div><div class="grid-2" style="margin-top:10px"><div><label>付款单据号 *</label><input class="field" id="invPayDoc" placeholder="请输入唯一单据号"></div><div><label>支付方 *</label><input class="field" id="invPayPayer" value="${esc(item.payer)}"></div></div><div style="margin-top:10px"><label>备注</label><textarea class="textarea" id="invPayDesc"></textarea></div><div class="help">普通费用必须匹配投入年度；合同付款允许跨年，但必须关联合同且付款年份不早于投入年份。</div><div class="modal-actions">${btn('取消','btn','invPayCancel')}${btn('确认核销','btn primary','invPaySave')}</div>`,true);
  $('#invPayDate').addEventListener('change',()=>{$('#invPayYear').value=$('#invPayDate').value.slice(0,4)});$('#invPayCancel').addEventListener('click',closeModal);$('#invPaySave').addEventListener('click',async()=>{const payload={item_id:item.id,contract_id:item.contract_id||null,payment_type:$('#invPayType').value,payment_year:Number($('#invPayYear').value),amount:Number($('#invPayAmount').value),payment_date:$('#invPayDate').value,document_no:$('#invPayDoc').value.trim(),payer:$('#invPayPayer').value.trim(),description:$('#invPayDesc').value.trim()};if(!payload.amount||!payload.payment_date||!payload.document_no||!payload.payer)return toast('请完整填写付款核销信息','error');try{const r=await api('/api/investments/payments',{method:'POST',body:JSON.stringify(payload)});closeModal();toast(r.message,'success');renderInvestmentExecution()}catch(e){toast(e.message,'error')}});
}

async function renderInvestmentExecution(){
  setPage({title:'投入执行跟踪与核销',iconName:'project',crumbs:['投入管理']});const result=await api('/api/investments/execution'),rows=result.data,meta=result.meta;
  appView.innerHTML=`<section class="section"><div class="toolbar"><input class="field" id="invExecSearch" placeholder="搜索明细编号、名称、计划或项目"></div><div class="investment-execution-list" id="invExecRows"></div></section>`;
  const draw=()=>{const q=$('#invExecSearch').value.toLowerCase();const filtered=rows.filter(i=>!q||`${i.item_no}${i.item_name}${i.plan_no}${i.project_name||''}${i.contract_name||''}`.toLowerCase().includes(q));$('#invExecRows').innerHTML=filtered.length?filtered.map(i=>`<article class="investment-execution-row"><div class="investment-execution-title"><strong>${esc(i.item_no)} · ${esc(i.item_name)}</strong><span>${esc(i.plan_no)} / ${i.plan_year}</span><div>${i.project_no?`<b>${esc(i.project_no)} · ${esc(i.project_name)}</b>`:'未关联项目'}${i.contract_no?`<small>${esc(i.contract_no)} · ${esc(i.contract_name)}</small>`:'<small>未关联合同</small>'}</div></div><div class="investment-execution-money"><span>已核销 / 审核额度</span><strong>¥${money(i.written_off_amount)} <small>/ ¥${money(i.approved_amount)}</small></strong>${invBar(i.written_off_amount,i.approved_amount,i.execution_rate>=90?'orange':'green')}<p>剩余 ¥${money(i.remaining_amount)} · ${invPct(i.execution_rate)}</p></div><div class="investment-execution-actions">${invStatus(i.status)}${hasPermission('investment.execute')?`<button class="btn inv-bind" data-id="${i.id}">关联项目/合同</button>`:''}${(hasPermission('investment.finance')||hasPermission('investment.execute'))&&i.remaining_amount>0?`<button class="btn primary inv-pay" data-id="${i.id}">登记付款核销</button>`:''}</div></article>`).join(''):invEmpty('暂无已生效投入项');$$('.inv-bind').forEach(b=>b.addEventListener('click',()=>openInvestmentBindingModal(rows.find(i=>i.id===Number(b.dataset.id)),meta)));$$('.inv-pay').forEach(b=>b.addEventListener('click',()=>openInvestmentPaymentModal(rows.find(i=>i.id===Number(b.dataset.id)))))};$('#invExecSearch').addEventListener('input',draw);draw();
}

async function renderInvestmentWarnings(){
  setPage({title:'投入预警中心',iconName:'risk',crumbs:['投入管理']});const result=await api('/api/investments/warnings?status=');const open=result.data.filter(w=>w.status==='待处理'),resolved=result.data.filter(w=>w.status!=='待处理');
  appView.innerHTML=`<div class="investment-warning-summary"><div><span>待处理</span><strong>${open.length}</strong></div><div><span>严重预警</span><strong>${open.filter(w=>w.level==='严重').length}</strong></div><div><span>已处理/恢复</span><strong>${resolved.length}</strong></div><div><span>启用规则</span><strong>${result.rules.filter(r=>r.enabled).length}</strong></div></div><section class="section"><div class="section-head"><h3>当前预警</h3><button class="btn" id="invWarningConfig">查看预警规则</button></div><div class="investment-warning-list">${open.length?open.map(w=>`<article class="investment-warning ${w.level==='严重'?'danger':w.level==='预警'?'warn':''}"><i></i><div><div><strong>${esc(w.title)}</strong>${invStatus(w.level)}</div><p>${esc(w.content)}</p><small>${esc(w.plan_no||'')} ${esc(w.item_no||'')} · ${invDate(w.triggered_at)}</small></div><button class="btn inv-warning-resolve" data-id="${w.id}">标记已处理</button></article>`).join(''):invEmpty('当前无待处理预警')}</div></section>`;
  $('#invWarningConfig').addEventListener('click',()=>navigate('investment-settings'));$$('.inv-warning-resolve').forEach(b=>b.addEventListener('click',async()=>{try{const r=await api(`/api/investments/warnings/${b.dataset.id}/resolve`,{method:'POST'});toast(r.message,'success');renderInvestmentWarnings()}catch(e){toast(e.message,'error')}}));
}

function openInvestmentCategoryModal(category=null){
  showModal(`<h3 id="modalTitle">${category?'编辑分类标签':'新建分类标签'}</h3><div class="grid-2"><div><label>投入类别 *</label><input class="field" id="invCatName" value="${esc(category?.category_name||'')}"></div><div><label>子类别 *</label><input class="field" id="invSubcatName" value="${esc(category?.subcategory_name||'')}"></div></div><div style="margin-top:10px"><label>自定义标签（逗号分隔）</label><input class="field" id="invCatTags" value="${esc((category?.tags||[]).join(','))}"></div><div class="grid-2" style="margin-top:10px"><div><label>状态</label><select class="select" id="invCatStatus"><option ${category?.status!=='停用'?'selected':''}>启用</option><option ${category?.status==='停用'?'selected':''}>停用</option></select></div><div><label>排序</label><input class="field" id="invCatOrder" type="number" min="0" value="${category?.sort_order||0}"></div></div><div class="modal-actions">${btn('取消','btn','invCatCancel')}${btn('保存','btn primary','invCatSave')}</div>`);
  $('#invCatCancel').addEventListener('click',closeModal);$('#invCatSave').addEventListener('click',async()=>{const payload={category_name:$('#invCatName').value.trim(),subcategory_name:$('#invSubcatName').value.trim(),tags:$('#invCatTags').value.split(/[,，]/).map(x=>x.trim()).filter(Boolean),status:$('#invCatStatus').value,sort_order:Number($('#invCatOrder').value||0)};if(!payload.category_name||!payload.subcategory_name)return toast('请填写类别和子类别','error');try{const r=await api(category?`/api/investments/categories/${category.id}`:'/api/investments/categories',{method:category?'PUT':'POST',body:JSON.stringify(payload)});closeModal();toast(r.message,'success');renderInvestmentSettings()}catch(e){toast(e.message,'error')}});
}

async function renderInvestmentSettings(){
  setPage({title:'投入分类与预警配置',iconName:'settings',crumbs:['投入管理'],actions:btn('新建分类标签','btn primary','invNewCategory')});const [catResult,warningResult]=await Promise.all([api('/api/investments/categories'),api('/api/investments/warnings?status=')]);const categories=catResult.data,rules=warningResult.rules;
  appView.innerHTML=`<div class="investment-settings-grid"><section class="section"><div class="section-head"><h3>自定义投入分类</h3><span>${categories.length} 个子类别</span></div><div class="investment-category-list">${categories.map(c=>`<button class="investment-category inv-edit-category" data-id="${c.id}"><span><b>${esc(c.category_name)}</b><strong>${esc(c.subcategory_name)}</strong><small>${(c.tags||[]).map(t=>`#${esc(t)}`).join(' ')||'无自定义标签'}</small></span>${invStatus(c.status)}</button>`).join('')}</div></section><section class="section"><div class="section-head"><h3>预警规则</h3><span>修改后立即重新计算</span></div><div class="investment-rule-list">${rules.map(r=>`<div class="investment-rule" data-code="${r.code}"><div><strong>${esc(r.name)}</strong><small>${esc(r.description)}</small></div><input class="field inv-rule-threshold" type="number" value="${r.threshold_value}" title="百分比阈值"><input class="field inv-rule-days" type="number" value="${r.days_value}" title="天数阈值"><select class="select inv-rule-level"><option ${r.level==='提示'?'selected':''}>提示</option><option ${r.level==='预警'?'selected':''}>预警</option><option ${r.level==='严重'?'selected':''}>严重</option></select><label><input class="inv-rule-enabled" type="checkbox" ${r.enabled?'checked':''}> 启用</label><button class="btn inv-rule-save">保存</button></div>`).join('')}</div></section></div>`;
  $('#invNewCategory').addEventListener('click',()=>openInvestmentCategoryModal());$$('.inv-edit-category').forEach(b=>b.addEventListener('click',()=>openInvestmentCategoryModal(categories.find(c=>c.id===Number(b.dataset.id)))));$$('.inv-rule-save').forEach(b=>b.addEventListener('click',async()=>{const row=b.closest('.investment-rule');const payload={threshold_value:Number($('.inv-rule-threshold',row).value||0),days_value:Number($('.inv-rule-days',row).value||0),enabled:$('.inv-rule-enabled',row).checked,level:$('.inv-rule-level',row).value};try{const r=await api(`/api/investments/warning-rules/${row.dataset.code}`,{method:'PUT',body:JSON.stringify(payload)});toast(r.message,'success')}catch(e){toast(e.message,'error')}}));
}
