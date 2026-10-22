"""agent_extras (palette, context gauge, /compact, /memory) + tools_cloud (`cloud.ask`): synthetic data only.

Owner rule 21.10: no reading of local chats/data/photos. Every test here builds its own session in a tmp data dir and
plants a CANARY string in a synthetic turn: the canary must never appear in any palette/context response, in the cloud
audit log or in what the fake transport receives beyond the explicit prompt.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bcc import conversation_context
from bcc.features import agent_extras as ax
from bcc.features import tools_cloud as tc
from bcc.features.chat_threads import _append_turn, _new_turn
from bcc.tools import ToolContext

CANARY = "KANAREYKA-7731-private-note"
FAKE_KEY = "sk-" + "a1B2c3D4e5F6g7H8i9J0" * 2


# --------------------------------------------------------------------------------------------- pure: estimate/level

def test_estimate_tokens_counts_cyrillic_denser_than_ascii():
    ascii_text = "x" * 440
    ru_text = "ж" * 440
    assert ax.estimate_tokens("") == 0
    assert ax.estimate_tokens(ascii_text) == 110
    assert ax.estimate_tokens(ru_text) == 200          # chars/4 would have said 110 and under-reported Russian
    assert ax.estimate_tokens(ru_text) > ax.estimate_tokens(ascii_text)


@pytest.mark.parametrize("pct,level", [(None, "unknown"), (0, "ok"), (69.9, "ok"), (70, "warn"), (89.9, "warn"),
                                       (90, "critical"), (140, "critical")])
def test_level_thresholds(pct, level):
    assert ax.level_of(pct) == level


# ------------------------------------------------------------------------------------- pure: cloud.ask static policy

def _args(prompt: str, **extra) -> dict:
    return {"prompt": prompt, **extra}


@pytest.mark.parametrize("args,code", [
    ({"prompt": ""}, "CLOUD_ASK_EMPTY"),
    ({"prompt": "   "}, "CLOUD_ASK_EMPTY"),
    ("not-a-dict", "CLOUD_ASK_EMPTY"),
    ({"prompt": "x" * (tc.MAX_PROMPT_CHARS + 1)}, "CLOUD_ASK_TOO_LONG"),
    ({"prompt": "question", "model": "runpod-h100"}, "CLOUD_ASK_RUNPOD"),
    ({"prompt": conversation_context.HEADER + "Владелец: привет\nBossman: ответ"}, "CLOUD_ASK_CONTEXT_LEAK"),
    ({"prompt": "вот переписка:\nВладелец: как дела\nBossman: нормально\nчто думаешь?"}, "CLOUD_ASK_CONTEXT_LEAK"),
    ({"prompt": "Вложение «отчёт.pdf» прилагаю"}, "CLOUD_ASK_CONTEXT_LEAK"),
    ({"prompt": "see chat/attachments/abc123/file.txt"}, "CLOUD_ASK_CONTEXT_LEAK"),
    ({"prompt": "look at C:\\Users\\me\\Pictures\\IMG_0001.jpg please"}, "CLOUD_ASK_MEDIA_PATH"),
    ({"prompt": "video /tmp/clip.mp4 is broken"}, "CLOUD_ASK_MEDIA_PATH"),
    ({"prompt": "data:image/png;base64,AAAA"}, "CLOUD_ASK_MEDIA_PATH"),
    ({"prompt": "ж" * 1900}, "CLOUD_ASK_PREVIEW_TOO_LONG"),       # fits MAX_PROMPT_CHARS but not the approval window
])
def test_static_denial_blocks_leaks_media_runpod_and_oversize(args, code):
    reason = tc.static_denial(args)
    assert reason is not None and reason.startswith(code + ":"), reason
    assert tc._effect(args)[0] == "deny"


def test_static_denial_accepts_plain_question_and_effect_is_always_ask_never_auto():
    args = _args("Объясни разницу между TCP и UDP в трёх предложениях.", reason="владелец попросил облако")
    assert tc.static_denial(args) is None
    effect, _why = tc._effect(args)
    assert effect == "ask"
    assert tc.SPECS[0].default_effect == "ask" and tc.SPECS[0].hook_is_floor is True
    assert tc.SPECS[0].external_output is True and tc.SPECS[0].idempotent is False


def test_outgoing_text_redacts_secret_and_keeps_the_question():
    text = tc.outgoing_text(_args(f"My key is {FAKE_KEY} - is this format valid?"))
    assert FAKE_KEY not in text
    assert "is this format valid?" in text


def test_normalize_args_puts_route_first_and_prompt_last():
    route = {"model": "m", "provider": "p", "paid": True, "chars_out": 5, "est_cost_usd": 0.01, "budget_remaining_usd": 1.0}
    out = tc.normalize_args({"prompt": "hello", "reason": "  why   this  ", "_cloud": route})
    assert list(out)[:2] == ["model", "provider"] and list(out)[-1] == "prompt"
    assert out["reason"] == "why this" and out["prompt"] == "hello"
    assert tc.normalize_args({"prompt": "hello"})["route"] == "не привязан"


def _cand(**kw) -> dict:
    base = {"id": "free-a", "tier_id": "worker:free-a", "tier": 3, "model": "vendor/free-a", "label": "Free A",
            "provider": "Vendor", "endpoint": "https://api.vendor.example/v1", "paid": False, "ready": True,
            "price_in_1m": 0.0, "price_out_1m": 0.0}
    base.update(kw)
    return base


def test_choose_prefers_strongest_ready_within_budget_and_refuses_runpod(monkeypatch):
    monkeypatch.setattr(tc, "_governance_refusal", lambda c: "")
    cands = [_cand(id="paid-strong", tier=4, model="vendor/strong", paid=True, price_in_1m=3.0, price_out_1m=15.0),
             _cand(id="runpod-x", tier=5, model="runpod/gpu", endpoint="https://runpod.io/v1"),
             _cand(id="free-a"), _cand(id="free-off", ready=False)]
    cand, denied = tc.choose(cands, requested=None, tokens_in=100, remaining_usd=5.0)
    assert denied is None and cand["id"] == "paid-strong"
    cand, denied = tc.choose(cands, requested=None, tokens_in=100, remaining_usd=0.0)
    assert denied is None and cand["id"] == "free-a"               # paid over budget -> free fallback, no silent paid
    cand, denied = tc.choose(cands, requested="runpod-x", tokens_in=1, remaining_usd=99)
    assert cand is None and denied.startswith("CLOUD_ASK_RUNPOD")
    cand, denied = tc.choose(cands, requested="free-off", tokens_in=1, remaining_usd=99)
    assert cand is None and denied.startswith("CLOUD_ASK_NO_MODEL")
    cand, denied = tc.choose(cands, requested="nope", tokens_in=1, remaining_usd=99)
    assert cand is None and denied.startswith("CLOUD_ASK_UNKNOWN_MODEL")
    cand, denied = tc.choose([_cand(id="paid-only", paid=True, price_in_1m=1.0, price_out_1m=1.0)],
                             requested=None, tokens_in=100, remaining_usd=0.0)
    assert cand is None and denied.startswith("CLOUD_ASK_NO_BUDGET")


def test_estimate_cost_none_without_price():
    assert tc.estimate_cost(_cand(price_in_1m=None), 10) is None
    assert tc.estimate_cost(_cand(price_in_1m=1.0, price_out_1m=2.0), 1_000_000, 0) == 1.0


# ------------------------------------------------------------------------------------------ handler: gates + transport

def _ctx(svc, approval_id):
    return ToolContext(svc=svc, task={"id": None}, run_id=0, agent={}, approval_id=approval_id)


def _async_const(value):
    async def _f(*_a, **_k):
        return value
    return _f


async def test_handler_refuses_without_approval_unbound_and_changed(env):
    r = await tc._t_ask(_args("q"), _ctx(env.svc, None))
    assert r.error and "CLOUD_ASK_NO_APPROVAL" in r.content
    r = await tc._t_ask(_args("q"), _ctx(env.svc, 7))
    assert r.error and "CLOUD_ASK_UNBOUND" in r.content
    r = await tc._t_ask({"prompt": "changed", "_cloud": {"prompt_sha256": "0" * 64, "model": "m"}}, _ctx(env.svc, 7))
    assert r.error and "CLOUD_ASK_CHANGED" in r.content


async def test_handler_sends_only_redacted_prompt_and_audits_without_text(env, monkeypatch):
    seen: list[dict] = []
    cand = _cand()
    monkeypatch.setattr(tc, "cloud_catalog", _async_const([cand]))
    monkeypatch.setattr(tc, "_governance_refusal", lambda c: "")
    monkeypatch.setattr(tc, "stop_reason", lambda svc: "")

    async def fake_transport(svc, route, messages, *, max_tokens, timeout):
        seen.append({"route": route, "messages": messages, "max_tokens": max_tokens})
        return SimpleNamespace(text="Ответ облака: 42", usage={})

    monkeypatch.setattr(tc, "transport", fake_transport)
    args = _args(f"Коротко: сколько будет 6*7? ключ {FAKE_KEY}")
    assert await tc.bind_route(args) is None
    assert args["_cloud"]["model"] == cand["model"]
    r = await tc._t_ask(args, _ctx(env.svc, 11))
    assert not r.error, r.content
    assert r.external is True and r.content.startswith(tc.PROVENANCE.format(model=cand["model"]))
    sent = seen[0]["messages"]
    assert len(sent) == 1 and sent[0]["role"] == "user"
    assert FAKE_KEY not in sent[0]["content"] and "6*7" in sent[0]["content"]
    assert CANARY not in json.dumps(sent, ensure_ascii=False)
    assert seen[0]["max_tokens"] == tc.MAX_ANSWER_TOKENS
    log = tc._audit_path(env.svc).read_text(encoding="utf-8")
    row = json.loads(log.splitlines()[-1])
    assert row["status"] == "ok" and row["approval_id"] == 11 and row["chars_out"] == len(sent[0]["content"])
    for forbidden in ("6*7", FAKE_KEY, "Ответ облака", "prompt", "answer"):
        assert forbidden not in log


async def test_handler_denies_when_stop_is_active(env, monkeypatch):
    monkeypatch.setattr(tc, "cloud_catalog", _async_const([_cand()]))
    monkeypatch.setattr(tc, "_governance_refusal", lambda c: "")
    monkeypatch.setattr(tc, "stop_reason", lambda svc: "owner STOP")
    args = _args("вопрос")
    denied = await tc.bind_route(args)
    assert denied is not None and denied.startswith("CLOUD_ASK_STOP") and "_cloud" not in args


# ----------------------------------------------------------------------------------------- endpoints: palette/context

async def _session_with_canary(env) -> str:
    created = await env.client.post("/api/agent/sessions", json={"title": "Extras test", "mode": "CHAT"})
    assert created.status_code == 201, created.text
    sid = created.json()["id"]
    await _append_turn(env.svc, sid, _new_turn(-101, f"заметка владельца {CANARY}", []))
    return sid


async def test_palette_shape_and_no_chat_text(env):
    sid = await _session_with_canary(env)
    res = await env.client.get("/api/agent/palette", params={"session_id": sid})
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body) >= {"commands", "tools", "skills", "agent", "generated_at", "counts", "warnings"}
    kinds = {c["kind"] for c in body["commands"]}
    assert kinds == {"message", "action", "client"}
    for c in body["commands"]:
        assert c["name"] and c["usage"].startswith("/") and "help" in c and "endpoint" in c
    assert {c["name"] for c in body["commands"]} >= {"code", "pc", "auto", "improve", "context", "compact", "memory",
                                                     "tools", "skills", "models", "status", "stop", "cloud", "new", "help"}
    for t in body["tools"]:
        assert set(t) == {"name", "group", "description", "risk", "needs_approval", "effect"}
        assert len(t["description"]) <= ax.TOOL_DESCRIPTION_CHARS
    assert len(body["tools"]) <= ax.PALETTE_TOOLS_MAX and len(body["skills"]) <= ax.PALETTE_SKILLS_MAX
    for s in body["skills"]:
        assert set(s) == {"id", "title", "status", "triggers"}
    assert CANARY not in res.text


async def test_context_gauge_counts_without_text(env):
    sid = await _session_with_canary(env)
    res = await env.client.get(f"/api/agent/sessions/{sid}/context")
    assert res.status_code == 200, res.text
    body = res.json()
    for key in ("session_id", "turns_total", "turns_in_context", "compacted", "compacted_at_turn", "summary_chars",
                "used_tokens", "context_window", "used_pct", "left_tokens", "left_pct", "level", "source", "compact"):
        assert key in body, key
    assert body["turns_total"] == 1 and body["compacted"] is False and body["summary_chars"] == 0
    assert body["level"] in ("ok", "warn", "critical", "unknown") and body["source"] in ("provider_usage", "estimate")
    assert body["compact"] == {"state": "idle", "task_id": None, "finished_at": None, "error": None}
    if body["context_window"]:
        assert body["used_pct"] == round(100.0 * body["used_tokens"] / body["context_window"], 1)
        assert body["left_tokens"] == max(0, body["context_window"] - body["used_tokens"])
    else:
        assert body["level"] == "unknown" and body["used_pct"] is None
    assert CANARY not in res.text


async def test_context_404_for_unknown_session(env):
    res = await env.client.get("/api/agent/sessions/20260101-000000-000000/context")
    assert res.status_code == 404


# --------------------------------------------------------------------------------------- endpoints: compact / memory

async def test_compact_refuses_when_nothing_to_compact(env):
    created = await env.client.post("/api/agent/sessions", json={"title": "Empty", "mode": "CHAT"})
    sid = created.json()["id"]
    res = await env.client.post(f"/api/agent/sessions/{sid}/compact", json={})
    assert res.status_code == 409 and res.json()["error"]["code"] == "AGENT_NOTHING_TO_COMPACT"


async def test_compact_refuses_while_running_and_under_stop(env, monkeypatch):
    sid = await _session_with_canary(env)
    ax._x(env.svc).running[sid] = {"task_id": 1, "started_at": 0}
    res = await env.client.post(f"/api/agent/sessions/{sid}/compact", json={})
    assert res.status_code == 409 and res.json()["error"]["code"] == "AGENT_COMPACT_RUNNING"
    ax._x(env.svc).running.pop(sid)
    monkeypatch.setattr(tc, "stop_reason", lambda svc: "owner STOP")
    res = await env.client.post(f"/api/agent/sessions/{sid}/compact", json={})
    assert res.status_code == 409 and res.json()["error"]["code"] == "AGENT_STOP_ACTIVE"
    ctx = (await env.client.get(f"/api/agent/sessions/{sid}/context")).json()
    assert ctx["compact"]["state"] == "idle"                      # refused compaction leaves no running state behind


async def test_memory_refuses_without_summary(env):
    sid = await _session_with_canary(env)
    res = await env.client.post(f"/api/agent/sessions/{sid}/memory", json={"note": "x"})
    assert res.status_code == 409 and res.json()["error"]["code"] == "AGENT_NO_SUMMARY"


async def test_compact_state_file_roundtrip(env):
    sid = "20260102-030405-abcdef"
    ax._write_compact_state(env.svc, sid, {"state": "failed", "task_id": 5, "finished_at": "t", "error": "boom"})
    assert ax._compact_view(env.svc, sid) == {"state": "failed", "task_id": 5, "finished_at": "t", "error": "boom"}
    assert ax._compact_view(env.svc, "other") == {"state": "idle", "task_id": None, "finished_at": None, "error": None}
    assert ax._compact_state_path(env.svc).is_relative_to(Path(env.settings.data_dir))


async def test_cloud_ask_status_endpoint_lists_catalog_without_keys(env):
    res = await env.client.get("/api/agent/cloud-ask")
    assert res.status_code == 200, res.text
    text = res.text.lower()
    assert "api_key" not in text and "sk-" not in text
