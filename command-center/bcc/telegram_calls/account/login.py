"""MTProto user-account login and read-only lookups (Telethon), with stable secret-free errors.

Flow: ``start_login(phone)`` -> Telegram sends the code -> ``submit_code(code)`` -> if 2FA is on
``submit_password(pw)`` -> the session string is saved encrypted (``CredentialStore``). Phone, code and password
exist only in local variables of these calls (and the pending phone in memory until the flow ends); they are never
logged, never put in an event, never returned.

Telethon is imported lazily (optional ``calls`` extra). Errors are mapped by exception CLASS NAME so this module
is testable without Telethon and never leaks Telethon's message text (it can contain the phone number).
"""
from __future__ import annotations

import re
from typing import Any, Callable

from ..types import AccountState, CallError
from .credentials import CredentialStore, mask_phone

_PHONE = re.compile(r"^\+\d{7,15}$")
_APP_NAME = "Bossman"

_ERROR_MAP = {
    "PhoneNumberInvalidError": "LOGIN_PHONE_INVALID",
    "PhoneNumberBannedError": "LOGIN_PHONE_INVALID",
    "PhoneNumberUnoccupiedError": "LOGIN_PHONE_INVALID",
    "PhoneCodeInvalidError": "LOGIN_CODE_INVALID",
    "PhoneCodeEmptyError": "LOGIN_CODE_INVALID",
    "PhoneCodeExpiredError": "LOGIN_CODE_EXPIRED",
    "PasswordHashInvalidError": "LOGIN_PASSWORD_INVALID",
    "FloodWaitError": "LOGIN_FLOOD_WAIT",
    "PhoneNumberFloodError": "LOGIN_FLOOD_WAIT",
    "SessionRevokedError": "SESSION_REVOKED",
    "AuthKeyUnregisteredError": "SESSION_REVOKED",
    "AuthKeyDuplicatedError": "SESSION_REVOKED",
    "UserDeactivatedError": "SESSION_REVOKED",
    "UserDeactivatedBanError": "SESSION_REVOKED",
    "UserPrivacyRestrictedError": "PEER_PRIVACY",
    "ApiIdInvalidError": "NO_CREDENTIALS",
    "ApiIdPublishedFloodError": "LOGIN_FLOOD_WAIT",
    "ConnectionError": "TELEGRAM_NETWORK",
    "TimeoutError": "TELEGRAM_NETWORK",
    "OSError": "TELEGRAM_NETWORK",
    "ServerError": "TELEGRAM_RPC",
    "RPCError": "TELEGRAM_RPC",
    "FloodError": "LOGIN_FLOOD_WAIT",
}


def map_error(exc: BaseException) -> CallError:
    """Telethon (or network) exception -> stable CallError. Never copies the exception text."""
    if isinstance(exc, CallError):
        return exc
    for cls in type(exc).__mro__:
        code = _ERROR_MAP.get(cls.__name__)
        if code:
            seconds = getattr(exc, "seconds", None)
            return CallError(code, detail=f"wait_{int(seconds)}s" if code == "LOGIN_FLOOD_WAIT" and isinstance(seconds, int) else cls.__name__)
    return CallError("TELEGRAM_RPC", detail=type(exc).__name__)


def normalize_phone(phone: str) -> str:
    p = re.sub(r"[\s\-().]", "", phone or "")          # an explicit "+" country code is required: no guessing (8 9xx… is not valid)
    if not _PHONE.match(p):
        raise CallError("LOGIN_PHONE_INVALID")
    return p


def telethon_available() -> bool:
    import importlib.util
    return importlib.util.find_spec("telethon") is not None


def _default_client_factory(session: str, api_id: int, api_hash: str):
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except Exception:  # noqa: BLE001
        raise CallError("DEPENDENCIES_MISSING", detail="telethon") from None
    return TelegramClient(StringSession(session or None), api_id, api_hash, device_model=_APP_NAME,
                          app_version="calls", system_version="local", lang_code="ru", system_lang_code="ru",
                          request_retries=3, connection_retries=3, receive_updates=True)


