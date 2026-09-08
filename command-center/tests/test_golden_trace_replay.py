"""§9 — the 202-event corpus as a regression, not a souvenir.

Every test here has two halves, and both matter:

  1. the corpus STILL contains the pathology it was chosen for. Without this
     the second half asserts against an empty set and passes forever, which is
     how a regression suite quietly stops testing anything.
  2. current code makes that shape impossible.

The trace is an observer's record, not a deterministic input tape, so nothing
here replays events through the engine — a replay driven by the same extractor
that reads the file would prove only that the extractor is self-consistent.
Instead each pathology is reproduced against the live engine in the shape the
corpus describes, and the corpus supplies the numbers that shape must beat.
"""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from bcc import approval_scope as scope
from bcc import golden_trace as gt
from bcc import mission_budget as mb
from bcc import review_escalation as resc
from bcc.db import approvals as approvals_t, tasks as tasks_t, utcnow

from .helpers import make_stack
from .test_finalize_gate import _status


@pytest.fixture(scope="module")
def corpus():
    return gt.load()


@pytest.fixture(scope="module")
def found(corpus):
    return gt.scenarios(corpus)


# ------------------------------------------------------------- the corpus

def test_the_corpus_is_intact(corpus):
    """The premise of every test below. Raw evidence is immutable: if this
    fails, someone edited the record rather than the code."""
    assert len(corpus) == gt.EXPECTED_EVENTS
    assert {e["phase"] for e in corpus} >= {
        "intervention", "final_verdict", "terminal_status", "task_created"}


def test_every_named_scenario_is_actually_present(found):
    """§9 names six pathologies plus a success control. An extractor that
    silently found nothing would make its regression vacuous."""
    for name in ("review_deadlock", "approval_storm", "token_burn",
                 "provider_down", "owner_interventions", "repeated_reviews",
                 "cloud_routed_tasks"):
        assert found[name].count > 0, f"{name} vanished from the corpus"


# ------------------------------------------- 1. review deadlock (four times)

def test_the_corpus_recorded_the_deadlock_on_three_tasks(found):
    scenario = found["review_deadlock"]
    assert scenario.count >= 4                       # the session's own count
    assert set(scenario.detail["tasks"]) >= {"T1", "T2", "T3"}


async def test_that_shape_is_now_unreachable(env, tmp_path):
    """ReviewDeadlockRate = 0: a task parked behind nothing gets a decision.

    Built from the corpus shape rather than from a mock — task
    `waiting_approval`, zero live approvals — because that is exactly what the
    observer recorded four times."""
    from .test_p0_review_deadlock import _age, _park, _stack
    tid, run = await _stack(env, tmp_path)
    await _park(env, tid, run)
    async with env.svc.db.session() as s:            # the approval goes away
        await s.execute(sa.delete(approvals_t).where(approvals_t.c.task_id == tid))
        await s.commit()
    await _age(env, tid)

    assert (await resc.audit(env.svc))["deadlocked"] == 1        # reproduced
    await resc.reconcile(env.svc)
    assert (await resc.audit(env.svc))["deadlocked"] == 0        # and resolved


async def test_stop_is_no_longer_the_only_escape(env, tmp_path):
    """The corpus records `/stop` as the sole way out. Now the sweep either
    re-asks the owner or fails the task honestly, without a human."""
    from .test_p0_review_deadlock import _age, _park, _stack
    tid, run = await _stack(env, tmp_path)
    await _park(env, tid, run)
    async with env.svc.db.session() as s:
        await s.execute(sa.delete(approvals_t).where(approvals_t.c.task_id == tid))
        await s.commit()
    await _age(env, tid)
    acted = await resc.reconcile(env.svc)
    assert acted and acted[0]["action"] in ("reopened", "failed")
    assert await _status(env, tid) in ("waiting_approval", "failed")


def test_stop_was_used_as_a_recovery_mechanism_in_the_corpus(found):
    """The evidence for the test above: `/stop` really is in the record."""
    assert found["owner_interventions"].detail["kinds"].get("stop_task", 0) >= 1


# ------------------------------------------------- 2. approval storm (161)

def test_the_corpus_recorded_the_storm(found):
    scenario = found["approval_storm"]
    assert scenario.count == 161
    assert scenario.detail["worst_count"] >= 60      # T3 alone
    assert all(count >= 30 for count in scenario.detail["per_task"].values())


async def test_one_scoped_decision_now_covers_what_cost_thirty(env):
    """The corpus's cheapest task still cost 30 confirmations. A single scoped
    lease covers that many in-scope calls, and the count of owner questions is
    what changed — not what any one answer authorizes."""
    stack = await make_stack(env.client)
    tid, agent = stack["task"]["id"], stack["agent"]["id"]
    sc = scope.Scope(tool="terminal.run", effect_class=scope.READ,
                     scope_key="sandbox", agent_id=agent, task_id=tid)
    await scope.grant(env.svc, approval={"id": None}, scope=sc,
                      max_uses=30, ttl_seconds=600)
    for _ in range(30):
        assert await scope.consume(env.svc, sc) is not None
    assert await scope.spent(env.svc, tid) == 0      # zero owner questions asked


async def test_a_write_still_costs_its_own_decision(env):
    """The negative control the storm fix lives or dies on: fewer questions
    must not mean wider authority."""
    stack = await make_stack(env.client)
    tid, agent = stack["task"]["id"], stack["agent"]["id"]
    read = scope.Scope(tool="terminal.run", effect_class=scope.READ,
                       scope_key="sandbox", agent_id=agent, task_id=tid)
    await scope.grant(env.svc, approval={"id": None}, scope=read,
                      max_uses=200, ttl_seconds=600)
    write = scope.Scope(tool="terminal.run", effect_class=scope.WRITE,
                        scope_key="sandbox", agent_id=agent, task_id=tid)
    assert await scope.consume(env.svc, write) is None


