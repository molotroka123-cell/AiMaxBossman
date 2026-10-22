"""/updates on the phone: the gateway keeps only the documented keys, clips text, rejects foreign kinds; the UI speaks in first person.

Owner rule 21.10: no chats/photos are read - every private string below is a synthetic canary.
"""
from __future__ import annotations

import pathlib

from bossmobile import slash

ROOT = pathlib.Path(__file__).resolve().parents[1]
CANARY = "CANARY-PRIVATE-CHAT-TURN-upd-5521"


def test_updates_view_keeps_only_documented_keys_and_clips_text():
    raw = {
        "session_id": "S1", "statements": ["Вот что я усвоил:", "x" * 900],
        "learned": [
            {"kind": "new_tool", "tool": "code.search", "count": 2, "task_ids": [5, True, "7", 9], "text": "Я впервые применил `code.search`.",
             "turns": [{"text": CANARY}], "args": {"secret": CANARY}},
            {"kind": "sneaky_kind", "text": "Я что-то выдумал"},                 # чужой kind отбрасывается
            {"kind": "refusal", "text": ""},                                      # без текста — нет утверждения
            {"kind": "delegation", "state": "FAILED_VERIFICATION", "count": 1, "task_ids": [11], "text": "Я поручил задачу, проверка не подтвердила."},
            "not a dict",
        ],
        "counts": {"tools_used": 1, "new_tools": 1, "delegations_verified": "many", "refusals": True, "extra": 5},
        "disclaimer": "d" * 900, "raw_history": [CANARY],
    }
    out = slash.updates_view(raw)
    assert set(out) == {"session_id", "statements", "learned", "counts", "disclaimer"}
    assert [x["kind"] for x in out["learned"]] == ["new_tool", "delegation"]
    assert set(out["learned"][0]) == {"kind", "text", "count", "task_ids", "tool"}
    assert out["learned"][0]["task_ids"] == [5, 9]                                # bool и строка — не id задач
    assert out["learned"][1]["state"] == "FAILED_VERIFICATION"
    assert all(len(s) <= 400 for s in out["statements"]) and len(out["disclaimer"]) == 400
    assert out["counts"] == {"tasks_considered": 0, "tools_used": 1, "new_tools": 1, "delegations_verified": 0, "refusals": 0}
    assert CANARY not in repr(out)


def test_updates_view_survives_garbage_upstream():
    for junk in (None, [], "text", 7, {"learned": "oops", "statements": 5, "counts": []}):
        out = slash.updates_view(junk)
        assert out["learned"] == [] and out["statements"] == [] and out["disclaimer"] == ""


def test_route_is_wired_through_the_allowlist_and_ui_speaks_first_person():
    api_src = (ROOT / "bossmobile" / "api.py").read_text(encoding="utf-8")
    assert '"/m/api/agent/sessions/{session_id}/updates"' in api_src and "slash.updates_view(" in api_src
    js = (ROOT / "bossmobile" / "ui" / "app.js").read_text(encoding="utf-8")
    assert "updates: () => cardLoad(" in js and "/updates`" in js
    assert "name: 'updates'" in js                                                # работает и без палитры (offline)
