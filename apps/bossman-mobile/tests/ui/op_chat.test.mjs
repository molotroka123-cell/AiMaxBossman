/* Operator UI (bossmobile/ui/op/, owner 11.11 «просто open code в телефоне»): login, operator + warm-up, send and
   stream, tool rows, approvals, reconnect dedupe, STOP, empty answer, CSRF refresh. REAL op/index.html + op.js + md.js
   in jsdom (skipped when jsdom is not installed). */
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { bootOp, closeAll, skip, sleep, until, OPERATOR } from './_op_boot.mjs';

after(closeAll);

test('login screen when there is no session; the code logs in and the app opens', { skip }, async () => {
  let authed = false;
  const t = await bootOp({ waitApp: false, routes: (p, m) => {
    if (p === '/session' && !authed) return { __status: 401, body: { detail: { message: 'нужен вход', code: 'BM_LOGIN_REQUIRED' } } };
    if (p === '/login') { authed = true; return { ok: true, csrf: 'C1' }; }
    return undefined;
  } });
  await until(() => !t.$('login').hidden, 'login shown');
  assert.equal(t.$('app').hidden, true);
  t.$('login-token').value = 'pair-123';
  t.$('login-form').dispatchEvent(new t.w.Event('submit', { cancelable: true }));
  await until(() => !t.$('app').hidden, 'app shown after login');
  const login = t.calls.find((c) => c.path === '/login');
  assert.deepEqual(login.body, { token: 'pair-123' });
  await until(() => t.count('GET', '/operator') >= 1, 'operator loaded');
  assert.equal(t.w.__opDebug.csrf, 'C1');
});

test('#pair= link logs in automatically and the token leaves the address bar', { skip }, async () => {
  const t = await bootOp({ url: 'http://pc.local/m/op/#pair=abc%2D42', routes: (p) => (p === '/login' ? { ok: true, csrf: 'P1' } : undefined) });
  const login = t.calls.find((c) => c.path === '/login');
  assert.deepEqual(login.body, { token: 'abc-42' });
  assert.equal(t.w.location.hash, '');
  assert.equal(t.calls.some((c) => c.path === '/session'), false, 'pairing must not depend on an old session');
});

test('no model / provider picker on the screen', { skip }, async () => {
  const t = await bootOp();
  assert.equal(t.d.querySelector('select'), null);
  const labels = [...t.d.querySelectorAll('button')].map((b) => b.textContent.trim());
  for (const want of ['STOP', 'Фото', 'Видео', 'Отправить']) assert.ok(labels.includes(want), want);
  assert.equal(t.d.querySelector('.title').textContent, 'Bossman');
});

test('operator with an unloaded model: warm-up starts by itself and the status is honest', { skip }, async () => {
  let release;
  const gate = new Promise((r) => { release = r; });
  const t = await bootOp({ routes: async (p, m) => {
    if (p === '/operator') return { ...OPERATOR, model: { ...OPERATOR.model, loaded: false } };
    if (p === '/operator/warm' && m === 'POST') { await gate; return { ok: true, loaded: true, load_ms: 3000, error: null }; }
    return undefined;
  } });
  await until(() => t.count('POST', '/operator/warm') === 1, 'warm called');
  await until(() => /модель загружается/.test(t.$('status-text').textContent), 'loading status');
  release();
  await until(() => t.$('status-text').textContent === 'готов', 'ready status');
  assert.equal(t.count('POST', '/operator/warm'), 1);
});

test('an older Bossman (warm unsupported) is not an error', { skip }, async () => {
  const t = await bootOp({ routes: (p) => {
    if (p === '/operator') return { ...OPERATOR, model: { ...OPERATOR.model, loaded: false } };
    if (p === '/operator/warm') return { __status: 501, body: { detail: { message: 'старый Bossman', code: 'BM_OPERATOR_UNSUPPORTED' } } };
    return undefined;
  } });
  await until(() => t.count('POST', '/operator/warm') === 1, 'warm tried');
  await sleep(30);
  assert.equal(t.$('status-text').textContent, 'готов');
  assert.equal(t.$('banner').hidden, true);
});

