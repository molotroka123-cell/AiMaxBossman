/* «/» menu, context gauge, /compact, /memory, cloud.ask approval card (owner 21.10).
   Facts for the gate: new test file, no importers; uses the shared jsdom harness _boot.mjs against the REAL index.html
   and app.js with scripted /m/api answers in the BCC contract shape. Owner rule 21.10 verbatim: «Запрещено читать
   локальные переписки данные и фото» -> only synthetic data here. NOT tested: a real iPhone, the real keyboard,
   visualViewport on a device. */
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { boot, skip, until, sleep, closeAll, UI } from './_boot.mjs';

after(closeAll);
const PALETTE = {
  commands: [
    { name: 'code', usage: '/code <задача>', help: 'Режим кода', kind: 'message' },
    { name: 'pc', usage: '/pc', help: 'Управление ПК', kind: 'message' },
    { name: 'auto', usage: '/auto', help: 'Авто-режим', kind: 'message' },
    { name: 'improve', usage: '/improve', help: 'Самоулучшение', kind: 'message' },
    { name: 'context', usage: '/context', help: 'Показать контекст', kind: 'action' },
    { name: 'compact', usage: '/compact', help: 'Сжать контекст', kind: 'action' },
    { name: 'memory', usage: '/memory', help: 'Резюме в общую память', kind: 'action' },
    { name: 'tools', usage: '/tools', help: 'Список tools', kind: 'action' },
    { name: 'skills', usage: '/skills', help: 'Список skills', kind: 'action' },
    { name: 'models', usage: '/models', help: 'Модели', kind: 'action' },
    { name: 'status', usage: '/status', help: 'Статус', kind: 'action' },
    { name: 'stop', usage: '/stop', help: 'Остановить', kind: 'action' },
    { name: 'diff', usage: '/diff', help: 'Git diff', kind: 'action' },
    { name: 'undo', usage: '/undo', help: 'Отменить', kind: 'action' },
    { name: 'cloud', usage: '/cloud <вопрос>', help: 'Спросить облако', kind: 'action' },
    { name: 'new', usage: '/new', help: 'Новый чат', kind: 'client' },
    { name: 'clear', usage: '/clear', help: 'Очистить', kind: 'client' },
    { name: 'help', usage: '/help', help: 'Справка', kind: 'client' },
  ],
  tools: [
    { name: 'computer.observe', group: 'Computer', description: 'Снимок экрана', risk: 'low', needs_approval: false, effect: 'allow' },
    { name: 'terminal.run', group: 'Terminal', description: 'Команда в терминале', risk: 'high', needs_approval: true, effect: 'ask' },
    { name: 'admin.wipe', group: 'Admin', description: 'Запрещено', risk: 'critical', needs_approval: false, effect: 'deny' },
    { name: 'cloud.ask', group: 'Cloud', description: 'Спросить облачную модель', risk: 'high', needs_approval: true, effect: 'ask' },
  ],
  skills: [{ id: 'pytest-fix', title: 'Починить тесты', status: 'green', triggers: ['pytest'] }],
  agent: { name: 'Gemma', mode: 'AGENT' },
};
const ctx = (o = {}) => ({ session_id: 't1', turns_total: 12, turns_in_context: 9, compacted: false, compacted_at_turn: null, summary_chars: 0,
  used_tokens: 6400, context_window: 8192, used_pct: 78.1, left_tokens: 1792, left_pct: 21.9, level: 'warn', source: 'provider_usage',
  compact: { state: 'idle', task_id: null, finished_at: null, error: null }, ...o });
const AGENTS = { items: [{ id: 7, name: 'Gemma', tools: [], model: { alias: 'gemma' } }], default_agent_id: 7 };

