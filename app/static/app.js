const state = {
  meta: null,
  v4meta: null,
  role: 'applicant',
  actorId: '',
  userDisplay: '',
  sessionToken: localStorage.getItem('trm_session') || '',
  currentUserData: null,
  currentDemandId: null,
  pendingFiles: [],
  openGroups: new Set(),
  sidebarCollapsed: false,
  detailTab: 'basic',
  aiSessionId: '',
  aiBusy: false,
  aiProvider: 'Gazellio G.AIOS',
  projectAiSessions: {},
  chat: [
    { type: 'ai', text: '你好，我是TRM AI助手。我已连接企业智能体，可以基于系统数据查询需求、审批、预算、TAPD进度、项目风险与历史统计。' }
  ]
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const appView = $('#appView');
const pageTitle = $('#pageTitle');
const breadcrumb = $('#breadcrumb');
const heroActions = $('#heroActions');
const heroIconUse = $('#heroIcon use');

function esc(value = '') {
  return String(value).replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[ch]));
}

function money(value) {
  return Number(value || 0).toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  });
}

function fmtSize(bytes = 0) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

function statusClass(status = '') {
  if (['已完成', '审批通过', '已创建', '通过', '成功'].some((v) => status.includes(v))) return 'success';
  if (['失败', '驳回', '终止', '不足'].some((v) => status.includes(v))) return 'danger';
  if (['审批', '待', '预警', '测试'].some((v) => status.includes(v))) return 'warn';
  if (['草稿', '未同步'].some((v) => status.includes(v))) return 'gray';
  return '';
}

function icon(name, cls = 'icon') {
  return `<svg class="${cls}"><use href="#i-${name}"></use></svg>`;
}

function btn(text, cls = 'btn', id = '', attrs = '') {
  return `<button type="button" class="${cls}" ${id ? `id="${id}"` : ''} ${attrs}>${text}</button>`;
}

function toast(message, type = '') {
  const node = document.createElement('div');
  node.className = `toast ${type}`;
  node.textContent = message;
  $('#toastWrap').appendChild(node);
  setTimeout(() => node.remove(), 3600);
}

function showModal(html, wide = false) {
  const box = $('#modalBox');
  box.className = `modal${wide ? ' wide' : ''}`;
  box.innerHTML = html;
  $('#modalBackdrop').classList.remove('hidden');
}

function closeModal() {
  $('#modalBackdrop').classList.add('hidden');
  $('#modalBox').innerHTML = '';
}

function closeDrawer() {
  $('#drawerBackdrop').classList.add('hidden');
  $('#notificationDrawer').classList.add('hidden');
}

function currentUser() {
  if (state.currentUserData) return {
    id: state.currentUserData.username,
    name: `${state.currentUserData.display_name} ${state.currentUserData.username}`,
    dept: state.currentUserData.department || ''
  };
  return { id: state.actorId, name: state.userDisplay, dept: '' };
}

function requestHeaders(extra = {}) {
  return {
    'Content-Type': 'application/json',
    ...(state.sessionToken ? { 'X-Session': state.sessionToken } : {}),
    ...extra
  };
}

async function api(path, options = {}) {
  const opts = { ...options };
  if (options.body instanceof FormData) {
    opts.headers = {
      ...(state.sessionToken ? { 'X-Session': state.sessionToken } : {}),
      ...(options.headers || {})
    };
  } else {
    opts.headers = requestHeaders(options.headers || {});
  }
  const response = await fetch(path, opts);
  let data = {};
  try {
    data = await response.json();
  } catch {
    data = {};
  }
  if (!response.ok) {
    if (response.status === 401 && path !== '/api/auth/login') {
      clearSession();
      showLogin('登录已失效，请重新登录');
    }
    const error = new Error(data.message || `请求失败 ${response.status}`);
    error.data = data;
    error.status = response.status;
    throw error;
  }
  return data;
}

async function askAiAgent(question, { projectId = null, source = 'assistant' } = {}) {
  const sessionId = projectId ? (state.projectAiSessions[projectId] || '') : state.aiSessionId;
  try {
    const result = await api('/api/ai/chat', {
      method: 'POST',
      body: JSON.stringify({ question, session_id: sessionId, project_id: projectId, source })
    });
    if (projectId) state.projectAiSessions[projectId] = result.data.session_id || sessionId;
    else state.aiSessionId = result.data.session_id || sessionId;
    state.aiProvider = result.data.provider || 'Gazellio G.AIOS';
    return { ...result.data, fallback: false };
  } catch (remoteError) {
    if (remoteError.status === 401) throw remoteError;
    const fallback = projectId
      ? await api(`/api/project360/${projectId}/query`, { method: 'POST', body: JSON.stringify({ question }) })
      : await api('/api/ai/query', { method: 'POST', body: JSON.stringify({ question }) });
    return {
      answer: fallback.data.answer,
      session_id: sessionId,
      provider: 'TRM本地知识引擎',
      fallback: true,
      warning: remoteError.message
    };
  }
}

function parseRoute() {
  const raw = location.hash.replace(/^#\/?/, '');
  const [pathPart, queryPart = ''] = raw.split('?');
  return {
    path: pathPart || 'dashboard',
    query: new URLSearchParams(queryPart)
  };
}

function navigate(path, query = {}) {
  const base = String(path).split('/')[0];
  const group = routeGroup(base);
  state.openGroups = group ? new Set([group]) : new Set();
  if (base === 'demand-form' && !query.id) { state.currentDemandId = null; state.pendingFiles = []; }
  const qs = new URLSearchParams();
  Object.entries(query).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') qs.set(key, value);
  });
  const hash = `#/${path}${qs.toString() ? `?${qs.toString()}` : ''}`;
  if (location.hash === hash) renderRoute();
  else location.hash = hash;
}

function setPage({ title, iconName = 'demand', crumbs = [], actions = '' }) {
  pageTitle.textContent = title;
  breadcrumb.textContent = ['科技资源管理系统', ...crumbs, title].join(' / ');
  heroActions.innerHTML = actions;
  heroIconUse.setAttribute('href', `#i-${iconName}`);
}

function stageForDemand(demand) {
  const status = demand?.status || '草稿';
  const node = demand?.current_node || '';
  if (status === '草稿' || status === '已驳回') return 1;
  if (node.includes('审批') || node === '终审') return 2;
  if (status === '审批通过' || status === 'TAPD同步失败') return 3;
  if (['已创建', '开发中', '测试中', '待发布'].includes(status)) return 4;
  if (['已完成', '已终止'].includes(status)) return 5;
  return 2;
}

function workflow(active = 1) {
  const steps = [
    ['需求申请', '填写并提交需求'],
    ['需求审批', '多级审批流转'],
    ['推送TAPD', '终审自动创建需求'],
    ['TAPD信息回读', '同步状态与工时'],
    ['AI问答', '全生命周期智能检索']
  ];
  return `<div class="workflow">${steps.map((item, index) => {
    const step = index + 1;
    return `<div class="workflow-step ${step <= active ? 'on' : ''} ${step === active ? 'current' : ''}">
      <b><span class="step-dot">${step}</span>${item[0]}</b><small>${item[1]}</small>
    </div>`;
  }).join('')}</div>`;
}

function approvalFlow(demand) {
  const amount = Number(demand?.estimated_amount || demand?.budget_amount || 0);
  const nodes = [
    ['直属领导审批', '部门负责人', '确认需求合理性', '24h'],
    ['产品经理审批', '产品经理', '功能点评估与费用分摊', '48h'],
    ['财务审批', '财务人员', '预算校验与执行率检查', '24h'],
    ...(amount > 50000 ? [['分管总审批', '分管总', '预估金额超过5万元', '24h']] : []),
    ['终审', '业务负责人', '最终业务决策', '72h']
  ];
  const approvals = demand?.approvals || [];
  const oaTasks = demand?.oa_tasks || [];
  const current = demand?.current_node;
  return `<div>${nodes.map((n, index) => {
    const record = [...approvals].reverse().find((a) => a.node === n[0] && a.action === '通过');
    const task = [...oaTasks].reverse().find((t) => t.node === n[0]);
    const done = !!record;
    const isCurrent = current === n[0];
    const overdue = task?.status === '待处理' && task?.due_at && new Date(task.due_at).getTime() < Date.now();
    const taskText = task ? ` · OA${task.status}${task.due_at ? ` · 截止${String(task.due_at).replace('T',' ').slice(0,16)}` : ''}${task.reminder_count ? ` · 已提醒${task.reminder_count}次` : ''}` : '';
    return `<div class="node-card ${done ? 'done' : ''} ${isCurrent ? 'current' : ''}">
      <span class="node-index">${done ? '✓' : index + 1}</span>
      <div class="node-info"><strong>${n[0]}</strong><small>${n[1]} · ${n[2]} · 超时 ${n[3]} 自动提醒${taskText}</small></div>
      <span class="status ${done ? statusClass(record.action) : overdue ? 'danger' : isCurrent ? 'warn' : 'gray'}">${done ? record.action : overdue ? '已超时' : isCurrent ? '当前节点' : '未开始'}</span>
    </div>`;
  }).join('')}</div>`;
}

function demandTable(items, { approvalMode = false, compact = false } = {}) {
  if (!items?.length) return '<div class="empty"><div><strong>暂无数据</strong><span>当前条件下没有可展示的需求。</span></div></div>';
  if (compact) {
    return `<div class="table-wrap"><table class="table compact-table">
      <thead><tr><th>需求编号</th><th>需求标题</th><th>状态</th><th>金额</th><th>操作</th></tr></thead>
      <tbody>${items.map((d) => `<tr>
        <td><span class="detail-no">${esc(d.demand_no || '草稿')}</span></td>
        <td><div class="row-title" title="${esc(d.title)}">${esc(d.title)}</div></td>
        <td><span class="status ${statusClass(d.status)}">${esc(d.status)}</span></td>
        <td>¥ ${money(d.estimated_amount || d.budget_amount)}</td>
        <td><div class="action-group"><button class="link demand-open" data-id="${d.id}">查看详情</button></div></td>
      </tr>`).join('')}</tbody>
    </table></div>`;
  }
  return `<div class="table-wrap"><table class="table">
    <thead><tr><th>需求编号</th><th>需求标题</th><th>申请人</th><th>金额</th><th>优先级</th><th>当前状态</th><th>TAPD</th><th>操作</th></tr></thead>
    <tbody>${items.map((d) => `<tr>
      <td><span class="detail-no">${esc(d.demand_no || '草稿')}</span></td>
      <td><div class="row-title" title="${esc(d.title)}">${esc(d.title)}</div></td>
      <td>${esc(d.applicant)}</td>
      <td>¥ ${money(d.estimated_amount || d.budget_amount)}</td>
      <td>${esc(d.priority)}</td>
      <td><span class="status ${statusClass(d.status)}">${esc(d.status)}</span></td>
      <td>${esc(d.tapd_status || '—')}</td>
      <td><div class="action-group">
        <button class="link demand-open" data-id="${d.id}">${approvalMode ? '进入审批' : '查看详情'}</button>
        ${(d.status === '草稿' || d.status === '已驳回') && ['applicant', 'admin'].includes(state.role) && !approvalMode ? `<button class="link demand-edit" data-id="${d.id}">编辑</button>` : ''}
      </div></td>
    </tr>`).join('')}</tbody>
  </table></div>`;
}

function approvalRecords(records = []) {
  if (!records.length) return '<div class="empty" style="min-height:100px">暂无审批记录</div>';
  return `<div class="timeline">${records.map((r) => `<div class="timeline-item">
    <div><strong>${esc(r.node)}</strong><span class="status ${statusClass(r.action)}">${esc(r.action)}</span>${r.return_to ? `<span class="status gray">退回：${esc(r.return_to)}</span>` : ''}</div>
    <div class="time">${esc(r.approver)} · ${esc(r.created_at)}</div>
    <div class="comment">${esc(r.comment || '未填写审批意见')}</div>
  </div>`).join('')}</div>`;
}

function functionPointTable(items = [], editable = false) {
  if (!items.length) return '<div class="empty" style="min-height:120px">尚未关联功能点</div>';
  return `<div class="table-wrap"><table class="table">
    <thead><tr><th>功能点编号</th><th>归属系统</th><th>需求名称</th><th>来源</th><th>功能点</th><th>单价</th><th>预估金额</th><th>评估人</th>${editable ? '<th>操作</th>' : ''}</tr></thead>
    <tbody>${items.map((fp) => `<tr>
      <td><button class="link fp-detail" data-id="${fp.id}">${esc(fp.fp_no)}</button></td>
      <td>${esc(fp.system_name)}</td><td>${esc(fp.name || '—')}</td><td><span class="status gray">${esc(fp.source_type || '新增')}</span></td>
      <td>${Number(fp.fp_count || 0).toFixed(2)}</td><td>¥ ${money(fp.unit_price)}</td><td><span title="计算过程：${Number(fp.fp_count || 0).toFixed(2)} × ¥${money(fp.unit_price)} = ¥${money(fp.estimated_amount)}">¥ ${money(fp.estimated_amount)}</span></td><td>${esc(fp.evaluator)}</td>
      ${editable ? `<td><div class="action-group"><button class="link fp-edit" data-id="${fp.id}">编辑</button><button class="link fp-delete" data-id="${fp.id}">删除</button></div></td>` : ''}
    </tr>`).join('')}</tbody></table></div>`;
}

function allocationTable(items = []) {
  if (!items.length) return '<div class="empty" style="min-height:100px">尚未录入费用分摊</div>';
  return `<div class="table-wrap"><table class="table"><thead><tr><th>归属系统</th><th>费用主体</th><th>费用出处</th><th>分摊比例</th><th>分摊金额</th><th>费用归属部门</th></tr></thead><tbody>
    ${items.map((r) => `<tr><td>${esc(r.system_name || '全部系统')}</td><td>${esc(r.expense_subject)}</td><td>${esc(r.expense_source)}</td><td>${Number(r.ratio).toFixed(2)}%</td><td>¥ ${money(r.amount)}</td><td>${esc(r.department)}</td></tr>`).join('')}
  </tbody></table></div>`;
}

function budgetSnapshot(snapshot) {
  if (!snapshot) return '<div class="callout warn">当前需求尚未关联可校验的预算。</div>';
  return `<div class="callout" style="margin-bottom:10px"><strong>${esc(snapshot.budget_name || '')}</strong> · ${esc(snapshot.budget_no || '')}</div>
  <div class="grid-3">
    <div class="metric soft"><div class="k">总预算</div><div class="v">¥ ${money(snapshot.total_budget)}</div><div class="sub">预算基线</div></div>
    <div class="metric soft"><div class="k">已发生实际支出</div><div class="v">¥ ${money(snapshot.used_budget)}</div><div class="sub">执行率 ${snapshot.execution_rate}%</div></div>
    <div class="metric soft"><div class="k">剩余可用</div><div class="v">¥ ${money(snapshot.remaining_budget)}</div><div class="sub">本次需求 ¥${money(snapshot.current_demand_amount)}</div></div>
  </div>
  <div class="grid-3" style="margin-top:10px">
    <div class="metric soft"><div class="k">已立项/审批需求累计预算</div><div class="v">¥ ${money(snapshot.committed_demand_amount || 0)}</div><div class="sub">本次纳入后 ¥${money(snapshot.commitment_after || 0)}</div></div>
    <div class="metric soft"><div class="k">内部研发预算</div><div class="v">${snapshot.internal_execution_rate || 0}%</div><div class="sub">¥${money(snapshot.internal_used)} / ¥${money(snapshot.internal_total)} · 可用¥${money(snapshot.internal_remaining)}</div></div>
    <div class="metric soft"><div class="k">委托数科预算</div><div class="v">${snapshot.digital_execution_rate || 0}%</div><div class="sub">¥${money(snapshot.digital_used)} / ¥${money(snapshot.digital_total)} · 可用¥${money(snapshot.digital_remaining)}</div></div>
  </div>
  <div style="margin-top:12px"><div class="toolbar"><span>当前预算执行率（实际支出 ÷ 总预算）</span><strong>${snapshot.execution_rate}%</strong></div><div class="progress"><span style="width:${Math.min(100, snapshot.execution_rate)}%"></span></div></div>
  <div class="callout ${!snapshot.sufficient ? 'danger' : snapshot.warning ? 'warn' : 'success'}" style="margin-top:12px">
    ${snapshot.sufficient ? '✓ 预算校验通过：已立项需求累计预算 + 本次需求预算未超过总预算' : '⚠ 预算不足：累计需求预算或实际剩余预算不足，财务审批将被阻断'}${snapshot.warning ? '；当前执行率已达到或超过95%，财务审批意见为必填。' : ''}
  </div>`;
}

function approvalReturnTargets(demand) {
  const map = {
    '直属领导审批': ['需求申请'],
    '产品经理审批': ['直属领导审批','需求申请'],
    '财务审批': ['产品经理审批','直属领导审批','需求申请'],
    '分管总审批': ['财务审批','产品经理审批','直属领导审批','需求申请'],
    '终审': ['分管总审批','财务审批','产品经理审批','直属领导审批','需求申请']
  };
  let items = [...(map[demand?.current_node] || ['需求申请'])];
  const amount = Number(demand?.estimated_amount || demand?.budget_amount || 0);
  if (amount <= 50000) items = items.filter(x => x !== '分管总审批');
  return items;
}

function roleCanApprove(demand) {
  const roleByNode = {
    '直属领导审批': 'department_head',
    '产品经理审批': 'product_manager',
    '财务审批': 'finance',
    '分管总审批': 'vp',
    '终审': 'business_owner'
  };
  return state.role === 'admin' || roleByNode[demand?.current_node] === state.role;
}

function bindCommonDemandActions(root = document) {
  $$('.demand-open', root).forEach((button) => {
    button.addEventListener('click', () => {
      state.currentDemandId = Number(button.dataset.id);
      navigate(`demand-detail/${button.dataset.id}`);
    });
  });
  $$('.demand-edit', root).forEach((button) => {
    button.addEventListener('click', () => {
      state.currentDemandId = Number(button.dataset.id);
      navigate('demand-form', { id: button.dataset.id });
    });
  });
}

async function loadNotifications(draw = false) {
  try {
    const result = await api('/api/notifications');
    const unread = result.data.filter((n) => !n.is_read);
    const badge = $('#notifyCount');
    badge.textContent = unread.length;
    badge.classList.toggle('hidden', unread.length === 0);
    if (draw) {
      $('#notificationList').innerHTML = result.data.length ? result.data.map((n) => `<button class="notification-item" type="button" data-id="${n.id}" data-demand="${n.demand_id || ''}" style="width:100%;border:0;background:${n.is_read ? '#fff' : '#f9fbff'};text-align:left">
        <strong>${esc(n.title)}</strong><p>${esc(n.content)}</p><small>${esc(n.created_at)} · ${n.is_read ? '已读' : '未读'}</small>
      </button>`).join('') : '<div class="empty">暂无消息</div>';
      $$('.notification-item').forEach((item) => item.addEventListener('click', async () => {
        try { await api(`/api/notifications/${item.dataset.id}/read`, { method: 'POST' }); } catch {}
        closeDrawer();
        await loadNotifications(false);
        if (item.dataset.demand) navigate(`demand-detail/${item.dataset.demand}`);
      }));
    }
  } catch (error) {
    console.warn('消息加载失败', error);
  }
}

function hasPermission(permission) {
  const permissions = state.currentUserData?.permissions || [];
  return permissions.includes('*') || permissions.includes(permission);
}

function applyMenuPermissions() {
  $$('[data-permission]').forEach((item) => {
    item.classList.toggle('permission-hidden', !hasPermission(item.dataset.permission));
  });
  $$('.nav-group').forEach((group) => {
    const children = $$('.nav-sub', group);
    if (!children.length) return;
    const visible = children.some((item) => !item.classList.contains('permission-hidden'));
    group.classList.toggle('permission-hidden', !visible);
  });
}

function updateUserUI() {
  const user = currentUser();
  $('#userName').textContent = user.name || state.actorId;
  $('#userRoleLabel').textContent = state.currentUserData?.role_label || state.meta?.roles?.[state.role] || state.role;
  $('#userAvatar').textContent = (state.currentUserData?.display_name || state.actorId || 'U').trim().slice(0, 1);
  $('#menuUserName').textContent = state.currentUserData?.display_name || '当前用户';
  $('#menuUserDept').textContent = state.currentUserData?.department || '—';
  $('#menuUsername').textContent = state.currentUserData?.username || '—';
  $('#menuRole').textContent = state.currentUserData?.role_label || state.role;
  applyMenuPermissions();
}

function renderAiAssistantWidget() {
  const history = $('#aiAssistantHistory');
  if (!history) return;
  history.innerHTML = state.chat.map((msg) => `<div class="bubble ${msg.type}">${esc(msg.text)}${msg.type==='ai'&&msg.provider?`<span class="ai-message-meta">${esc(msg.provider)}${msg.fallback?' · 本地降级':''}</span>`:''}</div>`).join('')
    + (state.aiBusy ? '<div class="bubble ai"><span class="ai-float-typing" aria-label="AI正在思考"><i></i><i></i><i></i></span></div>' : '');
  $('#aiAssistantSend').disabled = state.aiBusy;
  $('#aiProviderLabel').textContent = `${state.aiProvider}${state.aiProvider.includes('本地')?' · 降级':' · 已连接'}`;
  requestAnimationFrame(() => { history.scrollTop = history.scrollHeight; });
}

async function sendFloatingAiQuestion() {
  const input = $('#aiAssistantInput');
  const question = input.value.trim();
  if (!question || state.aiBusy) return;
  state.chat.push({type:'user',text:question});
  input.value = '';
  state.aiBusy = true;
  renderAiAssistantWidget();
  try {
    const result = await askAiAgent(question, {source:'floating-assistant'});
    state.aiProvider = result.provider;
    state.chat.push({type:'ai',text:result.answer,provider:result.provider,fallback:result.fallback});
    if (result.fallback) toast(`外部智能体暂不可用，已切换本地知识引擎：${result.warning}`,'warn');
  } catch (error) {
    state.chat.push({type:'ai',text:`对话失败：${error.message}`,provider:'系统提示'});
  } finally {
    state.aiBusy = false;
    renderAiAssistantWidget();
    if (parseRoute().path === 'ai') renderAI();
  }
}

function bindAiAssistant() {
  $('#aiAssistantToggle').addEventListener('click', () => {
    const panel = $('#aiAssistantPanel');
    const willOpen = panel.classList.contains('hidden');
    panel.classList.toggle('hidden', !willOpen);
    $('#aiAssistantToggle').setAttribute('aria-expanded', String(willOpen));
    if (willOpen) {
      renderAiAssistantWidget();
      setTimeout(() => $('#aiAssistantInput').focus(), 60);
    }
  });
  $('#aiAssistantClose').addEventListener('click', () => {
    $('#aiAssistantPanel').classList.add('hidden');
    $('#aiAssistantToggle').setAttribute('aria-expanded', 'false');
    $('#aiAssistantToggle').focus();
  });
  $('#aiAssistantSend').addEventListener('click', sendFloatingAiQuestion);
  $('#aiAssistantInput').addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      sendFloatingAiQuestion();
    }
  });
  $$('#aiAssistantSuggestions button').forEach((button) => button.addEventListener('click', () => {
    $('#aiAssistantInput').value = button.textContent;
    sendFloatingAiQuestion();
  }));
  renderAiAssistantWidget();
}


function routeGroup(base) {
  const groups = {
    initiative: new Set(['initiative-form','initiative-approvals','initiative-list','initiative-detail']),
    demand: new Set(['demand-form','product-eval','approvals','function-points','function-point-new','approval','demand-list','demand-detail','tapd','ai']),
    project: new Set(['project-list','project-detail','project-tasks','milestones','project-governance']),
    settlement: new Set(['settlement-form','settlement-approvals','settlement-list','settlement-detail']),
    indicator: new Set(['indicator-list','indicator-data','indicator-board']),
    contract: new Set(['contract-list','contract-detail','payment-plans','contract-approvals']),
    system: new Set(['users','roles','integrations','audit'])
  };
  return Object.entries(groups).find(([, routes]) => routes.has(base))?.[0] || null;
}

function updateNav(routePath) {
  const base = routePath.split('/')[0];
  const activeGroup = routeGroup(base);
  $$('.nav-item[data-route], .nav-sub[data-route]').forEach((item) => item.classList.toggle('active', item.dataset.route === base));
  $$('.nav-group').forEach((group) => {
    const name = group.dataset.group;
    group.classList.toggle('open', state.openGroups.has(name));
    const toggle = group.querySelector('[data-group-toggle]');
    if (toggle) {
      toggle.setAttribute('aria-expanded', String(state.openGroups.has(name)));
      toggle.classList.toggle('active', activeGroup === name);
    }
  });
}

