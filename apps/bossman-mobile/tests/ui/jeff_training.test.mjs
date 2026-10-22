/* «Jeff · обучение» (owner 21.10): the tab shows the manual state (name, sections, size, strict mode, model), uploads a
   file as base64, switches strict mode / scope / model, and «Проверка» asks Jeff twice (без методички / с методичкой)
   and shows both answers side by side with the cited sections. Real index.html + app.js + app.css in jsdom. */
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { boot, skip, until, closeAll, UI } from './_boot.mjs';

after(closeAll);

const STATE = { schema: 'bossman.pit.jeff-kb/1', present: true, readable: true, name: 'rules.md', sections: 3, size_bytes: 2048,
  strict: false, scope_all: false, scope: 'owner',
  model: { configured: 'bossman-uncensored-chat:latest', override: null, effective: 'bossman-uncensored-chat:latest' } };

function routesWith(state, log) {
  return (p, m, b) => {
    if (p === '/jeff-kb' && m === 'GET') return state;
    if (p === '/jeff-kb/sections') return { sections: [{ n: 1, title: 'Разминка', chars: 40 }, { n: 2, title: 'Ставки', chars: 60 }] };
    if (p === '/jeff-kb/mode' && m === 'PUT') { log.push(['mode', b]); Object.assign(state, b); return state; }
    if (p === '/jeff-kb/manual' && m === 'PUT') { log.push(['upload', b]); return state; }
    if (p === '/jeff-kb/model' && m === 'PUT') { log.push(['model', b]); state.model = { ...state.model, override: b.model, effective: b.model }; return state.model; }
    if (p === '/jeff-kb/check' && m === 'POST') {
      log.push(['check', b]);
      return b.use_manual
        ? { mode: 'manual', answer: 'Двенадцать минут (§1).', cited: [{ n: 1, title: 'Разминка' }], retrieved: [1], no_match: false }
        : { mode: 'plain', answer: 'Обычно минут десять.', cited: [], retrieved: [], no_match: false };
    }
    return undefined;
  };
}

test('the Jeff tab shows the manual state and the model', { skip }, async () => {
  const log = [];
  const { d, $ } = await boot({ routes: routesWith({ ...STATE }, log) });
  d.querySelector('.tab[data-tab="jeff"]').click();
  assert.equal($('view-jeff').hidden, false);
  await until(() => $('jeff-manual-summary').querySelector('.kv'), 'summary rows');
  const text = $('jeff-manual-summary').textContent;
  assert.match(text, /rules\.md/); assert.match(text, /Разделов3/); assert.match(text, /выключен/);
  assert.match($('jeff-model-line').textContent, /bossman-uncensored-chat:latest \(по умолчанию\)/);
  $('jeff-sections-box').open = true; $('jeff-sections-box').dispatchEvent(new d.defaultView.Event('toggle'));
  await until(() => $('jeff-sections').children.length === 2, 'section titles');
  assert.equal($('jeff-sections').children[0].textContent, 'Разминка');
});

test('strict mode, scope and Gemma are switched through the owner API', { skip }, async () => {
  const log = [];
  const state = { ...STATE };
  const { w, d, $ } = await boot({ routes: routesWith(state, log) });
  d.querySelector('.tab[data-tab="jeff"]').click();
  await until(() => !$('jeff-strict').disabled, 'loaded');
  $('jeff-strict').checked = true; $('jeff-strict').dispatchEvent(new w.Event('change'));
  await until(() => log.some(([k]) => k === 'mode'), 'strict saved');
  assert.deepEqual(log.find(([k]) => k === 'mode')[1], { strict: true });
  await until(() => /только мой чат/.test($('jeff-manual-summary').textContent), 'strict shown');
  $('jeff-scope-all').checked = true; $('jeff-scope-all').dispatchEvent(new w.Event('change'));
  await until(() => log.filter(([k]) => k === 'mode').length === 2, 'scope saved');
  assert.deepEqual(log.filter(([k]) => k === 'mode')[1][1], { scope_all: true });
  $('jeff-model-gemma').click();
  await until(() => log.some(([k]) => k === 'model'), 'model saved');
  assert.deepEqual(log.find(([k]) => k === 'model')[1], { model: 'gemma4-31b-uncensored:latest' });
  await until(() => /gemma4-31b-uncensored:latest \(выбрана вами\)/.test($('jeff-model-line').textContent), 'model shown');
});

