"""§26 — неизменяемая провенанс-запись прогона.

Дефект, который эти тесты держат закрытым: историю исполнения рассказывала
таблица `agents`, а она меняется и удаляется. Правка агента переписывала
прошлое, удаление — стирало его. Прогон помнил из личности исполнителя ровно
`model_alias`, поэтому на вопрос «с какими правами и каким промптом эта задача
ходила три дня назад» ответа не существовало.

Каждый тест ниже сначала ДОКАЗЫВАЕТ, что источник действительно изменился
(агент отредактирован, промпт другой, права другие, агента нет), и только потом
требует, чтобы запись прогона осталась прежней. Без первой половины тест
проходил бы и на системе, которая просто ничего не записала.
"""
from __future__ import annotations

import json

import pytest
import sqlalchemy as sa

from bcc import run_provenance
from bcc.db import agents as agents_t, task_runs as runs_t

from .conftest import FakeAdapter, client_for, make_settings, start_app, wait_for
from .helpers import make_stack

FAST_ENGINE = {"poll_interval": 0.02, "recover_every": 5.0, "retry_base_delay": 0.01}


async def _finished_run(client, task_id: int) -> dict:
    async def done():
        data = (await client.get(f"/api/tasks/{task_id}")).json()
        runs = data.get("runs") or []
        return runs[-1] if runs and runs[-1].get("finished_at") else None
    return await wait_for(done, timeout=15)


async def _stored_provenance(svc, run_id: int):
    """Сырое значение из базы — минуя API, чтобы читать факт, а не его показ."""
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(runs_t.c.provenance).where(
            runs_t.c.id == run_id))).first()
    value = row[0] if row else None
    return json.loads(value) if isinstance(value, str) else value


async def _run_one(tmp_path, **agent_over):
    """Довести один прогон до конца и вернуть (svc, client-context, ids, run)."""
    settings = make_settings(tmp_path)
    app, svc = await start_app(settings, start_workers=True,
                               adapter_factory=lambda m, p: FakeAdapter("готово"),
                               engine_options=FAST_ENGINE)
    return app, svc, settings


# --------------------------------------------------------------- снятие вообще

async def test_provenance_is_captured_at_run_start(tmp_path):
    """Законный случай: прогон помнит, кто и чем его выполнял."""
    app, svc, _ = await _run_one(tmp_path)
    try:
        async with client_for(app, svc) as client:
            ids = await make_stack(client, max_steps=1)
            run = await _finished_run(client, ids["task"]["id"])
            stored = await _stored_provenance(svc, run["id"])

            assert stored is not None, "провенанс не снят вовсе"
            assert stored["schema_version"] == run_provenance.SCHEMA_VERSION
            assert stored["scope"] == "RUN_START_CONFIGURATION"
            assert stored["run_id"] == run["id"]
            assert stored["task_id"] == ids["task"]["id"]
            assert stored["fence"] == run["fence"]
            assert stored["agent_id"] == ids["agent"]["id"]
            assert stored["agent_name"] == "аналитик"
            assert stored["agent_present"] is True
            # промпт — дайджестом, не текстом
            assert stored["system_prompt_sha256"] == \
                run_provenance._sha256("отвечай коротко")
            assert "отвечай коротко" not in json.dumps(stored, ensure_ascii=False)
            # модель названа, ключ провайдера — нет
            assert stored["model"]["alias"] == "local-7b"
            assert stored["model"]["present"] is True
            assert "sk-test-abcd" not in json.dumps(stored)
            # полномочия и пределы — дайджестами
            for key in ("allowed_tools_sha256", "permissions_sha256",
                        "policy_rules_sha256", "budget_sha256", "agent_revision"):
                assert isinstance(stored[key], str) and len(stored[key]) == 64, key
            assert stored["privacy_mode"] in ("ONLINE", "OFFLINE", "UNKNOWN")
    finally:
        await svc.stop()


