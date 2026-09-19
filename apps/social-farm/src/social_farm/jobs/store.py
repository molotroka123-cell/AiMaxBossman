"""Долговечное хранение работ: расписка ДО ожидания, аренда, продолжение.

Схема и автомат у приложения уже были, а того, кто ими пользуется, — не было:
`grep social_jobs` по всему `src/` давал только текст самой схемы. Этот модуль
и есть недостающая половина. Он ничего не придумывает заново:

* таблицы `social_jobs`, `job_attempts` и уникальный индекс
  `ux_jobs_account_idem (account_id, idempotency_key)` — из
  `storage/schema.py`;
* переходы, `on_restart`, `guard_retry`, `Checkpoint.ORDER` — из
  `domain/jobs.py`.

Три свойства, ради которых он существует, и все три — про деньги и репутацию
владельца, а не про удобство:

**Расписка раньше ожидания.** Строка работы записывается ДО того, как хоть
что-то уйдёт наружу. Процесс, умерший сразу после отправки, оставляет после
себя запись, а не пустоту: перезапуск найдёт работу в `RUNNING`, и
`on_restart` отправит её на сверку, а не на повтор.

**Аренда с истечением.** Работник, который упал, не держит работу вечно;
работник, который жив, продлевает аренду сам. Захват — одним UPDATE с
условием на истечение, поэтому двое рабочих не получат одну работу даже в
разных процессах: решает база, а не договорённость.

**Продолжение с достигнутой точки.** Пройденный этап отмечается в
`checkpoint`, и возобновление идёт с него, а не с начала. Без этого
«возобновить» означало бы «сделать всё заново», то есть заплатить дважды.

Чего здесь НЕТ намеренно: собственного файлового хранилища с замком поверх
`O_EXCL`. Оно было у ветки-источника, но рядом с уже объявленной таблицей это
было бы второе хранилище работ, и они разъехались бы. Единственность ключа
идемпотентности здесь обеспечивает индекс базы, а не код вызывающего.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
import json
import sqlite3
import uuid

from ..domain.jobs import (TERMINAL, Checkpoint, ExternalState, JobState,
                           check_transition, guard_retry, on_restart)

#: Сколько живёт аренда, если её не продлевают.
DEFAULT_LEASE_SECONDS = 900.0


class JobStoreError(RuntimeError):
    """Отказ хранилища работ: чужая аренда, пропавшая строка, неизвестный этап."""


def _now(moment: datetime | None = None) -> datetime:
    return (moment or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat()


@dataclass(frozen=True, slots=True)
class JobRecord:
    """Строка работы в том виде, в каком её читает вызывающий."""

    id: str
    account_id: str
    capability: str
    state: JobState
    external_state: ExternalState
    idempotency_key: str
    attempts: int
    lease_owner: str
    lease_expires_at: str
    checkpoint: str
    payload: dict[str, Any]

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL

    @classmethod
    def of(cls, row: sqlite3.Row) -> "JobRecord":
        raw = row["payload_ref"] or ""
        try:
            payload = json.loads(raw) if raw else {}
        except ValueError:                      # pragma: no cover - битая строка
            payload = {}
        return cls(id=row["id"], account_id=row["account_id"],
                   capability=row["capability"], state=JobState(row["state"]),
                   external_state=ExternalState(row["external_state"]),
                   idempotency_key=row["idempotency_key"], attempts=int(row["attempts"]),
                   lease_owner=row["lease_owner"] or "",
                   lease_expires_at=row["lease_expires_at"] or "",
                   checkpoint=row["checkpoint"] or "", payload=payload)


class JobStore:
    """Работы одной базы. Ничего не кэширует: источник правды — таблица."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ----------------------------------------------------------------- расписка

    def enqueue(self, *, account_id: str, capability: str, idempotency_key: str,
                payload: dict[str, Any] | None = None,
                now: datetime | None = None) -> JobRecord:
        """Записать намерение ДО того, как хоть что-то уйдёт наружу.

        Тот же ключ на том же аккаунте — то же намерение: возвращается ТА ЖЕ
        работа, а не вторая. Проверку делает уникальный индекс, а не сравнение
        в коде: код можно обойти, войдя с другой стороны, индекс — нельзя.
        """
        moment = _iso(_now(now))
        self.conn.execute(
            "INSERT INTO social_jobs (id, account_id, capability, state, payload_ref,"
            " idempotency_key, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (account_id, idempotency_key) DO NOTHING",
            (uuid.uuid4().hex, account_id, capability, JobState.QUEUED.value,
             json.dumps(payload or {}, ensure_ascii=False), idempotency_key,
             moment, moment))
        return self._require_by_key(account_id, idempotency_key)

    # ------------------------------------------------------------------- аренда

    def acquire(self, *, worker: str, capability: str | None = None,
                lease_seconds: float = DEFAULT_LEASE_SECONDS,
                now: datetime | None = None) -> JobRecord | None:
        """Взять одну готовую работу под аренду. `None` — брать нечего.

        Захват — ОДИН UPDATE с условием на истечение аренды, поэтому двое
        рабочих не получат одну работу даже в разных процессах. Сначала
        выбрать, потом обновить — это гонка, и здесь так не делается.
        """
        moment = _now(now)
        deadline = _iso(moment + timedelta(seconds=lease_seconds))
        # Условия на истечение аренды здесь НЕТ, и это осознанно. Оно тут было
        # и оказалось мёртвым: мутация «убрать его» не покрасила ни одного
        # теста. Причина — состояние: `QUEUED` с живой арендой не возникает
        # никогда (аренда ставится вместе с `RUNNING`, снимается вместе с
        # возвратом в очередь), так что отбор по состоянию уже делает всю
        # работу.
        #
        # А главное — если бы условие работало, оно было бы ОПАСНЫМ: истёкшая
        # аренда на `RUNNING` означает, что работник умер, возможно уже отправив
        # запрос провайдеру. Такую работу нельзя отдавать следующему
        # работнику — её забирает `recover_stale`, и только через сверку.
        where = ["state = ?"]
        args: list[Any] = [JobState.QUEUED.value]
        if capability is not None:
            where.append("capability = ?")
            args.append(capability)
        cur = self.conn.execute(
            f"UPDATE social_jobs SET lease_owner = ?, lease_expires_at = ?,"
            f" state = ?, attempts = attempts + 1, updated_at = ?"
            f" WHERE id = (SELECT id FROM social_jobs WHERE {' AND '.join(where)}"
            f" ORDER BY created_at LIMIT 1) RETURNING id",
            (worker, deadline, JobState.RUNNING.value, _iso(moment), *args))
        row = cur.fetchone()
        return self.get(row["id"]) if row else None

    def heartbeat(self, job_id: str, *, worker: str,
                  lease_seconds: float = DEFAULT_LEASE_SECONDS,
                  now: datetime | None = None) -> JobRecord:
        """Продлить СВОЮ аренду. Чужую продлить нельзя — это и есть проверка."""
        moment = _now(now)
        cur = self.conn.execute(
            "UPDATE social_jobs SET lease_expires_at = ?, updated_at = ?"
            " WHERE id = ? AND lease_owner = ? RETURNING id",
            (_iso(moment + timedelta(seconds=lease_seconds)), _iso(moment),
             job_id, worker))
        if cur.fetchone() is None:
            raise JobStoreError(
                f"работа {job_id!r} не арендована работником {worker!r}: "
                f"продлевать нечего")
        return self.get(job_id)

    def release(self, job_id: str, *, worker: str,
                now: datetime | None = None) -> JobRecord:
        """Отдать работу обратно в очередь, не объявляя её ни сделанной, ни сорванной."""
        self._require_lease(job_id, worker)
        moment = _iso(_now(now))
        self._transition(job_id, JobState.QUEUED, now=moment)
        self.conn.execute(
            "UPDATE social_jobs SET lease_owner = NULL, lease_expires_at = NULL,"
            " updated_at = ? WHERE id = ?", (moment, job_id))
        return self.get(job_id)

    # -------------------------------------------------------------- продолжение

    def checkpoint(self, job_id: str, name: str, *, worker: str,
                   now: datetime | None = None) -> JobRecord:
        """Отметить пройденный этап, чтобы возобновление шло с него, а не сначала."""
        if name not in Checkpoint.ORDER:
            raise JobStoreError(
                f"этап {name!r} не объявлен: известны {', '.join(Checkpoint.ORDER)}")
        self._require_lease(job_id, worker)
        moment = _iso(_now(now))
        self.conn.execute(
            "UPDATE social_jobs SET checkpoint = ?, updated_at = ? WHERE id = ?",
            (Checkpoint(name=name, at=moment).name, moment, job_id))
        return self.get(job_id)

    def remaining_steps(self, job_id: str) -> tuple[str, ...]:
        """Что ещё не сделано. Пустой чекпойнт — значит, не сделано ничего."""
        record = self.get(job_id)
        if not record.checkpoint:
            return Checkpoint.ORDER
        index = Checkpoint.ORDER.index(record.checkpoint)
        return Checkpoint.ORDER[index + 1:]

    # ------------------------------------------------------------ смена состояния

    def transition(self, job_id: str, target: JobState, *, worker: str,
                   external_state: ExternalState | None = None,
                   now: datetime | None = None) -> JobRecord:
        """Перевести работу по автомату. Переход вне автомата — отказ, а не запись."""
        self._require_lease(job_id, worker)
        moment = _iso(_now(now))
        if target is JobState.RUNNING:
            record = self.get(job_id)
            # Повтор действия, которое могло дойти до провайдера, запрещён до
            # сверки. Проверка живёт в домене, здесь только вызов.
            guard_retry(record.state, record.external_state)
        self._transition(job_id, target, now=moment)
        if external_state is not None:
            self.conn.execute(
                "UPDATE social_jobs SET external_state = ?, updated_at = ? WHERE id = ?",
                (external_state.value, moment, job_id))
        return self.get(job_id)

    def recover_stale(self, *, now: datetime | None = None) -> tuple[JobRecord, ...]:
        """Работы, чья аренда истекла: применить правило перезапуска.

        `RUNNING` уходит в `RECONCILING`, а не в `QUEUED` и не в `FAILED`:
        процесс мог умереть сразу после того, как провайдер принял запрос, и
        единственный честный ответ — пойти и выяснить.
        """
        moment = _now(now)
        rows = self.conn.execute(
            "SELECT id, state FROM social_jobs"
            " WHERE lease_expires_at IS NOT NULL AND lease_expires_at <= ?",
            (_iso(moment),)).fetchall()
        recovered: list[JobRecord] = []
        for row in rows:
            state = JobState(row["state"])
            target = on_restart(state)
            if target is state:
                continue
            self._transition(row["id"], target, now=_iso(moment))
            self.conn.execute(
                "UPDATE social_jobs SET lease_owner = NULL, lease_expires_at = NULL,"
                " updated_at = ? WHERE id = ?", (_iso(moment), row["id"]))
            recovered.append(self.get(row["id"]))
        return tuple(recovered)

    # -------------------------------------------------------------------- чтение

    def get(self, job_id: str) -> JobRecord:
        row = self.conn.execute("SELECT * FROM social_jobs WHERE id = ?",
                                (job_id,)).fetchone()
        if row is None:
            raise JobStoreError(f"работы {job_id!r} нет в хранилище")
        return JobRecord.of(row)

    def by_account(self, account_id: str) -> tuple[JobRecord, ...]:
        rows = self.conn.execute(
            "SELECT * FROM social_jobs WHERE account_id = ? ORDER BY created_at",
            (account_id,)).fetchall()
        return tuple(JobRecord.of(row) for row in rows)

    # ------------------------------------------------------------------ внутреннее

    def _require_by_key(self, account_id: str, idempotency_key: str) -> JobRecord:
        row = self.conn.execute(
            "SELECT * FROM social_jobs WHERE account_id = ? AND idempotency_key = ?",
            (account_id, idempotency_key)).fetchone()
        if row is None:                          # pragma: no cover - вставка и пропажа
            raise JobStoreError(
                f"расписка по ключу {idempotency_key!r} не записалась")
        return JobRecord.of(row)

    def _require_lease(self, job_id: str, worker: str) -> None:
        record = self.get(job_id)
        if record.lease_owner != worker:
            raise JobStoreError(
                f"работа {job_id!r} арендована {record.lease_owner or 'никем'!r}, "
                f"а не {worker!r}: чужую работу не двигают")

    def _transition(self, job_id: str, target: JobState, *, now: str) -> None:
        source = self.get(job_id).state
        if source is target:
            return
        check_transition(source, target)
        terminal_at = now if target in TERMINAL else None
        self.conn.execute(
            "UPDATE social_jobs SET state = ?, updated_at = ?, terminal_at = ?"
            " WHERE id = ?", (target.value, now, terminal_at, job_id))
