"""V2.2 §9 — рабочая область агента изолирована от соседей по миссии.

Регрессия, ради которой это написано: агент A пишет в промежуточные файлы
агента B той же миссии. До правки это проходило — каталог B лежит внутри того
же разрешённого корня, и общая проверка корней его пропускала.

Проверяется через РЕАЛЬНЫЕ инструменты (`terminal.run`, `code.search`), а не
только через функцию-предикат: важно, что запрет стоит на пути вызова.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from bcc.features import tools_code
from bcc.features.tools_terminal import _resolve_cwd, _tool_run
from bcc.tools import ToolContext
from bcc.v2 import scratch


def _ctx(env, *, mission_id, agent_id, task_id=1) -> ToolContext:
    return ToolContext(svc=env.svc, task={"id": task_id, "mission_id": mission_id},
                       run_id=1, agent={"id": agent_id}, workspace="")


# ------------------------------------------------------------------ раскладка каталогов

def test_owner_dir_separates_agents_and_missions(env):
    s = env.settings
    a = scratch.owner_dir(s, mission_id=7, agent_id=1)
    b = scratch.owner_dir(s, mission_id=7, agent_id=2)
    other_mission = scratch.owner_dir(s, mission_id=8, agent_id=1)

    assert a != b and a.parent == b.parent               # одна миссия, разные агенты
    assert a != other_mission                            # один агент, разные миссии
    assert a.relative_to(scratch.base_dir(s)).parts == ("mission-7", "agent-1")

    # задача без миссии — тоже отдельный владелец, а не общая свалка
    solo1 = scratch.owner_dir(s, mission_id=None, agent_id=1, task_id=10)
    solo2 = scratch.owner_dir(s, mission_id=None, agent_id=2, task_id=10)
    assert solo1 != solo2
    assert solo1.relative_to(scratch.base_dir(s)).parts == ("task-10", "agent-1")


def test_identifiers_cannot_escape_the_base(env):
    """Идентификатор из БД — данные: `../` в нём не должен уводить из базы."""
    evil = scratch.owner_dir(env.settings, mission_id="../../etc", agent_id="../root")
    assert scratch.base_dir(env.settings).resolve() in evil.resolve().parents
    assert ".." not in evil.parts


def test_paths_outside_scratch_are_not_this_modules_business(env):
    own = scratch.owner_dir(env.settings, mission_id=1, agent_id=1)
    assert scratch.violation(env.settings, own, Path("/home/user/AiMaxBossman")) == ""
    assert scratch.violation(env.settings, own, own / "notes.md") == ""


def test_neighbour_area_is_denied_both_ways(env):
    a = scratch.owner_dir(env.settings, mission_id=5, agent_id=1)
    b = scratch.owner_dir(env.settings, mission_id=5, agent_id=2)
    reason = scratch.violation(env.settings, a, b / "draft.txt")
    assert reason and "другого агента" in reason
    assert scratch.violation(env.settings, b, a / "draft.txt")
    # чтение соседа закрыто так же, как запись: черновик — не источник истины
    assert scratch.violation(env.settings, a, b)


# ------------------------------------------------------------------ через terminal.run

async def test_terminal_alias_resolves_to_own_area(env):
    ctx = _ctx(env, mission_id=3, agent_id=11)
    cwd, _ = await _resolve_cwd(ctx, {"cwd": "scratch"})
    assert cwd == scratch.owner_dir(env.settings, mission_id=3, agent_id=11)
    assert cwd.is_dir()                                  # каталог создан по требованию
    # 0700 — требование к боевой машине (Linux), и здесь оно не ослаблено:
    # ровно "700", никаких «не хуже чем». На NTFS битов режима нет вообще —
    # st_mode там всегда 777, и проверять было бы нечего; изоляция на Windows
    # держится не правами ФС, а проверкой scratch.violation, которую проверяют
    # соседние тесты этого же файла.
    if os.name != "nt":
        assert oct(cwd.stat().st_mode)[-3:] == "700"


async def test_agent_a_cannot_run_inside_agent_b_area(env):
    """Та самая регрессия: A обращается к каталогу B по прямому пути."""
    b_dir = scratch.ensure(scratch.owner_dir(env.settings, mission_id=9, agent_id=2))
    (b_dir / "draft.txt").write_text("промежуточный результат B", encoding="utf-8")

    ctx_a = _ctx(env, mission_id=9, agent_id=1)
    result = await _tool_run({"command": "echo испорчено > draft.txt",
                              "cwd": str(b_dir), "mode": "project_host"}, ctx_a)
    assert result.error is True
    assert "другого агента" in result.content
    assert (b_dir / "draft.txt").read_text(encoding="utf-8") == "промежуточный результат B"

    # обход через .. закрыт так же
    sneaky = await _tool_run({"command": "echo x", "mode": "project_host",
                              "cwd": str(b_dir / ".." / "agent-2")}, ctx_a)
    assert sneaky.error is True and "другого агента" in sneaky.content


async def test_owner_may_work_in_own_area(env):
    ctx = _ctx(env, mission_id=9, agent_id=1)
    # cmd.exe при перенаправлении пишет в OEM-кодировке хоста (тут cp1252) и
    # превращает кириллицу в '????' — свойство оболочки ОС, а не продукта,
    # поэтому на Windows эхо ASCII: проверяется изоляция своей области.
    word = "ok" if os.name == "nt" else "своё"
    result = await _tool_run({"command": f"echo {word} > draft.txt", "cwd": "scratch",
                              "mode": "project_host"}, ctx)
    assert result.error is False, result.content
    own = scratch.owner_dir(env.settings, mission_id=9, agent_id=1)
    assert (own / "draft.txt").read_text(encoding="utf-8").strip() == word


# ------------------------------------------------------------------ через code.*

async def test_code_root_refuses_neighbour_area(env):
    b_dir = scratch.ensure(scratch.owner_dir(env.settings, mission_id=4, agent_id=2))
    (b_dir / "secret_draft.py").write_text("PLAN = 'B'\n", encoding="utf-8")
    ctx_a = _ctx(env, mission_id=4, agent_id=1)

    with pytest.raises(PermissionError, match="другого агента"):
        await tools_code.resolve_root(env.svc, str(b_dir), "", ctx_a)

    own = await tools_code.resolve_root(env.svc, "scratch", "", ctx_a)
    assert own == scratch.owner_dir(env.settings, mission_id=4, agent_id=1)


async def test_ordinary_roots_are_not_narrowed(env):
    """Правило §9 не сузило обычную работу: репозиторий по-прежнему доступен."""
    ctx = _ctx(env, mission_id=4, agent_id=1)
    root = await tools_code.resolve_root(env.svc, None, "", ctx)
    assert root.is_dir() and scratch.base_dir(env.settings).resolve() not in root.parents