# --------------------------------------------------- 3. token burn (1.28M)

def test_the_corpus_recorded_the_burn(found):
    scenario = found["token_burn"]
    assert scenario.detail["peak_tokens"] > 1_000_000
    assert scenario.detail["peak_cost_usd"] > 4.0
    assert scenario.detail["worst_task"] == "T3"     # a documentation edit


def test_the_default_budget_would_have_stopped_it(found):
    """Numbers from the corpus, not from the test author: the ceiling must trip
    on what actually happened."""
    peak = found["token_burn"].detail["peak_tokens"]
    cost = found["token_burn"].detail["peak_cost_usd"]
    limits = mb.Limits.for_task(None)
    assert mb.check_spend(limits, tokens_in=peak, tokens_out=0, cost_usd=0).code \
        == "TOKEN_BUDGET_EXCEEDED"
    assert mb.check_spend(limits, tokens_in=0, tokens_out=0, cost_usd=cost).code \
        == "COST_BUDGET_EXCEEDED"


#: The corpus separates two populations, and the boundary is the corpus's own,
#: not a number chosen here. Runs that were still doing real work top out at
#: T2's first sample (154 405 + 16 326 = 170 731) and T4's tester-capped run
#: (109 563 + 12 766 = 122 329). The runaway loops start at T2's final sample
#: (432 257 + 31 197 = 463 454) and run to T3's 1 295 189 — and the corpus calls
#: BOTH of those pathologies: T2 "phantom obligation ... review FAIL x2 ->
#: review_escalated -> waiting_approval deadlock AGAIN", verdict PARTIAL.
#: A first draft of this test filed T2's 463k under "legitimate" because it was
#: merely smaller than T3's; the ceiling caught it and the classification was
#: wrong, not the ceiling.
WORKING_RUN_CEILING = 171_000
RUNAWAY_LOOP_FLOOR = 463_000


def test_the_budget_sits_between_the_two_populations_in_the_corpus(corpus):
    """The other half: a ceiling that also kills the healthy runs in the same
    corpus is not a fix, it is a smaller failure."""
    limits = mb.Limits.for_task(None)
    assert WORKING_RUN_CEILING < limits.max_tokens < RUNAWAY_LOOP_FLOOR

    working, runaway = [], []
    for event in corpus:
        payload = event.get("payload") or {}
        tokens = payload.get("tokens_in")
        if not isinstance(tokens, int) or tokens <= 0:
            continue
        sample = (tokens, int(payload.get("tokens_out") or 0),
                  float(payload.get("cost_usd") or 0))
        (working if tokens + sample[1] <= WORKING_RUN_CEILING else runaway).append(sample)

    assert working and runaway, "the corpus must contain both populations"
    for tokens_in, tokens_out, cost in working:
        assert mb.check_spend(limits, tokens_in=tokens_in, tokens_out=tokens_out,
                              cost_usd=cost) is None, "a working run was stopped"
    for tokens_in, tokens_out, cost in runaway:
        assert mb.check_spend(limits, tokens_in=tokens_in, tokens_out=tokens_out,
                              cost_usd=cost) is not None, "a runaway loop was allowed"


# ---------------------------------------- 4. provider down (negative control)

def test_the_corpus_contains_an_honest_provider_failure(found):
    """The session's own negative control: the key expired and the task FAILED
    rather than reporting success. That behaviour must not regress either."""
    assert found["provider_down"].count >= 1
    blob = str(found["provider_down"].evidence)
    assert "401" in blob or "expired" in blob


# -------------------------------------------------- 5. false-negative reviews

def test_the_corpus_recorded_the_phantom_obligation(found):
    scenario = found["repeated_reviews"]
    assert scenario.count >= 1
    assert scenario.detail["phantom_obligation_events"] >= 1


def test_a_url_no_longer_manufactures_a_file_obligation():
    """The root cause of two of the four deadlocks: `https://example.com`
    matched the filename regex and became `file:example.com`, an obligation
    nobody promised and nothing could satisfy."""
    from bcc.features import action_contract as ac
    assert ac._terminal_evidence(
        "Open https://example.com and record the title.") is None
    # and a real file next to a URL is still an obligation
    kept = ac._terminal_evidence(
        "Read https://example.com and save the facts to t2-web.json.")
    assert kept is not None and kept.target == "t2-web.json"


# ----------------------------------------- 6. successful tasks (not only bugs)

def test_the_corpus_also_contains_tasks_that_reached_a_verdict(found):
    """A suite built only from pathologies cannot tell "fixed" from "broke
    everything"."""
    assert found["cloud_routed_tasks"].count >= 4
    assert found["cloud_routed_tasks"].detail["verdicts"]


async def test_an_ordinary_task_still_completes(env):
    """The blunt control for every guard added by this run: none of them may
    stop a healthy task."""
    from .conftest import FakeAdapter
    stack = await make_stack(env.client)
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("готово")
    run = await env.svc.engine.claim()
    await env.svc.engine.execute(run)
    assert await _status(env, stack["task"]["id"]) == "completed"


# ------------------------------------------------------------- the report

def test_the_summary_is_derived_not_transcribed():
    """The acceptance report quotes these numbers, so they are recomputed from
    the raw file on every run rather than copied into a document."""
    report = gt.summary()
    assert report["events"] == gt.EXPECTED_EVENTS
    assert report["scenarios"]["approval_storm"]["count"] == 161
    assert report["scenarios"]["token_burn"]["peak_tokens"] > 1_000_000
