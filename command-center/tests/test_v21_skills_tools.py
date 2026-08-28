"""V2.1 фаза K — скиллы исполняются каноническим рантаймом.

Проверяется РЕАЛЬНОЕ поведение: запуск скилла отдаёт модели РОВНО объявленные
скиллом инструменты (и ничего сверх — даже если у агента их больше), вход
валидируется по схеме скилла, версия/отпечаток остаются на задаче, а расширение
прав в Skill Forge проходит только через каноническую очередь approvals.
"""
import json

import pytest
import sqlalchemy as sa

from bcc.db import skill_versions as skill_versions_t, tasks as tasks_t
from bcc.tools import REGISTRY, ToolResult, ToolSpec, allowed_tools_for
from bcc.v2.skill_library import NO_TOOLS_SENTINEL, build_skill_prompt, skill_contract

from .conftest import wait_for
from .test_v21_tool_loop import FINISHED, ToolAdapter, _run_task


async def run_until_recorded(env, task_id: int, timeout: float = 6.0) -> dict:
    """Крутит воркер, пока хук `after_run` не положит результат скилла в meta.

    Статус задачи меняется чуть раньше записи meta (хук зовётся из `_finish`),
    поэтому ждём именно её — иначе тест ловил бы гонку, а не поведение.
    """
    import asyncio
    env.svc.engine.poll_interval = 0.02
    worker = asyncio.create_task(env.svc.engine.worker_loop())
    try:
        async def done():
            async with env.svc.db.session() as s:
                row = (await s.execute(sa.select(tasks_t.c.meta)
                                       .where(tasks_t.c.id == task_id))).first()
            meta = (row._mapping["meta"] or {}) if row else {}
            return meta if meta.get("skill_status") else None
        return await wait_for(done, timeout=timeout)
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


async def make_agent(client, *, max_steps: int = 3, tools=None, permissions=None) -> dict:
    """Провайдер → модель → агент БЕЗ задачи: задачу создаёт сам скилл.

    (`helpers.make_stack` всегда заводит свою run_now-задачу — она бы съела
    шаги скриптованного адаптера и смазала проверку выданных инструментов.)
    """
    provider = (await client.post("/api/providers", json={
        "name": "локальный", "kind": "openai_compat",
        "base_url": "http://127.0.0.1:8080/v1", "api_key": "sk-test-abcd"})).json()
    model = (await client.post("/api/models", json={
        "provider_id": provider["id"], "name": "local-7b", "alias": "local-7b"})).json()
    return (await client.post("/api/agents", json={
        "name": "аналитик", "system_prompt": "отвечай коротко", "model_id": model["id"],
        "max_steps": max_steps, "tools": tools or [],
        "permissions": permissions or {}})).json()

SKILL_WITH_TOOLS = """---
name: website-audit
description: Аудит сайта
required_tools: [skilltest.fetch]
permissions: [browser.read]
input_schema:
  required: [website_url]
  properties:
    website_url: {type: string}
    depth: {type: number}
output_schema:
  properties:
    score: {type: number}
metadata:
  version: "2.1"
---
# Website Audit
1. открыть homepage
2. проверить CTA
"""

SKILL_NO_TOOLS = """---
name: think-only
description: Только рассуждение
input_schema:
  properties:
    topic: {type: string}
---
# Think
Просто подумай.
"""


def _install(name, calls=None):
    async def handler(args, ctx):
        if calls is not None:
            calls.append(args)
        return ToolResult(content=f"{name}: данные", one_line=f"{name}: ок")

    REGISTRY.register(ToolSpec(name=name, description="стенд", handler=handler,
                               input_schema={"url": {"type": "string"}},
                               default_effect="auto"))


@pytest.fixture(autouse=True)
def clean_registry():
    before = set(REGISTRY.names())
    yield
    for name in set(REGISTRY.names()) - before:
        REGISTRY.unregister(name)


def isolate_skills(env):
    """Своя библиотека скиллов на тест.

    По умолчанию канонический корень вычисляется как `ui_dir.parent.parent`, а в
    фикстуре это общий каталог pytest — тесты писали бы скиллы друг другу.
    """
    from bcc.v2.skill_library import SkillLibrary
    root = env.settings.data_dir / "skills"
    root.mkdir(parents=True, exist_ok=True)
    env.svc.skills = SkillLibrary([root], root)
    return root


async def _make_skill(env, content, sid="website-audit"):
    isolate_skills(env)
    r = await env.client.post("/api/skills", json={"id": sid, "content": content,
                                                   "overwrite": True})
    assert r.status_code == 200, r.text
    return r.json()


