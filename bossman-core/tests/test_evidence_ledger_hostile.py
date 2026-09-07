"""AT-04: real processes/files; spent evidence is not an evictable cache."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from bossman.learning_guard.evidence_ledger import DurableEvidenceLedger, EvidenceLedger


def test_capacity_exhaustion_refuses_new_measurement_without_forgetting_old():
    ledger = EvidenceLedger(capacity=2)
    assert ledger.consume('a', 'v1') is None
    assert ledger.consume('b', 'v2') is None
    assert ledger.consume('c', 'v3') is not None
    assert ledger.consume('a', 'v9') is not None
    assert ledger.consume('a', 'v1') is None
    assert len(ledger._records) == 2


@pytest.mark.parametrize('broken', ['{partial', '[]', '{"a": null}', '{"a":"v1","a":"v2"}'])
def test_corrupt_journal_refuses_and_preserves_evidence(tmp_path, broken):
    path = tmp_path / 'ledger.json'
    assert DurableEvidenceLedger(path).consume('a', 'v1') is None
    path.write_text(broken)
    assert DurableEvidenceLedger(path).consume('a', 'v9') is not None
    assert path.read_text() == broken


def test_missing_initialized_journal_refuses_new_process(tmp_path):
    path = tmp_path / 'ledger.json'
    assert DurableEvidenceLedger(path).consume('a', 'v1') is None
    path.unlink()
    assert DurableEvidenceLedger(path).consume('a', 'v9') is not None
    assert not path.exists()


def test_failed_replace_does_not_authorize_or_overwrite_previous_state(tmp_path, monkeypatch):
    path = tmp_path / 'ledger.json'
    ledger = DurableEvidenceLedger(path)
    assert ledger.consume('a', 'v1') is None
    def fail(*_):
        raise OSError('injected disk failure')
    monkeypatch.setattr(os, 'replace', fail)
    assert ledger.consume('b', 'v2') is not None
    assert json.loads(path.read_text()) == {'a': 'v1'}
    assert not list(tmp_path.glob('*.tmp'))


def _process_race(path, keys):
    # Independent interpreters report ready BEFORE any consume; release together.
    program = '''import json,sys
from bossman.learning_guard.evidence_ledger import DurableEvidenceLedger
ledger=DurableEvidenceLedger(sys.argv[1])
print("ready",flush=True)
sys.stdin.readline()
print(json.dumps(ledger.consume(sys.argv[2],sys.argv[3])),flush=True)
'''
    procs = [subprocess.Popen([sys.executable, '-u', '-c', program, str(path), key, f'v{i}'],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True) for i, key in enumerate(keys)]
    try:
        for proc in procs:
            assert proc.stdout.readline().strip() == 'ready'
        for proc in procs:
            proc.stdin.write('\n'); proc.stdin.flush()
        results = []
        for proc in procs:
            stdout, stderr = proc.communicate(timeout=20)
            assert proc.returncode == 0, stderr
            results.append(json.loads(stdout.strip()))
        return results
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)


def test_eight_processes_cannot_spend_one_measurement_twice(tmp_path):
    path = tmp_path / 'ledger.json'
    result = _process_race(path, ['same'] * 8)
    assert sum(x is None for x in result) == 1, result
    assert len(json.loads(path.read_text())) == 1


def test_eight_processes_preserve_distinct_records(tmp_path):
    path = tmp_path / 'ledger.json'
    assert _process_race(path, [f'key{i}' for i in range(8)]) == [None] * 8
    assert len(json.loads(path.read_text())) == 8


def test_independent_threads_cannot_both_consume(tmp_path):
    path = tmp_path / 'ledger.json'
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda i: DurableEvidenceLedger(path).consume('a', f'v{i}'), range(8)))
    assert sum(x is None for x in results) == 1


def test_reduced_capacity_preserves_existing_records(tmp_path):
    path = tmp_path / 'ledger.json'
    for i in range(3):
        assert DurableEvidenceLedger(path, capacity=5).consume(f'k{i}', f'v{i}') is None
    smaller = DurableEvidenceLedger(path, capacity=1)
    assert smaller.consume('k0', 'v0') is None
    assert smaller.consume('new', 'new') is not None
    assert smaller.consume('k0', 'attacker') is not None
    assert len(json.loads(path.read_text())) == 3