async def test_api_reports_historical_runs_as_not_captured_not_as_empty(tmp_path):
    """Никакого выдуманного дозаполнения.

    Прогон, снятый до появления колонки, обязан честно сказать NOT_CAPTURED.
    Подставить туда сегодняшнюю конфигурацию агента значило бы соврать ровно в
    том месте, ради которого всё это писалось; отдать пустой словарь — почти то
    же самое, потому что читатель примет его за «прав не было».
    """
    app, svc, _ = await _run_one(tmp_path)
    try:
        async with client_for(app, svc) as client:
            ids = await make_stack(client, max_steps=1)
            await _finished_run(client, ids["task"]["id"])
            # Исторический прогон — это прогон, у которого провенанса НИКОГДА не
            # было. Занулить его у существующего нельзя, и это правильно:
            # стереть улику — тоже изменить её, и триггер отказывает (см.
            # test_terminal_run_refuses_provenance_mutation_in_the_database).
            async with svc.db.session() as s:
                old_run = await s.execute(sa.insert(runs_t).values(
                    task_id=ids["task"]["id"], attempt=7, status="completed",
                    result="прогон из прошлого"))
                await s.commit()
                old_id = int(old_run.inserted_primary_key[0])
            assert await _stored_provenance(svc, old_id) is None

            shown = (await client.get(f"/api/runs/{old_id}")).json()["provenance"]
            assert shown["status"] == run_provenance.NOT_CAPTURED
            assert "agent_id" not in shown
            assert "reason" in shown          # сказано, ПОЧЕМУ нечего показать
    finally:
        await svc.stop()


# ------------------------------------------- источник меняется, запись — нет

@pytest.mark.parametrize("edit,label", [
    ({"name": "переименованный", "role": "другая роль"}, "agent renamed"),
    ({"system_prompt": "СОВЕРШЕННО ДРУГОЙ ПРОМПТ"}, "system prompt changed"),
    ({"permissions": {"tool_rules": [
        {"tool": "terminal.run", "resource": "*", "effect": "ALLOW"}]}}, "permissions changed"),
    ({"max_steps": 99, "max_tokens": 999_999, "budget_usd": 500.0}, "budget changed"),
])
async def test_editing_the_agent_cannot_rewrite_a_finished_run(tmp_path, edit, label):
    """Правка агента после прогона не трогает то, что прогон уже записал."""
    app, svc, _ = await _run_one(tmp_path)
    try:
        async with client_for(app, svc) as client:
            ids = await make_stack(client, max_steps=1)
            run = await _finished_run(client, ids["task"]["id"])
            before = await _stored_provenance(svc, run["id"])
            assert before is not None

            # 1. источник ДЕЙСТВИТЕЛЬНО изменился — иначе тест доказывает пустоту
            agent_id = ids["agent"]["id"]
            async with svc.db.session() as s:
                await s.execute(sa.update(agents_t).where(
                    agents_t.c.id == agent_id).values(**edit))
                await s.commit()
                changed = dict((await s.execute(sa.select(agents_t).where(
                    agents_t.c.id == agent_id))).first()._mapping)
            for key, value in edit.items():
                assert changed[key] == value, (label, key)

            # 2. запись прогона осталась прежней, побайтово
            after = await _stored_provenance(svc, run["id"])
            assert after == before, label

            # 3. и то, что изменилось, действительно попало бы в дайджест —
            #    иначе неизменность ничего не значит
            fresh = run_provenance.build(task={"kind": "generic", "meta": {}},
                                         run={"attempt": 0}, agent=changed)
            assert fresh["agent_revision"] != before["agent_revision"], label
    finally:
        await svc.stop()


async def test_provenance_survives_deleting_the_agent(tmp_path):
    """Агента нет — прогон всё ещё восстановим.

    Именно этот случай схема раньше не переживала: FK объявлен
    ON DELETE SET NULL, и удаление агента стирало последнюю ниточку к тому,
    кто выполнял задачу.
    """
    app, svc, _ = await _run_one(tmp_path)
    try:
        async with client_for(app, svc) as client:
            ids = await make_stack(client, max_steps=1)
            run = await _finished_run(client, ids["task"]["id"])
            before = await _stored_provenance(svc, run["id"])

            async with svc.db.session() as s:
                await s.execute(sa.delete(agents_t).where(
                    agents_t.c.id == ids["agent"]["id"]))
                await s.commit()
                gone = (await s.execute(sa.select(agents_t).where(
                    agents_t.c.id == ids["agent"]["id"]))).first()
            assert gone is None, "агент должен быть действительно удалён"

            after = await _stored_provenance(svc, run["id"])
            assert after == before
            assert after["agent_id"] == ids["agent"]["id"]
            assert after["agent_name"] == "аналитик"
            assert after["system_prompt_sha256"] == \
                run_provenance._sha256("отвечай коротко")
            # и наружу это по-прежнему читается как снятое, а не как потерянное
            shown = (await client.get(f"/api/runs/{run['id']}")).json()["provenance"]
            assert shown["status"] == "CAPTURED"
    finally:
        await svc.stop()


