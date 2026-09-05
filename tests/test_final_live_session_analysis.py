"""The accumulated owner export is counted once and never joined by proximity."""
import csv
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('live_analysis', ROOT / 'tools/analyze_final_live_session.py')
analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)


def test_export_counts_and_task_identity_are_preserved(tmp_path):
    result = analysis.analyze(ROOT / analysis.SOURCE, tmp_path, 'test')
    assert result == dict(events=2788, recorded_errors=9, dead_clicks=10, refusals=15,
                          rage_clicks=1, incident_rows=35, task_rows=57)
    with (tmp_path / 'LIVE_FAILURES_test.csv').open(encoding='utf-8', newline='') as f:
        rows = list(csv.DictReader(f))
    assert all(r['visible_ui_result'] == 'UNKNOWN' for r in rows)
    failure = next(r for r in rows if r['kind'] == 'task.failed')
    assert (failure['task_id'], failure['run_id'], failure['previous_state'], failure['new_state']) == ('10', '9', 'queued', 'failed')
    assert all(r['classification'] in {'EXPECTED_POLICY_REFUSAL', 'STALE_UI', 'MISSING_HANDLER',
                                     'BACKEND_FAILURE', 'RACE', 'UX_AMBIGUITY', 'FALSE_POSITIVE_TELEMETRY'} for r in rows)


def test_incomplete_or_duplicate_export_is_rejected(tmp_path):
    raw = (ROOT / analysis.SOURCE).read_text(encoding='utf-8')
    source = tmp_path / 'bad.jsonl'
    for content in ['\n'.join(raw.splitlines()[:2748]), raw + raw]:
        source.write_text(content, encoding='utf-8')
        with pytest.raises(ValueError, match='Unexpected export'):
            analysis.analyze(source, tmp_path / 'out', 'test')
    assert not (tmp_path / 'out').exists()
