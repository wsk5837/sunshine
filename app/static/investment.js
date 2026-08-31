
/* Shared investment workbench: scoped layout, local filters, sorting and paging.
   Only rows fetched through the authenticated business API can be exported. */
function invShowModal(html, wide = true) {
  showModal(html, wide);
  $('#modalBox').classList.add('investment-modal');
}
function invMount(html) {
  appView.innerHTML = '<div class="investment-workbench">' + html + '</div>';
}
function invSum(rows, key) { return rows.reduce((sum, row) => sum + Number(row[key] || 0), 0); }
function invMoney(value) { return '¥' + money(value); }
function invTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? esc(value) : esc(date.toLocaleString('zh-CN', {hour12:false}));
}
function invKpis(cards) {
  return '<div class="investment-kpis">' + cards.map(([label, value, hint = '', tone = 'blue']) =>
    '<div class="investment-kpi '+tone+'"><span>'+esc(label)+'</span><strong title="'+esc(value)+'">'+esc(value)+'</strong><small>'+esc(hint)+'</small></div>').join('') + '</div>';
}
function invFacts(entries) {
  return '<dl class="inv-facts">'+entries.map(([label,value])=>'<div><dt>'+esc(label)+'</dt><dd>'+esc(value)+'</dd></div>').join('')+'</dl>';
}
function invCanApprove(node) {
  if (!hasPermission('investment.approve')) return false;
  const roles = {'部门负责人审批':['department_head'], '财务审批':['finance'], '分管领导审批':['vp','business_owner']};
  return (roles[node] || []).some(role => hasRole(role));
}
function invChoices(rows, key, label) {
  return {key, label, options:[...new Set(rows.map(r=>String(r[key] ?? '')).filter(Boolean))].sort((a,b)=>a.localeCompare(b,'zh-CN',{numeric:true}))};
}
function invFilterRows(rows, keyword, filters) {
  const query = keyword.trim().toLocaleLowerCase();
  return rows.filter(row => (!query || Object.values(row).filter(v=>typeof v==='string'||typeof v==='number').join(' ').toLocaleLowerCase().includes(query))
    && Object.entries(filters).every(([key,value])=>!value||String(row[key] ?? '')===value));
}
function invCsvCell(value) {
  let text = String(value ?? '');
  if (typeof value !== 'number' && /^[\s]*[=+@\-\t\r]/.test(text)) text = "'" + text;
  return '"' + text.replace(/"/g,'""') + '"';
}
function invExport(rows, columns, name) {
  const exportCols = columns.filter(c=>c.key);
  const csv = '\uFEFF' + [exportCols.map(c=>invCsvCell(c.title)).join(','), ...rows.map(row=>exportCols.map(c=>invCsvCell(c.value?c.value(row):row[c.key])).join(','))].join('\r\n');
  const url = URL.createObjectURL(new Blob([csv], {type:'text/csv;charset=utf-8;'}));
  const link = document.createElement('a');
  link.href=url; link.download=name+'.csv'; document.body.appendChild(link); link.click(); link.remove();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function invTable({host, rows, columns, filters=[], title='明细台账', tools='', summary=null, selectable=false, onSelection=()=>{}, onFilter=()=>{}, onDraw=()=>{}, empty='暂无记录'}) {
  const root = $(host), selected = new Set();
  let page=1, size=10, filtered=[], sorted=[], sortKey='', descending=false;
  root.innerHTML = '<section class="section inv-ledger"><div class="section-head"><h3>'+esc(title)+'</h3><div class="inv-actions">'+tools+'<button class="btn inv-export">导出筛选结果</button></div></div>'
    + '<div class="inv-filters"><label class="inv-search">关键词<input class="field inv-query" placeholder="搜索编号、名称或部门"></label>'
    + filters.map(f=>'<label>'+esc(f.label)+'<select class="select inv-filter" data-key="'+esc(f.key)+'"><option value="">全部'+esc(f.label)+'</option>'+f.options.map(v=>'<option value="'+esc(v)+'">'+esc(v)+'</option>').join('')+'</select></label>').join('')
    + '<button class="btn inv-reset">重置</button></div><div class="inv-result"></div><div class="table-wrap"><table class="table inv-table"><thead><tr>'
    +(selectable?'<th class="inv-check-col"><input type="checkbox" class="inv-all" aria-label="选择当前页"></th>':'')
    +columns.map(c=>'<th class="'+(c.numeric?'inv-num':'')+'"'+(c.width?' style="width:'+c.width+'px"':'')+'>'+(c.key?'<button class="inv-sort" data-key="'+esc(c.key)+'">'+esc(c.title)+' <span>↕</span></button>':esc(c.title))+'</th>').join('')
    +'</tr></thead><tbody></tbody></table></div><div class="inv-pagination"><span class="inv-page-label"></span><div class="inv-actions"><label>每页 <select class="select inv-size"><option>10</option><option>20</option><option>50</option></select> 条</label><button class="btn inv-prev">上一页</button><button class="btn inv-next">下一页</button></div></div></section>';
  function selectionChanged() {
    const visible=sorted.slice((page-1)*size,page*size), all=$('.inv-all',root);
    if(all) {all.checked=visible.length>0&&visible.every(r=>selected.has(r.id)); all.indeterminate=visible.some(r=>selected.has(r.id))&&!all.checked; all.disabled=!visible.length;}
    onSelection(rows.filter(r=>selected.has(r.id)));
  }
  function draw(reset=false) {
    if(reset) {page=1; selected.clear();}
    const values=Object.fromEntries($$('.inv-filter',root).map(el=>[el.dataset.key,el.value]));
    filtered=invFilterRows(rows,$('.inv-query',root).value,values);
    sorted=[...filtered];
    if(sortKey) sorted.sort((a,b)=>{
      const left=a[sortKey], right=b[sortKey];
      const comparison=typeof left==='number'&&typeof right==='number'?left-right:String(left??'').localeCompare(String(right??''),'zh-CN',{numeric:true});
      return descending?-comparison:comparison;
    });
    const pages=Math.max(1,Math.ceil(sorted.length/size)); page=Math.min(page,pages);
    const visible=sorted.slice((page-1)*size,page*size);
    $('tbody',root).innerHTML=visible.length?visible.map(row=>'<tr>'+(selectable?'<td class="inv-check-col"><input class="inv-row-check" type="checkbox" value="'+row.id+'" aria-label="选择 '+esc(row.plan_no||row.item_no||row.id)+'" '+(selected.has(row.id)?'checked':'')+'></td>':'')
      +columns.map(c=>'<td class="'+(c.numeric?'inv-num':'')+'">'+(c.render?c.render(row):esc(c.value?c.value(row):row[c.key]??'—'))+'</td>').join('')+'</tr>').join('')
      :'<tr><td colspan="'+(columns.length+(selectable?1:0))+'">'+invEmpty(empty)+'</td></tr>';
    $('.inv-result',root).textContent='共 '+filtered.length+' 条'+(filtered.length!==rows.length?' / 全部 '+rows.length+' 条':'');
    $('.inv-page-label',root).textContent='第 '+page+' / '+pages+' 页';
    $('.inv-prev',root).disabled=page<=1; $('.inv-next',root).disabled=page>=pages;
    $$('.inv-row-check',root).forEach(el=>el.addEventListener('change',()=>{el.checked?selected.add(Number(el.value)):selected.delete(Number(el.value));selectionChanged();}));
    $$('[data-inv-plan]',root).forEach(el=>el.addEventListener('click',()=>navigate('investment-plan-detail/'+el.dataset.invPlan)));
    $$('.inv-sort',root).forEach(el=>{el.setAttribute('aria-label',el.textContent.trim()+'，点击排序');el.querySelector('span').textContent=el.dataset.key===sortKey?(descending?'↓':'↑'):'↕';});
    if(summary) {
      let footer=$('.inv-ledger-total',root);
      if(!footer) {footer=document.createElement('div');footer.className='inv-ledger-total';$('.table-wrap',root).after(footer);}
      footer.textContent=summary(filtered);
    }
    selectionChanged(); onFilter(filtered); onDraw(visible,root);
  }
  $('.inv-query',root).addEventListener('input',()=>draw(true));
  $$('.inv-filter',root).forEach(el=>el.addEventListener('change',()=>draw(true)));
  $('.inv-reset',root).addEventListener('click',()=>{$('.inv-query',root).value='';$$('.inv-filter',root).forEach(el=>el.value='');draw(true);});
  $('.inv-size',root).addEventListener('change',el=>{size=Number(el.target.value);page=1;draw();});
  $('.inv-prev',root).addEventListener('click',()=>{page--;draw();});
  $('.inv-next',root).addEventListener('click',()=>{page++;draw();});
  $('.inv-all',root)?.addEventListener('change',el=>{sorted.slice((page-1)*size,page*size).forEach(row=>el.target.checked?selected.add(row.id):selected.delete(row.id));draw();});
  $$('.inv-sort',root).forEach(el=>el.addEventListener('click',()=>{descending=sortKey===el.dataset.key?!descending:false;sortKey=el.dataset.key;draw();}));
  $('.inv-export',root).addEventListener('click',()=>invExport(sorted,columns,title));
  draw();
  return {getSelected:()=>rows.filter(r=>selected.has(r.id)), getFiltered:()=>filtered};
}
function invPlanLink(row) {return '<button class="link" data-inv-plan="'+(row.plan_id||row.id)+'">'+esc(row.plan_no)+'</button><small class="cell-sub">'+esc(row.plan_name)+'</small>';}
function invTimeline(rows) {
  return rows.length?'<div class="investment-timeline">'+rows.map(a=>'<div><i></i><strong>'+esc(a.node)+' · '+esc(a.action)+'</strong><span>'+esc(a.approver)+' · '+invTime(a.created_at)+'</span><p>'+esc(a.comment||'—')+'</p></div>').join('')+'</div>':invEmpty('暂无审批记录');
}
async function invAction(button, action) {
  if(button.disabled) return;
  button.disabled=true;
  try {await action();} catch(error) {toast(error.message,'error');} finally {button.disabled=false;}
}

/* Digital investment management workbench (V5.1). Loaded before app.js so the
   SPA renderer can reference these functions while sharing the same global scope. */

function invPct(value) { return `${Number(value || 0).toFixed(1)}%`; }
function invDate(value) { return value ? String(value).slice(0, 10) : '—'; }
function invStatus(value) {
  const tone=['已生效','已完成','已核销','已恢复','启用','通过'].includes(value)?'success'
    :['已驳回','严重','驳回'].includes(value)?'danger'
    :['审批中','待财务确认','待处理','预警'].includes(value)?'warn'
    :['草稿','已处理','停用'].includes(value)?'gray':'';
  return '<span class="tag '+tone+'">'+esc(value||'—')+'</span>';
}

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
  invShowModal(`<h3 id="modalTitle">${plan ? '编辑投入计划' : '新建数字化投入计划'}</h3>
    <div class="grid-2"><div><label>计划名称 *</label><input class="field" id="invPlanName" value="${esc(plan?.plan_name || '')}" placeholder="如：${year}年数字化投入计划"></div><div><label>投入年度 *</label><input class="field" id="invPlanYear" type="number" min="2020" max="2100" value="${year}"></div></div>
    <div style="margin-top:10px"><label>提报部门 *</label><input class="field" id="invPlanDept" value="${esc(plan?.department || state.currentUserData?.department || '')}"></div>
    <div class="grid-4 investment-reference-grid" style="margin-top:10px"><div><label>上年预算</label><input class="field" id="invPriorBudget" type="number" min="0" value="${plan?.prior_year_budget || 0}"></div><div><label>上年实际</label><input class="field" id="invPriorActual" type="number" min="0" value="${plan?.prior_year_actual || 0}"></div><div><label>当年预算</label><input class="field" id="invCurrentBudget" type="number" min="0" value="${plan?.current_year_budget || 0}"></div><div><label>当年实际</label><input class="field" id="invCurrentActual" type="number" min="0" value="${plan?.current_year_actual || 0}"></div></div>
    <div class="investment-reference"><b>历史参考</b><button class="btn small" id="invLoadReference">读取历史投入</button><span id="invReferenceText">填写上年与当年执行数据后，系统会计算执行率用于下一年规划。</span></div>
    <div style="margin-top:10px"><label>编制说明</label><textarea class="textarea" id="invPlanDesc" placeholder="说明规划依据、总体目标与资源边界">${esc(plan?.description || '')}</textarea></div>
    <div class="modal-actions">${btn('取消', 'btn', 'invPlanCancel')}${btn('保存', 'btn primary', 'invPlanSave')}</div>`, true);
  const refreshReference = () => {
    const priorBudget = Number($('#invPriorBudget').value || 0), priorActual = Number($('#invPriorActual').value || 0);
    const currentBudget = Number($('#invCurrentBudget').value || 0), currentActual = Number($('#invCurrentActual').value || 0);
    $('#invReferenceText').textContent = `上年执行率 ${priorBudget ? (priorActual / priorBudget * 100).toFixed(1) : '0.0'}%；当年执行率 ${currentBudget ? (currentActual / currentBudget * 100).toFixed(1) : '0.0'}%。`;
  };
  ['invPriorBudget','invPriorActual','invCurrentBudget','invCurrentActual'].forEach((id) => $(`#${id}`).addEventListener('input', refreshReference)); refreshReference();

  $('#invLoadReference').addEventListener('click',event=>invAction(event.currentTarget,async()=>{
    const department=$('#invPlanDept').value.trim(),year=Number($('#invPlanYear').value);
    if(!department||!year)throw Error('请先填写年度和部门');
    const result=(await api('/api/investments/reference?plan_year='+year+'&department='+encodeURIComponent(department))).data;
    if(!result.prior.plan_count&&!result.current.plan_count)throw Error('该部门暂无对应年度的已生效投入记录，原填写值未改变');
    $('#invPriorBudget').value=result.prior.budget;$('#invPriorActual').value=result.prior.actual;
    $('#invCurrentBudget').value=result.current.budget;$('#invCurrentActual').value=result.current.actual;
    refreshReference();toast('已读取 '+result.prior.year+' / '+result.current.year+' 年投入台账','success');
  }));
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
  setPage({title:'投入分析驾驶舱', iconName:'grid', crumbs:['投入管理']});
  const items=(await api('/api/investments/items')).data;
  invMount('<div id="invBoardKpis"></div><div class="investment-dashboard-grid" id="invBoardCharts"></div><div id="invBoardTable"></div>');
  invTable({host:'#invBoardTable',rows:items,title:'投入明细总览',
    filters:[invChoices(items,'plan_year','年度'),invChoices(items,'department','部门'),invChoices(items,'category_name','类别'),invChoices(items,'plan_status','状态')],
    columns:[
      {key:'item_no',title:'明细编号 / 所属计划',width:210,render:r=>'<strong>'+esc(r.item_no)+'</strong><small class="cell-sub">'+invPlanLink(r)+'</small>'},
      {key:'item_name',title:'投入项',render:r=>'<strong>'+esc(r.item_name)+'</strong><small class="cell-sub">'+(r.is_new?'新增投入':'存量投入')+(r.is_unplanned_reserve?' · 计划外候补':'')+'</small>'},
      {key:'department',title:'年度 / 部门',render:r=>r.plan_year+'<small class="cell-sub">'+esc(r.department)+'</small>'},
      {key:'category_name',title:'分类',render:r=>esc(r.category_name)+'<small class="cell-sub">'+esc(r.subcategory_name)+'</small>'},
      {key:'application_amount',title:'申请金额',numeric:true,render:r=>invMoney(r.application_amount)},
      {key:'approved_amount',title:'核定金额',numeric:true,render:r=>['已生效','待财务确认'].includes(r.plan_status)?invMoney(r.approved_amount):'<span class="muted">待审核</span>'},
      {key:'written_off_amount',title:'已核销',numeric:true,render:r=>invMoney(r.written_off_amount)},
      {key:'plan_status',title:'状态',render:r=>invStatus(r.plan_status)}
    ],onFilter:rows=>{
      const effective=rows.filter(r=>r.plan_status==='已生效'), amount=invSum(effective,'approved_amount'), used=invSum(effective,'written_off_amount');
      $('#invBoardKpis').innerHTML=invKpis([['投入明细',rows.length,new Set(rows.map(r=>r.plan_id)).size+' 个计划'],['申请金额',invMoney(invSum(rows,'application_amount')),'当前筛选范围','purple'],['已生效额度',invMoney(amount),'仅统计已生效投入','cyan'],['已核销金额',invMoney(used),'执行率 '+invPct(amount?used/amount*100:0),'green']]);
      const chart=(key,title)=>{
        const groups={}; rows.forEach(r=>{const label=r[key]||'未分类';groups[label]=(groups[label]||0)+Number(r.application_amount||0);});
        const values=Object.entries(groups).sort((a,b)=>b[1]-a[1]), max=Math.max(1,...values.map(([,v])=>v));
        return '<section class="section investment-chart"><div class="section-head"><h3>'+title+'</h3><span>申请金额 · 前6项</span></div>'+ (values.length?values.slice(0,6).map(([label,v])=>'<div class="investment-chart-row"><span title="'+esc(label)+'">'+esc(label)+'</span>'+invBar(v,max)+'<b>'+invMoney(v)+'</b></div>').join(''):invEmpty())+'</section>';
      };
      $('#invBoardCharts').innerHTML=chart('category_name','类别分布')+chart('department','部门投入');
    }
  });
}

async function renderInvestmentPlans() {
  setPage({title:'投入计划编制',iconName:'grid',crumbs:['投入管理'],actions:hasPermission('investment.create')?btn('新建投入计划','btn primary','invNewPlan'):''});
  const rows=(await api('/api/investments/plans')).data;
  invMount('<div id="invPlansLedger"></div>');
  invTable({host:'#invPlansLedger',rows,title:'年度投入计划',
    filters:[invChoices(rows,'plan_year','年度'),invChoices(rows,'department','部门'),invChoices(rows,'status','状态')],
    columns:[
      {key:'plan_no',title:'计划编号',width:190,render:r=>'<button class="link" data-inv-plan="'+r.id+'">'+esc(r.plan_no)+'</button>'},
      {key:'plan_name',title:'计划名称',render:r=>'<strong>'+esc(r.plan_name)+'</strong><small class="cell-sub">提报人 · '+esc(r.applicant)+'</small>'},
      {key:'department',title:'年度 / 部门',render:r=>r.plan_year+'<small class="cell-sub">'+esc(r.department)+'</small>'},
      {key:'application_total',title:'申请金额',numeric:true,render:r=>invMoney(r.application_total)},
      {key:'approved_total',title:'核定金额',numeric:true,render:r=>['已生效','待财务确认'].includes(r.status)?invMoney(r.approved_total):'—'},
      {key:'current_node',title:'当前节点'},{key:'status',title:'状态',render:r=>invStatus(r.status)},
      {title:'操作',render:r=>'<div class="inv-actions"><button class="link" data-inv-plan="'+r.id+'">详情</button>'+(['草稿','已驳回'].includes(r.status)&&hasPermission('investment.create')?'<button class="link inv-plan-edit" data-id="'+r.id+'">编辑</button>':'')+'</div>'}
    ],summary:filtered=>'筛选合计：申请金额 '+invMoney(invSum(filtered,'application_total')),
    onDraw:(_,root)=>$$('.inv-plan-edit',root).forEach(b=>b.addEventListener('click',()=>openInvestmentPlanModal(rows.find(r=>r.id===Number(b.dataset.id)))))
  });
  $('#invNewPlan')?.addEventListener('click',()=>openInvestmentPlanModal());
}

async function openInvestmentItemModal(plan, item = null) {
  const categories = (await api('/api/investments/categories')).data;
  invShowModal(`<h3 id="modalTitle">${item ? '编辑投入明细' : '新增投入明细'}</h3>
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
  const plan=(await api('/api/investments/plans/'+id)).data;
  const editable=['草稿','已驳回'].includes(plan.status)&&hasPermission('investment.create');
  setPage({title:'投入计划详情',iconName:'grid',crumbs:['投入管理',plan.plan_no],actions:btn('返回列表','btn','invBack')+(editable?btn('编辑计划','btn','invEditPlan')+btn('新增明细','btn primary','invAddItem'):'')});
  invMount('<div class="investment-plan-head"><div><span>'+esc(plan.plan_no)+'</span><h2>'+esc(plan.plan_name)+'</h2><p>'+plan.plan_year+' 年 · '+esc(plan.department)+' · 提报人 '+esc(plan.applicant)+'</p></div><div>'+invStatus(plan.status)+'<small>'+esc(plan.current_node)+'</small></div></div>'
    +invFacts([['申请金额',invMoney(plan.application_total)],['核定金额',['草稿','已驳回'].includes(plan.status)?'待审核':invMoney(plan.approved_total)],['已核销',invMoney(invSum(plan.items,'written_off_amount'))],['候补投入',plan.items.filter(i=>i.is_unplanned_reserve).length+' 项']])
    +'<div id="invDetailItems"></div><div class="investment-detail-grid"><section class="section"><div class="section-head"><h3>编制依据</h3></div><p class="investment-description">'+esc(plan.description||'暂无说明')+'</p><div class="investment-reference">上年预算 '+invMoney(plan.prior_year_budget)+' · 实际 '+invMoney(plan.prior_year_actual)+'<br>当年预算 '+invMoney(plan.current_year_budget)+' · 已执行 '+invMoney(plan.current_year_actual)+'</div></section><section class="section"><div class="section-head"><h3>审批记录</h3><span>'+plan.approvals.length+' 条</span></div>'+invTimeline(plan.approvals)+'</section></div>'
    +(editable?'<div class="investment-submit-bar"><div><strong>'+plan.items.length+' 条明细 · '+invMoney(plan.application_total)+'</strong><span>核对后提交部门负责人审批</span></div><button class="btn primary" id="invSubmitPlan" '+(!plan.items.length?'disabled':'')+'>提交审批</button></div>':''));
  invTable({host:'#invDetailItems',rows:plan.items,title:'投入明细',filters:[invChoices(plan.items,'category_name','类别')],
    columns:[{key:'item_name',title:'投入项 / 用途',render:r=>'<strong>'+esc(r.item_name)+'</strong><small class="cell-sub">'+esc(r.item_no)+'</small><small class="cell-sub">'+esc(r.business_purpose)+'</small>'},{key:'category_name',title:'分类',render:r=>esc(r.category_name)+'<small class="cell-sub">'+esc(r.subcategory_name)+'</small>'},{title:'标记',render:r=>(r.is_new?'<span class="tag success">新增</span>':'<span class="tag gray">存量</span>')+(r.is_unplanned_reserve?' <span class="tag warn">候补</span>':'')},{key:'quantity',title:'数量',numeric:true,render:r=>r.quantity+' '+esc(r.unit)},{key:'application_amount',title:'申请金额',numeric:true,render:r=>invMoney(r.application_amount)},{key:'approved_amount',title:'当前核定',numeric:true,render:r=>['草稿','已驳回'].includes(plan.status)?'待审核':invMoney(r.approved_amount)},{key:'payer',title:'支付方'},{key:'end_date',title:'计划周期',render:r=>invDate(r.start_date)+'<small class="cell-sub">至 '+invDate(r.end_date)+'</small>'},{title:'操作',render:r=>editable?'<div class="inv-actions"><button class="link inv-edit-item" data-id="'+r.id+'">编辑</button><button class="link danger inv-delete-item" data-id="'+r.id+'">删除</button></div>':'—'}],
    onDraw:(_,root)=>{
      $$('.inv-edit-item',root).forEach(b=>b.addEventListener('click',()=>openInvestmentItemModal(plan,plan.items.find(r=>r.id===Number(b.dataset.id))).catch(e=>toast(e.message,'error'))));
      $$('.inv-delete-item',root).forEach(b=>b.addEventListener('click',()=>invAction(b,async()=>{
        if(!confirm('确定删除这条草稿投入明细？'))return;
        await api('/api/investments/items/'+b.dataset.id,{method:'DELETE'});toast('明细已删除','success');await renderInvestmentPlanDetail(id);
      })));
    }
  });
  $('#invBack').addEventListener('click',()=>navigate('investment-plans'));
  $('#invEditPlan')?.addEventListener('click',()=>openInvestmentPlanModal(plan));
  $('#invAddItem')?.addEventListener('click',()=>openInvestmentItemModal(plan).catch(e=>toast(e.message,'error')));
  $('#invSubmitPlan')?.addEventListener('click',e=>invAction(e.currentTarget,async()=>{
    if(!confirm('确认提交该计划及全部投入明细进入审批？'))return;
    const r=await api('/api/investments/plans/'+id+'/submit',{method:'POST'});toast(r.message,'success');await renderInvestmentPlanDetail(id);
  }));
}

async function renderInvestmentApprovals() {
  const canReview=hasPermission('investment.approve'),canFinance=hasPermission('investment.finance')&&hasRole('finance');
  setPage({title:'投入审批与财务确认',iconName:'approve',crumbs:['投入管理'],actions:canFinance?'<button class="btn" data-protected-download="/api/investments/finance/export" data-filename="数字化投入财务复核.xlsx">导出财务复核资料</button>':''});
  const pending=canReview?(await api('/api/investments/approvals/pending')).data:[];
  const finance=canFinance?(await api('/api/investments/plans')).data.filter(r=>r.status==='待财务确认'):[];
  invMount('<div class="inv-tabs" role="tablist">'+(canReview?'<button class="btn" data-queue="approval" role="tab">我的待办审批（'+pending.length+'）</button>':'')+(canFinance?'<button class="btn" data-queue="finance" role="tab">财务复核（'+finance.length+'）</button>':'')+'</div><div id="invQueue"></div>');
  function drawQueue(type) {
    $$('[data-queue]').forEach(b=>{b.classList.toggle('primary',b.dataset.queue===type);b.setAttribute('aria-selected',String(b.dataset.queue===type));});
    const rows=type==='finance'?finance:pending;
    invTable({host:'#invQueue',rows,title:type==='finance'?'财务复核队列':'我的待办审批',selectable:true,
      filters:[invChoices(rows,'plan_year','年度'),invChoices(rows,'department','部门'),invChoices(rows,'current_node','节点')],
      tools:'<span id="invSelectedCount">已选 0 条</span><button class="btn primary" id="invQueueApprove" disabled>批量通过</button><button class="btn danger" id="invQueueReject" disabled>批量驳回</button>',
      columns:[{key:'plan_no',title:'计划',render:invPlanLink},{key:'department',title:'年度 / 部门',render:r=>r.plan_year+'<small class="cell-sub">'+esc(r.department)+'</small>'},{key:'application_total',title:'申请金额',numeric:true,render:r=>invMoney(r.application_total)},{key:'approved_total',title:'当前核定',numeric:true,render:r=>invMoney(r.approved_total)},{key:'current_node',title:'当前节点'},{key:'applicant',title:'提报人'},{title:'操作',render:r=>'<button class="link inv-review-one" data-id="'+r.id+'">核定与审批</button>'}],
      onSelection:selected=>{ $('#invSelectedCount').textContent='已选 '+selected.length+' 条'; ['#invQueueApprove','#invQueueReject'].forEach(id=>$(id).disabled=!selected.length);
        $('#invQueueApprove').onclick=()=>openInvestmentReview(selected,type,'通过').catch(e=>toast(e.message,'error'));$('#invQueueReject').onclick=()=>openInvestmentReview(selected,type,'驳回').catch(e=>toast(e.message,'error')); },
      onDraw:(_,root)=>$$('.inv-review-one',root).forEach(b=>b.addEventListener('click',()=>openInvestmentReview([rows.find(r=>r.id===Number(b.dataset.id))],type).catch(e=>toast(e.message,'error')))),
      empty:type==='finance'?'暂无待财务复核的计划':'当前没有待办审批'
    });
  }
  $$('[data-queue]').forEach(b=>b.addEventListener('click',()=>drawQueue(b.dataset.queue)));
  if(canReview||canFinance)drawQueue(canReview?'approval':'finance');
}

async function openInvestmentAdjustmentModal(existing=null) {
  const rows=(await api('/api/investments/execution')).data;
  if(!rows.length)return toast('暂无已生效投入项可调整','error');
  let savedId=existing?.id;
  invShowModal('<h3>'+(existing?'编辑':'新建')+'投入调整申请</h3><div class="inv-form-grid">'
    +'<label class="inv-span-all">已生效投入项 *<select class="select" id="invAdjItem" '+(existing?'disabled':'')+'><option value="">请选择投入项</option>'+rows.map(i=>'<option value="'+i.id+'" '+(i.id===existing?.item_id?'selected':'')+'>'+esc(i.item_no)+' · '+esc(i.item_name)+'</option>').join('')+'</select></label>'
    +'<label>调整类型<select class="select" id="invAdjType">'+['金额调整','范围调整','金额与范围调整'].map(v=>'<option '+(existing?.adjustment_type===v?'selected':'')+'>'+v+'</option>').join('')+'</select></label><label>调整后金额（元） *<input class="field" id="invAdjAmount" type="number" min="0" step="0.01"></label>'
    +'<div class="investment-reference inv-span-all" id="invAdjCompare">选择投入项后展示基线与已核销金额</div>'
    +'<label class="inv-span-all">调整后业务范围<textarea class="textarea" id="invAdjScope" maxlength="2000"></textarea></label><label class="inv-span-all">调整原因 *<textarea class="textarea" id="invAdjReason" maxlength="2000">'+esc(existing?.reason||'')+'</textarea></label></div>'
    +'<div class="modal-actions"><button class="btn" id="invAdjCancel">取消</button><button class="btn" id="invAdjDraft">保存草稿</button><button class="btn primary" id="invAdjSave">保存并提交</button></div>');
  const compare=()=>{
    const item=rows.find(i=>i.id===Number($('#invAdjItem').value));
    if(!item)return;
    const delta=Number($('#invAdjAmount').value)-item.approved_amount;
    $('#invAdjCompare').textContent='当前基线 '+invMoney(item.approved_amount)+' · 已核销 '+invMoney(item.written_off_amount)+' · 变动 '+invMoney(delta)+(Math.abs(delta)>50000?' · 需分管领导审批':'');
  };
  const select=()=>{const item=rows.find(i=>i.id===Number($('#invAdjItem').value));$('#invAdjAmount').value=existing?.requested_amount??item?.approved_amount??'';$('#invAdjScope').value=existing?.scope_after??item?.business_purpose??'';compare();};
  $('#invAdjItem').addEventListener('change',select);$('#invAdjAmount').addEventListener('input',compare);select();
  $('#invAdjCancel').addEventListener('click',closeModal);
  async function save(submit) {
    const item=rows.find(i=>i.id===Number($('#invAdjItem').value));
    const amount=$('#invAdjAmount'),reason=$('#invAdjReason').value.trim();
    if(!item||!reason||!amount.value||!amount.checkValidity())throw Error('请选择投入项，填写有效金额及调整原因');
    if(Number(amount.value)<Number(item.written_off_amount))throw Error('调整后金额不能小于已核销金额');
    const payload={plan_id:item.plan_id,item_id:item.id,adjustment_type:$('#invAdjType').value,requested_amount:Number(amount.value),scope_after:$('#invAdjScope').value.trim(),reason};
    const result=await api('/api/investments/adjustments'+(savedId?'/'+savedId:''),{method:savedId?'PUT':'POST',body:JSON.stringify(payload)});
    savedId=result.data.id;
    if(submit)await api('/api/investments/adjustments/'+savedId+'/submit',{method:'POST'});
    closeModal();toast(submit?'调整申请已提交':'调整申请已保存','success');await renderInvestmentAdjustments();
  }
  $('#invAdjDraft').addEventListener('click',e=>invAction(e.currentTarget,()=>save(false)));
  $('#invAdjSave').addEventListener('click',e=>invAction(e.currentTarget,()=>save(true)));
}

async function renderInvestmentAdjustments() {
  setPage({title:'投入调整申请',iconName:'sync',crumbs:['投入管理'],actions:hasPermission('investment.adjust')?btn('新建调整申请','btn primary','invNewAdjustment'):''});
  const rows=(await api('/api/investments/adjustments')).data;
  invMount('<div id="invAdjLedger"></div>');
  invTable({host:'#invAdjLedger',rows,title:'投入调整台账',filters:[invChoices(rows,'status','状态'),invChoices(rows,'adjustment_type','调整类型'),invChoices(rows,'current_node','审批节点')],
    columns:[{key:'adjustment_no',title:'调整单号',render:r=>'<strong>'+esc(r.adjustment_no)+'</strong><small class="cell-sub">'+esc(r.plan_no)+'</small>'},{key:'item_name',title:'投入项',render:r=>esc(r.item_name)+'<small class="cell-sub">'+esc(r.item_no)+'</small>'},{key:'original_amount',title:'原基线',numeric:true,render:r=>invMoney(r.original_amount)},{key:'requested_amount',title:'申请调整后',numeric:true,render:r=>invMoney(r.requested_amount)},{key:'amount_delta',title:'变动金额',numeric:true,render:r=>'<span class="'+(r.amount_delta>0?'amount-up':'amount-down')+'">'+(r.amount_delta>0?'+':'')+invMoney(r.amount_delta)+'</span>'},{key:'current_node',title:'当前节点'},{key:'status',title:'状态',render:r=>invStatus(r.status)},{title:'操作',render:r=>'<div class="inv-actions"><button class="link inv-adj-detail" data-id="'+r.id+'">详情</button>'+(r.status==='审批中'&&invCanApprove(r.current_node)?'<button class="link inv-adj-review" data-id="'+r.id+'">审批</button>':'')+(['草稿','已驳回'].includes(r.status)&&hasPermission('investment.adjust')?'<button class="link inv-adj-edit" data-id="'+r.id+'">编辑并提交</button>':'')+'</div>'}],
    summary:list=>'筛选合计：已生效调整 '+list.filter(r=>r.status==='已生效').length+' 笔 · 变动净额 '+invMoney(invSum(list.filter(r=>r.status==='已生效'),'amount_delta')),
    onDraw:(_,root)=>{for(const [selector,fn] of [['.inv-adj-detail',r=>openInvestmentAdjustmentDetail(r.id)],['.inv-adj-edit',openInvestmentAdjustmentModal],['.inv-adj-review',openInvestmentAdjustmentReview]])$$(selector,root).forEach(b=>b.addEventListener('click',()=>fn(rows.find(r=>r.id===Number(b.dataset.id))).catch(e=>toast(e.message,'error'))));}
  });
  $('#invNewAdjustment')?.addEventListener('click',()=>openInvestmentAdjustmentModal().catch(e=>toast(e.message,'error')));
}

async function openInvestmentBindingModal(item,meta) {
  invShowModal('<h3>关联项目成本与合同</h3><div class="investment-selected"><strong>'+esc(item.item_name)+'</strong><span>核定额度 '+invMoney(item.approved_amount)+'</span></div><div class="inv-form-grid"><label>关联项目<select class="select" id="invBindProject">'+invOptions(meta.projects.map(p=>({...p,name:p.project_no+' · '+p.name})),item.project_id,'不关联项目')+'</select></label><label>关联合同<select class="select" id="invBindContract"></select></label><label>计划付款金额（元）<input class="field" id="invBindPlanned" type="number" min="0" step="0.01" value="'+item.planned_payment_amount+'"></label></div><div class="modal-actions"><button class="btn" id="invBindCancel">取消</button><button class="btn primary" id="invBindSave">保存关联</button></div>');
  const refreshContracts=selected=>{const projectId=Number($('#invBindProject').value);const contracts=meta.contracts.filter(c=>!projectId||!c.project_id||c.project_id===projectId);$('#invBindContract').innerHTML=invOptions(contracts.map(c=>({...c,name:c.contract_no+' · '+c.name})),selected,'不关联合同');};
  refreshContracts(item.contract_id);
  $('#invBindProject').addEventListener('change',()=>refreshContracts(Number($('#invBindContract').value)));
  $('#invBindCancel').addEventListener('click',closeModal);
  $('#invBindSave').addEventListener('click',e=>invAction(e.currentTarget,async()=>{
    if(!$('#invBindPlanned').checkValidity())throw Error('请输入有效的计划付款金额');
    const r=await api('/api/investments/items/'+item.id+'/binding',{method:'PUT',body:JSON.stringify({project_id:Number($('#invBindProject').value)||null,contract_id:Number($('#invBindContract').value)||null,planned_payment_amount:Number($('#invBindPlanned').value||0)})});
    closeModal();toast(r.message,'success');await renderInvestmentExecution();
  }));
}

function openInvestmentPaymentModal(item) {
  const today=new Date(),day=today.getFullYear()+'-'+String(today.getMonth()+1).padStart(2,'0')+'-'+String(today.getDate()).padStart(2,'0');
  invShowModal('<h3>登记付款并核销</h3><div class="investment-selected"><strong>'+esc(item.item_name)+'</strong><span>剩余可核销 '+invMoney(item.remaining_amount)+'</span></div>'
    +'<div class="inv-form-grid"><label>付款类型 *<select class="select" id="invPayType"><option>普通费用</option>'+(item.contract_id?'<option selected>合同付款</option>':'')+'</select></label><label>付款金额（元） *<input class="field" id="invPayAmount" type="number" min="0.01" max="'+item.remaining_amount+'" step="0.01"></label>'
    +'<label>付款日期 *<input class="field" id="invPayDate" type="date" value="'+day+'"></label><label>付款年份<input class="field" id="invPayYear" readonly value="'+today.getFullYear()+'"></label><label>付款单据号 *<input class="field" id="invPayDoc" maxlength="120" placeholder="唯一付款单据编号"></label><label>支付方 *<input class="field" id="invPayPayer" maxlength="120" value="'+esc(item.payer)+'"></label><label class="inv-span-all">备注<textarea class="textarea" id="invPayDesc" maxlength="1000"></textarea></label></div>'
    +'<div class="investment-reference" id="invPayPreview">选择金额后展示剩余额度</div><div class="help">投入年度 '+item.plan_year+'。普通费用须在同年核销；合同付款可跨年但不得早于投入年度。</div><div class="modal-actions"><button class="btn" id="invPayCancel">取消</button><button class="btn primary" id="invPaySave">确认核销</button></div>');
  $('#invPayDate').addEventListener('change',()=>$('#invPayYear').value=$('#invPayDate').value.slice(0,4));
  $('#invPayAmount').addEventListener('input',()=>$('#invPayPreview').textContent='本次核销 '+invMoney(Number($('#invPayAmount').value||0))+' · 核销后剩余 '+invMoney(item.remaining_amount-Number($('#invPayAmount').value||0)));
  $('#invPayCancel').addEventListener('click',closeModal);
  $('#invPaySave').addEventListener('click',e=>invAction(e.currentTarget,async()=>{
    const payload={item_id:item.id,contract_id:item.contract_id||null,payment_type:$('#invPayType').value,payment_year:Number($('#invPayYear').value),amount:Number($('#invPayAmount').value),payment_date:$('#invPayDate').value,document_no:$('#invPayDoc').value.trim(),payer:$('#invPayPayer').value.trim(),description:$('#invPayDesc').value.trim()};
    if(!payload.amount||!$('#invPayAmount').checkValidity()||!payload.payment_date||!payload.document_no||!payload.payer)throw Error('请填写完整信息，核销金额不得超过剩余额度');
    if(payload.payment_type==='普通费用'&&payload.payment_year!==item.plan_year)throw Error('普通费用付款日期必须属于投入年度 '+item.plan_year);
    if(payload.payment_year<item.plan_year)throw Error('付款日期不能早于投入年度');
    const r=await api('/api/investments/payments',{method:'POST',body:JSON.stringify(payload)});
    closeModal();toast(r.message,'success');await renderInvestmentExecution();
  }));
}

async function renderInvestmentExecution() {
  setPage({title:'投入执行跟踪与核销',iconName:'project',crumbs:['投入管理']});
  const result=await api('/api/investments/execution'),rows=result.data,meta=result.meta;
  const canBind=hasPermission('investment.execute')&&['project_manager','finance','product_manager'].some(hasRole);
  const canPay=(hasPermission('investment.finance')||hasPermission('investment.execute'))&&['finance','project_manager'].some(hasRole);
  invMount('<div id="invExecLedger"></div>');
  invTable({host:'#invExecLedger',rows,title:'执行与核销台账',filters:[invChoices(rows,'plan_year','年度'),invChoices(rows,'department','部门'),invChoices(rows,'status','执行状态')],
    columns:[
      {key:'item_name',title:'投入项',render:r=>'<strong>'+esc(r.item_name)+'</strong><small class="cell-sub">'+esc(r.item_no)+'</small>'},
      {key:'project_name',title:'关联项目 / 合同',render:r=>esc(r.project_name||'未关联项目')+'<small class="cell-sub">'+esc(r.contract_name||'未关联合同')+'</small>'},
      {key:'approved_amount',title:'核定额度',numeric:true,render:r=>invMoney(r.approved_amount)},
      {key:'planned_payment_amount',title:'计划付款',numeric:true,render:r=>invMoney(r.planned_payment_amount)},
      {key:'written_off_amount',title:'已核销',numeric:true,render:r=>invMoney(r.written_off_amount)},
      {key:'remaining_amount',title:'剩余未核销',numeric:true,render:r=>invMoney(r.remaining_amount)},
      {key:'execution_rate',title:'执行进度',width:130,render:r=>'<div class="inv-progress-cell">'+invBar(r.written_off_amount,r.approved_amount,r.execution_rate>=90?'orange':'green')+'<small>'+invPct(r.execution_rate)+'</small></div>'},
      {title:'操作',width:190,render:r=>'<div class="inv-actions">'+((hasPermission('investment.finance')||hasPermission('investment.execute'))?'<button class="link inv-payment-history" data-id="'+r.id+'">核销流水</button>':'')+(canBind?'<button class="link inv-bind" data-id="'+r.id+'">关联</button>':'')+(canPay&&r.remaining_amount>0?'<button class="link inv-pay" data-id="'+r.id+'">登记付款</button>':'')+'</div>'}
    ],summary:list=>'筛选合计：核定 '+invMoney(invSum(list,'approved_amount'))+' · 计划付款 '+invMoney(invSum(list,'planned_payment_amount'))+' · 已核销 '+invMoney(invSum(list,'written_off_amount'))+' · 剩余 '+invMoney(invSum(list,'remaining_amount')),
    onDraw:(_,root)=>{
      $$('.inv-bind',root).forEach(b=>b.addEventListener('click',()=>openInvestmentBindingModal(rows.find(r=>r.id===Number(b.dataset.id)),meta).catch(e=>toast(e.message,'error'))));
      $$('.inv-pay',root).forEach(b=>b.addEventListener('click',()=>openInvestmentPaymentModal(rows.find(r=>r.id===Number(b.dataset.id)))));
      $$('.inv-payment-history',root).forEach(b=>b.addEventListener('click',()=>openInvestmentPaymentHistory(rows.find(r=>r.id===Number(b.dataset.id))).catch(e=>toast(e.message,'error'))));
    }
  });
}

async function renderInvestmentWarnings() {
  setPage({title:'投入预警中心',iconName:'risk',crumbs:['投入管理'],actions:hasPermission('investment.config')?btn('预警规则配置','btn','invWarningConfig'):''});
  const result=await api('/api/investments/warnings?status='),rows=result.data;
  const canResolve=['investment.execute','investment.finance','investment.config'].some(hasPermission);
  invMount('<div id="invWarnLedger"></div>');
  invTable({host:'#invWarnLedger',rows,title:'预警与处理记录',filters:[invChoices(rows,'status','状态'),invChoices(rows,'level','等级'),invChoices(rows,'title','预警类型')],
    columns:[{key:'level',title:'等级',width:80,render:r=>invStatus(r.level)},{key:'title',title:'预警内容',render:r=>'<strong>'+esc(r.title)+'</strong><small class="cell-sub inv-warning-content">'+esc(r.content)+'</small>'},{key:'plan_no',title:'关联计划 / 投入项',render:r=>invPlanLink(r)+'<small class="cell-sub">'+esc(r.item_no)+'</small>'},{key:'triggered_at',title:'触发时间',render:r=>invTime(r.triggered_at)},{key:'status',title:'处理状态',render:r=>invStatus(r.status)+'<small class="cell-sub">'+(r.resolved_at?invTime(r.resolved_at):'')+'</small>'},{title:'操作',render:r=>r.status==='待处理'&&canResolve?'<button class="link inv-warning-resolve" data-id="'+r.id+'">标记已处理</button>':'—'}],
    onDraw:(_,root)=>$$('.inv-warning-resolve',root).forEach(b=>b.addEventListener('click',()=>invAction(b,async()=>{
      const r=await api('/api/investments/warnings/'+b.dataset.id+'/resolve',{method:'POST'});toast(r.message,'success');await renderInvestmentWarnings();
    })))
  });
  $('#invWarningConfig')?.addEventListener('click',()=>navigate('investment-settings'));
}

function openInvestmentCategoryModal(category=null){
  invShowModal(`<h3 id="modalTitle">${category?'编辑分类标签':'新建分类标签'}</h3><div class="grid-2"><div><label>投入类别 *</label><input class="field" id="invCatName" value="${esc(category?.category_name||'')}"></div><div><label>子类别 *</label><input class="field" id="invSubcatName" value="${esc(category?.subcategory_name||'')}"></div></div><div style="margin-top:10px"><label>自定义标签（逗号分隔）</label><input class="field" id="invCatTags" value="${esc((category?.tags||[]).join(','))}"></div><div class="grid-2" style="margin-top:10px"><div><label>状态</label><select class="select" id="invCatStatus"><option ${category?.status!=='停用'?'selected':''}>启用</option><option ${category?.status==='停用'?'selected':''}>停用</option></select></div><div><label>排序</label><input class="field" id="invCatOrder" type="number" min="0" value="${category?.sort_order||0}"></div></div><div class="modal-actions">${btn('取消','btn','invCatCancel')}${btn('保存','btn primary','invCatSave')}</div>`);
  $('#invCatCancel').addEventListener('click',closeModal);$('#invCatSave').addEventListener('click',async()=>{const payload={category_name:$('#invCatName').value.trim(),subcategory_name:$('#invSubcatName').value.trim(),tags:$('#invCatTags').value.split(/[,，]/).map(x=>x.trim()).filter(Boolean),status:$('#invCatStatus').value,sort_order:Number($('#invCatOrder').value||0)};if(!payload.category_name||!payload.subcategory_name)return toast('请填写类别和子类别','error');try{const r=await api(category?`/api/investments/categories/${category.id}`:'/api/investments/categories',{method:category?'PUT':'POST',body:JSON.stringify(payload)});closeModal();toast(r.message,'success');renderInvestmentSettings()}catch(e){toast(e.message,'error')}});
}

async function renderInvestmentSettings() {
  setPage({title:'投入分类与预警配置',iconName:'settings',crumbs:['投入管理'],actions:btn('新建分类标签','btn primary','invNewCategory')});
  const [categoryResult,warningResult]=await Promise.all([api('/api/investments/categories'),api('/api/investments/warnings?status=')]);
  const categories=categoryResult.data.map(r=>({...r,tag_text:(r.tags||[]).join('、')})),rules=warningResult.rules;
  invMount('<div class="inv-tabs" role="tablist"><button class="btn primary" data-setting="categories" role="tab">投入分类（'+categories.length+'）</button><button class="btn" data-setting="rules" role="tab">预警规则（'+rules.length+'）</button></div><div id="invCategoryPanel"></div><section class="section hidden" id="invRulesPanel"><div class="section-head"><h3>预算执行预警</h3><span>百分比与天数按规则分别配置</span></div><div class="investment-rule-list">'+rules.map(r=>{
    const days=['long_unexecuted','near_expiry'].includes(r.code),unit=days?'天':'%';
    return '<article class="investment-rule" data-code="'+esc(r.code)+'"><div class="inv-rule-title"><strong>'+esc(r.name)+'</strong><small>'+esc(r.description)+'</small></div><div class="inv-rule-fields"><label>'+(days?'天数阈值':'比例阈值')+'（'+unit+'）<input class="field inv-rule-value" type="number" min="0" max="'+(days?3650:1000)+'" step="'+(days?'1':'0.1')+'" value="'+(days?r.days_value:r.threshold_value)+'"></label><label>预警等级<select class="select inv-rule-level">'+['提示','预警','严重'].map(v=>'<option '+(r.level===v?'selected':'')+'>'+v+'</option>').join('')+'</select></label><label class="inv-switch"><input type="checkbox" class="inv-rule-enabled" '+(r.enabled?'checked':'')+'>启用</label><button class="btn inv-rule-save">保存规则</button></div><span class="inv-rule-feedback" role="status"></span></article>';
  }).join('')+'</div></section>');
  invTable({host:'#invCategoryPanel',rows:categories,title:'自定义分类与标签',filters:[invChoices(categories,'category_name','类别'),invChoices(categories,'status','状态')],
    columns:[{key:'category_name',title:'投入类别'},{key:'subcategory_name',title:'子类别'},{key:'tag_text',title:'自定义标签',render:r=>(r.tags||[]).map(t=>'<span class="tag gray">'+esc(t)+'</span>').join(' ')||'—'},{key:'sort_order',title:'排序',numeric:true},{key:'status',title:'状态',render:r=>invStatus(r.status)},{title:'操作',render:r=>'<button class="link inv-edit-category" data-id="'+r.id+'">编辑</button>'}],
    onDraw:(_,root)=>$$('.inv-edit-category',root).forEach(b=>b.addEventListener('click',()=>openInvestmentCategoryModal(categories.find(r=>r.id===Number(b.dataset.id)))))
  });
  $$('[data-setting]').forEach(b=>b.addEventListener('click',()=>{
    const isCategories=b.dataset.setting==='categories';
    $('#invCategoryPanel').classList.toggle('hidden',!isCategories);$('#invRulesPanel').classList.toggle('hidden',isCategories);
    $('#invNewCategory').classList.toggle('hidden',!isCategories);
    $$('[data-setting]').forEach(x=>{x.classList.toggle('primary',x===b);x.setAttribute('aria-selected',String(x===b));});
  }));
  $('#invNewCategory').addEventListener('click',()=>openInvestmentCategoryModal());
  $$('.inv-rule-save').forEach(b=>b.addEventListener('click',()=>invAction(b,async()=>{
    const card=b.closest('.investment-rule'),rule=rules.find(r=>r.code===card.dataset.code),el=$('.inv-rule-value',card);
    if(!el.value||!el.checkValidity())throw Error('请输入范围内的有效阈值');
    const days=['long_unexecuted','near_expiry'].includes(rule.code);
    const payload={threshold_value:days?rule.threshold_value:Number(el.value),days_value:days?Number(el.value):rule.days_value,enabled:$('.inv-rule-enabled',card).checked,level:$('.inv-rule-level',card).value};
    await api('/api/investments/warning-rules/'+rule.code,{method:'PUT',body:JSON.stringify(payload)});
    Object.assign(rule,payload);$('.inv-rule-feedback',card).textContent='已保存 · '+new Date().toLocaleTimeString('zh-CN',{hour12:false});
  })));
}

async function openInvestmentReview(rows, type, action='通过') {
  if(!rows.length) return;
  const detail=rows.length===1?(await api('/api/investments/plans/'+rows[0].id)).data:null;
  const items=detail?.items||[];
  invShowModal('<h3>投入'+(type==='finance'?'财务复核':'审批')+'</h3><div class="investment-selected"><strong>已选 '+rows.length+' 个计划</strong><span>申请合计 '+invMoney(invSum(rows,'application_total'))+'</span></div>'
    +'<div class="inv-review-plans">'+rows.map(r=>'<div>'+esc(r.plan_no)+' · '+esc(r.plan_name)+'</div>').join('')+'</div>'
    +(items.length?'<div class="table-wrap"><table class="table inv-table"><thead><tr><th>投入项</th><th class="inv-num">申请金额</th><th>核定金额（元）</th></tr></thead><tbody>'+items.map(i=>'<tr><td>'+esc(i.item_name)+'<small class="cell-sub">'+esc(i.business_purpose)+'</small></td><td class="inv-num">'+invMoney(i.application_amount)+'</td><td><input class="field inv-review-amount" type="number" min="0" max="'+i.application_amount+'" step="0.01" value="'+i.approved_amount+'" data-id="'+i.id+'" aria-label="'+esc(i.item_name)+'核定金额"></td></tr>').join('')+'</tbody></table></div>':'')
    +'<div class="inv-form-grid"><label>处理结果<select class="select" id="invReviewAction"><option '+(action==='通过'?'selected':'')+'>通过</option><option '+(action==='驳回'?'selected':'')+'>驳回</option></select></label><label class="inv-span-all">审批意见<textarea class="textarea" id="invReviewComment" maxlength="1000" placeholder="填写核定依据或驳回原因"></textarea></label></div>'
    +'<div class="modal-actions"><button class="btn" id="invReviewCancel">取消</button><button class="btn primary" id="invReviewSave">确认处理</button></div>');
  const update=()=>$$('.inv-review-amount').forEach(el=>el.disabled=$('#invReviewAction').value==='驳回');
  $('#invReviewAction').addEventListener('change',update);update();
  $('#invReviewCancel').addEventListener('click',closeModal);
  $('#invReviewSave').addEventListener('click',e=>invAction(e.currentTarget,async()=>{
    const resultAction=$('#invReviewAction').value,comment=$('#invReviewComment').value.trim();
    if(resultAction==='驳回'&&!comment) throw Error('请填写驳回原因');
    const amounts={};
    if(resultAction==='通过') for(const el of $$('.inv-review-amount')) {
      if(!el.value||!el.checkValidity()) throw Error('核定金额必须介于0和申请金额之间，最多两位小数');
      amounts[el.dataset.id]=Number(el.value);
    }
    const result=await api(type==='finance'?'/api/investments/finance/confirm-batch':'/api/investments/approvals/batch',{method:'POST',body:JSON.stringify({ids:rows.map(r=>r.id),action:resultAction,comment,reviewed_amounts:amounts})});
    closeModal();await renderInvestmentApprovals();
    const failures=result.data.failed||[];
    if(failures.length) {
      invShowModal('<h3>审批处理结果</h3><p>成功 '+result.data.succeeded.length+' 条，失败 '+failures.length+' 条</p><div class="inv-review-plans">'+failures.map(f=>'<p><strong>'+esc(rows.find(r=>r.id===f.id)?.plan_no||f.id)+'</strong>：'+esc(f.message)+'</p>').join('')+'</div><div class="modal-actions"><button class="btn" id="invResultClose">关闭</button></div>');
      $('#invResultClose').addEventListener('click',closeModal);
    } else toast('已完成 '+result.data.succeeded.length+' 条'+(type==='finance'?'财务确认':'审批'),'success');
  }));
}

async function openInvestmentAdjustmentDetail(id) {
  const a=(await api('/api/investments/adjustments/'+id)).data;
  invShowModal('<h3>投入调整单详情</h3><div class="investment-selected"><strong>'+esc(a.adjustment_no)+'</strong>'+invStatus(a.status)+'</div>'
    +'<p>'+esc(a.item_no)+' · '+esc(a.item_name)+'</p>'
    +'<div class="inv-change-comparison">'+invFacts([['原基线',invMoney(a.original_amount)],['申请调整后',invMoney(a.requested_amount)],['变动金额',(a.amount_delta>0?'+':'')+invMoney(a.amount_delta)]])+'</div>'
    +'<div class="inv-form-grid"><section class="section"><h4>调整前范围</h4><p class="investment-description">'+esc(a.scope_before||'—')+'</p></section><section class="section"><h4>调整后范围</h4><p class="investment-description">'+esc(a.scope_after||a.scope_before||'—')+'</p></section></div>'
    +'<h4>调整原因</h4><p class="investment-description">'+esc(a.reason)+'</p><h4>审批记录</h4>'+invTimeline(a.approvals)
    +'<div class="modal-actions"><button class="btn" id="invAdjDetailClose">关闭</button></div>');
  $('#invAdjDetailClose').addEventListener('click',closeModal);
}

async function openInvestmentAdjustmentReview(row) {
  const a=(await api('/api/investments/adjustments/'+row.id)).data;
  invShowModal('<h3>审批投入调整</h3><div class="investment-selected"><strong>'+esc(a.adjustment_no)+'</strong><span>'+esc(a.current_node)+'</span></div>'
    +'<p>'+esc(a.item_name)+'：'+invMoney(a.original_amount)+' → '+invMoney(a.requested_amount)+'</p><h4>调整原因</h4><p class="investment-description">'+esc(a.reason)+'</p><h4>调整后范围</h4><p class="investment-description">'+esc(a.scope_after||a.scope_before)+'</p>'
    +'<div class="inv-form-grid"><label>处理结果<select class="select" id="invAdjDecision"><option>通过</option><option>驳回</option></select></label><label class="inv-span-all">审批意见<textarea class="textarea" id="invAdjComment" maxlength="1000"></textarea></label></div>'
    +'<div class="modal-actions"><button class="btn" id="invAdjReviewCancel">取消</button><button class="btn primary" id="invAdjReviewSave">确认处理</button></div>');
  $('#invAdjReviewCancel').addEventListener('click',closeModal);
  $('#invAdjReviewSave').addEventListener('click',e=>invAction(e.currentTarget,async()=>{
    const action=$('#invAdjDecision').value,comment=$('#invAdjComment').value.trim();
    if(action==='驳回'&&!comment)throw Error('请填写驳回原因');
    const r=await api('/api/investments/adjustments/'+a.id+'/approve',{method:'POST',body:JSON.stringify({action,comment})});
    closeModal();toast(r.message,'success');await renderInvestmentAdjustments();
  }));
}

async function openInvestmentPaymentHistory(item) {
  const rows=(await api('/api/investments/payments?item_id='+item.id)).data;
  invShowModal('<h3>付款与核销流水</h3><div class="investment-selected"><strong>'+esc(item.item_name)+'</strong><span>'+esc(item.item_no)+'</span></div>'
    +invFacts([['核定额度',invMoney(item.approved_amount)],['已核销',invMoney(item.written_off_amount)],['剩余可核销',invMoney(item.remaining_amount)]])
    +'<div id="invPaymentsLedger"></div><div class="modal-actions"><button class="btn" id="invHistoryClose">关闭</button></div>');
  invTable({host:'#invPaymentsLedger',rows,title:'核销单据',filters:[invChoices(rows,'payment_year','付款年度'),invChoices(rows,'payment_type','付款类型')],
    columns:[{key:'payment_no',title:'核销编号',render:r=>'<strong>'+esc(r.payment_no)+'</strong><small class="cell-sub">'+esc(r.document_no)+'</small>'},{key:'payment_date',title:'付款日期'},{key:'payment_type',title:'类型'},{key:'amount',title:'支付金额',numeric:true,render:r=>invMoney(r.amount)},{key:'writeoff_amount',title:'核销金额',numeric:true,render:r=>invMoney(r.writeoff_amount)},{key:'payer',title:'支付方'},{key:'contract_no',title:'合同编号'},{key:'description',title:'备注'}]
  });
  $('#invHistoryClose').addEventListener('click',closeModal);
}
