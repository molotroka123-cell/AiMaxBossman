"""PHASE 6/14 closure — behaviour tests for core code the gate measured at 0 %.

Why this file exists
--------------------
The core coverage gate runs `--cov=bossman_v3` and reported 88.89 % against an
85 % floor.  Three packages inside that measurement were at **0 %**:
`bossman_v3.skill_factory`, `bossman_v3.visual_state` and
`bossman_v3.adapters`.  Zero percent is not automatically dead code — each of
these encodes a stated safety boundary (a raw shell must never become a learned
skill; a vision guess must never become authoritative state; an adapter must
never invent permission it was not given).  An unexercised boundary is a
boundary nobody has proved, so the percentage was honest arithmetic over
dishonest confidence.

Every test below is a behaviour test: it drives the public API and asserts on
the observable outcome.  None of them assert "this line ran".
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bossman_v3.adapters.bossman_core import ExistingApprovalAdapter, ExistingPolicyAdapter
from bossman_v3.contracts import ApprovalDecision, PolicyDecision, TypedAction
from bossman_v3.skill_factory.factory import (
    PromotionEvidence,
    SkillCandidate,
    SkillFactory,
    SkillStage,
    TraceStep,
)
from bossman_v3.skill_factory.reliability import reliability_lcb
from bossman_v3.visual_state.fusion import StaleVisualStateError, VisualStateEngine
from bossman_v3.visual_state.models import StateFragment


# --------------------------------------------------------------------------
# skill_factory — a skill is learned from verified evidence, never from a shell
# --------------------------------------------------------------------------

def _click(name: str = "ui.click") -> TypedAction:
    return TypedAction(action_type=name, args={"selector": "#ok"})


def _verified_trace() -> list[TraceStep]:
    return [TraceStep(action=_click(), verified=True),
            TraceStep(action=_click("ui.type"), verified=True)]


SCHEMA_IN = {"target": "str"}
SCHEMA_OUT = {"receipt": "str"}


def _skill(factory: SkillFactory | None = None) -> SkillCandidate:
    factory = factory or SkillFactory()
    return factory.from_verified_trace("checkout", _verified_trace(),
                                       input_schema=SCHEMA_IN, output_schema=SCHEMA_OUT)


def test_unverified_step_cannot_become_a_skill():
    """One unverified step poisons the whole trace: partial verification is not
    verification, and a skill minted from it would carry the gap forever."""
    trace = _verified_trace()
    trace.append(TraceStep(action=_click("ui.submit"), verified=False))
    with pytest.raises(ValueError, match="fully verified"):
        SkillFactory().from_verified_trace("bad", trace,
                                           input_schema=SCHEMA_IN, output_schema=SCHEMA_OUT)


def test_empty_trace_cannot_become_a_skill():
    """No evidence at all must refuse just as loudly as bad evidence, otherwise
    an empty skill would promote on a vacuous `all()`."""
    with pytest.raises(ValueError, match="fully verified"):
        SkillFactory().from_verified_trace("empty", [],
                                           input_schema=SCHEMA_IN, output_schema=SCHEMA_OUT)


@pytest.mark.parametrize("shell_like", sorted(SkillFactory.RAW_SHELL) +
                         ["SHELL", "Bash", "shell.run", "shell.exec_anything"])
def test_raw_shell_can_never_be_learned_as_a_skill(shell_like):
    """The typed-action boundary: a learned skill is replayed later without a
    fresh human decision, so a shell step inside one is an arbitrary-command
    primitive with the approval already spent."""
    trace = [TraceStep(action=_click(), verified=True),
             TraceStep(action=TypedAction(action_type=shell_like, args={"cmd": "rm -rf /"}),
                       verified=True)]
    with pytest.raises(ValueError, match="raw shell"):
        SkillFactory().from_verified_trace("sneaky", trace,
                                           input_schema=SCHEMA_IN, output_schema=SCHEMA_OUT)


def test_fresh_skill_starts_experimental_and_unmeasured():
    cand = _skill()
    assert cand.stage is SkillStage.EXPERIMENTAL
    assert cand.status == "ESTIMATED"
    assert (cand.successes, cand.failures) == (0, 0)
    assert cand.actions == tuple(step.action for step in _verified_trace())


def test_reliability_is_a_lower_bound_not_a_success_rate():
    """0/0 and 1/1 are both "100 % success" as a ratio.  The gate must see them
    as almost no evidence, or one lucky trial promotes a skill."""
    assert reliability_lcb(0, 0) == pytest.approx(0.05, abs=1e-3)
    assert reliability_lcb(1, 0) < 0.30
    assert reliability_lcb(10, 0) < reliability_lcb(20, 0) < reliability_lcb(40, 0) < 1.0
    # a failure must always lower the bound, never leave it flat
    assert reliability_lcb(20, 1) < reliability_lcb(20, 0)


def test_only_recorded_outcomes_can_move_the_reliability_bound():
    """`status` is the honesty flag: any path that changes the counters must
    also flip ESTIMATED → MEASURED, so the gate can never read an unmeasured
    candidate's numbers as if they had been observed."""
    cand = _skill()
    assert cand.status == "ESTIMATED"
    SkillFactory.record(cand, True)
    assert cand.status == "MEASURED" and cand.successes == 1
    SkillFactory.record(cand, False)
    assert cand.status == "MEASURED" and cand.failures == 1


