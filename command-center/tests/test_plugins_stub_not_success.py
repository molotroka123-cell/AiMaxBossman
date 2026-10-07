"""Плагин-заглушка не имеет права выглядеть как выполненное действие.

Дефект (zone plugins, 2026-10-06): 18 из 23 капабилити (`mcp.*`, `ollama.chat`,
`openrouter.chat`, `github.*`, `gmail.*`, `calendar.*`, `drive.*`,
`telegram.*`, `n8n.*`, `browser.*`) обслуживает общий хендлер, который НИЧЕГО
не делает. При наличии креда (а у `mcp`/`ollama`/`browser` кред не нужен вовсе)
он возвращал `error=False` и `data={"ready": True}`. Движок и модель видели
успешный вызов: после одобрения владельцем `gmail.send` модель получала
«не ошибку» и могла доложить «письмо отправлено», хотя отправки не было.
Невыполненное действие обязано быть ошибкой с явной причиной.
"""
from __future__ import annotations

import pytest

import bcc.features.plugins as P
from bcc.tools import REGISTRY

REAL = {"plugin:http.get", "plugin:monitor.feed", "plugin:sql.read",
        "plugin:obsidian.read", "plugin:obsidian.write",
        # real handlers since 2026-10-06 (see test_leaf_plugins_real_handlers.py)
        "plugin:ollama.chat", "plugin:openrouter.chat", "plugin:github.repo_read", "plugin:mcp.tool_list",
        "plugin:telegram.status"}
GENERIC = [c for c in P.MANIFEST if c.tool_name not in REAL]


@pytest.fixture(autouse=True)
async def registered(monkeypatch):
    for cap in P.MANIFEST:
        if cap.credential_ref:
            monkeypatch.setenv(cap.credential_ref, "dummy-credential-value")
    await P.setup(None)
    yield


def _ctx():
    return type("C", (), {"svc": None, "task": {}, "run_id": 1, "agent": {},
                          "workspace": "", "call_id": "c", "step": 0})()


def test_generic_set_is_what_we_think():
    assert len(GENERIC) == 13 and len(P.MANIFEST) == 23


@pytest.mark.parametrize("cap", GENERIC, ids=[c.tool_name for c in GENERIC])
async def test_unperformed_action_is_an_error(cap):
    args = {k: "x" for k in cap.required}
    res = await REGISTRY.get(cap.tool_name).handler(args, _ctx())
    assert res.error is True, res.content
    assert "NOT_TESTED_LIVE" in res.content
    assert (res.data or {}).get("performed") is False