function bindShell() {
  $('#sideNav').addEventListener('click', (event) => {
    const groupToggle = event.target.closest('[data-group-toggle]');
    if (groupToggle) {
      const name = groupToggle.dataset.groupToggle;
      state.openGroups = state.openGroups.has(name) ? new Set() : new Set([name]);
      updateNav(parseRoute().path);
      return;
    }
    const routeButton = event.target.closest('[data-route]');
    if (!routeButton) return;
    const group = routeButton.closest('.nav-group')?.dataset.group;
    state.openGroups = group ? new Set([group]) : new Set();
    navigate(routeButton.dataset.route);
  });

  $('#sidebarCollapse').addEventListener('click', () => {
    state.sidebarCollapsed = !state.sidebarCollapsed;
    $('#appShell').classList.toggle('sidebar-collapsed', state.sidebarCollapsed);
  });

  $('#notificationBtn').addEventListener('click', async () => {
    $('#drawerBackdrop').classList.remove('hidden');
    $('#notificationDrawer').classList.remove('hidden');
    await loadNotifications(true);
  });
  $('#drawerClose').addEventListener('click', closeDrawer);
  $('#drawerBackdrop').addEventListener('click', closeDrawer);

  $('#userTrigger').addEventListener('click', (event) => {
    event.stopPropagation();
    const menu = $('#userMenu');
    menu.classList.toggle('hidden');
    $('#userTrigger').setAttribute('aria-expanded', String(!menu.classList.contains('hidden')));
  });
  document.addEventListener('click', (event) => {
    if (!event.target.closest('#userMenu') && !event.target.closest('#userTrigger')) {
      $('#userMenu').classList.add('hidden');
      $('#userTrigger').setAttribute('aria-expanded', 'false');
    }
  });

  $('#changePasswordBtn').addEventListener('click', openChangePassword);
  $('#openProfileBtn').addEventListener('click', openAccountProfile);

  $('#logoutBtn').addEventListener('click', () => {
    showModal(`<h3 id="modalTitle">退出登录</h3><p>确认退出当前账号？退出后需要重新输入账号和密码。</p><div class="modal-actions">${btn('取消', 'btn', 'cancelLogout')}${btn('确认退出', 'btn primary', 'confirmLogout')}</div>`);
    $('#cancelLogout').addEventListener('click', closeModal);
    $('#confirmLogout').addEventListener('click', async () => {
      try { await api('/api/auth/logout', { method: 'POST' }); } catch {}
      clearSession(); closeModal(); showLogin();
    });
  });

  $('#modalBackdrop').addEventListener('click', (event) => {
    if (event.target.id === 'modalBackdrop') closeModal();
  });
  window.addEventListener('hashchange', renderRoute);
}

function clearSession() {
  state.sessionToken = ''; state.currentUserData = null; state.actorId = ''; state.userDisplay = ''; state.role = 'applicant';
  state.aiSessionId = ''; state.projectAiSessions = {}; state.aiBusy = false;
  localStorage.removeItem('trm_session');
}

function showLogin(message = '') {
  $('#appShell').classList.add('hidden');
  $('#aiAssistantWidget').classList.add('hidden');
  $('#aiAssistantPanel').classList.add('hidden');
  $('#loginScreen').classList.remove('hidden');
  $('#loginError').textContent = message;
  $('#loginError').classList.toggle('hidden', !message);
  setTimeout(() => $('#loginUsername')?.focus(), 30);
}

function showApp() {
  $('#loginScreen').classList.add('hidden');
  $('#appShell').classList.remove('hidden');
  $('#aiAssistantWidget').classList.remove('hidden');
}

async function rawLogin(username, password) {
  const response = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, password }) });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.message || '登录失败');
  return data.data;
}

function bindLogin() {
  const submit = async () => {
    const username = $('#loginUsername').value.trim(), password = $('#loginPassword').value;
    if (!username || !password) { showLogin('请输入账号和密码'); return; }
    $('#loginSubmit').disabled = true; $('#loginSubmit').textContent = '正在登录...';
    try {
      const result = await rawLogin(username, password);
      state.sessionToken = result.token; localStorage.setItem('trm_session', result.token); state.currentUserData = result.user;
      await bootstrapAuthenticatedApp();
    } catch (error) { showLogin(error.message); }
    finally { $('#loginSubmit').disabled = false; $('#loginSubmit').textContent = '登录系统'; }
  };
  $('#loginSubmit').addEventListener('click', submit);
  ['loginUsername','loginPassword'].forEach(id => $('#'+id).addEventListener('keydown', e => { if (e.key === 'Enter') submit(); }));
}

async function bootstrapAuthenticatedApp() {
  const me = (await api('/api/auth/me')).data;
  state.currentUserData = me; state.actorId = me.username; state.userDisplay = `${me.display_name} ${me.username}`; state.role = me.role_code;
  const [meta, v4meta] = await Promise.all([api('/api/meta'), api('/api/v4/meta')]);
  state.meta = meta.data; state.v4meta = v4meta.data;
  updateUserUI(); showApp();
  await loadNotifications(false);
  if (!location.hash || location.hash === '#/login') location.hash = '#/dashboard';
  else { const initialGroup = routeGroup(parseRoute().path.split('/')[0]); state.openGroups = initialGroup ? new Set([initialGroup]) : new Set(); await renderRoute(); }
}

async function init() {
  bindLogin(); bindShell(); bindAiAssistant();
  if (location.protocol === 'file:') {
    showLogin('当前是直接文件打开方式，后端接口尚未启动。请返回项目根目录，双击“启动系统.command”，系统会自动打开正确地址。');
    $('#loginSubmit').disabled = true;
    $('#loginSubmit').textContent = '请先启动系统服务';
    return;
  }
  if (!state.sessionToken) { showLogin(); return; }
  try { await bootstrapAuthenticatedApp(); }
  catch (error) { clearSession(); showLogin(error.message || '登录已失效，请重新登录'); }
}


async function renderRoute() {
  const route = parseRoute();
  const routeActiveGroup = routeGroup(route.path.split('/')[0]);
  state.openGroups = routeActiveGroup ? new Set([routeActiveGroup]) : new Set();
  updateNav(route.path);
  heroActions.innerHTML = '';
  appView.innerHTML = '<div class="empty">加载中...</div>';
  try {
    const [base, id] = route.path.split('/');
    const renderers = {
      dashboard: renderDashboard,
      project360: renderProject360,
      value: renderValueOverview,
      budget: renderBudget,
      'initiative-form': renderInitiativeForm,
      'initiative-list': renderInitiativeList,
      'initiative-detail': () => renderInitiativeDetail(Number(id)),
      'initiative-approvals': renderInitiativeApprovals,
      'demand-form': renderDemandForm,
      'demand-list': renderDemandList,
      'demand-detail': () => renderDemandDetail(Number(id), route.query),
      approvals: renderApprovals,
      approval: () => renderApprovalDetail(Number(id)),
      'product-eval': () => renderProductEval(route.query),
      'function-points': () => renderFunctionPointCatalog(route.query),
      'function-point-new': () => renderFunctionPointNew(route.query),
      tapd: renderTapd,
      ai: renderAI,
      'project-list': renderProjectList,
      'project-detail': () => renderProjectDetail(Number(id), route.query),
      'project-tasks': renderProjectTasks,
      milestones: renderMilestones,
      'project-governance': renderProjectGovernance,
      'settlement-form': renderSettlementForm,
      'settlement-list': renderSettlementList,
      'settlement-detail': () => renderSettlementDetail(Number(id)),
      'settlement-approvals': renderSettlementApprovals,
      'indicator-list': renderIndicatorList,
      'indicator-data': renderIndicatorData,
      'indicator-board': renderIndicatorBoard,
      'contract-list': renderContractList,
      'contract-detail': () => renderContractDetail(Number(id)),
      'payment-plans': renderPaymentPlans,
      'contract-approvals': renderContractApprovals,
      users: renderUsers,
      roles: renderRoles,
      integrations: renderIntegrations,
      audit: renderAudit
    };
    const renderer = renderers[base] || renderDashboard;
    await renderer();
  } catch (error) {
    console.error('页面加载失败', error);
    appView.innerHTML = `<div class="callout danger"><strong>页面加载失败</strong><div style="margin-top:6px">${esc(error.message)}</div>${error.data?.requestId ? `<div class="help">requestId：${esc(error.data.requestId)}</div>` : ''}</div>`;
  }
}

async function renderDashboard() {
  setPage({ title: '首页', iconName: 'home', crumbs: ['驾驶舱'], actions: `${hasPermission('demand.create')?btn(`${icon('plus')} 新建需求`, 'btn primary', 'dashNew'):''}${btn('导出汇总','btn','dashExport')}` });
  const [platformResp, demandResp, pendingResp, noteResp, tapdResp] = await Promise.all([
    api('/api/platform-dashboard'), api('/api/dashboard'), api('/api/approvals/pending'), api('/api/notifications'), api('/api/tapd/overview')
  ]);
  const p = platformResp.data, d = demandResp.data, pending = pendingResp.data || [], notes = (noteResp.data || []).slice(0,6), tapd = tapdResp.data;
  const rate = p.budget_total ? p.budget_used / p.budget_total * 100 : 0;
  appView.innerHTML = `<div class="grid-4">
    <div class="metric"><div class="k">在管项目</div><div class="kpi-number">${p.projects}</div><div class="sub">平均进度 ${p.project_progress}%</div></div>
    <div class="metric"><div class="k">需求总数</div><div class="kpi-number">${p.demands}</div><div class="sub">待处理审批 ${pending.length} 条</div></div>
    <div class="metric"><div class="k">预算执行</div><div class="kpi-number" style="font-size:20px">${rate.toFixed(1)}%</div><div class="sub">¥${money(p.budget_used)} / ¥${money(p.budget_total)}</div></div>
    <div class="metric"><div class="k">TAPD同步</div><div class="kpi-number">${tapd.requirement_count}</div><div class="sub">${tapd.config.mode==='live'?'Live':'Mock'} · 待重试 ${tapd.waiting_retry}</div></div>
  </div>
  <div class="grid-2" style="margin-top:12px">
    <div class="section"><div class="section-title">近期需求</div>${demandTable(d.recent,{compact:true})}</div>
    <div class="section"><div class="toolbar"><div><div class="section-title">我的需求审批待办</div><div class="section-subtitle">按当前身份实时读取可处理节点</div></div>${pending.length?`<span class="status warn">${pending.length} 待处理</span>`:''}</div>${simpleTable(['需求编号','需求标题','当前节点','金额','操作'],pending.slice(0,6).map(x=>[x.demand_no,esc(x.title),esc(x.current_node),`¥${money(x.estimated_amount||x.budget_amount||0)}`,`<button class="link dash-approve" data-id="${x.id}">处理</button>`]))}</div>
  </div>
  <div class="grid-2" style="margin-top:12px">
    <div class="section"><div class="section-title">在管项目</div>${simpleTable(['项目编号','项目名称','经理','状态','进度'],p.recent_projects.map(x=>[x.project_no,x.name,x.manager,statusPill(x.status),progressCell(x.progress)]))}</div>
    <div class="section"><div class="toolbar"><div><div class="section-title">风险与提醒</div><div class="section-subtitle">预算、工时、审批与集成异常统一汇总</div></div></div><div class="notice-list">${notes.length?notes.map(n=>`<div class="notice-item"><span class="status ${n.level==='warning'?'warn':n.level==='error'?'danger':''}">${esc(n.level||'info')}</span><div><strong>${esc(n.title)}</strong><small>${esc(n.content||'')}</small></div></div>`).join(''):'<div class="empty">当前暂无提醒</div>'}</div></div>
  </div>
  <div class="grid-2" style="margin-top:12px"><div class="section"><div class="section-title">快捷入口</div><div class="quick-grid">${btn('新建立项','btn','quickIni')}${btn('新建需求','btn primary','quickDemand')}${btn('项目台账','btn','quickProject')}${btn('合同台账','btn','quickContract')}${btn('预算管理','btn','quickBudget')}${btn('TAPD同步中心','btn','quickTapd')}</div></div><div class="section"><div class="section-title">最近立项</div>${simpleTable(['立项编号','名称','状态','当前节点'],p.recent_initiatives.map(x=>[x.initiative_no,x.title,statusPill(x.status),x.current_node]))}</div></div>`;
  bindCommonDemandActions(appView);
  $$('.dash-approve').forEach(b=>b.addEventListener('click',()=>navigate(`approval/${b.dataset.id}`)));
  if($('#dashNew'))$('#dashNew').addEventListener('click',()=>navigate('demand-form'));
  if ($('#dashExport')) $('#dashExport').addEventListener('click',()=>window.open('/api/exports/platform-summary.csv','_blank'));
  $('#quickDemand').addEventListener('click',()=>navigate('demand-form')); $('#quickIni').addEventListener('click',()=>navigate('initiative-form')); $('#quickProject').addEventListener('click',()=>navigate('project-list')); $('#quickContract').addEventListener('click',()=>navigate('contract-list')); $('#quickBudget').addEventListener('click',()=>navigate('budget')); $('#quickTapd').addEventListener('click',()=>navigate('tapd'));
}

function statusPill(s){return `<span class="status ${statusClass(s)}">${esc(s)}</span>`;}
function progressCell(v, showLabel=true){const n=Number(v||0);const label=Number.isInteger(n)?n:n.toFixed(2).replace(/0+$/,'').replace(/\.$/,'');return `<div class="progress-cell"><div class="progress"><span style="width:${Math.max(0,Math.min(100,n))}%"></span></div>${showLabel?`<small>${label}%</small>`:''}</div>`;}
function simpleTable(headers, rows){return `<div class="table-wrap"><table class="table"><thead><tr>${headers.map(x=>`<th>${x}</th>`).join('')}</tr></thead><tbody>${rows.length?rows.map(r=>`<tr>${r.map(c=>`<td>${c??'—'}</td>`).join('')}</tr>`).join(''):`<tr><td colspan="${headers.length}" class="subtle">暂无数据</td></tr>`}</tbody></table></div>`;}

async function renderProject360() {
  setPage({ title: '项目360视图–机器人', iconName: '360', crumbs: ['项目视图'] });
  const projects=(await api('/api/projects')).data;
  const selected=Number(parseRoute().query.get('id')||projects[0]?.id||0);
  if(!selected){appView.innerHTML='<div class="empty">暂无项目，请先在项目管理中新建项目。</div>';return;}
  const d=(await api(`/api/project360/${selected}`)).data;
  appView.innerHTML=`<div class="section"><div class="toolbar"><div><div class="section-title">项目选择</div><div class="section-subtitle">选择项目后，360视图实时汇总需求、预算、任务、合同、结算与业务价值。</div></div><select id="p360Select" class="select" style="max-width:360px">${projects.map(p=>`<option value="${p.id}" ${p.id===selected?'selected':''}>${esc(p.project_no)} · ${esc(p.name)}</option>`).join('')}</select></div></div>
  <div class="grid-4"><div class="metric"><div class="k">项目状态</div><div class="v">${esc(d.status)}</div><div class="sub">经理 ${esc(d.manager)}</div></div><div class="metric"><div class="k">总体进度</div><div class="v">${d.progress}%</div>${progressCell(d.progress,false)}</div><div class="metric"><div class="k">关联需求</div><div class="v">${d.demands.length}</div><div class="sub">合同 ${d.contracts.length} · 结算 ${d.settlements.length}</div></div><div class="metric"><div class="k">预算执行率</div><div class="v">${d.budget?((d.budget.used_budget/d.budget.total_budget)*100).toFixed(1):'0.0'}%</div><div class="sub">${d.budget?`剩余 ¥${money(d.budget.total_budget-d.budget.used_budget)}`:'未关联预算'}</div></div></div>
  <div class="grid-2" style="margin-top:12px"><div class="section"><div class="section-title">项目任务</div>${simpleTable(['任务','负责人','状态','优先级','进度'],d.tasks.map(x=>[x.title,x.owner,statusPill(x.status),x.priority,progressCell(x.progress)]))}</div><div class="section"><div class="toolbar"><div><div class="section-title">项目360 AI机器人</div><div class="section-subtitle">Gazellio智能体将读取当前项目事实快照并保持多轮会话。</div></div><span class="status success">AI已接入</span></div><div class="chat-history" id="p360Chat"><div class="bubble ai">你好，我已获取 ${esc(d.project_no)} 的项目概况。可以问我项目进度、预算、风险、合同、结算与需求情况。</div></div><div class="chat-input"><textarea id="p360Q" maxlength="1000" placeholder="例如：这个项目当前有哪些风险？"></textarea><button id="p360Ask" class="btn primary">发送</button></div></div></div>
  <div class="grid-2" style="margin-top:12px"><div class="section"><div class="section-title">里程碑</div>${simpleTable(['里程碑','计划日期','状态','负责人'],d.milestones.map(x=>[x.name,x.planned_date||'—',statusPill(x.status),x.owner]))}</div><div class="section"><div class="section-title">业务价值</div>${simpleTable(['类型','指标','计划','已实现','状态'],d.values.map(x=>[x.value_type,x.metric_name,`${x.planned_value}${x.unit}`,`${x.realized_value}${x.unit}`,statusPill(x.status)]))}</div></div>`;
  $('#p360Select').addEventListener('change',e=>navigate('project360',{id:e.target.value}));
  const askProject=async()=>{
    const input=$('#p360Q'),button=$('#p360Ask'),q=input.value.trim();if(!q||button.disabled)return;
    const c=$('#p360Chat');c.innerHTML+=`<div class="bubble user">${esc(q)}</div><div class="bubble ai" id="p360Typing"><span class="ai-float-typing"><i></i><i></i><i></i></span></div>`;input.value='';button.disabled=true;c.scrollTop=c.scrollHeight;
    try{const result=await askAiAgent(q,{projectId:selected,source:'project360'});$('#p360Typing')?.remove();c.innerHTML+=`<div class="bubble ai">${esc(result.answer)}<span class="ai-message-meta">${esc(result.provider)}${result.fallback?' · 本地降级':''}</span></div>`;if(result.fallback)toast(`外部智能体暂不可用，项目机器人已使用本地回答：${result.warning}`,'warn');}
    catch(error){$('#p360Typing')?.remove();c.innerHTML+=`<div class="bubble ai">回答失败：${esc(error.message)}</div>`;}
    finally{button.disabled=false;c.scrollTop=c.scrollHeight;input.focus();}
  };
  $('#p360Ask').addEventListener('click',askProject);
  $('#p360Q').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();askProject();}});
}

async function renderValueOverview() {
  setPage({ title: '业务价值总览', iconName: 'grid', crumbs: ['经营分析'], actions: btn(`${icon('plus')} 新增价值指标`,'btn primary','newValue') });
  const items=(await api('/api/business-values')).data; const projects=(await api('/api/projects')).data;
  const planned=items.reduce((s,x)=>s+Number(x.planned_value||0),0),realized=items.reduce((s,x)=>s+Number(x.realized_value||0),0);
  appView.innerHTML=`<div class="grid-4"><div class="metric"><div class="k">价值指标</div><div class="v">${items.length}</div><div class="sub">跨项目持续跟踪</div></div><div class="metric"><div class="k">目标汇总</div><div class="v">${planned.toFixed(1)}</div><div class="sub">不同单位仅作条目汇总</div></div><div class="metric"><div class="k">已实现汇总</div><div class="v">${realized.toFixed(1)}</div><div class="sub">实时维护</div></div><div class="metric"><div class="k">平均达成</div><div class="v">${items.length?(items.reduce((s,x)=>s+(Number(x.planned_value)?Number(x.realized_value)/Number(x.planned_value)*100:0),0)/items.length).toFixed(1):0}%</div><div class="sub">按指标条目平均</div></div></div>
  <div class="section" style="margin-top:12px"><div class="section-title">业务价值指标台账</div><div class="table-wrap"><table class="table"><thead><tr><th>项目</th><th>价值类型</th><th>指标</th><th>计划值</th><th>已实现</th><th>达成率</th><th>周期</th><th>负责人</th><th>状态</th><th>操作</th></tr></thead><tbody>${items.map(x=>`<tr><td>${esc(x.project_no||'—')} ${esc(x.project_name||'')}</td><td>${esc(x.value_type)}</td><td>${esc(x.metric_name)}</td><td>${x.planned_value}${esc(x.unit)}</td><td>${x.realized_value}${esc(x.unit)}</td><td>${x.planned_value?(x.realized_value/x.planned_value*100).toFixed(1):0}%</td><td>${esc(x.period)}</td><td>${esc(x.owner)}</td><td>${statusPill(x.status)}</td><td><button class="link value-edit" data-id="${x.id}">编辑</button> <button class="link value-del" data-id="${x.id}">删除</button></td></tr>`).join('')}</tbody></table></div></div>`;
  const open=(item={})=>showModal(`<h3 id="modalTitle">${item.id?'编辑':'新增'}业务价值指标</h3><div class="form-row"><div class="label">项目</div><select id="vProject" class="select"><option value="">不指定</option>${projects.map(p=>`<option value="${p.id}" ${p.id===item.project_id?'selected':''}>${esc(p.project_no)} · ${esc(p.name)}</option>`).join('')}</select></div><div class="form-row"><div class="label required">价值类型</div><input id="vType" class="field" value="${esc(item.value_type||'效率')}"></div><div class="form-row"><div class="label required">指标名称</div><input id="vName" class="field" value="${esc(item.metric_name||'')}"></div><div class="grid-2"><div><label>计划值</label><input id="vPlan" type="number" class="field" value="${item.planned_value||0}"></div><div><label>已实现</label><input id="vReal" type="number" class="field" value="${item.realized_value||0}"></div></div><div class="grid-2" style="margin-top:10px"><div><label>单位</label><input id="vUnit" class="field" value="${esc(item.unit||'%')}"></div><div><label>周期</label><input id="vPeriod" class="field" value="${esc(item.period||'2026Q3')}"></div></div><div class="grid-2" style="margin-top:10px"><div><label>负责人</label><input id="vOwner" class="field" value="${esc(item.owner||'')}"></div><div><label>状态</label><select id="vStatus" class="select">${['跟踪中','已达成','暂停'].map(s=>`<option ${s===item.status?'selected':''}>${s}</option>`).join('')}</select></div></div><div class="modal-actions">${btn('取消','btn','vCancel')}${btn('保存','btn primary','vSave')}</div>`);
  const bindSave=(item={})=>{ $('#vCancel').addEventListener('click',closeModal);$('#vSave').addEventListener('click',async()=>{const payload={project_id:Number($('#vProject').value)||null,value_type:$('#vType').value,metric_name:$('#vName').value,planned_value:Number($('#vPlan').value),realized_value:Number($('#vReal').value),unit:$('#vUnit').value,period:$('#vPeriod').value,owner:$('#vOwner').value,status:$('#vStatus').value};await api(item.id?`/api/business-values/${item.id}`:'/api/business-values',{method:item.id?'PUT':'POST',body:JSON.stringify(payload)});closeModal();toast('已保存','success');renderValueOverview();});};
  $('#newValue').addEventListener('click',()=>{open();bindSave();});$$('.value-edit').forEach(b=>b.addEventListener('click',()=>{const item=items.find(x=>x.id===Number(b.dataset.id));open(item);bindSave(item);}));$$('.value-del').forEach(b=>b.addEventListener('click',async()=>{await api(`/api/business-values/${b.dataset.id}`,{method:'DELETE'});renderValueOverview();}));
}