test('upload sends the file as base64 with its name', { skip }, async () => {
  const log = [];
  const { w, d, $ } = await boot({ routes: routesWith({ ...STATE }, log) });
  d.querySelector('.tab[data-tab="jeff"]').click();
  const file = new w.File(['# Раздел\nтекст'], 'rules.md', { type: 'text/markdown' });
  if (!file.arrayBuffer) file.arrayBuffer = async () => new TextEncoder().encode('# Раздел\nтекст').buffer;
  Object.defineProperty($('jeff-file'), 'files', { value: [file] });
  $('jeff-upload-btn').click();
  await until(() => log.some(([k]) => k === 'upload'), 'uploaded');
  const body = log.find(([k]) => k === 'upload')[1];
  assert.equal(body.name, 'rules.md');
  assert.equal(Buffer.from(body.data_b64, 'base64').toString('utf8'), '# Раздел\nтекст');
});

test('«Проверка» asks twice and shows both answers side by side with citations', { skip }, async () => {
  const log = [];
  const { w, d, $ } = await boot({ css: true, routes: routesWith({ ...STATE, strict: true }, log) });
  d.querySelector('.tab[data-tab="jeff"]').click();
  $('jeff-question').value = 'Сколько длится разминка?';
  $('jeff-check-form').dispatchEvent(new w.Event('submit', { cancelable: true }));
  await until(() => $('jeff-ans-manual').textContent === 'Двенадцать минут (§1).', 'manual answer');
  assert.equal($('jeff-ans-plain').textContent, 'Обычно минут десять.');
  const checks = log.filter(([k]) => k === 'check').map(([, b]) => b);
  assert.deepEqual(checks, [{ question: 'Сколько длится разминка?', use_manual: false },
    { question: 'Сколько длится разминка?', use_manual: true }]);
  assert.match($('jeff-cited').textContent, /§1 — Разминка/);
  assert.equal($('jeff-col-plain').hidden, false); assert.equal($('jeff-col-manual').hidden, false);
  assert.equal(w.getComputedStyle($('jeff-results')).display, 'grid', 'the two answers sit in one grid row');
  // without «Сравнить» only the manual answer is requested
  $('jeff-compare').checked = false;
  $('jeff-check-form').dispatchEvent(new w.Event('submit', { cancelable: true }));
  await until(() => log.filter(([k]) => k === 'check').length === 3, 'third call');
  assert.equal(log.filter(([k]) => k === 'check')[2][1].use_manual, true);
  assert.equal($('jeff-col-plain').hidden, true);
});

test('cloud fallback switch: ON by default, switched off through the owner API, shown in the summary', { skip }, async () => {
  const log = [];
  const state = { ...STATE, strict: true, cloud_fallback: true, cloud_calls_today: 2 };
  const { w, d, $ } = await boot({ routes: routesWith(state, log) });
  d.querySelector('.tab[data-tab="jeff"]').click();
  await until(() => $('jeff-manual-summary').querySelector('.kv'), 'summary rows');
  assert.equal($('jeff-cloud').checked, true);
  assert.match($('jeff-manual-summary').textContent, /включено · сегодня 2/);
  assert.match($('jeff-cloud').parentElement.textContent, /Облачный запасной вариант \(разделы методички уходят провайдеру\)/);
  $('jeff-cloud').checked = false; $('jeff-cloud').dispatchEvent(new w.Event('change'));
  await until(() => log.some(([k]) => k === 'mode'), 'switch saved');
  assert.deepEqual(log.find(([k]) => k === 'mode')[1], { cloud_fallback: false });
  await until(() => /Облако, если ПК занятвыключено/.test($('jeff-manual-summary').textContent), 'off shown');
  assert.equal($('jeff-cloud').checked, false);
});

test('citations switch: marks hidden by default, switched on through the owner API, shown in the summary', { skip }, async () => {
  const log = [];
  const state = { ...STATE, strict: true, show_citations: false };     // owner 22.10 «пусть уберёт отсылки»
  const { w, d, $ } = await boot({ routes: routesWith(state, log) });
  d.querySelector('.tab[data-tab="jeff"]').click();
  await until(() => $('jeff-manual-summary').querySelector('.kv'), 'summary rows');
  assert.equal($('jeff-citations').checked, false);
  assert.match($('jeff-manual-summary').textContent, /Ссылки \(§N\) в ответахскрыты/);
  assert.doesNotMatch($('jeff-strict').parentElement.textContent, /со ссылками/);       // the strict label no longer promises marks
  assert.match($('jeff-citations').parentElement.textContent, /по умолчанию выключено, Jeff отвечает без отсылок/);
  $('jeff-citations').checked = true; $('jeff-citations').dispatchEvent(new w.Event('change'));
  await until(() => log.some(([k]) => k === 'mode'), 'switch saved');
  assert.deepEqual(log.find(([k]) => k === 'mode')[1], { show_citations: true });
  await until(() => /Ссылки \(§N\) в ответахпоказываются/.test($('jeff-manual-summary').textContent), 'on shown');
  assert.equal($('jeff-citations').checked, true);
  $('jeff-citations').checked = false; $('jeff-citations').dispatchEvent(new w.Event('change'));
  await until(() => log.filter(([k]) => k === 'mode').length === 2, 'off saved');
  assert.deepEqual(log.filter(([k]) => k === 'mode')[1][1], { show_citations: false });
});

