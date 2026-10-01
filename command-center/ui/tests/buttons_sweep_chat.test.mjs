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