async function renderDemandForm() {
  const route = parseRoute();
  const idFromQuery = Number(route.query.get('id') || 0);
  state.currentDemandId = idFromQuery || null;
  if (!idFromQuery) state.pendingFiles = [];
  let demand = idFromQuery ? (await api(`/api/demands/${idFromQuery}`)).data : null;
  const editable = !demand || ['草稿', '已驳回'].includes(demand.status);
  const allowed = ['applicant', 'admin'].includes(state.role);
  const actions = `${btn('保存草稿', 'btn', 'saveDraftTop', (!editable || !allowed) ? 'disabled' : '')}${btn('提交', 'btn primary', 'submitTop', (!editable || !allowed) ? 'disabled' : '')}`;
  setPage({ title: demand ? '编辑需求' : '新建需求', iconName: 'demand', crumbs: ['需求管理','需求列表'], actions });

  const user = currentUser();
  const priority = demand?.priority || '低';
  const selectedBudgets = demand?.budget_sources || [];
  const amount = Number(demand?.budget_amount || 0);
  appView.innerHTML = `${workflow(stageForDemand(demand))}
    ${!allowed ? `<div class="callout warn" style="margin-bottom:12px">当前身份为“${esc(state.meta.roles[state.role])}”，仅需求申请人或系统管理员可以创建/编辑需求。可以查看页面，但保存与提交按钮已禁用。</div>` : ''}
    ${demand && !editable ? `<div class="callout warn" style="margin-bottom:12px">该需求当前状态为“${esc(demand.status)}”，申请信息已进入流程，不允许直接修改。可前往需求详情查看审批、预算与TAPD进度。</div>` : ''}
    <div class="grid-2">
      <div>
        <div class="section">
          <div class="section-title">需求基本信息</div>
          <div class="form-row"><div class="label">需求编号</div><div><input class="field readonly" id="demandNo" readonly value="${esc(demand?.demand_no || '系统提交后自动生成')}"></div></div>
          <div class="form-row"><div class="label required">需求标题</div><div><input class="field" id="dTitle" maxlength="100" ${editable ? '' : 'disabled'} value="${esc(demand?.title || '')}" placeholder="请输入需求标题"><div class="help"><span id="titleCount">${(demand?.title || '').length}</span>/100</div></div></div>
          <div class="form-row"><div class="label">需求描述</div><div class="field-with-counter"><textarea class="textarea" id="dDesc" maxlength="5000" ${editable ? '' : 'disabled'} placeholder="详细描述需求内容">${esc(demand?.description || '')}</textarea><span class="counter"><span id="descCount">${(demand?.description || '').length}</span>/5000</span></div></div>
          <div class="form-row"><div class="label required">需求类型</div><div><select class="select" id="dType" ${editable ? '' : 'disabled'}>${state.meta.demandTypes.map((type) => `<option value="${esc(type)}" ${demand?.demand_type === type ? 'selected' : ''}>${esc(type)}</option>`).join('')}</select></div></div>
          <div class="form-row"><div class="label">预算出处</div><div class="multi-select-wrap">
            <div class="multi-select-field" id="budgetField" tabindex="0">${selectedBudgets.length ? selectedBudgets.map((name) => `<span class="tag" data-budget-tag="${esc(name)}">${esc(name)}${editable ? `<button type="button" class="remove-budget" data-budget="${esc(name)}">×</button>` : ''}</span>`).join('') : '<span class="multi-placeholder">请选择预算项目或常规预算</span>'}${editable ? `<button type="button" class="add-mini" id="budgetAdd">${icon('plus')}</button>` : ''}</div>
            <div class="multi-menu hidden" id="budgetMenu">${state.meta.budgets.map((budget) => `<label class="multi-option"><span><input type="checkbox" class="budget-option" value="${esc(budget.budget_name)}" ${selectedBudgets.includes(budget.budget_name) ? 'checked' : ''}> ${esc(budget.budget_name)}</span><span>${esc(budget.budget_no)}</span></label>`).join('')}</div>
          </div></div>
          <div class="form-row"><div class="label">预算金额</div><div><input class="field" id="dAmount" type="number" min="0" step="0.01" ${editable ? '' : 'disabled'} value="${amount}"><div class="help" id="amountHelp">预算金额超过50,000元时必须上传“预算依据”附件。</div></div></div>
          <div class="form-row two"><div class="label required">优先级</div><div class="inline" id="priorityGroup">${[['高','high'],['中','middle'],['低','low']].map(([text, cls]) => `<button type="button" class="radio-chip ${cls} ${priority === text ? 'selected' : ''}" data-priority="${text}" ${editable ? '' : 'disabled'}>${text}</button>`).join('')}</div><div class="label">申请人</div><div><input class="field readonly" id="dApplicant" readonly value="${esc(demand?.applicant || user.name)}"></div></div>
          <div class="form-row"><div class="label">附件上传</div><div>
            <div class="upload-box" id="uploadBox">
              <div class="upload-icon">${icon('upload')}</div>
              <div>拖拽文件到此处，或 <button class="link" type="button" id="chooseFileBtn" ${editable ? '' : 'disabled'}>点击上传</button></div>
              <div class="help">最多10个附件；单文件≤20MB；支持 doc/docx/xls/xlsx/pdf/png/jpg/jpeg/zip</div>
              <div class="upload-actions-line"><span>上传分类</span><select class="attachment-category" id="attachmentCategory" ${editable ? '' : 'disabled'}><option>普通附件</option><option>预算依据</option></select></div>
              <input type="file" id="fileInput" hidden multiple accept=".doc,.docx,.xls,.xlsx,.pdf,.png,.jpg,.jpeg,.zip">
            </div>
            <div id="pendingFileList">${pendingFileRows()}</div>
            ${demand?.attachments?.length ? `<div class="file-list" style="margin-top:8px">${demand.attachments.map((file) => `<div class="file-row"><span class="file-name">📎 ${esc(file.original_name)} <span class="status gray">${esc(file.category)}</span></span><span>${fmtSize(file.file_size)}</span><span class="action-group"><a class="link" href="/api/attachments/${file.id}/download" target="_blank">下载</a>${editable ? `<button class="link attachment-delete" data-id="${file.id}" type="button">删除</button>` : ''}</span></div>`).join('')}</div>` : ''}
          </div></div>
          <div class="rules-line"><span><b>*</b> 预算金额 &gt; 5万元：强制要求上传预算依据附件</span><span><b>*</b> 同一项目未关闭需求 &gt; 10条：提交时给出预警提示</span></div>
        </div>
      </div>
      <aside>
        <div class="section"><div class="section-title">预算校验预览</div><div id="budgetPreview"></div></div>
        <div class="section"><div class="section-title">流程说明</div><div>${approvalFlow(demand || { estimated_amount: amount, approvals: [], current_node: '' })}</div></div>
        <div class="section"><div class="section-title">当前操作</div><div class="callout">${demand ? `当前草稿/需求ID：${demand.id}<br>状态：${esc(demand.status)}` : '当前尚未保存到数据库，点击“保存草稿”后生成数据库记录。'}</div>${demand ? `<div style="margin-top:10px">${btn('查看需求详情','btn secondary','viewCurrentDetail')}</div>` : ''}</div>
      </aside>
    </div>`;

  bindDemandForm(demand, editable, allowed);
  updateBudgetPreview();
}

function pendingFileRows() {
  if (!state.pendingFiles.length) return '';
  return `<div class="file-list" style="margin-top:8px">${state.pendingFiles.map((item, index) => `<div class="file-row"><span class="file-name">📎 ${esc(item.file.name)} <span class="status gray">${esc(item.category)}</span></span><span>${fmtSize(item.file.size)}</span><button class="link pending-file-remove" data-index="${index}" type="button">移除</button></div>`).join('')}</div>`;
}

function getFormBudgetSources() {
  return $$('.budget-option:checked').map((input) => input.value);
}

function collectDemandPayload() {
  const selectedPriority = $('.radio-chip.selected')?.dataset.priority || '低';
  const user = currentUser();
  return {
    title: $('#dTitle').value.trim(),
    description: $('#dDesc').value.trim(),
    demand_type: $('#dType').value,
    budget_sources: getFormBudgetSources(),
    priority: selectedPriority,
    applicant: $('#dApplicant').value || user.name,
    applicant_code: user.id,
    applicant_dept: user.dept || '数字化管理部',
    budget_amount: Number($('#dAmount').value || 0)
  };
}

function renderBudgetFieldTags() {
  const field = $('#budgetField');
  if (!field) return;
  const selected = getFormBudgetSources();
  field.innerHTML = `${selected.length ? selected.map((name) => `<span class="tag">${esc(name)}<button type="button" class="remove-budget" data-budget="${esc(name)}">×</button></span>`).join('') : '<span class="multi-placeholder">请选择预算项目或常规预算</span>'}<button type="button" class="add-mini" id="budgetAdd">${icon('plus')}</button>`;
  $('#budgetAdd').addEventListener('click', (event) => { event.stopPropagation(); $('#budgetMenu').classList.toggle('hidden'); });
  $$('.remove-budget').forEach((button) => button.addEventListener('click', (event) => {
    event.stopPropagation();
    const input = $(`.budget-option[value="${CSS.escape(button.dataset.budget)}"]`);
    if (input) input.checked = false;
    renderBudgetFieldTags();
    updateBudgetPreview();
  }));
}

function updateBudgetPreview() {
  const preview = $('#budgetPreview');
  if (!preview) return;
  const selected = getFormBudgetSources();
  if (!selected.length) {
    preview.innerHTML = '<div class="empty" style="min-height:120px">选择预算出处后展示实时预算信息</div>';
    return;
  }
  const budget = state.meta.budgets.find((b) => b.budget_name === selected[0]);
  if (!budget) return;
  const amount = Number($('#dAmount')?.value || 0);
  const currentRate = budget.total_budget ? budget.used_budget / budget.total_budget * 100 : 0;
  const afterRate = budget.total_budget ? (budget.used_budget + amount) / budget.total_budget * 100 : 0;
  const remaining = budget.total_budget - budget.used_budget;
  preview.innerHTML = `<div class="subtle">${esc(budget.budget_no)}</div><strong style="display:block;margin:4px 0 10px">${esc(budget.budget_name)}</strong>
    <div class="info-grid" style="grid-template-columns:90px 1fr"><div>总预算</div><div>¥ ${money(budget.total_budget)}</div><div>已使用</div><div>¥ ${money(budget.used_budget)}</div><div>剩余可用</div><div>¥ ${money(remaining)}</div><div>当前执行率</div><div>${currentRate.toFixed(2)}%</div></div>
    <div style="margin-top:10px"><div class="progress"><span style="width:${Math.min(100, afterRate)}%"></span></div><div class="help">本次金额计入后预计执行率 ${afterRate.toFixed(2)}%</div></div>
    <div class="callout ${amount > remaining ? 'danger' : afterRate >= 95 ? 'warn' : 'success'}" style="margin-top:10px">${amount > remaining ? '预算不足' : '当前预算可用'}${afterRate >= 95 ? '，预计触发95%执行率预警' : ''}</div>`;
}

function bindDemandForm(demand, editable, allowed) {
  $('#dTitle').addEventListener('input', (event) => { $('#titleCount').textContent = event.target.value.length; });
  $('#dDesc').addEventListener('input', (event) => { $('#descCount').textContent = event.target.value.length; });
  $('#dAmount').addEventListener('input', () => {
    updateBudgetPreview();
    const amount = Number($('#dAmount').value || 0);
    $('#amountHelp').className = `help${amount > 50000 ? ' danger-text' : ''}`;
  });
  $$('.radio-chip').forEach((button) => button.addEventListener('click', () => {
    if (!editable) return;
    $$('.radio-chip').forEach((b) => b.classList.remove('selected'));
    button.classList.add('selected');
  }));

  const budgetField = $('#budgetField');
  budgetField.addEventListener('click', () => { if (editable) $('#budgetMenu').classList.toggle('hidden'); });
  $$('.budget-option').forEach((input) => input.addEventListener('change', () => {
    renderBudgetFieldTags();
    updateBudgetPreview();
  }));
  renderBudgetFieldTags();
  document.addEventListener('click', function closeBudgetMenu(event) {
    if (!event.target.closest('.multi-select-wrap')) $('#budgetMenu')?.classList.add('hidden');
  }, { once: true });

  const fileInput = $('#fileInput');
  $('#chooseFileBtn').addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', () => addPendingFiles([...fileInput.files]));
  const uploadBox = $('#uploadBox');
  ['dragenter', 'dragover'].forEach((eventName) => uploadBox.addEventListener(eventName, (event) => {
    event.preventDefault();
    if (editable) uploadBox.classList.add('dragover');
  }));
  ['dragleave', 'drop'].forEach((eventName) => uploadBox.addEventListener(eventName, (event) => {
    event.preventDefault();
    uploadBox.classList.remove('dragover');
  }));
  uploadBox.addEventListener('drop', (event) => { if (editable) addPendingFiles([...event.dataTransfer.files]); });
  bindPendingFileRemove();

  $$('.attachment-delete').forEach((button) => button.addEventListener('click', async () => {
    if (!confirm('确认删除该附件？')) return;
    try {
      await api(`/api/attachments/${button.dataset.id}`, { method: 'DELETE' });
      toast('附件已删除', 'success');
      await renderDemandForm();
    } catch (error) { toast(error.message, 'error'); }
  }));

  const save = async (submitAfter = false) => {
    if (!allowed || !editable) return;
    try {
      const payload = collectDemandPayload();
      if (!payload.title) throw new Error('需求标题不能为空');
      if (!payload.demand_type) throw new Error('请选择需求类型');
      let result;
      if (state.currentDemandId) {
        result = await api(`/api/demands/${state.currentDemandId}`, { method: 'PUT', body: JSON.stringify(payload) });
      } else {
        result = await api('/api/demands', { method: 'POST', body: JSON.stringify(payload) });
        state.currentDemandId = result.data.id;
      }
      await uploadPendingFiles(state.currentDemandId);
      if (!submitAfter) {
        toast('草稿已保存', 'success');
        navigate('demand-form', { id: state.currentDemandId });
        return;
      }
      await submitDemand(state.currentDemandId);
    } catch (error) {
      toast(error.message, 'error');
    }
  };
  $('#saveDraftTop').addEventListener('click', () => save(false));
  $('#submitTop').addEventListener('click', () => save(true));
  if ($('#viewCurrentDetail')) $('#viewCurrentDetail').addEventListener('click', () => navigate(`demand-detail/${demand.id}`));
}

function addPendingFiles(files) {
  const allowed = new Set(['doc','docx','xls','xlsx','pdf','png','jpg','jpeg','zip']);
  const existingCount = state.pendingFiles.length + (parseRoute().query.get('id') ? 0 : 0);
  const category = $('#attachmentCategory')?.value || '普通附件';
  for (const file of files) {
    const ext = file.name.includes('.') ? file.name.split('.').pop().toLowerCase() : '';
    if (!allowed.has(ext)) { toast(`不支持附件类型：${file.name}`, 'error'); continue; }
    if (file.size > 20 * 1024 * 1024) { toast(`文件超过20MB：${file.name}`, 'error'); continue; }
    if (existingCount + state.pendingFiles.length >= 10) { toast('单条需求最多10个附件', 'error'); break; }
    state.pendingFiles.push({ file, category });
  }
  $('#pendingFileList').innerHTML = pendingFileRows();
  bindPendingFileRemove();
}

function bindPendingFileRemove() {
  $$('.pending-file-remove').forEach((button) => button.addEventListener('click', () => {
    state.pendingFiles.splice(Number(button.dataset.index), 1);
    $('#pendingFileList').innerHTML = pendingFileRows();
    bindPendingFileRemove();
  }));
}

async function uploadPendingFiles(demandId) {
  const pending = [...state.pendingFiles];
  for (const item of pending) {
    const form = new FormData();
    form.append('file', item.file);
    await api(`/api/demands/${demandId}/attachments?category=${encodeURIComponent(item.category)}`, { method: 'POST', body: form });
  }
  state.pendingFiles = [];
}

async function submitDemand(demandId, confirmWarning = false) {
  try {
    const result = await api(`/api/demands/${demandId}/submit?confirm_warning=${confirmWarning ? 'true' : 'false'}`, { method: 'POST' });
    toast(result.message || '提交成功', 'success');
    state.pendingFiles = [];
    navigate(`demand-detail/${demandId}`, { tab: 'approval' });
  } catch (error) {
    if (error.data?.details?.warning === 'UNFINISHED_OVER_10' && !confirmWarning) {
      showModal(`<h3 id="modalTitle">提交预警</h3><div class="callout warn">同一预算项目下未关闭需求已超过10条（当前 ${error.data.details.count} 条）。根据需求提交规则，需要用户确认后才能继续提交。</div><div class="modal-actions">${btn('返回检查','btn','warningCancel')}${btn('确认提交','btn primary','warningConfirm')}</div>`);
      $('#warningCancel').addEventListener('click', closeModal);
      $('#warningConfirm').addEventListener('click', async () => { closeModal(); await submitDemand(demandId, true); });
      return;
    }
    throw error;
  }
}

async function renderDemandList() {
  setPage({ title: '需求列表', iconName: 'search', crumbs: ['需求管理'], actions: `${btn('导出当前结果','btn','exportDemandList')}${hasPermission('demand.create')?btn(`${icon('plus')} 新建需求`, 'btn primary', 'newDemandFromList'):''}` });
  const route = parseRoute();
  const q = route.query.get('q') || '';
  const status = route.query.get('status') || '';
  const result = (await api(`/api/demands?q=${encodeURIComponent(q)}&status=${encodeURIComponent(status)}&page_size=100`)).data;
  const statuses = [...new Set(result.items.map((d) => d.status))];
  appView.innerHTML = `<div class="section">
    <div class="toolbar"><div class="filters"><input class="field" id="demandSearch" value="${esc(q)}" placeholder="需求编号 / 标题 / 申请人"><select class="select" id="demandStatus"><option value="">全部状态</option>${statuses.map((s) => `<option ${s === status ? 'selected' : ''}>${esc(s)}</option>`).join('')}</select>${btn('查询','btn small','runDemandSearch')}${btn('重置','btn small ghost','resetDemandSearch')}</div><div class="subtle">共 ${result.total} 条</div></div>
    ${demandTable(result.items)}
  </div>`;
  bindCommonDemandActions(appView);
  if ($('#newDemandFromList')) $('#newDemandFromList').addEventListener('click', () => { state.currentDemandId = null; state.pendingFiles=[]; navigate('demand-form'); });
  $('#runDemandSearch').addEventListener('click', () => navigate('demand-list', { q: $('#demandSearch').value.trim(), status: $('#demandStatus').value }));
  $('#resetDemandSearch').addEventListener('click', () => navigate('demand-list'));
  $('#demandSearch').addEventListener('keydown', (event) => { if (event.key === 'Enter') $('#runDemandSearch').click(); });
  $('#exportDemandList').addEventListener('click', () => exportDemandCsv(result.items));
}

function exportDemandCsv(items) {
  const rows = [['需求编号','需求标题','申请人','优先级','状态','预算金额','预估金额','TAPD状态']];
  items.forEach((d) => rows.push([d.demand_no || '草稿', d.title, d.applicant, d.priority, d.status, d.budget_amount, d.estimated_amount, d.tapd_status || '']));
  const csv = '\ufeff' + rows.map((row) => row.map((cell) => `"${String(cell ?? '').replaceAll('"','""')}"`).join(',')).join('\n');
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
  const a = document.createElement('a'); a.href = url; a.download = `需求列表_${new Date().toISOString().slice(0,10)}.csv`; a.click(); URL.revokeObjectURL(url);
}

async function renderDemandDetail(id, query) {
  if (!id) return navigate('demand-list');
  state.currentDemandId = id;
  const demand = (await api(`/api/demands/${id}`)).data;
  const tab = query.get('tab') || 'basic';
  state.detailTab = tab;
  const actions = `${btn(`${icon('back')} 返回列表`,'btn','backToList')}${(demand.status === '草稿' || demand.status === '已驳回') && ['applicant','admin'].includes(state.role) ? btn('编辑需求','btn','editDemandDetail') : ''}${roleCanApprove(demand) ? btn('进入当前审批','btn primary','gotoApproval') : ''}${demand.tapd_id && ['applicant','product_manager','project_manager','admin'].includes(state.role) ? btn('同步TAPD状态','btn primary','detailSyncTapd') : ''}`;
  setPage({ title: '需求详情', iconName: 'demand', crumbs: ['需求管理', demand.demand_no || '草稿'], actions });
  const tabs = [['basic','基本信息'],['approval','审批记录'],['fp','功能点评估'],['budget','预算与分摊'],['tapd','TAPD与工时']];
  appView.innerHTML = `${workflow(stageForDemand(demand))}
    <div class="section">
      <div class="detail-head"><div><div class="detail-no">${esc(demand.demand_no || '草稿')}</div><h2 class="detail-title">${esc(demand.title)}</h2><div class="detail-meta"><span>申请人：${esc(demand.applicant)}</span><span>优先级：${esc(demand.priority)}</span><span>类型：${esc(demand.demand_type)}</span></div></div><span class="status ${statusClass(demand.status)}">${esc(demand.status)}</span></div>
      <div class="tabs">${tabs.map(([key,label]) => `<button class="tab ${key === tab ? 'active' : ''}" data-tab="${key}" type="button">${label}</button>`).join('')}</div>
      <div id="detailTabContent">${renderDemandDetailTab(demand, tab)}</div>
    </div>`;
  $$('.tab').forEach((button) => button.addEventListener('click', () => navigate(`demand-detail/${id}`, { tab: button.dataset.tab })));
  $('#backToList').addEventListener('click', () => navigate('demand-list'));
  if ($('#editDemandDetail')) $('#editDemandDetail').addEventListener('click', () => { navigate('demand-form', { id }); });
  if ($('#gotoApproval')) $('#gotoApproval').addEventListener('click', () => { navigate(`approval/${id}`); });
  if ($('#detailSyncTapd')) $('#detailSyncTapd').addEventListener('click', () => openTapdSyncModal(demand));
  bindDetailTabActions(demand, tab);
}

function renderDemandDetailTab(demand, tab) {
  if (tab === 'approval') return `<div class="grid-2"><div><div class="section flat"><div class="section-title">审批记录</div>${approvalRecords(demand.approvals)}</div></div><aside><div class="section flat"><div class="section-title">审批节点</div>${approvalFlow(demand)}</div></aside></div>`;
  if (tab === 'fp') return `<div class="section flat"><div class="section-title">功能点评估</div>${functionPointTable(demand.function_points, false)}<div class="grid-3" style="margin-top:12px"><div class="metric soft"><div class="k">功能点记录</div><div class="v">${demand.function_points.length}</div></div><div class="metric soft"><div class="k">功能点合计</div><div class="v">${demand.function_points.reduce((s,x)=>s+Number(x.fp_count||0),0).toFixed(2)}</div></div><div class="metric soft"><div class="k">预估金额</div><div class="v">¥ ${money(demand.estimated_amount)}</div></div></div></div>`;
  if (tab === 'budget') return `<div class="section flat"><div class="section-title">预算校验</div>${budgetSnapshot(demand.budget_snapshot)}<div class="section-title" style="margin-top:18px">费用分摊</div>${allocationTable(demand.allocations)}</div>`;
  if (tab === 'tapd') {
    const reqs = demand.tapd_requirements || [];
    const tasks = demand.tapd_tasks || [];
    const costs = demand.tapd_costs || [];
    const reqRows = reqs.map(r=>[
      esc(r.system_name), esc(r.tapd_id), r.tapd_url ? `<a class="link" href="${esc(r.tapd_url)}" target="_blank">打开TAPD</a>` : '—',
      esc(r.tapd_status||'—'), statusPill(r.sync_status||'未同步'), esc(r.last_sync_at||'—')
    ]);
    const taskRows = tasks.map(t=>[
      esc(t.external_task_id), esc(t.title), esc(t.description||'—'), esc(t.task_type||'—'), t.planned_start||'—', t.planned_end||'—',
      `${Number(t.estimated_hours||0).toFixed(1)}h`, esc(t.creator||'—'), t.external_created_at||'—', t.completed_at||'—',
      `${Number(t.completed_hours||0).toFixed(1)}h`, `${Number(t.remaining_hours||0).toFixed(1)}h`, `${Number(t.overrun_hours||0).toFixed(1)}h`
    ]);
    const costRows = costs.map(c=>[c.spent_date||'—', `${Number(c.hours||0).toFixed(1)}h`, esc(c.creator||'—'), esc(c.description||'—'), esc(c.task_external_id||'—')]);
    const deviation = Number(demand.estimated_hours||0) ? Math.abs(Number(demand.actual_hours||0)-Number(demand.estimated_hours||0))/Number(demand.estimated_hours||0)*100 : 0;
    return `<div class="section flat"><div class="section-title">TAPD需求创建结果</div>${reqRows.length ? simpleTable(['归属系统','TAPD需求ID','需求链接','TAPD状态','同步状态','最近同步'],reqRows) : '<div class="empty">尚未创建TAPD需求</div>'}</div>
    <div class="section flat" style="margin-top:12px"><div class="section-title">需求信息回读</div><div class="info-grid"><div>需求描述</div><div>${esc(demand.tapd_description||demand.description||'—')}</div><div>研发主体</div><div>${esc(demand.rd_owner||'—')}</div><div>研发部门</div><div>${esc(demand.rd_department||'—')}</div><div>内部人天数</div><div>${Number(demand.internal_days||0).toFixed(1)}</div><div>外部人天数</div><div>${Number(demand.external_days||0).toFixed(1)}</div><div>计划上线时间</div><div>${esc(demand.planned_online_date||'—')}</div><div>实际升级时间</div><div>${esc(demand.actual_online_date||'—')}</div><div>需求状态</div><div>${esc(demand.tapd_status||'—')} → ${esc(demand.status||'—')}</div><div>用户测试时间</div><div>${esc(demand.user_test_date||'—')}</div><div>测试完成时间</div><div>${esc(demand.test_complete_date||'—')}</div><div>需求确认时间</div><div>${esc(demand.demand_confirm_date||'—')}</div><div>同步来源</div><div>${esc(demand.last_sync_source||'—')}</div></div></div>
    <div class="section flat" style="margin-top:12px"><div class="section-title">需求关联任务信息</div>${taskRows.length ? simpleTable(['任务ID','任务标题','任务描述','任务分类','预计开始','预计结束','预估工时','创建人','创建时间','完成时间','完成工时','剩余工时','超出工时'],taskRows) : '<div class="empty">暂无任务回读数据</div>'}</div>
    <div class="section flat" style="margin-top:12px"><div class="section-title">任务关联花费信息</div>${costRows.length ? simpleTable(['花费日期','花费工时','花费创建人','花费描述','关联任务'],costRows) : '<div class="empty">暂无花费回读数据</div>'}</div>
    <div class="grid-2" style="margin-top:12px"><div class="section flat"><div class="section-title">工时偏差</div><div class="info-grid" style="grid-template-columns:120px 1fr"><div>预估工时</div><div>${Number(demand.estimated_hours||0).toFixed(1)} h</div><div>实际工时</div><div>${Number(demand.actual_hours||0).toFixed(1)} h</div><div>偏差率</div><div>${deviation.toFixed(1)}%</div></div><div class="callout ${deviation>30?'warn':'success'}" style="margin-top:10px">${deviation>30?'偏差率 > 30%，系统已同时向产品经理和项目经理发送预警。':'当前未超过30%预警阈值。'}</div></div><div class="section flat"><div class="section-title">同步记录</div>${(demand.tapd_sync_runs||[]).length ? simpleTable(['来源','变更数','结果','说明','时间'],demand.tapd_sync_runs.map(x=>[esc(x.source),x.changed_count,x.success?'成功':'失败',esc(x.message),esc(x.created_at)])) : '<div class="empty">暂无同步记录</div>'}</div></div>`;
  }
  return `<div class="info-grid"><div>需求编号</div><div>${esc(demand.demand_no || '提交后生成')}</div><div>需求类型</div><div>${esc(demand.demand_type)}</div><div>需求标题</div><div>${esc(demand.title)}</div><div>申请人</div><div>${esc(demand.applicant)}</div><div>优先级</div><div>${esc(demand.priority)}</div><div>当前节点</div><div>${esc(demand.current_node)}</div><div>预算出处</div><div>${(demand.budget_sources || []).map((x)=>`<span class="tag">${esc(x)}</span>`).join(' ') || '—'}</div><div>申请预算金额</div><div>¥ ${money(demand.budget_amount)}</div><div>预估金额</div><div>¥ ${money(demand.estimated_amount)}</div><div>需求描述</div><div>${esc(demand.description || '—')}</div></div><div class="section-title" style="margin-top:18px">附件</div>${demand.attachments.length ? `<div class="file-list">${demand.attachments.map((file)=>`<div class="file-row"><span class="file-name">📎 ${esc(file.original_name)} <span class="status gray">${esc(file.category)}</span></span><span>${fmtSize(file.file_size)}</span><a class="link" href="/api/attachments/${file.id}/download" target="_blank">下载</a></div>`).join('')}</div>` : '<div class="empty" style="min-height:80px">暂无附件</div>'}`;
}

