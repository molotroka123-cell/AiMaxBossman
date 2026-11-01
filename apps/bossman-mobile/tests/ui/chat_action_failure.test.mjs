import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { boot, closeAll, skip, until } from './_boot.mjs';

after(closeAll);

async function app({ turns = [], status = 'failed', error = 'action_contract/no_verified_action', lastRunResult = null, failureSummary = null, result = null, actionNotExecuted = null } = {}) {
  const streams = [];
  const a = await boot({
    before: (w) => {
      w.localStorage.setItem('bm.thread', 'T1');
      w.EventSource = class { constructor(url) { this.url = url; streams.push(this); } close() {} };
    },
    routes: (path, method) => {
      if (path === '/agents') return { items: [{ id: 1, name: 'Gemma', local: true, tools: [] }], default_agent_id: 1 };
      if (path === '/threads/T1' && method === 'GET') return { id: 'T1', turns };
      if (path === '/threads/T1/send') return { task: { id: 7 }, admission: { ok: true }, model: { alias: 'gemma' } };
      if (path === '/tasks/7') return { id: 7, status, result, error, action_not_executed: actionNotExecuted, failure_summary: failureSummary, runs: lastRunResult ? [{ result: lastRunResult }] : [] };
      return undefined;
    },
  });
  await until(() => a.calls.some((c) => c.path === '/threads/T1'), 'thread loaded');
  return { ...a, streams };
}

test('failed action displays explanation next to streamed answer', { skip }, async () => {
  const a = await app();
  a.$('input').value = 'Тестовое поручение';
  a.$('composer').dispatchEvent(new a.w.Event('submit', { cancelable: true, bubbles: true }));
  await until(() => a.streams.length > 0, 'stream opened');
  a.streams[0].onmessage({ data: JSON.stringify({ kind: 'run.answer_delta', text: 'Ответ модели' }) });
  a.streams[0].onmessage({ data: JSON.stringify({ kind: 'task.failed' }) });
  await until(() => a.d.querySelector('.action-failure'), 'failure explanation');
  assert.match(a.d.querySelector('.msg.bot .answer').textContent, /Ответ модели/);
  const notice = a.d.querySelector('.action-failure').textContent;
  assert.match(notice, /Действие не подтверждено/);
  assert.doesNotMatch(notice, /отклонён владельцем/);
});

test('failed action preserves last run response after missing the stream', { skip }, async () => {
  const a = await app({ lastRunResult: 'Частичный ответ модели' });
  a.$('input').value = 'Тестовое поручение';
  a.$('composer').dispatchEvent(new a.w.Event('submit', { cancelable: true, bubbles: true }));
  await until(() => a.streams.length > 0, 'stream opened');
  a.streams[0].onmessage({ data: JSON.stringify({ kind: 'task.failed' }) });
  await until(() => a.d.querySelector('.action-failure'), 'failure explanation');
  assert.match(a.d.querySelector('.msg.bot .answer').textContent, /Частичный ответ модели/);
  assert.match(a.d.querySelector('.action-failure').textContent, /Действие не подтверждено/);
});

test('failed action after lost stream restores response from mobile failure_summary', { skip }, async () => {
  const a = await app({ failureSummary: 'Частичный ответ модели\n\nДействие не подтверждено. Проверьте инструменты.' });
  a.$('input').value = 'Тестовое поручение';
  a.$('composer').dispatchEvent(new a.w.Event('submit', { cancelable: true, bubbles: true }));
  await until(() => a.streams.length > 0, 'stream opened');
  a.streams[0].onmessage({ data: JSON.stringify({ kind: 'task.failed' }) });
  await until(() => a.d.querySelector('.action-failure'), 'failure explanation');
  assert.match(a.d.querySelector('.msg.bot .answer').textContent, /Частичный ответ модели/);
  assert.equal((a.d.querySelector('.msg.bot').textContent.match(/Действие не подтверждено/g) || []).length, 1);
});

test('failed action explanation survives opening history with a model result', { skip }, async () => {
  const a = await app({ turns: [{ idx: 0, task_id: 7, text: 'учебная задача', status: 'failed', result: 'Ответ модели', error: 'action_contract/no_verified_action' }] });
  await until(() => a.d.querySelector('.msg.bot .answer'), 'history rendered');
  assert.match(a.d.querySelector('.msg.bot .answer').textContent, /Ответ модели/);
  assert.match(a.d.querySelector('.action-failure')?.textContent || '', /Действие не подтверждено/);
});