test('send → AUTO message to the operator agent → streamed deltas render as Markdown', { skip }, async () => {
  const t = await bootOp();
  await until(() => t.count('GET', '/operator') >= 1, 'operator');
  await t.send('Привет, ПК');
  await until(() => t.sources.length === 1, 'stream opened');
  const msg = t.calls.find((c) => c.path === '/agent/sessions/T9/messages');
  assert.equal(msg.body.text, 'Привет, ПК');
  assert.equal(msg.body.mode, 'AUTO');
  assert.equal(msg.body.agent_id, 1);
  assert.match(msg.body.client_request_id, /^op-/);
  assert.deepEqual(Object.keys(msg.body).sort(), ['agent_id', 'client_request_id', 'mode', 'text']);
  const patch = t.calls.find((c) => c.method === 'PATCH');
  assert.deepEqual(patch.body, { mode: 'AUTO', bypass_permissions: false });
  assert.equal(msg.csrf, 'tok');
  const es = t.es();
  assert.match(es.url, /\/m\/api\/tasks\/77\/events\?after=0$/);
  es.open();
  es.emit({ kind: 'task.started', seq: 1, task_id: 77 });
  es.emit({ kind: 'run.answer_delta', seq: 2, task_id: 77, text: 'Привет, ' });
  es.emit({ kind: 'run.answer_delta', seq: 3, task_id: 77, text: '**владелец**' });
  const ans = () => t.d.querySelector('.turn .answer');
  await until(() => ans() && ans().querySelector('strong'), 'markdown painted');
  assert.equal(ans().textContent, 'Привет, владелец');
  assert.equal(t.$('status-text').textContent, 'пишет ответ');
  assert.equal(t.d.querySelector('.turn .bubble').textContent, 'Привет, ПК');
});

test('tool_use + tool_result render ONE collapsible row with args and result', { skip }, async () => {
  const t = await bootOp();
  await t.send('dir');
  await until(() => t.sources.length === 1, 'stream');
  const es = t.es();
  es.emit({ kind: 'run.tool_use', seq: 1, task_id: 77, call_id: 'c1', tool: 'terminal.run', args: { command: 'dir C:\\Users', token: 'sk-SECRETSECRETSECRET1234' } });
  await until(() => t.d.querySelectorAll('.tl-row.tool').length === 1, 'tool row');
  assert.match(t.$('status-text').textContent, /выполняет/);
  es.emit({ kind: 'run.tool_result', seq: 2, task_id: 77, call_id: 'c1', tool: 'terminal.run', ok: true, duration_ms: 1200, summary: '3 файла', preview: 'a.txt\nb.txt\nc.txt', truncated: true });
  await sleep(10);
  const rows = t.d.querySelectorAll('.tl-row.tool');
  assert.equal(rows.length, 1);
  const row = rows[0];
  assert.ok(row.classList.contains('is-ok'));
  assert.equal(row.querySelector('.mark').textContent, '✓');
  assert.match(row.querySelector('.tool-state').textContent, /1\.2 с/);
  const body = row.querySelector('.tool-body').textContent;
  assert.match(body, /dir C:\\\\Users/);
  assert.match(body, /c\.txt/);
  assert.match(body, /обрезано/);
  assert.equal(body.includes('SECRETSECRETSECRET'), false, 'a secret in tool args reached the screen');
});

test('approval card: «Разрешить» and «Отклонить» post the decision', { skip }, async () => {
  const t = await bootOp();
  await t.send('удали temp');
  await until(() => t.sources.length === 1, 'stream');
  const es = t.es();
  es.emit({ kind: 'approval.created', seq: 1, task_id: 77, id: 5, approval_kind: 'terminal', preview: 'del C:\\temp\\*' });
  await until(() => t.d.querySelector('.apcard'), 'approval card');
  assert.equal(t.$('status-text').textContent, 'ждёт подтверждения');
  const yes = [...t.d.querySelectorAll('.apcard button')].find((b) => b.textContent === 'Разрешить');
  yes.click();
  await until(() => t.d.querySelector('.apcard .done.ok'), 'approved');
  assert.deepEqual(t.calls.find((c) => c.path === '/approvals/5').body, { approve: true });
  es.emit({ kind: 'approval.created', seq: 2, task_id: 77, id: 6, preview: 'format D:' });
  await until(() => t.d.querySelectorAll('.apcard').length === 2, 'second card');
  const no = [...t.d.querySelectorAll('.apcard')][1].querySelector('button');
  assert.equal(no.textContent, 'Отклонить');
  no.click();
  await until(() => t.d.querySelector('.apcard .done.bad'), 'denied');
  assert.deepEqual(t.calls.find((c) => c.path === '/approvals/6').body, { approve: false });
  // the same approval re-announced (reconnect / approvals scan) never duplicates the card
  es.emit({ kind: 'approval.created', seq: 3, task_id: 77, id: 6, preview: 'format D:' });
  await sleep(10);
  assert.equal(t.d.querySelectorAll('.apcard').length, 2);
});

