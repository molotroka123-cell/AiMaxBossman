"""Мост BOSSMAN → OpenClaw Gateway, контракт V1.

Основание — `docs/research/openclaw.md`: репозиторий склонирован, пакет из npm
поставлен, Gateway реально запущен, протокол опрошен сырым WebSocket-клиентом.
Вердикт исследования: ВНЕДРЯТЬ ПОЗЖЕ, **вариант B — channel gateway only**.
BOSSMAN остаётся control plane, OpenClaw — движок каналов связи.

Три условия из §13 исследования вшиты в код, а не оставлены в документе:

1. **ASK на любую отправку в канал.** Не «ASK для незнакомых контактов», а ASK
   на любую отправку, пока владелец явно не разрешит конкретный канал и
   конкретный контакт. OpenClaw впервые даёт BOSSMAN возможность писать живым
   людям, а `node.invoke` добавляет к этому `sms.send`. Ночная миссия,
   написавшая клиенту, не чинится откатом.
2. **`idempotencyKey` с первого вызова.** Для всего, что видит человек, ключ
   выводится из `mission_id + run_id + payload` — содержимого отправки, а не
   из `call_id`: `call_id` выдаёт провайдер модели, и наш собственный повтор
   после сбоя получает НОВЫЙ id (подробности — в `idempotency_key`). У OpenClaw
   поле обязательное для методов с побочным эффектом (док протокола:
   «Side-effecting methods require idempotency keys»).
3. **Память.** У OpenClaw своя долговременная память: `memory-core` включён по
   умолчанию, LanceDB-плагин с auto-capture, `memory-wiki` с режимом `obsidian`
   и скиллом, который правит vault. Мост проверяет конфигурацию **на боевом
   пути `_open`** и отказывается работать, если их память смотрит в наше
   хранилище: два писателя в одни заметки — потеря данных, а не «расхождение».
   Не удалось прочитать их конфигурацию — тоже отказ (`OpenClawMemoryUnverified`):
   непроверенное условие не считается выполненным.

   Решение о том, кто вообще пишет в память, зафиксировано отдельно:
   `docs/architecture/MEMORY_SINGLE_WRITER.md`. Коротко: пишет только BOSSMAN.

Чего в V1 НЕТ намеренно: `node.invoke` (камера, экран, геолокация, SMS),
`exec.*`, `config.*`, `skills.proposals.*`, доступ к vault. Расширять
поверхность — отдельное решение владельца, а не побочный эффект интеграции.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

# Идентификатор клиента у OpenClaw — ЗАКРЫТЫЙ перечень
# (packages/gateway-protocol/src/client-info.ts). Своего id для стороннего
# control plane там нет, поэтому представляемся `gateway-client`.
CLIENT_ID = "gateway-client"
CLIENT_MODE = "backend"

# Минимальные права. Мост проверяет их у себя же после рукопожатия и
# отказывается работать, если ему выдали больше: лишние права, о которых
# никто не просил, — это не удобство, а расширение поверхности отказа.
REQUIRED_SCOPES = ("operator.read", "operator.write")
FORBIDDEN_SCOPES = ("operator.admin",)

# Методы контракта V1. Всё, чего здесь нет, мост не проксирует вообще.
V1_METHODS = {
    "health": "health",
    "agents": "agents.list",
    "sessions": "sessions.list",
    "session_create": "sessions.create",
    "session_describe": "sessions.describe",
    "send": "sessions.send",
    "abort": "sessions.abort",
    "channels": "channels.status",
    "nodes": "node.list",
    "node_describe": "node.describe",
}

# Методы, которые мост обязан отвергать, даже если кто-то попросит по имени.
NEVER_PROXY = {
    "node.invoke",        # камера, экран, геолокация, SMS на парном устройстве
    "exec.approval.resolve", "exec.approvals.set", "exec.approvals.node.set",
    "config.set", "config.patch",
    "skills.proposals.apply", "skills.install",
    "users.setRole", "update.apply",
}

CONNECT_TIMEOUT = 10.0
HEALTH_TIMEOUT = 5.0
SEND_TIMEOUT = 30.0
DEFAULT_TIMEOUT = 15.0
# Как часто перепроверять их конфигурацию памяти на живом соединении.
# Проверки только при подключении мало: конфигурацию могут поменять после него.
MEMORY_RECHECK_SECONDS = 300.0


class OpenClawUnavailable(RuntimeError):
    """Gateway недоступен, не отвечает или отверг подключение.

    `ambiguous=True` означает: запрос МОГ дойти, но ответа мы не получили.
    Это не отказ. Разница решающая — на отказе действие можно повторить, на
    неоднозначности повтор означает второе сообщение живому человеку.
    """

    def __init__(self, *args: object, ambiguous: bool = False) -> None:
        super().__init__(*args)
        self.ambiguous = ambiguous


class OpenClawScopeError(RuntimeError):
    """Права не те, что запрашивались. Работать с лишними правами не будем."""


class OpenClawMemoryConflict(RuntimeError):
    """Их память настроена на наше хранилище. Это условие 3 из §13."""


class OpenClawMemoryUnverified(OpenClawMemoryConflict):
    """Конфигурацию прочитать не удалось — значит гарантии нет.

    Раньше этот случай глотался и мост шёл дальше вслепую. Цена ошибки
    несимметрична: отказ подключиться владелец чинит за минуту, а перезатёртые
    заметки не чинятся ничем. Подвид `OpenClawMemoryConflict`, чтобы прежние
    обработчики продолжали ловить оба случая одним `except`.
    """


class OpenClawForbidden(RuntimeError):
    """Метод вне контракта V1."""


def idempotency_key(*, mission_id: Any, run_id: Any, call_id: Any = None,
                    payload: Any = None) -> str:
    """Ключ из НАШИХ идентификаторов, а не случайный.

    Случайный ключ не защищает ни от чего: при повторе он будет другим, и
    сообщение уйдёт дважды. Смысл ключа именно в том, что повтор одного и того
    же нашего действия даёт то же значение.

    ВАЖНО про `call_id`. Он приходит из ответа провайдера модели
    (`bcc/engine.py`: `call_id=str(call.id)`) и НЕ переживает наш собственный
    повтор. Сценарий: процесс умер после отправки, но до `_save_checkpoint`;
    `recover()` вернул run в очередь с прошлым checkpoint'ом; модель повторила
    вызов — и провайдер выдал новый `tool_calls[].id`. Ключ другой, OpenClaw
    видит новую операцию, человек получает второе сообщение.

    Поэтому для всего, что видит человек, ключ выводится из `payload` —
    содержимого отправки (канал, получатель, текст). `call_id` при этом
    игнорируется: он не различает повтор и новое действие, а `payload`
    различает. Осознанная плата: два намеренно одинаковых сообщения одному
    адресату в рамках одного run'а схлопнутся в одно. Для человека это лучше,
    чем дубль, и всегда исправимо новым run'ом.
    """
    if payload is not None:
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False,
                       default=str).encode()).hexdigest()
        raw = f"{mission_id or 'none'}:{run_id or 'none'}:payload:{digest}"
    else:
        raw = f"{mission_id or 'none'}:{run_id or 'none'}:{call_id or 'none'}"
    return "bossman-" + hashlib.sha256(raw.encode()).hexdigest()[:32]


def traceparent(*, run_id: Any) -> str:
    """W3C traceparent — сквозная трассировка BOSSMAN → OpenClaw без изобретений."""
    trace = hashlib.sha256(f"bossman-run-{run_id}".encode()).hexdigest()[:32]
    span = uuid.uuid4().hex[:16]
    return f"00-{trace}-{span}-01"


def _looks_like_path(value: str) -> bool:
    """Отсеять строки, которые путями не являются вовсе.

    Без этого `abspath("telegram")` превратил бы название канала в путь
    относительно текущего каталога — и мы ловили бы призраков.
    """
    value = value.strip()
    if not value:
        return False
    seps = [os.sep] + ([os.altsep] if os.altsep else [])
    return value.startswith("~") or any(sep in value for sep in seps)


def _path_forms(value: str) -> set[str]:
    """Все формы одного каталога, по которым имеет смысл сравнивать.

    Сравнение строк не видит, что это один и тот же каталог:
      * `~/Obsidian/BOSSMAN` и `/home/user/Obsidian/BOSSMAN`;
      * относительный путь и абсолютный;
      * `//двойные//слэши`, хвостовой слэш, `..` в середине;
      * символическая ссылка и её цель — разные строки, один inode.

    Регистр приводим к нижнему намеренно: на регистронезависимой ФС (macOS,
    Windows) `/Vault` и `/vault` — один каталог, а лишнее срабатывание здесь
    безопасно (мы откажемся подключаться), пропуск — нет.
    """
    raw = os.path.expandvars(os.path.expanduser(str(value).strip()))
    if not raw:
        return set()
    literal = os.path.normpath(os.path.abspath(raw))
    try:
        resolved = os.path.realpath(literal)      # снимает симлинки и монтирования
    except OSError:
        resolved = literal
    return {p.rstrip(os.sep).lower() or os.sep for p in (literal, resolved)}


def _same_or_nested(theirs: set[str], ours: set[str]) -> bool:
    """Один каталог, их внутри нашего или наш внутри их — всё это конфликт."""
    for t in theirs:
        for o in ours:
            if t == o or t.startswith(o + os.sep) or o.startswith(t + os.sep):
                return True
    return False


def memory_conflict(config: dict[str, Any], our_vault: str) -> str:
    """Смотрит ли их память в наше хранилище. Пустая строка — конфликта нет.

    Проверяем ПУТЬ, а не название режима: `memory-wiki` в режиме `obsidian`
    сам по себе не опасен, опасно совпадение каталога.

    Обходим конфигурацию ЦЕЛИКОМ, а не три знакомых ключа: путь до vault'а
    может лежать под `agents.*.workspace`, в списке плагинов или в ключе,
    которого в их схеме ещё не было, когда писался этот код. Списки тоже
    обходим — раньше `plugins: [{vault: ...}]` проходил мимо проверки.
    """
    if not our_vault:
        return ""
    ours = _path_forms(our_vault)
    if not ours:
        return ""
    suspects: list[tuple[str, str]] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}" if path else str(key))
        elif isinstance(node, (list, tuple)):
            for i, value in enumerate(node):
                walk(value, f"{path}[{i}]")
        elif isinstance(node, str) and _looks_like_path(node):
            if _same_or_nested(_path_forms(node), ours):
                suspects.append((path or "config", node))

    walk(config, "")
    if suspects:
        where = ", ".join(f"{p}={v}" for p, v in suspects[:3])
        return (f"память OpenClaw настроена на наше хранилище ({where}). "
                f"Два писателя в одни заметки — потеря данных, а не расхождение. "
                f"Разведите каталоги или выключите memory-wiki, затем повторите.")
    return ""


@dataclass
class OpenClawConfig:
    url: str = ""                       # ws://127.0.0.1:18789
    token: str = ""
    vault_root: str = ""                # наше хранилище — для проверки конфликта
    # Каналы и контакты, которым владелец ЯВНО разрешил автоматическую отправку.
    # Пусто = ASK на всё. Это дефолт и он намеренно неудобный.
    auto_send_allow: list[dict[str, str]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "OpenClawConfig":
        raw = dict(raw or {})
        allow = raw.get("auto_send_allow")
        pairs: list[dict[str, str]] = []
        if isinstance(allow, list):
            for item in allow:
                if isinstance(item, dict) and item.get("channel") and item.get("contact"):
                    pairs.append({"channel": str(item["channel"]).lower(),
                                  "contact": str(item["contact"])})
        return cls(url=str(raw.get("url") or ""), token=str(raw.get("token") or ""),
                   vault_root=str(raw.get("vault_root") or ""), auto_send_allow=pairs)

    @property
    def configured(self) -> bool:
        return bool(self.url)

    def send_is_preapproved(self, channel: str, contact: str) -> bool:
        """Разрешена ли автоматическая отправка ИМЕННО этой паре.

        Ни подстановочных знаков, ни «весь канал целиком»: разрешение выдаётся
        на канал И контакт вместе. Разрешить «весь Telegram» невозможно — это
        и есть смысл условия 1.
        """
        ch = (channel or "").lower()
        return any(p["channel"] == ch and p["contact"] == contact
                   for p in self.auto_send_allow)


@dataclass
class OpenClawBridge:
    """Клиент Gateway. Одно соединение, ленивое, переустанавливается при обрыве."""

    config: OpenClawConfig
    _ws: Any = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _next_id: int = 0
    scopes: tuple[str, ...] = ()
    server_version: str = ""
    protocol: int = 0
    methods: tuple[str, ...] = ()
    # Когда в последний раз проверяли их конфигурацию памяти (монотонные часы
    # цикла событий). 0 — не проверяли: следующий вызов проверит обязательно.
    _memory_checked_at: float = 0.0

    # ---------------------------------------------------------------- соединение

    @property
    def available(self) -> bool:
        if not self.config.configured:
            return False
        try:
            import websockets  # noqa: F401
            return True
        except Exception:
            return False

    async def _open(self) -> Any:
        if self._ws is not None and not getattr(self._ws, "closed", False):
            return self._ws
        if not self.config.configured:
            raise OpenClawUnavailable("OpenClaw не настроен: не задан url Gateway")
        try:
            import websockets
        except Exception as exc:
            raise OpenClawUnavailable(
                "нет пакета websockets — `pip install websockets`") from exc

        try:
            ws = await asyncio.wait_for(
                websockets.connect(self.config.url, max_size=26 * 1024 * 1024),
                timeout=CONNECT_TIMEOUT)
        except Exception as exc:
            raise OpenClawUnavailable(
                f"не удалось подключиться к {self.config.url}: "
                f"{type(exc).__name__}: {exc}") from exc

        try:
            await self._handshake(ws)
            # Условие 3 §13 живёт ЗДЕСЬ, на боевом пути. Раньше `_check_memory`
            # существовал, был покрыт тестами — и не вызывался ниоткуда:
            # `_open` звал только `_handshake`. Тесты дёргали проверку руками и
            # поэтому были зелёными, а в бою мост подключался к чужой памяти
            # молча. Новое соединение — новая проверка, без исключений.
            self._memory_checked_at = 0.0
            await self._check_memory(ws)
        except Exception:
            await ws.close()
            self._ws = None
            raise
        self._ws = ws
        return ws

    async def _handshake(self, ws: Any) -> None:
        """`connect` → `hello-ok`. Здесь же проверяются права и память."""
        # Сервер шлёт connect.challenge до всего. Он может и не прийти —
        # ждём ограниченно и не считаем отсутствие ошибкой.
        try:
            await asyncio.wait_for(ws.recv(), timeout=2.0)
        except (asyncio.TimeoutError, Exception):
            pass

        params: dict[str, Any] = {
            "minProtocol": 1, "maxProtocol": 9,
            "client": {"id": CLIENT_ID, "version": "1.0.0",
                       "platform": "linux", "mode": CLIENT_MODE},
            "role": "operator", "scopes": list(REQUIRED_SCOPES),
            "caps": [], "commands": [], "permissions": {},
        }
        if self.config.token:
            params["auth"] = {"token": self.config.token}

        payload = await self._exchange(ws, "connect", params, timeout=CONNECT_TIMEOUT)
        self.protocol = int(payload.get("protocol") or 0)
        self.server_version = str((payload.get("server") or {}).get("version") or "")
        self.methods = tuple((payload.get("features") or {}).get("methods") or [])

        granted = tuple((payload.get("auth") or {}).get("scopes") or [])
        self.scopes = granted
        extra = [s for s in granted if s in FORBIDDEN_SCOPES]
        if extra:
            raise OpenClawScopeError(
                f"Gateway выдал права, которых мост не просил: {', '.join(extra)}. "
                f"Мост работает только с {', '.join(REQUIRED_SCOPES)} — "
                f"выдайте отдельный токен с минимальными правами.")
        missing = [s for s in REQUIRED_SCOPES if s not in granted]
        if missing and granted:
            raise OpenClawScopeError(
                f"не выданы права {', '.join(missing)}; получено: {', '.join(granted)}")

    async def _check_memory(self, ws: Any) -> None:
        """Условие 3: их память не должна смотреть в наше хранилище.

        Перепроверяем не только при подключении: конфигурацию OpenClaw могут
        поменять и ПОСЛЕ него, а соединение у нас долгоживущее. Полностью окно
        так не закрыть — но оно становится ограниченным
        `MEMORY_RECHECK_SECONDS`, а не «до следующего перезапуска».
        """
        if not self.config.vault_root:
            return                      # защищать нечего — и спрашивать нечего
        now = asyncio.get_running_loop().time()
        if self._memory_checked_at and now - self._memory_checked_at < MEMORY_RECHECK_SECONDS:
            return
        try:
            cfg = await self._exchange(ws, "config.get", {}, timeout=DEFAULT_TIMEOUT)
        except Exception as exc:
            # Молчаливое «продолжаем вслепую» здесь было бы худшим из решений:
            # владелец считал бы условие 3 выполненным, а оно не проверено.
            # Отказ несравнимо дешевле: подключение чинится за минуту, а
            # перезатёртые заметки не восстанавливаются ничем.
            raise OpenClawMemoryUnverified(
                f"не удалось прочитать конфигурацию OpenClaw "
                f"({type(exc).__name__}: {exc}) — ГАРАНТИИ НЕТ, что их память "
                f"не пишет в {self.config.vault_root}. Мост не подключается. "
                f"Выдайте токен с правом `config.get` либо уберите `vault_root` "
                f"из настроек моста, если защищать нечего.") from exc
        reason = memory_conflict(cfg if isinstance(cfg, dict) else {},
                                self.config.vault_root)
        if reason:
            raise OpenClawMemoryConflict(reason)
        self._memory_checked_at = now

    # ---------------------------------------------------------------- кадры

    async def _exchange(self, ws: Any, method: str, params: dict[str, Any], *,
                        timeout: float, extra: dict[str, Any] | None = None) -> Any:
        self._next_id += 1
        frame: dict[str, Any] = {"type": "req", "id": str(self._next_id),
                                 "method": method, "params": params}
        if extra:
            frame.update(extra)
        await ws.send(json.dumps(frame, ensure_ascii=False))

        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            remaining = deadline - asyncio.get_running_loop().time()
            raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, remaining))
            try:
                message = json.loads(raw)
            except Exception:
                continue
            if message.get("type") == "event":
                continue                       # события контракту V1 не мешают
            if message.get("type") != "res" or message.get("id") != frame["id"]:
                continue
            if message.get("ok"):
                return message.get("payload")
            err = message.get("error") or {}
            code = str(err.get("code") or "ERROR")
            detail = str(err.get("message") or "")
            if code == "UNAVAILABLE" and (err.get("details") or {}).get(
                    "reason") == "startup-sidecars":
                raise OpenClawUnavailable(
                    "Gateway ещё запускается (startup-sidecars) — повторите позже")
            raise OpenClawUnavailable(f"{code}: {detail}")
        # Тело запроса уже ушло. Отсутствие ответа НЕ означает, что его не
        # приняли на той стороне — только то, что мы не знаем.
        raise OpenClawUnavailable(f"{method}: нет ответа за {timeout:g} с",
                                  ambiguous=True)

    async def call(self, name: str, params: dict[str, Any] | None = None, *,
                   timeout: float = DEFAULT_TIMEOUT,
                   idem: str = "", run_id: Any = None) -> Any:
        """Вызов ИЗ КОНТРАКТА V1. Всё остальное отвергается здесь же."""
        method = V1_METHODS.get(name, name)
        if method in NEVER_PROXY or name in NEVER_PROXY:
            raise OpenClawForbidden(
                f"метод {method} вне контракта V1 и мостом не проксируется. "
                f"Расширение поверхности — отдельное решение владельца.")
        if method not in V1_METHODS.values():
            raise OpenClawForbidden(f"метод {method} не входит в контракт V1")

        async with self._lock:
            ws = await self._open()
            # Соединение живёт долго, их конфигурацию могли поменять после
            # подключения. Проверка сама себя троттлит (MEMORY_RECHECK_SECONDS).
            await self._check_memory(ws)
            extra: dict[str, Any] = {"traceparent": traceparent(run_id=run_id)}
            body = dict(params or {})
            if idem:
                body["idempotencyKey"] = idem
            try:
                return await self._exchange(ws, method, body, timeout=timeout,
                                            extra=extra)
            except Exception as exc:
                # Соединение могло умереть — закрываем, следующий вызов поднимет
                # заново. Автоматического повтора НЕТ: повторять отправку
                # сообщения человеку без ключа идемпотентности нельзя.
                await self.close()
                # Обрыв сокета ПОСЛЕ отправки кадра приходил сюда сырым
                # исключением websockets и уходил наружу как обычная ошибка —
                # неотличимо от «не дошло». Приводим к неоднозначности явно:
                # молчание сокета не означает, что запрос не приняли.
                if isinstance(exc, (OpenClawUnavailable, OpenClawForbidden,
                                    OpenClawScopeError, OpenClawMemoryConflict)):
                    raise
                raise OpenClawUnavailable(
                    f"{method}: соединение оборвалось после отправки запроса "
                    f"({type(exc).__name__}: {exc})", ambiguous=True) from exc

    async def health(self, *, run_id: Any = None) -> Any:
        return await self.call("health", {}, timeout=HEALTH_TIMEOUT, run_id=run_id)

    async def close(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass


__all__ = [
    "CLIENT_ID", "REQUIRED_SCOPES", "V1_METHODS", "NEVER_PROXY",
    "OpenClawBridge", "OpenClawConfig", "OpenClawUnavailable", "OpenClawScopeError",
    "OpenClawMemoryConflict", "OpenClawMemoryUnverified", "OpenClawForbidden",
    "idempotency_key", "traceparent", "memory_conflict", "MEMORY_RECHECK_SECONDS",
]
