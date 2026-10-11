from __future__ import annotations

import json
from pathlib import Path

from botlab import __main__ as cli
from botlab.report import load_history, report_from_hands, wilson


def _session(tmp: Path) -> Path:
    hands = []
    for i in range(1, 41):
        pre = "fold" if i % 2 else ("raise" if i % 4 == 0 else "call")
        hands.append({"hand": i, "position": "BTN" if i % 3 else "BB", "holeCards": "As Kd",
                      "actions": [{"stage": "preflop", "action": pre, "amount": 0}],
                      "chipsBefore": 1000, "chipsAfter": 1000, "netProfit": (20 if pre == "raise" else -10 if pre == "call" else 0),
                      "mistake": "limp" if pre == "call" else None, "evLost": 2 if pre == "call" else 0})
    data = {"sessionId": "s1", "handSummaries": hands,
            "records": [{"handNumber": i, "blinds": {"sb": 5, "bb": 10}} for i in range(1, 41)]}
    p = tmp / "session.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_wilson_bounds() -> None:
    lo, hi = wilson(50, 100)
    assert 0.40 < lo < 0.41 and 0.59 < hi < 0.60
    assert wilson(0, 0) == (0.0, 0.0)


def test_history_report_numbers_and_status(tmp_path: Path) -> None:
    hands, meta = load_history(_session(tmp_path))
    assert meta["hands_parsed"] == 40 and meta["format"] == "poker-train session export"
    rep = report_from_hands(hands, meta, None)
    assert rep["vpip"]["n"] == 40 and rep["vpip"]["value"] == 50.0  # 10 raises + 10 calls of 40
    assert rep["pfr"]["value"] == 25.0
    # 10 raises: +20 chips = +2 bb ; 10 calls: -10 = -1 bb ; 20 folds 0 -> mean 0.25 bb/hand = 25 bb/100
    assert rep["winrate"]["bb_per_100"] == 25.0
    assert rep["mistakes"]["by_type"] == {"limp": 10}


def test_cli_history_and_simulation_reports(tmp_path: Path, capsys) -> None:
    out1 = tmp_path / "h"
    assert cli.main(["report", "--history", str(_session(tmp_path)), "--out", str(out1)]) == 0
    rep = json.loads((out1 / "report.json").read_text(encoding="utf-8"))
    assert rep["status"]["accepted_by_customer"] == "NOT PROVEN" and rep["status"]["paid"] == "NOT PROVEN"
    assert "not a promise of winnings" in " ".join(rep["limits"])
    assert (out1 / "REPORT.md").read_text(encoding="utf-8").count("## Limits") == 1

    out2, out3 = tmp_path / "s1", tmp_path / "s2"
    args = ["report", "--hands", "240", "--seed", "5", "--opponents", "Rock,Maniac", "--rake-pct", "0.05"]
    assert cli.main(args + ["--out", str(out2)]) == 0
    assert cli.main(args + ["--out", str(out3)]) == 0
    a = json.loads((out2 / "report.json").read_text(encoding="utf-8"))
    b = json.loads((out3 / "report.json").read_text(encoding="utf-8"))
    for k in ("winrate", "vpip", "pfr", "table"):
        assert a[k] == b[k]  # same seed -> identical numbers
    assert a["table"]["chip_conservation"] is True and a["table"]["rake_total_chips"] > 0


def test_cli_rejects_missing_or_empty_history(tmp_path: Path) -> None:
    assert cli.main(["report", "--history", str(tmp_path / "nope.json")]) == 2
    empty = tmp_path / "e.jsonl"
    empty.write_text('{"event": "hand_over"}\n', encoding="utf-8")
    assert cli.main(["report", "--history", str(empty), "--out", str(tmp_path / "o")]) == 3
