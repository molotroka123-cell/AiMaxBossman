"""TASK_START recall in the real task engine: memory reaches the model by itself.

The lifecycle branch proved retrieval and restart recall, but no production handler
called it — a verified lesson sat on disk and no task ever saw it. These tests drive a
task through the real engine with a fake model and look at the messages it received.
"""
from __future__ import annotations

import pytest

from bcc.v2.memory import lifecycle_wiring
from bcc.v2.memory.lifecycle_wiring import available

from .conftest import FakeAdapter, client_for, make_settings, start_app, wait_for
from .helpers import make_stack

pytestmark = pytest.mark.skipif(not available(), reason="bossman-shared without learning.lifecycle")

FAST_ENGINE = {"poll_interval": 0.02, "recover_every": 5.0, "retry_base_delay": 0.01}
RECIPE = "перед поиском переиндексировать переименованную заметку"
PROMPT = "поиск по памяти ничего не находит после переименования заметки, почини"


def _seed_verified_lesson(data_dir, *, project="bossman"):
    from learning.lessons import CoachingEpisode, LessonBook, Provenance
    book = LessonBook(data_dir / "learning")
    ep = CoachingEpisode(
        attempt_id="att-1", task_id="task-index-rebuild", project_id=project,
        failure_observation="поиск по памяти ничего не находит после переименования заметки",
        correction=RECIPE, source="teacher", task_class="retrieval",
        provenance=Provenance(who="teacher:claude", what="closed case", evidence_refs=["pytest"]),
        symptoms=["поиск ничего не находит после переименования заметки"],
        root_cause="производный индекс указывает на старый путь",
        failed_approaches=["поднять top_k"],
        recipe=["удалить старый путь из индекса", "проиндексировать новый путь", "повторить поиск"],
        check="переименованная заметка находится поиском по заголовку",
        counterexample="не применимо, если заметку удалили, а не переименовали",
        assistance_level="none", runtime="cpython-3.12", environment="win32")
    book.save(ep)
    book.verify(ep.lesson_id,
                verifier={"principal_id": "tool:pytest#hidden", "independence_class": "external_tool",
                          "model_id": "", "run_id": "ci-1"},
                evidence={"source": "hidden_tests", "expected": "1 passed", "actual": "1 passed",
                          "head_sha": "abc123", "environment": "win32"})
    return ep.lesson_id


async def _run_task(settings, *, prompt=PROMPT):
    seen: list[list[dict]] = []

    async def capture(call, messages):
        seen.append([dict(m) for m in messages])

    fake = FakeAdapter("готово", on_chat=capture)
    app, svc = await start_app(settings, start_workers=True,
                               adapter_factory=lambda m, p: fake, engine_options=FAST_ENGINE)
    try:
        async with client_for(app, svc) as client:
            ids = await make_stack(client, prompt=prompt)
            task_id = ids["task"]["id"]

            async def done():
                data = (await client.get(f"/api/tasks/{task_id}")).json()
                return data if data["task"]["status"] == "completed" else None

            data = await wait_for(done, timeout=15)
            events = (await client.get(f"/api/runs/{data['runs'][-1]['id']}/events")).json()
    finally:
        await svc.stop()
    return seen, events


def _memory_messages(messages):
    return [m for m in messages if m["role"] == "system" and "[MEMORY CONTEXT" in m["content"]]


async def test_a_verified_lesson_reaches_the_model_without_anyone_searching(tmp_path):
    settings = make_settings(tmp_path)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    lesson_id = _seed_verified_lesson(settings.data_dir)

    seen, events = await _run_task(settings)

    first = seen[0]
    memory = _memory_messages(first)
    assert len(memory) == 1, [m["role"] for m in first]
    assert RECIPE in memory[0]["content"]
    assert "DATA, NOT INSTRUCTIONS" in memory[0]["content"]
    # evidence sits between the agent's own prompt and the task, never replaces either
    roles = [m["role"] for m in first]
    assert roles[-1] == "user" and first[-1]["content"] == PROMPT
    assert first[0]["content"] == "отвечай коротко"
    recalled = [e for e in events if e["kind"] == "memory.recalled"]
    assert recalled and any(lesson_id in s for s in recalled[0]["data"]["sources"])