function bindDetailTabActions(demand, tab) {
  if (tab === 'fp') $$('.fp-detail').forEach((button) => button.addEventListener('click', () => openFunctionPointDetail(demand, Number(button.dataset.id))));
}

async function renderApprovals() {
  setPage({ title: '需求审批', iconName: 'approve', crumbs: ['需求申请'], actions: btn('刷新待办','btn','refreshApproval') });
  const pending = (await api('/api/approvals/pending')).data;
  appView.innerHTML = `${workflow(2)}<div class="section"><div class="toolbar"><div><div class="section-title" style="margin-bottom:3px">我的待办</div><div class="subtle">当前身份：${esc(state.meta.roles[state.role])}</div></div><span class="status warn">${pending.length} 条待处理</span></div>${demandTable(pending,{approvalMode:true})}</div>`;
  bindCommonDemandActions(appView);
  $$('.demand-open').forEach((button) => {
    button.replaceWith(button.cloneNode(true));
  });
  $$('.demand-open').forEach((button) => button.addEventListener('click', () => navigate(`approval/${button.dataset.id}`)));
  $('#refreshApproval').addEventListener('click', renderApprovals);
}

async function renderApprovalDetail(id) {
  if (!id) return navigate('approvals');
  state.currentDemandId = id;
  const demand = (await api(`/api/demands/${id}`)).data;
  const canApprove = roleCanApprove(demand);
  setPage({ title: '需求审批', iconName: 'approve', crumbs: ['需求申请', demand.demand_no || '草稿'], actions: `${btn(`${icon('back')} 返回待办`,'btn','approvalBack')}${btn('查看完整详情','btn','approvalFullDetail')}` });
  let special = '';
  if (demand.current_node === '产品经理审批') {
    special = `<div class="callout ${demand.function_points.length ? 'success' : 'warn'}">产品经理节点必须完成至少1条功能点评估。当前已关联 ${demand.function_points.length} 条，预估金额 ¥${money(demand.estimated_amount)}。<div style="margin-top:8px">${btn('进入费用评估与预算','btn secondary','goProductEval')}</div></div>`;
  }
  if (demand.current_node === '财务审批') {
    special = `<div>${budgetSnapshot(demand.budget_snapshot)}</div>`;
  }
  const returnTargets = approvalReturnTargets(demand);
  const currentOa = [...(demand.oa_tasks || [])].reverse().find(t => t.node === demand.current_node && t.status === '待处理');
  appView.innerHTML = `${workflow(2)}<div class="approval-panel"><div>
    <div class="section"><div class="detail-head"><div><div class="detail-no">${esc(demand.demand_no || '草稿')}</div><h2 class="detail-title">${esc(demand.title)}</h2><div class="detail-meta"><span>申请人：${esc(demand.applicant)}</span><span>预估金额：¥${money(demand.estimated_amount || demand.budget_amount)}</span></div></div><span class="status ${statusClass(demand.current_node)}">${esc(demand.current_node)}</span></div></div>
    <div class="section"><div class="section-title">需求信息</div><p>${esc(demand.description || '未填写需求描述')}</p><div class="info-grid"><div>需求类型</div><div>${esc(demand.demand_type)}</div><div>优先级</div><div>${esc(demand.priority)}</div><div>预算出处</div><div>${esc((demand.budget_sources||[]).join('、') || '—')}</div><div>功能点</div><div>${demand.function_points.length} 条</div></div></div>
    <div class="section"><div class="section-title">当前节点专项检查</div>${special || '<div class="callout">请核对需求合理性、附件及历史审批意见后做出审批结论。</div>'}</div>
    <div class="section"><div class="section-title">历史审批记录</div>${approvalRecords(demand.approvals)}</div>
  </div><aside class="approval-actions">
    <div class="section"><div class="section-title">审批流程</div>${approvalFlow(demand)}</div>
    <div class="section"><div class="section-title">审批处理</div>${canApprove ? `<div class="callout ${currentOa ? 'success' : 'warn'}" style="margin-bottom:10px">${currentOa ? `OA待办 ${esc(currentOa.external_task_id)} · 截止 ${esc(String(currentOa.due_at||'').replace('T',' ').slice(0,16))}` : '当前节点未发现活动OA待办，请检查OA集成状态。'}</div><label class="subtle" for="approvalComment">审批意见</label><textarea id="approvalComment" class="textarea" placeholder="请输入审批意见"></textarea><label class="subtle" for="approvalReturnTo" style="display:block;margin-top:10px">驳回退回节点</label><select id="approvalReturnTo" class="select">${returnTargets.map(x=>`<option>${esc(x)}</option>`).join('')}</select><div class="help">POC支持驳回到任意前置节点；选择“需求申请”时退回申请人修改后重新提交。</div><div class="modal-actions">${btn('驳回','btn danger','rejectApproval')}${btn('通过','btn primary','passApproval')}</div>` : `<div class="callout warn">当前角色无权处理该节点。该节点需要与审批角色同时匹配。</div>`}</div>
  </aside></div>`;
  $('#approvalBack').addEventListener('click', () => navigate('approvals'));
  $('#approvalFullDetail').addEventListener('click', () => navigate(`demand-detail/${id}`, { tab: 'approval' }));
  if ($('#goProductEval')) $('#goProductEval').addEventListener('click', () => { navigate('product-eval', { id }); });
  if (canApprove) {
    $('#passApproval').addEventListener('click', () => approveDemand(id, '通过'));
    $('#rejectApproval').addEventListener('click', () => approveDemand(id, '驳回'));
  }
}

async function approveDemand(id, action) {
  const comment = $('#approvalComment')?.value.trim() || '';
  if (action === '驳回' && !comment) { toast('驳回时请填写审批意见', 'error'); return; }
  const return_to = action === '驳回' ? ($('#approvalReturnTo')?.value || '需求申请') : null;
  try {
    const result = await api(`/api/demands/${id}/approve`, { method: 'POST', body: JSON.stringify({ action, comment, return_to }) });
    toast(result.message || `已${action}`, 'success');
    await loadNotifications(false);
    navigate(`demand-detail/${id}`, { tab: 'approval' });
  } catch (error) { toast(error.message, 'error'); }
}

async function eligibleDemands() {
  return (await api('/api/demands?page_size=100')).data.items.filter((d) => d.status !== '草稿');
}

async function renderProductEval(query) {
  const demands = await eligibleDemands();
  const routeId = Number(query.get('id') || 0);
  if (routeId) state.currentDemandId = routeId;
  if (!state.currentDemandId && demands.length) state.currentDemandId = demands[0].id;
  const demand = state.currentDemandId ? (await api(`/api/demands/${state.currentDemandId}`)).data : null;
  const editable = demand && ['product_manager','admin'].includes(state.role);
  setPage({ title: '费用评估与预算', iconName: 'budget', crumbs: ['需求申请'], actions: demand ? `${btn('模板下载','btn','fpTemplate')}${btn('导入功能点','btn','fpImport')}${btn('导出功能点','btn','fpExport')}${btn(`${icon('plus')} 添加功能点`,'btn primary','addFunctionPoint')}` : '' });
  if (!demand) { appView.innerHTML = '<div class="empty">暂无可评估需求</div>'; return; }
  appView.innerHTML = `${workflow(2)}<div class="fp-layout">
    <aside class="section"><div class="section-title">待评估需求</div><div class="demand-selector-list">${demands.map((d) => `<button class="demand-selector-card ${d.id===demand.id?'active':''}" type="button" data-id="${d.id}"><strong>${esc(d.demand_no || '草稿')} · ${esc(d.title)}</strong><span class="status ${statusClass(d.status)}">${esc(d.status)}</span><div class="help">预估 ¥${money(d.estimated_amount || d.budget_amount)}</div></button>`).join('')}</div></aside>
    <div>
      <div class="section"><div class="detail-head"><div><div class="detail-no">${esc(demand.demand_no)}</div><h2 class="detail-title">${esc(demand.title)}</h2></div><span class="status ${statusClass(demand.status)}">${esc(demand.status)}</span></div><div class="grid-3"><div class="metric soft"><div class="k">功能点总数</div><div class="v">${demand.function_points.reduce((s,x)=>s+Number(x.fp_count||0),0).toFixed(2)}</div></div><div class="metric soft"><div class="k">预估金额</div><div class="v">¥ ${money(demand.estimated_amount)}</div></div><div class="metric soft"><div class="k">分摊比例</div><div class="v">${demand.allocations.reduce((s,x)=>s+Number(x.ratio||0),0).toFixed(2)}%</div></div></div></div>
      ${!editable ? `<div class="callout warn" style="margin-bottom:12px">当前身份为“${esc(state.meta.roles[state.role])}”，可查看评估结果，但只有产品经理或管理员可以维护功能点与费用分摊。</div>` : ''}
      <div class="section"><div class="toolbar"><div class="section-title" style="margin:0">功能点评估明细</div>${editable ? btn('添加功能点','btn small primary','addFunctionPointInline') : ''}</div>${functionPointTable(demand.function_points, editable)}</div>
      <div class="section"><div class="section-title">费用分摊</div>${editable ? allocationEditor(demand) : allocationTable(demand.allocations)}</div>
      <div class="section"><div class="section-title">预算校验与执行率</div>${budgetSnapshot(demand.budget_snapshot)}</div>
      ${demand.current_node === '产品经理审批' && roleCanApprove(demand) ? `<div class="section"><div class="section-title">产品经理审批</div><div class="callout">完成评估与费用分摊后，可直接在此节点提交审批结论。</div><div class="form-row compact" style="margin-top:12px"><div class="label">审批意见</div><textarea class="textarea" id="productApprovalComment" placeholder="填写评估说明"></textarea></div><div class="form-row compact"><div class="label">驳回退回节点</div><select class="select" id="productReturnTo">${approvalReturnTargets(demand).map(x=>`<option>${esc(x)}</option>`).join('')}</select></div><div class="modal-actions">${btn('驳回','btn danger','productReject')}${btn('通过并进入财务审批','btn primary','productPass')}</div></div>` : ''}
    </div>
  </div><input type="file" id="fpImportFile" accept=".xlsx" hidden>`;
  bindProductEval(demand, editable);
}

function allocationEditor(demand) {
  const systems = [...new Set(demand.function_points.map((fp) => fp.system_name))];
  const rows = demand.allocations.length ? demand.allocations : [{ system_name: systems[0] || '', expense_subject: '集团', expense_source: demand.budget_sources[0] || '', ratio: 100, department: '财务部' }];
  return `<div class="table-wrap"><table class="table"><thead><tr><th>归属系统</th><th>费用主体</th><th>费用出处</th><th>分摊比例</th><th>分摊金额</th><th>费用归属部门</th><th>操作</th></tr></thead><tbody id="allocBody">${rows.map((row,index)=>allocationRow(row,index,demand,systems)).join('')}</tbody></table></div><div class="toolbar" style="margin-top:10px"><div>${btn('+ 添加分摊行','btn small','addAllocation')} <span class="subtle">所有行比例合计不得超过100%</span></div><div>合计：<strong id="ratioSum">0%</strong> ${btn('保存分摊','btn primary small','saveAllocation')}</div></div>`;
}

function allocationRow(row, index, demand, systems) {
  const base = Number(demand.estimated_amount || demand.budget_amount || 0);
  const amount = base * Number(row.ratio || 0) / 100;
  const budgetOptions = state.meta.budgets.map((b) => b.budget_name);
  return `<tr class="alloc-row" data-index="${index}"><td><select class="select alloc-system">${['', ...systems].map((s) => `<option value="${esc(s)}" ${s === (row.system_name||'') ? 'selected' : ''}>${esc(s || '全部系统')}</option>`).join('')}</select></td><td><select class="select alloc-subject">${['集团','产险','寿险'].map((x)=>`<option ${x===row.expense_subject?'selected':''}>${x}</option>`).join('')}</select></td><td><select class="select alloc-source">${budgetOptions.map((x)=>`<option ${x===row.expense_source?'selected':''}>${esc(x)}</option>`).join('')}</select></td><td><input class="field alloc-ratio" type="number" min="0" max="100" step="0.01" value="${Number(row.ratio||0)}"></td><td class="alloc-amount">¥ ${money(amount)}</td><td><select class="select alloc-dept">${['财务部','数字化管理部','办公室','产品研发部'].map((x)=>`<option ${x===row.department?'selected':''}>${x}</option>`).join('')}</select></td><td><button class="link alloc-remove" type="button">删除</button></td></tr>`;
}

function bindProductEval(demand, editable) {
  $$('.demand-selector-card').forEach((button) => button.addEventListener('click', () => { state.currentDemandId = Number(button.dataset.id); navigate('product-eval', { id: button.dataset.id }); }));
  const goAdd = () => { navigate('function-points', { demandId: demand.id }); };
  $('#addFunctionPoint')?.addEventListener('click', goAdd);
  $('#addFunctionPointInline')?.addEventListener('click', goAdd);
  $('#fpTemplate')?.addEventListener('click', () => { location.href = '/api/function-points/template'; });
  $('#fpExport')?.addEventListener('click', () => { location.href = `/api/demands/${demand.id}/function-points/export`; });
  $('#fpImport')?.addEventListener('click', () => $('#fpImportFile').click());
  $('#fpImportFile')?.addEventListener('change', importFunctionPoints);
  $$('.fp-detail').forEach((button) => button.addEventListener('click', () => openFunctionPointDetail(demand, Number(button.dataset.id))));
  $$('.fp-edit').forEach((button) => button.addEventListener('click', () => openFunctionPointEdit(demand, Number(button.dataset.id))));
  $$('.fp-delete').forEach((button) => button.addEventListener('click', async () => {
    if (!confirm('确认删除该功能点评估？')) return;
    try { await api(`/api/function-points/${button.dataset.id}`, { method: 'DELETE' }); toast('功能点已删除','success'); await renderRoute(); } catch (error) { toast(error.message,'error'); }
  }));
  if (editable) {
    bindAllocationEditor(demand);
  }
  $('#productPass')?.addEventListener('click', async () => {
    const comment = $('#productApprovalComment').value.trim();
    try { const r = await api(`/api/demands/${demand.id}/approve`, { method:'POST', body:JSON.stringify({action:'通过', comment}) }); toast(r.message,'success'); navigate(`demand-detail/${demand.id}`,{tab:'approval'}); } catch(error){ toast(error.message,'error'); }
  });
  $('#productReject')?.addEventListener('click', async () => {
    const comment = $('#productApprovalComment').value.trim(); if(!comment){toast('驳回时请填写意见','error');return;}
    const return_to = $('#productReturnTo')?.value || '需求申请';
    try { const r = await api(`/api/demands/${demand.id}/approve`, { method:'POST', body:JSON.stringify({action:'驳回', comment, return_to}) }); toast(r.message,'success'); navigate(`demand-detail/${demand.id}`,{tab:'approval'}); } catch(error){ toast(error.message,'error'); }
  });
}

function bindAllocationEditor(demand) {
  const recalc = () => {
    const base = Number(demand.estimated_amount || demand.budget_amount || 0);
    let sum = 0;
    $$('.alloc-row').forEach((row) => {
      const ratio = Number($('.alloc-ratio', row).value || 0);
      sum += ratio;
      $('.alloc-amount', row).textContent = `¥ ${money(base * ratio / 100)}`;
    });
    $('#ratioSum').textContent = `${sum.toFixed(2)}%`;
    $('#ratioSum').style.color = sum > 100 ? '#e85a64' : '#233251';
  };
  $$('.alloc-ratio').forEach((input) => input.addEventListener('input', recalc));
  $$('.alloc-remove').forEach((button) => button.addEventListener('click', () => { button.closest('tr').remove(); recalc(); }));
  $('#addAllocation')?.addEventListener('click', () => {
    const body = $('#allocBody');
    const systems = [...new Set(demand.function_points.map((fp) => fp.system_name))];
    const wrapper = document.createElement('tbody');
    wrapper.innerHTML = allocationRow({ system_name:systems[0]||'', expense_subject:'集团', expense_source:demand.budget_sources[0]||'', ratio:0, department:'财务部' }, body.children.length, demand, systems);
    body.appendChild(wrapper.firstElementChild);
    bindAllocationEditor(demand);
    recalc();
  }, { once: true });
  $('#saveAllocation')?.addEventListener('click', async () => {
    const rows = $$('.alloc-row').map((row) => ({
      system_name: $('.alloc-system', row).value,
      expense_subject: $('.alloc-subject', row).value,
      expense_source: $('.alloc-source', row).value,
      ratio: Number($('.alloc-ratio', row).value || 0),
      department: $('.alloc-dept', row).value
    }));
    try { await api(`/api/demands/${demand.id}/allocations`, { method:'PUT', body:JSON.stringify({rows}) }); toast('费用分摊已保存','success'); renderRoute(); } catch(error){ toast(error.message,'error'); }
  });
  recalc();
}

async function importFunctionPoints(event) {
  const file = event.target.files[0]; if (!file || !state.currentDemandId) return;
  const form = new FormData(); form.append('file', file);
  try {
    const result = await api(`/api/demands/${state.currentDemandId}/function-points/import`, { method:'POST', body:form });
    toast(`导入 ${result.data.inserted} 行，错误 ${result.data.errorCount} 行`, result.data.errorCount ? 'warn' : 'success');
    renderRoute();
  } catch(error){ toast(error.message,'error'); }
}

function openFunctionPointDetail(demand, fpId) {
  const fp = demand.function_points.find((x) => x.id === fpId); if (!fp) return;
  showModal(`<h3 id="modalTitle">功能点评估详情</h3><div class="info-grid"><div>功能点编号</div><div>${esc(fp.fp_no)}</div><div>来源</div><div>${esc(fp.source_type||'新增')}</div><div>归属系统</div><div>${esc(fp.system_name)}</div><div>需求名称</div><div>${esc(fp.name)}</div><div>需求概述</div><div>${esc(fp.demand_summary)}</div><div>评估人</div><div>${esc(fp.evaluator)}</div><div>所属部门</div><div>${esc(fp.department)}</div><div>所属团队</div><div>${esc(fp.team)}</div><div>评估日期</div><div>${esc(fp.evaluation_date)}</div><div>预估功能点</div><div>${Number(fp.fp_count).toFixed(2)}</div><div>单价</div><div>¥ ${money(fp.unit_price)}</div><div>预估金额</div><div>¥ ${money(fp.estimated_amount)} = ${Number(fp.fp_count).toFixed(2)} × ¥${money(fp.unit_price)}</div></div><div class="modal-actions">${btn('关闭','btn','closeFpDetail')}</div>`);
  $('#closeFpDetail').addEventListener('click', closeModal);
}

function openFunctionPointEdit(demand, fpId) {
  const fp = demand.function_points.find((x) => x.id === fpId); if (!fp) return;
  showModal(`<h3 id="modalTitle">编辑功能点评估</h3>${functionPointForm(fp)}<div class="modal-actions">${btn('取消','btn','cancelFpEdit')}${btn('保存','btn primary','saveFpEdit')}</div>`);
  $('#cancelFpEdit').addEventListener('click', closeModal);
  $('#saveFpEdit').addEventListener('click', async () => {
    try { await api(`/api/function-points/${fpId}`, { method:'PUT', body:JSON.stringify(collectFunctionPointForm()) }); toast('功能点已更新','success'); closeModal(); renderRoute(); } catch(error){ toast(error.message,'error'); }
  });
}

async function renderFunctionPointCatalog(query) {
  const demandId = Number(query.get('demandId') || 0);
  if (demandId) state.currentDemandId = demandId;
  const q = query.get('q') || '';
  const system = query.get('system') || '';
  const catalog = (await api(`/api/function-point-catalog?q=${encodeURIComponent(q)}&system_name=${encodeURIComponent(system)}`)).data;
  const demand = demandId ? (await api(`/api/demands/${demandId}`)).data : null;
  setPage({ title: '功能点查询', iconName: 'fp', crumbs: ['需求申请'], actions: `${demand ? btn(`${icon('back')} 返回产品评估`,'btn','fpBackEval') : ''}${btn('模板下载','btn','catalogTemplate')}${btn(`${icon('plus')} 功能点填报`,'btn primary','catalogNew')}` });
  appView.innerHTML = `<div class="section"><div class="toolbar"><div class="filters"><input class="field" id="catalogQ" value="${esc(q)}" placeholder="功能点编号 / 名称 / 概述"><select class="select" id="catalogSystem"><option value="">全部系统</option>${catalog.systems.map((x)=>`<option ${x===system?'selected':''}>${esc(x)}</option>`).join('')}</select>${btn('查询','btn small','catalogSearch')}</div>${demand ? `<div class="callout" style="padding:7px 10px">当前关联需求：${esc(demand.demand_no)} · ${esc(demand.title)}</div>` : ''}</div>
    <div class="fp-catalog-grid">${catalog.items.map((fp) => `<div class="fp-card"><div class="fp-card-head"><div><strong>${esc(fp.catalog_no)} · ${esc(fp.name)}</strong><div class="system">${esc(fp.system_name)}</div></div><span class="status gray">${Number(fp.default_fp_count).toFixed(0)} FP</span></div><div class="fp-summary">${esc(fp.demand_summary)}</div><div class="toolbar" style="margin:10px 0 0"><span class="subtle">单价 ¥${money(fp.unit_price)}</span>${demand ? btn('关联当前需求','btn small primary','',`data-link-catalog="${fp.id}"`) : ''}</div></div>`).join('')}</div>
  </div>`;
  $('#catalogSearch').addEventListener('click', () => navigate('function-points', { demandId, q:$('#catalogQ').value.trim(), system:$('#catalogSystem').value }));
  $('#catalogQ').addEventListener('keydown', (e) => { if(e.key==='Enter') $('#catalogSearch').click(); });
  $('#catalogNew').addEventListener('click', () => navigate('function-point-new', { demandId }));
  $('#catalogTemplate').addEventListener('click', () => { location.href='/api/function-points/template'; });
  if ($('#fpBackEval')) $('#fpBackEval').addEventListener('click', () => navigate('product-eval',{id:demandId}));
  $$('[data-link-catalog]').forEach((button) => button.addEventListener('click', async () => {
    try { await api(`/api/demands/${demandId}/function-points/link`, { method:'POST', body:JSON.stringify({catalog_id:Number(button.dataset.linkCatalog)}) }); toast('已有功能点已关联','success'); navigate('product-eval',{id:demandId}); } catch(error){ toast(error.message,'error'); }
  }));
}

function functionPointForm(fp = {}) {
  const user = currentUser();
  return `<div class="form-row"><div class="label">功能点编号</div><input class="field readonly" readonly value="${esc(fp.fp_no || '保存后系统自动生成 FP-YYYY-XXXX')}"></div>
  <div class="form-row"><div class="label required">归属系统</div><input class="field" id="fpSystem" value="${esc(fp.system_name || '')}" placeholder="请输入归属系统"></div>
  <div class="form-row"><div class="label">需求概述</div><textarea class="textarea" id="fpSummary" placeholder="概述该功能点解决的需求">${esc(fp.demand_summary || '')}</textarea></div>
  <div class="form-row"><div class="label">需求名称</div><input class="field" id="fpName" value="${esc(fp.name || '')}" placeholder="请输入需求名称"></div>
  <div class="form-row two"><div class="label required">评估人</div><input class="field readonly" id="fpEvaluator" readonly value="${esc(fp.evaluator || user.name)}"><div class="label required">评估日期</div><input class="field" id="fpDate" type="date" value="${esc(fp.evaluation_date || new Date().toISOString().slice(0,10))}"></div>
  <div class="form-row two"><div class="label required">所属部门</div><input class="field" id="fpDept" value="${esc(fp.department || user.dept || '产品研发部')}"><div class="label required">所属团队</div><input class="field" id="fpTeam" value="${esc(fp.team || '研发团队')}"></div>
  <div class="form-row two"><div class="label">预估功能点</div><input class="field" id="fpCount" type="number" min="0" step="0.01" value="${Number(fp.fp_count ?? 10)}"><div class="label">功能点单价</div><input class="field" id="fpPrice" type="number" min="0" step="0.01" value="${Number(fp.unit_price ?? 1200)}"></div>`;
}

