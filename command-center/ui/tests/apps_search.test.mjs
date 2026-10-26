import test from 'node:test';
import assert from 'node:assert/strict';

import { filterApps } from '../pages/appcards.js';

const apps = [
  { id: 'war-terminal', name: 'War Terminal', subtitle: 'Procurement watch',
    status: 'LIVE', lifecycle_status: 'active', facts: [{ label: 'Sector', value: 'EU' }] },
  { id: 'travel-architect', name: 'Travel Architect', subtitle: 'Trip comparison',
    status: 'STOPPED', lifecycle_status: 'active', actions: [{ label: 'Plan trip' }] },
  { id: 'solana-volume-suite', name: 'Solana Volume Suite', description: 'Quarantined package',
    status: 'STOPPED', lifecycle_status: 'quarantined', quarantine_reason: 'wash trading' },
];

test('app search covers names, descriptions, facts and actions case-insensitively', () => {
  assert.deepEqual(filterApps(apps, { query: 'PROCUREMENT' }).map((app) => app.id), ['war-terminal']);
  assert.deepEqual(filterApps(apps, { query: 'plan trip' }).map((app) => app.id), ['travel-architect']);
  assert.deepEqual(filterApps(apps, { query: 'eu' }).map((app) => app.id), ['war-terminal']);
});

test('status filters keep quarantined apps out of live and stopped operational lists', () => {
  assert.deepEqual(filterApps(apps, { status: 'running' }).map((app) => app.id), ['war-terminal']);
  assert.deepEqual(filterApps(apps, { status: 'stopped' }).map((app) => app.id), ['travel-architect']);
  assert.deepEqual(filterApps(apps, { status: 'quarantined' }).map((app) => app.id), ['solana-volume-suite']);
});

test('empty or unknown filters return all apps without mutating the input', () => {
  assert.deepEqual(filterApps(apps, {}), apps);
  assert.deepEqual(filterApps(apps, { status: 'unknown' }), apps);
  assert.equal(apps.length, 3);
});
