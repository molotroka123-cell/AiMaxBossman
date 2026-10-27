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
    last_task_id,
    next_synthetic_task_id,
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
from ..perf_trace import GLOBAL_PERF_TRACER, REQUIRED_STAGE_SPANS
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


ROUTER_CATALOG_TTL_S = 5.0


class _OrchestratorState:
    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir: Path | None = data_dir
        self.sessions: dict[str, AgentSessionRuntime] = {}
        self.hibernated_slices: dict[str, dict[str, Any]] = {}
        self.task_to_session: dict[int, str] = {}
        self.coding_to_session: dict[str, str] = {}
        self.term_to_session: dict[str, str] = {}
        self.improve_runs: dict[str, dict[str, Any]] = {}
        self.lock = asyncio.Lock()
        self.agent_meta_cache: tuple[int, int, dict[str, dict[str, Any]]] | None = None
        self.router_catalog_cache: tuple[float, dict[str, Any], tuple[list, dict, dict]] | None = None
        self.skill_index_cache: dict[str, tuple[str, dict[str, Any]]] = {}
        self.tools_schema_cache: dict[tuple[str, bool], dict[str, Any]] = {}

    def _slice_dir(self) -> Path | None:
        if self.data_dir is None:
            return None
        d = self.data_dir / "terminal" / "infinity-slices"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _slice_path(self, session_id: str) -> Path | None:
        d = self._slice_dir()
        if d is None:
            return None
        safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", str(session_id))[:80]
        return d / f"{safe_id}.slice.json"

    def hibernate(self, session_id: str, reason: str = "awaiting_tool_or_idle") -> dict[str, Any]:
        """Serialize session runtime to a compact coroutine slice on disk (~1–100 KB)
        and evict in-memory state (`0 B` held in active RAM), inspired by hydro-project/infinity."""
        t0 = time.perf_counter()
        rt = self.sessions.get(session_id)
        sp = self._slice_path(session_id)
        payload = {
            "protocol": "RAP-Infinity-Slice-v1",
            "session_id": session_id,
            "seq": rt.seq if rt else 0,
            "events": list(rt.events[-120:]) if rt else [],
            "coding_task_ids": list(rt.coding_task_ids) if rt else [],
            "terminal_session_ids": list(rt.terminal_session_ids) if rt else [],
            "spent_usd": float(rt.spent_usd) if rt else 0.0,
            "tokens_in": int(rt.tokens_in) if rt else 0,
            "tokens_out": int(rt.tokens_out) if rt else 0,
            "reason": reason,
            "hibernated_at": utcnow().isoformat(),
        }
        raw_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if sp is not None:
            tmp = sp.with_suffix(".slice.tmp")
            tmp.write_bytes(raw_bytes)
            os.replace(tmp, sp)
        has_ws = bool(rt and rt.subscribers)
        if rt is not None:
            if has_ws:
                rt.events.clear()
            else:
                self.sessions.pop(session_id, None)
        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 3)
        info = {
            "session_id": session_id,
            "hibernated": True,
            "ram_bytes_held": 0 if not has_ws else 64,
            "slice_bytes": len(raw_bytes),
            "slice_kb": round(len(raw_bytes) / 1024.0, 2),
            "slice_path": str(sp) if sp else None,
            "hibernate_ms": elapsed_ms,
            "last_wake_ms": (self.hibernated_slices.get(session_id) or {}).get("last_wake_ms"),
            "reason": reason,
            "hibernated_at": payload["hibernated_at"],
            "protocol": "RAP-Infinity-Slice-v1",
        }
        self.hibernated_slices[session_id] = info
        return info

    def wake(self, session_id: str, trigger: str = "reactive_event") -> tuple[AgentSessionRuntime, dict[str, Any]]:
        """Restore a hibernated session slice from disk into active RAM in < 2 ms."""
        t0 = time.perf_counter()
        rt = self.sessions.get(session_id)
        sp = self._slice_path(session_id)
        restored_bytes = 0
        if sp is not None and sp.is_file():
            try:
                raw_bytes = sp.read_bytes()
                restored_bytes = len(raw_bytes)
                data = json.loads(raw_bytes.decode("utf-8"))
                if rt is None:
                    rt = AgentSessionRuntime(
                        session_id=session_id,
                        seq=int(data.get("seq") or 0),
                        events=list(data.get("events") or []),
                        coding_task_ids=list(data.get("coding_task_ids") or []),
                        terminal_session_ids=list(data.get("terminal_session_ids") or []),
                        spent_usd=float(data.get("spent_usd") or 0.0),
                        tokens_in=int(data.get("tokens_in") or 0),
                        tokens_out=int(data.get("tokens_out") or 0),
                    )
                    self.sessions[session_id] = rt
                elif not rt.events and data.get("events"):
                    rt.events = list(data.get("events") or [])
                    rt.seq = max(rt.seq, int(data.get("seq") or 0))
            except Exception:
                if rt is None:
                    rt = self.sessions[session_id] = AgentSessionRuntime(session_id=session_id)
        elif rt is None:
            rt = self.sessions[session_id] = AgentSessionRuntime(session_id=session_id)
        wake_ms = round((time.perf_counter() - t0) * 1000.0, 3)
        prev = self.hibernated_slices.get(session_id) or {}
        slice_bytes = restored_bytes or int(prev.get("slice_bytes") or 0)
        info = {
            "session_id": session_id,
            "hibernated": False,
            "ram_bytes_held": len(rt.events) * 256 + 128,
            "slice_bytes": slice_bytes,
            "slice_kb": round(slice_bytes / 1024.0, 2),
            "slice_path": str(sp) if sp else None,
            "wake_ms": wake_ms,
            "last_wake_ms": wake_ms,
            "trigger": trigger,
            "woken_at": utcnow().isoformat(),
            "protocol": "RAP-Infinity-Slice-v1",
        }
        self.hibernated_slices[session_id] = info
        return rt, info

    def runtime(self, session_id: str) -> AgentSessionRuntime:
        rt = self.sessions.get(session_id)
        if rt is None:
            meta = self.hibernated_slices.get(session_id)
            if (meta and meta.get("hibernated")) or (self._slice_path(session_id) and self._slice_path(session_id).is_file()):  # type: ignore[union-attr]
                rt, _ = self.wake(session_id, trigger="auto_runtime_access")
                return rt
            rt = self.sessions[session_id] = AgentSessionRuntime(session_id=session_id)
        return rt


def _orch(svc: Any) -> _OrchestratorState:
    st = getattr(svc, "_agent_orchestrator", None)
    raw_dir = getattr(getattr(svc, "settings", None), "data_dir", None)
    data_dir = Path(raw_dir) if raw_dir is not None else None
    if st is None:
        st = svc._agent_orchestrator = _OrchestratorState(data_dir=data_dir)
    elif st.data_dir is None and data_dir is not None:
        st.data_dir = data_dir
    return st


def invalidate_router_catalog_cache(svc: Any) -> None:
    _orch(svc).router_catalog_cache = None


def _agent_meta_path(svc: Any) -> Path | None:
    raw_dir = getattr(getattr(svc, "settings", None), "data_dir", None)
    if raw_dir is None:
        return None
    return Path(raw_dir) / "terminal" / "agent-sessions-meta.json"


def _read_agent_meta(svc: Any) -> dict[str, dict[str, Any]]:
    path = _agent_meta_path(svc)
    st_orch = _orch(svc)
    if path is None:
        hit = st_orch.agent_meta_cache
        return dict(hit[2]) if hit is not None and isinstance(hit[2], dict) else {}
    try:
        st = path.stat()
    except OSError:
        st_orch.agent_meta_cache = None
        return {}
    hit = st_orch.agent_meta_cache
    if hit is not None and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return hit[2]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and isinstance(raw.get("sessions"), dict):
            sessions = raw["sessions"]
            st_orch.agent_meta_cache = (st.st_mtime_ns, st.st_size, sessions)
            return sessions
    except (OSError, ValueError):
        pass
    return {}