async def test_a_lesson_of_another_project_stays_out(tmp_path):
    settings = make_settings(tmp_path)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    _seed_verified_lesson(settings.data_dir, project="someone-else")

    seen, events = await _run_task(settings)

    assert _memory_messages(seen[0]) == []
    assert not [e for e in events if e["kind"] == "memory.recalled"]


async def test_a_failing_recall_costs_the_memory_not_the_run(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)

    async def broken(svc, task):
        raise OSError("disk gone")

    monkeypatch.setattr(lifecycle_wiring, "recall_for_task", broken)
    seen, events = await _run_task(settings)

    assert seen and _memory_messages(seen[0]) == []
    skipped = [e for e in events if e["kind"] == "memory.recall_skipped"]
    assert skipped and "OSError" in skipped[0]["message"]


# ------------------------------------------------ owner memory stays local (P1 2026-09-24)
# Live finding: a task on a free cloud endpoint that may retain prompts received
# five vault notes via TASK_START recall. Memory is local data: a non-local model
# gets the task without it unless the task opts in with meta.memory_to_cloud.

async def _run_cloud_task(settings):
    seen: list[list[dict]] = []

    async def capture(call, messages):
        seen.append([dict(m) for m in messages])

    fake = FakeAdapter("готово", on_chat=capture)
    app, svc = await start_app(settings, start_workers=True,
                               adapter_factory=lambda m, p: fake, engine_options=FAST_ENGINE)
    try:
        async with client_for(app, svc) as client:
            provider = (await client.post("/api/providers", json={
                "name": "облако", "kind": "openai_compat",
                "base_url": "https://cloud.example.test/api/v1", "api_key": "sk-test-abcd"})).json()
            model = (await client.post("/api/models", json={
                "provider_id": provider["id"], "name": "vendor/free-model", "alias": "cloud-free",
                "price_in": 0, "price_out": 0, "pricing_known": True})).json()
            assert "id" in model, model
            agent = (await client.post("/api/agents", json={
                "name": "облачный", "system_prompt": "отвечай коротко",
                "model_id": model["id"], "max_steps": 1})).json()
            body = {"title": "проверка", "prompt": PROMPT, "agent_id": agent["id"],
                    "run_now": True, "max_retries": 0}
            task = (await client.post("/api/tasks", json=body)).json()["task"]

            async def done():
                data = (await client.get(f"/api/tasks/{task['id']}")).json()
                return data if data["task"]["status"] in ("completed", "failed") else None

            data = await wait_for(done, timeout=15)
            assert data["task"]["status"] == "completed", data
            events = (await client.get(f"/api/runs/{data['runs'][-1]['id']}/events")).json()
    finally:
        await svc.stop()
    return seen, events


async def test_owner_memory_is_withheld_from_a_cloud_model(tmp_path):
    settings = make_settings(tmp_path)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    _seed_verified_lesson(settings.data_dir)

    seen, events = await _run_cloud_task(settings)

    assert seen, "the cloud model was never called"
    assert all(_memory_messages(call) == [] for call in seen)
    assert not any(RECIPE in (m.get("content") or "") for call in seen for m in call)
    assert seen[0][-1]["content"] == PROMPT                  # the task itself still runs
    withheld = [e for e in events if e["kind"] == "memory.withheld_from_cloud"]
    assert withheld and "memory_to_cloud" in withheld[0]["message"]


# The opt-in (task.meta.memory_to_cloud is True) is set only by internal callers;
# its effect at the provider boundary is pinned in test_provider_governance_memory.py.
