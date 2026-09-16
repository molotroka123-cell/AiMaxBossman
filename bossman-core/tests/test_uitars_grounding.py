"""UI-TARS parser and production wiring without a desktop or model download."""
import base64
import io
import json
from types import SimpleNamespace

import pytest

from bossman.computer_operator.models import ActionKind, Observation, TaskMode
from bossman.computer_operator.policy import ComputerPolicy
from bossman.computer_operator.uitars import UiTarsError, UiTarsPlanner, parse_grounding


def parse(text, kind=ActionKind.CLICK):
    return parse_grounding(text, width=1920, height=1080,
                           image_width=1932, image_height=1092, expected_kind=kind)


def test_native_qwen_coordinates_are_rescaled_not_normalized_1000():
    assert parse("Thought: target found\nAction: click(start_box='(966,546)')") == {"x": 960, "y": 540}
    assert parse("Action: left_double(point='<point>966 546</point>')",
                 ActionKind.DOUBLE_CLICK) == {"x": 960, "y": 540}


@pytest.mark.parametrize("text", [
    "Action: click(start_box='(1932,546)')", "Action: click(start_box='(-1,0)')",
    "Action: click(start_box='(nan,0)')", "Action: right_single(start_box='(20,30)')",
    "Action: drag(start_box='(20,30)', end_box='(30,40)')", "Action: wait()",
    "Action: click(start_box=__import__('os').system('echo bad'))",
    "Action: click(start_box='(1,2)'); print('bad')",
    "Action: click(start_box='(1,2)')\nAction: click(start_box='(3,4)')",
    "Action: click(start_box='(1,2)', semantic='safe')",
    "Action: click(**{'start_box':'(1,2)'})",
])
def test_unsupported_or_executable_output_cannot_become_an_action(text):
    with pytest.raises(UiTarsError):
        parse(text)


def setup_planner(tmp_path, *, sensitive=False, target="Pay invoice", expected=None):
    Image = pytest.importorskip("PIL.Image", reason="UI-TARS image tests require the optional Pillow runtime")
    path = tmp_path / "screen-test.png"
    Image.new("RGB", (1920, 1080)).save(path)
    obs = Observation("obs_1", 1, {"app": "Editor"}, "current screenshot",
                      {"elements": []}, str(path), sensitive, 1)
    calls = []
    async def chat(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {"content": json.dumps({"kind": "CLICK", "target": target,
                "expected": expected if expected is not None else {"contains_text": "Payment complete"},
                "args": {"semantic": "pay"}, "confidence": 0.8})}
        return {"content": "Action: click(start_box='(966,546)')"}
    planner = UiTarsPlanner(chat, visual_model="local-ui-tars", observer=SimpleNamespace(last_observation=obs),
                            screenshot_root=tmp_path, model_alias="local-planner")
    inputs = dict(goal="Pay invoice", observation_summary=obs.summary, foreground=obs.foreground,
                  ui_tree=obs.ui_tree, last_result="", remaining_steps=10)
    return planner, inputs, calls, obs


@pytest.mark.asyncio
async def test_visual_result_preserves_postcondition_and_approval_escalation(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    planner, inputs, calls, obs = setup_planner(tmp_path)
    action = await planner.next_action(**inputs)
    assert action.args == {"semantic": "pay", "x": 960, "y": 540}
    assert action.expected.contains_text == "Payment complete"
    assert action.target == "Pay invoice" and action.confidence == 0.8
    assert action.source == "vision"
    decision = ComputerPolicy().classify(action, mode=TaskMode.CONTROL, observation=obs)
    assert decision.requires_approval and decision.approval_kind == "computer_pay"
    assert not ComputerPolicy().classify(action, mode=TaskMode.OBSERVE_ONLY).allow
    assert calls[1]["model"] == "local-ui-tars"
    url = calls[1]["messages"][1]["content"][1]["image_url"]["url"]
    image = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
    assert image.size == (1932, 1092)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["sensitive", "missing_frame", "outside_root", "stale", "no_expected"])
async def test_no_visual_call_without_current_allowed_frame_and_postcondition(tmp_path, case):
    planner, inputs, calls, obs = setup_planner(tmp_path, sensitive=case == "sensitive",
                                              expected={} if case == "no_expected" else None)
    if case == "missing_frame": obs.screenshot_ref = None
    if case == "outside_root": planner.screenshot_root = tmp_path / "other"
    if case == "stale": inputs["observation_summary"] = "different screen"
    with pytest.raises(UiTarsError):
        await planner.next_action(**inputs)
    assert len(calls) == 1


def test_production_wiring_is_opt_in_and_keeps_existing_manager(monkeypatch, tmp_path):
    from bossman.computer_operator import subsystem
    monkeypatch.delenv("BOSSMAN_COMPUTER_PLANNER", raising=False)
    normal = subsystem.build_manager(store_path=tmp_path / "normal.json")
    assert not isinstance(normal.planner, UiTarsPlanner)
    monkeypatch.setenv("BOSSMAN_COMPUTER_PLANNER", "uitars")
    monkeypatch.setenv("BOSSMAN_UITARS_MODEL", "local-ui-tars")
    visual = subsystem.build_manager(store_path=tmp_path / "visual.json")
    assert isinstance(visual.planner, UiTarsPlanner)
    assert isinstance(visual, subsystem.AuthorizedComputerOperatorManager)
    assert visual.planner.chat_fn is subsystem.planner_chat
    assert visual.planner.observer is visual.observer
    assert subsystem._planner_agent().cloud_policy == "never"
