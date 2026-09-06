import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
const text = await readFile(new URL('../../ui/mission_state.js', import.meta.url), 'utf8');
const { missionControls, continuityLabel, progressFraction } = await import(`data:text/javascript;base64,${Buffer.from(text).toString('base64')}`);

test('terminal missions never offer restart/resume', () => {
  for (const status of ['completed', 'failed', 'cancelled', 'stopped', 'unknown'])
    assert.deepEqual(missionControls(status), []);
});
test('paused and planning follow backend transitions', () => {
  assert.deepEqual(missionControls('paused'), ['resume', 'stop']);
  assert.deepEqual(missionControls('planning'), ['stop']);
  assert.ok(missionControls('queued').includes('pause'));
});
test('dependency identity is visible without invented success', () => {
  assert.match(continuityLabel({reason_code:'DEPENDENCIES_PENDING',waiting_for:[4, 8]}), /#4, #8/);
  assert.match(continuityLabel({reason_code:'READY'}), /не доказывает результат/);
});
test('missing/future states never render as healthy', () => {
  assert.match(continuityLabel(null), /неизвестно/);
  assert.match(continuityLabel({reason_code:'FUTURE_STATE'}), /заблокирован/);
});
test('invalid progress never produces NaN or over-100-percent progress', () => {
  for (const value of [NaN, Infinity, 'bad']) assert.equal(progressFraction(value), 0);
  assert.equal(progressFraction(2), 1);
  assert.equal(progressFraction(-1), 0);
  assert.equal(progressFraction(.4), .4);
});
