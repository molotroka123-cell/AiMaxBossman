import test from 'node:test';
import assert from 'node:assert/strict';
import { buildObjectiveSpec, OBJECTIVE_DRAFT } from '../pages/objectives.js';

const NOW_MS = 4_000_000_000_000;
const draft = (over = {}) => ({ ...OBJECTIVE_DRAFT, objective_id: 'nightly-build', ...over });

test('the wizard produces the exact spec shape the store validates', () => {
  const spec = buildObjectiveSpec(draft(), 'owner:ada', NOW_MS);
  assert.equal(spec.schema_version, 1);
  assert.equal(spec.owner_id, 'owner:ada');
  assert.equal(spec.objective_id, 'nightly-build');
  assert.equal(spec.revision, 1);
  assert.equal(spec.previous_digest, null);
  assert.deepEqual(spec.stop_conditions, ['owner-revocation']);
  // Условие успеха — БУЛЕВО, а не строка 'true' из переключателя формы: спека
  // с value_type boolean и строкой в expected хранилищем не принимается.
  assert.equal(spec.predicates[0].expected, true);
  assert.equal(spec.predicates[0].value_type, 'boolean');
  assert.equal(spec.predicates[0].source_ref, spec.sources[0].source_ref);
  assert.equal(buildObjectiveSpec(draft({ expected: 'false' }), 'o', NOW_MS)
    .predicates[0].expected, false);
});

test('the expiry is computed from the moment passed in, never from a hidden clock', () => {
  const spec = buildObjectiveSpec(draft({ expires_in_days: 7 }), 'o', NOW_MS);
  assert.equal(spec.expires_at, Math.floor(NOW_MS / 1000) + 7 * 86400);
});

test('form text becomes numbers, and an unusable entry falls back instead of NaN', () => {
  const spec = buildObjectiveSpec(draft({
    cooldown_seconds: '900', max_missions: '12', max_cost_usd: '0.5',
    max_wall_seconds: '', priority: 'сколько-нибудь',
  }), 'o', NOW_MS);
  assert.equal(spec.cooldown_seconds, 900);
  assert.equal(spec.limits.max_missions, 12);
  assert.equal(spec.limits.max_cost_usd, 0.5);
  // Пустое поле и мусор не должны стать NaN: спека с NaN не сериализуется в
  // JSON и отказ был бы про сериализацию, а не про то, что владелец ошибся.
  // И пустое поле — не ноль: Number('') равен нулю, поэтому очищенный «потолок
  // времени» молча становился бюджетом 0 с.
  assert.equal(spec.limits.max_wall_seconds, 600);
  assert.equal(spec.priority, 3);
  for (const value of Object.values(spec.limits)) assert.ok(Number.isFinite(value));
});

test('an explicitly typed zero is kept, so the server can refuse it by name', () => {
  const spec = buildObjectiveSpec(draft({ max_cost_usd: '0', max_missions: 0 }), 'o', NOW_MS);
  assert.equal(spec.limits.max_cost_usd, 0);
  assert.equal(spec.limits.max_missions, 0);
});

test('comma lists become clean arrays, and empty means no authority at all', () => {
  const spec = buildObjectiveSpec(draft({
    permission_refs: ' repo.read , repo.write ,, ', conflict_keys: 'project-build',
  }), 'o', NOW_MS);
  assert.deepEqual(spec.permission_refs, ['repo.read', 'repo.write']);
  assert.deepEqual(spec.conflict_keys, ['project-build']);
  assert.deepEqual(buildObjectiveSpec(draft(), 'o', NOW_MS).permission_refs, []);
});

test('the trigger list is exactly what was chosen, never a wider one', () => {
  assert.deepEqual(buildObjectiveSpec(draft(), 'o', NOW_MS).allowed_triggers, ['source_change']);
  assert.deepEqual(buildObjectiveSpec(draft({ trigger: 'scheduled' }), 'o', NOW_MS)
    .allowed_triggers, ['scheduled']);
});

test('whitespace never becomes an identifier, and the scope has a default', () => {
  const spec = buildObjectiveSpec(draft({ objective_id: '  ', scope_id: '   ' }), 'o', NOW_MS);
  assert.equal(spec.objective_id, '');        // сервер откажет и назовёт поле
  assert.equal(spec.scope_id, 'project');
});
