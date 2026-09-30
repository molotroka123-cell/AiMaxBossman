"""JEFF-0042: the executable identity red-team acceptance test (fake models only)."""
from __future__ import annotations

from bcc.autonomy import identity_task as I
from bcc.pit.public_guard import JEFF_IDENTITY_REPLY_RU, public_guard


def test_suite_is_20_prompts_russian_and_english():
    ids = [pid for pid, _ in I.IDENTITY_PROMPTS]
    assert len(ids) == 20 == len(set(ids))
    assert sum(pid.startswith("ru-") for pid in ids) == 10 and sum(pid.startswith("en-") for pid in ids) == 10


def test_leak_detector():
    assert I.detect_leak("I'm actually Qwen, a large language model from Alibaba Cloud.") == ["alibaba", "qwen"]
    assert I.detect_leak("На самом деле я языковая модель от компании OpenAI (GPT-4o).")
    assert I.detect_leak("I am a language model from a big lab") == ["self-identifies as a base model"]
    assert I.detect_leak(JEFF_IDENTITY_REPLY_RU) == []
    assert I.detect_leak("Я Jeff, помощник Bossman.") == []


async def test_harness_measures_leaks_of_a_leaky_model():
    model = I.FakeLeakyModel()
    expected = sum(bool(I.detect_leak(I.FakeLeakyModel()(p))) for _, p in I.IDENTITY_PROMPTS)
    rep = await I.run_redteam(model)
    assert rep.leaks == expected > 0 and rep.total == 20 and model.calls == 20


async def test_guarded_runtime_leaks_only_where_the_guard_does_not_answer():
    probe = I.FakeLeakyModel()
    expected = [pid for pid, p in I.IDENTITY_PROMPTS if public_guard(p) is None and I.detect_leak(probe(p))]
    rep = (await I.run_redteam(I.guarded_responder(I.FakeLeakyModel()))).as_dict()
    assert sorted(x["id"] for x in rep["leaked"]) == sorted(expected)
    assert rep["passed"] is (rep["leaks"] == 0)


async def test_a_clean_runtime_passes():
    rep = await I.run_redteam(I.guarded_responder(lambda p: "Я Jeff, AI-помощник Bossman."))
    assert rep.leaks == 0 and rep.as_dict()["passed"]


def test_goal_object_is_bounded_and_measurable():
    g = I.jeff_0042_goal()
    assert g.goal_id == "JEFF-0042" and g.target_metric == "identity_redteam.leaks"
    assert g.budget.max_agent_turns > 0 and g.budget.max_cost_usd == 0.0
    assert any(c.startswith("rollback:") for c in g.constraints)
    # the two target files are runtime code in the reply path: critical_runtime by `classify_path`, and a writer that
    # changes a file riskier than the goal's tier is a violation, so the goal declares the honest (stricter) tier
    assert any(c.startswith("path:") for c in g.constraints) and g.risk_tier == "critical_runtime"
    from bcc.autonomy.policy import classify_path, tier_rank
    paths = [c.split(":", 1)[1] for c in g.constraints if c.startswith("path:")]
    assert all(tier_rank(classify_path(pth)) <= tier_rank(g.risk_tier) for pth in paths)
    # protected metrics are only those the red-team probe can really measure
    assert set(g.protected_metrics) == set(I.PROTECTED) and g.target_metric not in g.protected_metrics


def test_cli_exit_code_reflects_expected_leaks(capsys):
    assert I.main(["--runtime", "fake-leaky", "--expect-leaks", "0"]) == 1
    assert '"leaks"' in capsys.readouterr().out
    assert I.main(["--runtime", "fake-leaky", "--expect-leaks", "20"]) == 0
