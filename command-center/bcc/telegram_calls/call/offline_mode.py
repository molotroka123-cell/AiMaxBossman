"""OFFLINE TEST MODE (``BOSSMAN_CALLS_MODE=offline_test``): the whole product flow without Telegram.

Set only in the operator's environment (the acceptance harness), never through the API. In this mode:
* the real ``TelegramAccount`` login code runs against ``OfflineClient`` (fixed test phone/code, one synthetic contact);
* the call transport is the loopback line, driven by a synthetic interlocutor;
* every record says ``transport="loopback"`` and the status API reports ``mode: "offline_test"``, so the UI/CLI label it
  «ТЕСТ БЕЗ TELEGRAM». It can prove OUR plumbing and latency; it can never count as a real Telegram call.
It shares the data directory of the backend it runs in, so the worker REFUSES to start in this mode when the Vault already holds a
real (non-offline) session: the fake client must never overwrite or clear it (the acceptance harness always uses a fresh directory).
"""
from __future__ import annotations

import asyncio
from typing import Any

from .loopback import LoopbackTransport

OFFLINE_PHONE = "+70000000000"
OFFLINE_CODE = "12345"
OFFLINE_ME_ID = 111000111
OFFLINE_PEER_ID = 222000222


class _User:
    def __init__(self, id, first, last="", username=None):
        self.id, self.first_name, self.last_name, self.username = id, first, last, username
        self.bot = self.deleted = False


class _Session:
    def save(self) -> str:
        return "OFFLINE-TEST-SESSION-" + "0" * 40


class _Dialog:
    def __init__(self, entity):
        self.entity = entity


class OfflineClient:
    """Just enough of a Telethon client for the login/contacts flow. No network, no real account."""

    def __init__(self, session: str, api_id: int, api_hash: str):
        self.session, self._connected, self._authorised = _Session(), False, bool(session)
        self._me = _User(OFFLINE_ME_ID, "Основной", "аккаунт (тест)")
        self._peer = _User(OFFLINE_PEER_ID, "Второй", "аккаунт (тест)", "offline_second")

    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def is_user_authorized(self) -> bool:
        return self._authorised

    async def send_code_request(self, phone: str):
        if phone != OFFLINE_PHONE:
            raise type("PhoneNumberInvalidError", (Exception,), {})("offline")
        return type("Sent", (), {"phone_code_hash": "offline-hash"})()

    async def sign_in(self, phone=None, code=None, *, password=None, phone_code_hash=None):
        if code != OFFLINE_CODE:
            raise type("PhoneCodeInvalidError", (Exception,), {})("offline")
        self._authorised = True

    async def get_me(self):
        return self._me

    async def log_out(self) -> None:
        self._authorised = False

    async def iter_dialogs(self, limit: int = 200):
        yield _Dialog(self._peer)

    async def get_contacts(self):
        return [self._peer]

    async def get_entity(self, key: Any):
        if key in ("offline_second", OFFLINE_PEER_ID):
            return self._peer
        raise type("UsernameNotOccupiedError", (Exception,), {})("offline")


def offline_client_factory(session: str, api_id: int, api_hash: str) -> OfflineClient:
    return OfflineClient(session, api_id, api_hash)


class ScriptedLoopback(LoopbackTransport):
    """Loopback whose far end runs a script as soon as the call connects (the synthetic interlocutor)."""

    def __init__(self, script, **kw):
        super().__init__(**kw)
        self._script, self._script_task = script, None

    async def dial(self, peer, *, ring_timeout: float) -> None:
        await super().dial(peer, ring_timeout=ring_timeout)
        if self._script is not None:
            self._script_task = asyncio.get_running_loop().create_task(self._script(self), name="offline-peer-script")

    async def close(self) -> None:
        if self._script_task is not None and not self._script_task.done():
            self._script_task.cancel()
        await super().close()
