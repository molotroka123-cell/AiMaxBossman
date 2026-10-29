"""Owner 29.10: «убрать лимиты» — max_steps <= 0 is an uncapped agent loop."""
from pathlib import Path


def test_zero_max_steps_means_uncapped_and_default_session_cap_is_off():
    root = Path(__file__).resolve().parents[1] / "bcc"
    eng = (root / "engine.py").read_text(encoding="utf-8")
    assert "max_steps = 10 ** 9 if _ms is not None and int(_ms) <= 0" in eng
    oc = (root / "features" / "agent_opencode.py").read_text(encoding="utf-8")
    assert "DEFAULT_MAX_LOOP_STEPS = 0" in oc


def test_zero_cost_run_has_no_token_ceiling_but_paid_run_keeps_it():
    """Owner 29.10: TOKEN_BUDGET_EXCEEDED must not kill a run; only an explicit task token cap or the USD ceiling stops it."""
    import dataclasses
    from bcc import mission_budget as mb
    limits = mb.Limits(max_tokens=400_000, max_cost_usd=2.0, max_identical_calls=6, max_stalled_steps=3)
    free = dataclasses.replace(limits, max_tokens=0)
    assert mb.check_spend(free, tokens_in=407_000, tokens_out=751, cost_usd=0.0) is None
    paid = mb.check_spend(limits, tokens_in=407_000, tokens_out=751, cost_usd=0.4)
    assert paid is not None and paid.code == "TOKEN_BUDGET_EXCEEDED"
    assert mb.check_spend(free, tokens_in=10, tokens_out=1, cost_usd=3.0).code == "COST_BUDGET_EXCEEDED"
    eng = (__import__("pathlib").Path(mb.__file__).parent / "engine.py").read_text(encoding="utf-8")
    assert "eff_limits = limits if _budget.TOKENS_KEY in _meta else dataclasses.replace(limits, max_tokens=0)" in eng
