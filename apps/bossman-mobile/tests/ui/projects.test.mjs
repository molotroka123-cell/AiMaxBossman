/* Owner 31.10: «выбор проекта перед созданием чата как у тебя», «функция проекты», «хороший файл менеджер памяти».
   The REAL app.js + index.html in jsdom with a scripted /m/api: the new-chat button opens the project picker, the chosen
   project goes into POST /threads and shows in the chat header, the drawer filters chats by project, the project file
   manager lists / navigates / deletes only after a confirmation, and the memory view lists and forgets facts. */
import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { boot, skip, until, closeAll } from './_boot.mjs';

after(closeAll);
const AGENTS = { items: [{ id: 7, name: 'Qwen', tools: ['code.write'], model: { alias: 'qwen' }, local: true }], default_agent_id: 7 };
const PROJECTS = { root: 'C:/P', items: [
  { slug: 'site', name: 'Мой сайт', path: 'C:/P/site', threads: 1 },
  { slug: 'calc', name: 'Калькулятор', path: 'C:/P/calc', threads: 0 }] };
const THREADS = { items: [
  { id: 'tA', title: 'Вёрстка', project: 'site', turns: 1 },
  { id: 'tB', title: 'Просто вопрос', project: null, turns: 2 },
  { id: 'tC', title: 'Формулы', project: 'calc', turns: 1 }] };
const FILES = { project: 'site', path: '', parent: null, hidden: 1, entries: [
  { name: 'src', path: 'src', is_dir: true, size: null, mtime: '2026-10-31T10:00:00+01:00' },
  { name: 'index.html', path: 'index.html', is_dir: false, size: 2048, mtime: '2026-10-31T10:01:00+01:00' }] };
const SRC = { project: 'site', path: 'src', parent: '', hidden: 0, entries: [
  { name: 'app.js', path: 'src/app.js', is_dir: false, size: 10, mtime: '2026-10-31T10:02:00+01:00' }] };

function routes(state = {}) {
  return (p, m, body) => {
    if (p === '/agents') return AGENTS;
    if (p === '/projects' && m === 'GET') return PROJECTS;
    if (p === '/projects' && m === 'POST') return { slug: 'new-proj', name: body.name, path: 'C:/P/new-proj', threads: 0 };
    if (p === '/threads' && m === 'GET') return THREADS;
    if (p === '/threads' && m === 'POST') return { id: 'tN' };
    if (p === '/threads/tN/send') return { task: { id: 5 }, model: { alias: 'qwen' } };
    if (p === '/threads/tA') return { id: 'tA', title: 'Вёрстка', project: 'site', turns: [] };
    if (p.startsWith('/tasks/')) return { id: 5, status: 'running' };
    if (p === '/projects/site/files') return FILES;
    if (p === '/projects/site/files?path=src') return SRC;
    if (m === 'DELETE' && p.startsWith('/projects/site/files?')) { state.deleted = p; return { ok: true, deleted: 'index.html' }; }
    if (p.startsWith('/memory/facts?')) return { items: state.forgot ? [] : [{ id: 7, subject: 'проект', predicate: 'стек', statement: 'синтетический факт' }] };
    if (p === '/memory/facts/7/forget') { state.forgot = true; return { revoked: [{ id: 7 }] }; }
    return undefined;
  };
}
const sheet = (d) => d.querySelector('.sheet.chat-actions');

test('new chat: the project picker comes first, the choice goes to POST /threads and into the header', { skip }, async () => {
  const { d, w, calls, $ } = await boot({ routes: routes() });
  await until(() => $('model-name').textContent.includes('Qwen'), 'agent');
  assert.equal($('project-name').textContent, 'Без проекта');
  $('btn-new').click();
  await until(() => sheet(d) && sheet(d).querySelectorAll('.project-opt').length === 3, 'picker with 2 projects + «Без проекта»');
  const opts = [...sheet(d).querySelectorAll('.project-opt b')].map((b) => b.textContent);
  assert.deepEqual(opts, ['Без проекта', 'Мой сайт', 'Калькулятор']);
  sheet(d).querySelector('.project-opt[data-slug="calc"]').click();
  await until(() => !sheet(d), 'picker closed');
  assert.equal($('project-name').textContent, 'Калькулятор');
  assert.equal(w.localStorage.getItem('bm.project'), 'calc');
  $('input').value = 'Сделай калькулятор'; $('composer').requestSubmit();
  await until(() => calls.some((c) => c.path === '/threads' && c.method === 'POST'), 'thread created');
  const created = calls.find((c) => c.path === '/threads' && c.method === 'POST');
  assert.deepEqual(created.body, { title: 'Сделай калькулятор', project: 'calc' });
});

