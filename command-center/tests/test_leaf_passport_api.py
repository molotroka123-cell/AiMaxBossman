"""authored_by_lane jeffb: Jeff passport window API (bcc.pit.passport_api) on a real PersonaVault."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from bcc.pit import passport_api
from bcc.pit.models import ConsentState
from bcc.pit.vault import PersonaVault


class _Rt:
    def __init__(self, vault):
        self.vault = vault
        self.invalidated: list[str] = []

    def invalidate_memory(self, key):
        self.invalidated.append(key)


def _client(tmp_path, signed_in=True):
    vault = PersonaVault(tmp_path, b"s" * 32)
    rt = _Rt(vault)
    app = FastAPI()

    async def body_json(request: Request):
        return await request.json()

    passport_api.register(
        app, rt=rt, own_key=vault.key_for_telegram,
        user_of=lambda request: 7 if signed_in else None,
        body_json=body_json,
        error=lambda status, code: JSONResponse({"error": code}, status_code=status))
    return TestClient(app), vault, rt


def test_requires_sign_in(tmp_path):
    c, _, _ = _client(tmp_path, signed_in=False)
    assert c.get("/api/jeff/passport").status_code == 401
    assert c.post("/api/jeff/passport/consent", json={"action": "revoke"}).status_code == 401


def test_view_shows_schema_and_no_facts_without_consent(tmp_path):
    c, vault, _ = _client(tmp_path)
    data = c.get("/api/jeff/passport").json()
    assert data["schema"] == "jeff.passport/1"
    assert data["facts"] == []
    assert set(data["consent"]) >= {"memory_enabled", "personalization_enabled"}


def test_unknown_action_rejected_and_personalization_toggle_works(tmp_path):
    c, vault, _ = _client(tmp_path)
    assert c.post("/api/jeff/passport/consent", json={"action": "bogus"}).status_code == 400
    off = c.post("/api/jeff/passport/consent", json={"action": "personalization_off"}).json()
    assert off["ok"] is True and off["consent"]["personalization_enabled"] is False
    # turning personalization back on never re-grants memory consent
    refused = c.post("/api/jeff/passport/consent", json={"action": "personalization_on"}).json()
    assert refused["ok"] is False and refused["consent"]["personalization_enabled"] is False
    vault.set_consent(vault.key_for_telegram(7), ConsentState(memory_enabled=True, personalization_enabled=False))
    on = c.post("/api/jeff/passport/consent", json={"action": "personalization_on"}).json()
    assert on["ok"] is True and on["consent"]["personalization_enabled"] is True


def test_revoke_invalidates_memory_cache_and_turns_memory_off(tmp_path):
    c, vault, rt = _client(tmp_path)
    key = vault.key_for_telegram(7)
    vault.set_consent(key, ConsentState(memory_enabled=True))
    assert c.get("/api/jeff/passport").json()["consent"]["memory_enabled"] is True
    res = c.post("/api/jeff/passport/consent", json={"action": "revoke"}).json()
    assert res["ok"] is True and res["consent"]["memory_enabled"] is False
    assert rt.invalidated == [key]
    assert vault.consent(key).memory_enabled is False
