/* Автосохранение панели «Веб-дизайн» (находки A9-06 и A9-07 аудита №9).

   Оба дефекта происходят БЕЗ единой ошибки на сервере: сервер отвечает честно,
   расходится клиент. Поэтому проверяются они здесь, на настоящем модуле
   страницы, с подменённым api.raw вместо браузера. */
import test from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { addEventListener() {}, innerWidth: 1200 };
globalThis.document = { activeElement: null, addEventListener() {}, getElementById: () => null };
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };

const { autosaveState: state, flushSave, scheduleSave, sendEdit, attachEditor, SAVE_DELAY_MS,
  acceptsPickerMessage } = await import('../pages/web_designer.js');
const { api } = await import('../api.js');

class Conflict extends Error {
  constructor(version) {
    super(`код изменился: сохранена версия ${version}`);
    this.status = 409;
  }
}

/** Сервер веб-дизайнера в одну функцию: версия растёт, чужая база — 409. */
function fakeServer(version = 1) {
  const server = { version, calls: [], code: '<p>сервер</p>' };
  api.raw = async (path, opts = {}) => {
    const method = opts.method || 'GET';
    server.calls.push({ path, method, body: opts.body });
    if (method === 'GET') {
      return { meta: { id: 1, version: server.version }, code: server.code, versions: [] };
    }
    if (opts.body.base_version !== server.version) throw new Conflict(server.version);
    server.version += 1;
    if (method === 'PUT') server.code = opts.body.html;
    if (path.endsWith('/edit')) server.code = `${server.code}<!--правка-->`;
    return { ok: true, meta: { id: 1, version: server.version },
             element: { tag: 'p', bd_id: 'bd-1' } };
  };
  return server;
}

function owner(text, { version, dirty = true }) {
  const editor = attachEditor({ value: text });
  state.id = 1;
  state.meta = { id: 1, version };
  state.code = '<p>сервер</p>';
  state.dirty = dirty;
  state.saveConflict = false;
  state.recovery = null;
  state.mutating = false;
  state.selected = { tag: 'p', bd_id: 'bd-1' };
  return editor;
}

const puts = (server) => server.calls.filter((c) => c.method === 'PUT');

test('после 409 версия обновлена, но чужой код не затирается без выбора владельца', async () => {
  const server = fakeServer(4);                 // вторая вкладка сохранила v4
  const editor = owner('<p>владелец печатает</p>', { version: 3 });

  await flushSave();                            // 409: правка построена на v3
  assert.equal(puts(server).length, 1);
  assert.equal(puts(server)[0].body.base_version, 3);
  // Отказ обязан обновить meta: иначе КАЖДОЕ следующее нажатие клавиши шлёт
  // ту же устаревшую базу и получает тот же 409 — сохранить нельзя ничего.
  assert.equal(state.meta.version, 4);
  assert.equal(state.dirty, true, 'набранное не потеряно');
  assert.equal(editor.value, '<p>владелец печатает</p>');

  editor.value = '<p>владелец печатает ещё</p>';
  state.dirty = true;
  assert.equal(await flushSave(), false);
  assert.equal(puts(server).length, 1, 'новый набор не даёт согласия затереть чужую версию');
  assert.equal(server.code, '<p>сервер</p>');
  assert.equal(editor.value, '<p>владелец печатает ещё</p>');
  assert.equal(state.saveConflict, true);
  // Explicit owner replacement is exercised through the real-browser suite.
});

test('положительный контроль: обычное сохранение проходит с первого раза', async () => {
  const server = fakeServer(2);
  owner('<h1>сайт</h1>', { version: 2 });
  await flushSave(false);
  assert.equal(puts(server).length, 1);
  assert.equal(server.code, '<h1>сайт</h1>');
  assert.equal(state.meta.version, 3);
  assert.equal(state.dirty, false);
});

test('нечего сохранять — запроса нет', async () => {
  const server = fakeServer(2);
  owner('<h1>сайт</h1>', { version: 2, dirty: false });
  await flushSave();
  assert.equal(server.calls.length, 0);
});

