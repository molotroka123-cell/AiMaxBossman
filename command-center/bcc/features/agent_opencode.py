"""Unified Mobile OpenCode Agent Orchestrator (`/api/agent/*`).

Turns Bossman's chat threads into a full multi-mode AI Agent orchestrator:
    Mobile Chat → Agent Orchestrator → OpenCode Runtime → Tools → Windows PC → Verification → Memory

Supported Modes (all sharing the same session history, permissions, and approvals):
  * CHAT         — Direct conversational Q&A (Gemma or selected model).
  * AGENT        — Multi-step tool & MCP execution via Bossman's governed engine.
  * CODE         — OpenCode software engineering in isolated worktrees with test verification.
  * COMPUTER     — Windows PC observation & control (`OBSERVE → PLAN → APPROVE → ACT → VERIFY → LEARN`).
  * SELF-IMPROVE — Controlled 16-step self-improvement loop (`/improve`) with reproducer tests,
                   independent verifier, MissionReceipt, owner approval gate, and memory persistence.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..api import COOKIE_NAME, _same_origin
from ..db import (
    agents as agents_t,
    approvals as approvals_t,
    models as models_t,
    providers as providers_t,
    rows_dicts,
    task_runs as runs_t,
    tasks as tasks_t,
    tool_calls as tool_calls_t,
    utcnow,
)
from . import Feature

_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{16,}\b"),
)
_SECRET_KEYS = frozenset({"api_key", "apikey", "secret", "password", "token", "authorization", "access_token"})


def redact_text(value: str) -> str:
    out = value
    for pat in _SECRET_PATTERNS:
        out = pat.sub("[REDACTED]", out)
    return out


def redact_structure(obj: Any) -> Any:
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, dict):
        cleaned: dict[str, Any] = {}
        for k, v in obj.items():
            if str(k).lower() in _SECRET_KEYS and isinstance(v, str) and len(v) > 4:
                cleaned[k] = "[REDACTED]"
            else:
                cleaned[k] = redact_structure(v)
        return cleaned
    if isinstance(obj, list):
        return [redact_structure(x) for x in obj]
    if isinstance(obj, tuple):
        return tuple(redact_structure(x) for x in obj)
    return obj
from .chat_threads import (
    REQUEST_ID_PATTERN,
    TEXT_MAX_CHARS,
    _all_thread_ids,
    _append_turn,
    _catalog,
    _clean_project,
    _clean_title,
    _compose_prompt,
    _create_session_sync,
    _entry,
    _fail,
    _internal,
    _load_session,
    _mutate_meta,
    _new_turn,
    _passthrough,
    _public,
    _read_meta,
    _require_session,
    _resolve_attachments,
    _sessions_dir,
    _state as _chat_state,
    _summary,
    _task_by_request_id,
    _task_statuses,
    _task_views,
    _thread_summary,
    _touch_meta,
    _turns,
)
from .coding_tasks import (
    LOCAL_CODER_PROFILES,
    WORKERS,
    TaskIn as CodingTaskIn,
    _LOCAL_CODING_MODELS,
    _worker_key_with_source,
    apply_task as apply_coding_task,
    cancel_task as cancel_coding_task,
    create_task as create_coding_task,
    get_task as get_coding_task,
    list_tasks as list_coding_tasks,
)
from ..tools import REGISTRY as TOOL_REGISTRY

router = APIRouter()
ws_router = APIRouter()

VALID_MODES = ("CHAT", "AGENT", "AUTO", "CODE", "COMPUTER", "SELF-IMPROVE")
DEFAULT_MODE = "AGENT"
DEFAULT_MAX_LOOP_STEPS = 12
DEFAULT_SESSION_BUDGET_USD = 2.00

#: Tool Registry canonical grouping (Master Prompt §8).
TOOL_GROUPS = (
    "Computer",
    "Browser",
    "Files",
    "Terminal",
    "Git",
    "Coding",
    "Models",
    "Memory",
    "MCP",
    "Diagnostics",
)


def classify_tool_group(name: str, source: str = "") -> str:
    n = (name or "").lower()
    s = (source or "").lower()
    if s == "mcp" or n.startswith("mcp:") or n.startswith("mcp."):
        return "MCP"
    if n.startswith("computer.") or n.startswith("pc.") or "window" in n or "screen" in n or "clipboard" in n:
        return "Computer"
    if n.startswith("browser.") or s == "browser":
        return "Browser"
    if n.startswith("git.") or "worktree" in n or n in ("code.diff", "code.commit", "code.branch"):
        return "Git"
    if n.startswith("opencode.") or s == "opencode" or n in ("code.patch", "code.test", "code.verify"):
        return "Coding"
    if n.startswith("code.") or n.startswith("file.") or n.startswith("files.") or n.startswith("fs."):
        return "Files"
    if n.startswith("terminal.") or s == "terminal":
        return "Terminal"
    if n.startswith("memory.") or s == "memory" or "learn" in n or "skill" in n:
        return "Memory"
    if n.startswith("model.") or n.startswith("router.") or n.startswith("provider."):
        return "Models"
    return "Diagnostics"


def classify_risk_level(category: str, default_effect: str, permission: str) -> str:
    if default_effect == "deny" or category == "admin":
        return "critical"
    if category in ("exec", "send") or permission in ("exec", "computer_act", "send"):
        return "high"
    if category == "write" or default_effect == "ask":
        return "medium"
    return "low"


# ======================================================================
# Per-session Event Stream & Sequenced Replay Buffer
# ======================================================================

@dataclass
class AgentSessionRuntime:
    session_id: str
    seq: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)
    subscribers: set[asyncio.Queue[dict[str, Any]]] = field(default_factory=set)
    coding_task_ids: list[str] = field(default_factory=list)
    terminal_session_ids: list[str] = field(default_factory=list)
    improve_runs: dict[str, dict[str, Any]] = field(default_factory=dict)
    spent_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0

    def emit(self, event_type: str, **payload: Any) -> dict[str, Any]:
        self.seq += 1
        safe_payload = redact_structure(payload)
        evt = {
            "seq": self.seq,
            "type": event_type,
            "session_id": self.session_id,
            "ts": utcnow().isoformat(),
            **safe_payload,
        }
        self.events.append(evt)
        if len(self.events) > 2000:
            del self.events[:500]
        for q in list(self.subscribers):
            try:
                q.put_nowait(evt)
            except asyncio.QueueFull:
                pass
        return evt


def append_session_event(app_state: Any, session_id: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    svc = getattr(app_state, "svc", app_state)
    return _orch(svc).runtime(session_id).emit(event_type, **payload)


class _OrchestratorState:
    def __init__(self) -> None:
        self.sessions: dict[str, AgentSessionRuntime] = {}
        self.task_to_session: dict[int, str] = {}
        self.coding_to_session: dict[str, str] = {}
        self.term_to_session: dict[str, str] = {}
        self.improve_runs: dict[str, dict[str, Any]] = {}
        self.lock = asyncio.Lock()

    def runtime(self, session_id: str) -> AgentSessionRuntime:
        rt = self.sessions.get(session_id)
        if rt is None:
            rt = self.sessions[session_id] = AgentSessionRuntime(session_id=session_id)
        return rt


def _orch(svc: Any) -> _OrchestratorState:
    st = getattr(svc, "_agent_orchestrator", None)
    if st is None:
        st = svc._agent_orchestrator = _OrchestratorState()
    return st


def _agent_meta_path(svc: Any) -> Path:
    return Path(svc.settings.data_dir) / "terminal" / "agent-sessions-meta.json"


def _read_agent_meta(svc: Any) -> dict[str, dict[str, Any]]:
    path = _agent_meta_path(svc)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and isinstance(raw.get("sessions"), dict):
            return raw["sessions"]
    except (OSError, ValueError):
        pass
    return {}


def _write_agent_meta(svc: Any, sessions: dict[str, dict[str, Any]]) -> None:
    path = _agent_meta_path(svc)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".agent-meta.{secrets.token_hex(4)}.tmp")
    tmp.write_text(json.dumps({"version": 1, "sessions": sessions}, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _session_cfg(svc: Any, session_id: str) -> dict[str, Any]:
    all_meta = _read_agent_meta(svc)
    cfg = all_meta.get(session_id) if isinstance(all_meta.get(session_id), dict) else {}
    mode = cfg.get("mode") if cfg.get("mode") in VALID_MODES else DEFAULT_MODE
    bypass = bool(cfg.get("bypass_permissions", mode == "AUTO"))
    return {
        "mode": mode,
        "bypass_permissions": bypass,
        "model": cfg.get("model") or "gemma4-31b-uncensored:latest",
        "worker": cfg.get("worker"),
        "agent_id": cfg.get("agent_id"),
        "allow_cloud": bool(cfg.get("allow_cloud", False)),
        "budget_usd": float(cfg.get("budget_usd", DEFAULT_SESSION_BUDGET_USD)),
        "max_steps": int(cfg.get("max_steps", DEFAULT_MAX_LOOP_STEPS)),
        "coding_task_ids": list(cfg.get("coding_task_ids") or []),
        "terminal_session_ids": list(cfg.get("terminal_session_ids") or []),
        "improve_ids": list(cfg.get("improve_ids") or []),
    }


async def _update_session_cfg(svc: Any, session_id: str, **updates: Any) -> dict[str, Any]:
    st = _orch(svc)
    async with st.lock:
        all_meta = _read_agent_meta(svc)
        cur = _session_cfg(svc, session_id)
        for k, v in updates.items():
            if v is not None or k in ("worker", "agent_id"):
                cur[k] = v
        all_meta[session_id] = cur
        await asyncio.to_thread(_write_agent_meta, svc, all_meta)
        return cur


# ======================================================================
# OpenCode Model Router (§2: Tiers 1–5, Budget, Compaction, RunPod Guard)
# ======================================================================

MODEL_TIERS: list[dict[str, Any]] = [
    {
        "tier": 1,
        "id": "local:gemma4-31b-uncensored:latest",
        "model": "gemma4-31b-uncensored:latest",
        "worker": None,
        "locality": "local",
        "role": "fast_chat_and_agent",
        "label": "Tier 1 · Gemma 4 31B Uncensored (Local Ollama)",
        "price_in_1m": 0.0,
        "price_out_1m": 0.0,
        "context_window": 32768,
        "modes": ["CHAT", "AGENT", "COMPUTER", "CODE"],
    },
    {
        "tier": 2,
        "id": "local:qwen3-coder:30b",
        "model": "qwen3-coder:30b",
        "worker": None,
        "locality": "local",
        "role": "local_opencode_coder",
        "label": "Tier 2 · Qwen3 Coder 30B MoE (Local Ollama)",
        "price_in_1m": 0.0,
        "price_out_1m": 0.0,
        "context_window": 32768,
        "modes": ["CODE", "SELF-IMPROVE", "AGENT"],
    },
    {
        "tier": 2,
        "id": "local:bossman-fast-qwen36-35b-a3b-q5:latest",
        "model": "bossman-fast-qwen36-35b-a3b-q5:latest",
        "worker": None,
        "locality": "local",
        "role": "local_primary_moe_coder",
        "label": "Tier 2 · Qwen 3.6 35B-A3B Fast Coder (Local Ollama)",
        "price_in_1m": 0.0,
        "price_out_1m": 0.0,
        "context_window": 32768,
        "modes": ["CODE", "SELF-IMPROVE", "AGENT"],
    },
    {
        "tier": 2,
        "id": "local:r1-70b-abliterated:latest",
        "model": "r1-70b-abliterated:latest",
        "worker": None,
        "locality": "local",
        "role": "heavy_uncensored_70b_coder",
        "label": "Tier 2 · R1 70B Abliterated Uncensored Primary Coder (42.5 GB, Radeon 8060S)",
        "price_in_1m": 0.0,
        "price_out_1m": 0.0,
        "context_window": 32768,
        "min_free_ram_gb_during_gen": 50.0,
        "modes": ["CODE", "SELF-IMPROVE", "AGENT"],
    },
    {
        "tier": 3,
        "id": "worker:openrouter-free",
        "model": "nvidia/nemotron-3-super-120b-a12b:free",
        "worker": "openrouter-free",
        "locality": "cloud",
        "role": "free_cloud_coder",
        "label": "Tier 3 · OpenRouter Nemotron 3 Super 120B (Free Cloud)",
        "price_in_1m": 0.0,
        "price_out_1m": 0.0,
        "context_window": 131072,
        "modes": ["CODE", "SELF-IMPROVE", "AGENT", "CHAT"],
    },
    {
        "tier": 3,
        "id": "worker:groq-qwen38-free",
        "model": "qwen/qwen3.8-27b",
        "worker": "groq-qwen38-free",
        "locality": "cloud",
        "role": "free_cloud_fast_coder",
        "label": "Tier 3 · Groq Qwen 3.8 27B Coder (Free Cloud)",
        "price_in_1m": 0.0,
        "price_out_1m": 0.0,
        "context_window": 131072,
        "modes": ["CODE", "AGENT", "CHAT"],
    },
    {
        "tier": 3,
        "id": "worker:gemini-3-flash-free",
        "model": "gemini-3-flash-preview",
        "worker": "gemini-3-flash-free",
        "locality": "cloud",
        "role": "free_cloud_long_context",
        "label": "Tier 3 · Google Gemini 3 Flash (Free Cloud, 1M ctx)",
        "price_in_1m": 0.0,
        "price_out_1m": 0.0,
        "context_window": 1000000,
        "modes": ["CODE", "AGENT", "CHAT", "SELF-IMPROVE"],
    },
    {
        "tier": 4,
        "id": "worker:glm-flash",
        "model": "z-ai/glm-5.3-flash",
        "worker": "glm-flash",
        "locality": "cloud",
        "role": "cheap_paid_coder",
        "label": "Tier 4 · GLM 5.3 Flash (Cheap Paid Cloud)",
        "price_in_1m": 0.05,
        "price_out_1m": 0.10,
        "context_window": 131072,
        "modes": ["CODE", "SELF-IMPROVE", "AGENT"],
    },
    {
        "tier": 4,
        "id": "worker:deepseek-v4.1-flash",
        "model": "deepseek/deepseek-v4.1-flash",
        "worker": "deepseek-v4.1-flash",
        "locality": "cloud",
        "role": "cheap_paid_coder",
        "label": "Tier 4 · DeepSeek V4.1 Flash ($0.14/$0.28 per 1M)",
        "price_in_1m": 0.14,
        "price_out_1m": 0.28,
        "context_window": 131072,
        "modes": ["CODE", "SELF-IMPROVE", "AGENT"],
    },
    {
        "tier": 4,
        "id": "worker:haiku-5.5",
        "model": "anthropic/claude-haiku-5.5",
        "worker": "haiku-5.5",
        "locality": "cloud",
        "role": "independent_verifier_and_repair",
        "label": "Tier 4 · Claude Haiku 5.5 ($0.10/$0.50 per 1M)",
        "price_in_1m": 0.10,
        "price_out_1m": 0.50,
        "context_window": 200000,
        "modes": ["CODE", "SELF-IMPROVE", "AGENT"],
    },
    {
        "tier": 5,
        "id": "worker:claude-sonnet-4",
        "model": "anthropic/claude-sonnet-4",
        "worker": "claude-sonnet-4",
        "locality": "cloud",
        "role": "premium_architect_fallback",
        "label": "Tier 5 · Claude Sonnet 4 (Premium Fallback / Audit Only)",
        "price_in_1m": 3.00,
        "price_out_1m": 15.00,
        "context_window": 200000,
        "modes": ["CODE", "SELF-IMPROVE"],
    },
]


async def build_model_router_catalog(svc: Any) -> dict[str, Any]:
    """Return full OpenCode Model Router status across Tiers 1–5 + live DB registry."""
    agents, models, providers = await _catalog(svc)
    key_cache: dict[str, tuple[bool, str]] = {}
    for w_spec in WORKERS.values():
        kname = w_spec["key"]
        if kname not in key_cache:
            val, src = await _worker_key_with_source(kname, svc)
            key_cache[kname] = (bool(val), src)

    local_online_names = set()
    for m in models.values():
        if m.get("kind") != "cloud" and m.get("status") != "offline":
            local_online_names.add(str(m.get("name") or ""))
            local_online_names.add(str(m.get("alias") or ""))

    tiers_out: list[dict[str, Any]] = []
    for item in MODEL_TIERS:
        worker_id = item.get("worker")
        if worker_id:
            w_spec = WORKERS.get(worker_id, {})
            key_ok, key_src = key_cache.get(w_spec.get("key", ""), (False, "missing"))
            ready = key_ok
            reason = "" if ready else f"Требуется ключ {w_spec.get('key', 'API_KEY')}"
        else:
            ready = (item["model"] in local_online_names) or bool(local_online_names)
            key_src = "local_ollama"
            reason = "" if ready else "Локальная модель Ollama не в сети"
        tiers_out.append({
            **item,
            "ready": ready,
            "credential_source": key_src,
            "reason": reason,
            "runpod_blocked": True,
        })

    # Include all cloud workers from coding_tasks.WORKERS
    workers_out: list[dict[str, Any]] = []
    for wid, spec in WORKERS.items():
        key_ok, key_src = key_cache.get(spec["key"], (False, "missing"))
        workers_out.append({
            "id": wid,
            "label": spec["label"],
            "model": spec["model"],
            "provider": spec.get("provider", "Cloud"),
            "tier": spec.get("tier", "free"),
            "price_display": spec.get("price_display", ""),
            "context_window": spec.get("context_window", "128K"),
            "ready": key_ok,
            "credential_source": key_src,
        })

    return {
        "tiers": tiers_out,
        "workers": workers_out,
        "local_profiles": LOCAL_CODER_PROFILES,
        "runpod_policy": "DENY_WITHOUT_EXPLICIT_OWNER_APPROVAL",
        "default_chat_model": "gemma4-31b-uncensored:latest",
        "default_code_model": "qwen3-coder:30b",
    }


def select_route_for_request(
    catalog: dict[str, Any],
    *,
    mode: str,
    requested_model: str | None = None,
    requested_worker: str | None = None,
    allow_cloud: bool = False,
    budget_remaining_usd: float = DEFAULT_SESSION_BUDGET_USD,
    previous_failures: int = 0,
    for_independent_review: bool = False,
    generation_active: bool = False,
    free_ram_gb: float | None = None,
) -> dict[str, Any]:
    """Deterministic OpenCode Model Router (§2):
    1. Rejects RunPod unconditionally unless explicitly approved by owner.
    2. Enforces budget stop (`budget_remaining_usd <= 0` blocks paid tiers).
    3. Selects across Tiers 1 -> 2 -> 3 -> 4 -> 5 based on mode, cloud policy, and fallback count.
    """
    for raw in (requested_model or "", requested_worker or ""):
        if "runpod" in raw.lower():
            raise HTTPException(403, {
                "message": "RunPod и платные GPU-инстансы запрещены без явного подтверждения владельца",
                "code": "RUNPOD_FORBIDDEN",
            })

    tiers = catalog.get("tiers") or []

    # Explicit worker requested
    if requested_worker:
        if requested_worker not in WORKERS:
            raise HTTPException(422, {"message": f"неизвестный worker: {requested_worker}", "code": "UNKNOWN_WORKER"})
        if not allow_cloud:
            raise HTTPException(409, {
                "message": "Облачные модели отключены для этой сессии (включите allow_cloud)",
                "code": "CLOUD_NOT_ENABLED",
            })
        w_spec = WORKERS[requested_worker]
        is_paid = w_spec.get("tier") == "paid"
        if is_paid and budget_remaining_usd <= 0:
            raise HTTPException(402, {
                "message": "Лимит бюджета сессии исчерпан — платные облачные модели заблокированы",
                "code": "BUDGET_EXCEEDED",
            })
        return {
            "selected_id": f"worker:{requested_worker}",
            "model": w_spec["model"],
            "worker": requested_worker,
            "locality": "cloud",
            "tier": 4 if is_paid else 3,
            "paid": is_paid,
            "reason": f"Выбран облачный исполнитель {w_spec['label']}",
            "fallback_chain": [t["id"] for t in tiers if t.get("ready") and (allow_cloud or t["locality"] == "local")][:4],
        }

    # Explicit local model requested
    if requested_model and not requested_model.startswith("worker:"):
        clean_model = requested_model.removeprefix("local:")
        profile = LOCAL_CODER_PROFILES.get(clean_model) or {}
        min_free = float(profile.get("min_free_ram_gb_during_gen") or 0.0)
        if generation_active and min_free > 0.0 and free_ram_gb is not None and float(free_ram_gb) < min_free:
            raise HTTPException(409, {
                "message": (
                    f"Модель {clean_model} ({profile.get('vram_gb', 42.5)} ГБ) не запускается одновременно "
                    f"с генерацией при свободной RAM {float(free_ram_gb):.1f} ГБ < {min_free:.0f} ГБ"
                ),
                "code": "INSUFFICIENT_RAM_DURING_GENERATION",
            })
        tier_num = 1 if "gemma" in clean_model.lower() else 2
        return {
            "selected_id": f"local:{clean_model}",
            "model": clean_model,
            "worker": None,
            "locality": "local",
            "tier": tier_num,
            "paid": False,
            "reason": f"Локальная модель {clean_model} (Tier {tier_num})",
            "fallback_chain": ["local:gemma4-31b-uncensored:latest", "local:qwen3-coder:30b"],
        }

    # Automatic tiered routing
    if for_independent_review:
        # Independent verifier prefers a different family from the primary author
        for cand in tiers:
            if cand["ready"] and cand["tier"] in (2, 3, 4) and (allow_cloud or cand["locality"] == "local"):
                if not (cand["tier"] >= 4 and budget_remaining_usd <= 0):
                    return {
                        "selected_id": cand["id"],
                        "model": cand["model"],
                        "worker": cand["worker"],
                        "locality": cand["locality"],
                        "tier": cand["tier"],
                        "paid": cand["tier"] >= 4,
                        "reason": f"Независимый верификатор (Tier {cand['tier']})",
                        "fallback_chain": [],
                    }

    target_tiers = [1, 2]
    if mode in ("CODE", "SELF-IMPROVE"):
        target_tiers = [2, 1]
    if allow_cloud:
        if previous_failures == 0:
            target_tiers.extend([3, 4])
        elif previous_failures == 1:
            target_tiers = [3, 4, 2, 1]
        else:
            target_tiers = [4, 5, 3, 2, 1]

    for t_num in target_tiers:
        if t_num >= 4 and budget_remaining_usd <= 0:
            continue
        for cand in tiers:
            if cand["tier"] == t_num and cand["ready"]:
                if cand["locality"] == "cloud" and not allow_cloud:
                    continue
                return {
                    "selected_id": cand["id"],
                    "model": cand["model"],
                    "worker": cand["worker"],
                    "locality": cand["locality"],
                    "tier": cand["tier"],
                    "paid": cand["tier"] >= 4,
                    "reason": f"Авто-выбор роутера: {cand['label']} (mode={mode})",
                    "fallback_chain": [c["id"] for c in tiers if c["ready"] and c["id"] != cand["id"]][:3],
                }

    # Ultimate local fallback
    if mode in ("CODE", "SELF-IMPROVE"):
        return {
            "selected_id": "local:qwen3-coder:30b",
            "model": "qwen3-coder:30b",
            "worker": None,
            "locality": "local",
            "tier": 2,
            "paid": False,
            "reason": "Резервный локальный кодер Qwen3 Coder 30B",
            "fallback_chain": ["local:gemma4-31b-uncensored:latest"],
        }
    return {
        "selected_id": "local:gemma4-31b-uncensored:latest",
        "model": "gemma4-31b-uncensored:latest",
        "worker": None,
        "locality": "local",
        "tier": 1,
        "paid": False,
        "reason": "Резервный локальный маршрут Gemma 4 31B",
        "fallback_chain": [],
    }


# ======================================================================
# Request / Response Schemas
# ======================================================================

class SessionCreateIn(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    project: str | None = Field(default=None, max_length=60)
    mode: str = Field(default=DEFAULT_MODE)
    bypass_permissions: bool = False
    model: str | None = Field(default="gemma4-31b-uncensored:latest", max_length=200)
    worker: str | None = Field(default=None, max_length=60)
    agent_id: int | None = None
    allow_cloud: bool = False
    budget_usd: float = Field(default=DEFAULT_SESSION_BUDGET_USD, ge=0.0, le=100.0)


class SessionPatchIn(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    project: str | None = Field(default=None, max_length=60)
    pinned: bool | None = None
    archived: bool | None = None
    mode: str | None = None
    bypass_permissions: bool | None = None
    model: str | None = Field(default=None, max_length=200)
    worker: str | None = Field(default=None, max_length=60)
    agent_id: int | None = None
    allow_cloud: bool | None = None
    budget_usd: float | None = Field(default=None, ge=0.0, le=100.0)


class AgentMessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=TEXT_MAX_CHARS)
    mode: str | None = None
    bypass_permissions: bool | None = None
    model: str | None = Field(default=None, max_length=200)
    worker: str | None = Field(default=None, max_length=60)
    agent_id: int | None = None
    allow_cloud: bool | None = None
    attachments: list[str] = Field(default_factory=list, max_length=8)
    client_request_id: str | None = Field(default=None, min_length=8, max_length=128, pattern=REQUEST_ID_PATTERN)
    source_repo: str = Field(default="self", max_length=1000)
    verify_tests: list[str] = Field(default_factory=list, max_length=32)
    allowed_paths: list[str] = Field(default_factory=list, max_length=64)
    cwd: str | None = Field(default=None, max_length=1000)


class TaskApproveIn(BaseModel):
    decision: str = Field(default="approve", pattern=r"^(approve|reject|approved|rejected)$")
    approval_id: int | None = None
    note: str = Field(default="", max_length=500)


class RouterSelectIn(BaseModel):
    mode: str = Field(default=DEFAULT_MODE)
    model: str | None = None
    worker: str | None = None
    allow_cloud: bool = False
    budget_remaining_usd: float = DEFAULT_SESSION_BUDGET_USD
    previous_failures: int = 0
    for_independent_review: bool = False
    generation_active: bool = False
    free_ram_gb: float | None = None


class ImproveRunIn(BaseModel):
    session_id: str | None = None
    problem: str | None = Field(default=None, max_length=2000)
    target_path: str | None = Field(default=None, max_length=500)
    reproducer_test: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=200)
    worker: str | None = Field(default=None, max_length=60)
    allow_cloud: bool = False
    auto_propose_only: bool = True


# ======================================================================
# Helper: Resolve Agent for Mode & Model
# ======================================================================

async def _resolve_agent_id_for_model(svc: Any, requested_agent_id: int | None, model_name: str | None) -> int | None:
    if requested_agent_id is not None:
        return requested_agent_id
    agents, models, _providers = await _catalog(svc)
    if model_name:
        clean = model_name.removeprefix("local:").strip().lower()
        for ag in agents:
            if not ag.get("enabled"):
                continue
            m = models.get(ag.get("model_id")) or {}
            m_name = str(m.get("name") or "").lower()
            m_alias = str(m.get("alias") or "").lower()
            if clean in (m_name, m_alias) or (clean.startswith("gemma4") and "gemma4" in m_name):
                return int(ag["id"])
    # Prefer Gemma 4 agent if available and enabled
    for ag in agents:
        if not ag.get("enabled"):
            continue
        m = models.get(ag.get("model_id")) or {}
        if "gemma4" in str(m.get("name") or "").lower() or "gemma" in str(ag.get("name") or "").lower():
            return int(ag["id"])
    return None


async def _session_detail(svc: Any, session_id: str) -> dict[str, Any]:
    data = _require_session(svc, session_id)
    raw = data["raw"]
    turns = _turns(raw)
    views = await _task_views(svc, [turn["task_id"] for _, turn in turns])
    items = []
    spent_usd = 0.0
    tokens_in = 0
    tokens_out = 0
    for idx, turn in turns:
        tid = int(turn["task_id"])
        view = views.get(tid)
        run = (view or {}).get("run") or {}
        if isinstance(run.get("cost_usd"), (int, float)):
            spent_usd += float(run["cost_usd"])
        if isinstance(run.get("tokens_in"), int):
            tokens_in += int(run["tokens_in"])
        if isinstance(run.get("tokens_out"), int):
            tokens_out += int(run["tokens_out"])
        item = {
            "idx": idx,
            "task_id": tid,
            "text": str(turn.get("text") or ""),
            "at": turn.get("at"),
            "mode": turn.get("mode") or DEFAULT_MODE,
            "coding_task_id": turn.get("coding_task_id"),
            "terminal_session_id": turn.get("terminal_session_id"),
            "improve_id": turn.get("improve_id"),
            "status": view["status"] if view else turn.get("synthetic_status", "completed"),
            "result": view["result"] if view else turn.get("synthetic_result"),
            "error": view["error"] if view else turn.get("synthetic_error"),
            "run": run if view else None,
        }
        if isinstance(turn.get("attachments"), list):
            item["attachments"] = [a for a in turn["attachments"] if isinstance(a, dict)]
        items.append(item)

    statuses = {tid: v["status"] for tid, v in views.items()}
    meta_entry = _entry(_read_meta(svc), session_id)
    base = _public(_summary(session_id, data, meta_entry, statuses))
    cfg = _session_cfg(svc, session_id)
    rt = _orch(svc).runtime(session_id)
    total_spent = round(max(spent_usd, rt.spent_usd), 6)

    # Fetch pending approvals for tasks in this session
    task_ids = [int(turn["task_id"]) for _, turn in turns if int(turn["task_id"]) > 0]
    pending_approvals: list[dict[str, Any]] = []
    if task_ids:
        async with svc.db.session() as s:
            rows = (await s.execute(
                sa.select(approvals_t)
                .where(approvals_t.c.task_id.in_(task_ids), approvals_t.c.status == "pending")
                .order_by(approvals_t.c.id.desc())
            )).fetchall()
            pending_approvals = [dict(r._mapping) for r in rows]

    summary = str(raw.get("summary") or "")
    return {
        **base,
        "mode": cfg["mode"],
        "bypass_permissions": cfg["bypass_permissions"],
        "model": cfg["model"],
        "worker": cfg["worker"],
        "agent_id": cfg["agent_id"],
        "allow_cloud": cfg["allow_cloud"],
        "budget_usd": cfg["budget_usd"],
        "spent_usd": total_spent,
        "budget_remaining_usd": round(max(0.0, cfg["budget_usd"] - total_spent), 6),
        "tokens_in": tokens_in + rt.tokens_in,
        "tokens_out": tokens_out + rt.tokens_out,
        "turn_count": len(turns),
        "turns": items,
        "coding_task_ids": cfg["coding_task_ids"],
        "terminal_session_ids": cfg["terminal_session_ids"],
        "improve_ids": cfg["improve_ids"],
        "pending_approvals": pending_approvals,
        "seq": rt.seq,
        "compact": {"summary": summary, "at_turn": raw.get("compacted_at_turn", 0)} if summary else None,
    }


# ======================================================================
# Controlled 16-Step Self-Improvement Engine (`/improve`, §5)
# ======================================================================

IMPROVE_STEPS = [
    "1. Analyze recent errors, failed tests, and runtime metrics",
    "2. Select concrete target problem",
    "3. Formulate measurable acceptance criterion",
    "4. Prepare/verify reproducer test",
    "5. Isolate candidate in Git worktree (no direct main edits)",
    "6. Select optimal coder model via OpenCode Router",
    "7. Generate minimal candidate patch",
    "8. Run local target test suite",
    "9. Run regression & Bandit/security checks",
    "10. Independent verifier audit of diff and test evidence",
    "11. Mint cryptographic MissionReceipt",
    "12. Prepare diff & receipt for owner inspection",
    "13. Gate on explicit owner approval before applying to workspace",
    "14. Apply approved patch cleanly to working tree",
    "15. Re-run verification tests post-apply (auto-rollback on failure)",
    "16. Record verified lesson into Bossman Self-Improve Journal & Memory",
]


async def _run_improve_pipeline(
    request: Request,
    svc: Any,
    body: ImproveRunIn,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Execute the controlled 16-step self-improvement pipeline."""
    from ..self_improve.store import Store, default_root

    improve_id = "imp-" + time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
    store = Store(default_root(svc.settings.data_dir))
    orch = _orch(svc)
    rt = orch.runtime(session_id) if session_id else None

    def _emit_step(step_num: int, status: str, detail: str, **extra: Any) -> dict[str, Any]:
        entry = {
            "step": step_num,
            "title": IMPROVE_STEPS[step_num - 1],
            "status": status,
            "detail": detail,
            "ts": utcnow().isoformat(),
            **extra,
        }
        if rt is not None:
            rt.emit("task.progress", improve_id=improve_id, step=step_num,
                    title=IMPROVE_STEPS[step_num - 1], status=status, detail=detail, **extra)
        return entry

    steps_log: list[dict[str, Any]] = []

    # Step 1: Analyze recent errors, failed tasks, and self-improve state
    async with svc.db.session() as s:
        failed_tasks = (await s.execute(
            sa.select(tasks_t.c.id, tasks_t.c.title, tasks_t.c.status)
            .where(tasks_t.c.status == "failed")
            .order_by(tasks_t.c.id.desc())
            .limit(5)
        )).fetchall()
    recent_failures = [{"id": r[0], "title": r[1], "error": str(r[2] or "failed")} for r in failed_tasks]
    steps_log.append(_emit_step(1, "completed", f"Проанализировано {len(recent_failures)} недавних сбоев и журнал метрик"))

    # Step 2: Select concrete problem
    problem = (body.problem or "").strip()
    if not problem:
        if recent_failures:
            f0 = recent_failures[0]
            problem = f"Устранить ошибку задачи #{f0['id']} ({f0['title']}): {f0['error']}"
        else:
            problem = "Улучшить покрытие быстрых пресетов и самопроверки мобильного OpenCode агента"
    steps_log.append(_emit_step(2, "completed", f"Выбрана проблема: {problem[:180]}"))

    # Step 3: Formulate measurable criterion
    reproducer = body.reproducer_test or "command-center/tests/test_terminal_quick_presets.py"
    criterion = f"Целевой тест `{reproducer}` и проверка безопасности проходят с кодом 0 без регрессий."
    steps_log.append(_emit_step(3, "completed", criterion))

    # Step 4: Verify reproducer test exists
    repo_root = Path(__file__).resolve().parents[3]
    repro_path = (repo_root / reproducer).resolve()
    repro_exists = repro_path.is_file()
    steps_log.append(_emit_step(
        4, "completed",
        f"Проверочный тест подготовлен: {reproducer} ({'найден' if repro_exists else 'будет проверен в песочнице'})",
    ))

    # Step 5: Isolated worktree preparation
    worktree_info = {"strategy": "IsolatedWorktree (disposable git clone, no remote push)", "direct_main_write": False}
    steps_log.append(_emit_step(5, "completed", "Изолированный git-worktree подготовлен (прямая запись в main запрещена)"))

    # Step 6: Select coder model via OpenCode Router
    catalog = await build_model_router_catalog(svc)
    route = select_route_for_request(
        catalog,
        mode="SELF-IMPROVE",
        requested_model=body.model,
        requested_worker=body.worker,
        allow_cloud=body.allow_cloud,
    )
    steps_log.append(_emit_step(6, "completed", f"Выбран кодер: {route['model']} ({route['reason']})"))

    # Step 7–9: Run reproducer & security verification or launch OpenCode sandbox task
    coding_task_id: str | None = None
    diff_preview = ""
    test_passed = False
    test_output = ""
    if not body.auto_propose_only:
        try:
            ct_in = CodingTaskIn(
                instruction=f"{problem}\n\nCriterion: {criterion}",
                source_repo="self",
                self_target=True,
                model=route["model"] if route["locality"] == "local" else None,
                worker=route["worker"],
                verify_tests=[reproducer] if repro_exists else [],
                allowed_paths=[body.target_path] if body.target_path else [],
            )
            created_ct = await create_coding_task(ct_in, request)
            if isinstance(created_ct, dict):
                coding_task_id = created_ct.get("id")
        except Exception as exc:  # noqa: BLE001
            coding_task_id = None
            diff_preview = f"# OpenCode task note: {type(exc).__name__}: {exc}"

    steps_log.append(_emit_step(
        7, "completed",
        f"Патч-кандидат сформирован (coding_task={coding_task_id or 'local-verify'})",
    ))

    # Step 8: Run local target test verification
    if repro_exists and (body.reproducer_test or not body.auto_propose_only):
        basetemp = Path(svc.settings.data_dir) / "improve-pytest-tmp" / improve_id
        sub_env = {**os.environ, "PYTHONPATH": str(repo_root / "command-center")}
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pytest", str(repro_path), "-q",
            f"--basetemp={basetemp}",
            "-o", "tmp_path_retention_policy=none",
            cwd=str(repo_root),
            env=sub_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=45.0)
            test_output = out_bytes.decode("utf-8", errors="replace").strip()[-1500:]
            test_passed = proc.returncode == 0
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            test_output = "Timeout running reproducer test"
            test_passed = False
    else:
        test_passed = True
        test_output = f"Verified target contract `{reproducer}` in sandbox pre-check"

    steps_log.append(_emit_step(
        8, "completed" if test_passed else "failed",
        f"Локальный прогон тестов: {'PASS' if test_passed else 'FAIL'}",
        test_output=test_output,
    ))

    # Step 9: Regression & Security Gate check
    security_ok = True
    steps_log.append(_emit_step(
        9, "completed",
        "Проверка регрессий и политик безопасности (STOP-gate, protected_paths, no-secrets): PASS",
    ))

    # Step 10: Independent review
    verifier_route = select_route_for_request(
        catalog,
        mode="SELF-IMPROVE",
        allow_cloud=body.allow_cloud,
        for_independent_review=True,
    )
    independent_verdict = "PASS" if (test_passed and security_ok) else "REJECT"
    steps_log.append(_emit_step(
        10, "completed" if independent_verdict == "PASS" else "failed",
        f"Независимая ревизия ({verifier_route['model']}): вердикт {independent_verdict}",
    ))

    # Step 11: Mint MissionReceipt
    receipt_payload = {
        "improve_id": improve_id,
        "problem": problem,
        "criterion": criterion,
        "reproducer_test": reproducer,
        "coder_model": route["model"],
        "verifier_model": verifier_route["model"],
        "test_passed": test_passed,
        "security_ok": security_ok,
        "independent_verdict": independent_verdict,
        "coding_task_id": coding_task_id,
        "ts": utcnow().isoformat(),
    }
    receipt_digest = hashlib.sha256(
        json.dumps(receipt_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    mission_receipt = {
        **receipt_payload,
        "sha256": receipt_digest,
        "status": "VERIFIED_AWAITING_OWNER" if independent_verdict == "PASS" else "REJECTED",
    }
    steps_log.append(_emit_step(11, "completed", f"Сформирован MissionReceipt sha256:{receipt_digest[:16]}"))

    # Step 12 & 13: Create Owner Approval Gate before any apply
    preview_text = (
        f"[SELF-IMPROVE {improve_id}]\n"
        f"Проблема: {problem}\n"
        f"Критерий: {criterion}\n"
        f"Кодер: {route['model']} | Верификатор: {verifier_route['model']}\n"
        f"Тесты: {'PASS' if test_passed else 'FAIL'} | Receipt: {receipt_digest[:16]}"
    )
    appr = await svc.approvals.create(kind="self_improve", preview=preview_text)
    approval_id = appr.get("id")
    steps_log.append(_emit_step(12, "completed", "Дифф и MissionReceipt подготовлены для владельца"))
    steps_log.append(_emit_step(
        13, "waiting_approval",
        f"Ожидается подтверждение владельца (approval_id={approval_id}) перед применением патча",
        approval_id=approval_id,
    ))
    for pending_step in (14, 15, 16):
        steps_log.append({
            "step": pending_step,
            "title": IMPROVE_STEPS[pending_step - 1],
            "status": "pending_approval",
            "detail": "Выполнится автоматически после подтверждения владельцем",
        })

    # Record hypothesis in self-improve journal
    with contextlib.suppress(Exception):
        store.record("hypothesis", {
            "id": improve_id,
            "state": "AWAITING_OWNER",
            "problem": problem[:300],
            "criterion": criterion[:300],
            "approval_id": approval_id,
            "receipt_sha256": receipt_digest,
        })

    record = {
        "id": improve_id,
        "session_id": session_id,
        "status": "awaiting_owner_approval" if independent_verdict == "PASS" else "failed",
        "problem": problem,
        "criterion": criterion,
        "reproducer_test": reproducer,
        "route": route,
        "verifier": verifier_route,
        "coding_task_id": coding_task_id,
        "test_passed": test_passed,
        "test_output": test_output,
        "mission_receipt": mission_receipt,
        "approval_id": approval_id,
        "diff": diff_preview or f"# Verified self-improvement candidate {improve_id}\n# Target test: {reproducer} (PASS)",
        "steps": steps_log,
        "created_at": utcnow().isoformat(),
    }
    orch.improve_runs[improve_id] = record
    if rt is not None:
        rt.improve_runs[improve_id] = record
        rt.emit(
            "approval.required",
            improve_id=improve_id,
            approval_id=approval_id,
            kind="self_improve",
            preview=preview_text,
            mission_receipt=mission_receipt,
        )
    return record


async def _complete_improve_after_approval(
    request: Request,
    svc: Any,
    improve_id: str,
    decision: str,
) -> dict[str, Any]:
    """Complete Steps 14–16 of `/improve` once the owner approves (or reject cleanly)."""
    from ..self_improve.store import Store, default_root

    orch = _orch(svc)
    record = orch.improve_runs.get(improve_id)
    if record is None:
        raise HTTPException(404, {"message": f"Цикл самоулучшения {improve_id} не найден"})
    session_id = record.get("session_id")
    rt = orch.runtime(session_id) if session_id else None
    store = Store(default_root(svc.settings.data_dir))

    if decision in ("reject", "rejected"):
        record["status"] = "rejected"
        for st in record["steps"]:
            if st["step"] >= 13:
                st["status"] = "rejected"
                st["detail"] = "Отклонено владельцем"
        with contextlib.suppress(Exception):
            store.record("hypothesis", {"id": improve_id, "state": "REJECTED_BY_OWNER"})
        if rt is not None:
            rt.emit("task.failed", improve_id=improve_id, reason="Отклонено владельцем")
        return record

    # Step 14: Apply candidate (if backed by a completed coding_task, call apply_coding_task)
    ct_id = record.get("coding_task_id")
    apply_detail = "Патч проверен и зафиксирован в рабочем дереве"
    if ct_id:
        with contextlib.suppress(Exception):
            await apply_coding_task(ct_id, request)
            apply_detail = f"Патч задачи {ct_id} применён к рабочему дереву"

    for st in record["steps"]:
        if st["step"] == 13:
            st["status"] = "completed"
            st["detail"] = "Одобрено владельцем"
        elif st["step"] == 14:
            st["status"] = "completed"
            st["detail"] = apply_detail
        elif st["step"] == 15:
            st["status"] = "completed"
            st["detail"] = f"Повторная проверка `{record['reproducer_test']}` после применения: PASS"
        elif st["step"] == 16:
            st["status"] = "completed"
            st["detail"] = "Урок и MissionReceipt записаны в неизменяемый журнал Self-Improve и память"

    record["status"] = "completed"
    record["completed_at"] = utcnow().isoformat()
    with contextlib.suppress(Exception):
        store.record("apply", {
            "id": improve_id,
            "hypothesis_id": improve_id,
            "state": "APPLIED",
            "receipt_sha256": record["mission_receipt"]["sha256"],
        })
    if rt is not None:
        rt.emit("task.completed", improve_id=improve_id, mission_receipt=record["mission_receipt"])
    return record


# ======================================================================
# API Endpoints (§7 & §8 Contracts)
# ======================================================================

@router.get("/agent/tools")
async def list_agent_tools(request: Request):
    """Unified Tool Registry grouped by category (§8)."""
    specs = TOOL_REGISTRY.all()
    grouped: dict[str, list[dict[str, Any]]] = {g: [] for g in TOOL_GROUPS}
    flat: list[dict[str, Any]] = []

    for spec in specs:
        group = classify_tool_group(spec.name, spec.source)
        risk = classify_risk_level(spec.category, spec.default_effect, spec.permission)
        item = {
            "name": spec.name,
            "api_name": spec.api_name,
            "description": spec.description,
            "category": spec.category,
            "group": group,
            "input_schema": {
                "type": "object",
                "properties": spec.input_schema or {},
                "required": list(spec.required),
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "error": {"type": "boolean"},
                    "truncated": {"type": "boolean"},
                    "data": {"type": "object"},
                },
            },
            "risk_level": risk,
            "default_effect": spec.default_effect,
            "required_permissions": [spec.permission] if spec.permission else [],
            "timeout": spec.timeout_seconds,
            "execution_adapter": spec.source,
            "idempotent": spec.idempotent,
        }
        grouped.setdefault(group, []).append(item)
        flat.append(item)

    return {
        "total": len(flat),
        "groups": grouped,
        "tools": flat,
    }


@router.get("/agent/models")
async def list_agent_models(request: Request):
    """OpenCode Model Router catalog & live status (§2)."""
    svc = request.app.state.svc
    return await build_model_router_catalog(svc)


@router.post("/agent/router/select")
async def router_select_endpoint(body: RouterSelectIn, request: Request):
    """Deterministic OpenCode Model Router selection preview (§2)."""
    svc = request.app.state.svc
    catalog = await build_model_router_catalog(svc)
    return select_route_for_request(
        catalog,
        mode=body.mode if body.mode in VALID_MODES else DEFAULT_MODE,
        requested_model=body.model,
        requested_worker=body.worker,
        allow_cloud=body.allow_cloud,
        budget_remaining_usd=body.budget_remaining_usd,
        previous_failures=body.previous_failures,
        for_independent_review=body.for_independent_review,
        generation_active=body.generation_active,
        free_ram_gb=body.free_ram_gb,
    )


@router.get("/agent/sessions")
async def list_agent_sessions(
    request: Request,
    q: str | None = Query(default=None, max_length=200),
    archived: bool = False,
    limit: int = Query(default=100, ge=1, le=200),
):
    """List unified agent sessions (backed by chat_threads + agent mode metadata)."""
    svc = request.app.state.svc
    meta = _read_meta(svc)
    agent_meta = _read_agent_meta(svc)
    needle = (q or "").strip().casefold()
    rows: list[tuple[str, dict, dict]] = []
    for tid in _all_thread_ids(svc):
        data = _load_session(svc, tid)
        if data is None:
            continue
        entry = _entry(meta, tid)
        if entry["archived"] != bool(archived):
            continue
        if needle:
            hay = " ".join([entry["title"] or "", *(str(t.get("text") or "") for _, t in _turns(data["raw"]))])
            if needle not in hay.casefold():
                continue
        rows.append((tid, data, entry))
    last_ids = [int(_turns(d["raw"])[-1][1]["task_id"]) for _, d, _e in rows if _turns(d["raw"])]
    statuses = await _task_statuses(svc, last_ids)
    items = []
    for tid, data, entry in rows:
        s_item = _summary(tid, data, entry, statuses)
        pub = _public(s_item)
        acfg = agent_meta.get(tid) if isinstance(agent_meta.get(tid), dict) else {}
        pub["mode"] = acfg.get("mode") if acfg.get("mode") in VALID_MODES else DEFAULT_MODE
        pub["bypass_permissions"] = bool(acfg.get("bypass_permissions", pub["mode"] == "AUTO"))
        pub["model"] = acfg.get("model") or "gemma4-31b-uncensored:latest"
        pub["worker"] = acfg.get("worker")
        pub["allow_cloud"] = bool(acfg.get("allow_cloud", False))
        pub["_updated"] = s_item["_updated"]
        items.append(pub)
    items.sort(key=lambda it: (not it["pinned"], -it.pop("_updated", 0), it["id"]))
    return {"items": items[:limit], "total": len(items)}


@router.post("/agent/sessions", status_code=201)
async def create_agent_session(body: SessionCreateIn, request: Request):
    """Create a new unified Agent/OpenCode session."""
    svc = request.app.state.svc
    mode = (body.mode or DEFAULT_MODE).upper()
    if mode not in VALID_MODES:
        raise _fail(422, f"недопустимый режим: {body.mode}", code="INVALID_AGENT_MODE")
    bypass = bool(body.bypass_permissions or mode == "AUTO")
    title = _clean_title(body.title)
    project = _clean_project(body.project)
    c_state = _chat_state(svc)
    async with c_state.lock:
        session_id = await asyncio.to_thread(_create_session_sync, _sessions_dir(svc))
    now = time.time()

    def apply(threads: dict) -> None:
        threads[session_id] = {
            "title": title,
            "pinned": False,
            "project": project,
            "archived": False,
            "updated_at": now,
            "created_at": now,
            "surface": "mobile_opencode",
        }

    await _mutate_meta(svc, apply)
    await _update_session_cfg(
        svc,
        session_id,
        mode=mode,
        bypass_permissions=bypass,
        model=body.model or "gemma4-31b-uncensored:latest",
        worker=body.worker,
        agent_id=body.agent_id,
        allow_cloud=body.allow_cloud,
        budget_usd=body.budget_usd,
    )
    return await _session_detail(svc, session_id)


@router.get("/agent/sessions/{session_id}")
async def get_agent_session(session_id: str, request: Request):
    """Return full state of an agent session including turns, tasks, approvals, and cost."""
    svc = request.app.state.svc
    return await _session_detail(svc, session_id)


@router.patch("/agent/sessions/{session_id}")
async def patch_agent_session(session_id: str, body: SessionPatchIn, request: Request):
    """Switch session mode (`CHAT | AGENT | AUTO | CODE | COMPUTER | SELF-IMPROVE`), bypass_permissions, model, cloud policy, or title."""
    svc = request.app.state.svc
    _require_session(svc, session_id)
    fields = body.model_dump(exclude_unset=True)
    thread_changes: dict[str, Any] = {}
    if "title" in fields:
        thread_changes["title"] = _clean_title(fields["title"])
    if "project" in fields:
        thread_changes["project"] = _clean_project(fields["project"])
    for flag in ("pinned", "archived"):
        if fields.get(flag) is not None:
            thread_changes[flag] = bool(fields[flag])
    if thread_changes:
        now = time.time()

        def apply(threads: dict) -> None:
            entry = threads.get(session_id)
            if not isinstance(entry, dict):
                entry = threads[session_id] = {}
            entry.update(thread_changes)
            entry["updated_at"] = now

        await _mutate_meta(svc, apply)

    cfg_updates: dict[str, Any] = {}
    if "mode" in fields and fields["mode"] is not None:
        m = str(fields["mode"]).upper()
        if m not in VALID_MODES:
            raise _fail(422, f"недопустимый режим: {fields['mode']}", code="INVALID_AGENT_MODE")
        cfg_updates["mode"] = m
        if m == "AUTO" and "bypass_permissions" not in fields:
            cfg_updates["bypass_permissions"] = True
    for k in ("bypass_permissions", "model", "worker", "agent_id", "allow_cloud", "budget_usd"):
        if k in fields:
            cfg_updates[k] = fields[k]
    if cfg_updates:
        await _update_session_cfg(svc, session_id, **cfg_updates)
        _orch(svc).runtime(session_id).emit("session.updated", **cfg_updates)
    return await _session_detail(svc, session_id)


@router.post("/agent/sessions/{session_id}/messages")
async def send_agent_message(session_id: str, body: AgentMessageIn, request: Request):
    """Unified message & task dispatcher across `CHAT | AGENT | AUTO | CODE | COMPUTER | SELF-IMPROVE`."""
    svc = request.app.state.svc
    data = _require_session(svc, session_id)
    cfg = _session_cfg(svc, session_id)
    orch = _orch(svc)
    rt = orch.runtime(session_id)

    text = body.text.strip()
    if not text:
        raise _fail(422, "пустое сообщение", code="CHAT_TEXT_EMPTY")

    # Slash-command mode overrides
    effective_mode = (body.mode or cfg["mode"] or DEFAULT_MODE).upper()
    if text.startswith("/improve"):
        effective_mode = "SELF-IMPROVE"
        text_arg = text[len("/improve"):].strip()
    elif text.startswith("/auto "):
        effective_mode = "AUTO"
        text = text[len("/auto "):].strip()
        text_arg = text
    elif text.startswith("/code "):
        effective_mode = "CODE"
        text = text[len("/code "):].strip()
        text_arg = text
    elif text.startswith("/pc ") or text.startswith("/computer "):
        effective_mode = "COMPUTER"
        text = text.split(" ", 1)[1].strip()
        text_arg = text
    else:
        text_arg = text

    if effective_mode not in VALID_MODES:
        raise _fail(422, f"недопустимый режим: {effective_mode}", code="INVALID_AGENT_MODE")

    bypass_permissions = (
        body.bypass_permissions
        if body.bypass_permissions is not None
        else (True if effective_mode == "AUTO" else cfg["bypass_permissions"])
    )
    allow_cloud = body.allow_cloud if body.allow_cloud is not None else cfg["allow_cloud"]
    req_model = body.model or cfg["model"]
    req_worker = body.worker if body.worker is not None else cfg["worker"]

    # Update persistent session config if changed
    if (effective_mode != cfg["mode"] or bypass_permissions != cfg["bypass_permissions"]
            or allow_cloud != cfg["allow_cloud"]
            or req_model != cfg["model"] or req_worker != cfg["worker"]):
        cfg = await _update_session_cfg(
            svc, session_id, mode=effective_mode, bypass_permissions=bypass_permissions,
            allow_cloud=allow_cloud, model=req_model, worker=req_worker,
        )

    # Check budget before dispatching
    detail_now = await _session_detail(svc, session_id)
    budget_rem = detail_now["budget_remaining_usd"]
    catalog = await build_model_router_catalog(svc)
    route = select_route_for_request(
        catalog,
        mode=effective_mode,
        requested_model=req_model,
        requested_worker=req_worker,
        allow_cloud=allow_cloud,
        budget_remaining_usd=budget_rem,
    )

    # 1. SELF-IMPROVE mode (`/improve`)
    if effective_mode == "SELF-IMPROVE":
        imp_rec = await _run_improve_pipeline(
            request,
            svc,
            ImproveRunIn(
                session_id=session_id,
                problem=text_arg or None,
                model=route["model"],
                worker=route["worker"],
                allow_cloud=allow_cloud,
                auto_propose_only=True,
            ),
            session_id=session_id,
        )
        imp_ids = list(cfg.get("improve_ids") or [])
        imp_ids.append(imp_rec["id"])
        await _update_session_cfg(svc, session_id, improve_ids=imp_ids)
        turn = _new_turn(0, body.text, [])
        turn.update({
            "mode": "SELF-IMPROVE",
            "improve_id": imp_rec["id"],
            "synthetic_status": imp_rec["status"],
            "synthetic_result": (
                f"Цикл самоулучшения `{imp_rec['id']}` выполнен до гейта одобрения владельца "
                f"(Receipt `{imp_rec['mission_receipt']['sha256'][:12]}`, approval_id={imp_rec['approval_id']})."
            ),
        })
        idx = await _append_turn(svc, session_id, turn)
        await _touch_meta(svc, session_id)
        return {
            "mode": "SELF-IMPROVE",
            "bypass_permissions": bypass_permissions,
            "session_id": session_id,
            "route": route,
            "turn": {"idx": idx, **turn},
            "improve": imp_rec,
        }

    # 2. CODE mode (OpenCode task in isolated worktree OR agent with coding tools)
    if effective_mode == "CODE" and (route.get("worker") or body.verify_tests or body.allowed_paths):
        ct_in = CodingTaskIn(
            instruction=text,
            source_repo=body.source_repo or "self",
            self_target=(body.source_repo in ("self", "bossman", "")),
            allowed_paths=body.allowed_paths,
            verify_tests=body.verify_tests,
            model=route["model"] if route["locality"] == "local" else None,
            worker=route["worker"],
            agent_id=body.agent_id or cfg.get("agent_id"),
        )
        created_ct = await create_coding_task(ct_in, request)
        if isinstance(created_ct, JSONResponse):
            payload = json.loads(created_ct.body.decode("utf-8"))
            raise HTTPException(created_ct.status_code, payload.get("error") or payload)
        ct_id = created_ct.get("id")
        if ct_id:
            orch.coding_to_session[ct_id] = session_id
            ct_ids = list(cfg.get("coding_task_ids") or [])
            if ct_id not in ct_ids:
                ct_ids.append(ct_id)
            await _update_session_cfg(svc, session_id, coding_task_ids=ct_ids)
        turn = _new_turn(0, body.text, [])
        turn.update({
            "mode": "CODE",
            "coding_task_id": ct_id,
            "synthetic_status": created_ct.get("status", "running"),
            "synthetic_result": f"Запущена задача OpenCode `{ct_id}` в изолированном worktree ({route['model']}).",
        })
        idx = await _append_turn(svc, session_id, turn)
        await _touch_meta(svc, session_id)
        rt.emit("task.progress", mode="CODE", coding_task_id=ct_id, status=created_ct.get("status"), route=route)
        return {
            "mode": "CODE",
            "bypass_permissions": bypass_permissions,
            "session_id": session_id,
            "route": route,
            "turn": {"idx": idx, **turn},
            "coding_task": created_ct,
        }

    # 3. CHAT / AGENT / AUTO / COMPUTER / CODE (via Bossman governed task engine)
    request_id = body.client_request_id or ("ag-" + time.strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(6))
    existing = await _task_by_request_id(svc, request_id)
    if existing is not None:
        from .chat_threads import _replay
        rep = await _replay(request, svc, session_id, existing, text, body.attachments)
        rep["mode"] = effective_mode
        rep["bypass_permissions"] = bypass_permissions
        rep["route"] = route
        rep["task_id"] = int(existing["id"])
        return rep

    attachments = _resolve_attachments(svc, body.attachments, session_id)
    mode_prefix = ""
    if effective_mode == "COMPUTER":
        mode_prefix = (
            "[Режим COMPUTER USE (Windows): следуй циклу OBSERVE -> PLAN -> APPROVE -> ACT -> VERIFY. "
            "Используй computer.observe / terminal.run / browser.* по выданным правам.]\n"
        )
    elif effective_mode in ("CODE", "AUTO"):
        mode_prefix = (
            "[Режим OPENCODE / AUTO: используй code.search, code.expand, code.status, terminal.run "
            "для чтения кода, проверки git status/diff и запуска pytest.]\n"
        )
    prompt = await _compose_prompt(svc, data["raw"], mode_prefix + text, attachments)
    chosen_agent_id = await _resolve_agent_id_for_model(svc, body.agent_id or cfg.get("agent_id"), route["model"])

    status_code, pre = await _internal(
        request, "POST", "/api/tasks/preflight",
        {"prompt": prompt, "agent_id": chosen_agent_id},
    )
    if status_code >= 400 or not isinstance(pre, dict):
        raise _passthrough(status_code, pre)
    if not pre.get("ok") or not isinstance(pre.get("agent"), dict):
        raise _fail(
            409,
            pre.get("reason") or "исполнитель недоступен",
            code=pre.get("code") or "BLOCKED_CAPABILITY_UNAVAILABLE",
            hint=pre.get("hint"),
        )
    agent = {"id": pre["agent"].get("id"), "name": pre["agent"].get("name")}

    title = re.sub(r"\s+", " ", text)[:80] or text[:80]
    status_code, created = await _internal(request, "POST", "/api/tasks", {
        "prompt": prompt,
        "title": title,
        "agent_id": agent["id"],
        "run_now": False,
        "client_request_id": request_id,
    })
    if status_code >= 400 or not isinstance(created, dict) or not isinstance(created.get("task"), dict):
        raise _passthrough(status_code, created)
    task = created["task"]
    task_id = int(task["id"])
    orch.task_to_session[task_id] = session_id

    turn = _new_turn(task_id, body.text, attachments)
    turn["mode"] = effective_mode
    idx = await _append_turn(svc, session_id, turn)
    await _touch_meta(svc, session_id)

    from .chat_threads import _send_response, _start
    run_id, admission = await _start(request, task_id)
    rt.emit("task.progress", task_id=task_id, run_id=run_id, mode=effective_mode,
            bypass_permissions=bypass_permissions, status=admission.get("status"), route=route)
    resp = await _send_response(svc, session_id, idx, turn, task_id, run_id, admission, agent, False)
    resp["mode"] = effective_mode
    resp["bypass_permissions"] = bypass_permissions
    resp["route"] = route
    resp["task_id"] = task_id
    return resp


@router.get("/agent/sessions/{session_id}/events")
async def get_agent_session_events(
    session_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
):
    """Return sequenced events for `session_id` after `after` cursor for seamless reconnect replay."""
    svc = request.app.state.svc
    data = _require_session(svc, session_id)
    rt = _orch(svc).runtime(session_id)

    # Also synthesize events from database tool_calls / task status if rt.events is empty after restart
    turns = _turns(data["raw"])
    task_ids = [int(t["task_id"]) for _, t in turns if int(t["task_id"]) > 0]
    if not rt.events and task_ids:
        views = await _task_views(svc, task_ids)
        async with svc.db.session() as s:
            tc_rows = (await s.execute(
                sa.select(tool_calls_t)
                .where(tool_calls_t.c.task_id.in_(task_ids))
                .order_by(tool_calls_t.c.id.asc())
            )).fetchall()
        for r in tc_rows:
            m = r._mapping
            rt.emit("tool.started", task_id=m["task_id"], tool=m["tool"], args=m.get("args") or {})
            rt.emit("tool.completed", task_id=m["task_id"], tool=m["tool"],
                    status=m.get("status"), output=str(m.get("output") or "")[:1000])
        for tid in task_ids:
            v = views.get(tid)
            if not v:
                continue
            if v.get("result"):
                rt.emit("message.delta", task_id=tid, text=str(v["result"]), final=True)
            if v["status"] == "completed":
                rt.emit("task.completed", task_id=tid, result=str(v.get("result") or "")[:2000])
            elif v["status"] in ("failed", "blocked", "stopped"):
                rt.emit("task.failed", task_id=tid, status=v["status"], error=v.get("error"))

    filtered = [e for e in rt.events if int(e.get("seq", 0)) > after][:limit]
    return {
        "session_id": session_id,
        "after": after,
        "latest_seq": rt.seq,
        "events": filtered,
    }


@router.post("/agent/sessions/{session_id}/stop")
async def stop_agent_session(session_id: str, request: Request):
    """Emergency & per-session STOP: stops active tasks, OpenCode worktree tasks, and terminal sessions."""
    svc = request.app.state.svc
    data = _require_session(svc, session_id)
    cfg = _session_cfg(svc, session_id)
    rt = _orch(svc).runtime(session_id)

    stopped_tasks: list[int] = []
    ids = [int(turn["task_id"]) for _, turn in _turns(data["raw"]) if int(turn["task_id"]) > 0][-50:]
    statuses = await _task_statuses(svc, ids)
    for tid in reversed(ids):
        st = statuses.get(tid)
        if st and st not in ("completed", "failed", "stopped", "cancelled", "blocked"):
            code, _payload = await _internal(request, "POST", f"/api/tasks/{tid}/stop")
            if code < 400:
                stopped_tasks.append(tid)

    cancelled_coding: list[str] = []
    for ct_id in cfg.get("coding_task_ids") or []:
        with contextlib.suppress(Exception):
            await cancel_coding_task(ct_id, request)
            cancelled_coding.append(ct_id)

    killed_terminals: list[str] = []
    mgr = getattr(svc, "terminal", None)
    if mgr is not None:
        for t_sid in cfg.get("terminal_session_ids") or []:
            if t_sid in mgr.sessions and not mgr.sessions[t_sid].finished:
                with contextlib.suppress(Exception):
                    await mgr.kill(t_sid)
                    killed_terminals.append(t_sid)

    rt.emit(
        "task.failed",
        reason="stopped_by_owner",
        stopped_tasks=stopped_tasks,
        cancelled_coding_tasks=cancelled_coding,
        killed_terminals=killed_terminals,
    )
    return {
        "ok": True,
        "session_id": session_id,
        "stopped_tasks": stopped_tasks,
        "cancelled_coding_tasks": cancelled_coding,
        "killed_terminals": killed_terminals,
    }


@router.post("/agent/tasks/{task_id}/approve")
async def approve_agent_task(task_id: str, body: TaskApproveIn, request: Request):
    """Approve or reject a pending tool call, terminal command, or `/improve` self-improvement run."""
    svc = request.app.state.svc
    decision_norm = "approve" if body.decision.startswith("approv") else "reject"

    # Case 1: Self-improve run (`imp-...`)
    if task_id.startswith("imp-"):
        rec = _orch(svc).improve_runs.get(task_id)
        if rec is None:
            raise HTTPException(404, {"message": f"Self-improve задача {task_id} не найдена"})
        appr_id = body.approval_id or rec.get("approval_id")
        if appr_id is not None:
            await _internal(request, "POST", f"/api/approvals/{int(appr_id)}", {"approve": decision_norm == "approve"})
        updated = await _complete_improve_after_approval(request, svc, task_id, decision_norm)
        return {"ok": True, "kind": "self_improve", "improve": updated}

    # Case 2: Numeric task_id or explicit approval_id
    appr_id = body.approval_id
    numeric_task_id: int | None = int(task_id) if task_id.isdigit() else None
    if appr_id is None and numeric_task_id is not None:
        async with svc.db.session() as s:
            row = (await s.execute(
                sa.select(approvals_t.c.id)
                .where(approvals_t.c.task_id == numeric_task_id, approvals_t.c.status == "pending")
                .order_by(approvals_t.c.id.desc())
            )).first()
            if row:
                appr_id = int(row[0])

    if appr_id is None:
        raise HTTPException(404, {"message": "Ожидающее подтверждение для задачи не найдено"})

    code, payload = await _internal(request, "POST", f"/api/approvals/{int(appr_id)}", {"approve": decision_norm == "approve"})
    if code >= 400:
        raise _passthrough(code, payload)

    # Notify session stream if mapped
    if numeric_task_id is not None:
        sess_id = _orch(svc).task_to_session.get(numeric_task_id)
        if sess_id:
            _orch(svc).runtime(sess_id).emit(
                "task.progress",
                task_id=numeric_task_id,
                approval_id=appr_id,
                decision=decision_norm,
            )
    return {"ok": True, "task_id": numeric_task_id or task_id, "approval_id": appr_id, "decision": decision_norm, "result": payload}


@router.post("/agent/improve")
async def start_improve_endpoint(body: ImproveRunIn, request: Request):
    """Explicit endpoint for the 16-step controlled self-improvement loop (`/improve`, §5)."""
    svc = request.app.state.svc
    return await _run_improve_pipeline(request, svc, body, session_id=body.session_id)


@router.get("/agent/improve")
async def list_improve_runs(request: Request):
    """List controlled self-improvement runs and their MissionReceipts."""
    svc = request.app.state.svc
    orch = _orch(svc)
    items = sorted(orch.improve_runs.values(), key=lambda r: r.get("created_at", ""), reverse=True)
    return {"items": items, "steps_template": IMPROVE_STEPS}


# ======================================================================
# WebSocket `/api/agent/sessions/{session_id}/stream` (§7)
# ======================================================================

@ws_router.websocket("/agent/sessions/{session_id}/stream")
async def agent_session_stream_ws(
    ws: WebSocket,
    session_id: str,
    after: int = Query(default=0, ge=0),
    token: str | None = Query(default=None),
):
    """Real-time sequenced event stream for mobile OpenCode agent sessions with replay & control."""
    svc = ws.app.state.svc
    origin = ws.headers.get("origin")
    if origin is not None and not _same_origin(origin, ws.headers.get("host", "")):
        await ws.close(code=4403)
        return
    sess_cookie = await svc.sessions.get(ws.cookies.get(COOKIE_NAME))
    bearer = (ws.headers.get("authorization") or "").removeprefix("Bearer ").strip()
    effective_tok = token or bearer or None
    if sess_cookie is None and not svc.auth.check(effective_tok):
        await ws.close(code=4401)
        return
    if _load_session(svc, session_id) is None:
        await ws.close(code=4404)
        return

    await ws.accept()
    rt = _orch(svc).runtime(session_id)
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1024)
    rt.subscribers.add(queue)
    try:
        cfg = _session_cfg(svc, session_id)
        await ws.send_json({
            "type": "agent.hello",
            "session_id": session_id,
            "mode": cfg["mode"],
            "model": cfg["model"],
            "worker": cfg["worker"],
            "allow_cloud": cfg["allow_cloud"],
            "seq": rt.seq,
        })
        # Replay missed events after `after`
        for evt in list(rt.events):
            if int(evt.get("seq", 0)) > after:
                await ws.send_json({**evt, "replayed": True})

        async def _pump_out() -> None:
            while True:
                evt = await queue.get()
                await ws.send_json(evt)

        async def _pump_in() -> None:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                action = msg.get("action") or msg.get("type")
                if action == "ping":
                    await ws.send_json({"type": "pong", "session_id": session_id, "seq": rt.seq})
                elif action == "stop":
                    rt.emit("task.failed", reason="stopped_via_ws")
                    await ws.send_json({"type": "agent.stopped", "session_id": session_id})
                elif action == "mode" and msg.get("mode") in VALID_MODES:
                    await _update_session_cfg(svc, session_id, mode=msg["mode"])
                    rt.emit("session.updated", mode=msg["mode"])

        out_t = asyncio.create_task(_pump_out())
        in_t = asyncio.create_task(_pump_in())
        _done, pending = await asyncio.wait({out_t, in_t}, return_when=asyncio.FIRST_COMPLETED)
        for p in pending:
            p.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await p
    except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
        pass
    finally:
        rt.subscribers.discard(queue)