test('after a dropped stream, history replay + reconnect never render an event twice', { skip }, async () => {
  const history = [
    { kind: 'task.started', seq: 1, task_id: 77 },
    { kind: 'run.tool_use', seq: 2, task_id: 77, call_id: 'c1', tool: 'fs.list', args: { path: 'C:\\' } },
    { kind: 'run.tool_result', seq: 3, task_id: 77, call_id: 'c1', tool: 'fs.list', ok: true },
    { kind: 'run.answer_delta', seq: 4, task_id: 77, text: 'Раз ' },
    { kind: 'run.answer_delta', seq: 5, task_id: 77, text: 'два' },
  ];
  const t = await bootOp({ routes: (p) => (p === '/tasks/77/history' ? { events: history } : undefined) });
  await t.send('что на диске');
  await until(() => t.sources.length === 1, 'stream');
  const es = t.es();
  es.open();
  for (const ev of history.slice(0, 4)) es.emit(ev);
  es.fail(true);                                          // the server closed the stream
  await until(() => t.sources.length === 2, 'reconnected');
  assert.match(t.es().url, /after=5$/, 'reconnect resumes after the last seen seq');
  t.es().emit(history[3]);                                // a late duplicate from the new stream
  t.es().emit({ kind: 'run.answer_delta', seq: 6, task_id: 77, text: ' три' });
  await until(() => t.d.querySelector('.turn .answer').textContent === 'Раз два три', 'answer');
  assert.equal(t.d.querySelectorAll('.tl-row.tool').length, 1);
});

test('STOP posts the global stop and stops this chat\'s running task', { skip }, async () => {
  const t = await bootOp();
  await t.send('долгая задача');
  await until(() => t.sources.length === 1, 'stream');
  t.$('btn-stop').click();
  await until(() => t.count('POST', '/stop') === 1 && t.count('POST', '/threads/T9/stop') === 1, 'stops posted');
  assert.deepEqual(t.calls.find((c) => c.path === '/stop' && c.method === 'POST').body, { confirm: true });
  await until(() => t.$('status-text').textContent === 'остановлен', 'stopped status');
  assert.ok(t.d.querySelector('.stopcard'));
});

test('an empty answer is never silent: «Модель вернула пустой ответ» + «Повторить»', { skip }, async () => {
  let done = false;
  const t = await bootOp({ routes: (p) => (p === '/tasks/77' && done ? { id: 77, status: 'completed', result: '' } : undefined) });
  await t.send('скажи что-нибудь');
  await until(() => t.sources.length === 1, 'stream');
  done = true;
  t.es().emit({ kind: 'task.completed', seq: 1, task_id: 77 });
  await until(() => t.d.querySelector('.notice.err'), 'empty notice');
  assert.match(t.d.querySelector('.notice.err').textContent, /Модель вернула пустой ответ/);
  assert.equal(t.$('status-text').textContent, 'готов');
  const retry = t.d.querySelector('.notice.err button');
  assert.equal(retry.textContent, 'Повторить');
  retry.click();
  await until(() => t.count('POST', '/agent/sessions/T9/messages') === 2, 'retried');
  const sent = t.calls.filter((c) => c.path === '/agent/sessions/T9/messages');
  assert.equal(sent[1].body.text, 'скажи что-нибудь');
});

test('a failed task shows the real reason in red with «Повторить»', { skip }, async () => {
  let done = false;
  const t = await bootOp({ routes: (p) => (p === '/tasks/77' && done ? { id: 77, status: 'failed', error: 'модель не отвечает (таймаут 120 с)' } : undefined) });
  await t.send('проверь');
  await until(() => t.sources.length === 1, 'stream');
  done = true;
  t.es().emit({ kind: 'task.failed', seq: 1, task_id: 77 });
  await until(() => t.d.querySelector('.notice.err'), 'error row');
  assert.match(t.d.querySelector('.notice.err').textContent, /таймаут 120 с/);
  assert.ok(t.d.querySelector('.notice.err button'));
});