function mk(extra = {}) {
  const st = { ctx: ctx(), palette: PALETTE, paletteFail: false, compact: null, memory: null, ...extra };
  const routes = (p, m, body) => {
    if (p === '/agents') return AGENTS;
    if (p === '/threads/t1') return { id: 't1', turns: [] };
    if (p.startsWith('/agent/palette')) return st.paletteFail ? new Error('offline') : st.palette;
    if (p === '/agent/sessions/t1/context') return typeof st.ctx === 'function' ? st.ctx() : st.ctx;
    if (p === '/agent/sessions/t1/compact' && m === 'POST') return st.compact || { __status: 202, body: { session_id: 't1', task_id: 'c1', status: 'running' } };
    if (p === '/agent/sessions/t1/memory' && m === 'POST') return st.memory || { ok: true, note_id: 'm1', chars: 1800 };
    if (p === '/agent/sessions/t1/stop') return { ok: true, stopped_tasks: [5], killed_terminals: [] };
    if (p === '/agent/models') return { tiers: [{ tier: 1, model: 'gemma', ready: true }], workers: [], runpod_policy: 'DENY_X' };
    if (p === '/terminal/diff') return { cwd: '/repo', status: '## main', diff: '' };
    if (p === '/status') return { bossman: { up: true }, ollama: { up: true, loaded: [] }, approvals_pending: 0, local_agents: 1 };
    if (p === '/threads' && m === 'POST') return { id: 't1' };
    if (p === '/threads/t1/send') return { task: { id: 5 }, model: { alias: 'gemma' } };
    if (p === '/agent/sessions/t1/messages' && m === 'POST') return { task: { id: 5 }, model: { alias: 'gemma' }, mode: body && body.mode };
    if (p === '/tasks/5') return { id: 5, status: 'running' };
    if (p.startsWith('/approvals/') && m === 'POST') return { id: 9, status: 'approved' };
    return undefined;
  };
  return { st, routes };
}
async function app(extra, opts = {}) {
  const m = mk(extra);
  const a = await boot({ routes: m.routes, css: true, before: (w) => { w.localStorage.setItem('bm.thread', 't1'); if (opts.before) opts.before(w); } });
  await until(() => a.calls.some((c) => c.path === '/agent/sessions/t1/context'), 'context fetched on open');
  return { ...a, st: m.st };
}
const type = (a, v) => { a.$('input').value = v; a.$('input').setSelectionRange(v.length, v.length); a.$('input').dispatchEvent(new a.w.Event('input')); };
const key = (a, k) => { const e = new a.w.KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true }); a.$('input').dispatchEvent(e); return e; };
const rows = (a) => [...a.d.querySelectorAll('#slash-list [role="option"]')];
const names = (a) => rows(a).map((r) => r.querySelector('.sl-name').textContent);
const openMenu = async (a, v = '/') => { type(a, v); await until(() => rows(a).length > 0, 'menu rows'); };
const cards = (a) => [...a.d.querySelectorAll('#messages .syscard')];

test('typing "/" opens grouped listbox; ARIA combobox state; lazily fetched once and cached', { skip }, async () => {
  const a = await app();
  assert.equal(a.calls.filter((c) => c.path.startsWith('/agent/palette')).length, 0, 'not fetched before the first "/"');
  await openMenu(a);
  assert.equal(a.$('input').getAttribute('aria-expanded'), 'true');
  assert.equal(a.$('slash-list').getAttribute('role'), 'listbox');
  assert.deepEqual([...a.d.querySelectorAll('.sl-h')].map((h) => h.textContent), ['Команды', 'Tools', 'Skills']);
  assert.equal(rows(a).length, 18 + 4 + 1);
  assert.ok(a.calls.find((c) => c.path === '/agent/palette?session_id=t1'));
  key(a, 'Escape'); assert.equal(a.$('slash-menu').hidden, true); assert.equal(a.$('input').getAttribute('aria-expanded'), 'false');
  await openMenu(a);
  assert.equal(a.calls.filter((c) => c.path.startsWith('/agent/palette')).length, 1, 'cached for 60 s');
});

test('filter, keyboard and risk badges (SAFE / ASK / DENY as text)', { skip }, async () => {
  const a = await app();
  await openMenu(a, '/term');
  assert.deepEqual(names(a), ['terminal.run']);
  const badge = (n) => rows(a).find((r) => r.textContent.includes(n)).querySelector('.sl-badge').textContent;
  type(a, '/'); await until(() => rows(a).length > 20, 'all rows');
  assert.equal(badge('computer.observe'), 'SAFE'); assert.equal(badge('terminal.run'), 'ASK'); assert.equal(badge('admin.wipe'), 'DENY'); assert.equal(badge('cloud.ask'), '☁ ASK');
  assert.equal(a.$('input').getAttribute('aria-activedescendant'), null, '"/" alone highlights nothing');
  key(a, 'ArrowDown'); assert.equal(a.$('input').getAttribute('aria-activedescendant'), 'slash-opt-0');
  key(a, 'ArrowUp'); assert.equal(a.$('input').getAttribute('aria-activedescendant'), `slash-opt-${rows(a).length - 1}`, 'wraps');
  type(a, '/zzzz'); assert.equal(rows(a).length, 0); assert.ok(a.$('slash-status').textContent.includes('Нет совпадений'));
  type(a, 'abc /code'); assert.equal(a.$('slash-menu').hidden, true, 'only at the start of a line');
  type(a, 'abc\n/co'); await until(() => rows(a).length > 0, 'after newline'); assert.ok(names(a).includes('/code'));
});

