"""Lab agents are real, idempotently seeded ``agents`` rows: six students with the
owner's prompts and one shared budget, and three observers that are not students."""
from __future__ import annotations

import pytest
import sqlalchemy as sa
from fastapi import HTTPException

from bcc.db import agents as agents_t
from bcc.db import utcnow
from bcc.features import coding_tasks as ct
from bcc.features import lab_agents as la

OWNER = {
    "RAW": "Улучши Bossman. Найди одну реальную проблему, исправь её и докажи результат тестом.",
    "TOOL_FIRST": "Улучши Bossman. Сначала исследуй систему инструментами. Не делай предположений без evidence. "
                  "Найди reproducer. Только после воспроизведения меняй код. После исправления запусти "
                  "regression и соседние тесты.",
    "MEMORY": "Улучши Bossman. До планирования запроси verified lessons, похожие ошибки и subsystem conventions. "
              "Не копируй старый patch. Используй память как evidence. Найди новую реальную проблему и реши её.",
    "PLAN_EXECUTE_VERIFY": "Улучши Bossman. PLAN: найди проблему и критерий успеха. EXECUTE: сделай минимальный "
                           "patch. VERIFY: попытайся доказать, что patch неправильный. Не завершай без "
                           "независимого подтверждения.",
    "RED_TEAM": "Попытайся сломать одну функцию Bossman. Ищи: fake success, restart, replay, stale state, "
                "resource leak, wrong provenance, UI/backend mismatch. После воспроизводимого дефекта исправь "
                "его и добавь regression.",
    "USER_UX": "Используй Bossman как обычный владелец. Выполни полезную задачу. Найди место, где "
               "пользовательский путь неудобен, ломается, требует лишних действий или обещает больше, чем "
               "реально делает backend. Исправь один наиболее полезный дефект и докажи результат.",
}
PREAMBLE = ("Ты работаешь в изолированной копии репозитория. Разрешены только инструменты из списка агента. "
            "Не трогай защищённые пути. Итог — минимальный patch и честный отчёт: что сделано, какие тесты "
            "запускались, что осталось непроверенным.")


async def _agents(client):
    return (await client.get("/api/agents")).json()


async def test_ensure_twice_leaves_six_students_and_three_observers(env):
    first = await la.ensure_lab_agents(env.svc)
    assert sorted(first["created"]) == sorted(la.VARIANTS + la.OBSERVERS)
    second = await la.ensure_lab_agents(env.svc)
    assert second["created"] == [] and second["updated"] == []
    assert sorted(second["unchanged"]) == sorted(la.VARIANTS + la.OBSERVERS)
    rows = await _agents(env.client)
    students = [r for r in rows if r["role"] == la.LAB_ROLE]
    observers = [r for r in rows if r["role"] == la.OBSERVER_ROLE]
    assert len(students) == 6 and len(observers) == 3
    assert sorted(r["name"] for r in students) == sorted(la.agent_name(v) for v in la.VARIANTS)
    assert sorted(r["name"] for r in observers) == sorted(la.agent_name(o) for o in la.OBSERVERS)


async def test_prompts_are_the_owner_texts_plus_the_isolation_preamble(env):
    assert la.OWNER_TEXTS == OWNER
    agents = (await la.ensure_lab_agents(env.svc))["agents"]
    by_variant = {a["variant"]: a for a in agents}
    for variant, text in OWNER.items():
        assert by_variant[variant]["system_prompt"] == f"{text} {PREAMBLE}", variant


async def test_students_share_one_budget_and_toolset_and_only_memory_recalls(env):
    """Fairness: one model, different Bossman — prompts, finish gate and memory vary;
    tools, step cap and token cap do not."""
    agents = (await la.ensure_lab_agents(env.svc))["agents"]
    by_variant = {a["variant"]: a for a in agents}
    assert set(by_variant) == set(la.VARIANTS)
    assert len({a["system_prompt"] for a in agents}) == 6
    fingerprints = {repr(a["fairness"]) for a in agents}
    assert len(fingerprints) == 1, fingerprints
    for a in agents:
        assert a["tools"] == list(la.SIDECAR_TOOLS)
        assert a["max_steps"] == la.STUDENT_MAX_STEPS and a["max_tokens"] == la.STUDENT_MAX_TOKENS
    assert [v for v, a in by_variant.items() if a["use_memory"]] == ["MEMORY"]
    assert by_variant["RAW"]["permissions"]["require_tests_before_finish"] is False
    for v in la.VARIANTS[1:]:
        assert by_variant[v]["permissions"]["require_tests_before_finish"] is True, v


