"""V2.1 фаза N — серверные сессии UI вместо вечного токена в localStorage.

Было: токен лежал в localStorage и уезжал в query-строку WebSocket — то есть
попадал в логи прокси и в историю. Стало: при логине создаётся серверная сессия,
браузер получает HttpOnly-cookie (JS её не читает), а изменяющие запросы
подтверждаются CSRF-токеном из той же сессии.

Заголовок X-BCC-Token остаётся для CLI и скриптов (settings.legacy_token_auth) —
он не подвержен CSRF: браузер не поставит произвольный заголовок кросс-доменно,
а CORS мы не включаем.
"""
from __future__ import annotations

import secrets
from datetime import timedelta

import sqlalchemy as sa

from .db import Database, fetch_one, sessions as sessions_t, utcnow

COOKIE_NAME = "bcc_session"
CSRF_HEADER = "X-BCC-CSRF"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class SessionStore:
    def __init__(self, db: Database, ttl_hours: int = 720):
        self.db = db
        self.ttl = timedelta(hours=max(1, ttl_hours))

    async def create(self, label: str = "ui") -> dict:
        sid = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        now = utcnow()
        async with self.db.session() as s:
            await s.execute(sa.insert(sessions_t).values(
                id=sid, csrf=csrf, label=label[:120], created_at=now, last_seen=now,
                expires_at=now + self.ttl, revoked=False))
            await s.commit()
        return {"id": sid, "csrf": csrf, "expires_at": now + self.ttl}

    async def get(self, sid: str | None) -> dict | None:
        """Живая сессия или None. Просроченная/отозванная не считается живой."""
        if not sid:
            return None
        async with self.db.session() as s:
            row = await fetch_one(s, sessions_t, sid)
        if row is None or row.get("revoked"):
            return None
        expires = row.get("expires_at")
        if expires is not None and expires <= utcnow():
            return None
        return row

    async def touch(self, sid: str) -> None:
        async with self.db.session() as s:
            await s.execute(sa.update(sessions_t).where(sessions_t.c.id == sid).values(
                last_seen=utcnow()))
            await s.commit()

    async def revoke(self, sid: str) -> bool:
        async with self.db.session() as s:
            res = await s.execute(sa.update(sessions_t).where(sa.and_(
                sessions_t.c.id == sid, sessions_t.c.revoked.is_(False))).values(revoked=True))
            await s.commit()
        return bool(res.rowcount)

    async def revoke_all(self) -> int:
        async with self.db.session() as s:
            res = await s.execute(sa.update(sessions_t).where(
                sessions_t.c.revoked.is_(False)).values(revoked=True))
            await s.commit()
        return int(res.rowcount or 0)

    async def purge_expired(self) -> int:
        async with self.db.session() as s:
            res = await s.execute(sa.delete(sessions_t).where(
                sessions_t.c.expires_at <= utcnow()))
            await s.commit()
        return int(res.rowcount or 0)

    async def list(self, limit: int = 50) -> list[dict]:
        """Только метаданные: ни sid, ни csrf наружу не отдаём."""
        async with self.db.session() as s:
            rows = (await s.execute(sa.select(
                sessions_t.c.label, sessions_t.c.created_at, sessions_t.c.last_seen,
                sessions_t.c.expires_at, sessions_t.c.revoked)
                .order_by(sessions_t.c.created_at.desc()).limit(limit))).fetchall()
        return [dict(r._mapping) for r in rows]


def cookie_kwargs(request_url_scheme: str, mode: str, ttl_hours: int) -> dict:
    """Параметры Set-Cookie. `Secure` по HTTP ставить нельзя — браузер выбросит
    cookie, и локальный доступ (localhost / Tailscale по http) сломается."""
    if mode == "always":
        secure = True
    elif mode == "never":
        secure = False
    else:
        secure = request_url_scheme == "https"
    return {"httponly": True, "samesite": "strict", "secure": secure, "path": "/",
            "max_age": max(1, ttl_hours) * 3600}
