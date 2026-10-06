import { technicalLogEvent } from './format.js';

const PAGE_SIZE = 1000;
const MAX_PAGES = 2;
const MAX_RUNS = 10;
const MAX_RUN_EVENTS = 5000;

function positiveId(value) {
  if (typeof value === 'string' && !/^[1-9]\d*$/.test(value)) return null;
  if (typeof value !== 'string' && typeof value !== 'number') return null;
  const id = Number(value);
  return Number.isSafeInteger(id) && id > 0 ? id : null;
}

function failureCode(error) {
  const status = error?.status;
  return Number.isInteger(status) && status >= 100 && status <= 599 ? status : 'FETCH_FAILED';
}

function orderedRows(rows, key, cursor, limit) {
  if (!Array.isArray(rows) || rows.length > limit) return false;
  let previous = cursor;
  for (const row of rows) {
    if (!row || typeof row !== 'object' || Array.isArray(row)
        || !Number.isSafeInteger(row[key]) || row[key] <= previous) return false;
    previous = row[key];
  }
  return true;
}

// Fetch only from the selected backend. The caller owns download/UI; keeping this
// collector independent of the DOM lets failure and pagination tests use fake data.
export async function collectTechnicalLogForTurn(turn, turnIndex, fetchRaw) {
  const metadata = technicalLogEvent({ status: turn.status, model: turn.model });
  const record = { turn: turnIndex + 1, task_id: positiveId(turn.taskId),
    status: metadata.event.status ?? null, model: metadata.event.model ?? null,
    run_ids: [], task_events: [], run_events: [], truncated: false };
  const failures = [];
  let omittedFields = metadata.omittedFields;
  if (record.task_id === null) {
    return { record, failures: [{ source: 'task_summary', code: 'INVALID_TASK_ID' }], omittedFields };
  }
  const runIds = new Set();
  let summaryValid = false;
  try {
    const task = await fetchRaw(`/api/tasks/${record.task_id}`);
    if (task?.task?.id !== record.task_id || !Array.isArray(task.runs)
        || task.runs.some((run) => !Number.isSafeInteger(run?.id) || run.id <= 0 || run.task_id !== record.task_id)
        || new Set(task.runs.map((run) => run.id)).size !== task.runs.length) {
      failures.push({ source: 'task_summary', code: 'INVALID_RESPONSE' });
    } else {
      summaryValid = true;
      for (const run of task.runs) runIds.add(run.id);
    }
  } catch (error) {
    failures.push({ source: 'task_summary', code: failureCode(error) });
  }

  function append(rows, destination) {
    for (const raw of rows) {
      const safe = technicalLogEvent(raw);
      destination.push(safe.event);
      omittedFields += safe.omittedFields;
    }
  }

  let cursor = 0;
  for (let page = 0; page < MAX_PAGES; page += 1) {
    try {
      const result = await fetchRaw(`/api/tasks/${record.task_id}/events?after=${cursor}&limit=${PAGE_SIZE}`);
      if (!orderedRows(result?.events, 'seq', cursor, PAGE_SIZE)
          || result.task_id !== record.task_id
          || result.events.some((event) => (event.task_id != null && event.task_id !== record.task_id)
            || (event.run_id != null && (!Number.isSafeInteger(event.run_id) || event.run_id <= 0
              || (summaryValid && !runIds.has(event.run_id)))))
          || typeof result.more !== 'boolean'
          || result.cursor !== (result.events.at(-1)?.seq ?? cursor)
          || (result.more && result.cursor <= cursor)) {
        failures.push({ source: 'task_events', code: 'INVALID_RESPONSE' });
        break;
      }
      append(result.events, record.task_events);
      if (!result.more) break;
      if (page === MAX_PAGES - 1) { record.truncated = true; break; }
      cursor = result.cursor;
    } catch (error) {
      failures.push({ source: 'task_events', code: failureCode(error) });
      break;
    }
  }

  const orderedRunIds = [...runIds].sort((a, b) => a - b);
  if (orderedRunIds.length > MAX_RUNS) record.truncated = true;
  record.run_ids = orderedRunIds.slice(-MAX_RUNS);
  for (const runId of record.run_ids) {
    let after = 0;
    for (let page = 0; page < MAX_PAGES; page += 1) {
      const remaining = MAX_RUN_EVENTS - record.run_events.length;
      if (remaining <= 0) { record.truncated = true; break; }
      const limit = Math.min(PAGE_SIZE, remaining);
      try {
        const rows = await fetchRaw(`/api/runs/${runId}/events?after=${after}&limit=${limit}`);
        if (!orderedRows(rows, 'id', after, limit)
            || rows.some((row) => row.run_id !== runId
              || (row.task_id != null && row.task_id !== record.task_id))) {
          failures.push({ source: `run_events:${runId}`, code: 'INVALID_RESPONSE' });
          break;
        }
        append(rows, record.run_events);
        if (rows.length < limit) break;
        // The run endpoint has no 'more' field; a full final page cannot prove completeness.
        if (page === MAX_PAGES - 1 || record.run_events.length >= MAX_RUN_EVENTS) {
          record.truncated = true;
          break;
        }
        after = rows.at(-1).id;
      } catch (error) {
        failures.push({ source: `run_events:${runId}`, code: failureCode(error) });
        break;
      }
    }
  }
  return { record, failures, omittedFields };
}
