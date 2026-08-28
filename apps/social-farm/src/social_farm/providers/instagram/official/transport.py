"""Транспорт: единственное место, где адаптер касается сети.

Весь файл существует ради одного различия, которое нельзя потерять:

* **запрос не ушёл** — соединение не установилось, DNS не разрешился, прокси
  отказал. Внешнего эффекта точно не было, повтор безопасен;
* **ответа нет** — запрос записан в сокет, а дальше тишина: таймаут чтения,
  обрыв, протокол сломался. Эффект мог случиться. Повтор публикации здесь
  создаёт вторую публикацию, и откатом это не чинится.

Библиотеки исключений так не делят: `httpx.TimeoutException` покрывает оба
случая. Поэтому деление сделано здесь, в двух классах, и адаптер работает с
ними, а не с исключениями транспорта. Транспорт, который вернёт одно вместо
другого, — это транспорт, который однажды опубликует пост дважды.

Живой транспорт вдобавок отказывается работать на несверенном профиле: без
заполненных путей ему всё равно некуда идти, и лучше сказать это словами, чем
собрать запрос по догадке.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ....security.redaction import redact
from .profile import ProfileIncomplete, ProviderProfile


class TransportError(RuntimeError):
    """Базовая ошибка транспорта."""


class RequestNotSent(TransportError):
    """Запрос не ушёл. Внешнего эффекта не было — повтор безопасен."""


class ResponseUnknown(TransportError):
    """Запрос мог дойти, ответа нет. Повтор внешнего эффекта НЕ безопасен.

    Отдельный класс, а не флаг на общем исключении: флаг забывают проверить,
    класс приходится разобрать.
    """


class TransportUnavailable(TransportError):
    """Транспорта нет: не установлен httpx либо профиль не сверен."""


@dataclass(frozen=True, slots=True)
class GraphRequest:
    """Один вызов провайдера.

    `token` сюда НЕ кладётся: транспорт получает его отдельно и ставит в
    заголовок непосредственно перед отправкой. В журнале вызовов, в логе и в
    аудите остаётся этот объект — и в нём нечему протечь.
    """

    operation: str
    method: str = "GET"
    path: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    body: dict[str, Any] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    mutating: bool = False
    idempotency_key: str | None = None

    def to_log(self) -> dict[str, Any]:
        return redact({"operation": self.operation, "method": self.method,
                       "path": self.path, "params": self.params,
                       "mutating": self.mutating,
                       "idempotency_key": self.idempotency_key})


@dataclass(frozen=True, slots=True)
class GraphResponse:
    """Ответ провайдера в том виде, в каком его увидел транспорт."""

    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    body: dict[str, Any] = field(default_factory=dict)
    request_id: str | None = None

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def header(self, name: str) -> str | None:
        lowered = name.lower()
        for key, value in self.headers.items():
            if key.lower() == lowered:
                return value
        return None


@runtime_checkable
class GraphTransport(Protocol):
    async def call(self, request: GraphRequest, *,
                   access_token: str) -> GraphResponse: ...


class FixtureTransport:
    """Проигрыватель фикстур. Сети не касается вообще.

    Тесты складывают сюда либо ответ, либо исключение транспорта. Возможность
    положить именно `ResponseUnknown` — не удобство: без неё «обрыв после
    отправки публикации» невозможно воспроизвести, а значит невозможно и
    доказать, что он не приводит к повтору.
    """

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self._queues: dict[str, list[Any]] = {}
        self._default: dict[str, GraphResponse] = dict(responses or {})
        self.calls: list[GraphRequest] = []
        self.tokens_seen: list[str] = []

    def enqueue(self, operation: str, outcome: Any) -> "FixtureTransport":
        """Ответ или исключение на следующий вызов операции."""
        self._queues.setdefault(operation, []).append(outcome)
        return self

    def set_default(self, operation: str, response: GraphResponse) -> "FixtureTransport":
        self._default[operation] = response
        return self

    def call_count(self, operation: str) -> int:
        return sum(1 for c in self.calls if c.operation == operation)

    async def call(self, request: GraphRequest, *,
                   access_token: str) -> GraphResponse:
        self.calls.append(request)
        # Тесты проверяют, что токен доходит до транспорта и НЕ доходит никуда
        # больше. Отпечаток, а не значение: журнал вызовов читают глазами.
        self.tokens_seen.append(access_token[:2] + "…" if access_token else "")
        queue = self._queues.get(request.operation) or []
        outcome = queue.pop(0) if queue else self._default.get(request.operation)
        if outcome is None:
            raise TransportUnavailable(
                f"фикстура для операции {request.operation} не задана: "
                f"тест обязан сказать, что отвечает провайдер")
        if isinstance(outcome, BaseException):
            raise outcome
        if callable(outcome):
            return outcome(request)
        return outcome


class HttpxTransport:
    """Живой транспорт. Включается только на сверенном профиле.

    Отказ на несверенном профиле — не перестраховка. Путь, собранный из
    незаполненного шаблона, ушёл бы в никуда или, хуже, куда-нибудь ушёл бы.
    """

    def __init__(self, profile: ProviderProfile, *, timeout: float = 20.0,
                 client: Any = None) -> None:
        if not profile.allows_live_calls:
            raise TransportUnavailable(
                f"живые вызовы запрещены: {profile.unverified_reason()}")
        if not profile.base_url:
            raise ProfileIncomplete(
                "base_url не заполнен в профиле провайдера — идти некуда")
        self._profile = profile
        self._timeout = timeout
        self._client = client

    def _http(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import httpx
        except ImportError as exc:      # pragma: no cover — зависит от установки
            raise TransportUnavailable(
                "httpx не установлен: поставьте группу зависимостей "
                "`official` (pip install -e '.[official]')") from exc
        self._client = httpx.AsyncClient(base_url=self._profile.base_url,
                                         timeout=self._timeout)
        return self._client

    async def call(self, request: GraphRequest, *,
                   access_token: str) -> GraphResponse:
        try:                            # pragma: no cover — сеть в тестах не трогаем
            import httpx
        except ImportError as exc:      # pragma: no cover
            raise TransportUnavailable("httpx не установлен") from exc

        client = self._http()
        headers = dict(request.headers)
        headers["Authorization"] = f"Bearer {access_token}"
        if request.idempotency_key and self._profile.idempotency_header:
            headers[self._profile.idempotency_header] = request.idempotency_key

        try:                            # pragma: no cover — требует сети
            raw = await client.request(request.method, request.path,
                                       params=request.params or None,
                                       json=request.body or None, headers=headers)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ProxyError) as exc:
            # Соединения не было: запрос не ушёл, внешнего эффекта нет.
            raise RequestNotSent(f"{type(exc).__name__}: соединение не установлено") \
                from exc
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.ReadError,
                httpx.WriteError, httpx.RemoteProtocolError) as exc:
            # Запрос записан в сокет. Дошёл ли — неизвестно, и узнать это
            # можно только сверкой у провайдера.
            raise ResponseUnknown(
                f"{type(exc).__name__}: ответа нет после отправки запроса") from exc
        except httpx.HTTPError as exc:
            raise ResponseUnknown(f"{type(exc).__name__}: исход вызова неизвестен") \
                from exc

        try:                            # pragma: no cover
            body = raw.json()
        except ValueError:
            body = {"_non_json_body": True}
        return GraphResponse(status=raw.status_code, headers=dict(raw.headers),
                             body=body if isinstance(body, dict) else {"data": body},
                             request_id=raw.headers.get("x-fb-request-id")
                             or raw.headers.get("x-request-id"))

    async def aclose(self) -> None:     # pragma: no cover
        if self._client is not None and hasattr(self._client, "aclose"):
            await self._client.aclose()


__all__ = ["FixtureTransport", "GraphRequest", "GraphResponse", "GraphTransport",
           "HttpxTransport", "RequestNotSent", "ResponseUnknown", "TransportError",
           "TransportUnavailable"]
