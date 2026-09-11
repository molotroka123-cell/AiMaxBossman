"""Память проекта: родная авторитетна, OpenContext — теневое зеркало.

Сценарий из §7 мастер-прогона проверяется целиком и на настоящей базе, а не
на моке хранилища: сессия A записывает решение, процесс завершается, сессия B
поднимается заново и обязана достать ИМЕННО ЭТО решение с его происхождением;
другой репозиторий его не видит; обновление делает старую версию
проигравшей; после перезапуска остаётся последняя верная версия.

Транспорт OpenContext подставляется, потому что проверяется контракт АДАПТЕРА,
а не чужой сервис: адаптер обязан одинаково честно вести себя, когда зеркало
молчит, отдаёт мусор, падает или возвращает запись чужого проекта. Поднятый
рядом настоящий OpenContext ни одного из этих четырёх случаев не покажет.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from bcc.db import Database
from bcc.hybrid.capabilities import (
    BackendUnavailableError,
    ContextNamespace,
    EffectCorrelation,
    MalformedResponseError,
    OperationCancelledError,
    OperationTimeoutError,
)
from bcc.hybrid.context_filter import SecretMaterialRefused, scrub_for_memory
from bcc.hybrid.context_store import (
    BossmanNativeContextStore,
    ContextStorePlanner,
    OpenContextShadowStore,
    StaleContextWrite,
    build_record,
)

BOSSMAN = ContextNamespace(project="Bossman", repository="molotroka123-cell/AiMaxBossman")
OTHER = ContextNamespace(project="Solana Volume Suite", repository="molotroka123-cell/svs")


def correlation(*, seconds: float = 30.0, cancelled: bool = False) -> EffectCorrelation:
    return EffectCorrelation(
        correlation_id="c-1", task_id="t-1", run_id="r-1",
        deadline_epoch_s=time.time() + seconds,
        cancellation_token=(lambda: True) if cancelled else None)


async def open_db(path) -> Database:
    db = Database(f"sqlite+aiosqlite:///{path}")
    await db.create_all()
    return db


@pytest.fixture
async def db(tmp_path):
    database = await open_db(tmp_path / "bcc.db")
    try:
        yield database
    finally:
        await database.close()


# --------------------------------------------------------------- фильтр секретов

@pytest.mark.parametrize("source", [
    ".env", "project/.env.production", "/home/o/.ssh/id_ed25519",
    "C:\\Users\\o\\.aws\\credentials", "deploy/server.pem", ".git-credentials",
])
def test_a_credential_file_is_refused_not_redacted(source):
    """У файла учётных данных нет вычищенной формы — он весь и есть секрет."""
    with pytest.raises(SecretMaterialRefused):
        scrub_for_memory("DATABASE_URL=postgres://u:p@h/db", source=source)


@pytest.mark.parametrize("source", [
    "docs/environment.md", "src/env_loader.py", "notes/aws-architecture.md", "",
])
def test_ordinary_files_that_merely_mention_credentials_are_not_refused(source):
    """Обратный контроль: отказ обязан ловить файлы, а не подстроки.

    Без этой половины «фильтр» мог бы отвергать `docs/environment.md` и
    выглядеть строгим, потеряв ровно ту память, ради которой он написан.
    """
    result = scrub_for_memory("we decided to read config from the environment",
                              source=source)
    assert result.redactions == ()
    assert "environment" in result.body


# Канарейки склеены из частей и помечены маркером — это принятый в репозитории
# способ держать секрет-ОБРАЗНЫЕ строки в тестах, не кладя их в исходники
# литералом (см. tests/test_ci_secret_scan.py). Значения заведомо фальшивые.
CANARIES = [  # ci-secret-scan: allow
    ("github_token", "gh" + "p_" + "0123456789abcdefghij0123456789abcdefgh"),  # ci-secret-scan: allow
    ("aws_access_key_id", "AK" + "IA" + "IOSFODNN7EXAMPLE"),  # ci-secret-scan: allow
    ("bearer_token", "Bearer " + "abcdefghijklmnopqrstuvwxyz012345"),  # ci-secret-scan: allow
    ("assigned_secret", '"client_secret": "' + 's3cr3t-value-here"'),  # ci-secret-scan: allow
    ("url_with_password", "postgres://admin:" + "hunter2" + "@db.internal/app"),  # ci-secret-scan: allow
    ("openai_key", "sk" + "-" + "abcdefghijklmnopqrstuvwxyz0123456789"),  # ci-secret-scan: allow
]


@pytest.mark.parametrize("kind,secret", CANARIES, ids=[c[0] for c in CANARIES])
def test_a_secret_inside_useful_text_is_redacted_and_the_redaction_is_named(kind, secret):
    result = scrub_for_memory(f"the team decided: {secret} goes in the vault",
                              source="notes/decision.md")
    assert kind in result.redactions, result
    assert f"[REDACTED:{kind}]" in result.body
    # Ни одна канарейка не остаётся в теле — ни та, что искали, ни любая другая.
    for _, value in CANARIES:
        assert value not in result.body
    # Обратный контроль: полезный текст вокруг секрета сохранён, а не стёрт.
    assert "the team decided" in result.body and "goes in the vault" in result.body


def test_a_record_that_is_nothing_but_a_secret_is_refused():
    with pytest.raises(SecretMaterialRefused):
        scrub_for_memory(CANARIES[0][1], source="paste.txt")


def test_the_secret_filter_guards_the_native_store_too(db):
    """Фильтр стоит перед ОБОИМИ хранилищами, а не только перед зеркалом."""
    with pytest.raises(SecretMaterialRefused):
        build_record(namespace=BOSSMAN, key="deploy", version=1,
                     body="anything", source=".env")


# --------------------------------------------------------------- родная память

async def test_a_decision_survives_process_death_and_is_recovered_with_provenance(tmp_path):
    """§7, сессия A -> смерть процесса -> сессия B. Настоящий файл, не мок."""
    path = tmp_path / "bcc.db"

    session_a = await open_db(path)
    store_a = BossmanNativeContextStore(session_a)
    await store_a.write_context(build_record(
        namespace=BOSSMAN, key="video-preview-codec",
        body="Preview renders webm/VP9/Opus because the shipped Chromium has no H.264.",
        version=1, source="docs/final/BUG_LEDGER.md", author="convergence-run",
        origin_sha="0" * 40, reason="MEDIA_ERR_SRC_NOT_SUPPORTED in the owner's preview"),
        correlation())
    await session_a.close()
    del store_a, session_a

    session_b = await open_db(path)
    try:
        store_b = BossmanNativeContextStore(session_b)
        found = await store_b.retrieve_candidates(
            BOSSMAN, "preview codec chromium", limit=5, correlation=correlation())
        assert found, "the decision did not survive the restart"
        top = found[0].record
        assert top.key == "video-preview-codec"
        assert "webm/VP9/Opus" in top.body
        assert top.provenance.source == "docs/final/BUG_LEDGER.md"
        assert top.provenance.origin_sha == "0" * 40
        assert found[0].is_authoritative is True
    finally:
        await session_b.close()


async def test_another_repository_cannot_see_it(db):
    store = BossmanNativeContextStore(db)
    await store.write_context(build_record(
        namespace=BOSSMAN, key="video-preview-codec", body="webm for preview",
        version=1, source="docs/final/BUG_LEDGER.md"), correlation())
    assert await store.retrieve_candidates(OTHER, "preview codec", limit=5,
                                           correlation=correlation()) == []
    # …и обратный контроль: в своём пространстве имён она находится.
    assert await store.retrieve_candidates(BOSSMAN, "preview codec", limit=5,
                                           correlation=correlation())


async def test_an_update_wins_and_the_stale_version_loses(db, tmp_path):
    store = BossmanNativeContextStore(db)
    await store.write_context(build_record(
        namespace=BOSSMAN, key="preview-codec", body="preview renders mp4/H.264",
        version=1, source="docs/decisions.md"), correlation())
    await store.write_context(build_record(
        namespace=BOSSMAN, key="preview-codec", body="preview renders webm/VP9",
        version=2, source="docs/decisions.md"), correlation())

    latest = await store.retrieve_candidates(BOSSMAN, "preview codec", limit=5,
                                             correlation=correlation())
    assert len(latest) == 1, "a superseded version must not be answered as current"
    assert latest[0].record.version == 2
    assert "webm/VP9" in latest[0].record.body

    # Старая версия, пришедшая позже (например, из отставшего зеркала или
    # повторно проигранного события), НЕ выигрывает.
    with pytest.raises(StaleContextWrite):
        await store.write_context(build_record(
            namespace=BOSSMAN, key="preview-codec", body="preview renders mp4/H.264",
            version=1, source="docs/decisions.md"), correlation())
    still = await store.retrieve_candidates(BOSSMAN, "preview codec", limit=5,
                                            correlation=correlation())
    assert still[0].record.version == 2 and "webm/VP9" in still[0].record.body


async def test_the_latest_version_is_what_survives_the_next_restart(tmp_path):
    path = tmp_path / "bcc.db"
    first = await open_db(path)
    store = BossmanNativeContextStore(first)
    for version, body in ((1, "mp4"), (2, "webm"), (3, "webm + realtime encode")):
        await store.write_context(build_record(
            namespace=BOSSMAN, key="preview-codec", body=body, version=version,
            source="docs/decisions.md"), correlation())
    await first.close()

    second = await open_db(path)
    try:
        found = await BossmanNativeContextStore(second).retrieve_candidates(
            BOSSMAN, "preview", limit=5, correlation=correlation())
        assert len(found) == 1 and found[0].record.version == 3
        assert found[0].record.body == "webm + realtime encode"
    finally:
        await second.close()


async def test_forgetting_removes_the_history_too(db):
    planner = ContextStorePlanner(BossmanNativeContextStore(db))
    for version in (1, 2):
        await planner.remember(build_record(
            namespace=BOSSMAN, key="preview-codec", body=f"v{version}", version=version,
            source="docs/decisions.md"), correlation())
    assert await planner.forget(BOSSMAN, "preview-codec", correlation()) is True
    assert await planner.recall(BOSSMAN, "preview", correlation=correlation()) == []
    # Удаление того, чего нет, — это False, а не исключение и не True.
    assert await planner.forget(BOSSMAN, "preview-codec", correlation()) is False


async def test_cancellation_and_deadline_are_checked_before_the_work(db):
    store = BossmanNativeContextStore(db)
    record = build_record(namespace=BOSSMAN, key="k", body="b", version=1,
                          source="docs/decisions.md")
    with pytest.raises(OperationCancelledError):
        await store.write_context(record, correlation(cancelled=True))
    with pytest.raises(OperationTimeoutError):
        await store.write_context(record, correlation(seconds=-1))
    # Ни одна из отменённых попыток ничего не записала.
    assert await store.retrieve_candidates(BOSSMAN, "b", limit=5,
                                           correlation=correlation()) == []


# --------------------------------------------------------------- зеркало

class FakeTransport:
    """Подставной OpenContext, который можно заставить вести себя плохо."""

    def __init__(self, *, behaviour="ok", items=None):
        self.behaviour = behaviour
        self.items = items or []
        self.writes = []
        self.deletes = []

    async def request(self, method, path, payload=None):
        if self.behaviour == "raise":
            raise ConnectionRefusedError("opencontext is not running")
        if self.behaviour == "garbage":
            return ["not", "an", "object"]
        if self.behaviour == "silent":
            await asyncio.sleep(0)
            return {}
        if method == "PUT":
            self.writes.append(payload)
            return {"ok": True}
        if method == "DELETE":
            self.deletes.append(payload)
            return {"deleted": True}
        if path == "/health":
            return {"ok": True}
        return {"items": list(self.items)}


def shadow(transport):
    return OpenContextShadowStore(transport, upstream_sha="0649e713", endpoint="http://127.0.0.1:7788")


async def test_the_mirror_receives_the_write_but_never_owns_it(db):
    transport = FakeTransport()
    planner = ContextStorePlanner(BossmanNativeContextStore(db), shadow(transport))
    observation = await planner.remember(build_record(
        namespace=BOSSMAN, key="preview-codec", body="webm", version=1,
        source="docs/decisions.md"), correlation())
    # Авторитетный исход пришёл от РОДНОГО хранилища.
    assert observation.source_runtime.backend_type == "bossman_native"
    assert observation.verified_by_bossman is True
    assert planner.mirrored_writes == 1
    assert transport.writes[0]["namespace"] == BOSSMAN.key()


@pytest.mark.parametrize("behaviour", ["raise", "garbage", "silent"])
async def test_a_broken_mirror_never_fails_the_write(db, behaviour):
    """Отсутствующий, сломанный и молчащий бэкенд — три разных случая, один исход.

    Bossman уже принял факт. Потерять его из-за чужого сервиса было бы ровно
    тем ущербом, ради предотвращения которого зеркало и держат теневым.
    """
    planner = ContextStorePlanner(BossmanNativeContextStore(db),
                                  shadow(FakeTransport(behaviour=behaviour)))
    await planner.remember(build_record(
        namespace=BOSSMAN, key="preview-codec", body="webm", version=1,
        source="docs/decisions.md"), correlation())
    found = await planner.recall(BOSSMAN, "preview", correlation=correlation())
    assert len(found) == 1 and found[0].is_authoritative is True
    # Тишина зеркала ВИДНА, а не прочитана как согласие.
    if behaviour != "silent":
        assert planner.failed_mirror_writes == 1
        assert planner.last_mirror_error


async def test_an_unconfigured_mirror_is_named_not_pretended(db):
    store = OpenContextShadowStore(None)
    assert store.get_runtime_identity().is_healthy is False
    probe = await store.health_probe(correlation())
    assert probe["reachable"] is False and probe["reason"] == "not_configured"
    with pytest.raises(BackendUnavailableError):
        await store.write_context(build_record(
            namespace=BOSSMAN, key="k", body="b", version=1,
            source="docs/decisions.md"), correlation())


async def test_a_malformed_search_answer_is_an_error_not_a_result():
    store = shadow(FakeTransport(behaviour="silent"))
    with pytest.raises(MalformedResponseError):
        await store.retrieve_candidates(BOSSMAN, "preview", limit=5,
                                        correlation=correlation())


async def test_the_mirror_cannot_smuggle_another_projects_context(db):
    """Главный негативный контроль изоляции.

    Бэкенд СПРОСИЛИ про Bossman и он ответил записью другого проекта — по
    ошибке, из-за настройки или потому, что протокол разъехался. Проверка
    на нашей стороне обязана её отбросить: внешний стор — источник данных,
    а не источник истины о том, кому эти данные принадлежат.
    """
    leaked = {"project": OTHER.project, "repository": OTHER.repository,
              "key": "private-key-rotation", "body": "rotate quarterly",
              "version": 9, "provenance": {"source": "svs/docs/ops.md"}}
    planner = ContextStorePlanner(BossmanNativeContextStore(db),
                                  shadow(FakeTransport(items=[leaked])))
    await planner.remember(build_record(
        namespace=BOSSMAN, key="preview-codec", body="webm", version=1,
        source="docs/decisions.md"), correlation())
    found = await planner.recall(BOSSMAN, "rotate private key quarterly",
                                 correlation=correlation())
    assert [c.record.key for c in found] == ["preview-codec"]
    assert all(c.record.namespace.key() == BOSSMAN.key() for c in found)


async def test_a_mirror_candidate_without_provenance_is_dropped(db):
    nameless = {"project": BOSSMAN.project, "repository": BOSSMAN.repository,
                "key": "unattributed", "body": "ship it on Friday", "version": 1}
    planner = ContextStorePlanner(BossmanNativeContextStore(db),
                                  shadow(FakeTransport(items=[nameless])))
    assert await planner.recall(BOSSMAN, "ship friday", correlation=correlation()) == []
    # Обратный контроль: та же запись С происхождением проходит.
    attributed = dict(nameless, provenance={"source": "docs/plan.md"})
    ok = ContextStorePlanner(BossmanNativeContextStore(db),
                             shadow(FakeTransport(items=[attributed])))
    found = await ok.recall(BOSSMAN, "ship friday", correlation=correlation())
    assert [c.record.key for c in found] == ["unattributed"]
    assert found[0].is_authoritative is False


async def test_the_authoritative_record_wins_a_duplicate_even_at_a_lower_version(db):
    """Устаревшее или подменённое зеркало не переписывает родную память."""
    stale_but_higher = {"project": BOSSMAN.project, "repository": BOSSMAN.repository,
                        "key": "preview-codec", "body": "preview renders mp4/H.264",
                        "version": 99, "provenance": {"source": "opencontext"}}
    planner = ContextStorePlanner(BossmanNativeContextStore(db),
                                  shadow(FakeTransport(items=[stale_but_higher])))
    await planner.remember(build_record(
        namespace=BOSSMAN, key="preview-codec", body="preview renders webm/VP9",
        version=2, source="docs/decisions.md"), correlation())
    found = await planner.recall(BOSSMAN, "preview codec", correlation=correlation())
    assert len(found) == 1, [c.record.body for c in found]
    assert found[0].is_authoritative is True
    assert "webm/VP9" in found[0].record.body


async def test_deletion_propagates_to_the_mirror(db):
    transport = FakeTransport()
    planner = ContextStorePlanner(BossmanNativeContextStore(db), shadow(transport))
    await planner.remember(build_record(
        namespace=BOSSMAN, key="preview-codec", body="webm", version=1,
        source="docs/decisions.md"), correlation())
    assert await planner.forget(BOSSMAN, "preview-codec", correlation()) is True
    assert transport.deletes == [{"namespace": BOSSMAN.key(), "key": "preview-codec"}]


async def test_the_context_budget_trims_the_tail_not_the_head(db):
    planner = ContextStorePlanner(BossmanNativeContextStore(db), budget_chars=400)
    for i in range(6):
        await planner.remember(build_record(
            namespace=BOSSMAN, key=f"decision-{i}", body="preview " + "x" * 200,
            version=1, source="docs/decisions.md"), correlation())
    found = await planner.recall(BOSSMAN, "preview", limit=10, correlation=correlation())
    assert 0 < len(found) < 6, [c.record.key for c in found]
    assert sum(len(c.record.body) for c in found[:-1]) < 400


async def test_one_record_larger_than_the_budget_is_still_answered(db):
    """Иначе длинное решение стало бы невидимым навсегда."""
    planner = ContextStorePlanner(BossmanNativeContextStore(db), budget_chars=50)
    await planner.remember(build_record(
        namespace=BOSSMAN, key="long", body="preview " + "y" * 3000, version=1,
        source="docs/decisions.md"), correlation())
    found = await planner.recall(BOSSMAN, "preview", correlation=correlation())
    assert [c.record.key for c in found] == ["long"]


async def test_a_record_that_is_only_logs_is_refused_by_size():
    with pytest.raises(ValueError, match="not raw logs"):
        build_record(namespace=BOSSMAN, key="k", body="z" * 9_000, version=1,
                     source="run.log")


def test_the_shadow_store_cannot_be_made_authoritative():
    """Авторитет зеркала — свойство кода, а не переменной окружения."""
    store = shadow(FakeTransport())
    identity = store.get_runtime_identity()
    assert identity.metadata["authoritative"] is False
    assert identity.metadata["mode"] == "shadow"
    assert "is_authoritative" not in OpenContextShadowStore.__init__.__code__.co_varnames
