"""Portable observation-contract regressions; no live browser/Windows claims."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from bossman_v3.contracts import TypedAction
from bossman_v3.visual_state import (
    AmbiguousVisualStateError, SemanticActionStateGuard, StateFragment,
    StateIdentity, StaleVisualStateError, VisualStateEngine,
)

NOW = datetime(2026, 9, 5, tzinfo=timezone.utc)
IDENTITY = StateIdentity("browser", "window-A", "document-A", 1, 1)
ACTION = TypedAction("browser.click", {"element": "save", "x": 10, "y": 20})
STATE = {"element": "save", "label": "Save draft", "enabled": True, "active": True, "modal": None}


def fragment(*, age=0, identity=IDENTITY, kind="dom", payload=None, confidence=1.0):
    return StateFragment(kind, NOW - timedelta(seconds=age), STATE if payload is None else payload,
                         "fixture-adapter", confidence=confidence, identity=identity)


@pytest.mark.parametrize("kind", ["dom", "a11y", "vision", "screenshot"])
def test_fresh_fragment_cannot_hide_an_old_fragment(kind):
    # Original engine picked max(observed_at), accepted old DOM + fresh screenshot.
    with pytest.raises(StaleVisualStateError):
        VisualStateEngine().fuse([fragment(age=6, kind=kind), fragment()], now=NOW)


@pytest.mark.parametrize("age", [-0.000001, -60, -86400])
def test_future_fragment_rejected_even_if_another_fragment_is_current(age):
    with pytest.raises(StaleVisualStateError, match="future"):
        VisualStateEngine().fuse([fragment(age=age), fragment()], now=NOW)


def test_legacy_no_identity_and_compact_shape_remain_supported():
    snapshot = VisualStateEngine().fuse([fragment(identity=None)], now=NOW)
    assert snapshot.structured == STATE
    assert set(snapshot.compact()) == {"observed_at", "structured", "screenshot_refs", "conflicts"}


def test_legacy_conflicts_are_reported_strict_conflicts_block():
    fragments = [fragment(), fragment(kind="a11y", payload={"label": "Delete permanently"})]
    assert VisualStateEngine().fuse(fragments, now=NOW).conflicts
    with pytest.raises(AmbiguousVisualStateError, match="conflicting structured"):
        VisualStateEngine(require_identity=True).fuse(fragments, now=NOW)


@pytest.mark.parametrize("change", [
    {"application_id": "mail"}, {"window_id": "window-B"}, {"document_id": "document-B"},
    {"navigation_generation": 2}, {"state_revision": 2},
])
def test_cross_window_document_navigation_and_revision_rejected(change):
    other = replace(IDENTITY, **change)
    with pytest.raises(AmbiguousVisualStateError, match="identity"):
        VisualStateEngine(require_identity=True).fuse([fragment(), fragment(identity=other)], now=NOW)
    guard = SemanticActionStateGuard()
    binding = guard.bind(ACTION, [fragment()], required_state=STATE, now=NOW)
    with pytest.raises(AmbiguousVisualStateError, match="reobserve"):
        guard.check(ACTION, binding, [fragment(identity=other)], now=NOW)


@pytest.mark.parametrize("fragments", [[fragment(identity=None)], [fragment(kind="vision")]])
def test_incomplete_identity_or_vision_alone_cannot_bind_action(fragments):
    with pytest.raises(AmbiguousVisualStateError):
        SemanticActionStateGuard().bind(ACTION, fragments, required_state=STATE, now=NOW)


def test_fresh_state_success_is_only_a_check_no_dispatch_or_receipt():
    guard = SemanticActionStateGuard()
    binding = guard.bind(ACTION, [fragment()], required_state=STATE, now=NOW)
    assert guard.check(ACTION, binding, [fragment(age=-1)], now=NOW + timedelta(seconds=1)) is None


@pytest.mark.parametrize("change", [
    {"label": "Delete permanently"}, {"enabled": False}, {"active": False},
    {"modal": "Confirm transfer"}, {"element": "send"}, {"enabled": 1},
])
def test_semantic_target_changes_block_even_if_adapter_reuses_revision(change):
    guard = SemanticActionStateGuard()
    binding = guard.bind(ACTION, [fragment()], required_state=STATE, now=NOW)
    with pytest.raises(AmbiguousVisualStateError, match="semantic precondition"):
        guard.check(ACTION, binding, [fragment(payload={**STATE, **change})], now=NOW)


def test_vision_cannot_supply_missing_semantic_precondition():
    with pytest.raises(AmbiguousVisualStateError, match="no structured observation"):
        SemanticActionStateGuard().bind(ACTION, [
            fragment(payload={"element": "save"}), fragment(kind="vision", payload={"enabled": True}),
        ], required_state={"enabled": True}, now=NOW)


def test_action_argument_mutation_cannot_reuse_state_binding():
    args = {"element": "save", "position": {"x": 10}}
    action = TypedAction("browser.click", args)
    guard = SemanticActionStateGuard()
    binding = guard.bind(action, [fragment()], required_state=STATE, now=NOW)
    args["position"]["x"] = 999
    with pytest.raises(AmbiguousVisualStateError, match="action changed"):
        guard.check(action, binding, [fragment()], now=NOW)


def test_binding_copies_nested_semantic_expectations():
    required = {"element": {"id": "save"}}
    state = fragment(payload={"element": {"id": "save"}})
    guard = SemanticActionStateGuard()
    binding = guard.bind(ACTION, [state], required_state=required, now=NOW)
    required["element"]["id"] = "send"
    guard.check(ACTION, binding, [state], now=NOW)
    with pytest.raises(AmbiguousVisualStateError):
        guard.check(ACTION, binding, [fragment(payload=required)], now=NOW)


def test_all_fragments_checked_again_at_dispatch_time():
    guard = SemanticActionStateGuard()
    binding = guard.bind(ACTION, [fragment(age=4), fragment(kind="screenshot")], required_state=STATE, now=NOW)
    with pytest.raises(StaleVisualStateError):
        guard.check(ACTION, binding, [fragment(age=4), fragment(kind="screenshot", age=-2)],
                    now=NOW + timedelta(seconds=2))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1])
def test_invalid_freshness_limit_rejected(value):
    with pytest.raises(ValueError):
        VisualStateEngine(value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, 1.1])
def test_invalid_confidence_rejected(value):
    with pytest.raises(ValueError):
        VisualStateEngine().fuse([fragment(confidence=value)], now=NOW)


def test_low_confidence_structured_state_cannot_be_actionable():
    with pytest.raises(AmbiguousVisualStateError, match="confidence"):
        SemanticActionStateGuard().bind(ACTION, [fragment(confidence=.3)], required_state=STATE, now=NOW)


def test_naive_time_rejected_with_explicit_error():
    with pytest.raises(ValueError, match="timezone-aware"):
        VisualStateEngine().fuse([replace(fragment(), observed_at=NOW.replace(tzinfo=None))], now=NOW)


def test_empty_semantic_condition_cannot_bind():
    with pytest.raises(ValueError, match="precondition"):
        SemanticActionStateGuard().bind(ACTION, [fragment()], required_state={}, now=NOW)


def test_nonfinite_action_argument_rejected():
    with pytest.raises(ValueError):
        SemanticActionStateGuard().bind(TypedAction("browser.click", {"x": float("nan")}),
                                       [fragment()], required_state=STATE, now=NOW)


def test_strict_snapshot_is_detached_and_deeply_immutable():
    payload = {"element": {"id": "save", "positions": [10, 20]}}
    snapshot = VisualStateEngine(require_identity=True).fuse([fragment(payload=payload)], now=NOW)
    payload["element"]["id"] = "delete"
    payload["element"]["positions"][0] = 999
    assert snapshot.structured["element"]["id"] == "save"
    assert snapshot.structured["element"]["positions"] == (10, 20)
    with pytest.raises(TypeError):
        snapshot.structured["element"]["id"] = "delete"
    # The existing compact interface stays JSON serializable.
    import json
    assert json.loads(json.dumps(snapshot.compact()))["structured"]["element"]["id"] == "save"


@pytest.mark.parametrize("values", [(True, 1), ({"enabled": True}, {"enabled": 1})])
def test_strict_conflicts_use_typed_json_equality(values):
    with pytest.raises(AmbiguousVisualStateError, match="conflicting structured"):
        VisualStateEngine(require_identity=True).fuse([
            fragment(payload={"target": values[0]}), fragment(kind="a11y", payload={"target": values[1]}),
        ], now=NOW)


def test_nested_non_string_keys_cannot_be_reinterpreted():
    with pytest.raises(ValueError, match="keys must be strings"):
        SemanticActionStateGuard().bind(ACTION, [fragment(payload={"target": {1: "save"}})],
                                       required_state={"target": {"1": "save"}}, now=NOW)


def test_reserved_vision_namespace_cannot_be_injected_as_structured_authority():
    with pytest.raises(ValueError, match="vision_hint"):
        VisualStateEngine(require_identity=True).fuse([
            fragment(payload={"vision_hint.enabled": True}),
        ], now=NOW)


def test_observation_iterator_cannot_mutate_action_during_check():
    args = {"element": "save"}
    action = TypedAction("browser.click", args)
    guard = SemanticActionStateGuard()
    binding = guard.bind(action, [fragment()], required_state=STATE, now=NOW)

    def observations():
        args["element"] = "delete"
        yield fragment()

    with pytest.raises(AmbiguousVisualStateError, match="action changed during state check"):
        guard.check(action, binding, observations(), now=NOW)


def test_observation_iterator_cannot_mutate_action_during_binding():
    args = {"element": "save"}
    action = TypedAction("browser.click", args)

    def observations():
        args["element"] = "delete"
        yield fragment()

    with pytest.raises(AmbiguousVisualStateError, match="action changed during state binding"):
        SemanticActionStateGuard().bind(action, observations(), required_state=STATE, now=NOW)


def test_observation_iterator_cannot_rewrite_required_semantics_during_binding():
    required = {"element": {"id": "save"}}

    def observations():
        required["element"]["id"] = "delete"
        yield fragment(payload={"element": {"id": "delete"}})

    with pytest.raises(AmbiguousVisualStateError, match="semantic precondition"):
        SemanticActionStateGuard().bind(ACTION, observations(), required_state=required, now=NOW)