# ------------------------------------------------------- неизменяемость записи

async def test_terminal_run_refuses_provenance_mutation_in_the_database(tmp_path):
    """Запрет стоит в БАЗЕ, а не в вызове.

    `task_runs` пишется из двух десятков мест. Проверка ниже идёт мимо всего
    кода приложения — прямым UPDATE, — потому что улика, которую можно
    переписать хотя бы одним путём, уликой не является.
    """
    app, svc, _ = await _run_one(tmp_path)
    try:
        async with client_for(app, svc) as client:
            ids = await make_stack(client, max_steps=1)
            run = await _finished_run(client, ids["task"]["id"])
            before = await _stored_provenance(svc, run["id"])
            assert before is not None

            forged = dict(before, agent_name="кто-то другой",
                          system_prompt_sha256="0" * 64)
            with pytest.raises(Exception) as excinfo:
                async with svc.db.session() as s:
                    await s.execute(sa.update(runs_t).where(
                        runs_t.c.id == run["id"]).values(provenance=forged))
                    await s.commit()
            assert "provenance is immutable" in str(excinfo.value)

            assert await _stored_provenance(svc, run["id"]) == before

            # Стереть улику — тоже изменить её. Занулением провенанс не
            # выносится: иначе «переписать» превратилось бы в «удалить и
            # записать заново».
            with pytest.raises(Exception) as erased:
                async with svc.db.session() as s:
                    await s.execute(sa.update(runs_t).where(
                        runs_t.c.id == run["id"]).values(provenance=None))
                    await s.commit()
            assert "provenance is immutable" in str(erased.value)

            # Разрешённое соседство: другие колонки терминального прогона
            # по-прежнему дописываются (улики, ссылки, брони).
            async with svc.db.session() as s:
                await s.execute(sa.update(runs_t).where(
                    runs_t.c.id == run["id"]).values(result="дописано позже"))
                await s.commit()
            assert await _stored_provenance(svc, run["id"]) == before
    finally:
        await svc.stop()


async def test_capture_is_write_once_and_a_restart_does_not_rewrite_it(tmp_path):
    """Рестарт, поднявший тот же прогон, снимает ту же личность и не переписывает.

    Проверяется на живом движке: провенанс снимается, затем агент меняется, и
    повторное снятие на том же прогоне обязано быть no-op, а не «обновлением».
    """
    app, svc, _ = await _run_one(tmp_path)
    try:
        async with client_for(app, svc) as client:
            ids = await make_stack(client, max_steps=1)
            run = await _finished_run(client, ids["task"]["id"])
            before = await _stored_provenance(svc, run["id"])

            async with svc.db.session() as s:
                task = dict((await s.execute(sa.select(sa.text("*")).select_from(
                    sa.text("tasks")).where(sa.text(f"id = {ids['task']['id']}")))
                ).first()._mapping)
                await s.execute(sa.update(agents_t).where(
                    agents_t.c.id == ids["agent"]["id"]).values(
                    system_prompt="подменённый после прогона"))
                await s.commit()
                agent = dict((await s.execute(sa.select(agents_t).where(
                    agents_t.c.id == ids["agent"]["id"]))).first()._mapping)

            # второе снятие на том же прогоне — ровно то, что делает рестарт
            await svc.engine._capture_provenance(run["id"], task, dict(run), agent)
            assert await _stored_provenance(svc, run["id"]) == before
    finally:
        await svc.stop()


# ------------------------------------------------- чистая функция: без секретов

