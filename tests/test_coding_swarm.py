"""tools/coding_swarm.py: refusals before any call, receipt shape, reasoning kept out of the receipt (no network)."""
import importlib.util
import json
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("cs", Path(__file__).resolve().parents[1] / "tools" / "coding_swarm.py")
cs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cs)


def _fake(name, prompt, **_):
    return {"model": name, "id": "fake", "ok": True, "seconds": 0.1, "tokens_in": 10, "tokens_out": 5,
            "cost_usd": 0.0, "text": "answer", "reasoning": "chain of thought" if name == "kimi" else ""}


def test_secret_in_prompt_is_refused_before_any_call(monkeypatch, tmp_path):
    monkeypatch.setattr(cs, "_call", lambda *a, **k: pytest.fail("network call made"))
    with pytest.raises(SystemExit):
        cs.run("key sk-" + "a" * 24, ["kimi"], tmp_path, "t")


def test_unknown_model_and_oversized_prompt_are_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(cs, "_call", lambda *a, **k: pytest.fail("network call made"))
    with pytest.raises(SystemExit):
        cs.run("hi", ["nope"], tmp_path, "t")
    with pytest.raises(SystemExit):
        cs.run("x" * (cs.MAX_PROMPT_CHARS + 1), ["kimi"], tmp_path, "t")


def test_receipt_has_costs_but_no_reasoning_and_dataset_gets_it(monkeypatch, tmp_path):
    monkeypatch.setattr(cs, "_call", _fake)
    monkeypatch.setattr(cs, "DATASET", tmp_path / "ds")
    doc = cs.run("task", ["kimi", "qwen"], tmp_path / "out", "t")
    receipt = json.loads((tmp_path / "out" / "t-receipt.json").read_text(encoding="utf-8"))
    assert receipt == doc and all("reasoning" not in c for c in receipt["calls"])
    rows = [json.loads(l) for f in (tmp_path / "ds").glob("*.jsonl") for l in f.read_text(encoding="utf-8").splitlines()]
    assert [r["model"] for r in rows] == ["kimi"] and rows[0]["training_use"] == "PENDING_TERMS_CHECK"
