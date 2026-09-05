"""Reproduce the final owner-export analysis without joining unrelated requests.

Run with Python 3.11+ from the repository root. No network or model calls.
Line numbers identify source records, not application correlation IDs.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

SOURCE = 'docs/testing/sessions/2026-09-05_173815__0ded40f7389f.jsonl'
UNKNOWN = 'UNKNOWN'
INCIDENTS = {'http.error', 'task.failed', 'ui.dead_click', 'ui.refused', 'ui.rage_click'}
FIELDS = ['source_line', 'timestamp', 'embedded_session', 'kind', 'ui_action', 'handler',
          'request', 'status', 'task_id', 'run_id', 'previous_state', 'new_state',
          'visible_ui_result', 'expected_result', 'classification', 'confidence',
          'root_cause', 'fix_status', 'regression_test', 'correlation']


def diagnosis(event):
    d, kind = event['data'], event['kind']
    element, path = d.get('element', ''), d.get('path', '')
    if kind == 'task.failed':
        return ('BACKEND_FAILURE', 'HIGH', 'Task created with agent_id=null then queued as run 9; '
                'commandBlock canRun checks existence but omits agent_id from POST body.',
                'UI_FIXED_BACKEND_AGENT5_RETEST_REQUIRED', 'test_command_requires_explicit_executor_and_sends_selected_id',
                'RUNNABLE_TASK -> RESOLVED_EXECUTOR; otherwise BLOCKED_CAPABILITY_UNAVAILABLE', 'commandBlock /api/tasks')
    if 'bcc-testing-publish' in element:
        return ('FALSE_POSITIVE_TELEMETRY', 'MEDIUM', 'Current publish handler updates button text/disabled outside #view; '
                'detector ignores those changes and excludes testing requests. Historical visible outcome unrecorded.',
                'DETECTOR_FIXED_CURRENT_BRANCH', 'test_visible_feedback_outside_view_is_not_dead[text/busy]',
                'Busy feedback then published SHA or explicit refusal', 'testing.js:publish')
    if 'think-close' in element:
        return ('FALSE_POSITIVE_TELEMETRY', 'MEDIUM', 'setOpen(false) changes hidden on ancestor outside #view; '
                'old detector misses attributes. Historical visible outcome unrecorded.',
                'DETECTOR_FIXED_CURRENT_BRANCH', 'test_visible_feedback_outside_view_is_not_dead[ancestor]',
                'Thinking pane hidden', 'thinking.js:setOpen(false)')
    if kind == 'ui.dead_click':
        return ('FALSE_POSITIVE_TELEMETRY', 'MEDIUM', 'Quick-command handler sets input.value and focuses input; '
                'old detector sees neither. Historical visible outcome unrecorded.',
                'DETECTOR_FIXED_CURRENT_BRANCH', 'test_visible_feedback_outside_view_is_not_dead[focus]',
                'Command input populated; no automatic submission', 'mission_console.js:QUICK onClick')
    if kind == 'ui.rage_click':
        return ('UX_AMBIGUITY', 'LOW', 'Three clicks on All apps detected; no action/request IDs or visible state snapshots. '
                'Repeated navigation may be intentional; no missing handler established.',
                'UNPROVEN', 'NOT_RUN', 'Apps view visible or explicit feedback', 'apps.js navigation control')
    if d.get('status') == 404 and '/browser/sessions/' in path:
        return ('STALE_UI', 'HIGH', 'Polling a browser session absent from server; repeated historical 404s.',
                'HISTORICAL_FIX_PRESENT_FRESH_RETEST_AGENT7', 'test_browser_navigation_ui.py',
                'Explain missing session and stop stale polling', 'browser.js session polling')
    if d.get('status') == 403:
        return ('UX_AMBIGUITY', 'LOW', '403 is recorded without response body/code; CSRF expiry versus approval refusal '
                'cannot be distinguished from this record alone. Historical acceptance documents both fixes.',
                'HISTORICAL_FIX_PRESENT_FRESH_RETEST_AGENT7', 'test_browser_navigation_ui.py',
                'Explicit action refusal; reauthentication only for csrf/auth code', UNKNOWN)
    return ('BACKEND_FAILURE', 'MEDIUM', 'Browser fetch failed with status 0; transport unavailable from browser. '
            'Server restart, network failure, and client abort are not distinguished.',
            'UNPROVEN', 'NOT_RUN', 'Offline/reconnecting state visible; safe retry', UNKNOWN)


def analyze(source: Path, out: Path, tag: str):
    raw = source.read_bytes()
    events = [json.loads(line) for line in raw.decode('utf-8-sig').splitlines() if line.strip()]
    counts = Counter(e['kind'] for e in events)
    expected = {'http.error': 8, 'task.failed': 1, 'ui.dead_click': 10, 'ui.refused': 15, 'ui.rage_click': 1}
    if len(events) != 2788 or any(counts[k] != v for k, v in expected.items()):
        raise ValueError(f'Unexpected export; refuse stale/truncated/concatenated prefix: {len(events)}, {counts}')
    out.mkdir(parents=True, exist_ok=True)
    incidents, lifecycle, states = [], [], {}
    transitions = {'created': 'created', 'queued': 'queued', 'started': 'running', 'failed': 'failed',
                   'completed': 'completed', 'stopped': 'stopped'}
    for number, e in enumerate(events, 1):
        d = e['data']
        row = dict.fromkeys(FIELDS, UNKNOWN)
        row.update(source_line=number, timestamp=e['ts'], embedded_session=e.get('session', UNKNOWN), kind=e['kind'],
                   ui_action=d.get('element', UNKNOWN), status=d.get('status', UNKNOWN),
                   task_id=d.get('task_id', UNKNOWN), run_id=d.get('run_id', UNKNOWN),
                   request=f"{d.get('method', UNKNOWN)} {d['path']}" if 'path' in d else UNKNOWN,
                   correlation='No click/request correlation IDs; no temporal join asserted')
        if e['kind'].startswith('task.') and 'task_id' in d:
            key = d['task_id']
            row['previous_state'] = states.get(key, UNKNOWN)
            state = transitions.get(e['kind'].split('.')[1])
            if state:
                states[key] = state
            row['new_state'] = states.get(key, UNKNOWN)
            row['correlation'] = 'Same task_id; run_id where recorded. Events are claims, not post-state proof.'
            lifecycle.append(row.copy())
        if e['kind'] in INCIDENTS:
            classification, confidence, root, fix, test, expected_result, handler = diagnosis(e)
            row.update(classification=classification, confidence=confidence, root_cause=root,
                       fix_status=fix, regression_test=test, expected_result=expected_result, handler=handler)
            incidents.append(row)

    def write_csv(name, rows):
        with (out / f'{name}_{tag}.csv').open('w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    write_csv('LIVE_FAILURES', incidents)
    write_csv('DEAD_CLICK_ANALYSIS', [r for r in incidents if r['kind'] == 'ui.dead_click'])
    write_csv('TASK_LIFECYCLE_ANALYSIS', lifecycle)
    lines = [f'# Live event timeline — audit baseline {tag}', '',
             f'Source: `{source.as_posix()}`; SHA256 `{hashlib.sha256(raw).hexdigest()}`.', '',
             'Inspection/repair base: `0da3f134df9df8af8c15d5bd12d43be1b6897351`.',
             'The filename tag identifies the starting audit baseline, not fresh historical execution.', '',
             'Exactly one 2,788-record export analyzed. Earlier prefixes and the previous 1,696-record '
             'export are not added. Embedded session IDs span September 4–5 and survive restarts.',
             'Recorded errors: 9 (8 http.error + 1 task.failed); dead clicks: 10; refusals: 15; rage clicks: 1.',
             'LIVE_FAILURES has 35 incident records, not 35 independent failures: HTTP/UI reports can describe '
             'the same request, but lack IDs to prove one-to-one joins.', '',
             'UI timestamps are server log receipt times of batched events. Their apparent ordering against '
             'backend events does not establish click time or causality. UNKNOWN is retained for unavailable '
             'request IDs, prior/new UI snapshots, and actual visible outcomes.', '',
             '## Classification limits', '',
             'Ten dead-click records map to implemented handlers: eight publish, one pane close, one command fill. '
             'Their FALSE_POSITIVE_TELEMETRY classification is a code-supported diagnosis at MEDIUM confidence, '
             'not proof each historical click succeeded. The single rage click remains UX_AMBIGUITY. '
             'Four 403 error records and two matching-path UI refusals lack body/code; the export cannot prove '
             'stale CSRF versus policy refusal. Nine status-0 refusals establish failed fetches, not their cause.', '',
             '## Click/action correlation map', '',
             '| UI action | Current handler | Request | Expected state / visible result | Historical join |',
             '|---|---|---|---|---|',
             '| Publish journal (8 dead records) | testing.js publish | POST /api/testing/publish after flush | disabled/text then SHA or refusal | UNKNOWN |',
             '| Close work pane | thinking.js setOpen(false) | none required | pane hidden | UNKNOWN |',
             '| Fill command | mission_console.js QUICK onClick | none required | input value/focus | UNKNOWN |',
             '| Send command | commandBlock onSubmit | POST /api/tasks | selected executor -> queued; otherwise draft; blocked reason visible | task10 created/queued/failed joined by task_id only |',
             '| All apps | apps.js navigation control | data fetch conditional | apps view | UNKNOWN |', '',
             'Task 10 / run 9: source lines 2711 (agent_id=null), 2712 (queued), 2723 (failed). '
             'The matching quick-command title supports the commandBlock diagnosis, but there is no click ID '
             'to prove which captured click originated the request.', '',
             '## Event counts', '', '| Kind | Count |', '|---|---|']
    lines += [f'| {k} | {v} |' for k, v in counts.most_common()]
    lines += ['', '## Timeline (all clicks, incidents and task transitions)', '',
              '| Source line | Recorded timestamp | Embedded session | Kind | Action / task / request |',
              '|---|---|---|---|---|']
    for n, e in enumerate(events, 1):
        if e['kind'] not in INCIDENTS | {'ui.click', 'ui.navigate'} and not e['kind'].startswith('task.'):
            continue
        d = e['data']
        detail = d.get('element') or d.get('path') or d.get('to') or f"task={d.get('task_id', UNKNOWN)}, run={d.get('run_id', UNKNOWN)}"
        lines.append(f"| {n} | {e['ts']} | {e.get('session', UNKNOWN)} | {e['kind']} | {str(detail).replace('|', '/')} |")
    (out / f'LIVE_EVENT_TIMELINE_{tag}.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return {'events': len(events), 'recorded_errors': counts['http.error'] + counts['task.failed'],
            'dead_clicks': counts['ui.dead_click'], 'refusals': counts['ui.refused'],
            'rage_clicks': counts['ui.rage_click'], 'incident_rows': len(incidents), 'task_rows': len(lifecycle)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(SOURCE))
    parser.add_argument('--output', type=Path, default=Path('docs/testing'))
    parser.add_argument('--tag', default='a14515d')
    args = parser.parse_args()
    print(json.dumps(analyze(args.source, args.output, args.tag), indent=2))