function collectFunctionPointForm() {
  return {
    system_name: $('#fpSystem').value.trim(),
    demand_summary: $('#fpSummary').value.trim(),
    name: $('#fpName').value.trim(),
    evaluator: $('#fpEvaluator').value.trim(),
    department: $('#fpDept').value.trim(),
    team: $('#fpTeam').value.trim(),
    evaluation_date: $('#fpDate').value,
    fp_count: Number($('#fpCount').value || 0),
    unit_price: Number($('#fpPrice').value || 0)
  };
}

async function renderFunctionPointNew(query) {
  let demandId = Number(query.get('demandId') || 0);
  const demands = await eligibleDemands();
  if (!demandId && state.currentDemandId) demandId = state.currentDemandId;
  const demand = demandId ? demands.find((d)=>d.id===demandId) : null;
  setPage({ title: '功能点填报', iconName: 'fp', crumbs: ['需求申请'], actions: btn(`${icon('back')} 返回功能点查询`,'btn','newFpBack') });
  appView.innerHTML = `<div class="section"><div class="section-title">新增功能点评估</div><div class="form-row"><div class="label required">关联需求</div><select class="select" id="fpDemandSelect"><option value="">请选择需求</option>${demands.map((d)=>`<option value="${d.id}" ${d.id===demandId?'selected':''}>${esc(d.demand_no)} · ${esc(d.title)}</option>`).join('')}</select></div>${functionPointForm()}<div class="modal-actions">${btn('取消','btn','cancelNewFp')}${btn('保存并返回评估','btn primary','saveNewFp')}</div></div>`;
  $('#newFpBack').addEventListener('click', () => navigate('function-points',{demandId}));
  $('#cancelNewFp').addEventListener('click', () => navigate('function-points',{demandId}));
  $('#saveNewFp').addEventListener('click', async () => {
    const selectedId = Number($('#fpDemandSelect').value || 0);
    if (!selectedId) { toast('请选择关联需求','error'); return; }
    const payload = collectFunctionPointForm();
    if (!payload.system_name) { toast('归属系统不能为空','error'); return; }
    try { await api(`/api/demands/${selectedId}/function-points`, { method:'POST', body:JSON.stringify(payload) }); state.currentDemandId=selectedId; toast('功能点已保存','success'); navigate('product-eval',{id:selectedId}); } catch(error){ toast(error.message,'error'); }
  });
}

async function renderBudget() {
  setPage({ title: '预算管理', iconName: 'budget', crumbs: ['预算管理'], actions: btn(`${icon('plus')} 新增预算`,'btn primary','newBudget') });
  const items=(await api('/api/budget-ledger')).data;
  appView.innerHTML=`<div class="grid-4"><div class="metric"><div class="k">预算总额</div><div class="v" style="font-size:20px">¥${money(items.reduce((a,b)=>a+Number(b.total_budget),0))}</div></div><div class="metric"><div class="k">已使用</div><div class="v" style="font-size:20px">¥${money(items.reduce((a,b)=>a+Number(b.used_budget),0))}</div></div><div class="metric"><div class="k">剩余可用</div><div class="v" style="font-size:20px">¥${money(items.reduce((a,b)=>a+Number(b.remaining),0))}</div></div><div class="metric"><div class="k">预算项</div><div class="v">${items.length}</div></div></div>
  <div class="section" style="margin-top:12px"><div class="toolbar"><div class="section-title">预算台账</div><input id="budgetSearch" class="field" style="max-width:300px" placeholder="搜索预算编号/名称"></div><div class="table-wrap"><table class="table"><thead><tr><th>预算编号</th><th>预算名称</th><th>年度</th><th>总预算</th><th>已使用</th><th>剩余</th><th>执行率</th><th>状态</th><th>操作</th></tr></thead><tbody id="budgetRows"></tbody></table></div></div>
  <div class="section" style="margin-top:12px" id="budgetDetail"><div class="empty">点击“详情”查看预算组成、关联项目/需求/合同及流水。</div></div>`;
  const draw=()=>{const q=$('#budgetSearch').value.trim().toLowerCase();$('#budgetRows').innerHTML=items.filter(b=>!q||`${b.budget_no} ${b.budget_name}`.toLowerCase().includes(q)).map(b=>`<tr><td>${b.budget_no}</td><td>${esc(b.budget_name)}</td><td>${b.year}</td><td>¥${money(b.total_budget)}</td><td>¥${money(b.used_budget)}</td><td>¥${money(b.remaining)}</td><td>${progressCell(Math.min(100,b.execution_rate))}</td><td>${statusPill(b.execution_rate>=95?'预警':'正常')}</td><td><button class="link budget-view" data-id="${b.id}">详情</button> <button class="link budget-edit" data-id="${b.id}">维护</button> <button class="link budget-txn" data-id="${b.id}">登记流水</button></td></tr>`).join('');bindRows();};
  const form=(b={})=>{showModal(`<h3 id="modalTitle">${b.id?'维护':'新增'}预算</h3><div class="form-row"><div class="label required">预算名称</div><input id="bName" class="field" value="${esc(b.budget_name||'')}"></div><div class="grid-2"><div><label>总预算</label><input id="bTotal" class="field" type="number" min="0" value="${b.total_budget||0}"></div><div><label>已使用</label><input id="bUsed" class="field" type="number" min="0" value="${b.used_budget||0}"></div></div><div class="grid-2" style="margin-top:10px"><div><label>内部研发预算</label><input id="bInternal" class="field" type="number" min="0" value="${b.internal_total||0}"></div><div><label>委托数科预算</label><input id="bDigital" class="field" type="number" min="0" value="${b.digital_total||0}"></div></div><div class="modal-actions">${btn('取消','btn','bCancel')}${btn('保存','btn primary','bSave')}</div>`);$('#bCancel').addEventListener('click',closeModal);$('#bSave').addEventListener('click',async()=>{try{const p={budget_no:b.budget_no||null,budget_name:$('#bName').value,total_budget:Number($('#bTotal').value),used_budget:Number($('#bUsed').value),internal_total:Number($('#bInternal').value),internal_used:b.internal_used||0,digital_total:Number($('#bDigital').value),digital_used:b.digital_used||0,year:2026};await api(b.id?`/api/budgets/${b.id}`:'/api/budgets',{method:b.id?'PUT':'POST',body:JSON.stringify(p)});closeModal();toast('预算已保存','success');renderBudget();}catch(e){toast(e.message,'error')}});};
  const showDetail=async(id)=>{const b=(await api(`/api/budgets/${id}/detail`)).data;$('#budgetDetail').innerHTML=`<div class="toolbar"><div><div class="section-title">${esc(b.budget_no)} · ${esc(b.budget_name)}</div><div class="section-subtitle">总预算 ¥${money(b.total_budget)} · 已使用 ¥${money(b.used_budget)} · 剩余 ¥${money(b.remaining)} · 执行率 ${b.execution_rate}%</div></div></div><div class="grid-3"><div class="metric"><div class="k">内部研发</div><div class="v" style="font-size:17px">¥${money(b.internal_used)} / ¥${money(b.internal_total)}</div></div><div class="metric"><div class="k">委托数科</div><div class="v" style="font-size:17px">¥${money(b.digital_used)} / ¥${money(b.digital_total)}</div></div><div class="metric"><div class="k">关联对象</div><div class="v">${b.projects.length+b.demands.length+b.contracts.length}</div><div class="sub">项目 ${b.projects.length} / 需求 ${b.demands.length} / 合同 ${b.contracts.length}</div></div></div><div class="grid-2" style="margin-top:12px"><div><div class="section-title">预算流水</div>${simpleTable(['时间','类型','金额','关联','说明'],b.transactions.map(t=>[t.created_at,t.txn_type,`¥${money(t.amount)}`,`${t.reference_type||'—'} ${t.reference_id||''}`,esc(t.description||'')]))}</div><div><div class="section-title">关联项目</div>${simpleTable(['项目编号','项目名称','状态','进度'],b.projects.map(x=>[x.project_no,esc(x.name),statusPill(x.status),progressCell(x.progress)]))}</div></div>`;};
  function bindRows(){$$('.budget-view').forEach(x=>x.addEventListener('click',()=>showDetail(x.dataset.id)));$$('.budget-edit').forEach(x=>x.addEventListener('click',()=>form(items.find(b=>b.id===Number(x.dataset.id)))));$$('.budget-txn').forEach(x=>x.addEventListener('click',()=>{const b=items.find(z=>z.id===Number(x.dataset.id));showModal(`<h3 id="modalTitle">登记预算流水</h3><div class="callout">${esc(b.budget_no)} · ${esc(b.budget_name)}</div><div class="grid-2" style="margin-top:12px"><div><label>流水类型</label><select id="txnType" class="select">${['支出','占用','释放','冲销','追加预算','调减预算'].map(v=>`<option>${v}</option>`).join('')}</select></div><div><label>金额</label><input id="txnAmount" type="number" min="0" class="field" value="0"></div></div><div class="grid-2" style="margin-top:10px"><div><label>费用归属部门</label><select id="txnDept" class="select">${['数字化管理部','产品研发部','科技管理部','财务部','办公室'].map(v=>`<option>${v}</option>`).join('')}</select></div><div><label>说明</label><input id="txnDesc" class="field"></div></div><div class="modal-actions">${btn('取消','btn','txnCancel')}${btn('登记','btn primary','txnSave')}</div>`);$('#txnCancel').addEventListener('click',closeModal);$('#txnSave').addEventListener('click',async()=>{try{await api(`/api/budgets/${b.id}/transactions`,{method:'POST',body:JSON.stringify({txn_type:$('#txnType').value,amount:Number($('#txnAmount').value),department:$('#txnDept').value,description:$('#txnDesc').value})});closeModal();toast('预算流水已登记并更新部门预算趋势','success');renderBudget();}catch(e){toast(e.message,'error')}});}));}
  $('#budgetSearch').addEventListener('input',draw);$('#newBudget').addEventListener('click',()=>form());draw();
}

async function renderTapd() {
  setPage({ title: 'TAPD同步中心', iconName: 'sync', crumbs: ['集成中心'], actions: `${state.role==='admin'?btn('执行后台任务','btn','tapdRunJobs'):''}${btn('刷新','btn','tapdRefresh')}` });
  const [demandResp, settingResp, overviewResp] = await Promise.all([api('/api/demands?page_size=100'), api('/api/poc/settings'), api('/api/tapd/overview')]);
  const demands = demandResp.data.items.filter((d)=>d.demand_no);
  const settings = settingResp.data, overview = overviewResp.data, cfg = overview.config;
  const strategyLabel = settings.tapd_split_strategy==='system_allocation' ? '按系统 + 分摊行' : '按系统';
  const intervalMin = Math.round(Number(settings.tapd_sync_interval_seconds||1800)/60);
  const rows = demands.map((d)=>{
    const reqs = d.tapd_requirements || [], retry = d.tapd_retry_job, systems = reqs.map((r)=>r.system_name).filter(Boolean);
    const tapdCell = reqs.length ? reqs.map((r)=>`<div class="tapd-id"><strong>${esc(r.tapd_id)}</strong><small>${esc(r.system_name||'默认系统')} · ${esc(r.tapd_status||'新')}</small></div>`).join('') : '—';
    const syncState = retry && retry.status==='等待重试' ? `等待第${Number(retry.attempt_count||1)+1}次重试` : (d.tapd_sync_status||'未同步');
    return `<tr><td>${esc(d.demand_no)}</td><td><div class="row-title">${esc(d.title)}</div><small>${systems.length?`已拆分：${esc([...new Set(systems)].join('、'))}`:'尚未创建TAPD'}</small></td><td>${statusPill(d.status)}</td><td>${tapdCell}</td><td>${statusPill(syncState)}</td><td>${esc(d.tapd_last_sync_at||retry?.next_retry_at||'—')}</td><td><div class="action-group">${reqs.length ? `<button class="link tapd-sync" data-id="${d.id}">${cfg.mode==='live'?'从TAPD回读':'手动回读'}</button>` : `<button class="link tapd-push" data-id="${d.id}" ${['审批通过','TAPD同步失败'].includes(d.status) ? '' : 'disabled'}>${cfg.mode==='live'?'创建到TAPD':'创建TAPD'}</button>`}${cfg.mode==='mock' && ['project_manager','admin'].includes(state.role) && !reqs.length && ['审批通过','TAPD同步失败'].includes(d.status) ? `<button class="link tapd-fail" data-id="${d.id}">模拟失败</button>` : ''}<button class="link tapd-detail" data-id="${d.id}">完整详情</button></div></td></tr>`;
  }).join('');
  const taskRows = overview.recent_tasks.map(t=>[esc(t.demand_no),esc(t.external_task_id),esc(t.title),esc(t.creator||'—'),`${Number(t.estimated_hours||0).toFixed(1)}h`,`${Number(t.completed_hours||0).toFixed(1)}h`,`${Number(t.remaining_hours||0).toFixed(1)}h`,esc(t.planned_end||'—')]);
  const runRows = overview.recent_runs.map(r=>[esc(r.demand_no),esc(r.source),r.success?statusPill('成功'):statusPill('失败'),`${r.changed_count} 条`,esc(r.message),esc(r.created_at)]);
  appView.innerHTML = `${workflow(4)}
  <div class="grid-4">
    <div class="metric"><div class="k">运行模式</div><div class="v" style="font-size:18px">${cfg.mode==='live'?'Live真实对接':'Mock演示'}</div><div class="sub">${cfg.mode==='live'?`Workspace ${esc(cfg.workspace_id||'未配置')}`:'无需外部账号即可演示全流程'}</div></div>
    <div class="metric"><div class="k">已创建TAPD需求</div><div class="v">${overview.requirement_count}</div><div class="sub">拆分策略：${strategyLabel}</div></div>
    <div class="metric"><div class="k">同步执行</div><div class="v">${overview.success_runs}</div><div class="sub">失败 ${overview.failed_runs} · 待重试 ${overview.waiting_retry}</div></div>
    <div class="metric"><div class="k">定时回读</div><div class="v" style="font-size:18px">每${intervalMin}分钟</div><div class="sub">任务接口单页最多200条，自动分页</div></div>
  </div>
  <div class="grid-2" style="margin-top:12px">
    <div class="section"><div class="toolbar"><div><div class="section-title">TAPD连接与同步配置</div><div class="section-subtitle">Live模式对接TAPD开放平台；API凭据仅从服务器环境变量读取，不在页面保存明文。</div></div>${state.role==='admin'?btn('测试连接','btn','tapdTest'):''}</div>
      <div class="info-grid" style="grid-template-columns:130px 1fr"><div>API地址</div><div>${esc(cfg.base_url)}</div><div>Workspace ID</div><div>${esc(cfg.workspace_id||'未配置')}</div><div>API账号</div><div>${settings.tapd_credentials_ready?`已配置（${esc(settings.tapd_api_user_masked||'')}）`:'未配置'}</div><div>Webhook</div><div>POST /api/tapd/webhook</div></div>
      ${state.role==='admin'?`<div class="config-grid"><div><label>运行模式</label><select id="tapdMode" class="select"><option value="mock" ${cfg.mode==='mock'?'selected':''}>Mock演示</option><option value="live" ${cfg.mode==='live'?'selected':''}>Live真实TAPD</option></select></div><div><label>Workspace ID</label><input id="tapdWorkspace" class="field" value="${esc(cfg.workspace_id||'')}"></div><div><label>API Base URL</label><input id="tapdBaseUrl" class="field" value="${esc(cfg.base_url)}"></div><div><label>拆分策略</label><select id="tapdStrategy" class="select"><option value="system" ${settings.tapd_split_strategy==='system'?'selected':''}>按系统生成</option><option value="system_allocation" ${settings.tapd_split_strategy==='system_allocation'?'selected':''}>按系统 + 分摊行</option></select></div><div><label>定时回读（秒）</label><input id="tapdInterval" type="number" min="60" class="field" value="${Number(settings.tapd_sync_interval_seconds||1800)}"></div><div><label>失败重试（秒）</label><input id="tapdRetry" type="number" min="1" class="field" value="${Number(settings.tapd_retry_seconds||30)}"></div></div><div style="margin-top:10px">${btn('保存TAPD配置','btn primary','saveTapdConfig')}</div>`:''}
    </div>
    <div class="section"><div class="section-title">开放平台接口映射</div><div class="api-list"><div><b>创建/查询需求</b><code>POST /stories · GET /stories</code><small>终审通过创建；回读需求状态、描述、开发人、计划/完成时间。</small></div><div><b>关联任务</b><code>GET /tasks?story_id=...</code><small>同步任务标题、状态、负责人、计划日期、预估/完成/剩余/超出工时。</small></div><div><b>花费工时</b><code>GET /timesheets?entity_type=task</code><small>同步花费日期、工时、创建人和描述，用于工时偏差预警。</small></div></div></div>
  </div>
  <div class="section" style="margin-top:12px"><div class="toolbar"><div><div class="section-title">TAPD需求创建与信息回读</div><div class="section-subtitle">支持终审自动创建、手动回读、30分钟定时回读、Webhook及3次异常重试。</div></div><input id="tapdSearch" class="field" style="max-width:280px" placeholder="搜索REQ / 标题 / TAPD ID"></div><div class="table-wrap"><table class="table"><thead><tr><th>需求编号</th><th>需求标题 / 拆分系统</th><th>系统状态</th><th>TAPD需求</th><th>同步 / 重试状态</th><th>最近同步 / 下次重试</th><th>操作</th></tr></thead><tbody id="tapdRows">${rows||'<tr><td colspan="7"><div class="empty">暂无可同步需求</div></td></tr>'}</tbody></table></div></div>
  <div class="grid-2" style="margin-top:12px"><div class="section"><div class="section-title">最近回读任务</div>${simpleTable(['需求','任务ID','任务标题','创建/负责人','预估','完成','剩余','预计结束'],taskRows)}</div><div class="section"><div class="section-title">最近同步记录</div>${simpleTable(['需求','来源','结果','变更量','说明','时间'],runRows)}</div></div>`;
  const bindRows=()=>{
    $$('.tapd-detail').forEach((b)=>b.addEventListener('click',()=>navigate(`demand-detail/${b.dataset.id}`,{tab:'tapd'})));
    $$('.tapd-sync').forEach((b)=>b.addEventListener('click',async()=>{const d=(await api(`/api/demands/${b.dataset.id}`)).data;openTapdSyncModal(d,cfg.mode);}));
    $$('.tapd-push').forEach((b)=>b.addEventListener('click',async()=>{try{const r=await api(`/api/demands/${b.dataset.id}/tapd/push`,{method:'POST'});toast(r.message,'success');renderTapd();}catch(error){toast(error.message,'error');}}));
    $$('.tapd-fail').forEach((b)=>b.addEventListener('click',async()=>{try{const r=await api(`/api/demands/${b.dataset.id}/tapd/push?simulate_failure=true`,{method:'POST'});toast(r.message||'已进入重试队列','warning');await loadNotifications(false);renderTapd();}catch(error){toast(error.message,'error');await loadNotifications(false);renderTapd();}}));
  };
  bindRows();
  $('#tapdSearch').addEventListener('input',()=>{const q=$('#tapdSearch').value.trim().toLowerCase();$$('#tapdRows tr').forEach(tr=>{tr.style.display=!q||tr.textContent.toLowerCase().includes(q)?'':'none';});});
  $('#tapdRefresh').addEventListener('click', renderTapd);
  if ($('#tapdRunJobs')) $('#tapdRunJobs').addEventListener('click', async()=>{try{const r=await api('/api/poc/jobs/scan?force=true',{method:'POST'});toast(`后台任务完成：OA提醒${r.data.oaReminders}，TAPD重试${r.data.tapdRetries}，定时回读${r.data.tapdScheduledSync}`,'success');await loadNotifications(false);renderTapd();}catch(e){toast(e.message,'error')}});
  if ($('#tapdTest')) $('#tapdTest').addEventListener('click',async()=>{try{const r=await api('/api/tapd/test-connection',{method:'POST'});const d=r.data;toast(`${d.message}${d.story_count!=null?` · 需求${d.story_count} · 任务${d.task_count}`:''}`,'success');}catch(e){toast(e.message,'error')}});
  if ($('#saveTapdConfig')) $('#saveTapdConfig').addEventListener('click', async()=>{try{await api('/api/poc/settings',{method:'PUT',body:JSON.stringify({tapd_mode:$('#tapdMode').value,tapd_workspace_id:$('#tapdWorkspace').value,tapd_base_url:$('#tapdBaseUrl').value,tapd_split_strategy:$('#tapdStrategy').value,tapd_sync_interval_seconds:Number($('#tapdInterval').value),tapd_retry_seconds:Number($('#tapdRetry').value)})});toast('TAPD配置已保存','success');renderTapd();}catch(e){toast(e.message,'error')}});
}

function openTapdSyncModal(demand, mode='mock') {
  const live = mode==='live';
  showModal(`<h3 id="modalTitle">${live?'从TAPD实时回读':'同步TAPD状态'}</h3><div class="callout">${esc(demand.demand_no)} · ${esc(demand.title)}<div class="help">当前TAPD状态：${esc(demand.tapd_status || '新')}</div></div>${live?`<div class="callout success" style="margin-top:14px">将从TAPD开放平台读取 Story、关联 Tasks 以及每个 Task 的 Timesheets，并同步需求状态、任务工时、花费记录和偏差预警。</div>`:`<div class="form-row" style="margin-top:14px"><div class="label">演示回读状态</div><select class="select" id="tapdSyncStatus">${['新','开发中','测试中','已验收','已关闭','已拒绝'].map((s)=>`<option ${s===demand.tapd_status?'selected':''}>${s}</option>`).join('')}</select></div>`}<div class="modal-actions">${btn('取消','btn','cancelTapdSync')}${btn(live?'立即从TAPD回读':'立即同步','btn primary','confirmTapdSync')}</div>`);
  $('#cancelTapdSync').addEventListener('click',closeModal);
  $('#confirmTapdSync').addEventListener('click',async()=>{try{const status=live?'':$('#tapdSyncStatus').value;const url=`/api/demands/${demand.id}/tapd/sync${status?`?tapd_status=${encodeURIComponent(status)}`:''}`;const r=await api(url,{method:'POST'});toast(r.message,'success');closeModal();await loadNotifications(false);renderRoute();}catch(error){toast(error.message,'error');}});
}

async function renderAI() {
  setPage({ title: 'AI智能问答', iconName: 'ai', crumbs: ['智能层'] });
  const recommendations = [
    'REQ-20260817-0001 的完整全生命周期信息是什么？',
    '机构透视管理机器人项目所有需求的状态分布和工时汇总',
    '数字化管理部月度预算执行趋势怎么样？',
    'REQ-20260817-0001 当前卡在哪个环节，预计何时完成？',
    '历史同类需求怎么处理，平均交付周期是多少？'
  ];
  appView.innerHTML = `${workflow(5)}<div class="ai-layout"><div class="chat"><div class="ai-page-agent-bar"><div><strong>TRM企业智能体</strong><small>通过本系统后端安全代理连接 Gazellio G.AIOS</small></div><span class="status ${state.aiProvider.includes('本地')?'warn':'success'}">${esc(state.aiProvider)}</span></div><div class="chat-history" id="chatHistory">${state.chat.map((msg)=>`<div class="bubble ${msg.type}">${esc(msg.text)}${msg.type==='ai'&&msg.provider?`<span class="ai-message-meta">${esc(msg.provider)}${msg.fallback?' · 本地降级':''}</span>`:''}</div>`).join('')}${state.aiBusy?'<div class="bubble ai"><span class="ai-float-typing"><i></i><i></i><i></i></span></div>':''}</div><div class="chat-input"><textarea id="aiInput" maxlength="1000" placeholder="可查询申请、审批、预算、TAPD进度、项目统计和历史处理数据"></textarea><button class="btn primary" id="aiSend" type="button" ${state.aiBusy?'disabled':''}>发送</button></div></div><aside class="section"><div class="section-title">五类推荐问题</div>${recommendations.map((q)=>`<button class="suggestion" type="button">${esc(q)}</button>`).join('')}<div class="callout">当前身份：${esc(state.meta.roles[state.role])}<div class="help">回答基于当前账号可访问的系统事实数据：①单条需求完整信息 ②项目批量统计 ③月/季度预算趋势 ④当前环节与预计完成 ⑤历史追溯与平均周期。</div></div></aside></div>`;
  const send = async () => {
    const input = $('#aiInput'); const question = input.value.trim(); if(!question||state.aiBusy)return;
    state.chat.push({type:'user',text:question}); input.value=''; state.aiBusy=true; renderAI(); renderAiAssistantWidget();
    try{const result=await askAiAgent(question,{source:'ai-page'});state.aiProvider=result.provider;state.chat.push({type:'ai',text:result.answer,provider:result.provider,fallback:result.fallback});if(result.fallback)toast(`外部智能体暂不可用，已切换本地知识引擎：${result.warning}`,'warn');}
    catch(error){state.chat.push({type:'ai',text:`查询失败：${error.message}`,provider:'系统提示'});}
    finally{state.aiBusy=false;renderAI();renderAiAssistantWidget();}
  };
  $('#aiSend').addEventListener('click',send);
  $('#aiInput').addEventListener('keydown',(e)=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send();}});
  $$('.suggestion').forEach((button)=>button.addEventListener('click',()=>{$('#aiInput').value=button.textContent;$('#aiSend').click();}));
  const history=$('#chatHistory');history.scrollTop=history.scrollHeight;
}