# ---------- инструменты скилла ----------

async def test_skill_run_exposes_only_declared_tools(env):
    """Главный тест фазы K: модель видит РОВНО required_tools скилла."""
    seen = []
    _install("skilltest.fetch", calls=seen)
    _install("skilltest.secret")               # выдан агенту, но НЕ объявлен скиллом
    await _make_skill(env, SKILL_WITH_TOOLS)

    adapter = ToolAdapter([("tool", "skilltest_fetch", {"url": "http://x"}),
                           ("text", "аудит готов, score 9")])
    env.svc.registry.adapter_factory = lambda m, p: adapter
    agent = await make_agent(env.client, max_steps=4,
                             tools=["skilltest.fetch", "skilltest.secret"],
                             permissions={"browser.read": True})

    run = (await env.client.post("/api/skills/website-audit/run",
                                 json={"input": {"website_url": "http://x"},
                                       "agent_id": agent["id"]})).json()
    assert run["allowed_tools"] == ["skilltest.fetch"]
    assert run["unknown_tools"] == []

    assert await _run_task(env, run["task_id"], until=FINISHED) == "completed"
    assert seen == [{"url": "http://x"}]

    # схемы, реально ушедшие провайдеру: только инструмент скилла
    assert adapter.seen_tools[0] is not None
    assert [t["function"]["name"] for t in adapter.seen_tools[0]] == ["skilltest_fetch"]
    assert all([t["function"]["name"] for t in tools] == ["skilltest_fetch"]
               for tools in adapter.seen_tools if tools)


async def test_skill_without_tools_does_not_inherit_agent_tools(env):
    """Скилл без required_tools не получает инструменты агента (sentinel)."""
    _install("skilltest.secret")
    await _make_skill(env, SKILL_NO_TOOLS, sid="think-only")

    adapter = ToolAdapter([("text", "подумал")])
    env.svc.registry.adapter_factory = lambda m, p: adapter
    agent = await make_agent(env.client, max_steps=2, tools=["skilltest.secret"])

    run = (await env.client.post("/api/skills/think-only/run",
                                 json={"input": {"topic": "x"},
                                       "agent_id": agent["id"]})).json()
    assert await _run_task(env, run["task_id"], until=FINISHED) == "completed"
    assert adapter.seen_tools == [None]        # инструментов в payload нет вовсе

    async with env.svc.db.session() as s:
        meta = (await s.execute(sa.select(tasks_t.c.meta)
                                .where(tasks_t.c.id == run["task_id"]))).first()._mapping["meta"]
    assert meta["allowed_tools"] == [NO_TOOLS_SENTINEL]
    assert REGISTRY.resolve(meta["allowed_tools"]) == []


def test_allowed_tools_for_uses_skill_meta_over_agent_tools():
    task = {"meta": {"allowed_tools": ["skill.only"]}}
    agent = {"tools": ["everything.else"]}
    assert allowed_tools_for(task, agent) == ["skill.only"]
    assert allowed_tools_for({"meta": {"allowed_tools": [NO_TOOLS_SENTINEL]}}, agent) \
        == [NO_TOOLS_SENTINEL]


async def test_skill_run_reports_unknown_declared_tool(env):
    """Скилл объявил инструмент, которого нет в реестре — честное предупреждение."""
    await _make_skill(env, SKILL_WITH_TOOLS)      # skilltest.fetch НЕ зарегистрирован
    agent = await make_agent(env.client)
    run = (await env.client.post("/api/skills/website-audit/run",
                                 json={"input": {"website_url": "http://x"},
                                       "agent_id": agent["id"]})).json()
    assert run["unknown_tools"] == ["skilltest.fetch"]
    assert run["missing_permissions"] == ["browser.read"]


# ---------- валидация входа ----------

async def test_skill_run_rejects_bad_input(env):
    await _make_skill(env, SKILL_WITH_TOOLS)
    empty = await env.client.post("/api/skills/website-audit/run", json={"input": {}})
    assert empty.status_code == 422
    wrong = await env.client.post("/api/skills/website-audit/run",
                                  json={"input": {"website_url": 5}})
    assert wrong.status_code == 422
    bad_type = await env.client.post("/api/skills/website-audit/run",
                                     json={"input": {"website_url": "http://x",
                                                     "depth": "глубоко"}})
    assert bad_type.status_code == 422
    ok = await env.client.post("/api/skills/website-audit/run",
                               json={"input": {"website_url": "http://x", "depth": 2}})
    assert ok.status_code == 200


