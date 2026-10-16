import { api } from '../api.js';
import { h, fmtGb, fmtNum, fmtTokens } from '../components.js';
import { pageHead, panel, pill, btn } from './_ui.js';
import { capabilityCounts, isKnownNumber, projectRoadmap } from './control_lab_state.js';

const CSS_ID = 'bcc-control-lab-css';
const PAGE = { id: 'control-lab', title: 'Control Lab', icon: 'activity', nav: 'primary', section: 'work' };
const DEMO = {
  system: { metrics: { ts: 'Демо-снимок', cpu_pct: 38, ram_used_mb: 48128, ram_total_mb: 131072, gpu: [{ name: 'Radeon 8060S · пример', utilization_pct: 44, vram_used_mb: 9216, vram_total_mb: 32768 }] } },
  tasks: [{ id: 'DEMO-01', title: 'Пример локальной задачи', status: 'running', last_run: { tokens_in: 12000, tokens_out: 3400 } }],
  agents: [{ id: 'DEMO-A', name: 'Локальный агент · пример', enabled: true }],
  spend: { enabled: true, spent: { today_usd: 0.12 } },
  resources: { measured: true, used_mb: 48128, total_mb: 131072, reserved_mb: 16384, available_mb: 52428 },
  capability: { tree: { nodes: [
    { id: 'demo-1', label: 'Пример: локальные модели', parent: 'demo', status: 'code', detail: 'Демо-строка, не подтверждение работы.' },
    { id: 'demo-2', label: 'Пример: управление задачами', parent: 'demo', status: 'reported', detail: 'Демо-строка, не подтверждение работы.' },
    { id: 'demo-3', label: 'Пример: GUI-контроль', parent: 'demo', status: 'blocked', detail: 'Демо-строка, не подтверждение работы.' },
  ] } },
};
const SOURCES = [
  ['/api/system', 'Ресурсы системы'],
  ['/api/tasks?limit=100', 'Задачи'], ['/api/agents', 'Агенты'],
  ['/api/spend', 'Учёт расходов'], ['/api/resources', 'Планировщик памяти'],
];
const TREE_SOURCE = '/api/capability-tree';
const state = { mode: 'live', tab: 'mission', query: '', branch: '', budget: 0, speed: 1,
  data: null, fetchedAt: null, treeFetchedAt: null, treeLoading: false, treeError: false, errors: [] };

function ensureCss() {
  if (document.getElementById(CSS_ID)) return Promise.resolve();
  return new Promise((resolve) => {
    const link = document.createElement('link'); link.id = CSS_ID; link.rel = 'stylesheet'; link.href = 'control_lab.css';
    link.addEventListener('load', resolve, { once: true }); link.addEventListener('error', resolve, { once: true });
    document.head.appendChild(link);
  });
}

const list = (value, key) => Array.isArray(value) ? value : (Array.isArray(value?.[key]) ? value[key] : []);
async function loadLive() {
  const settled = await Promise.allSettled(SOURCES.map(([url]) => api.raw(url)));
  const data = {}; const errors = [];
  settled.forEach((entry, i) => {
    const key = ['system', 'tasks', 'agents', 'spend', 'resources'][i];
    if (entry.status === 'fulfilled') data[key] = entry.value;
    else { data[key] = null; errors.push(SOURCES[i][0]); }
  });
  state.data = data; state.errors = errors; state.fetchedAt = new Date().toISOString();
}

async function loadTree() {
  state.treeLoading = true; state.treeError = false;
  try {
    const capability = await api.raw(TREE_SOURCE);
    state.data = { ...(state.data || {}), capability };
    state.treeFetchedAt = new Date().toISOString();
  } catch {
    state.data = { ...(state.data || {}), capability: null };
    state.treeError = true;
  } finally { state.treeLoading = false; }
}