test('отложенный автосейв не откатывает применённую правку', async () => {
  const server = fakeServer(5);
  const editor = owner('<p>набрано в редакторе</p>', { version: 5 });
  scheduleSave();                               // владелец печатал секунду назад

  await sendEdit({ op: 'style', bd_id: 'bd-1', props: { color: '#fff' } }, 'ok');
  // Набранное уходит ПЕРЕД правкой, правка ложится сверху: обе целы.
  assert.deepEqual(server.calls.map((c) => `${c.method} ${c.path.split('/').pop()}`),
                   ['PUT code', 'POST edit', 'GET 1', 'GET models']);
  assert.equal(server.version, 7);
  assert.equal(server.code, '<p>набрано в редакторе</p><!--правка-->');

  await new Promise((r) => setTimeout(r, SAVE_DELAY_MS + 200));
  // Раньше здесь уходил ещё один PUT со СТАРЫМ текстом и уже новой версией:
  // сервер принимал его, и правка исчезала — без ошибки и с ростом версии.
  assert.equal(puts(server).length, 1, 'отложенный автосейв не переписал правку');
  assert.equal(server.code, '<p>набрано в редакторе</p><!--правка-->');
  assert.equal(editor.value, server.code, 'редактор показывает то, что лежит на сервере');
});

test('положительный контроль: отложенный автосейв сам по себе сохраняет', async () => {
  const server = fakeServer(9);
  owner('<p>набрано</p>', { version: 9 });
  scheduleSave();
  await new Promise((r) => setTimeout(r, SAVE_DELAY_MS + 200));
  assert.equal(puts(server).length, 1);
  assert.equal(server.code, '<p>набрано</p>');
  assert.equal(state.meta.version, 10);
});

test('набор во время запроса не считается сохранённым', async () => {
  const server = fakeServer(1);
  const editor = owner('<p>первое</p>', { version: 1 });
  const flying = flushSave();
  editor.value = '<p>первое и второе</p>';      // владелец печатает, пока идёт PUT
  await flying;
  assert.equal(server.code, '<p>первое</p>');
  assert.equal(state.dirty, true, 'новый набор остаётся несохранённым');
  await new Promise((r) => setTimeout(r, SAVE_DELAY_MS + 200));
  assert.equal(server.code, '<p>первое и второе</p>', 'новый набор тоже сохранён без следующего нажатия');
});

test('ошибка сохранения не разрешает зависимую правку элемента', async () => {
  const server = fakeServer(4);
  const editor = owner('<p>мой черновик</p>', { version: 3 });
  await sendEdit({ op: 'text', bd_id: 'bd-1', text: 'ошибочная правка' });
  assert.equal(server.calls.filter((c) => c.method === 'POST').length, 0);
  assert.equal(server.code, '<p>сервер</p>');
  assert.equal(editor.value, '<p>мой черновик</p>');
});

test('повторяемая запись привязана к неизменённой версии и создаёт одну правку', async () => {
  const server = fakeServer(4);
  owner('<p>мой черновик</p>', { version: 4 });
  const raw = api.raw;
  let fail = true;
  api.raw = async (path, opts = {}) => {
    if (opts.method === 'PUT' && fail) {
      fail = false;
      throw Object.assign(new Error('шлюз временно недоступен'), { status: 502 });
    }
    return raw(path, opts);
  };
  assert.equal(await flushSave(), false);
  assert.equal(state.recovery.canRetry, true);
  assert.equal(state.recovery.version, 4);
  await state.recovery.retry();
  assert.equal(puts(server).length, 1);
  assert.equal(server.version, 5);
  assert.equal(state.dirty, false);
});

test('потерянный ответ после записи подтверждается чтением без второй записи', async () => {
  const server = fakeServer(4);
  owner('<p>мой черновик</p>', { version: 4 });
  const raw = api.raw;
  api.raw = async (path, opts = {}) => {
    const res = await raw(path, opts);
    if (opts.method === 'PUT') throw Object.assign(new Error('ответ потерян'), { status: 502 });
    return res;
  };
  assert.equal(await flushSave(), false);
  assert.equal(state.dirty, false, 'Bossman перечитал фактический код после неизвестного исхода');
  assert.equal(state.recovery.canRetry, false);
  await flushSave();
  assert.equal(puts(server).length, 1);
  assert.equal(server.version, 5);
});