test('CSRF 403: refetch /session once and repeat the request with the new token', { skip }, async () => {
  let first = true, rejected = false;
  const t = await bootOp({ routes: (p, m) => {
    if (p === '/threads' && m === 'POST' && first) { first = false; rejected = true; return { __status: 403, body: { detail: { message: 'csrf', code: 'csrf' } } }; }
    if (p === '/session') return { csrf: rejected ? 'NEW' : 'tok' };
    return undefined;
  } });
  await until(() => t.count('GET', '/operator') >= 1, 'operator');
  await t.send('привет');
  await until(() => t.sources.length === 1, 'sent after csrf refresh');
  const posts = t.calls.filter((c) => c.path === '/threads' && c.method === 'POST');
  assert.equal(posts.length, 2);
  assert.equal(posts[0].csrf, 'tok');
  assert.equal(posts[1].csrf, 'NEW');
});

test('a second 403 csrf is shown, never looped', { skip }, async () => {
  const t = await bootOp({ routes: (p, m) => (p === '/threads' && m === 'POST' ? { __status: 403, body: { detail: { message: 'не пройдена CSRF-проверка', code: 'csrf' } } } : undefined) });
  await t.send('привет');
  await until(() => t.d.querySelector('.notice.err'), 'error shown');
  assert.equal(t.calls.filter((c) => c.path === '/threads' && c.method === 'POST').length, 2);
  assert.match(t.d.querySelector('.notice.err').textContent, /CSRF/);
});

test('double submit while sending creates ONE task', { skip }, async () => {
  let release;
  const gate = new Promise((r) => { release = r; });
  const t = await bootOp({ routes: async (p) => { if (p === '/agent/sessions/T9/messages') { await gate; } return undefined; } });
  await t.send('раз');
  await until(() => t.count('POST', '/agent/sessions/T9/messages') === 1, 'first send');
  await t.send('раз');
  release();
  await until(() => t.sources.length === 1, 'stream');
  await sleep(20);
  assert.equal(t.count('POST', '/agent/sessions/T9/messages'), 1);
});

test('the sessions drawer lists past conversations and opens one', { skip }, async () => {
  const t = await bootOp({ routes: (p) => {
    if (p === '/threads') return { items: [{ id: 'A1', title: 'Почистить диск', updated_at: new Date().toISOString() }, { id: 'B2', title: 'Скриншот', updated_at: '2026-01-01T00:00:00Z' }] };
    if (p === '/threads/A1') return { id: 'A1', title: 'Почистить диск', turns: [{ task_id: 5, text: 'почисти temp', status: 'completed', result: 'Готово: удалено 3 ГБ' }] };
    if (p === '/threads/B2') return { id: 'B2', title: 'Скриншот', turns: [] };
    return undefined;
  } });
  await until(() => t.d.querySelector('.turn .answer') && t.d.querySelector('.turn .answer').textContent.includes('3 ГБ'), 'latest conversation restored');
  t.$('btn-history').click();
  await until(() => t.d.querySelectorAll('#sheet-list .srow').length === 2, 'sessions listed');
  assert.equal(t.$('sheet').hidden, false);
  t.d.querySelectorAll('#sheet-list .srow')[1].click();
  await until(() => t.w.__opDebug.threadId === 'B2', 'opened B2');
  assert.equal(t.$('sheet').hidden, true);
  assert.equal(t.d.querySelectorAll('.turn').length, 0);
});

test('Markdown renderer never creates script, handlers or javascript: links from hostile input', { skip }, async () => {
  const t = await bootOp();
  const { renderMarkdown } = t.w.__opTest;
  const box = t.d.createElement('div');
  const hostile = [
    '<img src=x onerror="alert(1)">', '<script>alert(1)</script>', '[жми](javascript:alert(1))',
    '[x](JaVaScRiPt:alert(1))', '[d](data:text/html,<script>alert(1)</script>)', '<a href="javascript:alert(1)">a</a>',
    '```html\n<svg onload=alert(1)>\n```', '**<b onclick=alert(1)>b</b>**', '[ok](https://example.com/a?b=1)',
  ].join('\n\n');
  renderMarkdown(box, hostile, {});
  assert.equal(box.querySelector('script, img, svg, iframe, object, embed, b'), null);
  for (const n of box.querySelectorAll('*')) {
    for (const a of n.attributes) assert.ok(!/^on/i.test(a.name), `handler attribute ${a.name}`);
  }
  const links = [...box.querySelectorAll('a')];
  assert.equal(links.length, 1);
  assert.equal(links[0].getAttribute('href'), 'https://example.com/a?b=1');
  assert.equal(links[0].getAttribute('rel'), 'noopener noreferrer');
  assert.ok(box.textContent.includes('<img src=x onerror="alert(1)">'), 'hostile markup stays visible text');
  assert.ok(box.querySelector('.md-pre pre code').textContent.includes('<svg onload=alert(1)>'));
});