function value(value, format = (x) => String(x)) {
  return isKnownNumber(value) ? format(Number(value)) : 'N/A';
}
function statusLabel(status) {
  return ({ working: 'Работающий по карте', reported: 'Исторический отчёт', code: 'Код найден', branch: 'Ветка', prepared: 'Подготовлено', idea: 'Идея', blocked: 'Блокер', recorded: 'Записано', mixed: 'Смешанный статус' })[status] || `N/A · ${status || 'нет статуса'}`;
}
function metric(title, val, source, note = '') {
  return h('article.cl-metric', h('span.cl-label', title), h('strong.cl-value', val),
    h('span.cl-source', source), note ? h('span.cl-note', note) : null);
}
function metricTime(data) {
  const ts = data?.system?.metrics?.ts;
  if (ts) return `Снимок системы: ${String(ts)}`;
  return state.fetchedAt ? `Получено: ${new Date(state.fetchedAt).toLocaleString()}` : 'Время получения: N/A';
}
function sourceNote(endpoint) { return `${endpoint} · ${state.fetchedAt ? new Date(state.fetchedAt).toLocaleTimeString() : 'N/A'}`; }

function missionPanel(data) {
  const system = data.system || {}; const m = system.metrics || {};
  const resources = data.resources || {};
  const tasks = list(data.tasks, 'tasks'); const agents = list(data.agents, 'agents');
  const runs = tasks.map((task) => task.last_run).filter(Boolean);
  const tokenRuns = runs.filter((run) => isKnownNumber(run.tokens_in) || isKnownNumber(run.tokens_out));
  const tokens = tokenRuns.length ? fmtTokens(tokenRuns.reduce((n, run) => n + Number(run.tokens_in || 0) + Number(run.tokens_out || 0), 0)) : 'N/A';
  const spend = data.spend?.enabled === true && isKnownNumber(data.spend?.spent?.today_usd)
    ? `$${fmtNum(Number(data.spend.spent.today_usd), 2)}` : 'N/A';
  const gpu = Array.isArray(m.gpu) ? m.gpu[0] : null;
  const ramMeasured = resources.measured === true || (isKnownNumber(m.ram_used_mb) && isKnownNumber(m.ram_total_mb));
  const taskCounts = tasks.reduce((acc, task) => { const key = String(task.status || 'unknown'); acc[key] = (acc[key] || 0) + 1; return acc; }, {});
  return panel('Live Mission Control · снимок', h('div.cl-grid',
    metric('CPU', value(m.cpu_pct, (n) => `${fmtNum(n, 1)}%`), sourceNote('/api/system'), 'Ryzen AI Max+ 395: N/A, API не сообщает точную модель CPU'),
    metric('RAM', ramMeasured ? `${fmtGb(resources.used_mb ?? m.ram_used_mb)} / ${fmtGb(resources.total_mb ?? m.ram_total_mb)} GB` : 'N/A', sourceNote('/api/resources + /api/system')),
    metric('GPU', gpu?.name || 'N/A', sourceNote('/api/system'), isKnownNumber(gpu?.utilization_pct) ? `${fmtNum(Number(gpu.utilization_pct), 0)}%` : 'Загрузка: N/A'),
    metric('Агенты', data.agents ? String(agents.filter((agent) => agent.enabled !== false && agent.enabled !== 0).length) : 'N/A', sourceNote('/api/agents')),
    metric('Задачи в выборке', data.tasks ? String(tasks.length) : 'N/A', sourceNote('/api/tasks?limit=100'), Object.entries(taskCounts).map(([k, v]) => `${k}: ${v}`).join(' · ') || 'N/A'),
    metric('Токены · выборка задач', tokens, sourceNote('/api/tasks?limit=100'), 'Сумма только по видимым last_run'),
    metric('Расход сегодня', spend, sourceNote('/api/spend'), data.spend?.enabled === true ? '' : 'Учёт не включён или показатель отсутствует'),
    metric('Миссия/агентные токены и сумма', 'N/A', 'Нет отдельного подтверждённого счётчика'),
  ), { icon: 'activity', aside: pill('Только чтение', { tone: 'ok' }) });
}

function descendants(nodes, rootId) {
  const byParent = new Map();
  nodes.forEach((node) => { const parent = String(node.parent || ''); const rows = byParent.get(parent) || []; rows.push(node); byParent.set(parent, rows); });
  const out = []; const seen = new Set(); const stack = [...(byParent.get(rootId) || [])];
  for (let cursor = 0; cursor < stack.length; cursor += 1) {
    const node = stack[cursor]; if (seen.has(node.id)) continue;
    seen.add(node.id); out.push(node); stack.push(...(byParent.get(String(node.id)) || []));
  }
  return out;
}