test('select: message command inserts "/cmd ", tool and skill insert prompts, draft is kept', { skip }, async () => {
  const a = await app();
  await openMenu(a, '/code'); key(a, 'Enter');
  assert.equal(a.$('input').value, '/code '); assert.equal(a.w.localStorage.getItem('bm.draft'), '/code ');
  assert.equal(a.$('slash-menu').hidden, true);
  await openMenu(a, '/computer.obs'); key(a, 'Tab');
  assert.equal(a.$('input').value, 'Используй tool computer.observe: ');
  await openMenu(a, '/pytest'); rows(a)[0].dispatchEvent(new a.w.MouseEvent('click', { bubbles: true }));
  assert.equal(a.$('input').value, 'Применяй навык pytest-fix: ');
  assert.equal(a.calls.filter((c) => c.path.includes('/send')).length, 0, 'selecting never sends');
});

test('every palette command is exercised once; action/client run, message and unmapped ones insert', { skip }, async () => {
  const seen = [];
  for (const c of PALETTE.commands) {
    const a = await app();
    await openMenu(a, '/' + c.name);
    const before = a.calls.length;
    rows(a).find((r) => r.querySelector('.sl-name').textContent === '/' + c.name).dispatchEvent(new a.w.MouseEvent('click', { bubbles: true }));
    seen.push(c.name);
    if (['message', 'undo', 'cloud'].includes(c.kind) || c.name === 'undo' || c.name === 'cloud') {
      assert.equal(a.$('input').value, `/${c.name} `, c.name);
      continue;
    }
    assert.equal(a.$('input').value, '', `${c.name} leaves the composer clean`);
    if (c.name === 'new') { assert.equal(a.w.localStorage.getItem('bm.thread'), null); continue; }
    if (c.name === 'clear') { await until(() => cards(a).length === 1, 'clear card'); continue; }
    if (c.name === 'memory') { await until(() => !a.$('mem-sheet').hidden, 'memory confirm'); assert.ok(!a.calls.slice(before).some((x) => x.path.endsWith('/memory')), 'confirm first'); continue; }
    await until(() => cards(a).length === 1 && !/Загружаю|Запускаю/.test(cards(a)[0].textContent), `${c.name} card`);
    if (c.name === 'compact') assert.ok(a.calls.some((x) => x.path === '/agent/sessions/t1/compact' && x.method === 'POST' && x.csrf));
    if (c.name === 'stop') assert.ok(cards(a)[0].textContent.includes('задач 1'));
    if (c.name === 'tools') assert.ok(cards(a)[0].textContent.includes('cloud.ask') && cards(a)[0].textContent.includes('DENY'));
  }
  assert.deepEqual(seen, PALETTE.commands.map((c) => c.name));
});

test('offline palette shows an error with «Повторить» and the local commands', { skip }, async () => {
  const a = await app({ paletteFail: true });
  type(a, '/'); await until(() => a.$('slash-status').textContent.includes('Не загрузилось'), 'error');
  assert.deepEqual(names(a), ['/new', '/clear', '/help', '/updates']);
  a.st.paletteFail = false; a.$('slash-status').querySelector('button').click();
  await until(() => rows(a).length > 20, 'retry loads the list');
});

test('gauge: levels, text, progressbar semantics, unknown window', { skip }, async () => {
  const a = await app();
  const g = () => a.$('ctx-gauge').dataset.level;
  await until(() => a.$('ctx-text').textContent.includes('78%'), 'gauge text');
  assert.equal(a.$('ctx-text').textContent, 'Контекст · 78% · осталось ≈1 792 из 8 192 токенов');
  assert.equal(g(), 'warn'); assert.equal(a.$('ctx-bar').getAttribute('aria-valuenow'), '78'); assert.equal(a.$('ctx-bar').getAttribute('role'), 'progressbar');
  assert.ok(a.$('ctx-level').textContent.includes('внимание'), 'level is text, not colour only');
  a.st.ctx = ctx({ used_pct: 12, level: 'ok', left_tokens: 7000 }); a.$('ctx-refresh') && (a.$('ctx-open').click(), a.$('ctx-refresh').click());
  await until(() => g() === 'ok', 'ok level');
  a.st.ctx = ctx({ used_pct: 96, level: 'critical' }); a.$('ctx-refresh').click(); await until(() => g() === 'critical', 'critical level');
  a.st.ctx = ctx({ context_window: null, used_pct: null, left_tokens: null, left_pct: null, level: 'unknown' }); a.$('ctx-refresh').click();
  await until(() => g() === 'unknown', 'unknown');
  assert.ok(a.$('ctx-text').textContent.includes('окно модели неизвестно')); assert.equal(a.$('ctx-bar').getAttribute('aria-valuenow'), null);
});

