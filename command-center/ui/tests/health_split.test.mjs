import test from 'node:test';
import assert from 'node:assert/strict';
import { splitHealth } from '../health_split.js';

const coreOk = { db: { status: 'ok' }, queue_worker: { status: 'ok' }, scheduler: { status: 'ok' }, 'tick:healing': { status: 'ok' } };

test('live case: healthy core + dead cloud providers is NOT a failure', () => {
  const h = { ...coreOk, models: { status: 'error' }, providers: { status: 'error' }, 'provider:1': { status: 'error' },
    'provider:7': { status: 'stale' }, browser: { status: 'unknown' } };
  const r = splitHealth(h);
  assert.equal(r.overall, 'ok');
  assert.equal(r.optional.total, 5);
  assert.deepEqual(r.optional.notReady.sort(), ['browser', 'models', 'provider:1', 'provider:7', 'providers']);
});

test('negative control: a failed CORE component still turns the badge red', () => {
  assert.equal(splitHealth({ ...coreOk, db: { status: 'error' } }).overall, 'err');
  assert.deepEqual(splitHealth({ ...coreOk, scheduler: { status: 'offline' } }).core.bad, ['scheduler']);
});

test('core warnings stay warnings', () => {
  assert.equal(splitHealth({ ...coreOk, 'tick:healing': { status: 'stale' } }).overall, 'warn');
});

test('no-fake-green: empty or unknown-only health is a warning, an unknown component counts as core', () => {
  assert.equal(splitHealth({}).overall, 'warn');
  assert.equal(splitHealth(null).overall, 'warn');
  assert.equal(splitHealth({ ...coreOk, brand_new_component: { status: 'error' } }).overall, 'err');
});

test('array and plain-string shapes are understood', () => {
  assert.equal(splitHealth([{ name: 'db', status: 'ok' }, { name: 'provider:2', status: 'error' }]).overall, 'ok');
  assert.equal(splitHealth({ db: 'ok', 'provider:3': 'error' }).overall, 'ok');
});
