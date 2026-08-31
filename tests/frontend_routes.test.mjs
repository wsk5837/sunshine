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