test('compact: success shows before→after; failure says the chat is unchanged; running polls', { skip }, async () => {
  let n = 0;
  const a = await app({ ctx: () => (++n < 3 ? ctx() : n < 5 ? ctx({ compact: { state: 'running', task_id: 'c1' } }) : ctx({ used_pct: 31, used_tokens: 2540, left_tokens: 5652, level: 'ok', turns_in_context: 3, compacted: true, compacted_at_turn: 9, summary_chars: 1800, compact: { state: 'done', task_id: 'c1', finished_at: 'x' } })) });
  await openMenu(a, '/compact'); key(a, 'Enter');
  await until(() => cards(a).some((c) => c.textContent.includes('Контекст сжат')), 'success card', 12000);
  const t = cards(a).find((c) => c.textContent.includes('Контекст сжат')).textContent;
  assert.ok(t.includes('78%') && t.includes('31%') && t.includes('9 → 3'), t);
  const b = await app({ ctx: () => ctx({ compact: { state: 'failed', task_id: 'c1', error: 'модель не ответила' } }) });
  b.st.compact = { __status: 202, body: { session_id: 't1', task_id: 'c1', status: 'running' } };
  type(b, '/compact'); await until(() => rows(b).length > 0, 'rows'); key(b, 'Enter');
  await until(() => cards(b).some((c) => c.textContent.includes('Чат не изменён')), 'failure card', 12000);
  assert.ok(cards(b).at(-1).textContent.includes('модель не ответила'));
  const c = await app(); c.st.compact = { __status: 409, body: { detail: { code: 'AGENT_NOTHING_TO_COMPACT', message: 'x' } } };
  type(c, '/compact'); await until(() => rows(c).length > 0, 'rows'); key(c, 'Enter');
  await until(() => cards(c).some((x) => x.textContent.includes('Сжимать пока нечего')), 'nothing to compact');
});

test('memory: explicit confirm sheet first; only then POST; 409 shows the reason', { skip }, async () => {
  const a = await app();
  a.$('ctx-open').click(); a.$('ctx-memory').click();
  assert.equal(a.$('mem-sheet').hidden, false);
  assert.ok(a.$('mem-title').textContent.includes('Записать резюме этого чата в общую память Bossman?'));
  assert.ok(a.$('mem-text').textContent.includes('не сами сообщения'));
  assert.equal(a.calls.filter((c) => c.path.endsWith('/memory')).length, 0);
  a.$('mem-no').click(); assert.equal(a.$('mem-sheet').hidden, true); assert.equal(a.calls.filter((c) => c.path.endsWith('/memory')).length, 0);
  a.$('ctx-open').click(); a.$('ctx-memory').click(); a.$('mem-note').value = 'про заказ'; a.$('mem-yes').click();
  await until(() => a.calls.some((c) => c.path.endsWith('/memory')), 'memory post');
  assert.deepEqual(a.calls.find((c) => c.path.endsWith('/memory')).body, { note: 'про заказ' });
  await until(() => cards(a).some((c) => c.textContent.includes('записано')), 'result card');
  const b = await app({ memory: { __status: 409, body: { detail: { code: 'AGENT_NO_SUMMARY', message: 'x' } } } });
  b.$('ctx-open').click(); b.$('ctx-memory').click(); b.$('mem-yes').click();
  await until(() => b.$('mem-error').textContent.includes('Резюме этого чата ещё нет'), '409 reason'); assert.equal(b.$('mem-sheet').hidden, false);
});

test('85% hint appears once per chat', { skip }, async () => {
  const a = await app({ ctx: ctx({ used_pct: 90, level: 'critical' }) });
  await until(() => a.d.querySelectorAll('.hint-card').length === 1, 'hint');
  assert.ok(a.d.querySelector('.hint-card').textContent.includes('Контекст почти заполнен — сжать?'));
  a.$('ctx-open').click(); a.$('ctx-refresh').click(); await sleep(150);
  assert.equal(a.d.querySelectorAll('.hint-card').length, 1);
});

