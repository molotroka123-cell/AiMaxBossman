"""Durable state for browser-driven media generation jobs.

The store is deliberately provider-agnostic.  Site adapters may disappear,
restart, or require owner intervention; the durable record remains the source
of truth for Bossman orchestration and post-state evidence.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any, Iterable, Iterator
import errno
import json
import os
import tempfile
import time

# Сколько ждать чужой замок, прежде чем признать работу занятой. Секунды, а не
# минуты: замок держится на время чтения-записи одного маленького файла.
LOCK_WAIT_SECONDS = 5.0
LOCK_POLL_SECONDS = 0.02
# Замок, который старше этого, оставил умерший процесс. Ломается с записью в
# сам замок, чтобы это было видно, а не случилось молча.
STALE_LOCK_SECONDS = 60.0
# Сколько живёт аренда работы, если её не продлевают. Работник, который упал,
# не должен держать работу вечно; работник, который жив, продлевает аренду
# сам.
DEFAULT_LEASE_SECONDS = 900.0


class StoreLocked(RuntimeError):
    """Хранилище занято другим процессом дольше разумного."""


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    """Межпроцессный замок на файле хранилища.

    Замка мало кому хочется, но без него «прочитать-изменить-записать» на общем
    файле — это гонка, а гонка здесь означает две отправки одной работы у
    разных процессов. `O_EXCL` — единственная операция, атомарная и на POSIX, и
    на Windows, поэтому замок именно такой, а не через `flock`.
    """
    lock_path = path.with_name(path.name + ".lock")
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            handle = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                age = time.time() - lock_path.stat().st_mtime
            except OSError:
                continue                     # замок исчез между попытками — повторим
            if age > STALE_LOCK_SECONDS:
                # Процесс, взявший замок, не дожил до его снятия.
                try:
                    lock_path.unlink()
                except OSError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise StoreLocked(
                    f"хранилище работ {path.name} занято другим процессом дольше "
                    f"{LOCK_WAIT_SECONDS:.0f} с")
            time.sleep(LOCK_POLL_SECONDS)
            continue
        except OSError as exc:               # pragma: no cover - файловая система
            if exc.errno != errno.EEXIST:
                raise
            continue
        try:
            os.write(handle, f"{os.getpid()} {time.time():.3f}".encode("utf-8"))
        finally:
            os.close(handle)
        try:
            yield
        finally:
            try:
                lock_path.unlink()
            except OSError:                  # pragma: no cover - уже снят
                pass
        return

from .higgsfield_browser_contracts import BrowserGenerationState, TERMINAL_STATES


@dataclass(slots=True)
class GenerationJobRecord:
    job_id: str
    mission_id: str
    provider: str
    state: BrowserGenerationState
    attempt: int = 0
    created_at_epoch_s: float = 0.0
    updated_at_epoch_s: float = 0.0
    provider_job_id: str | None = None
    output_path: str | None = None
    artifact_sha256: str | None = None
    safe_message: str = ""
    last_error_class: str | None = None
    owner_action_required: bool = False
    # Свидетельство отправки живёт в записи, а не в памяти адаптера. Память
    # адаптера кончается вместе с процессом — а работа у провайдера остаётся,
    # и перезапуск, не знающий о ней, отправил бы её второй раз.
    submitted_at_epoch_s: float | None = None
    submission_evidence: dict[str, str] = field(default_factory=dict)
    provider_job_ref: str = ""
    # Аренда: кто сейчас ведёт работу и до какого момента.
    lease_owner: str = ""
    lease_expires_at_epoch_s: float = 0.0

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def submitted(self) -> bool:
        """Работа уже ушла провайдеру. Повторная отправка запрещена."""
        return self.submitted_at_epoch_s is not None

    def leased_by_another(self, owner: str, *, now: float | None = None) -> bool:
        moment = time.time() if now is None else now
        if not self.lease_owner or self.lease_owner == owner:
            return False
        return self.lease_expires_at_epoch_s > moment

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["state"] = self.state.value
        return payload

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "GenerationJobRecord":
        data = dict(payload)
        data["state"] = BrowserGenerationState(data["state"])
        return cls(**data)


class GenerationJobStore:
    """Tiny crash-safe JSON store using atomic replace.

    This is intentionally not a queue implementation.  Scheduling lives in the
    orchestrator; this class only preserves observable state across restarts.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def _read_all(self) -> dict[str, GenerationJobRecord]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"generation job store is unreadable: {self.path}") from exc
        if not isinstance(raw, dict) or raw.get("version") != 1:
            raise RuntimeError("unsupported generation job store format")
        jobs = raw.get("jobs", {})
        if not isinstance(jobs, dict):
            raise RuntimeError("generation job store jobs must be an object")
        return {job_id: GenerationJobRecord.from_json(data) for job_id, data in jobs.items()}

    def _write_all(self, jobs: dict[str, GenerationJobRecord]) -> None:
        payload = {
            "version": 1,
            "updated_at_epoch_s": time.time(),
            "jobs": {job_id: record.to_json() for job_id, record in jobs.items()},
        }
        fd, temp_name = tempfile.mkstemp(prefix=self.path.name + ".", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            try:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            except OSError:
                pass

    def create(self, *, job_id: str, mission_id: str, provider: str) -> GenerationJobRecord:
        now = time.time()
        with self._lock, _file_lock(self.path):
            jobs = self._read_all()
            if job_id in jobs:
                return jobs[job_id]
            record = GenerationJobRecord(
                job_id=job_id,
                mission_id=mission_id,
                provider=provider,
                state=BrowserGenerationState.CREATED,
                created_at_epoch_s=now,
                updated_at_epoch_s=now,
            )
            jobs[job_id] = record
            self._write_all(jobs)
            return record

    def get(self, job_id: str) -> GenerationJobRecord | None:
        with self._lock:
            return self._read_all().get(job_id)

    def save(self, record: GenerationJobRecord) -> GenerationJobRecord:
        record.updated_at_epoch_s = time.time()
        with self._lock, _file_lock(self.path):
            jobs = self._read_all()
            jobs[record.job_id] = record
            self._write_all(jobs)
        return record

    # ---- аренда: одна работа ведётся одним работником

    def claim(self, job_id: str, *, owner: str,
              ttl_s: float = DEFAULT_LEASE_SECONDS) -> GenerationJobRecord | None:
        """Взять работу в ведение. `None`, если её уже ведёт кто-то другой.

        Аренда, а не флаг «занято»: работник может упасть, и работа обязана
        освободиться сама. Просроченную аренду перехватывает следующий
        работник — но именно перехватывает, с записью в поле, а не игнорирует.
        """
        if not str(owner).strip():
            raise ValueError("аренда без владельца не берётся")
        with self._lock, _file_lock(self.path):
            jobs = self._read_all()
            record = jobs.get(job_id)
            if record is None:
                return None
            if record.leased_by_another(owner):
                return None
            record.lease_owner = owner
            record.lease_expires_at_epoch_s = time.time() + float(ttl_s)
            record.updated_at_epoch_s = time.time()
            jobs[job_id] = record
            self._write_all(jobs)
            return record

    def renew(self, job_id: str, *, owner: str,
              ttl_s: float = DEFAULT_LEASE_SECONDS) -> bool:
        with self._lock, _file_lock(self.path):
            jobs = self._read_all()
            record = jobs.get(job_id)
            if record is None or record.lease_owner != owner:
                return False
            record.lease_expires_at_epoch_s = time.time() + float(ttl_s)
            record.updated_at_epoch_s = time.time()
            self._write_all(jobs)
            return True

    def release(self, job_id: str, *, owner: str) -> bool:
        with self._lock, _file_lock(self.path):
            jobs = self._read_all()
            record = jobs.get(job_id)
            if record is None or record.lease_owner != owner:
                return False
            record.lease_owner = ""
            record.lease_expires_at_epoch_s = 0.0
            record.updated_at_epoch_s = time.time()
            self._write_all(jobs)
            return True

    # ---- свидетельство отправки

    def record_submission(self, job_id: str, *, submitted_at_epoch_s: float,
                          evidence: dict[str, str],
                          provider_job_ref: str = "") -> GenerationJobRecord:
        """Записать, что работа ушла провайдеру. Записывается ДО ожидания.

        Порядок важен: если процесс умрёт между нажатием и записью, перезапуск
        отправит работу второй раз. Между записью и ожиданием — не отправит,
        а просто продолжит ждать.
        """
        with self._lock, _file_lock(self.path):
            jobs = self._read_all()
            record = jobs.get(job_id)
            if record is None:
                raise KeyError(f"работы {job_id} нет в хранилище")
            if record.submitted:
                raise RuntimeError(
                    f"работа {job_id} уже отмечена отправленной "
                    f"({record.submitted_at_epoch_s}): вторая отметка означала бы "
                    f"вторую генерацию за счёт владельца")
            record.submitted_at_epoch_s = float(submitted_at_epoch_s)
            record.submission_evidence = dict(evidence)
            record.provider_job_ref = str(provider_job_ref or "")
            record.updated_at_epoch_s = time.time()
            self._write_all(jobs)
            return record

    def list(self, *, include_terminal: bool = True) -> list[GenerationJobRecord]:
        with self._lock:
            records = list(self._read_all().values())
        records.sort(key=lambda item: (item.created_at_epoch_s, item.job_id))
        if include_terminal:
            return records
        return [record for record in records if not record.terminal]

    def resumable(self) -> Iterable[GenerationJobRecord]:
        return self.list(include_terminal=False)


__all__ = ["DEFAULT_LEASE_SECONDS", "LOCK_WAIT_SECONDS", "STALE_LOCK_SECONDS",
           "GenerationJobRecord", "GenerationJobStore", "StoreLocked"]
