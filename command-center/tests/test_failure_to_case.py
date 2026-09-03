"""Failure → learning case draft: настоящий отказ заводит черновик обучающей записи.

Проверяем поведение, а не реализацию: черновик рождается из события шины
(в том числе из реально упавшего запуска задачи), повторный отказ увеличивает
счётчик, а список недостающих полей берётся у настоящего learning.trace.validate
— тест грузит его сам, из файла, и сверяет побайтово совпадающий результат.
Отдельно доказываем, что корпус data/learning не меняется ни на байт.
"""
from __future__ import annotations

import hashlib
import importlib.util as iu
from pathlib import Path

import pytest

from bcc.features import failure_to_case as ftc

from .conftest import FakeAdapter, SimpleEnv, client_for, make_settings, start_app, wait_for
from .helpers import make_stack

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS = REPO_ROOT / "data" / "learning"


def real_trace():
    """Настоящий learning/trace.py, загруженный тестом независимо от модуля фичи:
    сравнение имеет смысл только если валидатор взят из первоисточника."""
    spec = iu.spec_from_file_location("t_learning_trace", REPO_ROOT / "learning" / "trace.py")
    mod = iu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def corpus_digest() -> dict[str, str]:
    """Побайтовый слепок корпуса: имя файла → sha256 содержимого."""
    if not CORPUS.exists():
        return {}
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(CORPUS.iterdir()) if p.is_file()}


@pytest.fixture
async def fenv(tmp_path, monkeypatch):
    """Приложение с ВКЛЮЧЁННЫМ флагом: подписка заводится на старте svc."""
    monkeypatch.setenv(ftc.FLAG, "1")
    settings = make_settings(tmp_path)
    app, svc = await start_app(settings, start_workers=False)
    async with client_for(app, svc) as client:
        yield SimpleEnv(app=app, svc=svc, client=client, settings=settings)
    await svc.stop()


async def cases(client) -> list[dict]:
    return (await client.get("/api/failure-cases")).json()["cases"]


async def wait_case(env, count: int = 1) -> list[dict]:
    return await wait_for(lambda: _at_least(env, count))


async def _at_least(env, count: int):
    items = await cases(env.client)
    return items if len(items) >= count else None


async def drive_failing_task(env) -> int:
    """Настоящий упавший запуск: адаптер всегда падает, движок доводит задачу
    до status=failed и сам эмитит task.failed."""
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter(fail_times=99)
    stack = await make_stack(env.client, max_retries=0)
    task_id = stack["task"]["id"]
    for _ in range(6):
        rid = await env.svc.engine.claim()
        if rid is None:
            break
        await env.svc.engine.execute(rid)
    status = (await env.client.get(f"/api/tasks/{task_id}")).json()["task"]["status"]
    assert status == "failed", f"нужен настоящий отказ, а статус {status}"
    return task_id


async def test_real_failed_run_creates_draft_with_symptom_and_time(fenv):
    """Реально упавший запуск задачи рождает черновик с непустым симптомом,
    временем наблюдения и участниками."""
    task_id = await drive_failing_task(fenv)
    item = await wait_for(lambda: _by_trigger(fenv, "task.failed"))

    draft = (await fenv.client.get(f"/api/failure-cases/{item['id']}")).json()
    case = draft["case"]
    assert case["symptom"].strip()
    assert case["symptom"].startswith("task.failed")
    assert draft["first_seen"] and draft["last_seen"]
    assert case["created_at"] == draft["first_seen"]
    assert draft["participants"]["task_id"] == task_id
    assert draft["participants"]["run_id"]
    assert draft["participants"]["module"]
    # воспроизведение и выдержка собраны из событий, а не выдуманы
    assert any("отказ task.failed" in step for step in case["reproduction"])
    assert [e for e in draft["context_events"] if e["position"] == "trigger"]
    assert case["learning_status"] == "UNVERIFIED"


async def _by_trigger(env, kind: str):
    """Настоящий упавший запуск даёт несколько признаков отказа (лог уровня
    error и task.failed) — берём черновик именно упавшего запуска."""
    for item in await cases(env.client):
        if item["trigger_kind"] == kind:
            return item
    return None


