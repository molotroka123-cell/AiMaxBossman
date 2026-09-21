"""CONTROL-001: coordinates are a named, explicit fallback, never the primary target."""
from bossman.computer_operator.models import ActionKind, ComputerAction, ExpectedState, TaskMode
from bossman.computer_operator.policy import ComputerPolicy


def action(*, target="Save", source="planner", confidence=1.0, fallback=False):
    return ComputerAction.make(
        ActionKind.CLICK,
        expected=ExpectedState(contains_text="Saved"),
        target=target,
        source=source,
        confidence=confidence,
        args={"x": 100, "y": 200, **({"coordinate_fallback": True} if fallback else {})},
    )


def test_raw_planner_coordinates_are_not_primary_target():
    decision=ComputerPolicy().classify(action(), mode=TaskMode.CONTROL)
    assert decision.allow is False
    assert "not a primary target" in decision.reason


def test_visual_grounding_coordinates_are_allowed_for_named_target():
    decision=ComputerPolicy().classify(action(source="vision"), mode=TaskMode.CONTROL)
    assert decision.allow is True


def test_explicit_coordinate_fallback_is_allowed_for_named_target():
    decision=ComputerPolicy().classify(action(fallback=True), mode=TaskMode.CONTROL)
    assert decision.allow is True


def test_coordinate_fallback_without_named_target_is_denied():
    decision=ComputerPolicy().classify(action(target="", fallback=True), mode=TaskMode.CONTROL)
    assert decision.allow is False
    assert "named target" in decision.reason


def test_low_confidence_visual_fallback_is_denied():
    decision=ComputerPolicy().classify(action(source="vision", confidence=.1), mode=TaskMode.CONTROL)
    assert decision.allow is False
    assert "low vision confidence" in decision.reason