for (const status of [403, 404, 413]) {
  test(`HTTP ${status} сохраняет причину и не предлагает повтор`, async () => {
    const server = fakeServer(4);
    const editor = owner('<p>мой черновик</p>', { version: 4 });
    const raw = api.raw;
    api.raw = async (path, opts = {}) => {
      if (opts.method === 'PUT') throw Object.assign(new Error(`точная причина ${status}`), { status });
      return raw(path, opts);
    };
    assert.equal(await flushSave(), false);
    assert.equal(state.recovery.error.message, `точная причина ${status}`);
    assert.equal(state.recovery.canRetry, false);
    assert.equal(state.recovery.fresh, true);
    assert.equal(editor.value, '<p>мой черновик</p>');
    assert.equal(server.code, '<p>сервер</p>');
  });
}

test('ошибка GET восстановления не скрывает исходный отказ политики', async () => {
  owner('<p>мой черновик</p>', { version: 4 });
  api.raw = async (path, opts = {}) => {
    throw Object.assign(new Error(opts.method === 'PUT' ? 'DENY' : 'нет связи'),
      { status: opts.method === 'PUT' ? 403 : 502 });
  };
  assert.equal(await flushSave(), false);
  assert.equal(state.recovery.error.message, 'DENY');
  assert.equal(state.recovery.fresh, false);
  assert.match(state.recovery.note, /нет связи/);
  assert.equal(state.recovery.canRetry, false);
});

test('одновременные сохранения объединяются до отправки второго запроса', async () => {
  const server = fakeServer(4);
  owner('<p>мой черновик</p>', { version: 4 });
  const raw = api.raw;
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  api.raw = async (path, opts = {}) => {
    if (opts.method === 'PUT') await gate;
    return raw(path, opts);
  };
  const first = flushSave();
  const second = flushSave();
  release();
  await Promise.all([first, second]);
  assert.equal(puts(server).length, 1);
  assert.equal(server.version, 5);
});


/* --------- A9-08: подделка сообщений пикера самим кадром превью ---------
   Кадр с sandbox="allow-scripts" волен увести СЕБЯ на чужую страницу: в
   Chromium `<meta http-equiv="refresh">` внутри превью уводит его, и страница
   атакующего шлёт parent.postMessage от имени пикера — окно то же самое, и
   прежняя проверка `ev.source === frame.contentWindow` её пропускала. */

const PREVIEW_WINDOW = { name: 'contentWindow кадра превью' };
const FRAME = { contentWindow: PREVIEW_WINDOW };
const NONCE = 'nonce-этой-загрузки';
/** Событие message так, как его отдаёт браузер: окно-отправитель + тело. */
const from = (window, data) => ({ source: window, data });
const select = (over = {}) => ({ source: 'bd-preview', type: 'select', nonce: NONCE,
  el: { tag: 'h1', bd_id: 'bd-2' }, ...over });

test('чужая страница внутри того же кадра пропуска не знает — сообщение отбито', () => {
  assert.equal(acceptsPickerMessage(from(PREVIEW_WINDOW, select({ nonce: undefined })),
                                    FRAME, NONCE), false);
  assert.equal(acceptsPickerMessage(from(PREVIEW_WINDOW, select({ nonce: 'угадал?' })),
                                    FRAME, NONCE), false);
});

test('положительный контроль: настоящий пикер по-прежнему принимается', () => {
  assert.equal(acceptsPickerMessage(from(PREVIEW_WINDOW, select()), FRAME, NONCE), true);
  assert.equal(acceptsPickerMessage(
    from(PREVIEW_WINDOW, { source: 'bd-preview', type: 'ready', nonce: NONCE }),
    FRAME, NONCE), true);
});

test('прежние проверки не потеряны: чужое окно и чужой отправитель', () => {
  assert.equal(acceptsPickerMessage(from({ name: 'чужое окно' }, select()), FRAME, NONCE), false);
  assert.equal(acceptsPickerMessage(from(PREVIEW_WINDOW, select({ source: 'кто-то ещё' })),
                                    FRAME, NONCE), false);
  assert.equal(acceptsPickerMessage(from(PREVIEW_WINDOW, null), FRAME, NONCE), false);
  // кадра ещё нет, либо панель не выдавала пропуск — принимать нечего
  assert.equal(acceptsPickerMessage(from(PREVIEW_WINDOW, select()), null, NONCE), false);
  assert.equal(acceptsPickerMessage(from(PREVIEW_WINDOW, select({ nonce: '' })), FRAME, ''), false);
});
