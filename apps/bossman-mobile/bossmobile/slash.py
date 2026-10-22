"""Phone-side allowlist projection for the «/» menu and the context gauge (owner 21.10).

Facts for the gate: importer is bossmobile/api.py only (routes /m/api/agent/palette, /m/api/agent/sessions/{id}/context,
.../compact, .../memory). Public API: palette_view, context_view, compact_view, memory_view (raw upstream JSON -> plain
dict). Schema is the BCC contract in command-center/bcc/features/agent_extras.py; owner rule 21.10 verbatim:
«Запрещено читать локальные переписки данные и фото».

The gateway never forwards an upstream object as it is: only the documented keys with the documented types leave this
module, so a chat turn, a summary text or a prompt that an upstream bug put into one of these answers cannot reach the
phone (the numbers, ids and enums of the gauge need no free text). Everything is clipped; wrong types become None.
"""
from __future__ import annotations

from typing import Any

LEVELS = {"ok", "warn", "critical", "unknown"}
SOURCES = {"provider_usage", "estimate"}
COMPACT_STATES = {"idle", "running", "done", "failed"}
MAX_COMMANDS, MAX_TOOLS, MAX_SKILLS = 100, 400, 400


def _s(v: Any, n: int) -> str:
    return v.strip()[:n] if isinstance(v, str) else ""


def _sid(v: Any, n: int = 80) -> str | int | None:
    """An id that may arrive as a string or a number (task ids are ints in Bossman)."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    return _s(v, n) or None


def _i(v: Any) -> int | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return int(v) if v == v and abs(v) < 10**12 else None


def _f(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return round(float(v), 2) if v == v and abs(v) < 10**12 else None


def _rows(src: dict, key: str, cap: int, build) -> list[dict]:
    items = src.get(key)
    out: list[dict] = []
    if isinstance(items, list):
        for it in items[:cap]:
            if isinstance(it, dict):
                row = build(it)
                if row is not None:
                    out.append(row)
    return out


def _command(it: dict) -> dict | None:
    name = _s(it.get("name"), 64).lstrip("/")
    if not name:
        return None
    return {"name": name, "usage": _s(it.get("usage"), 200), "help": _s(it.get("help"), 400),
            "kind": _s(it.get("kind"), 16) or "message"}


def _tool(it: dict) -> dict | None:
    name = _s(it.get("name"), 80)
    if not name:
        return None
    return {"name": name, "group": _s(it.get("group"), 40), "description": _s(it.get("description"), 400),
            "risk": _s(it.get("risk"), 24), "needs_approval": it.get("needs_approval") is True,
            "effect": _s(it.get("effect"), 24)}


def _skill(it: dict) -> dict | None:
    sid = _s(it.get("id"), 80)
    if not sid:
        return None
    trig = it.get("triggers")
    if isinstance(trig, str):
        trig = [trig]
    triggers = [_s(t, 60) for t in trig[:8] if isinstance(t, str) and t.strip()] if isinstance(trig, list) else []
    return {"id": sid, "title": _s(it.get("title"), 200), "status": _s(it.get("status"), 40), "triggers": triggers}


def palette_view(raw: Any) -> dict:
    src = raw if isinstance(raw, dict) else {}
    agent = src.get("agent")
    if isinstance(agent, dict):      # scalars only: a nested object or a long string is where text could hide
        agent = {_s(k, 40): (_s(v, 200) if isinstance(v, str) else v) for k, v in agent.items()
                 if isinstance(k, str) and isinstance(v, (str, int, float, bool))}
    else:
        agent = None
    return {"commands": _rows(src, "commands", MAX_COMMANDS, _command), "tools": _rows(src, "tools", MAX_TOOLS, _tool),
            "skills": _rows(src, "skills", MAX_SKILLS, _skill), "agent": agent, "generated_at": _s(src.get("generated_at"), 40)}


def context_view(raw: Any) -> dict:
    src = raw if isinstance(raw, dict) else {}
    cmp_ = src.get("compact") if isinstance(src.get("compact"), dict) else {}
    level = src.get("level")
    source = src.get("source")
    state = cmp_.get("state")
    return {
        "session_id": _s(src.get("session_id"), 64),
        "turns_total": _i(src.get("turns_total")), "turns_in_context": _i(src.get("turns_in_context")),
        "compacted": src.get("compacted") is True, "compacted_at_turn": _i(src.get("compacted_at_turn")),
        "summary_chars": _i(src.get("summary_chars")),
        "used_tokens": _i(src.get("used_tokens")), "context_window": _i(src.get("context_window")),
        "used_pct": _f(src.get("used_pct")), "left_tokens": _i(src.get("left_tokens")), "left_pct": _f(src.get("left_pct")),
        "level": level if level in LEVELS else "unknown", "source": source if source in SOURCES else None,
        "compact": {"state": state if state in COMPACT_STATES else "idle", "task_id": _sid(cmp_.get("task_id")),
                    "finished_at": _s(cmp_.get("finished_at"), 40) or None, "error": _s(cmp_.get("error"), 300) or None},
    }


def compact_view(raw: Any) -> dict:
    src = raw if isinstance(raw, dict) else {}
    state = src.get("status")
    return {"session_id": _s(src.get("session_id"), 64), "task_id": _sid(src.get("task_id")),
            "status": state if state in COMPACT_STATES else "running"}


def memory_view(raw: Any) -> dict:
    src = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {"ok": src.get("ok") is True, "chars": _i(src.get("chars"))}
    if _s(src.get("path"), 300):
        out["path"] = _s(src.get("path"), 300)
    if _sid(src.get("note_id")) is not None:
        out["note_id"] = _sid(src.get("note_id"))
    return out


UPDATE_KINDS = {"new_tool", "practiced_tool", "failed_tool", "refusal", "delegation", "compaction"}
MAX_UPDATE_ITEMS = 30


def _update_item(it: dict) -> dict | None:
    kind = _s(it.get("kind"), 24)
    text = _s(it.get("text"), 400)
    if kind not in UPDATE_KINDS or not text:
        return None
    ids = it.get("task_ids")
    task_ids = [i for i in (ids[:20] if isinstance(ids, list) else []) if isinstance(i, int) and not isinstance(i, bool)]
    out: dict[str, Any] = {"kind": kind, "text": text, "count": _i(it.get("count")) or 0, "task_ids": task_ids}
    if _s(it.get("tool"), 80):
        out["tool"] = _s(it.get("tool"), 80)
    if _s(it.get("state"), 32):
        out["state"] = _s(it.get("state"), 32)
    return out


def updates_view(raw: Any) -> dict:
    """/updates: first-person report of what Bossman applied and verified in this session. Only documented keys leave."""
    src = raw if isinstance(raw, dict) else {}
    counts = src.get("counts") if isinstance(src.get("counts"), dict) else {}
    stm = src.get("statements")
    return {
        "session_id": _s(src.get("session_id"), 64),
        "statements": [_s(x, 400) for x in (stm[:MAX_UPDATE_ITEMS + 1] if isinstance(stm, list) else []) if _s(x, 400)],
        "learned": _rows(src, "learned", MAX_UPDATE_ITEMS, _update_item),
        "counts": {k: _i(counts.get(k)) or 0 for k in ("tasks_considered", "tools_used", "new_tools",
                                                       "delegations_verified", "refusals")},
        "disclaimer": _s(src.get("disclaimer"), 400),
    }
