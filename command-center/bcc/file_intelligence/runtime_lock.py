"""§10 — один владелец рантайма сайдкара. Очередь, а не гонка.

Upstream держит собственный AnalysisRuntimeLock, и запуск второго процесса
против того же состояния — это не «параллельная работа», а два хозяина у одной
файловой системы. Планировщик Bossman'а обязан не создавать такую ситуацию, а
не надеяться, что upstream её переживёт.

Восстановление после падения делается по правилам upstream: замок считается
брошенным, когда он с ЭТОГО хоста и процесса с таким pid больше нет
(AnalysisRuntimeLock.cpp:139 — `!same_host(hostname) || process_is_running(pid)`).
Замок с чужого хоста не снимается никогда: судить о живости чужого процесса
отсюда нечем. Слепое удаление замка «чтобы не мешал» здесь не делается — это
ровно тот способ получить два процесса на одном каталоге.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import Denied, Refusal

LOCK_FILENAME = "bossman-aifs-runtime.lock"


def _process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True                      # существует, но не наш — значит, жив
    except OSError:
        return True                      # неизвестно — считаем живым (fail-closed)
    return True


@dataclass
class LockInfo:
    host: str
    pid: int
    job_id: str
    started_at: float
    description: str = ""

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class RuntimeLock:
    """Файловый замок + внутрипроцессная очередь.

    Замок на диске переживает рестарт; asyncio-очередь не даёт одному процессу
    самому себе устроить гонку. Нужны оба: без файла рестарт забудет о занятости,
    без очереди две задачи в одном процессе будут драться за файл.
    """

    def __init__(self, directory: str | os.PathLike[str]):
        self.path = Path(directory) / LOCK_FILENAME
        self._local = asyncio.Lock()
        self._holder: str | None = None

    # ------------------------------------------------------------------ чтение

    def read(self) -> LockInfo | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        try:
            return LockInfo(host=str(data["host"]), pid=int(data["pid"]),
                            job_id=str(data.get("job_id") or ""),
                            started_at=float(data.get("started_at") or 0.0),
                            description=str(data.get("description") or ""))
        except (KeyError, TypeError, ValueError):
            return None

    def stale(self, info: LockInfo | None = None) -> bool:
        """Брошен ли замок — по правилам upstream, а не по возрасту.

        Возраст замка НЕ является признаком: медленный анализ большой папки
        законно держит рантайм долго, и «старше N минут — снимаем» отобрал бы
        его у работающего процесса.
        """
        info = info if info is not None else self.read()
        if info is None:
            return False
        if info.host != socket.gethostname():
            return False                 # чужой хост — не наше дело
        return not _process_is_running(info.pid)

    def describe(self) -> dict[str, Any]:
        info = self.read()
        if info is None:
            return {"held": False}
        return {"held": True, "stale": self.stale(info), **info.as_dict()}

    # ------------------------------------------------------------------ захват

    def _write(self, job_id: str, description: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(LockInfo(host=socket.gethostname(), pid=os.getpid(),
                                      job_id=job_id, started_at=time.time(),
                                      description=description).as_dict())
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, self.path)

    def try_acquire(self, job_id: str, description: str = "") -> bool:
        """Взять замок, если он свободен или брошен своим же мёртвым процессом."""
        info = self.read()
        if info is not None and not self.stale(info):
            return False
        self._write(job_id, description)
        # Перечитать: между проверкой и записью мог успеть другой процесс.
        confirmed = self.read()
        return confirmed is not None and confirmed.pid == os.getpid() and \
            confirmed.job_id == job_id

    def release(self, job_id: str) -> None:
        """Отпустить ТОЛЬКО свой замок.

        Условие на job_id/pid не формальность: без него завершающаяся работа
        снесла бы замок, который уже успела взять следующая.
        """
        info = self.read()
        if info is None:
            return
        if info.pid == os.getpid() and info.job_id == job_id:
            try:
                self.path.unlink()
            except OSError:
                pass

    def require_free(self) -> None:
        info = self.read()
        if info is not None and not self.stale(info):
            raise Denied(Refusal.BUSY,
                         "another File Intelligence job owns the sidecar runtime",
                         holder=info.as_dict())

    class _Guard:
        def __init__(self, lock: "RuntimeLock", job_id: str, description: str):
            self.lock, self.job_id, self.description = lock, job_id, description

        async def __aenter__(self) -> "RuntimeLock._Guard":
            await self.lock._local.acquire()
            if not self.lock.try_acquire(self.job_id, self.description):
                self.lock._local.release()
                info = self.lock.read()
                raise Denied(Refusal.BUSY,
                             "another File Intelligence job owns the sidecar runtime",
                             holder=info.as_dict() if info else {})
            self.lock._holder = self.job_id
            return self

        async def __aexit__(self, *exc: Any) -> None:
            self.lock.release(self.job_id)
            self.lock._holder = None
            self.lock._local.release()

    def hold(self, job_id: str, description: str = "") -> "RuntimeLock._Guard":
        """`async with lock.hold(job_id):` — единственный способ владеть рантаймом."""
        return RuntimeLock._Guard(self, job_id, description)