class TelegramAccount:
    def __init__(self, store: CredentialStore, *, client_factory: Callable[[str, int, str], Any] | None = None):
        self.store = store
        self._factory = client_factory or _default_client_factory
        self._client: Any = None
        self._pending_phone: str | None = None
        self._pending_hash: str | None = None
        self._state = AccountState.LOGGED_OUT
        self._me: dict | None = None

    # ------------------------------------------------------------ state
    @property
    def client(self) -> Any:
        return self._client

    def state(self) -> AccountState:
        try:
            creds = self.store.load()
        except CallError:
            return AccountState.ERROR
        if not creds.has_api:
            return AccountState.NO_CREDENTIALS
        if self._state in (AccountState.CODE_SENT, AccountState.PASSWORD_NEEDED):
            return self._state
        return AccountState.READY if creds.session else AccountState.LOGGED_OUT

    def me_id(self) -> int | None:
        try:
            return self.store.load().me_id or None
        except CallError:
            return None

    def public(self) -> dict:
        state = self.state()
        base = self.store.public()
        return {"state": state.value, "has_api": base.get("has_api", False), "api_id": base.get("api_id"),
                "phone": base.get("phone"), "me_id": base.get("me_id"),
                "pending_phone": mask_phone(self._pending_phone) if self._pending_phone else None}

    # ------------------------------------------------------------ client
    async def _connect(self) -> Any:
        creds = self.store.load()
        if not creds.has_api:
            raise CallError("NO_CREDENTIALS")
        if self._client is None:
            self._client = self._factory(creds.session, creds.api_id, creds.api_hash)
        try:
            if not self._client.is_connected():
                await self._client.connect()
        except Exception as exc:  # noqa: BLE001
            raise map_error(exc) from None
        return self._client

    async def ensure_client(self) -> Any:
        """Connected AND authorised client (for the call transport). Revoked session -> cleared + SESSION_REVOKED."""
        client = await self._connect()
        try:
            authorised = await client.is_user_authorized()
        except Exception as exc:  # noqa: BLE001
            err = map_error(exc)
            if err.code == "SESSION_REVOKED":
                self.store.clear_session()
            raise err from None
        if not authorised:
            had_session = bool(self.store.load().session)
            self.store.clear_session()
            raise CallError("SESSION_REVOKED" if had_session else "NOT_LOGGED_IN")
        return client

    # ------------------------------------------------------------ login flow
    async def start_login(self, phone: str) -> None:
        raw_phone = (phone or "").strip()
        if not raw_phone or raw_phone.lower() == "saved":
            raw_phone = self.store.saved_phone()
        phone = normalize_phone(raw_phone)
        self.store.save_phone(phone)
        client = await self._connect()
        try:
            sent = await client.send_code_request(phone)
        except Exception as exc:  # noqa: BLE001
            raise map_error(exc) from None
        self._pending_phone, self._pending_hash = phone, getattr(sent, "phone_code_hash", None)
        self._state = AccountState.CODE_SENT

    async def submit_code(self, code: str) -> AccountState:
        if not self._pending_phone or not self._pending_hash:
            raise CallError("LOGIN_NOT_PENDING")
        code = re.sub(r"\D", "", code or "")
        if not 4 <= len(code) <= 8:
            raise CallError("LOGIN_CODE_INVALID")
        client = await self._connect()
        try:
            await client.sign_in(phone=self._pending_phone, code=code, phone_code_hash=self._pending_hash)
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ == "SessionPasswordNeededError":
                self._state = AccountState.PASSWORD_NEEDED
                return self._state
            err = map_error(exc)
            if err.code == "LOGIN_CODE_EXPIRED":
                self._pending_hash = None
                self._state = AccountState.LOGGED_OUT
            raise err from None
        await self._finish_login()
        return AccountState.READY

    async def submit_password(self, password: str) -> AccountState:
        if self._state != AccountState.PASSWORD_NEEDED or not self._pending_phone:
            raise CallError("LOGIN_NOT_PENDING")
        if not password:
            raise CallError("LOGIN_PASSWORD_INVALID")
        client = await self._connect()
        try:
            await client.sign_in(password=password)
        except Exception as exc:  # noqa: BLE001
            raise map_error(exc) from None
        await self._finish_login()
        return AccountState.READY

    async def _finish_login(self) -> None:
        client = self._client
        me = await client.get_me()
        session = client.session.save()
        self.store.save_session(session, int(getattr(me, "id", 0)), self._pending_phone or "")
        self._me = {"id": int(getattr(me, "id", 0))}
        self._pending_phone = self._pending_hash = None
        self._state = AccountState.READY

    async def logout(self) -> None:
        """Forget the session locally and revoke it at Telegram when reachable (best effort)."""
        if self._client is not None:
            try:
                if self._client.is_connected() and await self._client.is_user_authorized():
                    await self._client.log_out()
            except Exception:  # noqa: BLE001 - local forget must succeed even offline
                pass
            await self.close()
        self.store.clear_session()
        self._pending_phone = self._pending_hash = None
        self._state = AccountState.LOGGED_OUT

    async def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------ read-only lookups
    @staticmethod
    def _label(user: Any) -> str:
        name = " ".join(x for x in (getattr(user, "first_name", "") or "", getattr(user, "last_name", "") or "") if x).strip()
        username = getattr(user, "username", None)
        return (name or (f"@{username}" if username else f"id {getattr(user, 'id', '?')}"))[:120]

    @classmethod
    def describe(cls, user: Any) -> dict:
        """Plain dict for the guard/UI. Phone numbers are never included."""
        return {"id": int(getattr(user, "id", 0) or 0), "label": cls._label(user),
                "username": getattr(user, "username", None), "bot": bool(getattr(user, "bot", False)),
                "deleted": bool(getattr(user, "deleted", False)), "is_self": bool(getattr(user, "is_self", False)),
                "is_user": type(user).__name__ == "User" or hasattr(user, "first_name")}

    async def contacts(self, query: str = "", limit: int = 50) -> list[dict]:
        """People the owner can pick as the test peer: existing contacts and recent private dialogs."""
        client = await self.ensure_client()
        seen: dict[int, dict] = {}
        try:
            async for dialog in client.iter_dialogs(limit=200):
                ent = getattr(dialog, "entity", None)
                if ent is not None and type(ent).__name__ == "User":
                    d = self.describe(ent)
                    seen.setdefault(d["id"], d)
            try:
                for user in await client.get_contacts():
                    d = self.describe(user)
                    seen.setdefault(d["id"], d)
            except Exception:  # noqa: BLE001 - dialogs alone are enough
                pass
        except Exception as exc:  # noqa: BLE001
            raise map_error(exc) from None
        me = self.me_id()
        q = (query or "").strip().lower().lstrip("@")
        out = [d for d in seen.values() if d["id"] != me and not d["bot"] and not d["deleted"]
               and (not q or q in d["label"].lower() or q in (d["username"] or "").lower())]
        out.sort(key=lambda d: d["label"].lower())
        return out[:max(1, min(limit, 200))]

    async def channels(self, query: str = "", limit: int = 100) -> list[dict]:
        """Channels and supergroups from the connected MTProto account."""
        client = await self.ensure_client()
        q = (query or "").strip().lower().lstrip("@")
        out: list[dict] = []
        try:
            async for dialog in client.iter_dialogs(limit=300):
                ent = getattr(dialog, "entity", None)
                if ent is None:
                    continue
                cls_name = type(ent).__name__
                is_channel = cls_name in ("Channel", "Chat") or bool(getattr(dialog, "is_channel", False) or getattr(dialog, "is_group", False))
                if not is_channel:
                    continue
                cid = int(getattr(ent, "id", 0) or 0)
                title = str(getattr(ent, "title", "") or getattr(dialog, "name", "") or f"id {cid}")[:160]
                username = getattr(ent, "username", None)
                if q and q not in title.lower() and q not in str(username or "").lower() and q not in str(cid):
                    continue
                out.append({
                    "id": cid,
                    "title": title,
                    "username": username,
                    "is_broadcast": bool(getattr(ent, "broadcast", False)),
                    "is_megagroup": bool(getattr(ent, "megagroup", False)),
                    "unread_count": int(getattr(dialog, "unread_count", 0) or 0),
                    "participants_count": getattr(ent, "participants_count", None),
                })
                if len(out) >= max(1, min(limit, 300)):
                    break
        except Exception as exc:  # noqa: BLE001
            raise map_error(exc) from None
        return out

    async def parse_channels(
        self,
        *,
        channel_ids: list[Any] | None = None,
        query: str = "",
        limit_per_channel: int = 20,
        max_channels: int = 25,
    ) -> dict:
        """Parse recent posts from subscribed Telegram channels via MTProto."""
        client = await self.ensure_client()
        lim_msg = max(1, min(int(limit_per_channel or 20), 100))
        lim_ch = max(1, min(int(max_channels or 25), 100))
        q = (query or "").strip().lower()
        targets: list[tuple[Any, int, str, str | None]] = []
        try:
            if channel_ids:
                for raw_id in channel_ids[:lim_ch]:
                    try:
                        ent = await client.get_entity(raw_id)
                        cid = int(getattr(ent, "id", 0) or 0)
                        title = str(getattr(ent, "title", "") or getattr(ent, "first_name", "") or raw_id)[:160]
                        uname = getattr(ent, "username", None)
                        targets.append((ent, cid, title, uname))
                    except Exception:  # noqa: BLE001
                        continue
            else:
                async for dialog in client.iter_dialogs(limit=300):
                    ent = getattr(dialog, "entity", None)
                    if ent is None:
                        continue
                    cls_name = type(ent).__name__
                    if cls_name not in ("Channel", "Chat") and not getattr(dialog, "is_channel", False):
                        continue
                    cid = int(getattr(ent, "id", 0) or 0)
                    title = str(getattr(ent, "title", "") or getattr(dialog, "name", "") or f"id {cid}")[:160]
                    uname = getattr(ent, "username", None)
                    targets.append((ent, cid, title, uname))
                    if len(targets) >= lim_ch:
                        break

            parsed_channels: list[dict] = []
            total_posts = 0
            for ent, cid, title, uname in targets:
                posts: list[dict] = []
                try:
                    async for msg in client.iter_messages(ent, limit=lim_msg):
                        mid = int(getattr(msg, "id", 0) or 0)
                        txt = str(getattr(msg, "message", "") or getattr(msg, "text", "") or "").strip()
                        if q and q not in txt.lower() and q not in title.lower():
                            continue
                        media = getattr(msg, "media", None)
                        media_type = type(media).__name__ if media is not None else None
                        dt = getattr(msg, "date", None)
                        link = f"https://t.me/{uname}/{mid}" if uname else f"https://t.me/c/{cid}/{mid}"
                        posts.append({
                            "post_id": mid,
                            "date": dt.isoformat() if hasattr(dt, "isoformat") else None,
                            "text": txt[:2000],
                            "views": getattr(msg, "views", None),
                            "forwards": getattr(msg, "forwards", None),
                            "has_media": media is not None,
                            "media_type": media_type,
                            "link": link,
                        })
                except Exception:  # noqa: BLE001
                    continue
                total_posts += len(posts)
                parsed_channels.append({
                    "channel_id": cid,
                    "title": title,
                    "username": uname,
                    "posts_count": len(posts),
                    "posts": posts,
                })
        except Exception as exc:  # noqa: BLE001
            raise map_error(exc) from None
        return {
            "source": "mtproto",
            "channels_count": len(parsed_channels),
            "total_posts": total_posts,
            "channels": parsed_channels,
        }

    async def resolve_username(self, username: str) -> dict:
        client = await self.ensure_client()
        name = (username or "").strip().lstrip("@")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", name):
            raise CallError("PEER_NOT_FOUND")
        try:
            ent = await client.get_entity(name)
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ in ("UsernameNotOccupiedError", "UsernameInvalidError", "ValueError"):
                raise CallError("PEER_NOT_FOUND") from None
            raise map_error(exc) from None
        return self.describe(ent)

    async def confirm_user(self, user_id: int) -> dict:
        """Re-fetch a chosen peer from Telegram right before saving/dialing (id must still be a real user)."""
        client = await self.ensure_client()
        try:
            ent = await client.get_entity(user_id)
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ in ("UserIdInvalidError", "PeerIdInvalidError", "ValueError"):
                raise CallError("PEER_NOT_FOUND") from None
            raise map_error(exc) from None
        return self.describe(ent)