test('«Проверка» shows who answered: 💻 локально or ☁️ облако · model, and an honest error', { skip }, async () => {
  const log = [];
  let mode = 'cloud';
  const routes = (p, m, b) => {
    if (p === '/jeff-kb/check' && m === 'POST') {
      log.push(['check', b]);
      if (!b.use_manual) return { mode: 'plain', answer: 'Обычно минут десять.', cited: [], engine: 'local', provider: 'ollama', model: 'gemma4-31b-uncensored:latest' };
      if (mode === 'down') return { __status: 503, body: { detail: { code: 'JEFF_NO_MODELS', message: 'Нет связи с моделями: локальная — не ответила вовремя; облако — бесплатные облачные модели не ответили. Ответа по методичке нет.' } } };
      return mode === 'cloud'
        ? { mode: 'manual', answer: 'Двенадцать минут (§1).', cited: [{ n: 1, title: 'Разминка' }], retrieved: [1], no_match: false,
          engine: 'cloud', provider: 'OpenRouter', model: 'vendor/model-x:free', fallback_reason: 'timeout' }
        : { mode: 'manual', answer: 'Двенадцать минут (§1).', cited: [{ n: 1, title: 'Разминка' }], retrieved: [1], no_match: false,
          engine: 'local', provider: 'ollama', model: 'gemma4-31b-uncensored:latest' };
    }
    return routesWith({ ...STATE, strict: true, cloud_fallback: true }, log)(p, m, b);
  };
  const { w, d, $ } = await boot({ css: true, routes });
  d.querySelector('.tab[data-tab="jeff"]').click();
  $('jeff-question').value = 'Сколько длится разминка?';
  $('jeff-check-form').dispatchEvent(new w.Event('submit', { cancelable: true }));
  await until(() => $('jeff-engine-manual').textContent !== '', 'manual badge');
  assert.equal($('jeff-engine-manual').textContent, '☁️ облако · vendor/model-x:free');
  assert.ok($('jeff-engine-manual').classList.contains('badge-cloud'));
  assert.equal($('jeff-engine-plain').textContent, '💻 локально');
  assert.ok($('jeff-engine-plain').classList.contains('badge-local'));
  assert.match($('jeff-cited').textContent, /§1 — Разминка/);
  // local answer: the badge says so
  mode = 'local'; $('jeff-compare').checked = false;
  $('jeff-check-form').dispatchEvent(new w.Event('submit', { cancelable: true }));
  await until(() => $('jeff-engine-manual').textContent === '💻 локально', 'local badge');
  // neither answered: an honest error, no answer text and no badge
  mode = 'down';
  $('jeff-check-form').dispatchEvent(new w.Event('submit', { cancelable: true }));
  await until(() => /^Ошибка: Нет связи с моделями/.test($('jeff-ans-manual').textContent), 'honest error');
  assert.equal($('jeff-engine-manual').textContent, '');
  assert.equal(w.getComputedStyle($('jeff-engine-manual')).display, 'none');
});

test('8 tabs fit the bar and the shell cache is bumped for this tab', () => {
  const css = readFileSync(join(UI, 'app.css'), 'utf8');
  const html = readFileSync(join(UI, 'index.html'), 'utf8');
  const sw = readFileSync(join(UI, 'sw.js'), 'utf8');
  const tabs = (html.match(/class="tab[ "]/g) || []).length;
  assert.equal(tabs, 8);
  assert.match(css, /\.tabbar \{[^}]*grid-template-columns: repeat\(8, 1fr\)/);
  assert.ok(Number(/bossman-shell-v(\d+)/.exec(sw)[1]) >= 18, 'v18: cloud fallback switch + engine badge');
});
