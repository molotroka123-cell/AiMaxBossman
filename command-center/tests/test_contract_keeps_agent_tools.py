"""APP-CONTRACT-OVERRIDES-AGENT-TOOLS (P1, owner HW-02, 2026-09-23).

Живой прогон владельца: агент «Оператор ПК» с явно выданными через API
агентов `computer.observe` + `computer.act`, задача «Открой Блокнот и напиши в
нём: привет». В run'е модель увидела ТОЛЬКО apps.start/apps.stop: before_run
хук action_contract распознал APPS_ACTION и записал семейство apps в
`tasks.meta.allowed_tools`, а этот канал в `bcc.tools.allowed_tools_for`
ВАЖНЕЕ `agents.tools` — выданные агенту инструменты молча пропадали.

Проверяется через настоящий путь сборки tools для run'а (engine →
allowed_tools_for → TOOLS.resolve → payload модели), а не вызовом хелпера:
  * агент С computer.* получает их для «Открой Блокнот …» (RU/EN, и в форме,
    которую ловит браузерный роутер — «… на компьютере»);
  * НЕТ расширения: агент без computer.* их не получает; невыданное остаётся
    невыданным;
  * снятие инструмента с агента видно сразу (грант роутера не снапшотит
    инструменты агента в meta);
  * computer.act по-прежнему идёт через ASK — ничего не исполнено без
    одобрения владельца.
"""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from bcc import db as dbm
from bcc.tools import allowed_tools_for, to_api_name

from .test_computer_use_tools import desk  # noqa: F401  (фикстура-макет рабочего стола)
from .test_v21_tool_loop import TERMINAL, ToolAdapter, _run_task, _stack_with_tools

pytest.importorskip("bossman.computer_operator.models")

# Модель видит wire-имена (apps.start → apps_start): сравниваем именно их.
COMPUTER = {to_api_name("computer.observe"), to_api_name("computer.act")}

NOTEPAD_PROMPTS = [
    "Открой Блокнот и напиши в нём: привет",
    "Open Notepad and type: hello",
    # «на компьютере» ловит ещё и браузерный роутер (action_router) — он тоже
    # не должен вытеснять инструменты агента.
    "Открой Блокнот на компьютере и напиши в нём: привет",
]


def _names(tools) -> set[str]:
    out = set()
    for t in tools or []:
        fn = t.get("function") if isinstance(t, dict) else None
        out.add((fn or t).get("name") if isinstance(fn or t, dict) else str(t))
    return out


async def _first_payload_tools(env, agent_tools, prompt) -> tuple[set[str], dict]:
    adapter = ToolAdapter([("text", "не важно для этого теста")])
    stack = await _stack_with_tools(env, agent_tools, adapter=adapter, prompt=prompt)
    await _run_task(env, stack["task"]["id"], timeout=10, until=TERMINAL)
    assert adapter.seen_tools, "модель не вызывалась"
    return _names(adapter.seen_tools[0]), stack


@pytest.mark.parametrize("prompt", NOTEPAD_PROMPTS)
async def test_agent_granted_computer_tools_survive_action_contract(env, desk, prompt):  # noqa: F811
    seen, _ = await _first_payload_tools(env, ["computer.observe", "computer.act"], prompt)
    assert COMPUTER <= seen, f"инструменты агента вытеснены контрактом: {sorted(seen)}"


@pytest.mark.parametrize("agent_tools", [[], ["memory.search"], ["computer.observe"]])
@pytest.mark.parametrize("prompt", NOTEPAD_PROMPTS)
async def test_routing_never_grants_computer_tools_the_agent_lacks(env, desk, prompt,  # noqa: F811
                                                                   agent_tools):
    seen, _ = await _first_payload_tools(env, agent_tools, prompt)
    granted = {to_api_name(t) for t in agent_tools}
    assert not ((COMPUTER - granted) & seen), f"роутинг расширил права агента: {sorted(seen)}"


def test_skill_or_owner_allowed_tools_stay_exact():
    """Объединение — только для гранта роутинга. Скилл/миссия/владелец,
    задавшие allowed_tools сами (без флага), по-прежнему получают РОВНО свой
    список, без инструментов агента."""
    from bcc.tools import ROUTED_TOOLS_EXTEND_AGENT
    agent ={"tools": ["computer.observe", "computer.act"]}
    assert allowed_tools_for({"meta": {"allowed_tools": ["fs.read"]}}, agent) == ["fs.read"]
    assert allowed_tools_for({"meta": {"allowed_tools": []}}, agent) == []
    routed = {"meta": {"allowed_tools": ["apps.start"], ROUTED_TOOLS_EXTEND_AGENT: True}}
    assert allowed_tools_for(routed, agent) == ["apps.start", "computer.observe", "computer.act"]
    assert allowed_tools_for(routed, {"tools": []}) == ["apps.start"]


async def test_revoking_a_tool_from_the_agent_is_visible_after_routing(env, desk):  # noqa: F811
    """Грант контракта не должен «замораживать» инструменты агента в
    tasks.meta: снятый владельцем computer.act обязан исчезнуть — на этом
    держится проверка tool_withdrawn в момент эффекта (engine)."""
    _, stack = await _first_payload_tools(
        env, ["computer.observe", "computer.act"], NOTEPAD_PROMPTS[0])
    async with env.svc.db.session() as s:
        task = dict((await s.execute(sa.select(dbm.tasks).where(
            dbm.tasks.c.id == stack["task"]["id"]))).first()._mapping)
    assert "allowed_tools" in (task["meta"] or {}), "контракт должен был сработать"
    agent = {"tools": ["computer.observe"]}
    names = set(allowed_tools_for(task, agent))
    assert "computer.act" not in names and "computer.observe" in names


async def test_computer_act_still_needs_owner_approval(env, desk):  # noqa: F811
    """Инструмент видим — но исполнение по-прежнему за ASK: без одобрения
    на макете рабочего стола не выполнено ничего."""
    act = to_api_name("computer.act")
    adapter = ToolAdapter([("tool", act, {"action": "type", "generation": 1, "text": "привет"}),
                           ("text", "готово")])
    stack = await _stack_with_tools(env, ["computer.observe", "computer.act"], adapter=adapter,
                                    prompt=NOTEPAD_PROMPTS[0])
    status = await _run_task(env, stack["task"]["id"], timeout=10,
                             until=("waiting_approval", "completed", "failed", "stopped"))
    async with env.svc.db.session() as s:
        calls = [dict(r._mapping) for r in (await s.execute(sa.select(dbm.tool_calls).where(
            dbm.tool_calls.c.task_id == stack["task"]["id"]))).fetchall()]
    assert status == "waiting_approval", [(c["tool"], c["status"], c["result_preview"])
                                          for c in calls]
    assert [(c["tool"], c["effect"], c["status"]) for c in calls] == [
        ("computer.act", "ask", "pending_approval")]
    assert desk.desktop.executed == []
