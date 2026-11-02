"""Funds dashboard feed: local providers are free, OpenRouter is probed via /auth/key, keys never leak."""
from __future__ import annotations

import asyncio
import json

import pytest

from bcc.features import funds


class _Resp:
    def __init__(self, status: int, body: dict):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


def _patch_client(monkeypatch, resp: _Resp, seen: dict, credits: _Resp | None = None):
    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, headers=None):
            if url.endswith("/credits"):
                return credits or _Resp(404, {})
            seen["url"] = url
            seen["auth"] = (headers or {}).get("Authorization")
            return resp
    monkeypatch.setattr(funds.httpx, "AsyncClient", FakeClient)


def test_openrouter_probe_reads_limit_remaining(monkeypatch):
    seen: dict = {}
    _patch_client(monkeypatch, _Resp(200, {"data": {"limit": 10.0, "limit_remaining": 4.25}}), seen)
    out = asyncio.run(funds.probe_openrouter("https://openrouter.ai/api/v1", "sk-secret"))
    assert out == {"balance": 4.25, "limit": 10.0, "status": "ok", "note": "",
                   "key_remaining": 4.25, "account_remaining": None}
    assert seen["url"].endswith("/auth/key") and seen["auth"] == "Bearer sk-secret"
    assert "sk-secret" not in json.dumps(out)


def test_openrouter_balance_is_the_account_when_it_is_lower_than_the_key_limit(monkeypatch):
    """02.11.2026: key limit $46 left, account had ~$0.6 -> paid designers got 402 and silently ran on local Qwen."""
    _patch_client(monkeypatch, _Resp(200, {"data": {"limit": 50.0, "limit_remaining": 45.99}}), {},
                  credits=_Resp(200, {"data": {"total_credits": 5.0, "total_usage": 4.4}}))
    out = asyncio.run(funds.probe_openrouter("https://openrouter.ai/api/v1", "sk-secret"))
    assert out["balance"] == 0.6 and out["account_remaining"] == 0.6 and out["key_remaining"] == 45.99
    assert "пополните" in out["note"] and "sk-secret" not in json.dumps(out)


def test_openrouter_probe_401_is_reported_not_raised(monkeypatch):
    _patch_client(monkeypatch, _Resp(401, {"error": {"message": "API key expired."}}), {})
    out = asyncio.run(funds.probe_openrouter("https://openrouter.ai/api/v1", "sk-old"))
    assert out["status"] == "key_invalid" and out["balance"] is None


def test_local_and_unknown_providers_need_no_probe():
    assert funds._is_local("http://127.0.0.1:11434/v1") and funds._is_local("http://localhost:8081/v1")
    assert funds._probe_for("https://openrouter.ai/api/v1") is funds.probe_openrouter
    assert funds._probe_for("https://7api.st/v1") is None