async function renderInitiativeForm(){
  const id=Number(parseRoute().query.get('id')||0);
  let item=id?(await api(`/api/initiatives/${id}`)).data:null;
  const budgets=(await api('/api/budget-ledger')).data;
  const projectTypes=state.v4meta?.project_types||['系统建设','智能化改造','基础设施','咨询服务','研发创新'];
  setPage({title:'新建立项',iconName:'send',crumbs:['立项管理','立项列表'],actions:`${btn('保存草稿','btn','iniSave')}${item?btn('删除草稿','btn danger','iniDelete'):''}${btn('提交审批','btn primary','iniSubmit',item&& !['草稿','已驳回'].includes(item.status)?'disabled':'')}`});
  appView.innerHTML=`<div class="section"><div class="section-title">立项基本信息</div>
    <div class="grid-3"><div><label>立项编号</label><input class="field" value="${esc(item?.initiative_no||'提交后自动生成')}" disabled></div><div><label>项目类型</label><select id="iniType" class="select">${projectTypes.map(x=>`<option ${x===item?.project_type?'selected':''}>${x}</option>`).join('')}</select></div><div><label>紧急程度</label><select id="iniUrgency" class="select">${['高','中','低'].map(x=>`<option ${x===(item?.urgency||'中')?'selected':''}>${x}</option>`).join('')}</select></div></div>
    <div class="form-row"><div class="label required">立项名称</div><input id="iniTitle" class="field" maxlength="100" value="${esc(item?.title||'')}"></div>
    <div class="grid-3"><div><label>申请人</label><input id="iniApplicant" class="field" value="${esc(item?.applicant||state.userDisplay)}"></div><div><label>所属部门</label><input id="iniDept" class="field" value="${esc(item?.department||'数字化管理部')}"></div><div><label>项目负责人</label><input id="iniOwner" class="field" value="${esc(item?.owner||'王卫嘉')}"></div></div>
    <div class="grid-3" style="margin-top:10px"><div><label>项目发起人/Sponsor</label><input id="iniSponsor" class="field" value="${esc(item?.sponsor||'')}"></div><div><label>预估预算</label><input id="iniAmount" type="number" min="0" class="field" value="${item?.estimated_budget||0}"></div><div><label>预算来源</label><select id="iniBudget" class="select"><option value="">请选择</option>${budgets.map(b=>`<option value="${b.id}" ${b.id===item?.budget_id?'selected':''}>${esc(b.budget_no)} · ${esc(b.budget_name)}</option>`).join('')}</select></div></div>
    <div class="grid-2" style="margin-top:10px"><div><label>计划开始</label><input id="iniStart" type="date" class="field" value="${item?.planned_start||''}"></div><div><label>计划结束</label><input id="iniEnd" type="date" class="field" value="${item?.planned_end||''}"></div></div>
  </div>
  <div class="section" style="margin-top:12px"><div class="section-title">立项论证</div>
    <div><label>建设背景</label><textarea id="iniBackground" class="textarea" maxlength="3000">${esc(item?.background||'')}</textarea></div>
    <div style="margin-top:10px"><label>建设目标</label><textarea id="iniObjectives" class="textarea" maxlength="3000">${esc(item?.objectives||'')}</textarea></div>
    <div style="margin-top:10px"><label>建设范围</label><textarea id="iniScope" class="textarea" maxlength="3000">${esc(item?.scope||'')}</textarea></div>
    <div style="margin-top:10px"><label>预期收益</label><textarea id="iniBenefit" class="textarea" maxlength="3000">${esc(item?.expected_benefit||'')}</textarea></div>
    <div style="margin-top:10px"><label>补充说明</label><textarea id="iniDesc" class="textarea">${esc(item?.description||'')}</textarea></div>
    ${item?`<div class="callout" style="margin-top:14px">当前状态：${statusPill(item.status)} · 当前节点：${esc(item.current_node)}</div>`:''}
  </div>`;
  const corePayload=()=>({title:$('#iniTitle').value.trim(),description:$('#iniDesc').value,applicant:$('#iniApplicant').value,department:$('#iniDept').value,owner:$('#iniOwner').value,estimated_budget:Number($('#iniAmount').value),budget_id:Number($('#iniBudget').value)||null,planned_start:$('#iniStart').value||null,planned_end:$('#iniEnd').value||null});
  const profilePayload=()=>({project_type:$('#iniType').value,background:$('#iniBackground').value,objectives:$('#iniObjectives').value,scope:$('#iniScope').value,expected_benefit:$('#iniBenefit').value,sponsor:$('#iniSponsor').value,urgency:$('#iniUrgency').value});
  const save=async()=>{if(!$('#iniTitle').value.trim()){toast('请填写立项名称','error');return null;}const r=await api(item?`/api/initiatives/${item.id}`:'/api/initiatives',{method:item?'PUT':'POST',body:JSON.stringify(corePayload())});const iid=item?.id||r.data.id;await api(`/api/initiatives/${iid}/profile`,{method:'PUT',body:JSON.stringify(profilePayload())});item=(await api(`/api/initiatives/${iid}`)).data;return {id:iid,message:r.message};};
  $('#iniSave').addEventListener('click',async()=>{try{const r=await save();if(r){toast('立项草稿已保存','success');navigate('initiative-form',{id:r.id});}}catch(e){toast(e.message,'error')}});
  if($('#iniDelete'))$('#iniDelete').addEventListener('click',async()=>{if(!confirm('确认删除当前立项草稿？'))return;try{await api(`/api/initiatives/${item.id}`,{method:'DELETE'});toast('已删除','success');navigate('initiative-list');}catch(e){toast(e.message,'error')}});
  $('#iniSubmit').addEventListener('click',async()=>{try{const r=await save();if(!r)return;const out=await api(`/api/initiatives/${r.id}/submit`,{method:'POST'});toast(out.message,'success');navigate('initiative-detail/'+r.id);}catch(e){toast(e.message,'error')}});
}

async function renderInitiativeList(){
  setPage({title:'立项列表',iconName:'send',crumbs:['立项管理'],actions:hasPermission('initiative.create')?btn(`${icon('plus')} 新建立项`,'btn primary','iniNew'):''});
  const items=(await api('/api/initiatives')).data;
  appView.innerHTML=`<div class="section"><div class="toolbar"><input id="iniSearch" class="field" style="max-width:320px" placeholder="搜索立项编号/名称/申请人"><select id="iniStatus" class="select" style="max-width:180px"><option value="">全部状态</option>${['草稿','审批中','已通过','已驳回'].map(x=>`<option>${x}</option>`).join('')}</select></div><div class="table-wrap"><table class="table"><thead><tr><th>立项编号</th><th>立项名称</th><th>项目类型</th><th>申请人</th><th>预估预算</th><th>状态</th><th>当前节点</th><th>操作</th></tr></thead><tbody id="iniRows"></tbody></table></div></div>`;
  const draw=()=>{const q=$('#iniSearch').value.trim().toLowerCase(),st=$('#iniStatus').value;const rows=items.filter(x=>(!q||`${x.initiative_no} ${x.title} ${x.applicant}`.toLowerCase().includes(q))&&(!st||x.status===st));$('#iniRows').innerHTML=rows.map(x=>`<tr><td>${x.initiative_no}</td><td>${esc(x.title)}</td><td>${esc(x.project_type||'系统建设')}</td><td>${esc(x.applicant)}</td><td>¥${money(x.estimated_budget)}</td><td>${statusPill(x.status)}</td><td>${esc(x.current_node)}</td><td><button class="link iniView" data-id="${x.id}">详情</button>${['草稿','已驳回'].includes(x.status)?` <button class="link iniEdit" data-id="${x.id}">编辑</button>`:''}</td></tr>`).join('')||'<tr><td colspan="8" class="subtle">暂无匹配数据</td></tr>';$$('.iniView').forEach(b=>b.addEventListener('click',()=>navigate(`initiative-detail/${b.dataset.id}`)));$$('.iniEdit').forEach(b=>b.addEventListener('click',()=>navigate('initiative-form',{id:b.dataset.id})));};
  $('#iniSearch').addEventListener('input',draw);$('#iniStatus').addEventListener('change',draw);if($('#iniNew'))$('#iniNew').addEventListener('click',()=>navigate('initiative-form'));draw();
}
async function renderInitiativeDetail(id){
  const x=(await api(`/api/initiatives/${id}`)).data;
  const actions=[];if(['草稿','已驳回'].includes(x.status))actions.push(btn('编辑','btn','iniEditDetail'));if(x.status==='已通过'&&['project_manager','admin'].includes(state.role))actions.push(btn('生成项目','btn primary','iniConvert'));
  setPage({title:'立项详情',iconName:'send',crumbs:['立项管理','立项列表'],actions:actions.join('')});
  appView.innerHTML=`<div class="grid-2"><div class="section"><div class="section-title">${esc(x.initiative_no)} · ${esc(x.title)}</div><div class="detail-grid"><div><span>项目类型</span><b>${esc(x.project_type||'系统建设')}</b></div><div><span>紧急程度</span><b>${esc(x.urgency||'中')}</b></div><div><span>申请人</span><b>${esc(x.applicant)}</b></div><div><span>所属部门</span><b>${esc(x.department)}</b></div><div><span>项目负责人</span><b>${esc(x.owner)}</b></div><div><span>Sponsor</span><b>${esc(x.sponsor||'—')}</b></div><div><span>预估预算</span><b>¥${money(x.estimated_budget)}</b></div><div><span>计划周期</span><b>${x.planned_start||'—'} ~ ${x.planned_end||'—'}</b></div><div><span>状态</span><b>${statusPill(x.status)}</b></div><div><span>当前节点</span><b>${esc(x.current_node)}</b></div></div></div><div class="section"><div class="section-title">审批记录</div>${approvalRecords(x.approvals)}</div></div>
  <div class="section" style="margin-top:12px"><div class="section-title">立项论证</div><div class="detail-block"><b>建设背景</b><p>${esc(x.background||'—')}</p><b>建设目标</b><p>${esc(x.objectives||'—')}</p><b>建设范围</b><p>${esc(x.scope||'—')}</p><b>预期收益</b><p>${esc(x.expected_benefit||'—')}</p><b>补充说明</b><p>${esc(x.description||'—')}</p></div></div>`;
  if($('#iniEditDetail'))$('#iniEditDetail').addEventListener('click',()=>navigate('initiative-form',{id}));
  if($('#iniConvert'))$('#iniConvert').addEventListener('click',async()=>{try{const r=await api(`/api/initiatives/${id}/convert-project`,{method:'POST'});toast(r.message,'success');navigate(`project-detail/${r.data.project_id}`);}catch(e){toast(e.message,'error')}});
}
async function renderInitiativeApprovals(){setPage({title:'立项审批',iconName:'approve',crumbs:['立项管理']});const items=(await api('/api/initiative-approvals/pending')).data;appView.innerHTML=`<div class="section"><div class="section-title">我的立项审批待办</div>${simpleTable(['立项编号','名称','预算','当前节点','操作'],items.map(x=>[x.initiative_no,esc(x.title),`¥${money(x.estimated_budget)}`,x.current_node,`<button class="link iniApprove" data-id="${x.id}">处理</button>`]))}</div>`;$$('.iniApprove').forEach(b=>b.addEventListener('click',async()=>{const x=(await api(`/api/initiatives/${b.dataset.id}`)).data;showModal(`<h3 id="modalTitle">${esc(x.current_node)}</h3><div class="callout">${esc(x.initiative_no)} · ${esc(x.title)} · ¥${money(x.estimated_budget)}</div><textarea id="iniComment" class="textarea" placeholder="审批意见"></textarea><div class="modal-actions">${btn('驳回','btn danger','iniReject')}${btn('通过','btn primary','iniPass')}</div>`);for(const [id,action] of [['iniReject','驳回'],['iniPass','通过']])$('#'+id).addEventListener('click',async()=>{await api(`/api/initiatives/${x.id}/approve`,{method:'POST',body:JSON.stringify({action,comment:$('#iniComment').value})});closeModal();toast(`已${action}`,'success');renderInitiativeApprovals();});}));}

async function renderProjectList(){setPage({title:'项目台账',iconName:'project',crumbs:['项目管理'],actions:['project_manager','admin'].includes(state.role)?btn(`${icon('plus')} 新建项目`,'btn primary','projectNew'):''});const items=(await api('/api/projects')).data;appView.innerHTML=`<div class="section"><div class="table-wrap"><table class="table"><thead><tr><th>项目编号</th><th>项目名称</th><th>项目经理</th><th>部门</th><th>预算</th><th>状态</th><th>进度</th><th>周期</th><th>操作</th></tr></thead><tbody>${items.map(x=>`<tr><td>${x.project_no}</td><td>${esc(x.name)}</td><td>${esc(x.manager)}</td><td>${esc(x.department)}</td><td>¥${money(x.total_budget)}</td><td>${statusPill(x.status)}</td><td>${progressCell(x.progress)}</td><td>${x.start_date||'—'} ~ ${x.end_date||'—'}</td><td><button class="link pView" data-id="${x.id}">项目详情</button></td></tr>`).join('')}</tbody></table></div></div>`;$$('.pView').forEach(b=>b.addEventListener('click',()=>navigate(`project-detail/${b.dataset.id}`)));if($('#projectNew'))$('#projectNew').addEventListener('click',()=>openProjectForm());}
async function openProjectForm(item={}){const budgets=(await api('/api/budget-ledger')).data;showModal(`<h3 id="modalTitle">${item.id?'编辑':'新建'}项目</h3><div class="form-row"><div class="label required">项目名称</div><input id="pName" class="field" value="${esc(item.name||'')}"></div><div class="grid-2"><div><label>项目经理</label><input id="pManager" class="field" value="${esc(item.manager||'王卫嘉')}"></div><div><label>部门</label><input id="pDept" class="field" value="${esc(item.department||'数字化管理部')}"></div></div><div class="grid-2" style="margin-top:10px"><div><label>关联预算</label><select id="pBudget" class="select"><option value="">请选择</option>${budgets.map(b=>`<option value="${b.id}" ${b.id===item.budget_id?'selected':''}>${esc(b.budget_no)} · ${esc(b.budget_name)}</option>`).join('')}</select></div><div><label>项目预算</label><input id="pTotal" type="number" class="field" value="${item.total_budget||0}"></div></div><div class="grid-3" style="margin-top:10px"><div><label>状态</label><select id="pStatus" class="select">${['规划中','实施中','暂停','已完成'].map(s=>`<option ${s===item.status?'selected':''}>${s}</option>`).join('')}</select></div><div><label>进度</label><input id="pProgress" type="number" min="0" max="100" class="field" value="${item.progress||0}"></div><div><label>开始日期</label><input id="pStart" type="date" class="field" value="${item.start_date||''}"></div></div><div style="margin-top:10px"><label>结束日期</label><input id="pEnd" type="date" class="field" value="${item.end_date||''}"></div><div style="margin-top:10px"><label>项目说明</label><textarea id="pDesc" class="textarea">${esc(item.description||'')}</textarea></div><div class="modal-actions">${btn('取消','btn','pCancel')}${btn('保存','btn primary','pSave')}</div>`);$('#pCancel').addEventListener('click',closeModal);$('#pSave').addEventListener('click',async()=>{const p={name:$('#pName').value,manager:$('#pManager').value,department:$('#pDept').value,budget_id:Number($('#pBudget').value)||null,total_budget:Number($('#pTotal').value),status:$('#pStatus').value,progress:Number($('#pProgress').value),start_date:$('#pStart').value||null,end_date:$('#pEnd').value||null,description:$('#pDesc').value};const r=await api(item.id?`/api/projects/${item.id}`:'/api/projects',{method:item.id?'PUT':'POST',body:JSON.stringify(p)});closeModal();toast(r.message,'success');renderProjectList();});}
async function renderProjectDetail(id,query){
  const [d,risks,deliverables]=await Promise.all([api(`/api/projects/${id}`),api(`/api/projects/${id}/risks`),api(`/api/projects/${id}/deliverables`)]);const p=d.data;
  setPage({title:'项目详情',iconName:'project',crumbs:['项目管理','项目台账'],actions:`${['project_manager','admin'].includes(state.role)?btn('编辑项目','btn','editProject'):''}${btn('360视图','btn primary','open360')}`});
  const high=risks.data.filter(x=>x.level==='高'&&x.status!=='已关闭').length;
  appView.innerHTML=`<div class="grid-4"><div class="metric"><div class="k">项目状态</div><div class="v">${esc(p.status)}</div><div class="sub">健康度 ${esc(p.health||'正常')}</div></div><div class="metric"><div class="k">总体进度</div><div class="v">${p.progress}%</div>${progressCell(p.progress,false)}</div><div class="metric"><div class="k">项目预算</div><div class="v" style="font-size:17px">¥${money(p.total_budget)}</div><div class="sub">${esc(p.budget?.budget_name||'未关联预算')}</div></div><div class="metric"><div class="k">风险</div><div class="v">${risks.data.length}</div><div class="sub">高风险 ${high} 项</div></div></div>
  <div class="grid-2" style="margin-top:12px"><div class="section"><div class="section-title">项目基本信息</div><div class="detail-grid"><div><span>项目编号</span><b>${p.project_no}</b></div><div><span>项目名称</span><b>${esc(p.name)}</b></div><div><span>项目经理</span><b>${esc(p.manager)}</b></div><div><span>所属部门</span><b>${esc(p.department)}</b></div><div><span>计划周期</span><b>${p.start_date||'—'} ~ ${p.end_date||'—'}</b></div><div><span>项目类型</span><b>${esc(p.project_type||'系统建设')}</b></div></div><div class="description">${esc(p.description||'')}</div></div><div class="section"><div class="section-title">里程碑</div>${simpleTable(['名称','计划日期','状态','负责人'],p.milestones.map(x=>[x.name,x.planned_date||'—',statusPill(x.status),x.owner]))}</div></div>
  <div class="grid-2" style="margin-top:12px"><div class="section"><div class="section-title">任务</div>${simpleTable(['任务','负责人','状态','进度'],p.tasks.slice(0,8).map(x=>[x.title,x.owner,statusPill(x.status),progressCell(x.progress)]))}</div><div class="section"><div class="section-title">风险与交付</div>${simpleTable(['类型','编号/名称','状态','负责人'],[...risks.data.slice(0,4).map(x=>['风险',x.risk_no+' '+esc(x.title),statusPill(x.level+' / '+x.status),esc(x.owner)]),...deliverables.data.slice(0,4).map(x=>['交付物',x.deliverable_no+' '+esc(x.name),statusPill(x.status),esc(x.owner)])])}</div></div>
  <div class="grid-2" style="margin-top:12px"><div class="section"><div class="section-title">合同</div>${simpleTable(['合同编号','名称','金额','状态'],p.contracts.map(x=>[x.contract_no,x.name,`¥${money(x.total_amount)}`,statusPill(x.status)]))}</div><div class="section"><div class="section-title">结算</div>${simpleTable(['结算编号','类型','金额','状态'],p.settlements.map(x=>[x.settlement_no,x.settlement_type,`¥${money(x.amount)}`,statusPill(x.status)]))}</div></div>`;
  if($('#editProject'))$('#editProject').addEventListener('click',()=>openProjectForm(p));$('#open360').addEventListener('click',()=>navigate('project360',{projectId:id}));
}

async function renderProjectGovernance(){
  setPage({title:'风险与交付',iconName:'risk',crumbs:['项目管理']});const projects=(await api('/api/projects')).data;const selected=Number(parseRoute().query.get('projectId')||projects[0]?.id||0);if(!selected){appView.innerHTML='<div class="empty">暂无项目</div>';return;}const [risks,delivs,detail]=await Promise.all([api(`/api/projects/${selected}/risks`),api(`/api/projects/${selected}/deliverables`),api(`/api/projects/${selected}`)]);
  appView.innerHTML=`<div class="section"><div class="toolbar"><select id="govProject" class="select" style="max-width:380px">${projects.map(p=>`<option value="${p.id}" ${p.id===selected?'selected':''}>${p.project_no} · ${esc(p.name)}</option>`).join('')}</select><div>${btn(`${icon('plus')} 新增风险`,'btn','riskNew')}${btn(`${icon('plus')} 新增交付物`,'btn primary','delivNew')}</div></div></div><div class="grid-2" style="margin-top:12px"><div class="section"><div class="section-title">项目风险</div>${simpleTable(['风险编号','风险','等级','责任人','状态','到期','操作'],risks.data.map(x=>[x.risk_no,esc(x.title),statusPill(x.level),esc(x.owner),statusPill(x.status),x.due_date||'—',`<button class="link riskEdit" data-id="${x.id}">编辑</button> <button class="link riskDel" data-id="${x.id}">删除</button>`]))}</div><div class="section"><div class="section-title">项目交付物</div>${simpleTable(['编号','名称','类型','版本','计划日期','状态','操作'],delivs.data.map(x=>[x.deliverable_no,esc(x.name),x.type,x.version,x.planned_date||'—',statusPill(x.status),`<button class="link delivEdit" data-id="${x.id}">编辑</button> <button class="link delivDel" data-id="${x.id}">删除</button>`]))}</div></div>`;
  $('#govProject').addEventListener('change',e=>navigate('project-governance',{projectId:e.target.value}));
  const riskForm=(x={})=>{showModal(`<h3 id="modalTitle">${x.id?'编辑':'新增'}项目风险</h3><input id="rTitle" class="field" placeholder="风险名称" value="${esc(x.title||'')}"><div class="grid-3" style="margin-top:10px"><select id="rCat" class="select">${(state.v4meta.risk_categories||[]).map(v=>`<option ${v===x.category?'selected':''}>${v}</option>`).join('')}</select><select id="rLevel" class="select">${['高','中','低'].map(v=>`<option ${v===x.level?'selected':''}>${v}</option>`).join('')}</select><select id="rStatus" class="select">${['跟踪中','处理中','已关闭'].map(v=>`<option ${v===x.status?'selected':''}>${v}</option>`).join('')}</select></div><div class="grid-2" style="margin-top:10px"><input id="rOwner" class="field" placeholder="责任人" value="${esc(x.owner||'')}"><input id="rDue" type="date" class="field" value="${x.due_date||''}"></div><textarea id="rPlan" class="textarea" style="margin-top:10px" placeholder="应对措施">${esc(x.response_plan||'')}</textarea><div class="modal-actions">${btn('取消','btn','rCancel')}${btn('保存','btn primary','rSave')}</div>`);$('#rCancel').addEventListener('click',closeModal);$('#rSave').addEventListener('click',async()=>{try{const payload={title:$('#rTitle').value,category:$('#rCat').value,probability:x.probability||'中',impact:x.impact||'中',level:$('#rLevel').value,owner:$('#rOwner').value,response_plan:$('#rPlan').value,status:$('#rStatus').value,due_date:$('#rDue').value||null};await api(x.id?`/api/project-risks/${x.id}`:`/api/projects/${selected}/risks`,{method:x.id?'PUT':'POST',body:JSON.stringify(payload)});closeModal();renderProjectGovernance();}catch(e){toast(e.message,'error')}});};
  const delivForm=(x={})=>{showModal(`<h3 id="modalTitle">${x.id?'编辑':'新增'}交付物</h3><input id="dName" class="field" placeholder="交付物名称" value="${esc(x.name||'')}"><div class="grid-3" style="margin-top:10px"><select id="dType" class="select">${(state.v4meta.deliverable_types||[]).map(v=>`<option ${v===x.type?'selected':''}>${v}</option>`).join('')}</select><input id="dVer" class="field" value="${esc(x.version||'V1.0')}" placeholder="版本"><select id="dStatus" class="select">${['未提交','编制中','待审核','已交付','已验收'].map(v=>`<option ${v===x.status?'selected':''}>${v}</option>`).join('')}</select></div><div class="grid-2" style="margin-top:10px"><input id="dOwner" class="field" placeholder="负责人" value="${esc(x.owner||'')}"><input id="dDate" type="date" class="field" value="${x.planned_date||''}"></div><input id="dUrl" class="field" style="margin-top:10px" placeholder="文件/知识库链接" value="${esc(x.url||'')}"><textarea id="dDesc" class="textarea" style="margin-top:10px" placeholder="说明">${esc(x.description||'')}</textarea><div class="modal-actions">${btn('取消','btn','dCancel')}${btn('保存','btn primary','dSave')}</div>`);$('#dCancel').addEventListener('click',closeModal);$('#dSave').addEventListener('click',async()=>{try{const payload={name:$('#dName').value,type:$('#dType').value,milestone_id:null,owner:$('#dOwner').value,planned_date:$('#dDate').value||null,actual_date:x.actual_date||null,version:$('#dVer').value,status:$('#dStatus').value,url:$('#dUrl').value,description:$('#dDesc').value};await api(x.id?`/api/deliverables/${x.id}`:`/api/projects/${selected}/deliverables`,{method:x.id?'PUT':'POST',body:JSON.stringify(payload)});closeModal();renderProjectGovernance();}catch(e){toast(e.message,'error')}});};
  $('#riskNew').addEventListener('click',()=>riskForm());$$('.riskEdit').forEach(b=>b.addEventListener('click',()=>riskForm(risks.data.find(x=>x.id===Number(b.dataset.id)))));$$('.riskDel').forEach(b=>b.addEventListener('click',async()=>{if(confirm('确认删除该风险？')){await api(`/api/project-risks/${b.dataset.id}`,{method:'DELETE'});renderProjectGovernance();}}));$('#delivNew').addEventListener('click',()=>delivForm());$$('.delivEdit').forEach(b=>b.addEventListener('click',()=>delivForm(delivs.data.find(x=>x.id===Number(b.dataset.id)))));$$('.delivDel').forEach(b=>b.addEventListener('click',async()=>{if(confirm('确认删除该交付物？')){await api(`/api/deliverables/${b.dataset.id}`,{method:'DELETE'});renderProjectGovernance();}}));
}