def test_build_never_carries_secrets_or_hidden_reasoning():
    """Провенанс отвечает «какой провайдер», а не «каким ключом»."""
    record = run_provenance.build(
        task={"kind": "generic", "meta": {"limits": {"max_usd": 1.0}},
              "prompt": "секретный текст задачи"},
        run={"attempt": 0},
        agent={"id": 1, "name": "a", "system_prompt": "s",
               "permissions": {"tool_rules": [], "note": "оформление"}},
        model={"id": 2, "alias": "local-7b", "name": "n", "kind": "local",
               "provider_id": 3, "api_key_enc": "gAAAAABsecret",
               "context_window": 8192},
    )
    blob = json.dumps(record, ensure_ascii=False)
    for forbidden in ("gAAAAABsecret", "api_key", "секретный текст задачи"):
        assert forbidden not in blob, forbidden
    assert record["model"]["provider_id"] == 3


def test_permission_cosmetics_do_not_look_like_a_permission_change():
    """Негативный контроль к дайджесту прав: переименование — не полномочие,
    а вот новое правило — полномочие."""
    base = {"id": 1, "name": "a", "system_prompt": "s",
            "permissions": {"tool_rules": [{"tool": "x", "effect": "ALLOW"}]}}
    args = dict(task={"kind": "generic", "meta": {}}, run={"attempt": 0})

    cosmetic = dict(base, permissions=dict(base["permissions"], note="просто заметка"))
    assert run_provenance.build(agent=cosmetic, **args)["permissions_sha256"] == \
        run_provenance.build(agent=base, **args)["permissions_sha256"]

    widened = dict(base, permissions={"tool_rules": [
        {"tool": "x", "effect": "ALLOW"}, {"tool": "terminal.run", "effect": "ALLOW"}]})
    assert run_provenance.build(agent=widened, **args)["permissions_sha256"] != \
        run_provenance.build(agent=base, **args)["permissions_sha256"]


def test_digest_is_stable_across_key_order():
    """Иначе «права не менялись» было бы недоказуемо: тот же набор давал бы
    разный sha256 в зависимости от порядка ключей в памяти."""
    args = dict(task={"kind": "generic", "meta": {}}, run={"attempt": 0})
    one = {"id": 1, "name": "a", "system_prompt": "s",
           "permissions": {"a": 1, "b": 2, "tool_rules": []}}
    two = {"id": 1, "name": "a", "system_prompt": "s",
           "permissions": {"tool_rules": [], "b": 2, "a": 1}}
    assert run_provenance.build(agent=one, **args)["permissions_sha256"] == \
        run_provenance.build(agent=two, **args)["permissions_sha256"]


def test_describe_refuses_a_schema_from_the_future():
    """Читатель, встретивший больший номер версии, обязан сказать, что не
    понимает запись, а не разобрать её наполовину."""
    assert run_provenance.describe({"schema_version": 999})["status"] == \
        "UNSUPPORTED_SCHEMA"
    assert run_provenance.describe("не json")["status"] == "UNREADABLE"
    assert run_provenance.describe(None)["status"] == run_provenance.NOT_CAPTURED


def test_agentless_run_records_absence_as_a_fact():
    """Задача без агента — тоже факт исполнения, а не пустой словарь,
    притворяющийся агентом."""
    record = run_provenance.build(task={"kind": "generic", "meta": {}},
                                  run={"attempt": 0}, agent=None)
    assert record["agent_present"] is False
    assert record["agent_id"] is None
    assert record["system_prompt_sha256"] == run_provenance.NOT_CAPTURED
    assert record["model"]["present"] is False


def test_installed_provenance_uses_embedded_build_sha(tmp_path, monkeypatch):
    package = tmp_path / "site-packages" / "bcc"
    package.mkdir(parents=True)
    (package / "_build.json").write_text(json.dumps({"source_sha": "a" * 40}))
    monkeypatch.setattr(run_provenance, "__file__", str(package / "run_provenance.py"))
    monkeypatch.setenv("BCC_BUILD_SHA", "b" * 40)
    assert run_provenance.repository_sha() == "a" * 40