def _evidence(**over) -> PromotionEvidence:
    base = dict(trials=20, verified_success=0.95, baseline_verified_success=0.95,
                security_failures=0, baseline_security_failures=0, benchmark_present=True)
    base.update(over)
    return PromotionEvidence(**base)


def _shadow_candidate(successes: int, failures: int = 0) -> SkillCandidate:
    cand = _skill()
    cand.stage = SkillStage.SHADOW
    for _ in range(successes):
        SkillFactory.record(cand, True)
    for _ in range(failures):
        SkillFactory.record(cand, False)
    return cand


def test_experimental_needs_a_benchmark_to_reach_shadow():
    factory = SkillFactory()
    cand = _skill(factory)
    assert factory.promote(cand, _evidence(benchmark_present=False)) is SkillStage.EXPERIMENTAL
    assert factory.promote(cand, _evidence(trials=0)) is SkillStage.EXPERIMENTAL
    assert factory.promote(cand, _evidence(trials=1)) is SkillStage.SHADOW


def test_shadow_refuses_production_below_the_trial_floor():
    factory = SkillFactory()
    cand = _shadow_candidate(19)
    assert factory.promote(cand, _evidence(trials=19)) is SkillStage.SHADOW


def test_shadow_refuses_production_on_a_security_regression():
    """A skill that fails security more often than the baseline it replaces must
    not ship, no matter how reliable or fast it is."""
    factory = SkillFactory()
    cand = _shadow_candidate(40)
    assert factory.promote(cand, _evidence(security_failures=1, baseline_security_failures=0)) \
        is SkillStage.SHADOW


def test_shadow_refuses_production_on_a_quality_drop():
    factory = SkillFactory()
    cand = _shadow_candidate(40)
    assert factory.promote(cand, _evidence(verified_success=0.90,
                                           baseline_verified_success=0.95)) is SkillStage.SHADOW


def test_shadow_refuses_production_when_the_lower_bound_is_under_the_floor():
    """20 trials with a single failure look like 95 % but the 5 % lower bound is
    0.793 — under the 0.80 floor.  The point estimate must not be what ships."""
    factory = SkillFactory()
    cand = _shadow_candidate(19, failures=1)
    assert cand.reliability_lcb() < 0.80
    assert factory.promote(cand, _evidence(trials=20)) is SkillStage.SHADOW


def test_shadow_promotes_only_with_the_whole_evidence_set():
    factory = SkillFactory()
    cand = _shadow_candidate(20)
    assert cand.reliability_lcb() >= 0.80
    assert factory.promote(cand, _evidence(trials=20)) is SkillStage.PRODUCTION
    # promotion is terminal, not a per-call recomputation that can slide back
    assert factory.promote(cand, _evidence(trials=20)) is SkillStage.PRODUCTION


def test_typed_composition_requires_name_and_type_agreement():
    upstream = SkillCandidate("a", {}, {"receipt": "str", "count": "int"}, ())
    good = SkillCandidate("b", {"receipt": "str"}, {}, ())
    wrong_type = SkillCandidate("c", {"receipt": "int"}, {}, ())
    missing = SkillCandidate("d", {"absent": "str"}, {}, ())
    assert SkillFactory.typed_compatible(upstream, good) is True
    assert SkillFactory.typed_compatible(upstream, wrong_type) is False
    assert SkillFactory.typed_compatible(upstream, missing) is False


# --------------------------------------------------------------------------
# visual_state — vision is evidence, structured observation is authority
# --------------------------------------------------------------------------

NOW = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)


def _frag(kind: str, payload: dict, *, age_s: float = 0.0, source: str = "s",
          ref: str | None = None, confidence: float = 1.0) -> StateFragment:
    return StateFragment(kind=kind, observed_at=NOW - timedelta(seconds=age_s),
                         payload=payload, source=source, artifact_ref=ref,
                         confidence=confidence)