function treePanel(data) {
  if (state.mode === 'live' && state.treeLoading) return panel('Capability Tree · реестр и evidence', h('p.cl-note', 'Загружаю реестр из owner API…'), { icon: 'agents' });
  if (state.mode === 'live' && state.treeError) return panel('Capability Tree · реестр и evidence', h('p.cl-warning', 'Реестр недоступен: значения показаны как N/A. Нажмите «Обновить снимок», чтобы повторить запрос.'), { icon: 'agents' });
  const nodes = list(data.capability?.tree?.nodes, 'nodes');
  const roots = nodes.filter((node) => node.parent === 'bossman');
  const query = state.query.trim().toLowerCase();
  const scope = state.branch ? descendants(nodes, state.branch) : nodes;
  const filtered = scope.filter((node) => !query || `${node.label} ${node.detail} ${node.id}`.toLowerCase().includes(query));
  const shown = filtered.slice(0, 60);
  const counts = capabilityCounts(nodes);
  const branchSelect = h('select.cl-select', { 'aria-label': 'Ветка реестра', value: state.branch, onChange: (event) => { state.branch = event.target.value; rerender(); } },
    h('option', { value: '' }, 'Все возможности'), ...roots.map((root) => h('option', { value: root.id }, root.label)));
  const search = h('input.cl-search', { type: 'search', value: state.query, placeholder: 'Найти функцию', 'aria-label': 'Найти функцию', onInput: (event) => { state.query = event.target.value; } });
  const rows = shown.map((node) => {
    const sources = Array.isArray(node.sources) ? node.sources : [];
    return h('article.cl-tree-row', h('div.cl-tree-title', h('strong', node.label || node.id), pill(statusLabel(node.status), { tone: node.status === 'blocked' ? 'err' : node.status === 'code' ? 'info' : 'idle' })),
      h('p', node.detail || 'Описание: N/A'),
      h('small', sources.length ? `Источники: ${sources.slice(0, 2).map((source) => `${source.path || 'N/A'} · ${source.sha || 'SHA N/A'}`).join(' | ')}` : 'Источник: N/A'));
  });
  return panel('Capability Tree · реестр и evidence', h('div.stack sm cl-stack',
    h('p.cl-disclaimer', 'Это карта возможностей, не сертификат готовности. Статусы реестра — исходные отметки; код или исторический отчёт сами по себе не подтверждают PASS.'),
    h('div.cl-toolbar', branchSelect, search, btn('Найти', () => rerender(), { variant: 'secondary' })),
    h('div.cl-counts', ...Object.entries(counts).map(([status, count]) => pill(`${statusLabel(status)} · ${count}`, { tone: status === 'blocked' ? 'err' : 'idle' }))),
    h('p.cl-note', `${filtered.length} совпадений · показано ${shown.length} из максимум 60. `
      + (data.capability ? `${TREE_SOURCE} · получено ${state.treeFetchedAt ? new Date(state.treeFetchedAt).toLocaleTimeString() : 'N/A'} · реестр ${data.capability.tree?.as_of || 'N/A'}` : 'Источник: N/A')),
    h('div.cl-tree-list', ...rows)), { icon: 'agents' });
}