@pytest.mark.parametrize("manifest", [None, {"source_sha": "fake"}, {"source_sha": 123}, []])
def test_installed_missing_or_malformed_build_cannot_borrow_env_sha(tmp_path, monkeypatch, manifest):
    package = tmp_path / "site-packages" / "bcc"
    package.mkdir(parents=True)
    if manifest is not None:
        (package / "_build.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(run_provenance, "__file__", str(package / "run_provenance.py"))
    monkeypatch.setenv("BCC_BUILD_SHA", "b" * 40)
    assert run_provenance.repository_sha() == run_provenance.NOT_CAPTURED


@pytest.mark.parametrize("version", [True, False, 0, -1, "1"])
def test_malformed_schema_never_claims_captured(version):
    assert run_provenance.describe({"schema_version": version})["status"] == "UNSUPPORTED_SCHEMA"


async def test_stale_worker_cannot_write_run_provenance(env):
    from .test_fence_fl01 import _takeover, _run_row
    from bcc.engine import FencedOut
    ids = await make_stack(env.client)
    engine = env.svc.engine
    run_id = await engine.claim()
    before = await _run_row(env.svc.db, run_id)
    successor = await _takeover(env, engine, run_id)
    try:
        with pytest.raises(FencedOut):
            await engine._capture_provenance(run_id, ids["task"], before, ids["agent"])
        assert await _stored_provenance(env.svc, run_id) is None
    finally:
        await successor.aclose()


async def test_capture_failure_cannot_dispatch_or_complete(env, monkeypatch):
    from .test_fence_fl01 import _run_row
    calls = []
    async def called(*args):
        calls.append(1)
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("4", on_chat=called)
    ids = await make_stack(env.client, max_retries=0)
    def broken(**kwargs):
        raise ValueError("CANARY-capture-secret-must-not-enter-events")
    monkeypatch.setattr(run_provenance, "build", broken)
    run_id = await env.svc.engine.claim()
    await env.svc.engine.execute(run_id)
    row = await _run_row(env.svc.db, run_id)
    assert row["status"] == "failed"
    assert calls == []
    assert "provenance_capture_failed" in row["error"]
    from bcc.db import run_events
    async with env.svc.db.session() as session:
        events = (await session.execute(sa.select(run_events).where(run_events.c.run_id == run_id))).all()
    assert "CANARY-capture-secret" not in str(events)


async def test_historical_run_cannot_be_backfilled(env):
    from sqlalchemy.exc import IntegrityError
    ids = await make_stack(env.client)
    async with env.svc.db.session() as session:
        result = await session.execute(sa.insert(runs_t).values(
            task_id=ids["task"]["id"], attempt=9, status="completed", result="historic"))
        old_id = int(result.inserted_primary_key[0])
        await session.commit()
    with pytest.raises(IntegrityError, match="provenance.*backfill"):
        async with env.svc.db.session() as session:
            await session.execute(sa.update(runs_t).where(runs_t.c.id == old_id).values(
                provenance={"schema_version": 1}))
            await session.commit()
    assert await _stored_provenance(env.svc, old_id) is None


def test_resume_without_prior_capture_does_not_fabricate_original_configuration():
    record = run_provenance.build(task={"id": 1}, agent=None,
                                  run={"id": 2, "previous_started_at": "2026-01-01"})
    assert record["captured_at_run_start"] is False
    assert record["scope"] == "RESUMED_RUN_CONFIGURATION"


def test_repository_sha_tracks_only_clean_current_head(tmp_path):
    import subprocess
    root = tmp_path / "code"
    root.mkdir()
    def git(*args):
        result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True)
        return result.stdout.strip()
    git("init")
    git("config", "user.name", "Provenance test")
    git("config", "user.email", "provenance@example.invalid")
    (root / "code.py").write_text("initial")
    git("add", "code.py")
    git("commit", "-m", "first")
    first = git("rev-parse", "HEAD")
    assert run_provenance.repository_sha(root) == first
    (root / "code.py").write_text("modified")
    assert run_provenance.repository_sha(root) == run_provenance.NOT_CAPTURED
    git("commit", "-am", "second")
    second = git("rev-parse", "HEAD")
    assert second != first
    assert run_provenance.repository_sha(root) == second
