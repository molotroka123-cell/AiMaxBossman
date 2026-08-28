"""Браузерный воркер: отдельный процесс, привязанный к одному аккаунту.

`09_INSTAGRAM_BROWSER_FALLBACK` начинается словами «Dedicated worker», и это не
про удобство развёртывания. Chromium в общем рантайме — это чужой код в адресном
пространстве, где живут работы всех аккаунтов сразу. Отдельный процесс даёт три
вещи, которых иначе не получить:

* падение или зависание браузера не уносит планировщик и очередь работ;
* у процесса свой каталог контекста, и других он не видит;
* **у процесса ровно один аккаунт**, а значит, кросс-аккаунтное действие
  невозможно даже при ошибке в вызывающем коде.

Последнее проверяется дважды, на обоих концах очереди:

1. `BrowserWorkerHandle.call` отвергает запрос с чужим аккаунтом, не отправляя;
2. цикл воркера отвергает его ещё раз при получении.

Двойная проверка не избыточна. Первая ловит ошибку вызывающего, вторая — всё
остальное: подложенный в очередь конверт, перепутанные ручки, ошибку в самой
первой проверке. Инвариант, который стоит публикации от чужого имени, не
охраняется одним `if`.

Секретов в конверте нет и быть не может: `guard_payload` отвергает полезную
нагрузку, в которой есть поле с «секретным» именем. Через границу процесса
ходит только ССЫЛКА на секрет, а разрешается она внутри воркера.
"""
from __future__ import annotations

import multiprocessing as mp
import queue as queue_module
from dataclasses import dataclass, field
from typing import Any

from .config import BrowserConfig
from .isolation import AccountContextRoot, CrossAccountViolation
from .secrets import looks_like_secret_name

STOP = "__stop__"
DEFAULT_TIMEOUT_SECONDS = 30.0


class SecretInTransit(ValueError):
    """В конверте для воркера нашлось значение, похожее на секрет.

    Не «подозрительно» — отказ. Секрет, единожды попавший в очередь между
    процессами, попадает и в её сериализацию, и в любой дамп памяти, и в трассу
    при отладке. Через границу ходит ссылка.
    """


@dataclass(frozen=True, slots=True)
class WorkerRequest:
    """Конверт к воркеру. Аккаунт указан явно и сверяется на обоих концах."""

    id: str
    account_id: str
    op: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_tuple(self) -> tuple[str, str, str, dict[str, Any]]:
        return self.id, self.account_id, self.op, dict(self.payload)

    @classmethod
    def from_tuple(cls, raw: tuple[Any, ...]) -> "WorkerRequest":
        ident, account_id, op, payload = raw
        return cls(id=str(ident), account_id=str(account_id), op=str(op),
                   payload=dict(payload or {}))


@dataclass(frozen=True, slots=True)
class WorkerResponse:
    id: str
    ok: bool
    payload: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    error_type: str = ""

    def to_tuple(self) -> tuple[str, bool, dict[str, Any], str, str]:
        return self.id, self.ok, dict(self.payload), self.error, self.error_type

    @classmethod
    def from_tuple(cls, raw: tuple[Any, ...]) -> "WorkerResponse":
        ident, ok, payload, error, error_type = raw
        return cls(id=str(ident), ok=bool(ok), payload=dict(payload or {}),
                   error=str(error or ""), error_type=str(error_type or ""))


def guard_account(bound_account_id: str, request: WorkerRequest) -> None:
    """Конверт обязан быть адресован тому аккаунту, которому привязан воркер."""
    if request.account_id != bound_account_id:
        raise CrossAccountViolation(
            request.account_id, bound_account_id,
            f"воркер обслуживает только {bound_account_id!r}; операция "
            f"{request.op!r} отвергнута до выполнения")


def guard_payload(payload: dict[str, Any]) -> None:
    """В нагрузке не должно быть полей, похожих на значение секрета."""
    for key, value in (payload or {}).items():
        if isinstance(key, str) and looks_like_secret_name(key):
            # Исключение ровно одно и по имени: ссылка — не значение.
            if key in {"secret_ref", "credential_ref", "vault_ref"}:
                continue
            if value in (None, "", [], {}):
                continue
            raise SecretInTransit(
                f"поле {key!r} не может пересекать границу процесса воркера: "
                f"через неё ходит ссылка на секрет, а не значение")
        if isinstance(value, dict):
            guard_payload(value)


def _worker_main(account_id: str, request_queue: Any, response_queue: Any,
                 config: dict[str, Any]) -> None:                # pragma: no cover
    """Цикл воркера. Живёт в отдельном процессе.

    Функция объявлена на уровне модуля, потому что при `spawn` она обязана быть
    импортируемой по имени. Ничего из состояния родителя она не наследует —
    и это тоже часть изоляции.
    """
    root = AccountContextRoot(root=config.get("context_root", "./browser-contexts"),
                              mode=int(config.get("context_dir_mode", 0o700)))
    context_dir = None
    while True:
        raw = request_queue.get()
        if raw == STOP:
            break
        try:
            request = WorkerRequest.from_tuple(raw)
        except Exception as exc:
            response_queue.put(WorkerResponse(
                id="", ok=False, error=f"неразобранный конверт: {exc}",
                error_type="ValueError").to_tuple())
            continue
        try:
            # Вторая проверка. Первая была у отправителя — и именно поэтому
            # эта не лишняя: она ловит то, что мимо отправителя прошло.
            guard_account(account_id, request)
            guard_payload(request.payload)
            if request.op == "ping":
                result: dict[str, Any] = {"pong": True, "account_id": account_id}
            elif request.op == "context":
                if context_dir is None:
                    context_dir = root.prepare(account_id)
                root.assert_owned(account_id, context_dir)
                root.assert_private(context_dir)
                result = {"context_dir": str(context_dir)}
            elif request.op == "known_accounts":
                # Диагностика: воркер видит только свой каталог как владелец.
                result = {"own": account_id, "context_root": str(root.root)}
            else:
                raise ValueError(f"воркер не умеет операцию {request.op!r}")
            response_queue.put(
                WorkerResponse(id=request.id, ok=True, payload=result).to_tuple())
        except Exception as exc:
            response_queue.put(WorkerResponse(
                id=request.id, ok=False, error=str(exc),
                error_type=type(exc).__name__).to_tuple())