function roadmapPanel() {
  const scenarios = projectRoadmap({ now: Date.now(), budget: state.budget, speed: state.speed });
  const budget = h('input.cl-range', { type: 'range', min: 0, max: 5000, step: 50, value: state.budget, 'aria-label': 'Плановый бюджет в долларах', onChange: (event) => { state.budget = Number(event.target.value); rerender(); } });
  const speed = h('input.cl-range', { type: 'range', min: 0.1, max: 10, step: 0.1, value: state.speed, 'aria-label': 'Плановая скорость единиц работы в день', onChange: (event) => { state.speed = Number(event.target.value); rerender(); } });
  const rows = scenarios.map((scenario) => h('article.cl-scenario', h('h3', scenario.label),
    h('p', `${scenario.workUnits} условных единиц работы до 01.01.2027`),
    h('p.cl-note', `Плановый потолок: $${fmtNum(scenario.spendCap, 0)} · множитель скорости ${scenario.factor}× · ${scenario.budgetBound ? 'ограничено бюджетом' : 'ограничено временем/скоростью'}`)));
  return panel('Roadmap Simulator · до 01.01.2027', h('div.stack sm cl-stack',
    h('p.cl-disclaimer', 'Сценарная арифметика, не прогноз выручки и не запуск операций. Ползунки ничего не покупают, не создают задач и не меняют настройки Bossman.'),
    h('label.cl-control', `Плановый бюджет · $${fmtNum(state.budget, 0)}`, budget),
    h('label.cl-control', `Плановая скорость · ${fmtNum(state.speed, 1)} ед./день`, speed),
    h('p.cl-note', 'Явное допущение симулятора: $2 на условную единицу работы; бюджеты сценариев используют 50%, 75% и 100% заданного потолка.'),
    h('div.cl-scenarios', ...rows)), { icon: 'activity' });
}

function rerender() {
  const root = document.querySelector('#view [data-page="control-lab"]');
  if (root) root.replaceWith(renderBody());
}
function renderBody() {
  const data = state.mode === 'demo' ? DEMO : (state.data || {});
  const root = h('div.bx-page.cl-page', { 'data-page': 'control-lab' },
    pageHead('Control Lab', 'Единый обзор функций, исполнения и плановых сценариев. Источники подписаны, отсутствие данных показывается как N/A.', {
      pills: [pill(state.mode === 'demo' ? 'DEMO · синтетика' : 'LIVE · API чтение', { tone: state.mode === 'demo' ? 'warn' : 'info' })],
      actions: [btn('Обновить снимок', async () => {
        await loadLive();
        if (state.tab === 'tree' || state.data?.capability) await loadTree();
        rerender();
      }, { variant: 'secondary' })],
    }),
    h('div.cl-mode', h('button.cl-mode-button', { type: 'button', 'aria-pressed': String(state.mode === 'live'), onClick: async () => {
      state.mode = 'live';
      if (!state.data || !state.fetchedAt || Date.now() - Date.parse(state.fetchedAt) > 30000) await loadLive();
      if (state.tab === 'tree' && !state.data?.capability) await loadTree();
      rerender();
    } }, 'LIVE'),
      h('button.cl-mode-button', { type: 'button', 'aria-pressed': String(state.mode === 'demo'), onClick: () => { state.mode = 'demo'; rerender(); } }, 'DEMO')),
    state.mode === 'demo' ? h('p.cl-warning', 'DEMO: все показатели ниже синтетические примеры и не описывают эту машину.') : null,
    state.mode === 'live' ? h('p.cl-note', metricTime(data)) : null,
    state.mode === 'live' && state.errors.length ? h('p.cl-warning', `Нет ответа от источников: ${state.errors.join(', ')}. Эти поля показаны как N/A.`) : null,
    h('nav.cl-tabs', h('button.cl-tab', { type: 'button', 'aria-pressed': String(state.tab === 'mission'), onClick: () => { state.tab = 'mission'; rerender(); } }, 'Mission Control'),
      h('button.cl-tab', { type: 'button', 'aria-pressed': String(state.tab === 'tree'), onClick: () => {
        state.tab = 'tree';
        if (state.mode === 'live' && !state.data?.capability && !state.treeLoading) loadTree().then(rerender);
        rerender();
      } }, 'Capability Tree'),
      h('button.cl-tab', { type: 'button', 'aria-pressed': String(state.tab === 'roadmap'), onClick: () => { state.tab = 'roadmap'; rerender(); } }, 'Roadmap Simulator')),
    state.tab === 'mission' ? missionPanel(data) : state.tab === 'tree' ? treePanel(data) : roadmapPanel());
  return root;
}

const Page = {
  ...PAGE,
  async render() {
    await ensureCss();
    if (state.mode === 'live' && (!state.data || !state.fetchedAt || Date.now() - Date.parse(state.fetchedAt) > 30000)) await loadLive();
    if (state.mode === 'live' && state.tab === 'tree' && !state.data?.capability && !state.treeLoading) await loadTree();
    return renderBody();
  },
  onEvent() { return false; },
};
export default Page;