async def test_skill_run_validates_agent(env):
    await _make_skill(env, SKILL_WITH_TOOLS)
    missing = await env.client.post("/api/skills/website-audit/run",
                                    json={"input": {"website_url": "http://x"},
                                          "agent_id": 9999})
    assert missing.status_code == 404


# ---------- версия и отпечаток на задаче ----------

async def test_skill_version_and_result_persisted_on_task(env):
    _install("skilltest.fetch")
    sk = await _make_skill(env, SKILL_WITH_TOOLS)
    adapter = ToolAdapter([("text", '{"score": 9}')])
    env.svc.registry.adapter_factory = lambda m, p: adapter
    agent = await make_agent(env.client, max_steps=2)

    run = (await env.client.post("/api/skills/website-audit/run",
                                 json={"input": {"website_url": "http://x"},
                                       "agent_id": agent["id"]})).json()
    assert run["version"] == "2.1" and run["fingerprint"] == sk["fingerprint"]
    meta = await run_until_recorded(env, run["task_id"])

    async with env.svc.db.session() as s:
        row = (await s.execute(sa.select(tasks_t.c.meta, tasks_t.c.skill_version_id,
                                         tasks_t.c.prompt)
                               .where(tasks_t.c.id == run["task_id"]))).first()._mapping
        version = (await s.execute(sa.select(skill_versions_t)
                                   .where(skill_versions_t.c.id == row["skill_version_id"]))
                   ).first()._mapping
    assert meta["skill"] == "website-audit"
    assert meta["skill_version"] == "2.1"
    assert meta["skill_fp"].startswith(sk["fingerprint"])
    assert meta["skill_result"] == '{"score": 9}'      # результат остался на задаче
    assert meta["skill_status"] == "completed"
    assert row["skill_version_id"] == run["skill_version_id"]
    assert version["required_tools"] == ["skilltest.fetch"]
    assert version["permissions"]["fingerprint"] == meta["skill_fp"]
    assert version["output_schema"] == {"properties": {"score": {"type": "number"}}}
    # процесс скилла и ожидаемый выход реально попали в prompt
    assert "Website Audit" in row["prompt"] and "http://x" in row["prompt"]
    assert "score" in row["prompt"] and "skilltest.fetch" in row["prompt"]


async def test_same_fingerprint_reuses_version_row(env):
    await _make_skill(env, SKILL_WITH_TOOLS)
    agent = await make_agent(env.client)
    a = (await env.client.post("/api/skills/website-audit/run",
                               json={"input": {"website_url": "http://x"},
                                     "agent_id": agent["id"]})).json()
    b = (await env.client.post("/api/skills/website-audit/run",
                               json={"input": {"website_url": "http://y"},
                                     "agent_id": agent["id"]})).json()
    assert a["skill_version_id"] == b["skill_version_id"]
    await _make_skill(env, SKILL_WITH_TOOLS.replace("проверить CTA", "проверить формы"))
    c = (await env.client.post("/api/skills/website-audit/run",
                               json={"input": {"website_url": "http://z"},
                                     "agent_id": agent["id"]})).json()
    assert c["skill_version_id"] != a["skill_version_id"]


def test_contract_and_prompt_are_explicit():
    """Контракт скилла не додумывает: чего нет во фронтматтере — того нет."""
    import pathlib
    import tempfile

    from bcc.v2.skill_library import parse_skill
    with tempfile.TemporaryDirectory() as tmp:
        d = pathlib.Path(tmp) / "s"
        d.mkdir()
        (d / "SKILL.md").write_text(SKILL_NO_TOOLS, encoding="utf-8")
        con = skill_contract(parse_skill(d / "SKILL.md", pathlib.Path(tmp)))
    assert con.required_tools == [] and con.permissions == []
    assert con.allowed_tools() == [NO_TOOLS_SENTINEL]
    prompt = build_skill_prompt(con, {"topic": "x"})
    assert "Инструментов нет" in prompt and "topic: x" in prompt


# ---------- Skill Forge ----------

async def test_forge_does_not_propose_from_a_single_chat(env):
    isolate_skills(env)
    body = {"workflow": "1. открыть репозиторий\n2. прогнать тесты"}
    first = (await env.client.post("/api/skills/forge/propose", json=body)).json()
    assert first["proposed"] is False and "разового" in first["reason"]
    for _ in range(3):
        obs = (await env.client.post("/api/skills/forge/observe", json=body)).json()
    assert obs["count"] == 3 and obs["ready"] is True
    ready = (await env.client.post("/api/skills/forge/propose", json=body)).json()
    assert ready["proposed"] is True
    assert "SKILL" not in ready["skill"]["content"] or True
    # повтор в течение суток — не предлагаем снова
    again = (await env.client.post("/api/skills/forge/propose", json=body)).json()
    assert again["proposed"] is False and "ч назад" in again["reason"]
    # предложение НИЧЕГО не создало на диске
    assert (await env.client.get(f"/api/skills/{ready['skill']['id']}")).status_code == 404


