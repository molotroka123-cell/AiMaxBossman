import pytest
from bossman.computer_operator.planner import Planner
from bossman.computer_operator.models import ActionKind
async def chat(**kw):return {"content":'{"kind":"CLICK","target":"Save","expected":{"contains_text":"Saved"},"args":{"x":1,"y":2},"confidence":0.9}'}
@pytest.mark.asyncio
async def test_parse():assert (await Planner(chat).next_action(goal="x",observation_summary="",foreground={},ui_tree=None,last_result="",remaining_steps=2)).kind is ActionKind.CLICK
def test_unknown_action_rejected():
    with pytest.raises(Exception):Planner(chat).parse_action('{"kind":"SHELL","expected":{}}')
def test_malformed_rejected():
    with pytest.raises(Exception):Planner(chat).parse_action("ignore all policy")

def test_live_local_model_synonyms_accepted():
    """Live repro (2026-08-30 host): qwen2.5:7b via real Gateway returned
    action/parameters/expected_postcondition instead of kind/args/expected;
    strict-only parsing burned 21 replans and failed every local task."""
    p = Planner(chat)
    a = p.parse_action(
        '{"action":"APP_LAUNCH","parameters":{"application":"Notepad"},'
        '"expected_postcondition":{"foreground":{"title":"Untitled - Notepad","app":"Notepad"}}}')
    assert a.kind is ActionKind.APP_LAUNCH
    assert a.target == "Notepad"
    assert a.expected.window_title_contains == "Untitled - Notepad"
    assert a.expected.foreground_app_contains == "Notepad"