async function renderProjectTasks(){setPage({title:'任务管理',iconName:'project',crumbs:['项目管理']});const projects=(await api('/api/projects')).data;const selected=Number(parseRoute().query.get('projectId')||projects[0]?.id||0);if(!selected){appView.innerHTML='<div class="empty">暂无项目</div>';return;}const d=(await api(`/api/projects/${selected}`)).data;appView.innerHTML=`<div class="section"><div class="toolbar"><select id="taskProject" class="select" style="max-width:360px">${projects.map(p=>`<option value="${p.id}" ${p.id===selected?'selected':''}>${p.project_no} · ${esc(p.name)}</option>`).join('')}</select>${btn(`${icon('plus')} 新建任务`,'btn primary','taskNew')}</div>${simpleTable(['任务编号','任务名称','负责人','状态','优先级','进度','计划周期','操作'],d.tasks.map(x=>[x.task_no,esc(x.title),esc(x.owner),statusPill(x.status),x.priority,progressCell(x.progress),`${x.start_date||'—'} ~ ${x.end_date||'—'}`,`<button class="link taskEdit" data-id="${x.id}">编辑</button> <button class="link taskDel" data-id="${x.id}">删除</button>`]))}</div>`;$('#taskProject').addEventListener('change',e=>navigate('project-tasks',{projectId:e.target.value}));const form=(x={})=>{showModal(`<h3 id="modalTitle">${x.id?'编辑':'新建'}任务</h3><input id="tTitle" class="field" placeholder="任务名称" value="${esc(x.title||'')}"><div class="grid-2" style="margin-top:10px"><input id="tOwner" class="field" placeholder="负责人" value="${esc(x.owner||'')}"><select id="tStatus" class="select">${['未开始','进行中','已完成','暂停'].map(s=>`<option ${s===x.status?'selected':''}>${s}</option>`).join('')}</select></div><div class="grid-2" style="margin-top:10px"><select id="tPri" class="select">${['高','中','低'].map(s=>`<option ${s===x.priority?'selected':''}>${s}</option>`).join('')}</select><input id="tProgress" type="number" class="field" value="${x.progress||0}"></div><div class="grid-2" style="margin-top:10px"><input id="tStart" type="date" class="field" value="${x.start_date||''}"><input id="tEnd" type="date" class="field" value="${x.end_date||''}"></div><div class="modal-actions">${btn('取消','btn','tCancel')}${btn('保存','btn primary','tSave')}</div>`);$('#tCancel').addEventListener('click',closeModal);$('#tSave').addEventListener('click',async()=>{const p={title:$('#tTitle').value,owner:$('#tOwner').value,status:$('#tStatus').value,priority:$('#tPri').value,progress:Number($('#tProgress').value),start_date:$('#tStart').value||null,end_date:$('#tEnd').value||null};await api(x.id?`/api/project-tasks/${x.id}`:`/api/projects/${selected}/tasks`,{method:x.id?'PUT':'POST',body:JSON.stringify(p)});closeModal();renderProjectTasks();});};$('#taskNew').addEventListener('click',()=>form());$$('.taskEdit').forEach(b=>b.addEventListener('click',()=>form(d.tasks.find(x=>x.id===Number(b.dataset.id)))));$$('.taskDel').forEach(b=>b.addEventListener('click',async()=>{await api(`/api/project-tasks/${b.dataset.id}`,{method:'DELETE'});renderProjectTasks();}));}
async function renderMilestones(){setPage({title:'里程碑管理',iconName:'project',crumbs:['项目管理']});const projects=(await api('/api/projects')).data;const selected=Number(parseRoute().query.get('projectId')||projects[0]?.id||0);if(!selected){appView.innerHTML='<div class="empty">暂无项目</div>';return;}const d=(await api(`/api/projects/${selected}`)).data;appView.innerHTML=`<div class="section"><div class="toolbar"><select id="mProject" class="select" style="max-width:360px">${projects.map(p=>`<option value="${p.id}" ${p.id===selected?'selected':''}>${p.project_no} · ${esc(p.name)}</option>`).join('')}</select>${btn(`${icon('plus')} 新建里程碑`,'btn primary','mNew')}</div>${simpleTable(['里程碑','计划日期','实际日期','状态','负责人','说明','操作'],d.milestones.map(x=>[esc(x.name),x.planned_date||'—',x.actual_date||'—',statusPill(x.status),esc(x.owner),esc(x.description),`<button class="link mEdit" data-id="${x.id}">编辑</button> <button class="link mDel" data-id="${x.id}">删除</button>`]))}</div>`;$('#mProject').addEventListener('change',e=>navigate('milestones',{projectId:e.target.value}));const form=(x={})=>{showModal(`<h3 id="modalTitle">${x.id?'编辑':'新建'}里程碑</h3><input id="mName" class="field" placeholder="里程碑名称" value="${esc(x.name||'')}"><div class="grid-2" style="margin-top:10px"><input id="mPlan" type="date" class="field" value="${x.planned_date||''}"><input id="mActual" type="date" class="field" value="${x.actual_date||''}"></div><div class="grid-2" style="margin-top:10px"><select id="mStatus" class="select">${['未完成','进行中','已完成','延期'].map(s=>`<option ${s===x.status?'selected':''}>${s}</option>`).join('')}</select><input id="mOwner" class="field" placeholder="负责人" value="${esc(x.owner||'')}"></div><textarea id="mDesc" class="textarea" style="margin-top:10px">${esc(x.description||'')}</textarea><div class="modal-actions">${btn('取消','btn','mCancel')}${btn('保存','btn primary','mSave')}</div>`);$('#mCancel').addEventListener('click',closeModal);$('#mSave').addEventListener('click',async()=>{const p={name:$('#mName').value,planned_date:$('#mPlan').value||null,actual_date:$('#mActual').value||null,status:$('#mStatus').value,owner:$('#mOwner').value,description:$('#mDesc').value};await api(x.id?`/api/milestones/${x.id}`:`/api/projects/${selected}/milestones`,{method:x.id?'PUT':'POST',body:JSON.stringify(p)});closeModal();renderMilestones();});};$('#mNew').addEventListener('click',()=>form());$$('.mEdit').forEach(b=>b.addEventListener('click',()=>form(d.milestones.find(x=>x.id===Number(b.dataset.id)))));$$('.mDel').forEach(b=>b.addEventListener('click',async()=>{await api(`/api/milestones/${b.dataset.id}`,{method:'DELETE'});renderMilestones();}));}

async function renderSettlementForm(){
  const id=Number(parseRoute().query.get('id')||0);const projects=(await api('/api/projects')).data,contracts=(await api('/api/contracts')).data,budgets=(await api('/api/budget-ledger')).data,all=(await api('/api/settlements')).data,item=id?all.find(x=>x.id===id):null;
  setPage({title:'结算申请',iconName:'settle',crumbs:['结算管理'],actions:`${btn('保存草稿','btn','setSave')}${btn('提交审批','btn primary','setSubmit')}`});
  appView.innerHTML=`<div class="section"><div class="section-title">结算基本信息</div><div class="grid-3"><div><label>结算类型</label><select id="setType" class="select">${['项目结算','合同付款结算','费用报销结算'].map(v=>`<option ${v===item?.settlement_type?'selected':''}>${v}</option>`).join('')}</select></div><div><label>项目</label><select id="setProject" class="select"><option value="">请选择</option>${projects.map(x=>`<option value="${x.id}" ${x.id===item?.project_id?'selected':''}>${x.project_no} · ${esc(x.name)}</option>`).join('')}</select></div><div><label>合同</label><select id="setContract" class="select"><option value="">请选择</option>${contracts.map(x=>`<option value="${x.id}" ${x.id===item?.contract_id?'selected':''}>${x.contract_no} · ${esc(x.name)}</option>`).join('')}</select></div></div><div class="grid-3" style="margin-top:10px"><div><label>预算</label><select id="setBudget" class="select"><option value="">请选择</option>${budgets.map(x=>`<option value="${x.id}" ${x.id===item?.budget_id?'selected':''}>${x.budget_no} · ${esc(x.budget_name)}</option>`).join('')}</select></div><div><label>结算金额</label><input id="setAmount" type="number" class="field" value="${item?.amount||0}"></div><div><label>申请人</label><input id="setApplicant" class="field" value="${esc(item?.applicant||state.userDisplay)}"></div></div><div style="margin-top:10px"><label>结算说明</label><textarea id="setDesc" class="textarea">${esc(item?.description||'')}</textarea></div>${item?`<div class="callout" style="margin-top:12px">${item.settlement_no} · ${statusPill(item.status)} · ${esc(item.current_node)}<div class="help">保存后可在结算详情中维护明细项，明细合计会自动回写结算金额。</div></div>`:''}</div>`;
  const payload=()=>({project_id:Number($('#setProject').value)||null,contract_id:Number($('#setContract').value)||null,budget_id:Number($('#setBudget').value)||null,amount:Number($('#setAmount').value),settlement_type:$('#setType').value,applicant:$('#setApplicant').value,description:$('#setDesc').value});
  $('#setSave').addEventListener('click',async()=>{try{const r=await api(item?`/api/settlements/${item.id}`:'/api/settlements',{method:item?'PUT':'POST',body:JSON.stringify(payload())});toast(r.message,'success');navigate(item?`settlement-detail/${item.id}`:`settlement-detail/${r.data.id}`);}catch(e){toast(e.message,'error')}});$('#setSubmit').addEventListener('click',async()=>{try{let sid=item?.id;if(!sid){const r=await api('/api/settlements',{method:'POST',body:JSON.stringify(payload())});sid=r.data.id;}const r=await api(`/api/settlements/${sid}/submit`,{method:'POST'});toast(r.message,'success');navigate(`settlement-detail/${sid}`);}catch(e){toast(e.message,'error')}});
}
async function renderSettlementList(){
  setPage({title:'结算台账',iconName:'settle',crumbs:['结算管理'],actions:btn(`${icon('plus')} 新建结算`,'btn primary','setNew')});const items=(await api('/api/settlements')).data;appView.innerHTML=`<div class="section"><div class="toolbar"><input id="setSearch" class="field" style="max-width:320px" placeholder="搜索结算编号/项目/合同"><select id="setStatus" class="select" style="max-width:180px"><option value="">全部状态</option>${['草稿','审批中','已完成','已驳回'].map(v=>`<option>${v}</option>`).join('')}</select></div><div id="setRows"></div></div>`;const draw=()=>{const q=$('#setSearch').value.trim().toLowerCase(),st=$('#setStatus').value;const rows=items.filter(x=>(!q||`${x.settlement_no} ${x.project_name||''} ${x.contract_name||''}`.toLowerCase().includes(q))&&(!st||x.status===st));$('#setRows').innerHTML=simpleTable(['结算编号','项目','合同','类型','金额','申请人','状态','当前节点','操作'],rows.map(x=>[x.settlement_no,esc(x.project_name||'—'),esc(x.contract_name||'—'),x.settlement_type,`¥${money(x.amount)}`,esc(x.applicant),statusPill(x.status),esc(x.current_node),`<button class="link setView" data-id="${x.id}">详情</button>${['草稿','已驳回'].includes(x.status)?` <button class="link setEdit" data-id="${x.id}">编辑</button>`:''}`]));$$('.setView').forEach(b=>b.addEventListener('click',()=>navigate(`settlement-detail/${b.dataset.id}`)));$$('.setEdit').forEach(b=>b.addEventListener('click',()=>navigate('settlement-form',{id:b.dataset.id})));};$('#setNew').addEventListener('click',()=>navigate('settlement-form'));$('#setSearch').addEventListener('input',draw);$('#setStatus').addEventListener('change',draw);draw();
}
async function renderSettlementDetail(id){
  const s=(await api(`/api/settlements/${id}/detail`)).data;setPage({title:'结算详情',iconName:'settle',crumbs:['结算管理','结算台账'],actions:`${['草稿','已驳回'].includes(s.status)?btn('编辑基本信息','btn','setEditDetail')+btn('提交审批','btn primary','setSubmitDetail'):''}`});
  appView.innerHTML=`<div class="grid-2"><div class="section"><div class="section-title">${s.settlement_no} · ${s.settlement_type}</div><div class="detail-grid"><div><span>项目</span><b>${esc(s.project_name||'—')}</b></div><div><span>合同</span><b>${esc(s.contract_name||'—')}</b></div><div><span>预算</span><b>${esc(s.budget_name||'—')}</b></div><div><span>金额</span><b>¥${money(s.amount)}</b></div><div><span>申请人</span><b>${esc(s.applicant)}</b></div><div><span>状态</span><b>${statusPill(s.status)}</b></div></div><div class="description">${esc(s.description||'')}</div></div><div class="section"><div class="section-title">审批记录</div>${approvalRecords(s.approvals)}</div></div><div class="section" style="margin-top:12px"><div class="toolbar"><div><div class="section-title">结算明细</div><div class="section-subtitle">数量 × 单价自动计算，明细合计自动回写结算金额</div></div>${['草稿','已驳回'].includes(s.status)?btn(`${icon('plus')} 新增明细`,'btn primary','setItemNew'):''}</div>${simpleTable(['明细名称','类型','数量','单价','金额','说明','操作'],s.items.map(x=>[esc(x.item_name),x.item_type,x.quantity,`¥${money(x.unit_price)}`,`¥${money(x.amount)}`,esc(x.description),['草稿','已驳回'].includes(s.status)?`<button class="link setItemDel" data-id="${x.id}">删除</button>`:'—']))}</div>`;
  if($('#setEditDetail'))$('#setEditDetail').addEventListener('click',()=>navigate('settlement-form',{id}));if($('#setSubmitDetail'))$('#setSubmitDetail').addEventListener('click',async()=>{try{const r=await api(`/api/settlements/${id}/submit`,{method:'POST'});toast(r.message,'success');renderSettlementDetail(id);}catch(e){toast(e.message,'error')}});if($('#setItemNew'))$('#setItemNew').addEventListener('click',()=>{showModal(`<h3 id="modalTitle">新增结算明细</h3><div class="grid-2"><input id="siName" class="field" placeholder="明细名称"><select id="siType" class="select">${['服务费','软件费','硬件费','实施费','咨询费','其他'].map(v=>`<option>${v}</option>`).join('')}</select></div><div class="grid-2" style="margin-top:10px"><input id="siQty" type="number" class="field" value="1" min="0"><input id="siPrice" type="number" class="field" value="0" min="0"></div><input id="siDesc" class="field" style="margin-top:10px" placeholder="说明"><div class="modal-actions">${btn('取消','btn','siCancel')}${btn('保存','btn primary','siSave')}</div>`);$('#siCancel').addEventListener('click',closeModal);$('#siSave').addEventListener('click',async()=>{try{await api(`/api/settlements/${id}/items`,{method:'POST',body:JSON.stringify({item_name:$('#siName').value,item_type:$('#siType').value,quantity:Number($('#siQty').value),unit_price:Number($('#siPrice').value),description:$('#siDesc').value})});closeModal();renderSettlementDetail(id);}catch(e){toast(e.message,'error')}});});$$('.setItemDel').forEach(b=>b.addEventListener('click',async()=>{if(confirm('确认删除该结算明细？')){await api(`/api/settlement-items/${b.dataset.id}`,{method:'DELETE'});renderSettlementDetail(id);}}));
}
async function renderSettlementApprovals(){setPage({title:'结算审批',iconName:'approve',crumbs:['结算管理']});const items=(await api('/api/settlement-approvals/pending')).data;appView.innerHTML=`<div class="section"><div class="section-title">我的结算审批待办</div>${simpleTable(['结算编号','类型','金额','申请人','当前节点','操作'],items.map(x=>[x.settlement_no,x.settlement_type,`¥${money(x.amount)}`,esc(x.applicant),x.current_node,`<button class="link setApprove" data-id="${x.id}">处理</button>`]))}</div>`;$$('.setApprove').forEach(b=>b.addEventListener('click',()=>openGenericApproval('结算',b.dataset.id,`/api/settlements/${b.dataset.id}/approve`,renderSettlementApprovals)));}
function openGenericApproval(type,id,url,refresh){showModal(`<h3 id="modalTitle">${type}审批</h3><textarea id="genericComment" class="textarea" placeholder="审批意见"></textarea><div class="modal-actions">${btn('驳回','btn danger','gReject')}${btn('通过','btn primary','gPass')}</div>`);for(const [bid,action] of [['gReject','驳回'],['gPass','通过']])$('#'+bid).addEventListener('click',async()=>{await api(url,{method:'POST',body:JSON.stringify({action,comment:$('#genericComment').value})});closeModal();toast(`已${action}`,'success');refresh();});}

async function renderIndicatorList(){setPage({title:'指标维护',iconName:'indicator',crumbs:['指标库'],actions:btn(`${icon('plus')} 新建指标`,'btn primary','kpiNew')});const items=(await api('/api/indicators')).data;appView.innerHTML=`<div class="section">${simpleTable(['指标编号','指标名称','分类','单位','目标值','当前值','频率','负责人','状态','操作'],items.map(x=>[x.indicator_no,esc(x.name),x.category,x.unit,x.target_value,x.current_value,x.frequency,esc(x.owner),statusPill(x.status),`<button class="link kpiEdit" data-id="${x.id}">编辑</button> <button class="link kpiDel" data-id="${x.id}">删除</button>`]))}</div>`;const form=(x={})=>{showModal(`<h3 id="modalTitle">${x.id?'编辑':'新建'}指标</h3><input id="kName" class="field" placeholder="指标名称" value="${esc(x.name||'')}"><div class="grid-2" style="margin-top:10px"><input id="kCat" class="field" placeholder="分类" value="${esc(x.category||'流程效率')}"><input id="kUnit" class="field" placeholder="单位" value="${esc(x.unit||'%')}"></div><div class="grid-2" style="margin-top:10px"><input id="kTarget" type="number" class="field" value="${x.target_value||0}"><input id="kCurrent" type="number" class="field" value="${x.current_value||0}"></div><input id="kFormula" class="field" style="margin-top:10px" placeholder="计算公式" value="${esc(x.formula||'')}"><div class="grid-3" style="margin-top:10px"><input id="kSource" class="field" placeholder="数据来源" value="${esc(x.data_source||'')}"><select id="kFreq" class="select">${['实时','日度','周度','月度','季度','年度'].map(s=>`<option ${s===x.frequency?'selected':''}>${s}</option>`).join('')}</select><input id="kOwner" class="field" placeholder="负责人" value="${esc(x.owner||'')}"></div><div class="modal-actions">${btn('取消','btn','kCancel')}${btn('保存','btn primary','kSave')}</div>`);$('#kCancel').addEventListener('click',closeModal);$('#kSave').addEventListener('click',async()=>{const p={name:$('#kName').value,category:$('#kCat').value,unit:$('#kUnit').value,formula:$('#kFormula').value,target_value:Number($('#kTarget').value),current_value:Number($('#kCurrent').value),data_source:$('#kSource').value,frequency:$('#kFreq').value,owner:$('#kOwner').value,status:x.status||'启用'};await api(x.id?`/api/indicators/${x.id}`:'/api/indicators',{method:x.id?'PUT':'POST',body:JSON.stringify(p)});closeModal();renderIndicatorList();});};$('#kpiNew').addEventListener('click',()=>form());$$('.kpiEdit').forEach(b=>b.addEventListener('click',()=>form(items.find(x=>x.id===Number(b.dataset.id)))));$$('.kpiDel').forEach(b=>b.addEventListener('click',async()=>{await api(`/api/indicators/${b.dataset.id}`,{method:'DELETE'});renderIndicatorList();}));}
async function renderIndicatorData(){setPage({title:'指标数据',iconName:'indicator',crumbs:['指标库']});const items=(await api('/api/indicators')).data;appView.innerHTML=`<div class="section">${simpleTable(['指标','当前值','目标值','最近周期','趋势记录','操作'],items.map(x=>[`${x.indicator_no} · ${esc(x.name)}`,`${x.current_value}${x.unit}`,`${x.target_value}${x.unit}`,x.records[0]?.period||'—',x.records.slice(0,3).map(r=>`${r.period}:${r.value}`).join(' / ')||'—',`<button class="link kData" data-id="${x.id}">录入数据</button>`]))}</div>`;$$('.kData').forEach(b=>b.addEventListener('click',()=>{const x=items.find(i=>i.id===Number(b.dataset.id));showModal(`<h3 id="modalTitle">录入指标数据</h3><div class="callout">${esc(x.indicator_no)} · ${esc(x.name)}</div><div class="grid-2" style="margin-top:10px"><input id="krPeriod" class="field" placeholder="周期，如2026-08"><input id="krValue" type="number" class="field" placeholder="指标值"></div><input id="krSource" class="field" style="margin-top:10px" placeholder="来源说明"><div class="modal-actions">${btn('取消','btn','krCancel')}${btn('保存','btn primary','krSave')}</div>`);$('#krCancel').addEventListener('click',closeModal);$('#krSave').addEventListener('click',async()=>{await api(`/api/indicators/${x.id}/records`,{method:'POST',body:JSON.stringify({period:$('#krPeriod').value,value:Number($('#krValue').value),source:$('#krSource').value})});closeModal();renderIndicatorData();});}));}
async function renderIndicatorBoard(){setPage({title:'指标看板',iconName:'indicator',crumbs:['指标库']});const items=(await api('/api/indicators')).data;appView.innerHTML=`<div class="grid-3">${items.map(x=>{const rate=Number(x.target_value)?Number(x.current_value)/Number(x.target_value)*100:0;return `<div class="metric"><div class="k">${esc(x.category)} · ${esc(x.indicator_no)}</div><div class="v" style="font-size:17px">${esc(x.name)}</div><div class="kpi-number" style="font-size:22px">${x.current_value}${esc(x.unit)}</div><div class="sub">目标 ${x.target_value}${esc(x.unit)}</div><div class="progress" style="margin-top:10px"><span style="width:${Math.min(100,Math.max(0,rate))}%"></span></div><div class="sub">目标达成 ${rate.toFixed(1)}%</div></div>`;}).join('')}</div>`;}

