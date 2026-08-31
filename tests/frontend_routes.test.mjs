import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';

const appSource = readFileSync(new URL('../app/static/app.js', import.meta.url), 'utf8');
const moduleSource = readFileSync(new URL('../app/static/investment.js', import.meta.url), 'utf8');
// Execute the production routing code, not a second implementation of it.
const routeSource = appSource.slice(appSource.indexOf('async function renderInvestmentRoute('), appSource.indexOf('async function renderDashboard('));
assert.ok(routeSource.includes('async function renderRoute()'));
const investmentRoutes = {
  'investment-board': 'renderInvestmentBoard',
  'investment-plans': 'renderInvestmentPlans',
  'investment-plan-detail/42': 'renderInvestmentPlanDetail',
  'investment-approvals': 'renderInvestmentApprovals',
  'investment-adjustments': 'renderInvestmentAdjustments',
  'investment-execution': 'renderInvestmentExecution',
  'investment-warnings': 'renderInvestmentWarnings',
  'investment-settings': 'renderInvestmentSettings',
};

function harness(path) {
  const calls = [], listeners = {}, errors = [];
  const context = {
    parseRoute: () => ({path, query: {}}),
    document: {body: {classList: {toggle() {}}}},
    canAccessRoute: () => true,
    routeGroup: () => '', updateNav() {}, state: {},
    heroActions: {innerHTML: ''}, appView: {innerHTML: ''},
    setPage() {}, esc: String,
    $: selector => ({addEventListener: (event, callback) => {listeners[`${selector}:${event}`] = callback;}}),
    location: {reload: () => calls.push('reload')},
    console: {error: (...args) => errors.push(args)},
  };
  // Other page bodies aren't under test; retain their names in the real table.
  for (const [, name] of routeSource.matchAll(/:\s*(render\w+)\s*[,}]/g)) {
    context[name] = async () => {calls.push(name); context.appView.innerHTML = 'page rendered';};
  }
  context.window = context;
  vm.createContext(context);
  vm.runInContext(routeSource, context);
  return {context, calls, listeners, errors};
}

test('homepage and existing list still render when investment.js never loads', async () => {
  for (const [path, name] of [['dashboard', 'renderDashboard'], ['demand-list', 'renderDemandList'], ['project-list', 'renderProjectList']]) {
    const {context, calls, errors} = harness(path);
    await context.renderRoute();
    assert.deepEqual(calls, [name]);
    assert.equal(context.appView.innerHTML, 'page rendered');
    assert.deepEqual(errors, []);
  }
});

test('every investment route has a local retry state when its script is missing', async () => {
  for (const path of Object.keys(investmentRoutes)) {
    const {context, calls, listeners, errors} = harness(path);
    await context.renderRoute();
    assert.match(context.appView.innerHTML, /投入管理暂未加载/);
    assert.doesNotMatch(context.appView.innerHTML, /页面加载失败/);
    listeners['#investmentReload:click']();
    assert.deepEqual(calls, ['reload']);
    assert.deepEqual(errors, []);
  }
});

test('loaded classic script defines all investment page functions and dispatches correctly', async () => {
  for (const [path, name] of Object.entries(investmentRoutes)) {
    const {context, errors} = harness(path);
    vm.runInContext(moduleSource, context);
    assert.equal(typeof context[name], 'function', name);
    let actualArgs;
    context[name] = async (...args) => {actualArgs = args; context.appView.innerHTML = name;};
    await context.renderRoute();
    assert.equal(context.appView.innerHTML, name);
    assert.deepEqual(actualArgs, path.includes('/') ? [42] : []);
    assert.deepEqual(errors, []);
  }
});

test('workbench filters combine keyword, year, department and status', () => {
  const context = vm.createContext({});
  vm.runInContext(moduleSource, context);
  const rows = [
    {id:1,plan_year:2026,department:'数字化部',status:'已生效',plan_name:'AI服务'},
    {id:2,plan_year:2027,department:'数字化部',status:'草稿',plan_name:'AI规划'},
    {id:3,plan_year:2026,department:'财务部',status:'已生效',plan_name:'AI核算'},
  ];
  assert.deepEqual(context.invFilterRows(rows,'ai',{plan_year:'2026',department:'数字化部',status:'已生效'}).map(r=>r.id),[1]);
  assert.equal(context.invFilterRows(rows,'不存在',{}).length,0);
  assert.equal(context.invFilterRows(rows,'',{plan_year:'',department:''}).length,3);
});

test('CSV export escapes formulas and quotes while retaining numeric values', () => {
  const context = vm.createContext({});
  vm.runInContext(moduleSource, context);
  assert.equal(context.invCsvCell('=HYPERLINK("example")'), '"\'=HYPERLINK(""example"")"');
  assert.equal(context.invCsvCell('+SUM(1,2)'), '"\'+SUM(1,2)"');
  assert.equal(context.invCsvCell(-100), '"-100"');
  assert.equal(context.invCsvCell('投入,名称'), '"投入,名称"');
});

test('investment markup consistently opts into the shared table layout', () => {
  assert.doesNotMatch(moduleSource, /<table>/);
  assert.match(moduleSource, /class="table inv-table"/);
  const css = readFileSync(new URL('../app/static/investment.css', import.meta.url),'utf8');
  assert.match(css, /\.inv-table\s*\{[^}]*width:\s*100%/);
  assert.match(css, /\.inv-rule-save\s*\{[^}]*grid-column:\s*1\/-1/);
  assert.match(css, /\.investment-mode \.btn[^}]*white-space:\s*nowrap/);
  assert.match(appSource, /classList.toggle\('investment-mode', base.startsWith\('investment-'\)\)/);
});

test('KPI cards appear only on the analysis board, never on workflows or dialogs', () => {
  const context = vm.createContext({});
  vm.runInContext(moduleSource, context);
  assert.equal((moduleSource.match(/invKpis\(/g) || []).length, 2, 'one definition plus one board call');
  assert.match(context.renderInvestmentBoard.toString(), /invKpis\(/);
  for (const name of ['renderInvestmentPlans','renderInvestmentPlanDetail','renderInvestmentApprovals',
    'renderInvestmentAdjustments','renderInvestmentExecution','renderInvestmentWarnings',
    'renderInvestmentSettings','openInvestmentAdjustmentDetail','openInvestmentPaymentHistory']) {
    assert.doesNotMatch(context[name].toString(), /invKpis\(/, name);
  }
  assert.match(context.renderInvestmentApprovals.toString(), /role="tablist"/);
  assert.match(context.renderInvestmentExecution.toString(), /summary:list/);
  assert.match(context.invTable.toString(), /summary\(filtered\)/);
});

test('compact detail facts escape both labels and values', () => {
  const context = vm.createContext({esc:value=>String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;')});
  vm.runInContext(moduleSource, context);
  const html = context.invFacts([['<label>','<script>'],['金额','¥1,000.00']]);
  assert.match(html, /<dl class="inv-facts">/);
  assert.match(html, /&lt;script&gt;/);
  assert.doesNotMatch(html, /<script>|investment-kpi/);
  assert.match(html, /¥1,000.00/);
});
