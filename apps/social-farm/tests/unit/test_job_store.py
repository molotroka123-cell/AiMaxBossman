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

from social_farm.domain.jobs import ExternalState, JobState, UnsafeRetry
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
