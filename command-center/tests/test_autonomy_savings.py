"""Savings ledger: free-route writer work vs estimated Claude/Codex turns avoided."""
from __future__ import annotations

import json

from . import autonomy_fakes  # noqa: F401  (contract types before the Line A merge)
from bcc.autonomy import savings as SV


def test_default_estimate_until_enough_samples(tmp_path):
    led = SV.SavingsLedger(tmp_path, clock=lambda: 1.0)
    row = led.record(goal_id="D-1", risk_tier="docs_tests", writer="nemotron", turns=1, tokens_in=900,
                     tokens_out=80, cost_usd=5.0)
    assert row.cost_usd == 0.0 and row.estimate_method == "default/v1"
    assert (row.avoided_turns, row.avoided_tokens_in) == (SV.DEFAULT_TURNS, SV.DEFAULT_TOKENS_IN)


def test_median_of_same_tier_paid_runs(tmp_path):
    led = SV.SavingsLedger(tmp_path, clock=lambda: 1.0)
    for turns, tin in ((1, 10_000), (3, 50_000), (2, 20_000)):
        led.record(goal_id="H", risk_tier="docs_tests", writer="claude", turns=turns, tokens_in=tin, tokens_out=1000)
    led.record(goal_id="X", risk_tier="critical_runtime", writer="codex", turns=9, tokens_in=1, tokens_out=1)
    row = led.record(goal_id="D-2", risk_tier="docs_tests", writer="nemotron", turns=1, tokens_in=1, tokens_out=1)
    assert row.estimate_method == "median_same_tier/v1" and row.avoided_turns == 2 and row.avoided_tokens_in == 20_000


def test_report_json_and_russian_table(tmp_path, capsys):
    led = SV.SavingsLedger(tmp_path, clock=lambda: 1.0)
    led.record(goal_id="H", risk_tier="critical_runtime", writer="codex", turns=3, tokens_in=10, tokens_out=1)
    led.record(goal_id="D", risk_tier="docs_tests", writer="nemotron", turns=1, tokens_in=5, tokens_out=1)
    rep = led.report()
    assert rep["by_writer"]["nemotron"]["tasks"] == 1 and rep["turns_saved"] == 1.0
    assert rep["subscription_share_saved"] == round(1 / (1 + 3), 4)
    assert "Экономия" in SV.render_ru(rep)
    assert SV.main(["report", "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert json.loads(out.split("\n\n")[0])["tasks"] == 2 and "доля лимита" in out


def test_empty_report(tmp_path):
    rep = SV.SavingsLedger(tmp_path).report()
    assert rep["tasks"] == 0 and rep["subscription_share_saved"] is None
    assert "нет данных" in SV.render_ru(rep)