test('«Без проекта» sends no project; «Новый проект» creates it first', { skip }, async () => {
  const { d, calls, $ } = await boot({ routes: routes(), before: (w) => w.localStorage.setItem('bm.project', 'site') });
  await until(() => $('model-name').textContent.includes('Qwen'), 'agent');
  $('btn-new').click();
  await until(() => sheet(d) && sheet(d).querySelectorAll('.project-opt').length === 3, 'picker');
  assert.ok(sheet(d).querySelector('.project-opt[data-slug="site"]').classList.contains('sel'), 'current project marked');
  const inp = sheet(d).querySelector('#project-new-name'); const mk = sheet(d).querySelector('[data-act="project-create"]');
  assert.equal(mk.disabled, true, 'empty name: button off');
  inp.value = 'Новый проект'; inp.dispatchEvent(new d.defaultView.Event('input'));
  mk.click();
  await until(() => !sheet(d), 'created and closed');
  assert.deepEqual(calls.find((c) => c.path === '/projects' && c.method === 'POST').body, { name: 'Новый проект' });
  assert.equal($('project-name').textContent, 'Новый проект');

  $('btn-new').click();
  await until(() => sheet(d) && sheet(d).querySelector('.project-opt[data-slug=""]'), 'picker again');
  sheet(d).querySelector('.project-opt[data-slug=""]').click();
  $('input').value = 'Вопрос'; $('composer').requestSubmit();
  await until(() => calls.some((c) => c.path === '/threads' && c.method === 'POST'), 'thread created');
  assert.deepEqual(calls.find((c) => c.path === '/threads' && c.method === 'POST').body, { title: 'Вопрос' });
});

test('drawer: chats are tagged and filtered by project; opening a chat shows its project', { skip }, async () => {
  const { d, $ } = await boot({ routes: routes() });
  $('btn-drawer').click();
  await until(() => $('thread-list').querySelectorAll('button.thread').length === 3 && !$('thread-filter').hidden
    && $('thread-list').querySelector('.thread-proj'), 'threads + filter');
  const chips = [...$('thread-filter').querySelectorAll('button')].map((b) => b.textContent);
  assert.deepEqual(chips, ['Все', 'Мой сайт', 'Калькулятор', 'Без проекта']);
  assert.equal($('thread-list').querySelector('button.thread').querySelector('.thread-proj').textContent, '📁 Мой сайт');
  [...$('thread-filter').querySelectorAll('button')].find((b) => b.textContent === 'Мой сайт').click();
  const visible = () => [...$('thread-list').querySelectorAll('button.thread')].filter((n) => !n.hidden).map((n) => n.querySelector('b').textContent);
  assert.deepEqual(visible(), ['Вёрстка']);
  [...$('thread-filter').querySelectorAll('button')].find((b) => b.textContent === 'Без проекта').click();
  assert.deepEqual(visible(), ['Просто вопрос']);
  [...$('thread-filter').querySelectorAll('button')].find((b) => b.textContent === 'Все').click();
  assert.equal(visible().length, 3);
  $('thread-list').querySelector('button.thread').click();
  await until(() => $('project-name').textContent === 'Мой сайт', 'header shows the chat project');
  assert.ok(d);
});

test('file manager: list, open a folder, download link, delete only after confirmation', { skip }, async () => {
  const state = {};
  const { d, $ } = await boot({ routes: routes(state), before: (w) => w.localStorage.setItem('bm.project', 'site') });
  $('project-chip').click();
  await until(() => sheet(d) && sheet(d).querySelectorAll('.file-row').length === 2, 'two entries');
  const rows = [...sheet(d).querySelectorAll('.file-row')];
  assert.match(rows[0].textContent, /📁 src/); assert.match(rows[1].textContent, /📄 index\.html.*2 КБ/);
  const dl = rows[1].querySelector('a');
  assert.equal(dl.getAttribute('href'), '/m/api/projects/site/files/download?path=index.html');
  assert.equal(dl.getAttribute('download'), 'index.html');
  assert.match(sheet(d).textContent, /Скрыто: 1/);
  rows[0].querySelector('[data-act="open"]').click();
  await until(() => sheet(d).querySelector('.file-row[data-path="src/app.js"]'), 'inside src');
  sheet(d).querySelector('.file-row.dir').click();                      // «..» back
  await until(() => sheet(d).querySelector('.file-row[data-path="index.html"]'), 'back at the root');
  sheet(d).querySelector('.file-row[data-path="index.html"] [data-act="delete"]').click();
  await until(() => sheet(d).querySelector('[data-act="delete-confirm"]'), 'confirmation view');
  assert.equal(state.deleted, undefined, 'nothing deleted before the confirmation');
  sheet(d).querySelector('[data-act="delete-confirm"]').click();
  await until(() => state.deleted, 'deleted');
  assert.equal(state.deleted, '/projects/site/files?path=index.html&confirm=index.html');
  assert.ok($('drawer-files') && $('drawer-memory'));
});

test('memory view: facts listed, «Забыть» asks first, then forgets', { skip }, async () => {
  const state = {};
  const { d, calls, $ } = await boot({ routes: routes(state) });
  $('drawer-memory').click();
  await until(() => sheet(d) && sheet(d).querySelector('.memory-fact'), 'fact row');
  assert.match(sheet(d).querySelector('.memory-fact').textContent, /проект · стек.*синтетический факт/);
  sheet(d).querySelector('.memory-fact [data-act="forget"]').click();
  await until(() => sheet(d).querySelector('[data-act="forget-confirm"]'), 'confirm view');
  assert.ok(!calls.some((c) => c.path.endsWith('/forget')), 'not forgotten before confirmation');
  sheet(d).querySelector('[data-act="forget-confirm"]').click();
  await until(() => state.forgot && sheet(d).textContent.includes('Фактов пока нет'), 'forgotten and reloaded');
  assert.equal(calls.find((c) => c.path.endsWith('/forget')).method, 'POST');
});
