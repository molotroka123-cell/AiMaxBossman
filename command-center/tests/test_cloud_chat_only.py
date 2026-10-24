"""Owner 24.10: «у облачных моделей не будет доступа к контексту компа, только внутри чатов».

Live 24.10 (task 228): cloud agent 46 with tools=[] and permissions.cloud_pc_access=false was still handed
terminal.run/kill/stdin — action_contract's routing grant treated «no own tools» as «not owner-scoped».
A chat-only agent must see NO tools on the real run path (engine → allowed_tools_for → model payload),
whatever the prompt asks; a normal agent keeps the old routing grant (negative control).
"""
from __future__ import annotations

from bcc.tools import ROUTED_TOOLS_EXTEND_AGENT, allowed_tools_for, chat_only, to_api_name

from .test_contract_keeps_agent_tools import _names
from .test_v21_tool_loop import TERMINAL, ToolAdapter, _run_task, _stack_with_tools

HOSTNAME = "Выполни в терминале команду hostname и пришли вывод"
TERMINAL_RUN = to_api_name("terminal.run")


def test_chat_only_agent_gets_nothing_from_any_channel():
    agent = {"tools": ["terminal.run", "fs.read"], "permissions": {"cloud_pc_access": False}}
    assert chat_only(agent)
    assert allowed_tools_for({}, agent) == []
    assert allowed_tools_for({"meta": {"allowed_tools": ["fs.read"]}}, agent) == []
    routed = {"meta": {"allowed_tools": ["terminal.run"], ROUTED_TOOLS_EXTEND_AGENT: True}}
    assert allowed_tools_for(routed, agent) == []


def test_chat_only_needs_the_key_and_fails_closed_on_a_hand_edited_value():
    assert not chat_only({"permissions": {}})
    assert not chat_only({"permissions": None})
    assert not chat_only({"permissions": {"cloud_pc_access": True}})
    for v in (False, None, "false", "False", "0", 0, "no", "off"):     # GLM review 24.10: «"false"» used to mean «open»
        assert chat_only({"permissions": {"cloud_pc_access": v}}), v
    assert allowed_tools_for({}, {"tools": ["fs.read"], "permissions": {}}) == ["fs.read"]


def test_a_broken_allowed_tools_value_means_no_tools_not_the_agents_full_set():
    agent = {"tools": ["terminal.run", "fs.read"]}
    for broken in ("terminal.run", {"terminal.run": True}, None, 5):
        assert allowed_tools_for({"meta": {"allowed_tools": broken}}, agent) == [], broken


def test_dict_tools_and_permissions_grant_only_on_a_real_true():
    from bcc.permissions import agent_allowed, needs_approval
    assert allowed_tools_for({}, {"tools": {"terminal.run": "false", "fs.read": True}}) == ["fs.read"]
    assert not agent_allowed({"permissions": {"terminal.run": "false"}}, "terminal.run")
    assert needs_approval({"permissions": {"terminal.run": "false"}}, "terminal.run")
    assert agent_allowed({"permissions": {"terminal.run": True}}, "terminal.run")
    assert agent_allowed({"permissions": ["terminal.run"]}, "terminal.run")


def test_an_unknown_permission_is_dangerous_and_screen_recording_is_registered():
    from bcc.permissions import ALL_PERMISSIONS, is_dangerous, needs_approval
    assert is_dangerous("terminal.run ") and is_dangerous("made.up"), "unknown/typo permission must fail closed"
    assert not is_dangerous("") and not is_dangerous("filesystem.read")
    assert "desktop.control" in ALL_PERMISSIONS and is_dangerous("desktop.control")
    assert needs_approval({"permissions": {}}, "made.up")


async def test_a_routing_grant_without_an_agent_withholds_dangerous_and_unknown_tools():
    from bcc.features.action_contract import _withheld_from_agent
    withheld = await _withheld_from_agent(None, {"agent_id": None}, ["terminal.run", "no.such.tool"])
    assert "no.such.tool" in withheld and "terminal.run" in withheld


async def _first_payload(env, perms, prompt=HOSTNAME):
    adapter = ToolAdapter([("text", "ок")])
    stack = await _stack_with_tools(env, [], adapter=adapter, prompt=prompt)
    if perms is not None:
        r = await env.client.patch(f"/api/agents/{stack['agent']['id']}", json={"permissions": perms})
        assert r.status_code == 200, r.text
    await _run_task(env, stack["task"]["id"], timeout=10, until=TERMINAL)
    assert adapter.seen_tools, "модель не вызывалась"
    return _names(adapter.seen_tools[0])


async def test_chat_only_agent_sees_no_terminal_tools_for_a_terminal_prompt(env):
    seen = await _first_payload(env, {"cloud_pc_access": False})
    assert seen == set() or seen == {None}, f"chat-only агенту выданы инструменты: {sorted(map(str, seen))}"


async def test_negative_control_an_ordinary_agent_still_gets_the_routing_grant(env):
    seen = await _first_payload(env, None)
    assert TERMINAL_RUN in seen, "контроль: без флага роутинг выдаёт terminal.* (иначе тест выше ничего не доказывает)"