def test_fusion_refuses_an_empty_observation_set():
    with pytest.raises(ValueError):
        VisualStateEngine().fuse([], now=NOW)


def test_fusion_refuses_state_older_than_the_freshness_window():
    engine = VisualStateEngine(max_age_seconds=5.0)
    with pytest.raises(StaleVisualStateError):
        engine.fuse([_frag("dom", {"title": "old"}, age_s=6.0)], now=NOW)
    # exactly at the boundary is still usable — the window must not be off by one
    assert engine.fuse([_frag("dom", {"title": "ok"}, age_s=5.0)], now=NOW).structured["title"] == "ok"


def test_vision_never_overrides_a_structured_observation():
    """The whole point of the engine: a model looking at a screenshot may not
    silently replace what the DOM/accessibility tree actually says."""
    snap = VisualStateEngine().fuse(
        [_frag("dom", {"balance": "10.00"}),
         _frag("vision", {"balance": "1000.00"}, ref="art:png", confidence=0.99)],
        now=NOW)
    assert snap.structured["balance"] == "10.00"
    assert snap.structured["vision_hint.balance"] == "1000.00"
    assert "balance" not in snap.conflicts


def test_vision_only_fills_fields_structured_state_never_claimed():
    snap = VisualStateEngine().fuse(
        [_frag("dom", {"title": "Cart"}),
         _frag("vision", {"spinner": "visible"}, ref="art:png")], now=NOW)
    assert snap.structured["title"] == "Cart"
    assert snap.structured["vision_hint.spinner"] == "visible"
    assert "spinner" not in snap.structured, "a vision guess must stay namespaced"


def test_disagreeing_structured_sources_are_reported_not_quietly_merged():
    snap = VisualStateEngine().fuse(
        [_frag("dom", {"state": "paid"}, confidence=0.9),
         _frag("accessibility", {"state": "unpaid"}, confidence=0.4)], now=NOW)
    assert snap.conflicts == ("state: conflicting structured observations",)
    assert snap.structured["state"] == "paid", "higher-confidence structured source wins"


def test_agreeing_structured_sources_do_not_raise_a_false_conflict():
    snap = VisualStateEngine().fuse(
        [_frag("dom", {"state": "paid"}), _frag("a11y", {"state": "paid"})], now=NOW)
    assert snap.conflicts == ()


def test_artifact_refs_come_only_from_image_kinds_and_are_deduplicated():
    snap = VisualStateEngine().fuse(
        [_frag("dom", {"a": 1}, ref="art:dom-should-not-be-listed"),
         _frag("screenshot", {}, ref="art:shot"),
         _frag("vision", {"b": 2}, ref="art:shot"),
         _frag("vision", {"c": 3}, ref="art:other")], now=NOW)
    assert snap.screenshot_refs == ("art:shot", "art:other")


def test_every_fragment_is_named_in_the_provenance():
    frags = [_frag("dom", {"a": 1}, source="chromium"),
             _frag("vision", {"b": 2}, source="vlm", ref="art:x")]
    snap = VisualStateEngine().fuse(frags, now=NOW)
    assert len(snap.provenance) == len(frags)
    assert any(p.startswith("dom:chromium:") for p in snap.provenance)
    assert any(p.startswith("vision:vlm:") for p in snap.provenance)


def test_compact_view_keeps_the_conflict_warning():
    """`compact()` is what a token budget hands to the model.  Dropping the
    conflict list there would turn a known disagreement into confident state."""
    snap = VisualStateEngine().fuse(
        [_frag("dom", {"state": "paid"}), _frag("a11y", {"state": "unpaid"})], now=NOW)
    compact = snap.compact()
    assert compact["conflicts"] == snap.conflicts
    assert compact["observed_at"] == snap.observed_at.isoformat()
    assert compact["structured"] == snap.structured


# --------------------------------------------------------------------------
# adapters — a shim may translate a decision, never manufacture one
# --------------------------------------------------------------------------

class _Foreign:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class _Policy:
    def __init__(self, result):
        self.result = result
        self.calls: list[TypedAction] = []

    def authorize(self, action, context):
        self.calls.append(action)
        return self.result


class _Approval:
    def __init__(self, result):
        self.result = result

    def request(self, action, context):
        return self.result


def test_policy_adapter_passes_a_native_decision_through_unchanged():
    native = PolicyDecision(True, True, "needs owner")
    canonical = _Policy(native)
    out = ExistingPolicyAdapter(canonical).authorize(_click(), {"run": 1})
    assert out is native
    assert canonical.calls == [_click()]


