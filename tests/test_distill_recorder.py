"""Distillation recorder + free-only worker guard (owner run 2026-09-24).

The dataset must never hold a secret, must say whether outputs may be used for
training, and a cloud worker must never silently become a paid one.
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import distill_export_bossman  # noqa: E402
import distill_recorder  # noqa: E402
import worker_client  # noqa: E402

FAKE_KEY = "sk-or-v1-" + "0123456789abcdef" * 4          # ci-secret-scan: allow
NEMO = "nvidia/nemotron-3-ultra-550b-a55b:free"


def _lines(path):
    return [json.loads(x) for x in pathlib.Path(path).read_text(encoding="utf-8").splitlines()]


def test_record_is_scrubbed_and_labelled(tmp_path):
    rec = distill_recorder.Recorder("t", directory=tmp_path)
    rec.record(model=NEMO, task_class="c", messages=[{"role": "user", "content": f"key {FAKE_KEY} here"}],
               response_text="Authorization: Bearer " + FAKE_KEY, verdict="PASS", curator_note="ok")
    (row,) = _lines(rec.path)
    assert FAKE_KEY not in json.dumps(row)
    assert row["training_use"] == "ALLOWED_BY_MODEL_LICENSE" and "OpenMDW" in row["license"]["model_license"]
    assert row["gate_state"] == "RAW_CANDIDATE" and row["verifier_verdict"] == "PASS"
    for field in ("ts", "model", "source_sha", "task_class", "messages", "tools", "response", "tool_calls",
                  "latency_s", "usage", "curator_note"):
        assert field in row


def test_unknown_model_licence_is_unverified_not_allowed(tmp_path):
    rec = distill_recorder.Recorder("t", directory=tmp_path)
    rec.record(model="someone/else:free", task_class="c", messages=[], response_text="x")
    assert _lines(rec.path)[0]["training_use"] == "UNVERIFIED"


def test_worker_refuses_a_paid_openrouter_model(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_KEY)
    with pytest.raises(worker_client.PaidViolation):
        worker_client.Worker(worker_client.OPENROUTER, "nvidia/nemotron-3-ultra-550b-a55b")


def test_nonzero_cost_is_flagged_even_on_a_free_id(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_KEY)
    rec = distill_recorder.Recorder("t", directory=tmp_path)
    w = worker_client.Worker(worker_client.OPENROUTER, NEMO, recorder=rec)
    monkeypatch.setattr(w, "_stream", lambda body: {"text": "x", "reasoning": "", "tool_calls": [],
                                                    "usage": {"cost": 0.02}, "latency_s": 1, "ttft_ms": 1,
                                                    "tps": 1, "error": None})
    out = w.chat([{"role": "user", "content": "q"}])
    assert out["error"].startswith("PAID_VIOLATION")
    assert _lines(rec.path)[0]["error"].startswith("PAID_VIOLATION")


def test_bossman_export_reads_runs_once(tmp_path, monkeypatch):
    monkeypatch.setattr(distill_recorder, "DEFAULT_DIR", tmp_path / "ds")
    db = tmp_path / "bcc.db"
    con = sqlite3.connect(db)
    con.execute("create table tasks (id integer primary key, title text)")
    con.execute("create table task_runs (id integer primary key, task_id int, status text, model_alias text, "
                "tokens_in int, tokens_out int, cost_usd real, result text, error text, checkpoint text, "
                "started_at text, finished_at text)")
    con.execute("insert into tasks values (1, 't')")
    cp = {"messages": [{"role": "user", "content": "2+2"}, {"role": "assistant", "content": "4"}]}
    con.execute("insert into task_runs values (5, 1, 'completed', 'nemo', 3, 1, 0.0, '4', null, ?, null, null)",
                (json.dumps(cp),))
    con.execute("insert into task_runs values (6, 1, 'running', 'nemo', 3, 1, 0.0, null, null, null, null, null)")
    con.commit()
    con.close()
    args = ["--alias", "nemo", "--model-id", NEMO, "--db", str(db), "--name", "runs"]
    distill_export_bossman.main(args)
    distill_export_bossman.main(args)
    rows = _lines(tmp_path / "ds" / "runs.jsonl")
    assert len(rows) == 1 and rows[0]["extra"]["bossman_run_id"] == 5
    assert rows[0]["response"] == "4" and rows[0]["messages"] == [{"role": "user", "content": "2+2"}]
    assert rows[0]["verifier_verdict"] == "UNVERIFIED"