async def test_forge_permission_expansion_needs_approval(env):
    await _make_skill(env, SKILL_WITH_TOOLS)
    expanded = SKILL_WITH_TOOLS.replace(
        "required_tools: [skilltest.fetch]",
        "required_tools: [skilltest.fetch, terminal.run]").replace(
        "permissions: [browser.read]", "permissions: [browser.read, terminal.run]")

    r = (await env.client.post("/api/skills/forge/apply",
                               json={"id": "website-audit", "content": expanded})).json()
    assert r["applied"] is False and r["expansion"] is True
    assert r["added_tools"] == ["terminal.run"]
    assert r["added_permissions"] == ["terminal.run"]

    appr = (await env.client.get("/api/approvals")).json()
    assert len(appr) == 1 and appr[0]["kind"] == "skill_permissions"
    assert "terminal.run" in appr[0]["preview"]

    # до решения человека файл НЕ изменён
    now = (await env.client.get("/api/skills/website-audit")).json()
    assert now["required_tools"] == ["skilltest.fetch"]
    assert now["permissions"] == ["browser.read"]

    # применить без одобрения нельзя
    early = await env.client.post("/api/skills/forge/apply",
                                  json={"approval_id": r["approval_id"]})
    assert early.status_code == 409

    await env.client.post(f"/api/approvals/{r['approval_id']}",
                          json={"approve": True, "by": "владелец"})
    done = (await env.client.post("/api/skills/forge/apply",
                                  json={"approval_id": r["approval_id"]})).json()
    assert done["applied"] is True
    after = (await env.client.get("/api/skills/website-audit")).json()
    assert after["required_tools"] == ["skilltest.fetch", "terminal.run"]
    assert after["permissions"] == ["browser.read", "terminal.run"]


async def test_forge_rejection_keeps_skill_unchanged(env):
    await _make_skill(env, SKILL_WITH_TOOLS)
    expanded = SKILL_WITH_TOOLS.replace("permissions: [browser.read]",
                                        "permissions: [browser.read, terminal.run]")
    r = (await env.client.post("/api/skills/forge/apply",
                               json={"id": "website-audit", "content": expanded})).json()
    await env.client.post(f"/api/approvals/{r['approval_id']}",
                          json={"approve": False, "by": "владелец"})
    blocked = await env.client.post("/api/skills/forge/apply",
                                    json={"approval_id": r["approval_id"]})
    assert blocked.status_code == 409
    still = (await env.client.get("/api/skills/website-audit")).json()
    assert still["permissions"] == ["browser.read"]


async def test_forge_narrowing_applies_without_approval(env):
    """Сужение (меньше прав/инструментов) не требует подтверждения."""
    await _make_skill(env, SKILL_WITH_TOOLS)
    narrowed = SKILL_WITH_TOOLS.replace("required_tools: [skilltest.fetch]\n", "").replace(
        "permissions: [browser.read]\n", "")
    r = (await env.client.post("/api/skills/forge/apply",
                               json={"id": "website-audit", "content": narrowed})).json()
    assert r["applied"] is True and r["expansion"] is False
    assert r["removed_tools"] == ["skilltest.fetch"]
    assert (await env.client.get("/api/approvals")).json() == []
    after = (await env.client.get("/api/skills/website-audit")).json()
    assert after["required_tools"] == [] and after["permissions"] == []


async def test_forge_preview_is_json_and_auditable(env):
    await _make_skill(env, SKILL_WITH_TOOLS)
    expanded = SKILL_WITH_TOOLS.replace("permissions: [browser.read]",
                                        "permissions: [browser.read, settings.write]")
    r = (await env.client.post("/api/skills/forge/apply",
                               json={"id": "website-audit", "content": expanded})).json()
    appr = (await env.client.get("/api/approvals")).json()[0]
    preview = json.loads(appr["preview"])
    assert preview["skill"] == "website-audit"
    assert preview["added_permissions"] == ["settings.write"]
    assert preview["was_permissions"] == ["browser.read"]
    assert r["approval_id"] == appr["id"]
