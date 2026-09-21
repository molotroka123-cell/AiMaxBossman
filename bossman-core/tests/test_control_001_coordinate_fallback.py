from __future__ import annotations

from bossman.computer_operator.models import ActionKind, ComputerAction, ExpectedState, TaskMode
from bossman.computer_operator.policy import ComputerPolicy


def _click(*, target="Save", args=None, confidence=1.0, source="planner") -> ComputerAction:
    return ComputerAction.make(
        ActionKind.CLICK,
        expected=ExpectedState(contains_text="ok"),
        target=target,
        args=args or {},
        confidence=confidence,
        source=source,
    )


def test_raw_coordinates_are_not_a_primary_target() -> None:
    decision = ComputerPolicy().classify(
        _click(args={"x": 100, "y": 200}), mode=TaskMode.CONTROL
    )
    assert decision.allow is False
    assert "primary target" in decision.reason


def test_explicit_named_coordinate_fallback_is_allowed_when_confident() -> None:
    decision = ComputerPolicy().classify(
        _click(args={"x": 100, "y": 200, "coordinate_fallback": True}),
        mode=TaskMode.CONTROL,
    )
    assert decision.allow is True


def test_coordinate_fallback_requires_a_named_target() -> None:
    decision = ComputerPolicy().classify(
        _click(target=None, args={"x": 100, "y": 200, "coordinate_fallback": True}),
        mode=TaskMode.CONTROL,
    )
    assert decision.allow is False
    assert "named target" in decision.reason


def test_coordinate_fallback_still_requires_high_confidence() -> None:
    decision = ComputerPolicy().classify(
        _click(args={"x": 100, "y": 200, "coordinate_fallback": True}, confidence=0.5),
        mode=TaskMode.CONTROL,
    )
    assert decision.allow is False
    assert "confidence" in decision.reason


def test_vision_coordinates_are_an_explicit_fallback_not_an_unnamed_click() -> None:
    decision = ComputerPolicy().classify(
        _click(args={"x": 100, "y": 200}, source="vision"),
        mode=TaskMode.CONTROL,
    )
    assert decision.allow is True
