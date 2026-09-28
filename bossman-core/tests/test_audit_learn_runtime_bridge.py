"""Audit 2026-09-28: the trainer bridge counts a task once and loses no episode."""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bossman.learning_guard import runtime_bridge as rb  # noqa: E402


def _rec(i: int, **over) -> dict:
    rec = {"task_id": f"T-{i}", "bug_class": "path_traversal", "component": "fs", "learning_status": "VERIFIED",
           "principal_id": "agent:qwen#r1", "model": "qwen-14b", "environment": "env-a", "start_sha": "a",
           "end_sha": "b", "verifiers": [{"principal_id": "human:qa", "independence_class": "human", "run_id": "r9"}],
           "evidence_records": [{"observed_at": 1.0, "environment": "env-a"}]}
    rec.update(over)
    return rec


def _lines(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_new_versions_of_one_case_are_not_new_episodes(tmp_path, monkeypatch):
    monkeypatch.setenv(rb.TRAINER_FLAG, "1")
    path = tmp_path / "eps.jsonl"
    for _ in range(5):                                        # save, dedup bump, verify, re-verify, ...
        out = rb.observe_learning_record(_rec(1), episodes_path=path)
    assert len(_lines(path)) == 1
    assert out["repeated_task"] is True and out["status"] == "CANDIDATE" and out["sample_count"] == 1


def test_concurrent_observers_lose_no_episode(tmp_path, monkeypatch):
    monkeypatch.setenv(rb.TRAINER_FLAG, "1")
    path = tmp_path / "eps.jsonl"
    errors: list[BaseException] = []

    def worker(base: int) -> None:
        try:
            for i in range(base, base + 8):
                rb.observe_learning_record(_rec(i), episodes_path=path)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(k * 100,)) for k in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert sorted(l["task_id"] for l in _lines(path)) == sorted(f"T-{k * 100 + i}" for k in range(4) for i in range(8))
