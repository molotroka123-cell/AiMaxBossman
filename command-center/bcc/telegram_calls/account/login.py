"""Telethon login state machine (phone -> code -> optional 2FA) plus contacts / peer resolution.

Written against an INJECTED client factory ``factory(api_id, api_hash, session) -> client`` so it is
tested with a fake client and never imports Telethon at module import time. The client must look like a
Telethon ``TelegramClient``: ``connect / disconnect / send_code_request / sign_in / get_me /
is_user_authorized / log_out / get_entity`` and ``session.save()``.

Secrets rules: phone, code, 2FA password, api_hash and the session string are held only in memory
(phone / hash) or in the encrypted ``CredentialStore``; they never enter an exception, a log line or a
return value. Telethon errors are mapped by class NAME (no Telethon import) to stable ``LOGIN_*`` codes;
the Telethon message text is discarded because it can contain the phone number.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable

from ..types import AccountState, CallError
from .credentials import CredentialStore

CLIENT_TIMEOUT_S = 30.0

_BY_NAME: dict[str, str] = {
    "PhoneNumberInvalidError": "LOGIN_PHONE_INVALID", "PhoneNumberBannedError": "LOGIN_PHONE_INVALID",
    "PhoneNumberUnoccupiedError": "LOGIN_PHONE_INVALID", "PhoneNumberOccupiedError": "LOGIN_PHONE_INVALID",
    "PhoneCodeInvalidError": "LOGIN_CODE_INVALID", "PhoneCodeEmptyError": "LOGIN_CODE_INVALID",
    "PhoneCodeExpiredError": "LOGIN_CODE_EXPIRED", "PhoneCodeHashEmptyError": "LOGIN_CODE_EXPIRED",
    "PasswordHashInvalidError": "LOGIN_PASSWORD_INVALID",
    "FloodWaitError": "LOGIN_FLOOD_WAIT", "FloodError": "LOGIN_FLOOD_WAIT", "PhoneNumberFloodError": "LOGIN_FLOOD_WAIT",
    "SendCodeUnavailableError": "LOGIN_FLOOD_WAIT",
    "ApiIdInvalidError": "NO_CREDENTIALS", "ApiIdPublishedFloodError": "LOGIN_FLOOD_WAIT",
    "AuthKeyUnregisteredError": "SESSION_REVOKED", "SessionRevokedError": "SESSION_REVOKED",
    "SessionExpiredError": "SESSION_REVOKED", "UserDeactivatedError": "SESSION_REVOKED",
    "UserDeactivatedBanError": "SESSION_REVOKED", "AuthKeyDuplicatedError": "SESSION_REVOKED",
    "AuthKeyInvalidError": "SESSION_REVOKED",
    "PeerIdInvalidError": "PEER_NOT_FOUND", "UsernameNotOccupiedError": "PEER_NOT_FOUND",
    "UsernameInvalidError": "PEER_NOT_FOUND",
}


def map_telethon_error(exc: BaseException) -> CallError:
    """Exception -> secret-free CallError. Only class names (and a flood-wait seconds int) survive."""
    if isinstance(exc, CallError):
        return exc
    names = [c.__name__ for c in type(exc).__mro__]
    for name in names:
        code = _BY_NAME.get(name)
        if code:
            detail = None
            if code == "LOGIN_FLOOD_WAIT":
                seconds = getattr(exc, "seconds", None)
                detail = f"{int(seconds)}s" if isinstance(seconds, int) and not isinstance(seconds, bool) else None
            return CallError(code, detail=detail)
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError, OSError)):
        return CallError("TELEGRAM_NETWORK", detail=type(exc).__name__)
    if "RPCError" in names:
        return CallError("TELEGRAM_RPC", detail=type(exc).__name__)
    return CallError("INTERNAL", detail=type(exc).__name__)


def default_client_factory(api_id: int, api_hash: str, session: str) -> Any:
    """Real Telethon client on a StringSession. Optional dependency: imported lazily."""
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except ImportError:
        raise CallError("DEPENDENCIES_MISSING") from None
    return TelegramClient(StringSession(session or None), api_id, api_hash)


async def _call(coro: Any, timeout: float = CLIENT_TIMEOUT_S) -> Any:
    try:
        return await asyncio.wait_for(coro, timeout)
    except CallError:
        raise
    except asyncio.CancelledError:
        raise
    except BaseException as exc:  # noqa: BLE001 - mapped to a stable code; text is discarded
        raise map_telethon_error(exc) from None


class LoginFlow:
    def __init__(self, store: CredentialStore, client_factory: Callable[[int, str, str], Any] = default_client_factory,
                 *, timeout: float = CLIENT_TIMEOUT_S):
        self.store = store
        self._factory = client_factory
        self._timeout = timeout
        self._client: Any = None
        self._phone: str | None = None
        self._code_hash: str | None = None
        self._state = AccountState.LOGGED_OUT

    # ---------------------------------------------------------------- state
    @property
    def state(self) -> AccountState:
        if self._state in (AccountState.CODE_SENT, AccountState.PASSWORD_NEEDED):
            return self._state
        if self._state == AccountState.ERROR:
            return self._state
        st = self.store.status()
        if st["has_session"]:
            return AccountState.READY
        return AccountState.LOGGED_OUT if st["has_credentials"] else AccountState.NO_CREDENTIALS

    def status(self) -> dict[str, Any]:
        return {"state": self.state.value, **self.store.status()}

    async def _drop_client(self) -> None:
        client, self._client = self._client, None
        self._phone = self._code_hash = None
        if client is not None:
            try:
                await asyncio.wait_for(client.disconnect(), 5)
            except BaseException:  # noqa: BLE001 - cleanup only
                pass

    def _reset(self) -> None:
        self._state = AccountState.LOGGED_OUT

    # ---------------------------------------------------------------- flow
    async def start(self, phone: Any) -> dict[str, Any]:
        """Step 1: request the login code for ``phone`` (international format)."""
        if not isinstance(phone, str) or not 7 <= len(phone.strip()) <= 20 or not phone.strip().lstrip("+").isdigit():
            raise CallError("LOGIN_PHONE_INVALID")
        if not self.store.has_api():
            raise CallError("NO_CREDENTIALS")
        if self.store.has_session():
            return self.status()                       # already connected: nothing to do
        await self._drop_client()
        creds = self.store.get()
        phone = phone.strip()
        client = self._factory(creds.api_id, creds.api_hash, "")
        try:
            await _call(client.connect(), self._timeout)
            sent = await _call(client.send_code_request(phone), self._timeout)
        except CallError:
            self._client = client
            await self._drop_client()
            self._reset()
            raise
        code_hash = getattr(sent, "phone_code_hash", None)
        if not isinstance(code_hash, str) or not code_hash:
            self._client = client
            await self._drop_client()
            raise CallError("TELEGRAM_RPC", detail="no_code_hash")
        self._client, self._phone, self._code_hash = client, phone, code_hash
        self._state = AccountState.CODE_SENT
        return self.status()

    async def submit_code(self, code: Any) -> dict[str, Any]:
        """Step 2: the code from the Telegram app. May end in PASSWORD_NEEDED (2FA)."""
        if self._state != AccountState.CODE_SENT or self._client is None:
            raise CallError("LOGIN_NOT_PENDING")
        if not isinstance(code, str) or not code.strip().isdigit() or not 3 <= len(code.strip()) <= 8:
            raise CallError("LOGIN_CODE_INVALID")
        try:
            await self._sign_in(phone=self._phone, code=code.strip(), phone_code_hash=self._code_hash)
        except _PasswordNeeded:
            self._state = AccountState.PASSWORD_NEEDED
            return self.status()
        except CallError as exc:
            if exc.code == "LOGIN_CODE_EXPIRED":       # a new code must be requested from scratch
                await self._drop_client()
                self._reset()
            raise
        return await self._finish()

    async def submit_password(self, password: Any) -> dict[str, Any]:
        """Step 3 (only if 2FA is on)."""
        if self._state != AccountState.PASSWORD_NEEDED or self._client is None:
            raise CallError("LOGIN_NOT_PENDING")
        if not isinstance(password, str) or not password:
            raise CallError("LOGIN_PASSWORD_INVALID")
        await self._sign_in(password=password)
        return await self._finish()

    async def _sign_in(self, **kwargs: Any) -> None:
        try:
            await asyncio.wait_for(self._client.sign_in(**kwargs), self._timeout)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001
            if type(exc).__name__ == "SessionPasswordNeededError":
                raise _PasswordNeeded() from None
            raise map_telethon_error(exc) from None

    async def _finish(self) -> dict[str, Any]:
        client = self._client
        try:
            me = await _call(client.get_me(), self._timeout)
            self_id = getattr(me, "id", None)
            session = client.session.save()
            if not isinstance(self_id, int) or not isinstance(session, str) or not session:
                raise CallError("TELEGRAM_RPC", detail="no_self")
            self.store.save_session(session, self_id)
        except CallError:
            await self._drop_client()
            self._reset()
            raise
        await self._drop_client()
        self._state = AccountState.LOGGED_OUT          # derived: has_session -> READY
        return self.status()

    async def cancel(self) -> dict[str, Any]:
        await self._drop_client()
        self._reset()
        return self.status()

    async def logout(self) -> dict[str, Any]:
        """Terminate the session on Telegram's side (best effort) and forget everything locally."""
        await self._drop_client()
        if self.store.has_session():
            try:
                client = await self.open_session()
                try:
                    await asyncio.wait_for(client.log_out(), 10)
                finally:
                    try:
                        await asyncio.wait_for(client.disconnect(), 5)
                    except BaseException:  # noqa: BLE001
                        pass
            except BaseException as exc:  # noqa: BLE001 - local forget must happen regardless
                if isinstance(exc, asyncio.CancelledError):
                    raise
        self.store.clear_all()
        self._reset()
        return self.status()

    # ---------------------------------------------------------------- authorised session
    async def open_session(self) -> Any:
        """Connected, authorised client from the stored session (for contacts and calls)."""
        if not self.store.has_api():
            raise CallError("NO_CREDENTIALS")
        creds = self.store.get()
        if not creds.session:
            raise CallError("NOT_LOGGED_IN")
        client = self._factory(creds.api_id, creds.api_hash, creds.session)
        try:
            await _call(client.connect(), self._timeout)
            if not await _call(client.is_user_authorized(), self._timeout):
                self.store.clear_session()
                raise CallError("SESSION_REVOKED")
        except CallError as exc:
            if exc.code == "SESSION_REVOKED":
                self.store.clear_session()
            try:
                await asyncio.wait_for(client.disconnect(), 5)
            except BaseException:  # noqa: BLE001
                pass
            raise
        return client


