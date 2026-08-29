"""Оркестрация Stage 6: enroll / open_session / authenticate / lock / revoke.

`DeviceService` не знает, где лежат данные — он работает поверх любого
`DeviceStore` (in-memory фейк или Postgres). Здесь же — процессный синглтон
активного сервиса, который используют и роутер (зависимость по скоупу), и
подсистема (устанавливает боевой сервис на validate()).

Порядок проверок в authenticate() — фейл-клоуз:
  1. глобальная блокировка → DeviceRevoked (гасит всё, даже валидные токены);
  2. нет/битый заголовок → AuthDenied;
  3. неизвестный токен → AuthDenied;
  4. отозвано/заблокировано устройство или сессия → DeviceRevoked;
  5. финальное подтверждение хэша через compare_digest → иначе AuthDenied.
"""
from __future__ import annotations

from .. import correlation, obs
from ..errors import AuthDenied, DeviceRevoked
from .auth import (
    DEVICE_TOKEN_PREFIX,
    SESSION_TOKEN_PREFIX,
    DeviceRecord,
    Principal,
    SessionRecord,
    constant_time_eq,
    hash_token,
    new_device_id,
    new_device_secret,
    new_session_id,
    new_session_secret,
    normalize_scopes,
)
from .store import DeviceStore, InMemoryDeviceStore

log = obs.get_logger("bossman.remote_client")


class DeviceService:
    def __init__(self, store: DeviceStore) -> None:
        self.store = store

    # --- провижининг (операторский путь; ограничения прав — на слое роутера) ---

    async def enroll(self, name: str, scopes) -> tuple[str, str]:
        """Зарегистрировать устройство. Возвращает (device_id, raw_token).
        raw_token НИКОГДА не логируется и не сохраняется — только его sha256."""
        did = new_device_id()
        raw = new_device_secret()
        record = DeviceRecord(id=did, name=name, scopes=normalize_scopes(scopes))
        await self.store.add_device(record, hash_token(raw))
        # Логируем факт регистрации без секрета.
        log.info("device enrolled", extra={"device_id": did, "scopes": sorted(record.scopes)})
        return did, raw

    async def open_session(self, device_id: str) -> tuple[str, str]:
        """Выдать сессию аутентифицированному устройству. Токен сессии
        показывается один раз; сессии отзываются независимо от устройства."""
        sid = new_session_id()
        raw = new_session_secret()
        await self.store.add_session(SessionRecord(id=sid, device_id=device_id), hash_token(raw))
        log.info("session opened", extra={"device_id": device_id, "session_id": sid})
        return sid, raw

    # --- отзыв / блокировка ---

    async def revoke_device(self, device_id: str) -> bool:
        ok = await self.store.set_device_revoked(device_id, True)
        if ok:
            log.warning("device revoked", extra={"device_id": device_id})
        return ok

    async def lock_device(self, device_id: str, locked: bool = True) -> bool:
        ok = await self.store.set_device_locked(device_id, locked)
        if ok:
            log.warning("device lock=%s", locked, extra={"device_id": device_id})
        return ok

    async def lock_all(self, locked: bool = True) -> int:
        n = await self.store.set_all_locked(locked)
        log.warning("emergency lock-all=%s affected=%s", locked, n)
        return n

    async def revoke_session(self, session_id: str) -> bool:
        return await self.store.set_session_revoked(session_id, True)

    # --- аутентификация ---

    async def authenticate(self, authorization: str | None) -> Principal:
        """Разобрать заголовок Authorization и вернуть Principal либо бросить.
        Никогда не кладёт токен в лог/исключение."""
        # (1) фейл-клоуз при глобальной блокировке — до любой другой логики.
        if await self.store.is_global_lock():
            raise DeviceRevoked("all devices are locked")

        token = self._extract_bearer(authorization)
        if not token:
            raise AuthDenied("missing bearer token")

        if token.startswith(SESSION_TOKEN_PREFIX):
            principal = await self._auth_session(token)
        else:
            # device-токен (или без известного префикса) идёт по пути креденшла
            principal = await self._auth_device(token)

        # Привязать device_id в correlation-бандл для логов и шины событий.
        correlation.bind(device_id=principal.device_id)
        return principal

    async def _auth_device(self, token: str) -> Principal:
        presented = hash_token(token)
        match = await self.store.find_credential(presented)
        if match is None:
            raise AuthDenied("unknown device token")
        # финальное подтверждение совпадения в постоянном времени
        if not constant_time_eq(match.token_sha256, presented):
            raise AuthDenied("token mismatch")
        device = await self.store.get_device(match.device_id)
        if device is None or device.revoked or device.locked:
            raise DeviceRevoked("device revoked or locked")
        return Principal(device_id=device.id, scopes=device.scopes, name=device.name,
                         session_id=None)

    async def _auth_session(self, token: str) -> Principal:
        presented = hash_token(token)
        match = await self.store.find_session(presented)
        if match is None:
            raise AuthDenied("unknown session token")
        if not constant_time_eq(match.token_sha256, presented):
            raise AuthDenied("token mismatch")
        session = await self.store.get_session(match.session_id) if match.session_id else None
        if session is None or session.revoked:
            raise DeviceRevoked("session revoked")
        device = await self.store.get_device(session.device_id)
        if device is None or device.revoked or device.locked:
            raise DeviceRevoked("device revoked or locked")
        await self.store.touch_session(session.id)
        return Principal(device_id=device.id, scopes=device.scopes, name=device.name,
                         session_id=session.id)

    @staticmethod
    def _extract_bearer(authorization: str | None) -> str | None:
        if not authorization:
            return None
        parts = authorization.split(" ", 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return None
        return parts[1].strip() or None


# --- процессный синглтон активного сервиса ---

_ACTIVE: DeviceService | None = None


def get_service() -> DeviceService:
    """Активный сервис. По умолчанию — in-memory (dev/tests); подсистема на
    validate() подменяет на Postgres-версию, если БД доступна."""
    global _ACTIVE
    if _ACTIVE is None:
        _ACTIVE = DeviceService(InMemoryDeviceStore())
    return _ACTIVE


def set_service(service: DeviceService) -> None:
    global _ACTIVE
    _ACTIVE = service


def reset_service() -> None:
    """Только для тестов: сбросить синглтон (следующий get_service поднимет фейк)."""
    global _ACTIVE
    _ACTIVE = None