test('history preserves the partial result in failure_summary without duplicating the notice', { skip }, async () => {
  const summary = 'Ответ модели\n\nДействие не подтверждено. Проверьте инструменты и разрешения выбранного агента или выберите локального исполнителя с нужным инструментом.';
  const a = await app({ turns: [{ idx: 0, task_id: 7, text: 'учебная задача', status: 'failed', result: null, error: 'action_contract/no_verified_action', failure_summary: summary }] });
  await until(() => a.d.querySelector('.action-failure'), 'history notice rendered');
  assert.equal(a.d.querySelector('.msg.bot .answer').textContent, 'Ответ модели');
  assert.equal((a.d.querySelector('.msg.bot').textContent.match(/Действие не подтверждено/g) || []).length, 1);
});

test('completed tasks do not show a failed-action warning', { skip }, async () => {
  const a = await app({ turns: [{ idx: 0, task_id: 7, text: 'вопрос', status: 'completed', result: 'Готово', error: 'action_contract/no_verified_action' }] });
  await until(() => a.d.querySelector('.msg.bot .answer'), 'history rendered');
  assert.equal(a.d.querySelector('.action-failure'), null);
});

test('completed text-only reply shows unperformed action in history', { skip }, async () => {
  const a = await app({ turns: [{ idx: 0, task_id: 7, text: 'учебная задача', status: 'completed', result: 'Текстовый ответ', action_not_executed: ['CODE_ACTION'] }] });
  await until(() => a.d.querySelector('.msg.bot .answer'), 'history rendered');
  assert.match(a.d.querySelector('.msg.bot').textContent, /действие инструментом не выполнялось/);
  assert.equal(a.d.querySelector('.action-failure'), null);
});

test('completed text-only reply shows unperformed action immediately', { skip }, async () => {
  const a = await app({ status: 'completed', error: null, result: 'Текстовый ответ', actionNotExecuted: ['CODE_ACTION'] });
  a.$('input').value = 'Тестовое поручение';
  a.$('composer').dispatchEvent(new a.w.Event('submit', { cancelable: true, bubbles: true }));
  await until(() => a.streams.length > 0, 'stream opened');
  a.streams[0].onmessage({ data: JSON.stringify({ kind: 'task.completed' }) });
  await until(() => a.d.querySelector('.msg.bot')?.getAttribute('aria-busy') === 'false', 'task settled');
  assert.match(a.d.querySelector('.msg.bot').textContent, /действие инструментом не выполнялось/);
  assert.equal(a.d.querySelector('.action-failure'), null);
});

test('normal completed reply does not claim an unperformed action', { skip }, async () => {
  const a = await app({ status: 'completed', error: null, result: 'Ответ' });
  a.$('input').value = 'Тестовый вопрос';
  a.$('composer').dispatchEvent(new a.w.Event('submit', { cancelable: true, bubbles: true }));
  await until(() => a.streams.length > 0, 'stream opened');
  a.streams[0].onmessage({ data: JSON.stringify({ kind: 'task.completed' }) });
  await until(() => a.d.querySelector('.msg.bot')?.getAttribute('aria-busy') === 'false', 'task settled');
  assert.doesNotMatch(a.d.querySelector('.msg.bot').textContent, /действие инструментом не выполнялось/);
});

test('a timeout is not classified from a code mentioned in a failed answer', { skip }, async () => {
  const a = await app({ error: 'provider timeout', failureSummary: 'Пример кода no_verified_action из ответа' });
  a.$('input').value = 'Тестовое поручение';
  a.$('composer').dispatchEvent(new a.w.Event('submit', { cancelable: true, bubbles: true }));
  await until(() => a.streams.length > 0, 'stream opened');
  a.streams[0].onmessage({ data: JSON.stringify({ kind: 'task.failed' }) });
  await until(() => a.d.querySelector('.msg.bot')?.getAttribute('aria-busy') === 'false', 'task settled');
  assert.equal(a.d.querySelector('.action-failure'), null);
});

test('history does not classify a timeout from a code mentioned in a failed answer', { skip }, async () => {
  const a = await app({ turns: [{ idx: 0, task_id: 7, text: 'вопрос', status: 'failed', result: null, error: 'provider timeout', failure_summary: 'Пример кода no_verified_action из ответа' }] });
  await until(() => a.d.querySelector('.msg.bot .answer'), 'history rendered');
  assert.equal(a.d.querySelector('.action-failure'), null);
});
