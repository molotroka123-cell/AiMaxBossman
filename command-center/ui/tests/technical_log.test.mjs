import test from 'node:test';
import assert from 'node:assert/strict';
import { technicalLogEvent } from '../chat/format.js';
import { collectTechnicalLogForTurn } from '../chat/technical-log.js';

test('technical diagnostics reject content in numeric and boolean fields', () => {
  const raw = { task_id: 'private user text', run_id: 'sk-fakecredential123456789',
    duration_ms: 'tool result', ok: 'answer text', count: 4, pricing_known: false };
  assert.deepEqual(technicalLogEvent(raw), {
    event: { count: 4, pricing_known: false }, omittedFields: 4,
  });
});

test('collector paginates task and run events and preserves diagnostic IDs only', async () => {
  const requests = [];
  const fetchRaw = async (path) => {
    requests.push(path);
    if (path === '/api/tasks/7') return { task: { id: 7 }, runs: [{ id: 9, task_id: 7 }] };
    if (path.startsWith('/api/tasks/7/events')) {
      const next = path.includes('after=0') ? 1 : 2;
      return { task_id: 7, events: [{ seq: next, task_id: 7, message: 'private prompt' }], cursor: next, more: next === 1 };
    }
    if (path === '/api/runs/9/events?after=0&limit=1000') {
      return Array.from({ length: 1000 }, (_, i) => ({ id: i + 1, run_id: 9, data: { args: 'private tool args' } }));
    }
    if (path === '/api/runs/9/events?after=1000&limit=1000') return [{ id: 1001, run_id: 9, message: 'private answer' }];
    throw new Error('unexpected request');
  };
  const result = await collectTechnicalLogForTurn({ taskId: 7, status: 'completed', model: 'local-qwen' }, 0, fetchRaw);
  assert.deepEqual(result.failures, []);
  assert.equal(result.record.truncated, false);
  assert.deepEqual(result.record.task_events.map((event) => event.seq), [1, 2]);
  assert.equal(result.record.run_events.length, 1001);
  assert.equal(requests.length, 5);
  assert.doesNotMatch(JSON.stringify(result), /private|prompt|answer|args/);
});

test('collector records partial network failure and keeps the successful first page', async () => {
  const result = await collectTechnicalLogForTurn({ taskId: 7 }, 0, async (path) => {
    if (path === '/api/tasks/7') return { task: { id: 7 }, runs: [] };
    if (path.includes('after=0')) return { task_id: 7, events: [{ seq: 1 }], cursor: 1, more: true };
    throw Object.assign(new Error('private server exception'), { status: 503 });
  });
  assert.deepEqual(result.record.task_events, [{ seq: 1 }]);
  assert.deepEqual(result.failures, [{ source: 'task_events', code: 503 }]);
  assert.doesNotMatch(JSON.stringify(result), /private|exception/);
});

test('collector does not treat malformed pages or a stuck cursor as a complete export', async () => {
  for (const malformed of [null, {}, { events: [], cursor: 0, more: true },
    { events: [{ seq: 1 }], cursor: 0, more: true },
    { events: [{ seq: 2 }, { seq: 1 }], cursor: 1, more: false }]) {
    const result = await collectTechnicalLogForTurn({ taskId: 7, runIds: [9] }, 0, async (path) => {
      if (path === '/api/tasks/7') return { task: { id: 7 }, runs: [{ id: 9, task_id: 7 }] };
      return malformed && !Array.isArray(malformed) ? { task_id: 7, ...malformed } : malformed;
    });
    assert.ok(result.failures.some((failure) => failure.source === 'task_events' && failure.code === 'INVALID_RESPONSE'));
    assert.ok(result.failures.some((failure) => failure.source === 'run_events:9' && failure.code === 'INVALID_RESPONSE'));
  }
});

test('collector bounds pagination and marks omitted data', async () => {
  let requests = 0;
  const result = await collectTechnicalLogForTurn({ taskId: 7 }, 0, async (path) => {
    requests += 1;
    if (path === '/api/tasks/7') return { task: { id: 7 }, runs: [{ id: 9, task_id: 7 }] };
    const start = Number(new URL(path, 'http://test').searchParams.get('after'));
    if (path.startsWith('/api/tasks')) return { task_id: 7, events: [{ seq: start + 1 }], cursor: start + 1, more: true };
    return Array.from({ length: 1000 }, (_, i) => ({ id: start + i + 1, run_id: 9 }));
  });
  assert.equal(requests, 5);
  assert.equal(result.record.truncated, true);
  assert.equal(result.record.run_events.length, 2000);
});