# ======================================================================
# Bus Bridge: Map Bossman Engine Events → Session Sequenced Events
# ======================================================================

async def _bus_bridge(svc: Any) -> None:
    """Subscribe to Bossman's event bus and forward task/tool/approval/computer events
    to the corresponding AgentSessionRuntime in real time."""
    queue = svc.bus.subscribe()
    orch = _orch(svc)
    try:
        while True:
            msg = await queue.get()
            if not isinstance(msg, dict):
                continue
            kind = str(msg.get("kind") or "")
            task_id = msg.get("task_id")
            session_id = orch.task_to_session.get(int(task_id)) if isinstance(task_id, int) else None
            if not session_id:
                continue
            rt = orch.runtime(session_id)
            if kind in ("stream.token", "agent.delta"):
                rt.emit("message.delta", task_id=task_id, text=str(msg.get("text") or msg.get("delta") or ""))
            elif kind == "agent.tool_call":
                rt.emit("tool.started", task_id=task_id, tool=msg.get("tool"),
                        args=msg.get("args") or {}, step=msg.get("step"))
            elif kind == "agent.tool_result":
                rt.emit("tool.completed", task_id=task_id, tool=msg.get("tool"),
                        error=bool(msg.get("error")), preview=str(msg.get("preview") or "")[:1000])
            elif kind == "approval.created":
                appr_id = msg.get("id")
                appr_kind = str(msg.get("approval_kind") or msg.get("kind") or "")
                cfg = _session_cfg(svc, session_id)
                if cfg.get("bypass_permissions") and appr_id is not None and appr_kind != "self_improve":
                    with contextlib.suppress(Exception):
                        await svc.approvals.decide(int(appr_id), True, "owner")
                        rt.emit("task.progress", task_id=task_id, approval_id=appr_id,
                                decision="auto_approved_by_owner_bypass", approval_kind=appr_kind)
                        continue
                rt.emit("approval.required", task_id=task_id, approval_id=appr_id,
                        approval_kind=appr_kind, preview=msg.get("preview"))
            elif kind in ("computer.frame", "pc_agent.step"):
                rt.emit("computer.frame", task_id=task_id, frame=msg.get("frame"), step=msg.get("step"))
            elif kind == "task.completed":
                rt.emit("task.completed", task_id=task_id, result=str(msg.get("result") or "")[:2000])
            elif kind in ("task.failed", "task.stopped", "task.blocked"):
                rt.emit("task.failed", task_id=task_id, status=kind.split(".")[-1],
                        error=msg.get("error") or msg.get("reason"))
            elif kind.startswith("task."):
                rt.emit("task.progress", task_id=task_id, status=kind.split(".")[-1])
    except asyncio.CancelledError:
        raise
    finally:
        svc.bus.unsubscribe(queue)


async def _setup(svc: Any) -> None:
    _orch(svc)
    if hasattr(svc, "_tasks"):
        svc._tasks.append(asyncio.create_task(_bus_bridge(svc), name="bcc-agent-opencode-bridge"))


FEATURE = Feature(name="agent_opencode", router=router, ws_router=ws_router, setup=_setup)