async def test_ten_identical_failures_give_one_draft_with_counter(fenv):
    """Один и тот же отказ десять раз — один черновик со счётчиком 10."""
    for i in range(10):
        await fenv.svc.bus.emit("task.failed", task_id=7, run_id=100 + i,
                                error="provider timeout after 30s")
    items = await wait_for(lambda: _counted(fenv, 10))
    assert len(items) == 1 and items[0]["occurrences"] == 10


async def _counted(env, n: int):
    items = await cases(env.client)
    return items if len(items) == 1 and items[0]["occurrences"] >= n else None


async def test_missing_fields_come_from_real_validate(fenv):
    """Список недостающего получен настоящим validate: тест сам грузит
    learning/trace.py и получает ровно те же ошибки на том же черновике."""
    await fenv.svc.bus.emit("run.log", run_id=3, level="error",
                            message="tool call crashed: KeyError('path')")
    items = await wait_case(fenv)
    draft = (await fenv.client.get(f"/api/failure-cases/{items[0]['id']}")).json()

    trace = real_trace()
    expected = trace.validate(draft["case"])
    assert draft["validation"]["errors"] == expected
    assert draft["validation"]["validator"] == "learning.trace.validate"
    assert expected, "черновик не может быть валидной записью — человеку есть что дописать"

    # needs_human — ровно те обязательные поля, которых validate не досчитался
    missing = {e.split(": ", 1)[1] for e in expected if e.startswith("missing required field: ")}
    assert set(draft["needs_human"]) == missing
    assert "root_cause" in missing and "verified_by" in missing
    # то, что реально видно в событии, человеку дописывать не нужно
    assert "symptom" not in missing and "reproduction" not in missing
    assert items[0]["missing_fields"] == draft["needs_human"]


async def test_corpus_data_learning_is_not_touched(fenv):
    """Черновик не попадает в корпус: data/learning не меняется ни на байт,
    а файлы черновиков лежат в settings.data_dir."""
    before = corpus_digest()
    await fenv.svc.bus.emit("task.failed", task_id=5, run_id=9, error="segfault в инструменте")
    await fenv.svc.bus.emit("mission.failed", mission_id=1, reason="никто не взял задачу")
    items = await wait_case(fenv, 2)
    assert corpus_digest() == before

    stored = list((Path(fenv.settings.data_dir) / ftc.DIRNAME).glob("*.json"))
    assert len(stored) == len(items) == 2
    assert all(str(p).startswith(str(fenv.settings.data_dir)) for p in stored)


async def test_delete_drops_draft_and_needs_flag(fenv, monkeypatch):
    """DELETE меняет состояние: с флагом выбрасывает черновик, без флага — 409."""
    await fenv.svc.bus.emit("benchmark.failed", benchmark_id=2, error="упал прогон")
    items = await wait_case(fenv)
    case_id = items[0]["id"]

    monkeypatch.delenv(ftc.FLAG, raising=False)
    assert (await fenv.client.delete(f"/api/failure-cases/{case_id}")).status_code == 409
    assert len(await cases(fenv.client)) == 1

    monkeypatch.setenv(ftc.FLAG, "1")
    assert (await fenv.client.delete(f"/api/failure-cases/{case_id}")).status_code == 200
    assert await cases(fenv.client) == []
    assert (await fenv.client.get(f"/api/failure-cases/{case_id}")).status_code == 404


async def test_flag_off_creates_nothing(env, monkeypatch):
    """Выключенный флаг: подписки нет, отказ не оставляет ни файла, ни ответа."""
    monkeypatch.delenv(ftc.FLAG, raising=False)
    before = corpus_digest()
    await drive_failing_task(env)
    await env.svc.bus.emit("worker.error", message="boom")

    body = (await env.client.get("/api/failure-cases")).json()
    assert body == {"enabled": False, "count": 0, "cases": []}
    assert not (Path(env.settings.data_dir) / ftc.DIRNAME).exists()
    assert corpus_digest() == before
