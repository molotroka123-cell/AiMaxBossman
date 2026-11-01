"""The preregistered lesson-transfer scorer (docs/audits/2026-11-01-lesson-transfer/scorer.py): parsing and the
decision rule, on synthetic runs. Each rule branch has a case that hits it."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCORER = Path(__file__).resolve().parents[2] / "docs" / "audits" / "2026-11-01-lesson-transfer" / "scorer.py"
spec = importlib.util.spec_from_file_location("lt_scorer", SCORER)
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)


@pytest.mark.parametrize("answer,kind,truth,ok", [
    ("расчёт\nОТВЕТ: 18 828,15 CZK", "money", "18828.15", True),
    ("**ОТВЕТ:** 18828.1425 CZK", "money", "18828.15", False),          # unrounded is wrong
    ("ОТВЕТ: 18828.14 CZK", "money", "18828.15", False),                # half-up instead of desk rounding
    ("ОТВЕТ: 29,598.50 CZK", "money", "29598.50", True),
    ("ОТВЕТ: $29598.50$ CZK", "money", "29598.50", True),
    ("итого 248\n\n3", "money", "3", True),                            # fallback: last number, lines never merge
    ("ОТВЕТ: Воскресенье.", "text", "воскресенье", True),
    ("ОТВЕТ: понедельник", "text", "воскресенье", False),
    ("нет ответа", "money", "1.00", False),
])
def test_parse_and_compare(answer, kind, truth, ok):
    assert S.correct(answer, kind, truth)[0] is ok


def test_fallback_is_flagged():
    assert S.correct("итого 248 CZK", "money", "248.00") == (True, "248", True)


def _runs(phase_pass: dict[str, int], sets: dict[str, str], recalled: bool) -> list[dict]:
    """phase_pass: task -> number of correct reps out of 3. Answers are 'ОТВЕТ: 1' (right) / 'ОТВЕТ: 2'."""
    out = []
    for tid, n in phase_pass.items():
        for rep in (1, 2, 3):
            out.append({"task": tid, "set": sets[tid], "rep": rep, "status": "completed",
                        "answer": "ОТВЕТ: 1" if rep <= n else "ОТВЕТ: 2",
                        "memory_events": [{"kind": "memory.recalled", "sources": ["fact:7"]}] if recalled else []})
    return out


def _world(h_base: int, h_after: int, ctrl_reg: int = 0):
    sets = {**{f"H{i:02d}": "heldout" for i in range(1, 21)}, **{f"I{i:02d}": "independent" for i in range(1, 7)},
            **{f"C{i:02d}": "control" for i in range(1, 13)}}
    truth = {t: {"value": "1", "kind": "number"} for t in sets}
    base = {t: (3 if (t.startswith("H") and int(t[1:]) <= h_base) or t.startswith("C") else 0) for t in sets}
    after = {t: (3 if (t.startswith("H") and int(t[1:]) <= h_after) or t.startswith("C") else 0) for t in sets}
    for i in range(1, ctrl_reg + 1):
        after[f"C{i:02d}"] = 1
    b = S.score(truth, _runs(base, sets, recalled=False), "fact:7")
    a = S.score(truth, _runs(after, sets, recalled=True), "fact:7")
    return S.decide(b, a, truth)


def test_pass_needs_delta_significance_and_no_control_regression():
    assert _world(2, 14)["verdict"] == "PASS"
    assert _world(2, 14, ctrl_reg=1)["verdict"] == "INCONCLUSIVE"
    assert _world(2, 14, ctrl_reg=2)["verdict"] == "FAIL"


def test_small_delta_fails_and_no_headroom_is_inconclusive():
    assert _world(5, 7)["verdict"] == "FAIL"
    d = _world(18, 20)
    assert d["verdict"] == "INCONCLUSIVE" and "no headroom" in d["reasons"][0]


def test_recall_gate_fails_when_the_lesson_was_not_injected():
    sets = {**{f"H{i:02d}": "heldout" for i in range(1, 21)}, "C01": "control", "I01": "independent"}
    truth = {t: {"value": "1", "kind": "number"} for t in sets}
    base = {t: 0 for t in sets}
    after = {t: 3 for t in sets}
    b = S.score(truth, _runs(base, sets, recalled=False), "fact:7")
    a = S.score(truth, _runs(after, sets, recalled=False), "fact:7")   # big gain, but no recall: not transfer
    d = S.decide(b, a, truth)
    assert d["verdict"] == "FAIL" and "recall gate" in d["reasons"][0]


def test_sign_test_values():
    assert S.sign_test_one_sided(10, 0) == pytest.approx(1 / 1024)
    assert S.sign_test_one_sided(0, 0) == 1.0
