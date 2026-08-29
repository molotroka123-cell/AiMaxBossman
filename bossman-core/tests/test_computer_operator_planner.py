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