test('cloud.ask approval: heading, model, exact text in a scroll block, owner taps only (also in AUTO)', { skip }, async () => {
  const es = [];
  const a = await app({}, { before: (w) => { w.EventSource = class { constructor(u) { this.url = u; es.push(this); } close() {} }; w.localStorage.setItem('bm.agentMode', 'AUTO'); } });
  const posts = () => a.calls.filter((c) => c.path.startsWith('/approvals/'));
  const text = '<b>Вопрос</b> про заказ №5';
  const preview = `cloud.ask
Модель: anthropic/claude-x
412 симв. · стоимость ≈ $0.0012
---
${text}`;
  type(a, 'привет'); a.$('composer').requestSubmit();
  await until(() => es.length > 0, 'stream opened');
  es[0].onmessage({ data: JSON.stringify({ kind: 'approval.created', id: 9, approval_kind: 'tool', preview }) });
  const box = a.d.querySelector('.approval.cloud'); assert.ok(box, 'cloud card');
  assert.equal(box.querySelector('h4').textContent, '☁️ Отправить в облако?');
  assert.ok(box.querySelector('.cloud-meta').textContent.includes('anthropic/claude-x'));
  const pre = box.querySelector('pre.cloud-text'); assert.ok(pre.textContent.includes(text), 'exact text, as text'); assert.equal(pre.querySelector('b'), null, 'no markup from the text');
  assert.equal(pre.getAttribute('tabindex'), '0');
  await sleep(100); assert.equal(posts().length, 0, 'AUTO does not approve cloud.ask');
  [...box.querySelectorAll('button')].find((x) => x.textContent === 'Разрешить один раз').click();
  await until(() => posts().length === 1, 'owner tap decides'); assert.deepEqual(posts()[0].body, { approve: true });
  es[0].onmessage({ data: JSON.stringify({ kind: 'approval.created', id: 10, approval_kind: 'tool', preview: 'terminal.run git status' }) });
  await until(() => posts().length === 2, 'AUTO still auto-approves ordinary tools'); assert.equal(posts()[1].path, '/approvals/10');
});

test('«Не спрашивать в этом чате»: same operation is auto-decided in this chat; destructive asks always wait', { skip }, async () => {
  const es = [];
  const a = await app({}, { before: (w) => { w.EventSource = class { constructor(u) { this.url = u; es.push(this); } close() {} }; w.localStorage.setItem('bm.agentMode', 'CHAT'); } });
  const posts = () => a.calls.filter((c) => c.path.startsWith('/approvals/'));
  type(a, 'привет'); a.$('composer').requestSubmit();
  await until(() => es.length > 0, 'stream opened');
  const ask = (id, preview) => es[0].onmessage({ data: JSON.stringify({ kind: 'approval.created', id, approval_kind: 'tool', preview }) });
  ask(21, 'Агент «D» хочет выполнить terminal.run\nnpm test');
  await sleep(50); assert.equal(posts().length, 0, 'first ask waits for the owner');
  const btns = [...a.d.querySelectorAll('.approval button')].map((x) => x.textContent);
  assert.deepEqual(btns, ['Отклонить', 'Разрешить один раз', 'Не спрашивать в этом чате: terminal.run']);
  [...a.d.querySelectorAll('.approval button')].find((x) => x.textContent.startsWith('Не спрашивать')).click();
  await until(() => posts().length === 1, 'owner tap decides');
  ask(22, 'Агент «D» хочет выполнить terminal.run\nnpm run build');
  await until(() => posts().length === 2, 'same operation auto-decided'); assert.equal(posts()[1].path, '/approvals/22');
  ask(23, 'Агент «D» хочет выполнить code.write');
  await sleep(50); assert.equal(posts().length, 2, 'another operation still asks');
  ask(24, 'Агент «D» хочет выполнить terminal.run\nдеструктивная команда: rm -rf build');
  await sleep(50); assert.equal(posts().length, 2, 'destructive asks always wait');
});

test('zero innerHTML in app.js and the shell cache is bumped', { skip: false }, () => {
  assert.ok(!/innerHTML/.test(readFileSync(join(UI, 'app.js'), 'utf8')));
  assert.ok(Number(/bossman-shell-v(\d+)/.exec(readFileSync(join(UI, 'sw.js'), 'utf8'))[1]) >= 19);
});