async def test_profile_matches_the_coding_tasks_contract(env):
    agents = (await la.ensure_lab_agents(env.svc))["agents"]
    mem = next(a for a in agents if a["variant"] == "MEMORY")
    prof = la.agent_profile(mem)
    assert set(prof) == {"name", "system_prompt", "max_steps", "tools", "require_tests_before_finish"}
    assert prof["name"] == "LAB · MEMORY" and prof["require_tests_before_finish"] is True
    assert prof["tools"] == list(la.SIDECAR_TOOLS)


async def test_drift_is_repaired_and_owner_model_is_kept(env):
    provider = (await env.client.post("/api/providers", json={
        "name": "локальный", "kind": "openai_compat", "base_url": "http://127.0.0.1:8080/v1",
        "api_key": "sk-test-abcd"})).json()
    model = (await env.client.post("/api/models", json={
        "provider_id": provider["id"], "name": "local-7b", "alias": "local-7b"})).json()
    agents = (await la.ensure_lab_agents(env.svc, model_id=model["id"]))["agents"]
    assert {a["model_id"] for a in agents} == {model["id"]}
    raw = next(a for a in agents if a["variant"] == "RAW")
    # owner (or anyone) edits the prompt through the product API: the lab config drifted
    r = await env.client.patch(f"/api/agents/{raw['id']}", json={"system_prompt": "что-то другое",
                                                                   "tools": ["read_file"]})
    assert r.status_code == 200
    again = await la.ensure_lab_agents(env.svc)
    assert again["updated"] == ["RAW"] and again["created"] == []
    raw2 = next(a for a in again["agents"] if a["variant"] == "RAW")
    assert raw2["id"] == raw["id"]
    assert raw2["system_prompt"] == la.SPECS["RAW"]["system_prompt"]
    assert raw2["tools"] == list(la.SIDECAR_TOOLS)
    assert raw2["model_id"] == model["id"]          # not reset by a call without model_id


async def test_rows_saved_by_the_previous_build_are_realigned_in_place(env):
    """A data dir from yesterday's build holds six rows with the old prompts, USER_UX
    without write_file and uneven step caps: ensure updates them in place (same ids),
    never duplicates them, and a second ensure changes nothing."""
    old = {"RAW": ("Исправь задачу. " + PREAMBLE, list(la.SIDECAR_TOOLS), 30),
           "USER_UX": ("Смотри глазами владельца … " + PREAMBLE,
                       ["read_file", "search", "list_dir", "edit_file", "run_tests"], 30),
           "PLAN_EXECUTE_VERIFY": ("Работай циклом ПЛАН → ИСПОЛНЕНИЕ → ПРОВЕРКА … " + PREAMBLE,
                                   list(la.SIDECAR_TOOLS), 50)}
    ids = {}
    async with env.svc.db.session() as s:
        for variant in la.VARIANTS:
            prompt, tools, steps = old.get(variant, (f"старый промпт {variant}", list(la.SIDECAR_TOOLS), 40))
            res = await s.execute(sa.insert(agents_t).values(
                name=la.agent_name(variant), role=la.LAB_ROLE, system_prompt=prompt, tools=tools,
                max_steps=steps, max_tokens=4096 if variant != "PLAN_EXECUTE_VERIFY" else 6144,
                budget_usd=0.0, enabled=True, created_at=utcnow(),
                permissions={"lab_variant": variant, "use_memory": variant == "MEMORY",
                             "require_tests_before_finish": variant != "RAW"}))
            ids[variant] = int(res.inserted_primary_key[0])
        await s.commit()
    first = await la.ensure_lab_agents(env.svc)
    assert sorted(first["updated"]) == sorted(la.VARIANTS)
    assert sorted(first["created"]) == sorted(la.OBSERVERS) and first["duplicates"] == []
    by_variant = {a["variant"]: a for a in first["agents"]}
    assert {v: a["id"] for v, a in by_variant.items()} == ids
    assert by_variant["USER_UX"]["system_prompt"].startswith(OWNER["USER_UX"])
    assert "write_file" in by_variant["USER_UX"]["tools"]
    second = await la.ensure_lab_agents(env.svc)
    assert second["updated"] == [] and second["created"] == []


