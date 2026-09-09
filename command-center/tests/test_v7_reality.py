"""V7 reality core: compiler, observers, strategy, recovery, shadow telemetry.

The five invariants the multi-model audit corpus agrees on, and which every
test here is written to defend:

    strategy score       != permission
    model confidence     != evidence
    MissionIR            != authorization
    WorldState belief    != verified external truth
    skill promotion      != self-granted capability

A V7 feature that improved routing while quietly breaking one of these would
be a regression, not an advance, so the negative controls outnumber the
positive cases on purpose.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from bcc import model_health as mh
from bcc.db import models as models_t, tasks as tasks_t
from bcc.providers import ProviderError
from bcc.reality import compiler, observers, recovery, strategy, telemetry

from .conftest import FakeAdapter
from .helpers import make_stack
from .test_v21_tool_loop import FINISHED, _run_task


# ============================================================ Reality Compiler

BASE_INTENT = {"goal": "исправить документацию", "owner_id": "owner",
               "project_id": "bossman", "mission_id": "m-1", "goal_id": "g-1",
               "success_conditions": ["файл существует"]}


def effect(kind="READ_ONLY", target="notes.md"):
    return {"effect_id": "e1", "kind": kind, "description": "правка",
            "capabilities": ["TERMINAL_FILE_ACTION"], "depends_on": [],
            "verifiers": [{"kind": "file", "target": target,
                           "expect": {"exists": True}, "max_age_seconds": 60}]}


def test_a_complete_intent_compiles_to_a_digest_bound_contract():
    result = compiler.compile_intent({**BASE_INTENT, "effects": [effect()]})
    assert result.ok and result.mission is not None
    assert len(result.mission.digest) == 64            # the contract is identified


def test_an_intent_without_declared_effects_is_incomplete_not_empty():
    """Silence about effects is not "no effects" and not "any effect". Guessing
    either way is how a mission acquires an obligation, or an authority, nobody
    stated."""
    result = compiler.compile_intent(BASE_INTENT)
    assert result.status == compiler.INCOMPLETE
    assert "effects" in result.missing
    assert result.mission is None


def test_the_compiler_never_infers_an_obligation_from_prose():
    """The defect that manufactured `file:example.com` and deadlocked two tasks:
    an obligation derived from a sentence nobody meant as a promise."""
    result = compiler.compile_intent(
        {**BASE_INTENT, "goal": "открой https://example.com и сохрани notes.md"})
    assert result.status == compiler.INCOMPLETE
    assert any("НЕ выведены" in note for note in result.not_inferred)


def test_a_mutating_effect_cannot_hide_inside_an_informational_mission():
    """`side_effect=false` with a write in the effect list would let a mutation
    travel under a read-only summary."""
    result = compiler.compile_intent(
        {**BASE_INTENT, "side_effect": False, "effects": [effect("REVERSIBLE_WRITE")]})
    assert result.status == compiler.REFUSED
    assert "не смягчает" in result.reason


def test_a_declared_write_makes_the_mission_effectful_whatever_it_claims():
    result = compiler.compile_intent(
        {**BASE_INTENT, "effects": [effect("IRREVERSIBLE")]})
    assert result.ok and result.mission.to_dict()["side_effect"] is True


def test_an_unspecified_budget_is_modest_never_unlimited():
    """"Unspecified" must not read as "unlimited": a mission with no stated
    ceiling is the one most likely to need one."""
    result = compiler.compile_intent({**BASE_INTENT, "effects": [effect()]})
    budget = result.mission.to_dict()["budget"]
    assert 0 < budget["max_tokens"] <= 200_000
    assert 0 < budget["max_cost_usd"] <= 5


def test_compiling_grants_nothing():
    """MissionIR != authorization. The compiled contract carries scope
    REFERENCES, which downstream policy resolves; it carries no grant, no
    receipt and no signature, and `required_effects` is explicitly not a
    dispatch contract."""
    result = compiler.compile_intent(
        {**BASE_INTENT, "effects": [effect("IRREVERSIBLE")],
         "authorized_scope_refs": ["scope-that-does-not-exist"]})
    assert result.ok
    raw = result.mission.to_dict()
    assert raw["authorized_scope_refs"] == ["scope-that-does-not-exist"]
    assert not {"grant", "receipt", "signature", "approved", "token"} & set(raw)


@pytest.mark.parametrize("intent", [None, "text", 7, [], {"goal": ""}])
def test_malformed_intent_is_refused_not_coerced(intent):
    result = compiler.compile_intent(intent)
    assert result.status in (compiler.REFUSED, compiler.INCOMPLETE)
    assert result.mission is None


def test_a_contract_violation_is_reported_in_the_shared_validator_words():
    """Re-wording the refusal here would create a second, softer account of why
    something was rejected."""
    bad = {**BASE_INTENT, "effects": [{**effect(), "verifiers": []}]}
    result = compiler.compile_intent(bad)
    assert result.status == compiler.REFUSED
    assert "post-state" in result.reason or "verifier" in result.reason


# ================================================================== Observers

def test_an_observer_that_cannot_measure_says_so_rather_than_guessing():
    """"We could not look" and "we looked and found nothing" are different
    findings; merging them is how a broken probe reads as a healthy system."""
    obs = observers.observe_git(None)
    assert obs.available is False and obs.value is None and obs.reason


def test_a_non_repository_is_not_reported_as_a_clean_repository(tmp_path):
    obs = observers.observe_git(tmp_path)
    assert obs.available is False
    assert "git" in obs.reason


def test_a_real_repository_is_measured_with_git_itself(tmp_path):
    import subprocess
    ws = tmp_path / "ws"
    ws.mkdir()
    subprocess.run(["git", "-C", str(ws), "init", "-q", "."], check=True, capture_output=True)
    (ws / "a.txt").write_text("x", encoding="utf-8")
    obs = observers.observe_git(ws)
    assert obs.available is True
    assert obs.value["dirty"] is True and obs.value["changed_count"] >= 1


def test_every_available_reading_carries_an_expiry():
    """A fact with no expiry is fresh forever, which is the one thing an
    observation never is."""
    for obs in (observers.observe_git(None), observers.observe_process()):
        assert obs.max_age_seconds > 0


async def test_observe_all_survives_one_adapter_failing(env, monkeypatch):
    """A partial picture with an honest gap beats no picture at all."""
    monkeypatch.setattr(observers, "observe_process",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        observers.observe_process()
    monkeypatch.undo()
    result = await observers.observe_all(env.svc, repo=None)
    assert len(result) >= 4
    assert any(o.available for o in result)


async def test_unavailable_readings_are_dropped_not_ingested_as_null(env):
    """A null fact would read as FRESH knowledge that the value is nothing."""
    readings = await observers.observe_all(env.svc, repo=None)
    facts = observers.to_world_facts(readings, scope_id="test")
    assert all(f["value"] is not None for f in facts)
    assert len(facts) < len(readings)                  # the git one was dropped


async def test_observers_are_model_free_by_construction(env):
    """WorldState belief != verified external truth. Every source here is a
    subprocess, a counter or a SQL row — no model output can enter."""
    readings = await observers.observe_all(env.svc, repo=None)
    for obs in readings:
        assert obs.source in ("git", "process", "task", "provider", "app", "unknown")


# =================================================================== Strategy

def test_an_unmeasured_path_is_the_floor_not_an_optimistic_bet():
    """model confidence != evidence: `unknown` sorts below `low`."""
    assert strategy.Band("unknown").weight < strategy.Band("low").weight
    assert strategy.Band.from_health(None).label == "unknown"
    assert strategy.Band.from_health(mh.HealthRecord()).label == "unknown"


def test_a_band_records_what_it_was_derived_from():
    """"high" from one lucky probe must be visibly different from "high" backed
    by twenty observations."""
    healthy = mh.record_observation(None, mh.HEALTHY, "")
    band = strategy.Band.from_health(healthy)
    assert band.samples == 1 and band.source == "health=healthy"


def test_a_broken_model_never_produces_a_high_band():
    silent = mh.record_observation(None, mh.SILENT, "пусто")
    assert strategy.Band.from_health(silent).label == "low"


def test_the_shadow_router_filters_by_permission_before_ranking():
    """strategy score != permission. A high score can never surface a path the
    mission is not allowed to take."""
    forbidden = strategy.Strategy("privileged", "deploy", strategy.Band("high"),
                                  goal_value=10_000, required_permissions=("deploy.production",))
    allowed = strategy.Strategy("plain", "tool", strategy.Band("low"), goal_value=1)
    decision = strategy.shadow_route([forbidden, allowed], permissions=[])
    assert decision.selected is allowed                 # the 10,000 lost to policy
    assert forbidden not in decision.ranked


def test_no_eligible_candidate_is_a_decision_not_a_fallback():
    forbidden = strategy.Strategy("p", "deploy", strategy.Band("high"),
                                  required_permissions=("deploy.production",))
    decision = strategy.shadow_route([forbidden], permissions=[])
    assert decision.selected is None and "политик" in decision.reason


def test_the_router_says_when_its_top_pick_is_merely_unmeasured():
    unknown = strategy.Strategy("a", "small_model", strategy.Band("unknown"))
    decision = strategy.shadow_route([unknown], permissions=[])
    assert decision.selected is unknown
    assert "не измерен" in decision.reason              # ignorance, stated as such


def test_utility_terms_are_kept_separately_not_collapsed():
    """A router whose reasoning is a single float is one nobody can review."""
    terms = strategy.Strategy("a", "tool", strategy.Band("high"),
                              latency_cost=5, money_cost=2).terms()
    for key in ("band", "band_samples", "band_source", "latency_cost",
                "money_cost", "risk_penalty", "utility"):
        assert key in terms


def test_a_deterministic_path_is_only_offered_when_one_exists():
    """Recommending a path that does not exist is worse than recommending
    nothing."""
    with_det = strategy.generate_strategies(deterministic_available=True)
    without = strategy.generate_strategies(deterministic_available=False)
    assert any(s.path == "tool" for s in with_det)
    assert not any(s.path == "tool" for s in without)


def test_an_unknown_world_penalises_every_model_path_equally():
    calm = strategy.generate_strategies(deterministic_available=False, unknown_facts=0)
    murky = strategy.generate_strategies(deterministic_available=False, unknown_facts=8)
    penalties = {s.strategy_id: s.uncertainty_penalty for s in murky}
    assert all(p > 0 for sid, p in penalties.items() if sid != "human-escalation")
    assert all(s.uncertainty_penalty == 0 for s in calm)


def test_the_router_is_shadow_only_in_the_data_not_just_in_the_code():
    decision = strategy.shadow_route(
        strategy.generate_strategies(deterministic_available=True), permissions=[])
    assert decision.shadow_only is True and decision.to_dict()["shadow_only"] is True


# =================================================================== Recovery

@pytest.mark.parametrize("error,expected", [
    ("HTTP 401 OpenRouter: API key expired", recovery.UNAUTHORIZED),
    ("403 forbidden", recovery.UNAUTHORIZED),
    ("429 Too Many Requests", recovery.THROTTLED),
    ("402 insufficient credit", recovery.THROTTLED),
    ("context length exceeded", recovery.CONTEXT),
    ("model does not support tool use", recovery.CAPABILITY),
    ("empty response from provider", recovery.SILENT),
    ("connection reset by peer", recovery.TRANSIENT),
    ("503 service unavailable", recovery.TRANSIENT),
    ("что-то пошло не так", recovery.UNKNOWN),
])
def test_failures_are_classified_by_what_would_actually_fix_them(error, expected):
    assert recovery.classify_failure(error) == expected


def test_a_rejected_key_goes_straight_to_the_owner():
    """The corpus's own case: an expired key burned every retry. Waiting does
    not fix a rejected credential, and re-sending it is how keys get blocked."""
    ladder = recovery.Ladder(recovery.UNAUTHORIZED)
    rung = recovery.next_rung(ladder, current_model_id=1, fallback_model_id=2,
                              retries_left=10)
    assert rung.terminal and rung.name == recovery.HUMAN
    assert "владелец" in rung.reason


def test_a_transient_blip_retries_the_same_route_first():
    rung = recovery.next_rung(recovery.Ladder(recovery.TRANSIENT),
                              current_model_id=1, retries_left=2)
    assert rung.name == recovery.RETRY_SAME


def test_throttling_tries_another_model_before_waiting():
    """Waiting helps a per-model limit; it does not help an exhausted account."""
    rung = recovery.next_rung(recovery.Ladder(recovery.THROTTLED),
                              current_model_id=1, fallback_model_id=2, retries_left=5)
    assert rung.name == recovery.ALTERNATE_MODEL and rung.model_id == 2


def test_the_owners_retry_budget_is_not_burned_by_one_rung():
    """`retry_same` is the owner's `max_retries`, not one idea among many.
    Spending it all at once would fail a flaky provider after one attempt."""
    ladder = recovery.Ladder(recovery.TRANSIENT)
    for remaining in (3, 2, 1):
        rung = recovery.next_rung(ladder, current_model_id=1, retries_left=remaining)
        assert rung.name == recovery.RETRY_SAME
        ladder = rung.ladder
    spent = recovery.next_rung(ladder, current_model_id=1, retries_left=0)
    assert spent.name != recovery.RETRY_SAME          # budget gone, ladder moves on


def test_every_other_rung_is_spent_exactly_once():
    """No cycling: a failing task reaches a terminal state in bounded steps."""
    ladder = recovery.Ladder(recovery.SILENT)
    seen = []
    for _ in range(6):
        rung = recovery.next_rung(ladder, current_model_id=1, fallback_model_id=2,
                                  retries_left=0)
        seen.append(rung.name)
        ladder = rung.ladder
        if rung.terminal:
            break
    assert seen[-1] == recovery.HUMAN
    assert len(seen) == len(set(seen)), f"a rung repeated: {seen}"


def test_a_different_failure_keeps_the_spent_rungs():
    """Astra/Codex F6 (2026-09-08): a fresh ladder per failure class let a
    provider alternating two error labels re-earn the degraded path forever.
    The class selects which rungs are RELEVANT; it never un-spends one. A run
    that runs out of rungs goes to the owner — that is the bounded answer."""
    spent = recovery.Ladder(recovery.TRANSIENT, ("alternate_model",))
    other = recovery.Ladder.from_dict(spent.to_dict(), recovery.THROTTLED)
    assert other.spent == ("alternate_model",)
    assert other.failure_class == recovery.THROTTLED and other.transitions == 1
    assert other.classes == (recovery.TRANSIENT, recovery.THROTTLED)
    same = recovery.Ladder.from_dict(spent.to_dict(), recovery.TRANSIENT)
    assert same.spent == ("alternate_model",)


def test_a_corrupt_stored_ladder_does_not_strand_the_run():
    for junk in (None, "ladder", 7, {"failure_class": "transient", "spent": "all"}):
        assert recovery.Ladder.from_dict(junk, recovery.TRANSIENT).spent == ()


def test_no_rung_grants_authority():
    """The ladder changes HOW a request is made, never WHAT it may do. The
    degraded rung REMOVES capability; nothing adds permission."""
    for failure_class in recovery.LADDERS:
        ladder = recovery.Ladder(failure_class)
        for _ in range(5):
            rung = recovery.next_rung(ladder, current_model_id=1, fallback_model_id=2,
                                      retries_left=1)
            assert not any(k in rung.to_dict() for k in
                           ("permissions", "grant", "approval", "scope"))
            if rung.degrade:
                assert set(rung.degrade) <= {"stream", "tools"}
                assert all(v is False for v in rung.degrade.values())
            ladder = rung.ladder
            if rung.terminal:
                break


def test_an_alternate_model_is_never_the_one_that_just_failed():
    healthy = [(1, mh.record_observation(None, mh.HEALTHY, "")),
               (2, mh.record_observation(None, mh.HEALTHY, ""))]
    rung = recovery.next_rung(recovery.Ladder(recovery.SILENT), current_model_id=1,
                              healthy_models=healthy, retries_left=0)
    assert rung.name == recovery.ALTERNATE_MODEL and rung.model_id == 2


def test_a_silent_model_is_not_offered_as_the_alternative():
    """B5 feeding V7: a model measured silent is not a recovery path."""
    pool = [(2, mh.record_observation(None, mh.SILENT, "пусто"))]
    rung = recovery.next_rung(recovery.Ladder(recovery.SILENT), current_model_id=1,
                              healthy_models=pool, retries_left=0)
    assert rung.name != recovery.ALTERNATE_MODEL


# --------------------------------------------------------- engine integration

async def test_an_expired_key_escalates_instead_of_burning_every_retry(env):
    """End to end, the corpus's T1: the run stops at the owner rather than
    re-sending a rejected credential until the budget is gone."""
    stack = await make_stack(env.client, max_retries=5)
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter(
        fail_times=99, error="HTTP 401 OpenRouter: API key expired")
    tid = stack["task"]["id"]

    assert await _run_task(env, tid, timeout=20, until=FINISHED) == "failed"
    async with env.svc.db.session() as s:
        from bcc.db import task_runs as runs_t
        row = (await s.execute(sa.select(runs_t.c.attempt, runs_t.c.error).where(
            runs_t.c.task_id == tid))).first()
    assert row._mapping["attempt"] == 0, "the key was re-sent instead of escalating"
    assert "владелец" in (row._mapping["error"] or "")


async def test_a_transient_failure_still_recovers_normally(env):
    """The negative control for the whole ladder: it must not make a healthy
    retry path worse."""
    stack = await make_stack(env.client, max_retries=3)
    # ONE adapter instance: a factory that builds a fresh one per call would
    # reset `fail_times` every attempt and never succeed, which tests the
    # fixture rather than the ladder.
    adapter = FakeAdapter("готово", fail_times=1, error="connection reset")
    env.svc.registry.adapter_factory = lambda m, p: adapter
    assert await _run_task(env, stack["task"]["id"], timeout=25, until=FINISHED) == "completed"
    assert adapter.calls == 2                        # failed once, then succeeded


# ================================================================== Telemetry

@pytest.mark.parametrize("recommended,production,outcome,expected", [
    ("tool", "tool", "ok", telemetry.AGREED_OK),
    ("tool", "tool", "failed", telemetry.AGREED_FAILED),
    ("tool", "large_model", "ok", telemetry.DIVERGED_OK),
    ("tool", "large_model", "failed", telemetry.DIVERGED_FAILED),
    ("tool", "tool", None, telemetry.UNKNOWN),
    (None, "tool", "ok", telemetry.UNKNOWN),
])
def test_regret_names_the_relationship_and_invents_no_counterfactual(
        recommended, production, outcome, expected):
    """The alternative was not run, so its result is unknown. A decimal regret
    would be a measurement of something that did not happen."""
    assert telemetry.regret_of(recommended, production, outcome) == expected


async def test_a_shadow_decision_and_its_outcome_are_both_recorded(env):
    decision = strategy.shadow_route(
        strategy.generate_strategies(deterministic_available=True), permissions=[])
    record = await telemetry.record_shadow(env.svc, decision, task_id=1, run_id=1)
    assert record.recommended == "tool" and record.ranked

    await telemetry.record_outcome(env.svc, record, production_path="large_model",
                                   outcome="failed")
    assert record.regret == telemetry.DIVERGED_FAILED
    board = await telemetry.scoreboard(env.svc)
    assert board["observations"] == 1
    assert board["by_regret"][telemetry.DIVERGED_FAILED] == 1


async def test_the_scoreboard_never_claims_authority(env):
    """A shadow router that is never compared is an opinion nobody checked; one
    that is compared is still not a decision until an owner makes it one."""
    board = await telemetry.scoreboard(env.svc)
    assert board["authoritative"] is False


async def test_recording_a_shadow_decision_changes_no_task_state(env):
    stack = await make_stack(env.client)
    tid = stack["task"]["id"]
    before = (await env.client.get(f"/api/tasks/{tid}")).json()["task"]["status"]
    decision = strategy.shadow_route(
        strategy.generate_strategies(deterministic_available=True), permissions=[])
    await telemetry.record_shadow(env.svc, decision, task_id=tid)
    after = (await env.client.get(f"/api/tasks/{tid}")).json()["task"]["status"]
    assert before == after


# =============================================================== API surface

async def test_the_compile_endpoint_refuses_rather_than_best_efforts(env):
    res = await env.client.post("/api/reality/compile",
                                json={**BASE_INTENT, "side_effect": False,
                                      "effects": [effect("IRREVERSIBLE")]})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "MISSION_IR_REFUSED"


async def test_the_compile_endpoint_creates_no_task(env):
    """Compiling shows the owner what a contract WOULD be. Acting on it still
    goes through the task, permission and approval machinery."""
    before = len((await env.client.get("/api/tasks")).json())
    res = await env.client.post("/api/reality/compile",
                                json={**BASE_INTENT, "effects": [effect()]})
    assert res.status_code == 200 and res.json()["digest"]
    assert len((await env.client.get("/api/tasks")).json()) == before


async def test_the_observe_endpoint_separates_measured_from_unmeasurable(env):
    body = (await env.client.get("/api/reality/observe")).json()
    assert body["available"] + body["unavailable"] == len(body["observations"])
    assert body["unavailable"] >= 1                 # no repo given, so git cannot look
    for obs in body["observations"]:
        assert (obs["value"] is not None) == obs["available"]


async def test_the_strategy_endpoint_is_advisory_in_its_own_response(env):
    body = (await env.client.get("/api/reality/strategies?deterministic=true")).json()
    assert body["shadow_only"] is True
    assert body["ranked"] and "utility" in body["ranked"][0]


async def test_there_is_no_endpoint_that_executes_a_recommendation(env):
    """Structural: an advisory subsystem with an "apply" button is a second
    authorization path."""
    from bcc.features import reality as feature
    routes = {getattr(r, "path", "") for r in feature.router.routes}
    assert not any(word in path for path in routes
                   for word in ("execute", "apply", "promote", "approve", "run"))
