"""Ephemeral owner-secret intake for Telegram Companion.

Security goal: plaintext from Telegram must never enter Store/SQLite, model
history, learning logs, cloud prompts, screenshots, or audit logs. A secret is
accepted only for a short-lived in-memory request bound to the exact owner chat.

Python cannot guarantee physical RAM erasure and Telegram is an external
service, so this module never claims zero-risk secrecy. It minimizes local
exposure and fails closed.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Protocol, Sequence


_FIELD_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_SECRETISH_RE = re.compile(
    r"(?i)(?:^|\n)\s*(?:password|passwd|pass|пароль|otp|pin|token|api[_-]?key)\s*=|"
    r"\bsk-[A-Za-z0-9_-]{12,}\b|\bghp_[A-Za-z0-9]{20,}\b"
)


class SecretIntakeError(RuntimeError):
    pass


@dataclass(frozen=True)
class SecretField:
    name: str
    label: str
    kind: str = "secret"

    def __post_init__(self):
        if not _FIELD_RE.fullmatch(self.name):
            raise ValueError("invalid secret field name")
        if not self.label or len(self.label) > 80:
            raise ValueError("invalid secret field label")
        if self.kind not in {"secret", "username", "otp"}:
            raise ValueError("invalid secret field kind")


@dataclass
class SecretExecutionResult:
    success: bool
    verified: bool
    code: str = ""
    detail: str = ""


class LocalOnlySecretExecutor(Protocol):
    local_only: bool
    network_isolated: bool
    model_sees_secret: bool
    local_model_controlled: bool

    async def apply(self, request: "SecretRequest",
                    values: dict[str, bytearray]) -> SecretExecutionResult: ...


@dataclass
class SecretRequest:
    session_id: str
    owner_key: str
    chat_id: int
    target: str
    fields: tuple[SecretField, ...]
    screenshot_sha256: str
    created_at: float
    expires_at: float
    executor: LocalOnlySecretExecutor = field(repr=False)
    request_message_id: int | None = None
    secret_message_id: int | None = None
    state: str = "WAIT_SECRET"
    attempts: int = 0

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at


def looks_like_secret_message(text: str) -> bool:
    return bool(_SECRETISH_RE.search(str(text or "")))


def _wipe(values: dict[str, bytearray]) -> None:
    for buf in values.values():
        for i in range(len(buf)):
            buf[i] = 0


def _parse(request: SecretRequest, text: str) -> dict[str, bytearray]:
    raw = str(text or "")
    if not raw or len(raw) > 4096:
        raise SecretIntakeError("SECRET_PAYLOAD_INVALID")
    expected = {f.name for f in request.fields}
    if len(request.fields) == 1 and "=" not in raw and "\n" not in raw:
        only = request.fields[0].name
        return {only: bytearray(raw.encode("utf-8"))}

    parsed: dict[str, bytearray] = {}
    try:
        for line in raw.splitlines():
            if not line.strip():
                continue
            key, sep, value = line.partition("=")
            key = key.strip().lower()
            if not sep or key not in expected or key in parsed or not value:
                raise SecretIntakeError("SECRET_PAYLOAD_INVALID")
            parsed[key] = bytearray(value.encode("utf-8"))
        if set(parsed) != expected:
            raise SecretIntakeError("SECRET_FIELDS_MISSING")
        return parsed
    except Exception:
        _wipe(parsed)
        raise


class SecretIntakeManager:
    """One in-memory request per owner. Restart invalidates every request."""

    def __init__(self):
        self._by_owner: dict[str, SecretRequest] = {}

    def begin(self, *, owner_key: str, chat_id: int, target: str,
              fields: Sequence[SecretField], screenshot: bytes,
              screenshot_redacted: bool, executor: LocalOnlySecretExecutor,
              ttl_seconds: int = 180) -> SecretRequest:
        if not screenshot_redacted:
            raise SecretIntakeError("SCREENSHOT_NOT_REDACTED")
        if not getattr(executor, "local_only", False):
            raise SecretIntakeError("SECRET_EXECUTOR_NOT_LOCAL")
        if not getattr(executor, "network_isolated", False):
            raise SecretIntakeError("SECRET_EXECUTOR_HAS_NETWORK")
        if getattr(executor, "model_sees_secret", True):
            raise SecretIntakeError("MODEL_MUST_NOT_SEE_SECRET")
        if not getattr(executor, "local_model_controlled", False):
            raise SecretIntakeError("LOCAL_MODEL_CONTROL_REQUIRED")
        rows = tuple(fields)
        if not 1 <= len(rows) <= 4 or len({f.name for f in rows}) != len(rows):
            raise SecretIntakeError("SECRET_FIELDS_INVALID")
        if not isinstance(screenshot, (bytes, bytearray)) or not screenshot:
            raise SecretIntakeError("SCREENSHOT_REQUIRED")
        ttl = max(30, min(int(ttl_seconds), 300))
        req = SecretRequest(
            session_id=secrets.token_hex(8), owner_key=owner_key, chat_id=int(chat_id),
            target=str(target)[:120], fields=rows,
            screenshot_sha256=hashlib.sha256(bytes(screenshot)).hexdigest(),
            created_at=time.time(), expires_at=time.time() + ttl, executor=executor,
        )
        old = self._by_owner.pop(owner_key, None)
        if old:
            old.state = "REPLACED"
        self._by_owner[owner_key] = req
        return req

    def bind_request_message(self, owner_key: str, session_id: str, message_id: int) -> None:
        req = self._get(owner_key, session_id)
        if type(message_id) is not int or message_id <= 0:
            raise SecretIntakeError("TELEGRAM_MESSAGE_ID_INVALID")
        req.request_message_id = message_id

    def active(self, owner_key: str) -> bool:
        req = self._by_owner.get(owner_key)
        if req and req.expired:
            req.state = "EXPIRED"
            self._by_owner.pop(owner_key, None)
            return False
        return bool(req and req.state in {"WAIT_SECRET", "PROCESSING"})

    def pending(self, owner_key: str) -> SecretRequest | None:
        req = self._by_owner.get(owner_key)
        if req and req.expired:
            req.state = "EXPIRED"
            self._by_owner.pop(owner_key, None)
            return None
        return req if req and req.state == "WAIT_SECRET" else None

    def cancel(self, owner_key: str) -> bool:
        req = self._by_owner.pop(owner_key, None)
        if not req:
            return False
        req.state = "CANCELLED"
        return True

    def _get(self, owner_key: str, session_id: str) -> SecretRequest:
        req = self._by_owner.get(owner_key)
        if not req or req.session_id != session_id or req.expired:
            raise SecretIntakeError("SECRET_SESSION_EXPIRED")
        return req

    async def consume(self, *, owner_key: str, chat_id: int, message_id: int,
                      text: str) -> tuple[SecretRequest, SecretExecutionResult]:
        req = self.pending(owner_key)
        if req is None:
            raise SecretIntakeError("SECRET_SESSION_MISSING")
        if req.chat_id != int(chat_id):
            raise SecretIntakeError("SECRET_OWNER_MISMATCH")
        if type(message_id) is not int or message_id <= 0:
            raise SecretIntakeError("TELEGRAM_MESSAGE_ID_INVALID")
        req.secret_message_id = message_id
        req.state = "PROCESSING"
        req.attempts += 1
        values: dict[str, bytearray] = {}
        try:
            values = _parse(req, text)
            raw_result = await req.executor.apply(req, values)
            if isinstance(raw_result, SecretExecutionResult):
                result = raw_result
            elif all(hasattr(raw_result, name) for name in ("success", "verified", "code", "detail")):
                result = SecretExecutionResult(
                    bool(raw_result.success), bool(raw_result.verified),
                    str(raw_result.code)[:80], str(raw_result.detail)[:200],
                )
            else:
                raise SecretIntakeError("SECRET_EXECUTOR_RESULT_INVALID")
            if result.success and result.verified:
                req.state = "VERIFIED"
            else:
                req.state = "FAILED"
            return req, result
        finally:
            _wipe(values)
            # Burn the request after one plaintext message. A retry needs a new
            # request/screenshot, so a stale credential is never replayed.
            self._by_owner.pop(owner_key, None)


def request_caption(req: SecretRequest) -> str:
    rows = [f"• {f.name} — {f.label}" for f in req.fields]
    if len(req.fields) == 1:
        how = "Пришлите только значение одним сообщением."
    else:
        how = "Пришлите строки вида name=value, по одной строке на поле."
    return (
        f"🔐 Локальный ввод данных · {req.target}\n"
        f"Сессия {req.session_id} · действует до 5 минут.\n\n"
        + "\n".join(rows)
        + "\n\n" + how
        + "\nСообщение не попадёт в память/историю Bossman или cloud-модель. "
          "После попытки ввода оно будет удалено ботом best-effort."
    )
