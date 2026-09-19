"""Работа переживает смерть процесса, не удваивается и продолжается с места.

Схема `social_jobs` и автомат `domain/jobs.py` у приложения были, а того, кто
ими пользуется, — не было: `grep social_jobs` по всему `src/` давал только
текст самой схемы, и ни один тест её не трогал. Здесь проверяется недостающая
половина, и проверяется НАСТОЯЩЕЙ базой, а не двойником: единственность ключа
идемпотентности обеспечивает индекс SQLite, и подменить его двойником значило
бы проверить собственную выдумку.

Перезапуск изображается ЧЕСТНО — новым соединением с тем же файлом. Хранилище
без перезапуска доказывает только работу словаря в памяти.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from social_farm.domain.jobs import (ExternalState, JobState, UnsafeRetry,
                                     on_restart, reconciliation_outcome)
from social_farm.jobs import JobStore, JobStoreError
from social_farm.storage.schema import open_database

ACCOUNT = "acc-vladelec"
T0 = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)


def _database(tmp_path):
    conn = open_database(tmp_path / "farm.sqlite3")
    conn.execute(
        "INSERT INTO social_accounts (id, provider, provider_account_id, created_at,"
        " updated_at) VALUES (?, 'fixture', 'acc-1', ?, ?)",
        (ACCOUNT, T0.isoformat(), T0.isoformat()))
    return conn


@pytest.fixture()
def store(tmp_path):
    return JobStore(_database(tmp_path))


def _enqueue(store, key="pub-1", **kw):
    return store.enqueue(account_id=ACCOUNT, capability="media.publish",
                         idempotency_key=key, now=T0, **kw)


# ------------------------------------------------------------------ расписка

def test_the_receipt_exists_before_anything_leaves(store):
    """Строка записана ДО внешней работы: попыток ноль, аренды нет."""
    job = _enqueue(store, payload={"текст": "привет"})
    assert job.state is JobState.QUEUED
    assert job.attempts == 0 and job.lease_owner == ""
    assert job.payload == {"текст": "привет"}
    assert store.get(job.id).id == job.id


def test_the_same_intent_never_becomes_two_jobs(store):
    """Тот же ключ на том же аккаунте — та же работа, а не вторая отправка."""
    first = _enqueue(store, "pub-1")
    second = _enqueue(store, "pub-1", payload={"текст": "другой"})
    assert second.id == first.id
    assert len(store.by_account(ACCOUNT)) == 1
    # И полезная нагрузка первой записи не переписана вторым вызовом:
    # «то же намерение» означает «уже принято», а не «принято заново».
    assert second.payload == first.payload


def test_a_different_key_is_a_different_job(store):
    """Обратная сторона: запрет не тотальный, другая работа заводится."""
    assert _enqueue(store, "pub-1").id != _enqueue(store, "pub-2").id
    assert len(store.by_account(ACCOUNT)) == 2


def test_the_uniqueness_is_enforced_by_the_database_not_by_us(store):
    """Прямая вставка мимо хранилища тоже отвергается — значит, решает индекс."""
    import sqlite3
    _enqueue(store, "pub-1")
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(
            "INSERT INTO social_jobs (id, account_id, capability, state,"
            " idempotency_key, created_at, updated_at)"
            " VALUES ('другой-id', ?, 'media.publish', 'QUEUED', 'pub-1', ?, ?)",
            (ACCOUNT, T0.isoformat(), T0.isoformat()))


# -------------------------------------------------------------------- аренда

def test_a_live_lease_hides_the_job_from_everyone_else(store):
    _enqueue(store)
    mine = store.acquire(worker="работник-A", now=T0)
    assert mine is not None and mine.lease_owner == "работник-A"
    assert mine.state is JobState.RUNNING and mine.attempts == 1
    assert store.acquire(worker="работник-B", now=T0 + timedelta(seconds=60)) is None


def test_an_expired_lease_is_reclaimed_but_not_handed_to_the_next_worker(store):
    """Имя важно: работа НЕ возвращается в очередь. Она идёт на сверку.

    Отдать её следующему работнику значило бы повторить действие, которое
    могло дойти до провайдера, — то есть опубликовать дважды.
    """
    _enqueue(store)
    store.acquire(worker="работник-A", lease_seconds=30, now=T0)
    late = T0 + timedelta(seconds=31)
    assert store.recover_stale(now=late), "истёкшая аренда не разобрана"
    assert store.acquire(worker="работник-B", now=late) is None


def test_only_the_holder_extends_the_lease(store):
    job = _enqueue(store)
    store.acquire(worker="работник-A", now=T0)
    store.heartbeat(job.id, worker="работник-A", now=T0 + timedelta(seconds=10))
    with pytest.raises(JobStoreError):
        store.heartbeat(job.id, worker="работник-B", now=T0 + timedelta(seconds=11))


def test_a_living_worker_keeps_its_job(store):
    """Продление — не формальность: без него работу отобрали бы у живого."""
    job = _enqueue(store)
    store.acquire(worker="работник-A", lease_seconds=30, now=T0)
    store.heartbeat(job.id, worker="работник-A", lease_seconds=30,
                    now=T0 + timedelta(seconds=20))
    assert store.recover_stale(now=T0 + timedelta(seconds=31)) == ()


def test_a_foreign_job_is_not_moved(store):
    job = _enqueue(store)
    store.acquire(worker="работник-A", now=T0)
    with pytest.raises(JobStoreError):
        store.checkpoint(job.id, "POLICY_EVALUATED", worker="работник-B", now=T0)


# --------------------------------------------------------------- продолжение

def test_work_resumes_from_the_reached_point_after_a_restart(store, tmp_path):
    """Возобновление идёт с пройденного этапа, а не с начала.

    Иначе «возобновить» означало бы «сделать всё заново», то есть заплатить
    дважды. Перезапуск здесь настоящий: новое соединение с тем же файлом.
    """
    job = _enqueue(store)
    store.acquire(worker="работник-A", now=T0)
    store.checkpoint(job.id, "POLICY_EVALUATED", worker="работник-A", now=T0)
    store.checkpoint(job.id, "APPROVAL_GRANTED", worker="работник-A", now=T0)
    store.conn.close()

    restarted = JobStore(open_database(tmp_path / "farm.sqlite3"))
    assert restarted.get(job.id).checkpoint == "APPROVAL_GRANTED"
    assert restarted.remaining_steps(job.id) == (
        "MEDIA_RENDERED", "PROVIDER_CONTAINER_CREATED", "PROVIDER_PUBLISHED",
        "RECONCILED")


def test_a_job_with_no_checkpoint_starts_from_the_beginning(store):
    job = _enqueue(store)
    assert store.remaining_steps(job.id)[0] == "POLICY_EVALUATED"


def test_an_unknown_step_is_refused_not_recorded(store):
    """Этап, которого нет в порядке, — опечатка, а не новый этап."""
    job = _enqueue(store)
    store.acquire(worker="работник-A", now=T0)
    with pytest.raises(JobStoreError):
        store.checkpoint(job.id, "ПРИДУМАННЫЙ_ЭТАП", worker="работник-A", now=T0)
    assert store.get(job.id).checkpoint == ""


# ------------------------------------------------------------------ перезапуск

def test_a_job_killed_mid_flight_goes_to_reconciliation_not_to_retry(store, tmp_path):
    """Самое дорогое свойство: после срыва — сверка, а не вторая отправка.

    Процесс мог умереть сразу ПОСЛЕ того, как провайдер принял запрос.
    Повторить вслепую — значит опубликовать дважды.
    """
    job = _enqueue(store)
    store.acquire(worker="работник-A", lease_seconds=30, now=T0)
    store.conn.close()

    restarted = JobStore(open_database(tmp_path / "farm.sqlite3"))
    recovered = restarted.recover_stale(now=T0 + timedelta(seconds=31))
    assert [r.state for r in recovered] == [JobState.RECONCILING]
    assert restarted.get(job.id).lease_owner == "", "аренда мертвеца снята"


def test_a_retry_with_an_unknown_external_state_is_refused(store):
    """Доказательством считается ABSENT, а не отсутствие доказательства обратного."""
    job = _enqueue(store)
    store.acquire(worker="работник-A", lease_seconds=30, now=T0)
    store.recover_stale(now=T0 + timedelta(seconds=31))
    store.conn.execute("UPDATE social_jobs SET external_state = 'UNKNOWN',"
                       " lease_owner = 'работник-B' WHERE id = ?", (job.id,))
    with pytest.raises(UnsafeRetry):
        store.transition(job.id, JobState.RUNNING, worker="работник-B", now=T0)


def test_a_transition_outside_the_automaton_is_refused(store):
    from social_farm.domain.jobs import TransitionError
    job = _enqueue(store)
    store.acquire(worker="работник-A", now=T0)
    with pytest.raises(TransitionError):
        store.transition(job.id, JobState.DRAFT, worker="работник-A", now=T0)
    assert store.get(job.id).state is JobState.RUNNING


def test_a_finished_job_records_when_it_finished(store):
    job = _enqueue(store)
    store.acquire(worker="работник-A", now=T0)
    done = store.transition(job.id, JobState.SUCCEEDED, worker="работник-A",
                            external_state=ExternalState.CONFIRMED, now=T0)
    assert done.terminal and done.external_state is ExternalState.CONFIRMED
    row = store.conn.execute("SELECT terminal_at FROM social_jobs WHERE id = ?",
                             (job.id,)).fetchone()
    assert row["terminal_at"], "время завершения не записано"


def test_a_dead_workers_job_is_never_handed_out_before_reconciliation(store):
    """Прямая проверка свойства, которое раньше держалось случайно.

    Мутация показала, что условие на истечение аренды в `acquire` было
    мёртвым: отбор по состоянию делал всю работу. Условие убрано, а СВОЙСТВО
    закреплено здесь явно — истёкшая аренда на `RUNNING` не делает работу
    доступной никому, пока сверка не сказала своё.
    """
    job = _enqueue(store)
    store.acquire(worker="работник-A", lease_seconds=30, now=T0)
    dead = T0 + timedelta(seconds=999)
    assert store.acquire(worker="работник-B", now=dead) is None, (
        "работа мертвеца ушла следующему работнику МИМО сверки")
    assert store.get(job.id).state is JobState.RUNNING
    store.recover_stale(now=dead)
    assert store.get(job.id).state is JobState.RECONCILING
    assert store.acquire(worker="работник-B", now=dead) is None, (
        "и после снятия аренды она всё ещё не в очереди — сначала сверка")


# --------------------------------------------------- BL-114/115/116/117: выход из сверки

def test_reconciliation_has_a_door_out_and_the_work_continues(store):
    """BL-114. Раньше это был тупик: пережить срыв и замереть навсегда.

    `recover_stale` переводил работу в `RECONCILING` и снимал аренду мертвеца,
    `acquire` отбирал только `QUEUED`, а `transition`/`checkpoint`/`heartbeat`
    требовали аренды, которую взять стало НЕГДЕ. Доказательство, что дыра была
    настоящей, а не теоретической: соседний тест этого же файла выдавал себе
    аренду прямым UPDATE мимо хранилища — потому что двери не было.
    """
    job = _enqueue(store)
    store.acquire(worker="работник-A", lease_seconds=30, now=T0)
    store.checkpoint(job.id, "PROVIDER_CONTAINER_CREATED", worker="работник-A", now=T0)
    dead = T0 + timedelta(seconds=999)
    store.recover_stale(now=dead)
    assert store.get(job.id).state is JobState.RECONCILING

    taken = store.claim_reconciliation(worker="сверщик", now=dead)
    assert taken is not None and taken.id == job.id
    # Сверка не меняет состояние и не считается новой попыткой внешнего эффекта.
    assert taken.state is JobState.RECONCILING and taken.attempts == 1
    assert taken.checkpoint == "PROVIDER_CONTAINER_CREATED", "точка продолжения потеряна"

    state, external = reconciliation_outcome(effect_found=True)
    done = store.transition(job.id, state, worker="сверщик",
                            external_state=external, now=dead)
    assert done.state is JobState.SUCCEEDED and done.terminal


def test_a_job_waiting_for_reconciliation_is_never_given_to_a_second_reconciler(store):
    _enqueue(store)
    store.acquire(worker="работник-A", lease_seconds=30, now=T0)
    dead = T0 + timedelta(seconds=999)
    store.recover_stale(now=dead)
    assert store.claim_reconciliation(worker="сверщик-1", now=dead) is not None
    assert store.claim_reconciliation(worker="сверщик-2", now=dead) is None


def test_a_queued_job_is_not_mistaken_for_one_awaiting_reconciliation(store):
    """Отрицательный контроль к двери: она открывается не в очередь."""
    job = _enqueue(store)
    assert store.claim_reconciliation(worker="сверщик", now=T0) is None
    assert store.get(job.id).state is JobState.QUEUED and store.get(job.id).lease_owner == ""


def test_an_empty_worker_name_moves_nothing(store):
    """BL-115. Дыра была ровно размером с пустую строку.

    `lease_owner` незанятой работы читается как `""`, и проверка аренды
    пропускала любого, кто назвался пустым именем: он двигал, отмечал этапами
    и объявлял ЗАВЕРШЁННОЙ работу, которой не держал.
    """
    job = _enqueue(store)
    for empty in ("", "   "):
        with pytest.raises(JobStoreError, match="без имени"):
            store.transition(job.id, JobState.CANCELLED, worker=empty, now=T0)
        with pytest.raises(JobStoreError, match="без имени"):
            store.checkpoint(job.id, "POLICY_EVALUATED", worker=empty, now=T0)
        with pytest.raises(JobStoreError, match="без имени"):
            store.acquire(worker=empty, now=T0)
        with pytest.raises(JobStoreError, match="без имени"):
            store.claim_reconciliation(worker=empty, now=T0)
    assert store.get(job.id).state is JobState.QUEUED
    assert store.get(job.id).checkpoint == ""
    # Положительная половина пары: настоящему арендатору ничего не мешает.
    store.acquire(worker="работник-A", now=T0)
    assert store.checkpoint(job.id, "POLICY_EVALUATED",
                            worker="работник-A", now=T0).checkpoint == "POLICY_EVALUATED"


def test_an_unleased_job_is_not_moved_by_anyone(store):
    """Та же дыра с другой стороны: «никем не арендована» — это отказ."""
    job = _enqueue(store)
    with pytest.raises(JobStoreError, match="никем не арендована"):
        store.transition(job.id, JobState.CANCELLED, worker="работник-A", now=T0)
    assert store.get(job.id).state is JobState.QUEUED


def test_returning_a_job_in_flight_to_the_queue_is_refused_by_name(store):
    """BL-116. Метод обещал возврат в очередь, автомат его не разрешал.

    Вызывающий получал `TransitionError` про рёбра автомата. Ребро не
    добавлено — обещание подогнано под правило, а не правило под обещание.
    """
    job = _enqueue(store)
    store.acquire(worker="работник-A", now=T0)
    with pytest.raises(JobStoreError, match="сверка"):
        store.release(job.id, worker="работник-A", now=T0)
    assert store.get(job.id).state is JobState.RUNNING
    assert store.get(job.id).lease_owner == "работник-A", "аренда снята в отказе"


def test_a_process_that_died_waiting_for_the_provider_is_recovered_too(store, tmp_path):
    """BL-117. Самое вероятное место смерти — ожидание сети, а не работа.

    Раньше `WAITING_PROVIDER` перезапуск не разбирал ВООБЩЕ: работа оставалась
    с арендой мертвеца навсегда — её не брал ни `acquire` (не `QUEUED`), ни
    сверщик (не `RECONCILING`), а двигать её мог только исчезнувший работник.
    """
    assert on_restart(JobState.WAITING_PROVIDER) is JobState.RECONCILING
    job = _enqueue(store)
    store.acquire(worker="работник-A", lease_seconds=30, now=T0)
    store.transition(job.id, JobState.WAITING_PROVIDER, worker="работник-A",
                     external_state=ExternalState.UNKNOWN, now=T0)
    store.conn.close()

    restarted = JobStore(open_database(tmp_path / "farm.sqlite3"))
    dead = T0 + timedelta(seconds=999)
    assert [r.state for r in restarted.recover_stale(now=dead)] == [JobState.RECONCILING]
    assert restarted.get(job.id).lease_owner == ""
    assert restarted.acquire(worker="работник-B", now=dead) is None, (
        "ожидавшая провайдера работа ушла следующему работнику МИМО сверки")
    assert restarted.claim_reconciliation(worker="сверщик", now=dead) is not None


def test_a_reconciler_that_died_does_not_freeze_the_job_forever(store):
    """BL-117, вторая половина: аренду мертвеца снимает ЛЮБОЕ состояние.

    Раньше стояло `continue`: состояние не меняется — аренда не снимается.
    Умерший посреди сверки работник запирал работу навсегда.
    """
    _enqueue(store)
    store.acquire(worker="работник-A", lease_seconds=30, now=T0)
    dead = T0 + timedelta(seconds=999)
    store.recover_stale(now=dead)
    first = store.claim_reconciliation(worker="сверщик-1", lease_seconds=30, now=dead)
    assert first is not None

    later = dead + timedelta(seconds=999)
    recovered = store.recover_stale(now=later)
    assert [r.state for r in recovered] == [JobState.RECONCILING], "состояние не тронуто"
    assert recovered[0].lease_owner == "", "аренда мёртвого сверщика не снята"
    assert store.claim_reconciliation(worker="сверщик-2", now=later) is not None
    assert store.acquire(worker="работник-B", now=later) is None, (
        "снятие аренды не должно возвращать работу в очередь"
    )


def test_a_living_reconciler_is_not_robbed(store):
    """Отрицательный контроль к снятию аренды: живого не трогают."""
    job = _enqueue(store)
    store.acquire(worker="работник-A", lease_seconds=30, now=T0)
    dead = T0 + timedelta(seconds=999)
    store.recover_stale(now=dead)
    store.claim_reconciliation(worker="сверщик-1", lease_seconds=30, now=dead)
    store.heartbeat(job.id, worker="сверщик-1", lease_seconds=30,
                    now=dead + timedelta(seconds=20))
    assert store.recover_stale(now=dead + timedelta(seconds=31)) == ()
    assert store.get(job.id).lease_owner == "сверщик-1"
    assert store.claim_reconciliation(worker="сверщик-2",
                                      now=dead + timedelta(seconds=31)) is None