test('collector rejects invalid identity and redacts top level metadata', async () => {
  let called = false;
  const invalid = await collectTechnicalLogForTurn({ taskId: 'private text' }, 0, async () => { called = true; });
  assert.equal(called, false);
  assert.equal(invalid.failures[0].code, 'INVALID_TASK_ID');
  const result = await collectTechnicalLogForTurn({ taskId: 7, status: 'private user text', model: 'sk-fakecredential123456789' }, 0,
    async (path) => path === '/api/tasks/7' ? { task: { id: 7 }, runs: [] } : { task_id: 7, events: [], cursor: 0, more: false });
  assert.equal(result.record.status, null);
  assert.equal(result.record.model, null);
  assert.doesNotMatch(JSON.stringify(result), /private|fakecredential/);
});

test('technical diagnostics validate the entire identifier before export', () => {
  for (const field of ['kind', 'level', 'status', 'tool', 'source', 'model', 'code', 'error_code', 'approval_kind']) {
    for (const value of ['C:/Users/private/document.txt', 'https://private.example/key',
      'github_pat_fakecredential123456789', 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.fakeSignature']) {
      assert.deepEqual(technicalLogEvent({ [field]: value }).event, {}, `${field}: ${value}`);
    }
  }
});

test('technical timestamps retain backend naive UTC and reject impossible dates', () => {
  for (const value of ['2026-10-02T12:00:00', '2026-10-02T12:00:00.123456']) {
    assert.deepEqual(technicalLogEvent({ ts: value }), { event: { ts: `${value}Z` }, omittedFields: 0 });
  }
  for (const value of ['2026-10-02T12:00:00Z', '2026-10-02T12:00:00.123456+03:00']) {
    assert.equal(technicalLogEvent({ ts: value }).event.ts, value);
  }
  for (const value of ['2026-02-30T12:00:00', '2026-10-02T24:00:00', '2026-10-02T12:00:00 private']) {
    assert.deepEqual(technicalLogEvent({ ts: value }).event, {});
  }
});

test('technical numeric fields enforce positive IDs and nonnegative counts and measures', () => {
  for (const field of ['seq', 'id', 'task_id', 'run_id', 'approval_id']) {
    for (const value of [0, -1, 1.5, Number.MAX_SAFE_INTEGER + 1]) {
      assert.deepEqual(technicalLogEvent({ [field]: value }).event, {});
    }
    assert.equal(technicalLogEvent({ [field]: 1 }).event[field], 1);
  }
  for (const field of ['step', 'count', 'tokens_in', 'tokens_out', 'context_window']) {
    for (const value of [-1, 1.5, Number.MAX_SAFE_INTEGER + 1]) {
      assert.deepEqual(technicalLogEvent({ [field]: value }).event, {});
    }
    assert.equal(technicalLogEvent({ [field]: 0 }).event[field], 0);
  }
  for (const field of ['duration_ms', 'elapsed_ms', 'cost_usd', 'max_cost_usd']) {
    for (const value of [-1, NaN, Infinity]) assert.deepEqual(technicalLogEvent({ [field]: value }).event, {});
    assert.equal(technicalLogEvent({ [field]: 0.125 }).event[field], 0.125);
  }
});

test('technical strings exclude sources, relative paths and additional credential patterns', () => {
  assert.deepEqual(technicalLogEvent({ source: 'internal' }).event, {});
  for (const field of ['kind', 'level', 'status', 'tool', 'source', 'model', 'code', 'error_code', 'approval_kind']) {
    for (const value of ['Users/alice/private.txt', 'alice/private.txt', 'home/private',
      'AIzaSy000000000000000000000000000000000', 'AKIA0000000000000000']) {
      assert.deepEqual(technicalLogEvent({ [field]: value }).event, {}, `${field}: ${value}`);
    }
  }
  assert.equal(technicalLogEvent({ model: 'Qwen/Qwen3.5-35B-A3B:latest' }).event.model, 'Qwen/Qwen3.5-35B-A3B:latest');
});

test('collector rejects wrong summary identity and never fetches unverified UI run IDs', async () => {
  for (const summary of [{ runs: [] }, { task: { id: 8 }, runs: [] },
    { task: { id: 7 }, runs: [{ id: 9, task_id: 8 }] },
    { task: { id: 7 }, runs: [{ id: 9, task_id: 7 }, { id: 9, task_id: 7 }] }]) {
    const paths = [];
    const result = await collectTechnicalLogForTurn({ taskId: 7, runIds: [99], run: { id: 98 } }, 0, async (path) => {
      paths.push(path);
      return path === '/api/tasks/7' ? summary : { task_id: 7, events: [{ seq: 1, task_id: 7 }], cursor: 1, more: false };
    });
    assert.deepEqual(result.failures, [{ source: 'task_summary', code: 'INVALID_RESPONSE' }]);
    assert.deepEqual(result.record.run_ids, []);
    assert.equal(result.record.task_events.length, 1);
    assert.equal(paths.some((path) => path.startsWith('/api/runs/')), false);
  }
});

test('collector rejects mismatched task envelope and event links', async () => {
  for (const page of [
    { task_id: 8, events: [{ seq: 1 }], cursor: 1, more: false },
    { task_id: 7, events: [{ seq: 1, task_id: 8 }], cursor: 1, more: false },
    { task_id: 7, events: [{ seq: 1, run_id: 10 }], cursor: 1, more: false },
    { task_id: 7, events: [{ seq: 1, run_id: 1.5 }], cursor: 1, more: false },
  ]) {
    const result = await collectTechnicalLogForTurn({ taskId: 7 }, 0, async (path) => {
      if (path === '/api/tasks/7') return { task: { id: 7 }, runs: [{ id: 9, task_id: 7 }] };
      return path.startsWith('/api/tasks/') ? page : [];
    });
    assert.deepEqual(result.failures, [{ source: 'task_events', code: 'INVALID_RESPONSE' }]);
    assert.deepEqual(result.record.task_events, []);
  }
});

test('collector validates run row ownership and preserves earlier good pages', async () => {
  for (const row of [{ id: 1001 }, { id: 1001, run_id: 10 }, { id: 1001, run_id: 9, task_id: 8 }]) {
    const result = await collectTechnicalLogForTurn({ taskId: 7 }, 0, async (path) => {
      if (path === '/api/tasks/7') return { task: { id: 7 }, runs: [{ id: 9, task_id: 7 }] };
      if (path.startsWith('/api/tasks/')) return { task_id: 7, events: [{ seq: 1, run_id: 9 }], cursor: 1, more: false };
      return path.includes('after=0') ? Array.from({ length: 1000 }, (_, i) => ({ id: i + 1, run_id: 9 })) : [row];
    });
    assert.deepEqual(result.failures, [{ source: 'run_events:9', code: 'INVALID_RESPONSE' }]);
    assert.equal(result.record.run_events.length, 1000);
    assert.equal(result.record.task_events[0].run_id, 9);
  }
});

test('collector bounds run selection and cumulative events across runs', async () => {
  const requestedRuns = [];
  const result = await collectTechnicalLogForTurn({ taskId: 7 }, 0, async (path) => {
    if (path === '/api/tasks/7') return { task: { id: 7 }, runs: Array.from({ length: 12 }, (_, i) => ({ id: i + 1, task_id: 7 })) };
    if (path.startsWith('/api/tasks/')) return { task_id: 7, events: [], cursor: 0, more: false };
    const runId = Number(path.split('/')[3]);
    const params = new URL(path, 'http://test').searchParams;
    const after = Number(params.get('after'));
    const limit = Number(params.get('limit'));
    requestedRuns.push(runId);
    return Array.from({ length: limit }, (_, i) => ({ id: after + i + 1, run_id: runId }));
  });
  assert.deepEqual(result.record.run_ids, [3, 4, 5, 6, 7, 8, 9, 10, 11, 12]);
  assert.deepEqual(requestedRuns, [3, 3, 4, 4, 5]);
  assert.equal(result.record.run_events.length, 5000);
  assert.equal(result.record.truncated, true);
  assert.deepEqual(result.failures, []);
});

test('collector keeps task events on summary failure and permits approval events without task links', async () => {
  const result = await collectTechnicalLogForTurn({ taskId: 7, runIds: [99] }, 0, async (path) => {
    if (path === '/api/tasks/7') throw Object.assign(new Error('synthetic'), { status: 503 });
    assert.ok(path.startsWith('/api/tasks/7/events'));
    return { task_id: 7, events: [{ seq: 1, id: 4, kind: 'approval.created', ts: '2026-10-02T12:00:00.123456' }], cursor: 1, more: false };
  });
  assert.deepEqual(result.failures, [{ source: 'task_summary', code: 503 }]);
  assert.equal(result.record.task_events[0].ts, '2026-10-02T12:00:00.123456Z');
  assert.deepEqual(result.record.run_events, []);
});
