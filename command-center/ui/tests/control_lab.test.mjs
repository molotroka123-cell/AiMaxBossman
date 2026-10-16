import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { capabilityCounts, isKnownNumber, projectRoadmap, ROADMAP_END } from '../pages/control_lab_state.js';

test('roadmap scenarios are local arithmetic bounded by date, budget and speed', () => {
  const now = Date.parse('2026-10-16T00:00:00Z');
  const scenarios = projectRoadmap({ now, budget: 200, speed: 2 });
  assert.equal(scenarios.length, 3);
  assert.ok(scenarios.every((s) => s.days === Math.ceil((Date.parse(ROADMAP_END) - now) / 86400000)));
  assert.deepEqual(scenarios.map((s) => s.id), ['cautious', 'balanced', 'accelerated']);
  assert.ok(scenarios[0].workUnits <= scenarios[1].workUnits);
  assert.ok(scenarios[1].workUnits <= scenarios[2].workUnits);
  assert.ok(scenarios.every((s) => s.spendCap <= 200 && s.workUnits <= s.spendCap / 2));
  assert.equal(projectRoadmap({ now: Date.parse(ROADMAP_END), budget: 100, speed: 4 })[2].workUnits, 0);
});

test('capability counts preserve source labels without promoting a status', () => {
  assert.deepEqual(capabilityCounts([{ status: 'code' }, { status: 'reported' }, { status: 'blocked' }, {}]),
    { code: 1, reported: 1, blocked: 1, unknown: 1 });
});

test('unknown telemetry is distinguishable from measured zero', () => {
  assert.equal(isKnownNumber(0), true);
  assert.equal(isKnownNumber(null), false);
  assert.equal(isKnownNumber(undefined), false);
  assert.equal(isKnownNumber('N/A'), false);
});

test('Control Lab source contains read-only API calls and no mutation method', () => {
  const source = fs.readFileSync(new URL('../pages/control_lab.js', import.meta.url), 'utf8');
  assert.match(source, /api\.raw\(url\)/);
  assert.match(source, /\/api\/capability-tree/);
  assert.match(source, /\/api\/system/);
  assert.match(source, /\/api\/tasks\?limit=100/);
  assert.match(source, /\/api\/agents/);
  assert.match(source, /\/api\/spend/);
  assert.match(source, /\/api\/resources/);
  assert.doesNotMatch(source, /method\s*:\s*['"](?:POST|PUT|PATCH|DELETE)['"]/i);
});
