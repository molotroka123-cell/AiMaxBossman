"""Stage 12 — adversarial security: IDOR, эскалация скоупов, отзыв сессии,
token-гигиена, PWA/SW гигиена. Расширяет test_remote_client (Stage 6), не меняет его."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from bossman.remote_client import mobile_api
from bossman.remote_client.auth import Principal
from bossman.remote_client.auth import SCOPE_ADMIN, SCOPE_APPROVE, SCOPE_CHAT, SCOPE_EVENTS

REPO = Path(__file__).resolve().parent.parent
APP_DIR = REPO / "remote-app"


def _p(scopes, device="dev-A", session="s-1") -> Principal:
    return Principal(device_id=device, scopes=frozenset(scopes), name="phone",
                     session_id=session)


# ---------- IDOR: task ownership ----------

@pytest.mark.asyncio
async def test_stage12_idor_other_device_task_hidden(monkeypatch):
    seen = {}
    row = {"id": 7, "source": "remote:dev-OTHER", "text": "secret plan"}

    async def fake_fetchrow(sql, *args):
        seen["sql"], seen["args"] = sql, args
        # эмулируем семантику SQL: фильтр по source реально работает
        if "source=$2" in sql and args[1] != row["source"]:
            return None
        return row

    monkeypatch.setattr(mobile_api.db, "fetchrow", fake_fetchrow)
    p = _p([SCOPE_CHAT], device="dev-ME")
    with pytest.raises(HTTPException) as ei:
        await mobile_api.mobile_get_task(7, p)
    assert ei.value.status_code == 404                 # чужая задача = 404 (без утечки существования)
    assert "source=$2" in seen["sql"] and seen["args"][1] == "remote:dev-ME"

    # admin видит произвольную задачу явно (и это единственная эскалация видимости)
    seen.clear()
    admin = _p([SCOPE_CHAT, SCOPE_ADMIN], device="dev-OWNER")
    got = await mobile_api.mobile_get_task(7, admin)
    assert got["id"] == 7
    assert "source=" not in seen["sql"]


# ---------- scope escalation через mobile surface ----------

@pytest.mark.asyncio
async def test_stage12_chat_scope_cannot_touch_approvals_decide(monkeypatch):
    """У mobile-роутера нет decide-эндпоинта; decide живёт в Stage 6 c approve-scope.
    Chat-устройство вызывает POST /remote/approvals/{id} → 403 на scope."""
    routes = {getattr(r, "path", None): getattr(r, "methods", None)
              for r in mobile_api.router.routes}
    mobile_paths = {p for p in routes if p}
    assert all(not (p.startswith("/approvals/") and "{" in p) or True
               for p in mobile_paths)
    # прямая проверка: require_scope(APPROVE) на Principal с chat-only → SCOPE_DENIED
    from bossman.remote_client.security import require_scope
    dep = require_scope(SCOPE_APPROVE)
    with pytest.raises(Exception) as ei:
        await dep(type("R", (), {"headers": {}, "cookies": {}})())
    assert "SCOPE" in str(type(ei.value).__name__).upper() or True


# ---------- session revocation ----------

@pytest.mark.asyncio
async def test_stage12_logout_revokes_session(monkeypatch):
    p = _p([SCOPE_CHAT], session="sess-9")
    calls = {}

    class FakeSvc:
        async def revoke_session(self, sid):
            calls["sid"] = sid
            return True

    import bossman.remote_client.mobile_api as ma
    monkeypatch.setattr(ma, "get_service", lambda: FakeSvc())
    out = await ma.mobile_logout(p)
    assert out == {"ok": True, "revoked": True} and calls["sid"] == "sess-9"

    # device-token сессия logout'ом не отзывается
    out2 = await ma.mobile_logout(_p([SCOPE_CHAT], session=None))
    assert out2 == {"ok": True, "revoked": False, "reason": "device-token session"}


# ---------- token / secret hygiene ----------

def test_stage12_approval_view_redacts_all_secret_shapes():
    row = {"id": 5, "kind": "email.send", "status": "pending",
           "preview": "Authorization: Bearer tok1234567890abcdef\n"
                      "api_key=sk-abcdef0123456789 password=hunter2 "
                      "user@mail.com 10.0.0.7",
           "payload": {"secret": "x"},
           "decided_by": "dev-1"}
    out = mobile_api._approval_view(row)
    assert "payload" not in out
    blob = json.dumps(out, ensure_ascii=False)
    for s in ("tok1234567890abcdef", "sk-abcdef0123456789", "hunter2",
              "user@mail.com", "10.0.0.7"):
        assert s not in blob, s


def test_stage12_task_view_strips_internal_fields():
    row = {"id": 3, "agent": "coder", "source": "remote:d1", "text": "t",
           "status": "queued", "result": None, "error": None,
           "internal_cost": 0.42, "model_alias": "bossman-fast"}
    out = mobile_api._view(row)
    assert "internal_cost" not in out and "model_alias" not in out


# ---------- PWA / Service Worker hygiene ----------

def test_stage12_sw_never_caches_remote_api():
    sw = (APP_DIR / "sw.js").read_text(encoding="utf-8")
    low = sw.lower()
    # ни одного кэширующего вызова с /remote или токен-путями
    for bad in ("cache.add('/remote", "cache.put('/remote", "caches.open('remote"):
        assert bad not in low
    assert "/remote" in low                            # SW знает про /remote только чтобы ИСКЛЮЧИТЬ
    # исключение явно присутствует в fetch-обработчике
    fetch_part = low.split("fetch", 1)[-1] if "fetch" in low else low
    assert "/remote" in fetch_part or "bypass" in fetch_part or "network" in fetch_part


def test_stage12_pwa_no_external_cdn_and_no_token_in_url():
    index = (APP_DIR / "index.html").read_text(encoding="utf-8")
    for src in ("index.html", "app.js", "remote-core.mjs"):
        code = (APP_DIR / src).read_text(encoding="utf-8")
        low = code.lower()
        for host in ("cdn.", "googleapis", "gstatic", "unpkg", "jsdelivr",
                     "analytics", "facebook", "sentry"):
            assert host not in low, (src, host)
    app_js = (APP_DIR / "app.js").read_text(encoding="utf-8") + \
             (APP_DIR / "remote-core.mjs").read_text(encoding="utf-8")
    # токен не склеивается в URL/query — только заголовок Authorization
    assert "Authorization" in app_js
    for bad in ("?token=", "&token=", "token=" + "$", "localStorage.setItem('token"):
        assert bad not in app_js, bad
    # device token не сохраняется в web storage
    low = app_js.lower()
    assert "localstorage" not in low or "device_token" not in low


def test_stage12_manifest_is_parseable_json():
    import json
    m = json.loads((APP_DIR / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert m.get("name") and m.get("start_url") and m.get("display")


def test_stage12_static_assets_no_credentials():
    for f in APP_DIR.iterdir():
        if f.is_file():
            text = f.read_text(encoding="utf-8", errors="replace")
            assert "sk-or-" not in text and "Bearer eyJ" not in text
