// UX-07: честный отказ (пустое поле, 4xx с текстом) — не console.error; сбой программы — console.error.
// Запуск: node --test --experimental-detect-module command-center/ui/tests/ux_sweep_toast_error.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';

globalThis.document = { getElementById: () => null };   // тост без корня — no-op, но путь логирования проходит
const { toastError, isExpectedRefusal } = await import('../components.js');

function capture(fn) {
  const calls = { error: [], warn: [] };
  const saved = { error: console.error, warn: console.warn };
  console.error = (...a) => calls.error.push(a);
  console.warn = (...a) => calls.warn.push(a);
  try { fn(); } finally { console.error = saved.error; console.warn = saved.warn; }
  return calls;
}

test('a local validation refusal is a warning, not a console error', () => {
  const calls = capture(() => toastError(new Error('Команда пустая'), 'Введите команду'));
  assert.equal(calls.error.length, 0);
  assert.equal(calls.warn.length, 1);
});

test('a plain {message, hint} object (openrouter empty key) is a warning', () => {
  const calls = capture(() => toastError({ message: 'Вставьте ключ', hint: 'без ключа подключаться нечем' }));
  assert.equal(calls.error.length, 0);
  assert.equal(calls.warn.length, 1);
});

test('a 4xx API refusal with a human message is a warning', () => {
  const err = Object.assign(new Error('Нет настроенного исполнителя для задачи.'), { status: 409, hint: 'Откройте «Агенты»' });
  assert.equal(isExpectedRefusal(err), true);
  const calls = capture(() => toastError(err));
  assert.equal(calls.error.length, 0);
  assert.equal(calls.warn.length, 1);
});

test('5xx, dropped network (status 0) and program errors stay console errors (negative control)', () => {
  for (const err of [
    Object.assign(new Error('Сервер упал'), { status: 500 }),
    Object.assign(new Error('Нет связи'), { status: 0 }),
    new TypeError('x is not a function'),
    new ReferenceError('y is not defined'),
  ]) {
    assert.equal(isExpectedRefusal(err), false, String(err));
    const calls = capture(() => toastError(err));
    assert.equal(calls.error.length, 1, String(err));
    assert.equal(calls.warn.length, 0, String(err));
  }
});

test('a value with no message is not treated as an expected refusal', () => {
  assert.equal(isExpectedRefusal(null), false);
  assert.equal(isExpectedRefusal(undefined), false);
  assert.equal(isExpectedRefusal('строка'), false);
  assert.equal(isExpectedRefusal({}), false);
});