@dataclass(slots=True)
class BrowserWorkerHandle:
    """Ручка к процессу воркера одного аккаунта.

    Ручка не умеет менять аккаунт. Это не пропущенный сеттер, а решение:
    объект, у которого аккаунт можно переставить, рано или поздно переставят.
    """

    account_id: str
    config: BrowserConfig = field(default_factory=BrowserConfig)
    _process: Any = None
    _requests: Any = None
    _responses: Any = None
    _counter: int = 0

    @property
    def alive(self) -> bool:
        return self._process is not None and self._process.is_alive()

    def start(self) -> "BrowserWorkerHandle":
        if self.alive:
            return self
        # `spawn`, а не `fork`: воркер не должен унаследовать ни открытых
        # соединений родителя, ни его памяти с секретами других аккаунтов.
        ctx = mp.get_context("spawn")
        self._requests = ctx.Queue()
        self._responses = ctx.Queue()
        self._process = ctx.Process(
            target=_worker_main,
            args=(self.account_id, self._requests, self._responses,
                  {"context_root": str(self.config.context_root),
                   "context_dir_mode": self.config.context_dir_mode}),
            daemon=True, name=f"social-farm-browser-{self.account_id}")
        self._process.start()
        return self

    def call(self, op: str, payload: dict[str, Any] | None = None, *,
             account_id: str = "", timeout: float = DEFAULT_TIMEOUT_SECONDS
             ) -> WorkerResponse:
        """Отправить операцию воркеру. Чужой аккаунт не уходит в очередь вовсе."""
        self._counter += 1
        request = WorkerRequest(id=f"r{self._counter}",
                                account_id=account_id or self.account_id, op=op,
                                payload=dict(payload or {}))
        guard_account(self.account_id, request)
        guard_payload(request.payload)
        if not self.alive:
            raise RuntimeError(f"воркер аккаунта {self.account_id} не запущен")
        self._requests.put(request.to_tuple())
        try:
            raw = self._responses.get(timeout=timeout)
        except queue_module.Empty as exc:
            raise TimeoutError(
                f"воркер аккаунта {self.account_id} не ответил за {timeout} с") from exc
        return WorkerResponse.from_tuple(raw)

    def send_raw(self, request: WorkerRequest, *,
                 timeout: float = DEFAULT_TIMEOUT_SECONDS) -> WorkerResponse:
        """Положить конверт в очередь МИМО проверки отправителя.

        Существует ради одного — теста, доказывающего, что вторая проверка,
        внутри воркера, действительно работает. В рабочем коде не используется.
        """
        if not self.alive:
            raise RuntimeError(f"воркер аккаунта {self.account_id} не запущен")
        self._requests.put(request.to_tuple())
        try:
            raw = self._responses.get(timeout=timeout)
        except queue_module.Empty as exc:
            raise TimeoutError(
                f"воркер аккаунта {self.account_id} не ответил за {timeout} с") from exc
        return WorkerResponse.from_tuple(raw)

    def stop(self, timeout: float = 5.0) -> None:
        if self._process is None:
            return
        try:
            if self._process.is_alive():
                self._requests.put(STOP)
                self._process.join(timeout)
        finally:
            if self._process.is_alive():                      # pragma: no cover
                self._process.terminate()
                self._process.join(timeout)
            self._process = None


@dataclass(slots=True)
class BrowserWorkerPool:
    """По воркеру на аккаунт. Общего воркера нет и не будет."""

    config: BrowserConfig = field(default_factory=BrowserConfig)
    workers: dict[str, BrowserWorkerHandle] = field(default_factory=dict)

    def worker_for(self, account_id: str) -> BrowserWorkerHandle:
        account_id = str(account_id).strip()
        if not account_id:
            raise ValueError("воркер не выдаётся без аккаунта")
        handle = self.workers.get(account_id)
        if handle is None or not handle.alive:
            handle = BrowserWorkerHandle(account_id=account_id,
                                         config=self.config).start()
            self.workers[account_id] = handle
        return handle

    def call(self, account_id: str, op: str,
             payload: dict[str, Any] | None = None, *,
             timeout: float = DEFAULT_TIMEOUT_SECONDS) -> WorkerResponse:
        return self.worker_for(account_id).call(op, payload, account_id=account_id,
                                                timeout=timeout)

    def shutdown(self) -> None:
        for handle in list(self.workers.values()):
            handle.stop()
        self.workers.clear()


__all__ = ["DEFAULT_TIMEOUT_SECONDS", "STOP", "BrowserWorkerHandle", "BrowserWorkerPool",
           "SecretInTransit", "WorkerRequest", "WorkerResponse", "guard_account",
           "guard_payload"]
