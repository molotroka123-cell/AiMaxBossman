// Обход кнопок 01.10.2026: серверные коды проверки не показываются владельцу как есть.
// Запуск: node --test --experimental-detect-module command-center/ui/tests/buttons_sweep_chat.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';

const { verdictLabel } = await import('../chat/format.js');

test('verdictLabel: the three server verdict codes become Russian words', () => {
  assert.equal(verdictLabel('PASS'), 'пройдена');
  assert.equal(verdictLabel('FAIL'), 'не пройдена');
  assert.equal(verdictLabel('NOT_APPLICABLE'), 'не требуется');
  assert.equal(verdictLabel('not_applicable'), 'не требуется');
});

test('verdictLabel: an unknown code is shown as is (never hidden), an empty one as a dash', () => {
  assert.equal(verdictLabel('QUARANTINED'), 'QUARANTINED');
  assert.equal(verdictLabel(''), '—');
  assert.equal(verdictLabel(undefined), '—');
});

const { createTurn, applyEvent, rungLabel, approvalLabel } = await import('../chat/state.js');

test('a retry note says what the recovery rung means, not the server code retry_same', () => {
  const turn = createTurn({ taskId: 7, text: 'x' });
  applyEvent(turn, { kind: 'task.queued', retry: true, attempt: 1, run_id: 3, recovery_rung: 'retry_same' });
  const [note] = [...turn.notes.values()];
  assert.ok(note, 'the retry note exists');
  assert.doesNotMatch(note.detail, /retry_same/);
  assert.match(note.detail, /повтор того же маршрута/);
  assert.equal(rungLabel('alternate_model'), 'другая модель');
  assert.equal(rungLabel('something_new'), 'something_new', 'an unknown code is shown, never hidden');
});

test('approval statuses are words in the Thinking panel', () => {
  assert.equal(approvalLabel('pending'), 'ждёт');
  assert.equal(approvalLabel('approved'), 'разрешено');
  assert.equal(approvalLabel('rejected'), 'отклонено');
  assert.equal(approvalLabel('denied'), 'отклонено');
  assert.equal(approvalLabel(''), '—');
});