def test_policy_adapter_denies_a_foreign_result_that_never_said_allowed():
    """Fail closed on shape drift: a canonical policy that stops exposing
    `allowed` must read as a denial, not as silent permission."""
    out = ExistingPolicyAdapter(_Policy(_Foreign(reason="schema changed"))).authorize(_click(), {})
    assert out.allowed is False
    assert out.reason == "schema changed"


def test_policy_adapter_denies_a_dict_shaped_result():
    """A mapping has no attributes — `{"allowed": True}` must not read as allowed."""
    out = ExistingPolicyAdapter(_Policy({"allowed": True})).authorize(_click(), {})
    assert out.allowed is False


def test_approval_adapter_passes_a_native_decision_through_unchanged():
    native = ApprovalDecision(True, "ap-1", "owner said yes")
    out = ExistingApprovalAdapter(_Approval(native)).request(_click(), PolicyDecision(True, True), {})
    assert out is native


def test_approval_adapter_denies_a_foreign_result_that_never_said_approved():
    out = ExistingApprovalAdapter(_Approval(_Foreign(approval_id="ap-2"))).request(
        _click(), PolicyDecision(True, True), {})
    assert out.approved is False and out.approval_id == "ap-2"


# --------------------------------------------------------------------------
# RES-001 — a component that creates a temp store must reclaim it
# --------------------------------------------------------------------------

def test_owned_search_engine_reclaims_the_directory_it_created(tmp_path, monkeypatch):
    """`SearchEngine()` with no store builds its own `mkdtemp` directory.

    Reproduced before the fix: eight repetitions of `tests/test_stage4_7.py` in
    one process left eight `bossman_search_*` directories behind — one per
    engine, never reclaimed, each holding a SQLite file.  `close()` shut the
    connection and walked away from the directory.
    """
    import tempfile

    from bossman.search_everything.engine import SearchDocument, SearchEngine

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    before = set(tmp_path.iterdir())
    engine = SearchEngine()
    created = set(tmp_path.iterdir()) - before
    assert len(created) == 1, "engine must build exactly one private store directory"
    engine.upsert([SearchDocument("1", "browser context memory", "repo", "p")])
    engine.close()
    assert set(tmp_path.iterdir()) == before, "close() left its private store behind"


def test_borrowed_search_engine_never_deletes_a_caller_owned_store(tmp_path):
    """The mirror invariant: a store the caller supplied is not ours to delete."""
    from bossman.context_engine import ContextEngine
    from bossman.search_everything.engine import SearchEngine

    db = tmp_path / "caller.db"
    shared = ContextEngine(db)
    borrowed = SearchEngine(shared)
    borrowed.close()
    assert db.exists(), "borrowed store was deleted by a consumer that did not own it"
    shared.close()


def test_search_engine_with_explicit_db_path_keeps_that_path(tmp_path):
    """`db_path=` is a caller decision, not a temp store: close() must keep it."""
    from bossman.search_everything.engine import SearchEngine

    db = tmp_path / "explicit" / "context.db"
    db.parent.mkdir(parents=True)
    engine = SearchEngine(db_path=db)
    engine.close()
    assert db.parent.exists() and db.exists()


# --------------------------------------------------------------------------
# ISO-001 — the suite must not keep durable state inside the source tree
# --------------------------------------------------------------------------

def test_suite_workspace_is_not_the_source_tree():
    """Reproduced before the fix: `bossman-core/workspace/_notifications/
    notifications.db` and `.../_cost_control/cost_control.db` were created by
    the suite inside the checkout and never cleaned.  `callback_tokens` grew
    2 → 5 over three full runs, and two engineers running the suite at once
    wrote the same SQLite file.  Both stores are built at *import* time from
    `settings.workspace_dir`, so no per-test monkeypatch can redirect them —
    the redirection has to happen before the first `import bossman.*`.
    """
    from bossman.config import ROOT, settings

    workspace = Path(settings.workspace_dir).resolve()
    assert not workspace.is_relative_to(Path(ROOT).resolve()), (
        f"tests write durable state into the checkout: {workspace}")


def test_import_time_sqlite_stores_follow_the_isolated_workspace():
    """The two stores that are constructed at import time must land in the
    isolated workspace, not next to the source they were imported from."""
    from bossman.config import ROOT
    from bossman.cost_control.runtime import STORE as cost_store
    from bossman.notifications.runtime import STORE as notification_store

    root = Path(ROOT).resolve()
    for label, store in (("cost_control", cost_store), ("notifications", notification_store)):
        path = Path(getattr(store, "path", getattr(store, "_path", ""))).resolve()
        assert str(path) not in ("", "."), f"{label} store exposes no path to check"
        assert not path.is_relative_to(root), f"{label} store writes into the checkout: {path}"
