"""Dedicated unit & endpoint tests for `bcc.features.agent_extras` (palette, context gauge, /compact, /memory).

Owner rule 21.10: synthetic data in tmp data_dir only; canary strings planted in turns must never leak in responses.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bcc.features import agent_extras as ax
from bcc.features import tools_cloud as tc
from bcc.features.chat_threads import _append_turn, _new_turn

CANARY = "KANAREYKA-9912-agent-extras-private"


def test_parse_window_variants():
    assert ax._parse_window(131072) == 131072
    assert ax._parse_window("128K") == 131072
    assert ax._parse_window("1M") == 1048576
    assert ax._parse_window("") is None
    assert ax._parse_window(0) is None
    assert ax._parse_window(True) is None


def test_estimate_tokens_and_level_thresholds():
    assert ax.estimate_tokens("") == 0
    assert ax.estimate_tokens("a" * 400) == 100
    assert ax.estimate_tokens("я" * 220) == 100
    assert ax.level_of(None) == "unknown"
    assert ax.level_of(50.0) == "ok"
    assert ax.level_of(70.0) == "warn"
    assert ax.level_of(90.0) == "critical"


async def _create_session_with_canary(env) -> str:
    created = await env.client.post("/api/agent/sessions", json={"title": "Extras unit", "mode": "CHAT"})
    assert created.status_code == 201, created.text
    sid = created.json()["id"]
    await _append_turn(env.svc, sid, _new_turn(-201, f"синтетический ход {CANARY}", []))
    return sid


async def test_palette_endpoint_never_leaks_session_turns(env):
    sid = await _create_session_with_canary(env)
    res = await env.client.get("/api/agent/palette", params={"session_id": sid})
    assert res.status_code == 200, res.text
    payload = res.json()
    assert {"commands", "tools", "skills", "agent", "counts"} <= set(payload)
    assert CANARY not in res.text


async def test_context_gauge_endpoint_and_compact_guards(env, monkeypatch):
    sid = await _create_session_with_canary(env)
    res = await env.client.get(f"/api/agent/sessions/{sid}/context")
    assert res.status_code == 200, res.text
    ctx = res.json()
    assert ctx["session_id"] == sid
    assert ctx["turns_total"] == 1
    assert CANARY not in res.text

    monkeypatch.setattr(tc, "stop_reason", lambda svc: "owner STOP")
    r_stop = await env.client.post(f"/api/agent/sessions/{sid}/compact", json={})
    assert r_stop.status_code == 409
    assert r_stop.json()["error"]["code"] == "AGENT_STOP_ACTIVE"


async def test_compact_state_persistence_in_data_dir(env):
    sid = "20261022-010203-fedcba"
    ax._write_compact_state(env.svc, sid, {"state": "done", "task_id": 42, "finished_at": "2026-10-22T00:00:00Z", "error": None})
    view = ax._compact_view(env.svc, sid)
    assert view["state"] == "done" and view["task_id"] == 42
    assert ax._compact_state_path(env.svc).is_relative_to(Path(env.settings.data_dir))