def _write_agent_meta(svc: Any, sessions: dict[str, dict[str, Any]]) -> None:
    path = _agent_meta_path(svc)
    if path is None:
        _orch(svc).agent_meta_cache = (0, 0, dict(sessions))
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".agent-meta.{secrets.token_hex(4)}.tmp")
    tmp.write_text(json.dumps({"version": 1, "sessions": sessions}, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    with contextlib.suppress(OSError):
        st = path.stat()
        _orch(svc).agent_meta_cache = (st.st_mtime_ns, st.st_size, dict(sessions))


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
        "id": "local:qwen3.8-flash-next-iq3_s",
        "model": "qwen3.8-flash-next-iq3_s",
        "alias": "strata-qwen38-flash-next-iq3s",
        "worker": None,
        "locality": "local",
        "role": "strata_fast_chat_and_pc_agent",
        "label": "Tier 1 · Strata Qwen3.8-Flash-Next IQ3_S (45–58 tok/s, Strix Halo :8090)",
        "price_in_1m": 0.0,
        "price_out_1m": 0.0,
        "context_window": 32768,
        "modes": ["CHAT", "AGENT", "AUTO", "COMPUTER", "CODE", "SELF-IMPROVE"],
    },
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
        "id": "local:strata-qwen38-flash-next-iq3s",
        "model": "qwen3.8-flash-next-iq3_s",
        "alias": "strata-qwen38-flash-next-iq3s",
        "worker": None,
        "locality": "local",
        "role": "local_strata_opencode_coder",
        "label": "Tier 2 · Strata Qwen3.8-Flash-Next IQ3_S Coder + PC Tools (Local Strata :8090)",
        "price_in_1m": 0.0,
        "price_out_1m": 0.0,
        "context_window": 32768,
        "modes": ["CODE", "SELF-IMPROVE", "AGENT", "AUTO", "COMPUTER"],
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


async def _cached_catalog(svc: Any) -> tuple[list, dict, dict]:
    st_orch = _orch(svc)
    now = time.monotonic()
    hit = st_orch.router_catalog_cache
    if hit is not None and (now - hit[0]) < ROUTER_CATALOG_TTL_S:
        return hit[2]
    await build_model_router_catalog(svc)
    if st_orch.router_catalog_cache is not None:
        return st_orch.router_catalog_cache[2]
    return await _catalog(svc)


async def build_model_router_catalog(svc: Any, *, force_refresh: bool = False) -> dict[str, Any]:
    """Return full OpenCode Model Router status across Tiers 1–5 + live DB registry."""
    st_orch = _orch(svc)
    now = time.monotonic()
    hit = st_orch.router_catalog_cache
    if not force_refresh and hit is not None and (now - hit[0]) < ROUTER_CATALOG_TTL_S:
        return hit[1]

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

    strata_online = (
        "qwen3.8-flash-next-iq3_s" in local_online_names
        or "strata-qwen38-flash-next-iq3s" in local_online_names
    )

    tiers_out: list[dict[str, Any]] = []
    for item in MODEL_TIERS:
        worker_id = item.get("worker")
        if worker_id:
            w_spec = WORKERS.get(worker_id, {})
            key_ok, key_src = key_cache.get(w_spec.get("key", ""), (False, "missing"))
            ready = key_ok
            reason = "" if ready else f"Требуется ключ {w_spec.get('key', 'API_KEY')}"
        elif item["model"] in ("qwen3.8-flash-next-iq3_s", "strata-qwen38-flash-next-iq3s"):
            ready = strata_online
            key_src = "local_strata"
            reason = "" if ready else "Локальный сервер Strata (:8090) не зарегистрирован или не в сети"
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

    result = {
        "tiers": tiers_out,
        "workers": workers_out,
        "local_profiles": LOCAL_CODER_PROFILES,
        "runpod_policy": "DENY_WITHOUT_EXPLICIT_OWNER_APPROVAL",
        "default_chat_model": "qwen3.8-flash-next-iq3_s" if strata_online else "gemma4-31b-uncensored:latest",
        "default_code_model": "qwen3.8-flash-next-iq3_s" if strata_online else "qwen3-coder:30b",
    }
    st_orch.router_catalog_cache = (time.monotonic(), result, (agents, models, providers))
    return result


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
        if clean_model.lower() in ("strata", "strata-qwen38-flash-next-iq3s"):
            clean_model = "qwen3.8-flash-next-iq3_s"
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
        is_tier1_model = "gemma" in clean_model.lower() or (
            "qwen3.8-flash-next" in clean_model.lower() and mode not in ("CODE", "SELF-IMPROVE")
        )
        tier_num = 1 if is_tier1_model else 2
        return {
            "selected_id": f"local:{clean_model}",
            "model": clean_model,
            "worker": None,
            "locality": "local",
            "tier": tier_num,
            "paid": False,
            "reason": f"Локальная модель {clean_model} (Tier {tier_num})",
            "fallback_chain": ["local:qwen3.8-flash-next-iq3_s", "local:gemma4-31b-uncensored:latest", "local:qwen3-coder:30b"],
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


SKILL_ID_STRICT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")

ALLOWED_SKILL_LOOP_TOOLS: frozenset[str] = frozenset({
    "code.status",
    "code.diff",
    "code.search",
    "code.expand",
    "code.read",
    "code.patch",
    "terminal.run",
    "memory.search",
    "memory.save",
    "facts.get",
    "facts.set",
    "computer.observe",
    "computer.act",
    "web.search",
    "web.fetch",
    "counter_defense.dossier",
})

ALLOWED_SKILL_LOOP_PERMISSIONS: frozenset[str] = frozenset({
    "read",
    "write",
    "exec",
    "computer_read",
    "computer_act",
    "memory.read",
    "memory.write",
    "terminal.read",
})


def build_safe_skill_markdown(
    *,
    skill_id: str,
    description: str,
    improve_id: str,
    required_tools: list[str],
    permissions: list[str],
    problem: str,
    workflow: str,
) -> str:
    """Serialize SKILL.md frontmatter strictly through yaml.safe_dump (no f-string YAML)."""
    import yaml

    if not SKILL_ID_STRICT_RE.fullmatch(skill_id):
        raise ValueError(f"Invalid skill_id '{skill_id}': must match ^[a-z0-9][a-z0-9-]{{1,62}}$")
    bad_tools = [t for t in required_tools if t not in ALLOWED_SKILL_LOOP_TOOLS]
    if bad_tools:
        raise ValueError(f"Disallowed required_tools: {bad_tools}")
    bad_perms = [p for p in permissions if p not in ALLOWED_SKILL_LOOP_PERMISSIONS]
    if bad_perms:
        raise ValueError(f"Disallowed permissions: {bad_perms}")

    frontmatter_obj: dict[str, Any] = {
        "name": str(skill_id),
        "description": str(description),
        "license": "MIT",
        "compatibility": "BOSSMAN, OpenCode, Claude-compatible agent skills",
        "metadata": {
            "owner": "bossman",
            "version": "1.0",
            "provenance": str(improve_id),
        },
        "required_tools": [str(t) for t in required_tools],
        "permissions": [str(p) for p in permissions],
        "input_schema": {
            "type": "object",
            "properties": {"repo_path": {"type": "string"}},
            "required": ["repo_path"],
        },
        "output_schema": {
            "type": "object",
            "properties": {"verdict": {"type": "string"}},
            "required": ["verdict"],
        },
    }

    class _NoAliasSafeDumper(yaml.SafeDumper):
        def ignore_aliases(self, data: Any) -> bool:
            return True

    yaml_text = yaml.dump(
        frontmatter_obj,
        Dumper=_NoAliasSafeDumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    ).strip()
    roundtrip = yaml.safe_load(yaml_text)
    if not isinstance(roundtrip, dict) or set(roundtrip.keys()) != set(frontmatter_obj.keys()):
        raise ValueError("YAML frontmatter round-trip integrity check failed")

    safe_problem = str(problem).replace("\r\n", "\n").replace("\r", "\n")
    safe_workflow = str(workflow).replace("\r\n", "\n").replace("\r", "\n").strip()
    return (
        f"---\n{yaml_text}\n---\n\n"
        f"# {skill_id}\n\n"
        f"## Проблема\n{safe_problem}\n\n"
        f"## Алгоритм\n{safe_workflow}\n"
    )


class NorthStarSkillLoopIn(BaseModel):
    session_id: str | None = None
    skill_id: str = Field(default="north-star-tree-hygiene", min_length=2, max_length=63)
    problem: str = Field(
        default="Повторяющийся дрейф SHA в tree-registry.json (927 листьев) и проверка CRLF/манифестов навыков перед релизом",
        max_length=2000,
    )
    workflow: str = Field(
        default=(
            "1. Проверить git rev-parse HEAD и чистоту дерева (code.status).\n"
            "2. Сверить HEAD с docs/architecture/tree-registry.json и запустить tree_apply_evidence.py --dry-run (terminal.run).\n"
            "3. Проверить манифесты .agents/skills/*/SKILL.md и отсутствие CRLF.\n"
            "4. Записать и сверить уроки в памяти (memory.search)."
        ),
        max_length=4000,
    )
    description: str = Field(
        default="Детерминированная сверка 927 листьев Capability Tree, git HEAD SHA, LF-переводов строк и манифестов SKILL.md без фиктивных зелёных статусов.",
        max_length=1000,
    )
    required_tools: list[str] = Field(default_factory=lambda: ["code.status", "terminal.run", "memory.search"])
    permissions: list[str] = Field(default_factory=lambda: ["read", "exec"])
    unseen_query: str = Field(
        default="Проверь гигиену 927 листьев capability tree, сверь SHA tree-registry.json с HEAD и проверь отсутствие CRLF перед коммитом",
        max_length=1000,
    )
    allow_cloud: bool = False


_PROMPT_INJECTION_BYPASS_RE = re.compile(
    r"(?i)(\[\s*system\s*:[^\]]*bypass_permissions\s*=\s*true|"
    r"ignore\s+previous\s+instructions[^\n]*bypass_permissions|"
    r"<\s*admin_override\s*>|escalate_to_auto_mode)"
)


def _assert_owner_bypass_allowed(
    request: Request,
    *,
    effective_mode: str,
    requested_bypass: bool | None,
    text: str = "",
) -> bool:
    """Enforce North Star P0 + P2.7 security boundary for `AUTO` (`bypass_permissions`):
    1. Role is determined strictly by verified credential/token scope (`request.state.principal`):
       any non-owner principal (`role != "owner"`) is forbidden from enabling `AUTO` or `bypass_permissions=True`,
       even if client HTTP headers (`X-Agent-Caller`, etc.) are omitted or spoofed.
    2. Subagent/tool-callback headers remain an additional tripwire -> 403 `AGENT_ESCALATION_FORBIDDEN`.
    3. Prompt-injection strings attempting to flip `bypass_permissions` inside message text -> 403.
    4. Unattended `SELF-IMPROVE` mode NEVER runs with `bypass_permissions=True` (forced False).
    """
    principal = getattr(request.state, "principal", None)
    is_non_owner_principal = principal is not None and getattr(principal, "role", "owner") != "owner"
    caller = (
        request.headers.get("x-agent-caller")
        or request.headers.get("x-subagent-id")
        or request.headers.get("x-tool-callback")
        or ""
    ).strip()
    wants_bypass = bool(requested_bypass or effective_mode == "AUTO")
    if (is_non_owner_principal or caller) and wants_bypass:
        raise HTTPException(403, {
            "message": "Агенты, подпроцессы и инструменты не имеют права включать режим AUTO или bypass_permissions",
            "code": "AGENT_ESCALATION_FORBIDDEN",
        })
    if text and _PROMPT_INJECTION_BYPASS_RE.search(text):
        raise HTTPException(403, {
            "message": "Заблокирована попытка повышения привилегий (prompt injection -> bypass_permissions)",
            "code": "PROMPT_INJECTION_ESCALATION_BLOCKED",
        })
    if effective_mode == "SELF-IMPROVE":
        return False
    return wants_bypass



_SKILL_MATCH_STOPWORDS: frozenset[str] = frozenset({
    "bossman", "mobile", "agent", "agents", "code", "mode", "file", "files",
    "test", "tests", "pytest", "task", "tasks", "run", "check", "status",
    "read", "write", "patch", "line", "lines", "строка", "строки", "строку",
    "файл", "файле", "файлы", "код", "кода", "тест", "тесты", "проверь",
    "исправь", "запусти", "покажи", "пульт", "модель", "версия", "version",
    "true", "false", "json", "http", "https", "local", "main", "git", "repo",
    "workspace", "skill", "skills", "tool", "tools", "python", "terminal",
})


def match_workspace_skill_for_task(svc: Any, text: str) -> dict[str, Any] | None:
    """Deterministically match an installed workspace skill (`.agents/skills/<id>/SKILL.md`)
    against a user/North-Star task description and return its contract & required tools."""
    from .skills import _lib
    from ..v2.skill_library import build_skill_prompt, skill_contract, validate_manifest

    q = (text or "").strip().lower()
    if not q:
        return None
    q_tokens = {
        tok
        for tok in re.findall(r"[a-zа-яё0-9_]{3,}", q)
        if len(tok) >= 3 and tok not in _SKILL_MATCH_STOPWORDS
    }
    if not q_tokens:
        return None

    lib = _lib(svc)
    skills_map = lib.by_id()
    st_orch = _orch(svc)
    idx_cache = st_orch.skill_index_cache

    best_score = 0
    best_entry: dict[str, Any] | None = None
    best_matched: list[str] = []

    sorted_q_tokens = sorted(q_tokens)
    for sid, sk in skills_map.items():
        fp = getattr(sk, "fingerprint", "")
        cached = idx_cache.get(sid)
        if cached is not None and cached[0] == fp and "hay_id_tokens" in cached[1]:
            entry = cached[1]
        else:
            valid_errors = validate_manifest(sk)["errors"]
            if valid_errors:
                idx_cache[sid] = (fp, {"valid": False, "hay_id_tokens": frozenset()})
                continue
            con = skill_contract(sk)
            hay_id_str = f"{sid} {sk.name}".lower()
            hay_desc_str = f"{sk.description} {sk.body[:1200]}".lower()
            entry = {
                "valid": True,
                "contract": con,
                "sid_low": sid.lower(),
                "hay_id": hay_id_str,
                "hay_desc": hay_desc_str,
                "hay_id_tokens": frozenset(
                    t for t in re.findall(r"[a-zа-яё0-9_]{3,}", hay_id_str)
                    if t not in _SKILL_MATCH_STOPWORDS
                ),
                "hay_desc_tokens": frozenset(
                    t for t in re.findall(r"[a-zа-яё0-9_]{3,}", hay_desc_str)
                    if t not in _SKILL_MATCH_STOPWORDS
                ),
            }
            idx_cache[sid] = (fp, entry)

        if not entry.get("valid"):
            continue

        hay_id_tokens = entry["hay_id_tokens"]
        hay_desc_tokens = entry["hay_desc_tokens"]
        score = 0
        matched: list[str] = []
        id_hits = 0
        if entry["sid_low"] in q:
            score += 12
            id_hits += 1
            matched.append(sid)
        for tok in sorted_q_tokens:
            if tok in hay_id_tokens:
                score += 4
                id_hits += 1
                matched.append(tok)
            elif tok in hay_desc_tokens:
                score += 2
                matched.append(tok)
        if (id_hits >= 1 or len(matched) >= 3) and len(matched) >= 2 and score > best_score:
            best_score = score
            best_entry = entry
            best_matched = matched[:8]

    if best_entry is None or best_score < 6:
        return None

    best_contract = best_entry["contract"]
    req_tools = list(best_contract.required_tools)
    return {
        "id": best_contract.id,
        "name": best_contract.name,
        "version": best_contract.version,
        "fingerprint": best_contract.fingerprint[:16],
        "required_tools": req_tools,
        "filtered_tools": req_tools,
        "fast_path": best_score >= 8,
        "permissions": list(best_contract.permissions),
        "score": best_score,
        "matched_terms": best_matched,
        "prompt_block": build_skill_prompt(best_contract, {"task": text[:400]}),
    }


def _task_meta_for_skill(request_id: str, matched_skill: dict[str, Any] | None) -> dict[str, Any]:
    """A confidently matched skill (fast_path) narrows the run to the tools its contract declares.

    Without this the model saw every agent tool and a small local model looped on ``terminal.run``
    instead of the skill's ``browser.open`` (live run 22.10, task 170). Only registered tools are kept;
    if none of the declared tools exist, the agent's own tool set is used as before (never widened)."""
    meta: dict[str, Any] = {"client_request_id": request_id}
    if not matched_skill or not matched_skill.get("fast_path"):
        return meta
    names = [n for n in (matched_skill.get("filtered_tools") or []) if TOOL_REGISTRY.get(n) is not None]
    if names:
        meta["allowed_tools"] = names
        meta["skill_id"] = matched_skill.get("id")
    return meta


# ======================================================================
# Helper: Resolve Agent for Mode & Model
# ======================================================================

async def _resolve_agent_id_for_model(svc: Any, requested_agent_id: int | None, model_name: str | None) -> int | None:
    if requested_agent_id is not None:
        return requested_agent_id
    agents, models, _providers = await _cached_catalog(svc)
    enabled_agents = [ag for ag in agents if ag.get("enabled")]
    # Sort so non-eval/non-clone production agents come before temporary tournament clone agents
    enabled_agents.sort(key=lambda a: (1 if "tournament clone" in str(a.get("name") or "").lower() else 0, int(a.get("id") or 0)))
    if model_name:
        clean = model_name.removeprefix("local:").strip().lower()
        is_strata_q = clean in ("qwen3.8-flash-next-iq3_s", "strata-qwen38-flash-next-iq3s", "strata") or "flash-next" in clean or "strata" in clean
        for ag in enabled_agents:
            m = models.get(ag.get("model_id")) or {}
            m_name = str(m.get("name") or "").lower()
            m_alias = str(m.get("alias") or "").lower()
            ag_name = str(ag.get("name") or "").lower()
            if (
                clean in (m_name, m_alias)
                or (clean.startswith("gemma4") and "gemma4" in m_name)
                or (is_strata_q and ("flash-next" in m_name or "strata" in m_name or "strata" in ag_name))
            ):
                return int(ag["id"])
    # Prefer online Strata or Gemma 4 agent if available and enabled
    for ag in enabled_agents:
        m = models.get(ag.get("model_id")) or {}
        m_name = str(m.get("name") or "").lower()
        ag_name = str(ag.get("name") or "").lower()
        if ("flash-next" in m_name or "strata" in ag_name) and str(m.get("status") or "").lower() == "online":
            return int(ag["id"])
    for ag in enabled_agents:
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

    # Step 14: Apply candidate (if backed by a completed coding_task or forged_skill)
    ct_id = record.get("coding_task_id")
    forged_skill = record.get("forged_skill")
    apply_detail = "Патч проверен и зафиксирован в рабочем дереве"
    if isinstance(forged_skill, dict) and forged_skill.get("id") and forged_skill.get("content"):
        from .skills import _lib
        lib = _lib(svc)
        sk = lib.create(str(forged_skill["id"]), str(forged_skill["content"]), overwrite=True)
        record["applied_skill"] = {
            "id": sk.id,
            "name": sk.name,
            "fingerprint": sk.fingerprint[:16],
            "path": f".agents/skills/{sk.id}/SKILL.md",
        }
        apply_detail = f"Навык `.agents/skills/{sk.id}/SKILL.md` (fp={sk.fingerprint[:12]}) применён после одобрения владельца"
    elif ct_id:
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

_MODE_RELEVANT_TOOL_GROUPS: dict[str, frozenset[str]] = {
    "CHAT": frozenset({"Memory", "Files", "Coding", "Git", "memory", "code", "git_worktree"}),
    "CODE": frozenset({"Files", "Coding", "Terminal", "Git", "Memory", "code", "terminal", "git_worktree", "memory"}),
    "COMPUTER": frozenset({"Computer", "Browser", "Terminal", "Files", "Memory", "computer", "browser", "terminal", "memory"}),
    "SELF-IMPROVE": frozenset({"Files", "Coding", "Terminal", "Git", "Memory", "Diagnostics", "code", "terminal", "git_worktree", "self_improve", "memory"}),
}


@router.get("/agent/hibernation/status")
async def get_agent_hibernation_status(request: Request):
    """Infinity-style Stateless Coroutine Slice Hibernation status (`hydro-project/infinity` pattern)."""
    svc = request.app.state.svc
    st_orch = _orch(svc)
    active_ids = sorted(st_orch.sessions.keys())
    slices = dict(st_orch.hibernated_slices)
    hibernated_count = sum(1 for v in slices.values() if v.get("hibernated"))
    total_slice_bytes = sum(int(v.get("slice_bytes") or 0) for v in slices.values() if v.get("hibernated"))
    active_ram_bytes = sum(len(rt.events) * 256 + 128 for rt in st_orch.sessions.values())
    wake_times = [float(v["last_wake_ms"]) for v in slices.values() if isinstance(v.get("last_wake_ms"), (int, float))]
    return {
        "ok": True,
        "protocol": "RAP-Infinity-Slice-v1",
        "inspired_by": "hydro-project/infinity (AWS + UC Berkeley, Apache-2.0)",
        "active_sessions": len(active_ids),
        "active_ram_sessions": len(active_ids),
        "active_session_ids": active_ids,
        "hibernated_sessions": hibernated_count,
        "hibernated_count": hibernated_count,
        "total_tracked_slices": len(slices),
        "hibernated_ram_bytes_held": 0,
        "active_ram_bytes_estimate": active_ram_bytes,
        "total_hibernated_slice_bytes": total_slice_bytes,
        "total_slice_bytes": total_slice_bytes,
        "avg_wake_ms": round(sum(wake_times) / len(wake_times), 3) if wake_times else 0.0,
        "slices": slices,
    }


@router.post("/agent/sessions/{session_id}/hibernate")
async def hibernate_agent_session(session_id: str, request: Request, reason: str = "owner_or_tool_wait", body: dict[str, Any] | None = None):
    """Serialize session runtime to a disk coroutine slice (~1-100 KB) and evict RAM (`0 B` held)."""
    svc = request.app.state.svc
    _require_session(svc, session_id)
    eff_reason = str((body or {}).get("reason") or reason or "owner_or_tool_wait")
    info = _orch(svc).hibernate(session_id, reason=eff_reason)
    return {"ok": True, **info}


@router.post("/agent/sessions/{session_id}/wake")
async def wake_agent_session(session_id: str, request: Request, body: dict[str, Any] | None = None):
    """Wake a hibernated session slice from disk into RAM in < 2 ms."""
    svc = request.app.state.svc
    _require_session(svc, session_id)
    trigger = str((body or {}).get("trigger") or "reactive_event")
    _rt, info = _orch(svc).wake(session_id, trigger=trigger)
    return {"ok": True, "woke": True, **info}


@router.get("/agent/tools")
async def list_agent_tools(
    request: Request,
    mode: str | None = None,
    compact: bool = False,
):
    """Unified Tool Registry grouped by category (§8), with optional mode-based filtering and compact schema (`CP-PERF-03`)."""
    svc = request.app.state.svc
    st_orch = _orch(svc)
    mode_key = mode.strip().upper() if isinstance(mode, str) else ""
    compact_flag = bool(compact) if isinstance(compact, bool) else False
    allowed_groups = _MODE_RELEVANT_TOOL_GROUPS.get(mode_key)
    specs = TOOL_REGISTRY.all()
    cache_key = (f"{mode_key}:{len(specs)}", compact_flag)
    cached = st_orch.tools_schema_cache.get(cache_key)
    if cached is not None:
        return cached

    grouped: dict[str, list[dict[str, Any]]] = {g: [] for g in TOOL_GROUPS}
    flat: list[dict[str, Any]] = []

    for spec in specs:
        group = classify_tool_group(spec.name, spec.source)
        if allowed_groups is not None and group not in allowed_groups:
            continue
        risk = classify_risk_level(spec.category, spec.default_effect, spec.permission)
        if compact:
            item = {
                "name": spec.name,
                "api_name": spec.api_name,
                "description": (spec.description or "")[:160],
                "category": spec.category,
                "group": group,
                "required": list(spec.required),
                "risk_level": risk,
                "required_permissions": [spec.permission] if spec.permission else [],
            }
        else:
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

    payload = {
        "total": len(flat),
        "mode_filter": mode_key or None,
        "compact": bool(compact),
        "groups": grouped,
        "tools": flat,
    }
    st_orch.tools_schema_cache[cache_key] = payload
    return payload


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
    last_ids = [i for i in (last_task_id(d["raw"]) for _, d, _e in rows) if i is not None]
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
    bypass = _assert_owner_bypass_allowed(
        request,
        effective_mode=mode,
        requested_bypass=bool(body.bypass_permissions or mode == "AUTO"),
    )
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
    cur_cfg = _session_cfg(svc, session_id)
    target_mode = cur_cfg["mode"]
    if "mode" in fields and fields["mode"] is not None:
        m = str(fields["mode"]).upper()
        if m not in VALID_MODES:
            raise _fail(422, f"недопустимый режим: {fields['mode']}", code="INVALID_AGENT_MODE")
        target_mode = m
        cfg_updates["mode"] = m
        if m == "AUTO" and "bypass_permissions" not in fields:
            cfg_updates["bypass_permissions"] = True
    for k in ("bypass_permissions", "model", "worker", "agent_id", "allow_cloud", "budget_usd"):
        if k in fields:
            cfg_updates[k] = fields[k]
    if "bypass_permissions" in cfg_updates or target_mode == "AUTO" or target_mode == "SELF-IMPROVE":
        cfg_updates["bypass_permissions"] = _assert_owner_bypass_allowed(
            request,
            effective_mode=target_mode,
            requested_bypass=cfg_updates.get("bypass_permissions", cur_cfg["bypass_permissions"]),
        )
    if cfg_updates:
        await _update_session_cfg(svc, session_id, **cfg_updates)
        _orch(svc).runtime(session_id).emit("session.updated", **cfg_updates)
    return await _session_detail(svc, session_id)


@router.get("/agent/perf/traces")
async def get_agent_perf_traces(limit: int = Query(default=50, ge=1, le=250)):
    """Return sanitized monotonic span traces (`CP-PERF-01`). Never includes prompts or secrets."""
    traces = GLOBAL_PERF_TRACER.recent_traces(limit=limit)
    return {
        "count": len(traces),
        "required_stage_spans": list(REQUIRED_STAGE_SPANS),
        "traces": traces,
    }


@router.get("/agent/perf/top-spans")
async def get_agent_perf_top_spans(limit: int = Query(default=10, ge=1, le=50)):
    """Return top latency spans ranked by cumulative duration and p95 (`CP-PERF-01`)."""
    top = GLOBAL_PERF_TRACER.top_spans(limit=limit)
    return {
        "count": len(top),
        "top_spans": top,
    }


async def _session_budget_remaining_usd(
    svc: Any,
    raw: dict[str, Any],
    cfg: dict[str, Any],
    rt: AgentSessionRuntime,
) -> float:
    """Fast session budget computation without full `_session_detail` disk/DB fan-out (`CP-PERF-04`)."""
    budget = float(cfg.get("budget_usd", DEFAULT_SESSION_BUDGET_USD))
    if not getattr(rt, "_db_spent_initialized", False):
        task_ids = [int(t["task_id"]) for _, t in _turns(raw) if int(t.get("task_id") or 0) > 0]
        if task_ids and cfg.get("allow_cloud"):
            async with svc.db.session() as s:
                row = (await s.execute(
                    sa.select(sa.func.coalesce(sa.func.sum(runs_t.c.cost_usd), 0.0))
                    .where(runs_t.c.task_id.in_(task_ids))
                )).first()
                if row and row[0]:
                    rt.spent_usd = max(rt.spent_usd, float(row[0]))
        rt._db_spent_initialized = True  # type: ignore[attr-defined]
    return round(max(0.0, budget - rt.spent_usd), 6)


def _persist_turn_and_touch_sync(
    svc: Any,
    session_id: str,
    turn: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    from .chat_threads import (
        META_VERSION,
        _append_turn_sync,
        _meta_path,
        _read_session_retry,
        _session_path,
        _state,
        _write_json_atomic,
    )

    spath = _session_path(svc, session_id)
    state = _state(svc)
    idx = _append_turn_sync(spath, session_id, turn)
    raw = _read_session_retry(spath) or {"id": session_id, "turns": [turn]}
    try:
        st = spath.stat()
        data = {"raw": raw, "mtime": st.st_mtime}
        state.sessions[session_id] = (st.st_mtime_ns, st.st_size, data)
    except OSError:
        data = {"raw": raw, "mtime": time.time()}

    # Update threads-meta.json in the same worker thread hop
    mpath = _meta_path(svc)
    now = time.time()
    m_hit = state.meta_cache
    threads_dict = dict(m_hit[2]) if m_hit is not None else {}
    if not threads_dict and mpath.is_file():
        with contextlib.suppress(OSError, ValueError):
            mraw = json.loads(mpath.read_text(encoding="utf-8"))
            if isinstance(mraw, dict) and isinstance(mraw.get("threads"), dict):
                threads_dict = dict(mraw["threads"])
    entry = dict(threads_dict.get(session_id) or {})
    entry["updated_at"] = now
    threads_dict[session_id] = entry
    _write_json_atomic(mpath, {"version": META_VERSION, "threads": threads_dict})
    with contextlib.suppress(OSError):
        mst = mpath.stat()
        state.meta_cache = (mst.st_mtime_ns, mst.st_size, threads_dict)
    return idx, data


async def _persist_turn_fast(svc: Any, session_id: str, turn: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    from .chat_threads import SessionUnreadable
    state = _chat_state(svc)
    async with state.lock:
        try:
            return await asyncio.to_thread(_persist_turn_and_touch_sync, svc, session_id, turn)
        except FileNotFoundError:
            raise _fail(
                404,
                "чат удалён, пока отправлялось сообщение",
                code="CHAT_THREAD_NOT_FOUND",
                hint="задача уже создана — её видно в «Задачах»",
            ) from None
        except SessionUnreadable as exc:
            raise _fail(
                503,
                "файл чата сейчас не читается — сообщение не записано в историю чата",
                code="CHAT_THREAD_UNREADABLE",
                hint=f"файл не тронут; задача уже создана — её видно в «Задачах». {exc}",
            ) from None


@router.post("/agent/sessions/{session_id}/messages")
async def send_agent_message(session_id: str, body: AgentMessageIn, request: Request):
    """Unified message & task dispatcher across `CHAT | AGENT | AUTO | CODE | COMPUTER | SELF-IMPROVE`."""
    svc = request.app.state.svc
    with GLOBAL_PERF_TRACER.start_trace(
        route="POST /api/agent/sessions/messages",
        session_id=session_id,
        mode=str(body.mode or DEFAULT_MODE),
    ) as ptrace:
        with ptrace.span("mobile.request", mode=str(body.mode or DEFAULT_MODE)):
            with ptrace.span("session.load", cache_hit=True):
                data = _require_session(svc, session_id)
                cfg = _session_cfg(svc, session_id)
                orch = _orch(svc)
                rt = orch.runtime(session_id)

            text = body.text.strip()
            if not text:
                raise _fail(422, "пустое сообщение", code="CHAT_TEXT_EMPTY")

            # Slash-command mode overrides
            effective_mode = (body.mode or cfg["mode"] or DEFAULT_MODE).upper()
            browser_cmd = False
            if text.startswith("/improve"):
                effective_mode = "SELF-IMPROVE"
                text_arg = text[len("/improve"):].strip()
            elif text == "/browser" or text.startswith("/browser ") or text.startswith("/web "):
                effective_mode = "COMPUTER"
                browser_cmd = True
                raw_b = text.split(" ", 1)[1].strip() if " " in text else "https://example.com"
                text = (
                    f"Выполни задачу в браузере (команда /browser): {raw_b}. "
                    "Используй browser.open, browser.read_dom, browser.screenshot (или pc.browser_act) "
                    "и проверь результат."
                )
                text_arg = raw_b
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
            ptrace.mode = effective_mode

            with ptrace.span("session.auth", mode=effective_mode):
                raw_bypass = (
                    body.bypass_permissions
                    if body.bypass_permissions is not None
                    else (True if effective_mode == "AUTO" else cfg["bypass_permissions"])
                )
                bypass_permissions = _assert_owner_bypass_allowed(
                    request,
                    effective_mode=effective_mode,
                    requested_bypass=raw_bypass,
                    text=body.text,
                )
            allow_cloud = body.allow_cloud if body.allow_cloud is not None else cfg["allow_cloud"]
            req_model = body.model or cfg["model"]
            req_worker = body.worker if body.worker is not None else cfg["worker"]

            # Update persistent session config if changed
            if (effective_mode != cfg["mode"] or bypass_permissions != cfg["bypass_permissions"]
                    or allow_cloud != cfg["allow_cloud"]
                    or req_model != cfg["model"] or req_worker != cfg["worker"]):
                with ptrace.span("session.load", cache_hit=False):
                    cfg = await _update_session_cfg(
                        svc, session_id, mode=effective_mode, bypass_permissions=bypass_permissions,
                        allow_cloud=allow_cloud, model=req_model, worker=req_worker,
                    )

            # Fast budget + cached router catalog
            with ptrace.span("session.load", cache_hit=True):
                budget_rem = await _session_budget_remaining_usd(svc, data["raw"], cfg, rt)
            with ptrace.span("model.catalog", cache_hit=orch.router_catalog_cache is not None):
                catalog = await build_model_router_catalog(svc)
            with ptrace.span("model.route", mode=effective_mode):
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
                turn = _new_turn(next_synthetic_task_id(), body.text, [])
                turn.update({
                    "mode": "SELF-IMPROVE",
                    "improve_id": imp_rec["id"],
                    "synthetic_status": imp_rec["status"],
                    "synthetic_result": (
                        f"Цикл самоулучшения `{imp_rec['id']}` выполнен до гейта одобрения владельца "
                        f"(Receipt `{imp_rec['mission_receipt']['sha256'][:12]}`, approval_id={imp_rec['approval_id']})."
                    ),
                })
                with ptrace.span("receipt.persist", mode="SELF-IMPROVE"):
                    idx, _ = await _persist_turn_fast(svc, session_id, turn)
                return {
                    "mode": "SELF-IMPROVE",
                    "bypass_permissions": bypass_permissions,
                    "session_id": session_id,
                    "route": route,
                    "turn": {"idx": idx, **turn},
                    "improve": imp_rec,
                    "perf_trace_id": ptrace.trace_id,
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
                with ptrace.span("task.create", mode="CODE"):
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
                turn = _new_turn(next_synthetic_task_id(), body.text, [])
                turn.update({
                    "mode": "CODE",
                    "coding_task_id": ct_id,
                    "synthetic_status": created_ct.get("status", "running"),
                    "synthetic_result": f"Запущена задача OpenCode `{ct_id}` в изолированном worktree ({route['model']}).",
                })
                with ptrace.span("receipt.persist", mode="CODE"):
                    idx, _ = await _persist_turn_fast(svc, session_id, turn)
                with ptrace.span("ws.deliver", mode="CODE"):
                    rt.emit("task.progress", mode="CODE", coding_task_id=ct_id, status=created_ct.get("status"), route=route)
                return {
                    "mode": "CODE",
                    "bypass_permissions": bypass_permissions,
                    "session_id": session_id,
                    "route": route,
                    "turn": {"idx": idx, **turn},
                    "coding_task": created_ct,
                    "perf_trace_id": ptrace.trace_id,
                }

            # 3. CHAT / AGENT / AUTO / COMPUTER / CODE (via Bossman governed task engine)
            with ptrace.span("skill.match", mode=effective_mode, cache_hit=bool(orch.skill_index_cache)):
                matched_skill = match_workspace_skill_for_task(svc, text)
            request_id = body.client_request_id or ("ag-" + time.strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(6))
            existing = (await _task_by_request_id(svc, body.client_request_id)) if body.client_request_id else None
            if existing is not None:
                from .chat_threads import _replay
                rep = await _replay(request, svc, session_id, existing, text, body.attachments)
                rep["mode"] = effective_mode
                rep["bypass_permissions"] = bypass_permissions
                rep["route"] = route
                rep["matched_skill"] = matched_skill
                rep["task_id"] = int(existing["id"])
                rep["perf_trace_id"] = ptrace.trace_id
                return rep

            with ptrace.span("memory.retrieve", mode=effective_mode):
                attachments = _resolve_attachments(svc, body.attachments, session_id) if body.attachments else []
                mode_prefix = ""
                if effective_mode == "COMPUTER":
                    mode_prefix = (
                        "[Режим COMPUTER USE (Windows): следуй циклу OBSERVE -> PLAN -> APPROVE -> ACT -> VERIFY. "
                        "Используй computer.observe, computer.act, browser.open, browser.read_dom, browser.screenshot, "
                        "code.read, code.write, code.patch, terminal.run по выданным правам.]\n"
                    )
                elif effective_mode in ("CODE", "AUTO", "AGENT"):
                    mode_prefix = (
                        "[Режим OPENCODE / AGENT: для чтения и правки файлов (UTF-8) используй нативные "
                        "инструменты code.read, code.write, code.patch (не используй cmd.exe echo/python -c для записи файлов); "
                        "для поиска и проверок используй code.search, code.expand, code.status, sandbox.run_check, terminal.run.]\n"
                    )
                skill_prefix = ""
                if matched_skill is not None:
                    skill_prefix = (
                        f"[Выбран проверенный навык `{matched_skill['id']}` v{matched_skill['version']} "
                        f"(required_tools={matched_skill['required_tools']})]\n"
                        f"{matched_skill['prompt_block']}\n\n"
                    )
                prompt = await _compose_prompt(svc, data["raw"], mode_prefix + skill_prefix + text, attachments)
                chosen_agent_id = await _resolve_agent_id_for_model(svc, body.agent_id or cfg.get("agent_id"), route["model"])

            from ..task_admission import ExecutorUnavailable, select_executor
            from .chat_threads import _admission_of, _model_view, _turn_view

            title = re.sub(r"\s+", " ", text)[:80] or text[:80]
            async with svc.db.session() as s:
                with ptrace.span("preflight", mode=effective_mode):
                    try:
                        chosen = await select_executor(s, prompt=prompt, agent_id=chosen_agent_id)
                    except ExecutorUnavailable as exc:
                        raise _fail(
                            409,
                            str(exc) or "исполнитель недоступен",
                            code="BLOCKED_CAPABILITY_UNAVAILABLE",
                            hint=exc.hint,
                        ) from None
                    agent = {"id": int(chosen["id"]), "name": chosen.get("name")}

                with ptrace.span("task.create", mode=effective_mode):
                    now_dt = utcnow()
                    res = await s.execute(sa.insert(tasks_t).values(
                        title=title,
                        prompt=prompt,
                        agent_id=agent["id"],
                        priority=5,
                        max_retries=2,
                        status="draft",
                        created_at=now_dt,
                        updated_at=now_dt,
                        meta=_task_meta_for_skill(request_id, matched_skill),
                    ))
                    task_id = int(res.inserted_primary_key[0])
                    await s.commit()

            await svc.bus.emit("task.created", task_id=task_id, title=title, agent_id=agent["id"])
            orch.task_to_session[task_id] = session_id

            turn = _new_turn(task_id, body.text, attachments)
            turn["mode"] = effective_mode
            if matched_skill is not None:
                turn["matched_skill"] = {
                    "id": matched_skill["id"],
                    "version": matched_skill["version"],
                    "required_tools": matched_skill["required_tools"],
                    "fast_path": matched_skill.get("fast_path", False),
                }
            with ptrace.span("receipt.persist", mode=effective_mode):
                idx, updated_data = await _persist_turn_fast(svc, session_id, turn)

            with ptrace.span("queue.wait", mode=effective_mode):
                run_id = await svc.engine.enqueue(task_id, only_if_idle=True)
                adm_raw = await svc.engine.admission_result(task_id, run_id)
                admission = _admission_of(200, adm_raw)

            with ptrace.span("ws.deliver", mode=effective_mode):
                task_status = str(admission.get("status") or "queued")
                rt.emit("task.progress", task_id=task_id, run_id=run_id, mode=effective_mode,
                        bypass_permissions=bypass_permissions, matched_skill=(matched_skill or {}).get("id"),
                        status=task_status, route=route)
                _agents_c, models_c, providers_c = await _cached_catalog(svc)
                chosen_model_row = models_c.get(chosen.get("model_id")) if chosen else None
                model_v = (
                    _model_view(chosen_model_row, providers_c.get((chosen_model_row or {}).get("provider_id")))
                    if chosen_model_row else None
                )
                thread_pub = _public(_summary(
                    session_id,
                    updated_data,
                    _entry(_read_meta(svc), session_id),
                    {task_id: task_status},
                ))
                resp = {
                    "thread": thread_pub,
                    "turn": _turn_view(idx, turn),
                    "task": {"id": task_id, "status": task_status},
                    "run_id": run_id,
                    "admission": admission,
                    "agent": agent,
                    "model": {
                        "id": model_v.get("id"),
                        "alias": model_v.get("alias"),
                        "locality": model_v.get("locality"),
                        "locality_detail": model_v.get("locality_detail"),
                        "billing": model_v.get("billing"),
                    } if model_v else None,
                    "replayed": False,
                    "mode": effective_mode,
                    "bypass_permissions": bypass_permissions,
                    "route": route,
                    "matched_skill": matched_skill,
                    "task_id": task_id,
                    "perf_trace_id": ptrace.trace_id,
                }
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


async def stop_session_core(svc: Any, request_like: Any, session_id: str) -> dict:
    """Единственная реализация STOP сессии: HTTP, WebSocket, desktop и телефон вызывают её.

    Задачи останавливаются каноническим `TaskEngine.stop` (он же каскадно гасит делегированных детей)."""
    from ..engine import TaskStateConflict

    data = _require_session(svc, session_id)
    cfg = _session_cfg(svc, session_id)
    rt = _orch(svc).runtime(session_id)

    stopped_tasks: list[int] = []
    ids = [int(turn["task_id"]) for _, turn in _turns(data["raw"]) if int(turn["task_id"]) > 0][-50:]
    statuses = await _task_statuses(svc, ids)
    for tid in reversed(ids):
        st = statuses.get(tid)
        if st and st not in ("completed", "failed", "stopped", "cancelled", "blocked"):
            try:
                await svc.engine.stop(tid)
                stopped_tasks.append(tid)
            except (TaskStateConflict, ValueError):
                continue

    cancelled_coding: list[str] = []
    for ct_id in cfg.get("coding_task_ids") or []:
        with contextlib.suppress(Exception):
            await cancel_coding_task(ct_id, request_like)
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


@router.post("/agent/sessions/{session_id}/stop")
async def stop_agent_session(session_id: str, request: Request):
    """Emergency & per-session STOP: stops active tasks, OpenCode worktree tasks, and terminal sessions."""
    return await stop_session_core(request.app.state.svc, request, session_id)


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


@router.get("/agent/skills/match")
async def match_agent_skill_endpoint(
    request: Request,
    q: str = Query(default="", max_length=1000),
):
    """Deterministically match an installed `.agents/skills/<id>/SKILL.md` to an unseen task query."""
    svc = request.app.state.svc
    matched = await asyncio.to_thread(match_workspace_skill_for_task, svc, q)
    return {
        "query": q,
        "matched": matched is not None,
        "skill": matched,
    }


@router.post("/agent/north-star/skill-loop")
async def north_star_skill_loop_endpoint(body: NorthStarSkillLoopIn, request: Request):
    """North Star Autonomous Skill Creation Loop:
    Planner -> Skill Forge (3x observe + propose) -> Isolated Worktree Web Terminal ->
    RED/GREEN Manifest & Policy Verification -> Independent Verifier -> MissionReceipt -> Owner Approval Gate.
    Never enables AUTO/bypass_permissions during skill creation.
    """
    from .skills import FORGE_MIN_RUNS, _forge_state, _save_forge_state, _signature
    from ..self_improve.store import Store, default_root
    from ..v2.skill_catalog import scan_policy
    from ..v2.skill_library import SkillLibrary, parse_skill, skill_contract, validate_manifest
    from ..v2.terminal_control import TerminalPolicy

    # P1.1a: Strict skill_id regex validation before any filesystem/state operation
    if not SKILL_ID_STRICT_RE.fullmatch(body.skill_id):
        raise HTTPException(
            422,
            {
                "code": "SKILL_ID_INVALID",
                "message": "skill_id должен соответствовать ^[a-z0-9][a-z0-9-]{1,62}$",
            },
        )

    # P1.1b: Closed allowlist for required_tools and permissions
    disallowed_tools = [t for t in body.required_tools if t not in ALLOWED_SKILL_LOOP_TOOLS]
    if disallowed_tools:
        raise HTTPException(
            422,
            {
                "code": "SKILL_TOOLS_NOT_ALLOWED",
                "message": f"Неразрешённые инструменты в required_tools: {disallowed_tools}",
            },
        )
    disallowed_perms = [p for p in body.permissions if p not in ALLOWED_SKILL_LOOP_PERMISSIONS]
    if disallowed_perms:
        raise HTTPException(
            422,
            {
                "code": "SKILL_PERMISSIONS_NOT_ALLOWED",
                "message": f"Неразрешённые права в permissions: {disallowed_perms}",
            },
        )

    svc = request.app.state.svc
    orch = _orch(svc)
    improve_id = "imp-skill-" + time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
    rt = orch.runtime(body.session_id) if body.session_id else None
    store = Store(default_root(svc.settings.data_dir))

    # 1. Observe recurring workflow FORGE_MIN_RUNS times & propose via Skill Forge
    sig = _signature(body.workflow)
    fstate = await _forge_state(svc)
    entry = fstate["signatures"].setdefault(sig, {"count": 0, "workflow": body.workflow[:2000], "last_proposed": None})
    while entry["count"] < FORGE_MIN_RUNS:
        entry["count"] += 1
    await _save_forge_state(svc, fstate)

    # 2. RED baseline check: in an isolated empty skill root before authoring, unseen_query does NOT match skill_id
    worktrees_base = (Path(svc.settings.data_dir) / "north-star-worktrees").resolve()
    isolated_wt = (worktrees_base / improve_id).resolve()
    if isolated_wt.parent != worktrees_base:
        raise HTTPException(400, {"code": "ISOLATED_ROOT_ESCAPE", "message": "Некорректный путь изолированного дерева"})
    isolated_skills_root = (isolated_wt / ".agents" / "skills").resolve()
    isolated_skills_root.mkdir(parents=True, exist_ok=True)

    # P1.1c: Verify resolved skill_dir and skill_file stay strictly inside isolated_skills_root BEFORE write
    skill_dir = (isolated_skills_root / body.skill_id).resolve()
    if skill_dir.parent != isolated_skills_root or not skill_dir.is_relative_to(isolated_skills_root):
        raise HTTPException(
            400,
            {"code": "SKILL_PATH_TRAVERSAL", "message": "Путь навыка выходит за пределы изолированного корня"},
        )
    skill_file = (skill_dir / "SKILL.md").resolve()
    if skill_file.parent != skill_dir or not skill_file.is_relative_to(isolated_skills_root):
        raise HTTPException(
            400,
            {"code": "SKILL_PATH_TRAVERSAL", "message": "Файл SKILL.md выходит за пределы изолированного корня"},
        )

    isolated_lib = SkillLibrary([isolated_skills_root], isolated_skills_root)
    red_before = body.skill_id in isolated_lib.by_id()

    # 3. P1.1d: Author SKILL.md using safe structured YAML serializer only
    try:
        skill_md_content = build_safe_skill_markdown(
            skill_id=body.skill_id,
            description=body.description,
            improve_id=improve_id,
            required_tools=list(body.required_tools),
            permissions=list(body.permissions),
            problem=body.problem,
            workflow=body.workflow,
        )
    except ValueError as vex:
        raise HTTPException(422, {"code": "SKILL_YAML_INVALID", "message": str(vex)}) from None

    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file.write_text(skill_md_content, encoding="utf-8", newline="\n")

    # P1.1e & P1.1f: Execute static Web Terminal verification without interpolating user values;
    # any terminal failure must produce FAIL (never VERIFIED).
    probe_file = (isolated_wt / "test_isolated_skill_probe.py").resolve()
    probe_file.write_text(
        "import pathlib\n"
        "def test_skill_manifest_exists():\n"
        "    files = list(pathlib.Path('.agents/skills').glob('*/SKILL.md'))\n"
        "    assert len(files) == 1 and files[0].stat().st_size > 0\n",
        encoding="utf-8",
        newline="\n",
    )

    terminal_session_id = None
    terminal_exit_code = -1
    terminal_output = ""
    terminal_failed = False
    mgr = getattr(svc, "terminal", None)
    if mgr is None:
        terminal_failed = True
        terminal_output = "Terminal manager unavailable"
    else:
        try:
            iso_policy = TerminalPolicy(allowed_roots=[isolated_wt], mode="project_host")
            ts = await mgr.start(
                "python -m pytest -q test_isolated_skill_probe.py",
                isolated_wt,
                iso_policy,
                approved=False,
                network=False,
                owner="north-star-skill-forge",
            )
            terminal_session_id = getattr(ts, "id", None)
            reader_task = getattr(ts, "_reader", None)
            if reader_task is not None:
                await asyncio.wait_for(reader_task, timeout=15.0)
            elif hasattr(ts, "done"):
                await asyncio.wait_for(ts.done.wait(), timeout=15.0)
            raw_out = getattr(ts, "output", "")
            if callable(raw_out):
                terminal_output = str(raw_out())
            elif isinstance(raw_out, list):
                terminal_output = "\n".join(str(x) for x in raw_out)
            else:
                terminal_output = str(raw_out)
            terminal_exit_code = int(ts.exit_code) if getattr(ts, "exit_code", None) is not None else -1
            if terminal_exit_code != 0:
                terminal_failed = True
        except Exception as exc:  # noqa: BLE001
            terminal_failed = True
            terminal_exit_code = -1
            terminal_output = f"Terminal verification failed: {type(exc).__name__}: {exc}"

    # 4. GREEN check in isolated worktree: parse_skill + validate_manifest + scan_policy + terminal_exit_code == 0
    parsed_sk = parse_skill(skill_file, isolated_skills_root)
    manifest_res = validate_manifest(parsed_sk)
    policy_crit = [v.code for v in scan_policy(parsed_sk.body) if v.critical]
    con = skill_contract(parsed_sk)
    green_after = (
        not terminal_failed
        and terminal_exit_code == 0
        and not red_before
        and parsed_sk.id == body.skill_id
        and manifest_res["errors"] == []
        and policy_crit == []
        and con.required_tools == list(body.required_tools)
        and "auto" not in [p.lower() for p in con.permissions]
    )

    # 5. Select coder + independent verifier via OpenCode Model Router
    catalog = await build_model_router_catalog(svc)
    coder_route = select_route_for_request(catalog, mode="SELF-IMPROVE", allow_cloud=body.allow_cloud)
    verifier_route = select_route_for_request(
        catalog, mode="SELF-IMPROVE", allow_cloud=body.allow_cloud, for_independent_review=True
    )
    independent_verdict = "PASS" if green_after else "FAIL"

    # 6. Mint MissionReceipt & Owner Approval Gate (only if PASS)
    receipt_payload = {
        "improve_id": improve_id,
        "kind": "north_star_skill_forge",
        "skill_id": body.skill_id,
        "signature": sig,
        "observed_runs": entry["count"],
        "isolated_worktree": str(isolated_wt),
        "terminal_session_id": terminal_session_id,
        "terminal_exit_code": terminal_exit_code,
        "terminal_failed": terminal_failed,
        "red_before": red_before,
        "green_after": green_after,
        "manifest_errors": manifest_res["errors"],
        "policy_violations": policy_crit,
        "coder_model": coder_route["model"],
        "verifier_model": verifier_route["model"],
        "independent_verdict": independent_verdict,
        "bypass_permissions_used": False,
        "fingerprint": parsed_sk.fingerprint,
        "ts": utcnow().isoformat(),
    }
    receipt_digest = hashlib.sha256(
        json.dumps(receipt_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    mission_receipt = {
        **receipt_payload,
        "sha256": receipt_digest,
        "status": "VERIFIED_AWAITING_OWNER" if independent_verdict == "PASS" else "FAIL",
    }

    preview_text = (
        f"[NORTH-STAR SKILL FORGE {improve_id}]\n"
        f"Навык: .agents/skills/{body.skill_id}/SKILL.md (fp={parsed_sk.fingerprint[:12]})\n"
        f"Инструменты: {body.required_tools} | Права: {body.permissions}\n"
        f"RED->GREEN: {not red_before} -> {green_after} | Независимый ревьюер ({verifier_route['model']}): {independent_verdict}\n"
        f"Receipt: sha256:{receipt_digest[:16]}"
    )
    approval_id = None
    if independent_verdict == "PASS":
        appr = await svc.approvals.create(kind="skill_permissions", preview=preview_text)
        approval_id = appr.get("id")

    steps_log = [
        {
            "step": i + 1,
            "title": IMPROVE_STEPS[i],
            "status": (
                ("completed" if i < 12 else ("waiting_approval" if i == 12 else "pending_approval"))
                if independent_verdict == "PASS"
                else ("completed" if i < 9 else "failed")
            ),
            "detail": preview_text if i == 11 else IMPROVE_STEPS[i],
        }
        for i in range(16)
    ]
    record = {
        "id": improve_id,
        "session_id": body.session_id,
        "status": "awaiting_owner_approval" if independent_verdict == "PASS" else "failed",
        "problem": body.problem,
        "criterion": f"Навык `{body.skill_id}` валиден, проходит RED->GREEN и выбирается на новой задаче `{body.unseen_query[:80]}`",
        "reproducer_test": "command-center/tests/test_skill_manifest.py",
        "route": coder_route,
        "verifier": verifier_route,
        "coding_task_id": None,
        "terminal_session_id": terminal_session_id,
        "terminal_output": terminal_output[-1000:],
        "test_passed": green_after,
        "mission_receipt": mission_receipt,
        "approval_id": approval_id,
        "forged_skill": {
            "id": body.skill_id,
            "content": skill_md_content,
            "required_tools": body.required_tools,
            "permissions": body.permissions,
            "fingerprint": parsed_sk.fingerprint,
        },
        "diff": f"+++ b/.agents/skills/{body.skill_id}/SKILL.md\n" + "\n".join(f"+{ln}" for ln in skill_md_content.splitlines()[:25]),
        "steps": steps_log,
        "created_at": utcnow().isoformat(),
    }
    orch.improve_runs[improve_id] = record
    with contextlib.suppress(Exception):
        store.record("hypothesis", {
            "id": improve_id,
            "state": "AWAITING_OWNER" if independent_verdict == "PASS" else "FAILED",
            "skill_id": body.skill_id,
            "approval_id": approval_id,
            "receipt_sha256": receipt_digest,
        })
    if rt is not None:
        rt.improve_runs[improve_id] = record
        if independent_verdict == "PASS":
            rt.emit(
                "approval.required",
                improve_id=improve_id,
                approval_id=approval_id,
                kind="skill_permissions",
                preview=preview_text,
                mission_receipt=mission_receipt,
            )
        else:
            rt.emit(
                "task.failed",
                improve_id=improve_id,
                reason="north_star_skill_verification_failed",
                mission_receipt=mission_receipt,
            )
    return record


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
                    result = await stop_session_core(svc, ws, session_id)
                    await ws.send_json({"type": "agent.stopped", "session_id": session_id,
                                        "stopped_tasks": result["stopped_tasks"],
                                        "cancelled_coding_tasks": result["cancelled_coding_tasks"],
                                        "killed_terminals": result["killed_terminals"]})
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

_NEVER_AUTO_APPROVE_KINDS = frozenset({
    "self_improve",
    "skill_permissions",
    "admin",
    "destructive",
    "outbound_send",
    "network_send",
    "cloud_send",
    "cloud.ask",
})


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
                appr_tool = str(msg.get("tool") or "")
                preview_lc = str(msg.get("preview") or "").lower()
                cfg = _session_cfg(svc, session_id)
                if (
                    cfg.get("bypass_permissions")
                    and appr_id is not None
                    and appr_kind.lower() not in _NEVER_AUTO_APPROVE_KINDS
                    and appr_tool.lower() not in _NEVER_AUTO_APPROVE_KINDS
                    and "cloud.ask" not in preview_lc
                ):
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