async function renderContractList(){setPage({title:'合同台账',iconName:'contract',crumbs:['合同管理'],actions:btn(`${icon('plus')} 新建合同`,'btn primary','cNew')});const items=(await api('/api/contracts')).data;appView.innerHTML=`<div class="section">${simpleTable(['合同编号','合同名称','项目','供应商','合同金额','周期','状态','当前节点','操作'],items.map(x=>[x.contract_no,esc(x.name),esc(x.project_name||'—'),esc(x.supplier),`¥${money(x.total_amount)}`,`${x.start_date||'—'} ~ ${x.end_date||'—'}`,statusPill(x.status),x.current_node,`<button class="link cView" data-id="${x.id}">详情</button>`]))}</div>`;$('#cNew').addEventListener('click',()=>openContractForm());$$('.cView').forEach(b=>b.addEventListener('click',()=>navigate(`contract-detail/${b.dataset.id}`)));}
async function openContractForm(item={}){const projects=(await api('/api/projects')).data,budgets=(await api('/api/budget-ledger')).data;showModal(`<h3 id="modalTitle">${item.id?'编辑':'新建'}合同</h3><input id="cName" class="field" placeholder="合同名称" value="${esc(item.name||'')}"><div class="grid-2" style="margin-top:10px"><select id="cProject" class="select"><option value="">关联项目</option>${projects.map(x=>`<option value="${x.id}" ${x.id===item.project_id?'selected':''}>${x.project_no} · ${esc(x.name)}</option>`).join('')}</select><select id="cBudget" class="select"><option value="">关联预算</option>${budgets.map(x=>`<option value="${x.id}" ${x.id===item.budget_id?'selected':''}>${x.budget_no} · ${esc(x.budget_name)}</option>`).join('')}</select></div><div class="grid-2" style="margin-top:10px"><input id="cSupplier" class="field" placeholder="供应商" value="${esc(item.supplier||'')}"><input id="cAmount" type="number" class="field" value="${item.total_amount||0}"></div><div class="grid-2" style="margin-top:10px"><input id="cStart" type="date" class="field" value="${item.start_date||''}"><input id="cEnd" type="date" class="field" value="${item.end_date||''}"></div><input id="cOwner" class="field" style="margin-top:10px" placeholder="合同负责人" value="${esc(item.owner||'')}"><textarea id="cDesc" class="textarea" style="margin-top:10px">${esc(item.description||'')}</textarea><div class="modal-actions">${btn('取消','btn','cCancel')}${btn('保存','btn primary','cSave')}</div>`);$('#cCancel').addEventListener('click',closeModal);$('#cSave').addEventListener('click',async()=>{const p={name:$('#cName').value,project_id:Number($('#cProject').value)||null,budget_id:Number($('#cBudget').value)||null,supplier:$('#cSupplier').value,total_amount:Number($('#cAmount').value),start_date:$('#cStart').value||null,end_date:$('#cEnd').value||null,owner:$('#cOwner').value,description:$('#cDesc').value};await api(item.id?`/api/contracts/${item.id}`:'/api/contracts',{method:item.id?'PUT':'POST',body:JSON.stringify(p)});closeModal();renderContractList();});}
async function renderContractDetail(id){
  const [cr,changes]=await Promise.all([api(`/api/contracts/${id}`),api(`/api/contracts/${id}/changes`)]);const c=cr.data;
  setPage({title:'合同详情',iconName:'contract',crumbs:['合同管理','合同台账'],actions:`${['草稿','已驳回'].includes(c.status)?btn('编辑','btn','cEdit')+btn('提交审批','btn primary','cSubmit'):''}${btn('新增变更','btn','cChange')}`});
  appView.innerHTML=`<div class="grid-2"><div class="section"><div class="section-title">${esc(c.contract_no)} · ${esc(c.name)}</div><div class="detail-grid"><div><span>供应商</span><b>${esc(c.supplier)}</b></div><div><span>合同金额</span><b>¥${money(c.total_amount)}</b></div><div><span>周期</span><b>${c.start_date||'—'} ~ ${c.end_date||'—'}</b></div><div><span>状态</span><b>${statusPill(c.status)}</b></div><div><span>当前节点</span><b>${esc(c.current_node)}</b></div><div><span>负责人</span><b>${esc(c.owner)}</b></div></div><div class="description">${esc(c.description)}</div></div><div class="section"><div class="section-title">审批记录</div>${approvalRecords(c.approvals)}</div></div>
  <div class="section" style="margin-top:12px"><div class="toolbar"><div class="section-title">收付款计划</div>${btn(`${icon('plus')} 新增付款计划`,'btn primary','payNew')}</div>${simpleTable(['计划编号','类型','金额','计划日期','实际日期','状态','说明','操作'],c.payments.map(x=>[x.plan_no,x.payment_type,`¥${money(x.amount)}`,x.planned_date||'—',x.actual_date||'—',statusPill(x.status),esc(x.description),`<button class="link payEdit" data-id="${x.id}">编辑</button>${x.status!=='已支付'?` <button class="link payDel" data-id="${x.id}">删除</button>`:''}`]))}</div>
  <div class="section" style="margin-top:12px"><div class="section-title">合同变更</div>${simpleTable(['变更编号','类型','原因','金额变化','变更后金额','负责人','状态','生效日期','操作'],changes.data.map(x=>[x.change_no,x.change_type,esc(x.reason),`¥${money(x.amount_delta)}`,`¥${money(x.after_amount)}`,esc(x.owner),statusPill(x.status),x.effective_date||'—',x.status!=='已生效'&&['business_owner','admin'].includes(state.role)?`<button class="link chEffective" data-id="${x.id}">确认生效</button>`:'—']))}</div>`;
  if($('#cEdit'))$('#cEdit').addEventListener('click',()=>openContractForm(c));if($('#cSubmit'))$('#cSubmit').addEventListener('click',async()=>{try{const r=await api(`/api/contracts/${id}/submit`,{method:'POST'});toast(r.message,'success');renderContractDetail(id);}catch(e){toast(e.message,'error')}});$('#payNew').addEventListener('click',()=>openPaymentForm(id));$$('.payEdit').forEach(b=>b.addEventListener('click',()=>openPaymentForm(id,c.payments.find(x=>x.id===Number(b.dataset.id)))));$$('.payDel').forEach(b=>b.addEventListener('click',async()=>{if(confirm('确认删除该付款计划？')){await api(`/api/payment-plans/${b.dataset.id}`,{method:'DELETE'});renderContractDetail(id);}}));$('#cChange').addEventListener('click',()=>{showModal(`<h3 id="modalTitle">新增合同变更</h3><div class="grid-2"><select id="chType" class="select">${(state.v4meta.contract_change_types||[]).map(v=>`<option>${v}</option>`).join('')}</select><input id="chDelta" type="number" class="field" value="0" placeholder="金额变化，可为负数"></div><input id="chOwner" class="field" style="margin-top:10px" placeholder="负责人"><textarea id="chReason" class="textarea" style="margin-top:10px" placeholder="变更原因"></textarea><div class="modal-actions">${btn('取消','btn','chCancel')}${btn('保存','btn primary','chSave')}</div>`);$('#chCancel').addEventListener('click',closeModal);$('#chSave').addEventListener('click',async()=>{try{await api(`/api/contracts/${id}/changes`,{method:'POST',body:JSON.stringify({change_type:$('#chType').value,reason:$('#chReason').value,amount_delta:Number($('#chDelta').value),owner:$('#chOwner').value,status:'待确认',effective_date:null})});closeModal();renderContractDetail(id);}catch(e){toast(e.message,'error')}});});$$('.chEffective').forEach(b=>b.addEventListener('click',async()=>{await api(`/api/contract-changes/${b.dataset.id}/effective`,{method:'PUT'});toast('合同变更已生效','success');renderContractDetail(id);}));
}
async function openPaymentForm(contractId,item={}){showModal(`<h3 id="modalTitle">${item.id?'编辑':'新增'}付款计划</h3><div class="grid-2"><input id="payType" class="field" placeholder="付款类型，如里程碑款" value="${esc(item.payment_type||'')}"><input id="payAmount" type="number" class="field" placeholder="金额" value="${item.amount||0}"></div><div class="grid-2" style="margin-top:10px"><input id="payDate" type="date" class="field" value="${item.planned_date||''}"><select id="payStatus" class="select">${['待支付','已支付','延期'].map(v=>`<option ${v===item.status?'selected':''}>${v}</option>`).join('')}</select></div><input id="payActual" type="date" class="field" style="margin-top:10px" value="${item.actual_date||''}"><input id="payDesc" class="field" style="margin-top:10px" placeholder="说明" value="${esc(item.description||'')}"><div class="modal-actions">${btn('取消','btn','payCancel')}${btn('保存','btn primary','paySave')}</div>`);$('#payCancel').addEventListener('click',closeModal);$('#paySave').addEventListener('click',async()=>{try{const payload={payment_type:$('#payType').value,amount:Number($('#payAmount').value),planned_date:$('#payDate').value||null,actual_date:$('#payActual').value||null,status:$('#payStatus').value,description:$('#payDesc').value};await api(item.id?`/api/payment-plans/${item.id}`:`/api/contracts/${contractId}/payments`,{method:item.id?'PUT':'POST',body:JSON.stringify(payload)});closeModal();renderContractDetail(contractId);}catch(e){toast(e.message,'error')}});}
async function renderPaymentPlans(){setPage({title:'收付款计划',iconName:'contract',crumbs:['合同管理']});const contracts=(await api('/api/contracts')).data;const details=await Promise.all(contracts.map(x=>api(`/api/contracts/${x.id}`)));const rows=[];details.forEach((r,i)=>r.data.payments.forEach(p=>rows.push([contracts[i].contract_no,esc(contracts[i].name),p.plan_no,p.payment_type,`¥${money(p.amount)}`,p.planned_date||'—',p.actual_date||'—',statusPill(p.status)])));appView.innerHTML=`<div class="section">${simpleTable(['合同编号','合同名称','计划编号','类型','金额','计划日期','实际日期','状态'],rows)}</div>`;}
async function renderContractApprovals(){setPage({title:'合同审批',iconName:'approve',crumbs:['合同管理']});const items=(await api('/api/contract-approvals/pending')).data;appView.innerHTML=`<div class="section"><div class="section-title">我的合同审批待办</div>${simpleTable(['合同编号','合同名称','供应商','金额','当前节点','操作'],items.map(x=>[x.contract_no,esc(x.name),esc(x.supplier),`¥${money(x.total_amount)}`,x.current_node,`<button class="link cApprove" data-id="${x.id}">处理</button>`]))}</div>`;$$('.cApprove').forEach(b=>b.addEventListener('click',()=>openGenericApproval('合同',b.dataset.id,`/api/contracts/${b.dataset.id}/approve`,renderContractApprovals)));}


function openAccountProfile() {
  const u = state.currentUserData || {};
  showModal(`<h3 id="modalTitle">账号信息</h3>
    <div class="profile-card"><div class="profile-avatar">${esc((u.display_name||u.username||'U').slice(0,1))}</div><div><strong>${esc(u.display_name||'—')}</strong><p>${esc(u.username||'—')} · ${esc(u.role_label||'—')}</p></div></div>
    <div class="detail-grid"><div><span>所属部门</span><b>${esc(u.department||'—')}</b></div><div><span>邮箱</span><b>${esc(u.email||'—')}</b></div><div><span>手机</span><b>${esc(u.phone||'—')}</b></div><div><span>账号状态</span><b>正常</b></div></div>
    <div class="modal-actions">${btn('关闭','btn primary','profileClose')}</div>`);
  $('#profileClose').addEventListener('click', closeModal);
}

function openChangePassword() {
  showModal(`<h3 id="modalTitle">修改密码</h3><div class="form-stack">
    <div><label>原密码</label><input id="oldPwd" class="field" type="password" autocomplete="current-password"></div>
    <div><label>新密码</label><input id="newPwd" class="field" type="password" autocomplete="new-password" placeholder="至少8位"></div>
    <div><label>确认新密码</label><input id="confirmPwd" class="field" type="password" autocomplete="new-password"></div>
    </div><div class="modal-actions">${btn('取消','btn','pwdCancel')}${btn('确认修改','btn primary','pwdSave')}</div>`);
  $('#pwdCancel').addEventListener('click', closeModal);
  $('#pwdSave').addEventListener('click', async () => {
    const next = $('#newPwd').value;
    if (next.length < 8) return toast('新密码至少8位','error');
    if (next !== $('#confirmPwd').value) return toast('两次输入的新密码不一致','error');
    try { const r = await api('/api/auth/change-password',{method:'POST',body:JSON.stringify({old_password:$('#oldPwd').value,new_password:next})}); closeModal(); toast(r.message,'success'); }
    catch(e){ toast(e.message,'error'); }
  });
}

async function renderUsers(){
  setPage({title:'用户管理',iconName:'user',crumbs:['系统管理'],actions:btn(`${icon('plus')} 新增用户`,'btn primary','userNew')});
  const [usersResp,rolesResp]=await Promise.all([api('/api/system/users'),api('/api/system/roles')]);
  const users=usersResp.data,roles=rolesResp.data.filter(r=>r.status==='启用');
  appView.innerHTML=`<div class="section"><div class="toolbar"><div class="filters"><input id="userSearch" class="field" style="max-width:300px" placeholder="账号 / 姓名 / 部门"><select id="userRoleFilter" class="select" style="max-width:190px"><option value="">全部角色</option>${roles.map(r=>`<option value="${esc(r.code)}">${esc(r.label)}</option>`).join('')}</select><select id="userStatusFilter" class="select" style="max-width:140px"><option value="">全部状态</option><option>启用</option><option>停用</option></select></div><div class="subtle">共 ${users.length} 个账号</div></div>
  <div class="table-wrap"><table class="table"><thead><tr><th>账号</th><th>姓名</th><th>部门</th><th>角色</th><th>联系方式</th><th>状态</th><th>最近登录</th><th>操作</th></tr></thead><tbody id="userRows"></tbody></table></div></div>`;
  const draw=()=>{const q=$('#userSearch').value.trim().toLowerCase(),role=$('#userRoleFilter').value,st=$('#userStatusFilter').value;const rows=users.filter(u=>(!q||`${u.username} ${u.display_name} ${u.department}`.toLowerCase().includes(q))&&(!role||u.role_code===role)&&(!st||u.status===st));$('#userRows').innerHTML=rows.map(u=>`<tr><td><span class="detail-no">${esc(u.username)}</span></td><td>${esc(u.display_name)}</td><td>${esc(u.department||'—')}</td><td>${esc(u.role_label)}</td><td><div>${esc(u.email||'—')}</div><div class="subtle">${esc(u.phone||'')}</div></td><td>${statusPill(u.status)}</td><td>${esc(u.last_login||'从未登录')}</td><td><div class="action-group"><button class="link userEdit" data-id="${u.id}">编辑</button><button class="link userReset" data-id="${u.id}">重置密码</button></div></td></tr>`).join('')||'<tr><td colspan="8" class="empty">暂无匹配账号</td></tr>';$$('.userEdit').forEach(b=>b.addEventListener('click',()=>openUserForm(users.find(u=>u.id===Number(b.dataset.id)),roles)));$$('.userReset').forEach(b=>b.addEventListener('click',async()=>{if(!confirm('确认将该账号密码重置为系统演示初始密码？'))return;try{const r=await api(`/api/system/users/${b.dataset.id}/reset-password`,{method:'POST'});toast(r.message,'success');}catch(e){toast(e.message,'error')}}));};
  ['userSearch','userRoleFilter','userStatusFilter'].forEach(id=>$('#'+id).addEventListener(id==='userSearch'?'input':'change',draw));$('#userNew').addEventListener('click',()=>openUserForm(null,roles));draw();
}

function openUserForm(item,roles){
  const isEdit=Boolean(item);
  showModal(`<h3 id="modalTitle">${isEdit?'编辑用户':'新增用户'}</h3><div class="grid-2"><div><label>登录账号 *</label><input id="uUsername" class="field" value="${esc(item?.username||'')}" ${item?.username==='admin'?'disabled':''}></div><div><label>姓名 *</label><input id="uDisplay" class="field" value="${esc(item?.display_name||'')}"></div></div><div class="grid-2" style="margin-top:10px"><div><label>所属部门</label><input id="uDept" class="field" value="${esc(item?.department||'')}"></div><div><label>角色 *</label><select id="uRole" class="select">${roles.map(r=>`<option value="${esc(r.code)}" ${r.code===item?.role_code?'selected':''}>${esc(r.label)}</option>`).join('')}</select></div></div><div class="grid-2" style="margin-top:10px"><div><label>邮箱</label><input id="uEmail" class="field" value="${esc(item?.email||'')}"></div><div><label>手机</label><input id="uPhone" class="field" value="${esc(item?.phone||'')}"></div></div><div class="grid-2" style="margin-top:10px"><div><label>账号状态</label><select id="uStatus" class="select"><option ${item?.status!=='停用'?'selected':''}>启用</option><option ${item?.status==='停用'?'selected':''}>停用</option></select></div><div><label>${isEdit?'新密码（留空不修改）':'初始密码'}</label><input id="uPassword" class="field" type="password" placeholder="${isEdit?'留空保持原密码':'默认 Demo@123'}"></div></div><div class="modal-actions">${btn('取消','btn','uCancel')}${btn('保存','btn primary','uSave')}</div>`);
  $('#uCancel').addEventListener('click',closeModal);$('#uSave').addEventListener('click',async()=>{const payload={username:(item?.username==='admin'?'admin':$('#uUsername').value.trim()),display_name:$('#uDisplay').value.trim(),department:$('#uDept').value,email:$('#uEmail').value,phone:$('#uPhone').value,role_code:$('#uRole').value,status:$('#uStatus').value,password:$('#uPassword').value||null};if(!payload.username||!payload.display_name)return toast('请填写账号和姓名','error');try{const r=await api(isEdit?`/api/system/users/${item.id}`:'/api/system/users',{method:isEdit?'PUT':'POST',body:JSON.stringify(payload)});closeModal();toast(r.message,'success');renderUsers();}catch(e){toast(e.message,'error')}});
}

async function renderRoles(){
  setPage({title:'角色管理',iconName:'settings',crumbs:['系统管理'],actions:btn(`${icon('plus')} 新增角色`,'btn primary','roleNew')});
  const [rolesResp,permResp]=await Promise.all([api('/api/system/roles'),api('/api/system/permissions')]);const roles=rolesResp.data,permissions=permResp.data;
  appView.innerHTML=`<div class="section"><div class="section-title">角色与权限</div><div class="section-subtitle">角色权限决定登录后可见的功能菜单；内置审批角色同时用于需求和立项审批节点匹配。</div><div class="role-card-grid">${roles.map(r=>`<div class="role-card"><div class="toolbar"><div><strong>${esc(r.label)}</strong><div class="subtle">${esc(r.code)}</div></div>${statusPill(r.status)}</div><p>${esc(r.description||'—')}</p><div class="role-card-meta"><span>用户数 <b>${r.user_count}</b></span><span>权限 <b>${r.permissions.includes('*')?'全部':r.permissions.length}</b></span><span>${r.built_in?'系统内置':'自定义'}</span></div><button class="btn roleEdit" data-code="${esc(r.code)}">配置权限</button></div>`).join('')}</div></div>`;
  $$('.roleEdit').forEach(b=>b.addEventListener('click',()=>openRoleForm(roles.find(r=>r.code===b.dataset.code),permissions)));$('#roleNew').addEventListener('click',()=>openRoleForm(null,permissions));
}

function openRoleForm(item,permissions){
  const edit=Boolean(item),selected=new Set(item?.permissions||[]),all=selected.has('*');
  showModal(`<h3 id="modalTitle">${edit?'配置角色':'新增角色'}</h3><div class="grid-2"><div><label>角色编码 *</label><input id="rCode" class="field" value="${esc(item?.code||'')}" ${edit?'disabled':''} placeholder="如 reviewer"></div><div><label>角色名称 *</label><input id="rLabel" class="field" value="${esc(item?.label||'')}"></div></div><div style="margin-top:10px"><label>角色说明</label><textarea id="rDesc" class="textarea">${esc(item?.description||'')}</textarea></div><div class="grid-2" style="margin-top:10px"><div><label>状态</label><select id="rStatus" class="select"><option ${item?.status!=='停用'?'selected':''}>启用</option><option ${item?.status==='停用'?'selected':''}>停用</option></select></div><label class="permission-all"><input id="rAll" type="checkbox" ${all?'checked':''}> 授予全部功能权限</label></div><div class="permission-grid" id="permissionGrid">${Object.entries(permissions).map(([code,label])=>`<label><input type="checkbox" class="permCheck" value="${esc(code)}" ${all||selected.has(code)?'checked':''} ${all?'disabled':''}><span>${esc(label)}</span><small>${esc(code)}</small></label>`).join('')}</div><div class="modal-actions">${btn('取消','btn','rCancel')}${btn('保存','btn primary','rSave')}</div>`,true);
  $('#rAll').addEventListener('change',e=>$$('.permCheck').forEach(c=>{c.disabled=e.target.checked;c.checked=e.target.checked;}));$('#rCancel').addEventListener('click',closeModal);$('#rSave').addEventListener('click',async()=>{const perms=$('#rAll').checked?['*']:$$('.permCheck:checked').map(c=>c.value);const payload={code:edit?item.code:$('#rCode').value.trim(),label:$('#rLabel').value.trim(),description:$('#rDesc').value,permissions:perms,status:$('#rStatus').value};if(!payload.code||!payload.label)return toast('请填写角色编码和名称','error');try{const r=await api(edit?`/api/system/roles/${encodeURIComponent(item.code)}`:'/api/system/roles',{method:edit?'PUT':'POST',body:JSON.stringify(payload)});closeModal();toast(r.message,'success');if(item?.code===state.role){state.currentUserData=(await api('/api/auth/me')).data;updateUserUI();}renderRoles();}catch(e){toast(e.message,'error')}});
}

async function renderIntegrations(){
  setPage({title:'集成配置',iconName:'settings',crumbs:['系统管理']});
  const items=(await api('/api/integrations')).data;
  appView.innerHTML=`<div class="grid-3">${items.map(x=>`<div class="metric"><div class="toolbar"><div><div class="k">${esc(x.code.toUpperCase())}</div><div class="v" style="font-size:17px">${esc(x.name)}</div></div>${statusPill(x.enabled?x.status:'停用')}</div><div class="sub">模式：${esc(x.mode)} · ${x.base_url?esc(x.base_url):'本地适配器'}</div>${x.code==='ai'?`<div class="sub" style="margin-top:6px">智能体：<span class="detail-no">${esc(x.agent_id||'未配置')}</span></div>`:''}${x.code==='mcp'?'<div class="sub" style="margin-top:6px">协议：Streamable HTTP · Bearer鉴权 · 9个工具</div>':''}<div class="sub" style="margin-top:6px">${esc(x.description)}</div><div class="action-group" style="margin-top:12px"><button class="btn intCheck" data-code="${x.code}">连通性检查</button>${state.role==='admin'?`<button class="btn primary intEdit" data-code="${x.code}">配置</button>`:''}</div></div>`).join('')}</div><div class="section" style="margin-top:12px"><div class="section-title">集成运行说明</div><div class="detail-block"><p>AI Live 模式由本系统后端代理调用 Gazellio G.AIOS，浏览器不会接触后台管理员账号。项目360机器人、AI问答页和右下角悬浮助手共用该配置；外部服务异常时保留原有本地规则问答作为降级能力。</p><p>TRM MCP是反向工具通道：G.AIOS智能体通过它查询本系统，并在用户确认后创建项目/需求。服务Token和写开关只能在服务器环境变量中配置，页面不显示也不保存明文。</p></div></div>`;
  $$('.intCheck').forEach(b=>b.addEventListener('click',async()=>{try{const r=await api(`/api/integrations/${b.dataset.code}/check`,{method:'POST'});toast(r.message,'success');renderIntegrations();}catch(e){toast(e.message,'error')}}));
  $$('.intEdit').forEach(b=>b.addEventListener('click',()=>{
    const x=items.find(i=>i.code===b.dataset.code);
    showModal(`<h3 id="modalTitle">配置 ${esc(x.name)}</h3><div class="grid-2"><select id="intMode" class="select"><option ${x.mode==='mock'?'selected':''}>mock</option><option ${x.mode==='live'?'selected':''}>live</option></select><select id="intEnabled" class="select"><option value="1" ${x.enabled?'selected':''}>启用</option><option value="0" ${!x.enabled?'selected':''}>停用</option></select></div><input id="intUrl" class="field" style="margin-top:10px" value="${esc(x.base_url||'')}" placeholder="Live模式服务地址">${x.code==='ai'?`<input id="intAgentId" class="field" style="margin-top:10px" value="${esc(x.agent_id||'')}" placeholder="G.AIOS 智能体公开标识（agent_id）"><div class="help">仅填写公开 agent_id，不要填写后台用户名、密码或管理员 Token。</div>`:''}<div class="modal-actions">${btn('取消','btn','intCancel')}${btn('保存','btn primary','intSave')}</div>`);
    $('#intCancel').addEventListener('click',closeModal);
    $('#intSave').addEventListener('click',async()=>{try{await api(`/api/integrations/${x.code}`,{method:'PUT',body:JSON.stringify({mode:$('#intMode').value,base_url:$('#intUrl').value,agent_id:$('#intAgentId')?.value||'',enabled:$('#intEnabled').value==='1'})});closeModal();toast('集成配置已保存','success');renderIntegrations();}catch(e){toast(e.message,'error')}});
  }));
}


async function renderAudit() {
  setPage({ title: '审计日志', iconName: 'audit', crumbs: ['系统管理'], actions: btn('刷新','btn','auditRefresh') });
  const logs=(await api('/api/audit')).data;
  appView.innerHTML=`<div class="section"><div class="section-title">操作审计</div><div class="section-subtitle">提交、审批、预算维护、接口调用、手动同步等操作均保留操作人、角色、业务对象、结果及requestId。</div><div class="table-wrap"><table class="table"><thead><tr><th>时间</th><th>操作人</th><th>角色</th><th>动作</th><th>对象</th><th>结果</th><th>requestId</th></tr></thead><tbody>${logs.map((log)=>`<tr><td>${esc(log.created_at)}</td><td>${esc(log.actor)}</td><td>${esc(state.meta.roles[log.role]||log.role)}</td><td>${esc(log.action)}</td><td>${esc(log.object_type)} / ${esc(log.object_id||'')}</td><td><span class="status ${statusClass(log.result)}">${esc(log.result)}</span></td><td class="subtle">${esc(log.request_id||'—')}</td></tr>`).join('')}</tbody></table></div></div>`;
  $('#auditRefresh').addEventListener('click',renderAudit);
}

init().catch((error) => {
  console.error('系统初始化失败', error);
  appView.innerHTML = `<div class="callout danger"><strong>系统初始化失败</strong><div style="margin-top:8px">${esc(error.message || '未知错误')}</div><div class="help">请确认FastAPI服务已启动并检查 /api/health。</div></div>`;
});