async def test_observers_are_not_variants_and_get_no_edit_tools(env):
    res = await la.ensure_lab_agents(env.svc)
    assert {a["variant"] for a in res["agents"]} == set(la.VARIANTS)       # observers are not students
    obs = {o["observer"]: o for o in res["observers"]}
    assert set(obs) == set(la.OBSERVERS)
    for name, row in obs.items():
        assert la.variant_of(row) is None and la.is_observer(row)
        assert set(row["tools"]) <= set(la.READ_ONLY_TOOLS)
        assert not set(row["tools"]) & set(la.EDIT_TOOLS) and "run_tests" not in row["tools"]
        assert row["permissions"]["student"] is False and row["permissions"]["may_edit"] is False
        assert row["permissions"]["use_memory"] is False
        assert not set(la.agent_profile(row)["tools"]) & set(la.EDIT_TOOLS)
    assert obs["CLAUDE_AUDITOR"]["permissions"]["outcomes"] == list(la.AUDITOR_OUTCOMES)
    assert "OBSERVE" in obs["CLAUDE_AUDITOR"]["system_prompt"]
    assert "НЕ помогай" in obs["CLAUDE_AUDITOR"]["system_prompt"]
    assert obs["RESULT_VERIFIER"]["permissions"]["verifier_module"] == "bossman_v3.self_improvement.verifier"
    # a hand-edited row that grows a lab_variant is still not a student
    forged = {**obs["UX_OBSERVER"], "permissions": {**obs["UX_OBSERVER"]["permissions"], "lab_variant": "RAW"}}
    assert la.variant_of(forged) is None
    assert la.agent_profile({**forged, "tools": list(la.SIDECAR_TOOLS)})["tools"] == list(la.READ_ONLY_TOOLS)


async def test_the_coding_task_api_refuses_an_observer_profile(env):
    """Negative control in the same test: a student profile is accepted."""
    res = await la.ensure_lab_agents(env.svc)
    student = next(a for a in res["agents"] if a["variant"] == "TOOL_FIRST")
    prof = await ct._agent_profile(env.svc, student["id"])
    assert prof["name"] == "LAB · TOOL_FIRST" and "edit_file" in prof["tools"]
    for obs in res["observers"]:
        with pytest.raises(HTTPException) as info:
            await ct._agent_profile(env.svc, obs["id"])
        assert info.value.status_code == 409
        assert info.value.detail["code"] == "LAB_OBSERVER_NOT_A_STUDENT"


async def test_http_routes(env):
    r = await env.client.post("/api/lab-agents/ensure", json={})
    assert r.status_code == 200, r.text
    assert len(r.json()["agents"]) == 6 and len(r.json()["observers"]) == 3
    r = await env.client.post("/api/lab-agents/ensure", json={})
    assert r.json()["created"] == []
    listed = (await env.client.get("/api/lab-agents")).json()
    assert len(listed["items"]) == 6 and listed["variants"] == list(la.VARIANTS)
    assert listed["observer_roles"] == list(la.OBSERVERS) and len(listed["observers"]) == 3
    assert listed["auditor_outcomes"] == list(la.AUDITOR_OUTCOMES)
    missing = await env.client.post("/api/lab-agents/ensure", json={"model_id": 999})
    assert missing.status_code == 404


async def test_ordinary_agents_are_not_touched(env):
    mine = (await env.client.post("/api/agents", json={"name": "Решатель", "system_prompt": "x"})).json()
    await la.ensure_lab_agents(env.svc)
    await la.ensure_lab_agents(env.svc)
    rows = await _agents(env.client)
    assert [r for r in rows if r["id"] == mine["id"]][0]["system_prompt"] == "x"
    assert len(rows) == 1 + 6 + 3