class _PasswordNeeded(Exception):
    pass


# ==================================================================== contacts / peers
def _display(user: Any) -> str:
    parts = [str(getattr(user, "first_name", "") or ""), str(getattr(user, "last_name", "") or "")]
    name = " ".join(p for p in parts if p).strip()
    return ("".join(ch for ch in (name or str(getattr(user, "username", "") or "")) if ch.isprintable()))[:64]


def peer_problem(user: Any, self_id: int | None) -> str | None:
    """None if ``user`` may be the call peer, else the CallError code."""
    uid = getattr(user, "id", None)
    if type(user).__name__ != "User" or not isinstance(uid, int) or isinstance(uid, bool):
        return "PEER_INVALID"                       # chats/channels/unknown objects
    if getattr(user, "is_self", False) or (self_id is not None and uid == self_id):
        return "PEER_IS_SELF"
    if getattr(user, "bot", False) or getattr(user, "deleted", False):
        return "PEER_INVALID"
    return None


def public_contact(user: Any) -> dict[str, Any]:
    """What the UI may show: id, display name, username. Never the phone number."""
    username = getattr(user, "username", None)
    return {"user_id": int(user.id), "label": _display(user), "username": username if isinstance(username, str) else None}


async def list_contacts(client: Any, self_id: int | None, *, limit: int = 500) -> list[dict[str, Any]]:
    """Contacts that could be call peers (bots, deleted accounts and ourselves are filtered out)."""
    getter = getattr(client, "get_contacts", None)
    if callable(getter):
        users = await _call(getter())
    else:                                            # real Telethon: raw request, lazy import
        try:
            from telethon.tl.functions.contacts import GetContactsRequest
        except ImportError:
            raise CallError("DEPENDENCIES_MISSING") from None
        result = await _call(client(GetContactsRequest(0)))
        users = getattr(result, "users", [])
    out = [public_contact(u) for u in users if peer_problem(u, self_id) is None]
    out.sort(key=lambda c: c["label"].lower())
    return out[:limit]


async def resolve_peer(client: Any, user_id: Any, self_id: int | None) -> dict[str, Any]:
    """Validate ONE user id (as selected by the owner) and return its public contact card."""
    if isinstance(user_id, bool) or not isinstance(user_id, int) or not 0 < user_id < 2**52:
        raise CallError("PEER_INVALID")
    if self_id is not None and user_id == self_id:
        raise CallError("PEER_IS_SELF")
    try:
        user = await asyncio.wait_for(client.get_entity(user_id), CLIENT_TIMEOUT_S)
    except asyncio.CancelledError:
        raise
    except BaseException as exc:  # noqa: BLE001
        if isinstance(exc, ValueError):
            raise CallError("PEER_NOT_FOUND") from None
        mapped = map_telethon_error(exc)
        raise mapped from None
    problem = peer_problem(user, self_id)
    if problem:
        raise CallError(problem)
    if user.id != user_id:
        raise CallError("PEER_INVALID")
    return public_contact(user)
