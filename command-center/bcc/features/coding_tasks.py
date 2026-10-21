"""Coding tasks — the owner-visible path to the OpenHands runtime.

Owner audit 2026-09-08, F4: the Coding page managed worktrees (create / diff /
merge / discard) but had no way to hand a coding task to the agent — "0
responses" was an exact description of the UI. The engine existed
(bossman.apprentice.openhands_client, exercised end to end by bossman-core's
tests) and the owner could not reach it.

This feature is an API/UI wrapper around that ONE runtime; no second coding
agent is built here:

    Coding → New coding task → workspace (a repository inside the allowed
    roots) → instruction + allowed/protected paths → OpenHands status →
    diff / changed files / evidence / sandbox cleanup state.

What it deliberately does NOT do: push, merge, deploy, commit, widen the
sidecar's permissions, or mark anything "complete" on the sidecar's word. The
sidecar runs in a disposable clone with no remote (IsolatedWorktree); its
result is admitted only through OpenHandsClient's independent evidence
derivation; the patch is returned as EVIDENCE for the owner.

Bringing a verified candidate into the canonical project is a separate, explicit
owner decision (owner run 2026-09-23, P1 — раньше «отдельное решение» не имело
никакого управляемого пути, патч переносили руками): ``POST
/api/coding-tasks/{id}/apply`` — see ``apply_task`` below for the state machine.
The worktree sessions' merge (coding_session) is NOT reused for this: it commits
and moves the owner's branch, while here the candidate is a reviewed patch and
the owner keeps the commit decision — the result is left as working-tree
modifications.

Readiness is reported honestly: without the bossman-core runtime importable
or without `BOSSMAN_OPENHANDS_COMMAND` configured, the page says so and why,
instead of a button that does nothing. A configured command is not readiness
either (lab checkpoint 2026-09-22): the sidecar must answer a
``bossman.openhands.v1`` handshake — protocol, tools, a reachable model and one
real tool call — or the page shows the handshake's reason.

2026-09-23 additions, all on this one path:
  * a saved agent (``agents`` row) becomes the sidecar's profile — system prompt,
    allowed tools, step cap, "tests before finish";
  * memory: the engine's own recall (lifecycle_wiring.recall_for_task) plus
    VERIFIED executable recipes (features.coding_recipes, when installed) go to
    the sidecar as quoted context; they widen nothing;
  * ``verify_tests``: after the sidecar, BOSSMAN runs the named tests in the
    sandbox itself with the guarded runner — the task is completed only if they
    pass; the sidecar's own "tests passed" is not the verdict;
  * a task still "running" from a previous server process is shown as
    ``failed`` with outcome UNKNOWN_INTERRUPTED — never as still running and
    never silently re-run.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import Feature
from .tools_code import _within, allowed_roots

router = APIRouter()

RUNTIME_MODULE = "bossman.apprentice.openhands_client"
WORKTREE_MODULE = "bossman.apprentice.isolated_worktree"
COMMAND_ENV = "BOSSMAN_OPENHANDS_COMMAND"
_OLLAMA_TAGS = "http://127.0.0.1:11434/api/tags"
_OLLAMA_CHAT = "http://127.0.0.1:11434/api/chat"
_LOCAL_CODING_MODELS = (
    "bossman-fast-qwen36-35b-a3b-q5:latest",
    "qwen3-coder:30b",
    "r1-70b-abliterated:latest",
    "qwen38-27b-uncensored:latest",
    "gemma4-31b-uncensored:latest",
)
#: Local coder & sparse MoE sub-agent profiles (VRAM footprint vs 36 GB main / 42 GB 70B uncensored).
LOCAL_CODER_PROFILES: dict[str, dict[str, Any]] = {
    "qwen3-coder:30b": {
        "role": "sparse_helper_subagent",
        "architecture": "qwen3moe",
        "total_params_b": 30.5,
        "active_params_b": 3.3,
        "active_weights_pct": 10.8,
        "vram_gb": 18.0,
        "main_vram_gb": 36.0,
        "vram_ratio_of_main": 0.50,
        "vram_ratio_of_70b": round(18.0 / 42.0, 4),
        "within_50pct_vram_budget": True,
        "uncensored": False,
        "description": "MoE 30.5B coder activating only 3.3B expert weights per token (18 GB VRAM = 50% of 36 GB main, 42.9% of 70B).",
    },
    "bossman-fast-qwen36-35b-a3b-q5:latest": {
        "role": "primary_moe_coder",
        "architecture": "qwen35moe",
        "total_params_b": 35.0,
        "active_params_b": 3.0,
        "active_weights_pct": 8.6,
        "vram_gb": 26.0,
        "main_vram_gb": 36.0,
        "vram_ratio_of_main": round(26.0 / 36.0, 4),
        "vram_ratio_of_70b": round(26.0 / 42.0, 4),
        "within_50pct_vram_budget": False,
        "uncensored": False,
        "description": "Fast MoE 35B-A3B Q5 primary local coding model (3B active weights per token).",
    },
    "r1-70b-abliterated:latest": {
        "role": "heavy_uncensored_70b_coder",
        "architecture": "llama-70b-dense",
        "total_params_b": 70.6,
        "active_params_b": 70.6,
        "active_weights_pct": 100.0,
        "vram_gb": 42.5,
        "main_vram_gb": 36.0,
        "vram_ratio_of_main": round(42.5 / 36.0, 4),
        "vram_ratio_of_70b": 1.0,
        "within_50pct_vram_budget": False,
        "primary_coder": True,
        "min_free_ram_gb_during_gen": 50.0,
        "hardware": "AMD Ryzen AI Max+ 395 / Radeon 8060S (128 GB unified memory)",
        "uncensored": True,
        "description": "70B abliterated/uncensored deep reasoning and coding model (42.5 GB in 128 GB unified memory on Radeon 8060S; requires >= 50 GB free RAM during generation).",
    },
    "qwen38-27b-uncensored:latest": {
        "role": "uncensored_coder",
        "architecture": "qwen35-dense",
        "total_params_b": 27.0,
        "active_params_b": 27.0,
        "active_weights_pct": 100.0,
        "vram_gb": 29.0,
        "main_vram_gb": 36.0,
        "vram_ratio_of_main": round(29.0 / 36.0, 4),
        "vram_ratio_of_70b": round(29.0 / 42.0, 4),
        "within_50pct_vram_budget": False,
        "uncensored": True,
        "description": "27B uncensored local coding and reasoning model.",
    },
    "qwen38-pi-ab:latest": {
        "role": "compact_uncensored_helper",
        "architecture": "qwen35-abliterated",
        "total_params_b": 27.0,
        "active_params_b": 27.0,
        "active_weights_pct": 100.0,
        "vram_gb": 17.0,
        "main_vram_gb": 36.0,
        "vram_ratio_of_main": round(17.0 / 36.0, 4),
        "vram_ratio_of_70b": round(17.0 / 42.0, 4),
        "within_50pct_vram_budget": True,
        "uncensored": True,
        "description": "17 GB abliterated helper (47.2% VRAM of 36 GB main).",
    },
    "gemma4-31b-uncensored:latest": {
        "role": "gemma4_uncensored_coder",
        "architecture": "gemma4-dense",
        "total_params_b": 30.7,
        "active_params_b": 30.7,
        "active_weights_pct": 100.0,
        "vram_gb": 20.0,
        "main_vram_gb": 36.0,
        "vram_ratio_of_main": round(20.0 / 36.0, 4),
        "vram_ratio_of_70b": round(20.0 / 42.0, 4),
        "within_50pct_vram_budget": False,
        "uncensored": True,
        "description": "Gemma 4 31B Q4_K_M uncensored local coding, terminal, and tool-use model.",
    },
}
SPARSE_HELPER_DEFAULT = "qwen3-coder:30b"
TERMINAL = ("completed", "failed", "blocked")
_ID_RE = re.compile(r"^[a-z0-9]{12}$")
#: Identifies THIS server process; a running record from another boot is orphaned.
BOOT_ID = secrets.token_hex(8)
HANDSHAKE_TTL_S = 60.0
HANDSHAKE_TIMEOUT_S = 150
_handshake_cache: dict[str, tuple[float, dict]] = {}
#: task id -> running sidecar process tree (this process only) / cancel requests.
_ACTIVE: dict[str, Any] = {}
_CANCELLED: set[str] = set()


class TaskIn(BaseModel):
    instruction: str = Field(min_length=1, max_length=8000)
    source_repo: str = Field(default="self", min_length=1, max_length=1000)
    allowed_paths: list[str] = Field(default_factory=list, max_length=64)
    protected_paths: list[str] = Field(default_factory=list, max_length=64)
    model: str | None = Field(default=None, max_length=200)
    timeout_seconds: int = Field(default=900, ge=30, le=7200)
    agent_id: int | None = Field(default=None, ge=1)
    project_id: str | None = Field(default=None, max_length=120)
    use_memory: bool = True
    verify_tests: list[str] = Field(default_factory=list, max_length=32)
    #: Optional cloud worker from the fixed WORKERS allowlist; None = the configured local sidecar.
    worker: str | None = Field(default=None, max_length=40)
    #: Optional fallback cloud workers tried in order if the primary worker hits upstream model_error before any edit.
    fallback_workers: list[str] = Field(default_factory=list, max_length=8)
    #: When True (or when source_repo is "self"/"bossman"), targets Bossman's own repository checkout.
    self_target: bool = False


#: Cloud workers the owner can pick per task in the OpenCode Web Pult. Fixed allowlist:
#: endpoint, model and the credential name never come from the request.
#: Includes both curated FREE cloud coding models ($0) and PAID top cloud coding models.
WORKERS: dict[str, dict[str, str]] = {
    "openrouter-free": {
        "endpoint": "https://openrouter.ai/api/v1",
        "model": "nvidia/nemotron-3-super-120b-a12b:free",
        "key": "OPENROUTER_API_KEY",
        "label": "OpenRouter · Nemotron 3 Super 120B (free)",
        "tier": "free",
        "provider": "OpenRouter",
        "price_display": "$0 / 1M (free)",
        "context_window": "128K",
    },
    "openrouter-code-free": {
        "endpoint": "https://openrouter.ai/api/v1",
        "model": "cohere/north-mini-code:free",
        "key": "OPENROUTER_API_KEY",
        "label": "OpenRouter · Cohere North Mini Code (free)",
        "tier": "free",
        "provider": "OpenRouter",
        "price_display": "$0 / 1M (free)",
        "context_window": "128K",
    },
    "poolside-laguna-free": {
        "endpoint": "https://openrouter.ai/api/v1",
        "model": "poolside/laguna-s-2.1:free",
        "key": "OPENROUTER_API_KEY",
        "label": "OpenRouter · Poolside Laguna S 2.1 Code (free)",
        "tier": "free",
        "provider": "OpenRouter",
        "price_display": "$0 / 1M (free)",
        "context_window": "128K",
    },
    "minimax-m31-free": {
        "endpoint": "https://openrouter.ai/api/v1",
        "model": "minimax/minimax-m3.1-flash:free",
        "key": "OPENROUTER_API_KEY",
        "label": "OpenRouter · MiniMax M3.1 Flash Code (free)",
        "tier": "free",
        "provider": "OpenRouter",
        "price_display": "$0 / 1M (free)",
        "context_window": "200K",
    },
    "nemotron-35-free": {
        "endpoint": "https://openrouter.ai/api/v1",
        "model": "nvidia/nemotron-3.5-lightning:free",
        "key": "OPENROUTER_API_KEY",
        "label": "OpenRouter · Nemotron 3.5 Lightning (free)",
        "tier": "free",
        "provider": "OpenRouter",
        "price_display": "$0 / 1M (free)",
        "context_window": "128K",
    },
    "groq-qwen38-free": {
        "endpoint": "https://api.groq.com/openai/v1",
        "model": "qwen/qwen3.8-27b",
        "key": "GROQ_API_KEY",
        "label": "Groq Cloud · Qwen 3.8 27B Coder (free tier)",
        "tier": "free",
        "provider": "Groq",
        "price_display": "$0 / 1M (free tier)",
        "context_window": "128K",
    },
    "groq-gpt-oss-120b-free": {
        "endpoint": "https://api.groq.com/openai/v1",
        "model": "openai/gpt-oss-120b",
        "key": "GROQ_API_KEY",
        "label": "Groq Cloud · GPT-OSS 120B (free tier)",
        "tier": "free",
        "provider": "Groq",
        "price_display": "$0 / 1M (free tier)",
        "context_window": "128K",
    },
    "gemini-3-flash-free": {
        "endpoint": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-3-flash-preview",
        "key": "GEMINI_API_KEY",
        "label": "Google AI Studio · Gemini 3 Flash (free tier)",
        "tier": "free",
        "provider": "Google AI Studio",
        "price_display": "$0 / 1M (free tier)",
        "context_window": "1M",
    },
    "nvidia-nim": {
        "endpoint": "https://integrate.api.nvidia.com/v1",
        "model": "nvidia/nemotron-3-super-120b-a12b",
        "key": "NVIDIA_API_KEY",
        "label": "NVIDIA NIM · Nemotron 3 Super 120B",
        "tier": "free",
        "provider": "NVIDIA NIM",
        "price_display": "$0 / 1M (free credits)",
        "context_window": "128K",
    },
    "nemotron-ultra-free": {
        "endpoint": "https://openrouter.ai/api/v1",
        "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
        "key": "OPENROUTER_API_KEY",
        "label": "OpenRouter · Nemotron 3 Ultra 550B (free)",
        "tier": "free",
        "provider": "OpenRouter",
        "price_display": "$0 / 1M (free)",
        "context_window": "128K",
    },
    "deepseek-v4.1-flash": {
        "endpoint": "https://openrouter.ai/api/v1",
        "model": "deepseek/deepseek-v4.1-flash",
        "key": "OPENROUTER_API_KEY",
        "label": "OpenRouter · DeepSeek V4.1 Flash (paid, fast)",
        "tier": "paid",
        "provider": "OpenRouter",
        "price_display": "$0.14 / $0.28 per 1M",
        "context_window": "128K",
    },
    "glm-flash": {
        "endpoint": "https://openrouter.ai/api/v1",
        "model": "z-ai/glm-5.3-flash",
        "key": "OPENROUTER_API_KEY",
        "label": "OpenRouter · GLM 5.3 Flash (paid, ~$0.00004/call)",
        "tier": "paid",
        "provider": "OpenRouter",
        "price_display": "~$0.00004 / call",
        "context_window": "128K",
    },
    # owner 07.10: Haiku 5.5 via OpenRouter ($0.10/$0.50 per M, tools, 1M ctx) for self-repair step 3 and audits
    "haiku-5.5": {
        "endpoint": "https://openrouter.ai/api/v1",
        "model": "anthropic/claude-haiku-5.5",
        "key": "OPENROUTER_API_KEY",
        "label": "OpenRouter · Claude Haiku 5.5 (paid, cheap)",
        "tier": "paid",
        "provider": "OpenRouter",
        "price_display": "$0.10 / $0.50 per 1M",
        "context_window": "200K",
    },
    "qwen3-coder-paid": {
        "endpoint": "https://openrouter.ai/api/v1",
        "model": "qwen/qwen3-coder",
        "key": "OPENROUTER_API_KEY",
        "label": "OpenRouter · Qwen3 Coder 480B (paid SLA)",
        "tier": "paid",
        "provider": "OpenRouter",
        "price_display": "$0.20 / $0.60 per 1M",
        "context_window": "128K",
    },
    "claude-sonnet-4": {
        "endpoint": "https://openrouter.ai/api/v1",
        "model": "anthropic/claude-sonnet-4",
        "key": "OPENROUTER_API_KEY",
        "label": "OpenRouter · Claude Sonnet 4 (paid, architect)",
        "tier": "paid",
        "provider": "OpenRouter",
        "price_display": "$3.00 / $15.00 per 1M",
        "context_window": "200K",
    },
}
ALLOWED_WORKER_KEY_ENVS = frozenset({"OPENROUTER_API_KEY", "NVIDIA_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY"})
#: The owner's local provider key file (user ACL); read only when the vault/env has no key.
OWNER_KEYS_FILE = Path(os.environ.get("LOCALAPPDATA", "")) / "Bossman" / "keys" / "provider-keys.env"
_WORKER_KEY_ENV = "BOSSMAN_WORKER_API_KEY"


async def _worker_key_with_source(name: str, svc) -> tuple[str | None, str]:
    """Return (key_value, source_label) where source_label is 'owner_keys_file', 'vault_or_env', or 'missing'."""
    try:
        for line in OWNER_KEYS_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith(name + "="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value, "owner_keys_file"
    except OSError:
        pass
    try:
        from .plugins import resolve_cred
        val = await resolve_cred(name, svc) or None
        if val:
            return val, "vault_or_env"
    except Exception:  # noqa: BLE001 — no key anywhere: the task fails visibly
        pass
    return None, "missing"


async def _worker_key(name: str, svc) -> str | None:
    """The owner's provider key file first (where the owner put the worker keys), then vault/env.

    Live 2026-10-05: with vault/env first every cloud worker task failed on its first call
    (HTTPError) while the same sidecar with the key-file key fixed a planted bug end to end.
    """
    val, _ = await _worker_key_with_source(name, svc)
    return val


def _missing_key_hint(name: str) -> str:
    """What the owner does about a missing worker key (the generic 5xx hint pointed to server logs)."""
    where = (f"подключите OpenRouter с ключом на странице «OpenRouter» или в панели OpenCode (или строкой {name}=… в {OWNER_KEYS_FILE})"
             if name == "OPENROUTER_API_KEY" else f"подключите ключ {name} в панели OpenCode или добавьте строку {name}=… в {OWNER_KEYS_FILE}")
    return f"Ключ не подключён: {where}. Без ключа выберите исполнителя «Локальная модель Bossman»."


def _worker_command(worker: str) -> list[str]:
    spec = WORKERS[worker]
    return [sys.executable, "-I", "-m", "bossman.apprentice.local_sidecar", "--endpoint", spec["endpoint"],
            "--model", spec["model"], "--api-key-env", _WORKER_KEY_ENV]


def _runtime() -> tuple[Any, Any, str]:
    """(openhands_client module, isolated_worktree module, reason-if-missing)."""
    try:
        oc = importlib.import_module(RUNTIME_MODULE)
        wt = importlib.import_module(WORKTREE_MODULE)
    except Exception as exc:  # noqa: BLE001 — the reason is shown to the owner
        return None, None, (f"рантайм OpenHands (bossman-core) не установлен рядом с Command Center: "
                            f"{type(exc).__name__}: {exc}")
    return oc, wt, ""


def _local_sidecar_command() -> str:
    """Find a known local coding model without widening the configured route.

    Only loopback Ollama is queried. Model names from its response are never
    interpolated into a command; the command uses our fixed allowlist instead.
    An unavailable model leaves the coding path visibly unavailable.
    """
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(_OLLAMA_TAGS, timeout=1.0) as response:
            if response.status != 200:
                return ""
            raw = response.read(131_073)
        if len(raw) > 131_072:
            return ""
        payload = json.loads(raw)
        names = {item.get("name") for item in payload.get("models", [])
                 if isinstance(item, dict) and isinstance(item.get("name"), str)}
    except (OSError, ValueError, TypeError, AttributeError, urllib.error.URLError):
        return ""
    model = next((name for name in _LOCAL_CODING_MODELS if name in names), "")
    if not model:
        return ""
    argv = [sys.executable, "-I", "-m", "bossman.apprentice.local_sidecar",
            "--endpoint", "http://127.0.0.1:11434/v1", "--model", model]
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)


def _sidecar_command() -> str:
    # An explicit value, including an empty disable, always wins. Once found,
    # pin the local model for this backend process so readiness and tasks agree.
    if COMMAND_ENV in os.environ:
        return os.environ[COMMAND_ENV].strip()
    auto = _local_sidecar_command()
    return os.environ.setdefault(COMMAND_ENV, auto).strip() if auto else ""


def _handshake(command: str | list[str], env: dict[str, str] | None = None) -> dict:
    """Blocking: one handshake with the configured sidecar (cached briefly)."""
    cache_key = subprocess.list2cmdline(command) if isinstance(command, list) else str(command)
    now = time.monotonic()
    hit = _handshake_cache.get(cache_key)
    if hit and now - hit[0] < HANDSHAKE_TTL_S:
        return hit[1]
    oc, _wt, reason = _runtime()
    if oc is None:
        return {"ok": False, "reason": reason}
    try:
        client_kw: dict[str, Any] = {"env": env or None}
        if isinstance(command, list):
            client_kw["command"] = command
        resp = oc.OpenHandsClient(**client_kw).handshake(timeout_seconds=HANDSHAKE_TIMEOUT_S)
        out = {"ok": True, "executor": resp.get("executor"), "model": resp.get("model"),
               "tools": resp.get("tools"), "tool_call_ok": resp.get("tool_call_ok"),
               "test_runners": resp.get("test_runners"), "isolation": resp.get("isolation"),
               "deterministic_test_model": bool(resp.get("deterministic_test_model")),
               "model_kind": resp.get("model_kind") or ("MOCK_MODEL" if resp.get("deterministic_test_model") else ""),
               "endpoint": resp.get("endpoint")}
    except Exception as exc:  # noqa: BLE001 — shown to the owner
        out = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"[:400]}
    _handshake_cache[cache_key] = (now, out)
    return out


async def _sidecar_env(svc) -> dict[str, str]:
    """Explicit provider input for the sidecar (never inherited wholesale).

    The OpenHands security contract keeps the Bossman process environment out
    of the sidecar; only the resolved OpenRouter credential and the configured
    executor model are forwarded, and the sidecar itself pops the key before
    the agent's terminal tool starts.
    """
    env: dict[str, str] = {}
    try:
        from .plugins import resolve_cred
        key = await resolve_cred("OPENROUTER_API_KEY", svc)
    except Exception:  # noqa: BLE001 — a credential outage must not crash readiness
        key = None
    if key:
        env["OPENROUTER_API_KEY"] = key
    model = os.environ.get("BOSSMAN_OPENHANDS_MODEL", "").strip()
    if model:
        env["BOSSMAN_OPENHANDS_MODEL"] = model
    # The owner's test interpreter (a path, not a secret): without it the worker's own run_tests fell back to
    # unittest on pytest-style tests in the installed bundle (owner PC 06.10, task cecbba1c42a9).
    env.update(_verify_python_env())
    return env


def _verify_python_env() -> dict[str, str]:
    """The owner's test interpreter, if it exists. Shared by the local and the cloud-worker sidecar start."""
    verify_python = os.environ.get("BOSSMAN_VERIFY_PYTHON", "").strip()
    if verify_python and Path(verify_python).is_file():
        return {"BOSSMAN_VERIFY_PYTHON": verify_python}
    return {}


async def readiness(svc, worker: str | None = None, model: str | None = None) -> dict[str, Any]:
    """Can a coding task start now?

    Local sidecar (worker=None): the full ``bossman.openhands.v1`` handshake with the configured
    local command. A cloud worker never uses that command, so its readiness must not run it:
    on the owner PC (06.10) every glm-flash task still handshook the LOCAL sidecar first, which
    loaded a 27 GB Ollama model into the GPU for a task that never used it. A cloud worker is
    ready when the runtime is installed and its key exists; the sidecar's own start is the check.
    """
    oc, wt, reason = _runtime()
    roots = [str(r) for r in await allowed_roots(svc)]
    if worker is not None:
        out = {"available": False, "runtime": oc is not None, "sidecar_command": True, "worker": worker,
               "roots": roots, "reason": "", "handshake": None}
        if oc is None:
            out["reason"] = reason
        elif worker not in WORKERS:
            out["reason"] = f"неизвестный исполнитель: {worker}"
        elif not await _worker_key(WORKERS[worker]["key"], svc):
            out["reason"] = f"нет ключа {WORKERS[worker]['key']} для исполнителя {worker}"
            out["hint"] = _missing_key_hint(WORKERS[worker]["key"])
        else:
            out["available"] = True
        return out
    explicit_local: list[str] | None = None
    if (
        oc is not None
        and model
        and model in _LOCAL_CODING_MODELS
        and "DETERMINISTIC-TEST-MODEL" not in os.environ.get(COMMAND_ENV, "")
    ):
        explicit_local = [
            sys.executable, "-I", "-m", "bossman.apprentice.local_sidecar",
            "--endpoint", "http://127.0.0.1:11434/v1", "--model", model,
        ]
    command: str | list[str] = explicit_local or (_sidecar_command() if oc is not None else os.environ.get(COMMAND_ENV, "").strip())
    out = {"available": False, "runtime": oc is not None, "sidecar_command": bool(command),
           "roots": roots, "reason": "", "handshake": None}
    if oc is None:
        out["reason"] = reason
    elif not command:
        out["reason"] = (f"команда сайдкара не настроена: задайте {COMMAND_ENV} "
                         "(путь к OpenHands-сайдкару) и перезапустите Bossman")
    else:
        env = await _sidecar_env(svc)
        hs = await asyncio.to_thread(_handshake, command, env)
        out["handshake"] = hs
        if hs.get("ok"):
            out["available"] = True
        else:
            out["reason"] = f"сайдкар не прошёл проверку готовности: {hs.get('reason')}"
    return out


def _store(svc) -> Path:
    root = Path(svc.settings.data_dir) / "coding-tasks"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _path(svc, task_id: str) -> Path:
    if not _ID_RE.match(task_id or ""):
        raise HTTPException(404, {"message": "задача не найдена"})
    return _store(svc) / f"{task_id}.json"


def _write(svc, record: dict) -> None:
    path = _path(svc, record["id"])
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _settle_orphan(svc, record: dict) -> dict:
    """A record left "running" by an earlier server process has no worker any
    more. Its outcome is unknown: say so, and never re-run it silently."""
    if record.get("status") == "running" and record.get("boot_id") != BOOT_ID:
        record = {**record, "status": "failed", "outcome": "UNKNOWN_INTERRUPTED",
                  "error": "задача прервана перезапуском Bossman; исход неизвестен, повторный запуск — решение владельца",
                  "finished_at": time.time()}
        try:
            _write(svc, record)
        except OSError:
            pass
    return record


def _read(svc, task_id: str) -> dict:
    path = _path(svc, task_id)
    if not path.exists():
        raise HTTPException(404, {"message": "задача не найдена"})
    return _settle_orphan(svc, json.loads(path.read_text(encoding="utf-8")))


def _list(svc) -> list[dict]:
    items = []
    for p in _store(svc).glob("*.json"):
        try:
            items.append(_settle_orphan(svc, json.loads(p.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    items.sort(key=lambda r: r.get("created_at") or 0, reverse=True)
    return items


MODEL_TIER_PRIORITY = ("local", "free", "cheap_paid", "premium")
DEFAULT_TASK_SPEND_CAP_USD = 0.50
DEFAULT_DAILY_SPEND_CAP_USD = 2.00
_PAID_STEP_USD: dict[str, float] = {
    "glm-flash": 0.00004,
    "deepseek-v4.1-flash": 0.00045,
    "haiku-5.5": 0.00060,
    "qwen3-coder-paid": 0.00080,
    "claude-sonnet-4": 0.01200,
}


def estimate_coding_task_cost(record: dict) -> dict[str, Any]:
    """Compute deterministic USD cost, tier priority, and spend cap compliance for a coding task."""
    worker = str(record.get("worker") or "").strip()
    sidecar = record.get("sidecar") if isinstance(record.get("sidecar"), dict) else {}
    steps = max(1, int(sidecar.get("steps") or 1)) if record.get("status") in {"completed", "failed"} else 0
    cap_usd = float(record.get("spend_cap_usd") or os.environ.get("BOSSMAN_CODING_SPEND_CAP_USD") or DEFAULT_TASK_SPEND_CAP_USD)
    if not worker:
        cost_usd = 0.0
        tier = "local"
        cost_display = "$0.0000 (LOCAL)"
    else:
        spec = WORKERS.get(worker) or {}
        raw_tier = spec.get("tier") or ("free" if ":free" in str(spec.get("model", "")) else "paid")
        if raw_tier == "free":
            cost_usd = 0.0
            tier = "free"
            cost_display = "$0.0000 (FREE)"
        else:
            tier = "premium" if worker == "claude-sonnet-4" else "cheap_paid"
            per_step = _PAID_STEP_USD.get(worker, 0.0005)
            cost_usd = round(steps * per_step, 6)
            cost_display = f"${cost_usd:.4f} ({tier.upper()})"
    return {
        "cost_usd": cost_usd,
        "cost_display": cost_display,
        "cost_tier": tier,
        "spend_cap_usd": cap_usd,
        "within_spend_cap": cost_usd <= cap_usd,
    }


def _public(record: dict) -> dict:
    """The list view: no patch bodies, so the page stays light."""
    out = dict(record)
    out.pop("diff", None)
    out["diff_bytes"] = len(record.get("diff") or "")
    for k, v in estimate_coding_task_cost(out).items():
        out.setdefault(k, v)
    return out


async def _confined_repo(svc, raw: str) -> Path:
    roots = await allowed_roots(svc)
    try:
        p = Path(str(raw)).expanduser().resolve(strict=True)
    except (OSError, ValueError):
        raise HTTPException(400, {"message": f"путь репозитория недоступен: {raw}"})
    if not any(_within(p, [r]) for r in roots):
        raise HTTPException(403, {"message": "репозиторий вне разрешённых корней",
                                  "hint": "добавьте корень в настройки code/terminal roots"})
    if not (p / ".git").exists():
        raise HTTPException(400, {"message": "это не git-репозиторий"})
    return p


SIDECAR_FIELDS = ("schema", "status", "summary", "tests", "notes", "steps", "stop_reason", "tool_calls",
                  "recipes_applied", "executor", "model", "deterministic_test_model", "model_kind", "profile",
                  "memory_used", "skills_used", "endpoint", "elapsed_seconds", "tool_calls_total",
                  "error_type", "stderr_tail")


def _verify_in_sandbox(root: Path, tests: list[str], timeout: int) -> dict:
    """Bossman's own check of the sidecar's work: the named tests run in the
    sandbox with the guarded runner (minimal env, private HOME/TEMP, process
    tree killed on timeout). The sidecar's claim about tests is not used."""
    try:
        from bossman.apprentice import local_sidecar as ls  # noqa: WPS433
    except Exception as exc:  # noqa: BLE001
        return {"ran": False, "passed": False, "error": f"проверяющий раннер недоступен: {type(exc).__name__}"}
    import tempfile  # noqa: WPS433
    ws = ls.Workspace(root, [], [])
    with tempfile.TemporaryDirectory(prefix="bossman-verify-") as scratch:
        try:
            res = ls.tool_run_tests(ws, {"paths": list(tests), "runner": "auto"}, scratch=Path(scratch),
                                    deadline=time.monotonic() + timeout, test_timeout=timeout)
        except ls.ToolError as exc:
            return {"ran": False, "passed": False, "error": str(exc)}
    return {"ran": True, **res}


class _Cancelled(Exception):
    """The owner cancelled the task (STOP)."""


def _stop_active(data_dir: str | os.PathLike | None = None, repo: Path | None = None) -> bool:
    candidates: list[Path] = []
    if data_dir:
        candidates.append(Path(data_dir))
    env_dir = os.environ.get("BCC_DATA_DIR", "").strip()
    if env_dir:
        candidates.append(Path(env_dir))
    if repo is not None:
        candidates.append(Path(repo))
        if Path(repo).parent.name == "qwen-operator":
            candidates.append(Path(repo).parent.parent)
    for base in candidates:
        try:
            if (base / "computer" / "STOP").exists():
                return True
        except OSError:
            continue
    return False


def _execute(record: dict, repo: Path, body: TaskIn, context: dict | None = None,
             env: dict[str, str] | None = None, command: list[str] | None = None,
             data_dir: str | os.PathLike | None = None) -> dict:
    """Blocking: runs in a worker thread. Returns the terminal record."""
    if _stop_active(data_dir or record.get("data_dir"), repo):
        out = {**record, "status": "failed", "outcome": "CANCELLED", "changed_files": [], "diff": "",
               "error": "Остановлено владельцем (STOP)", "duration_seconds": 0.0, "finished_at": time.time()}
        out["receipt"] = mission_receipt(out)
        return out
    oc, wt, reason = _runtime()
    if oc is None:
        return {**record, "status": "blocked", "error": reason}
    sandbox = wt.IsolatedWorktree(str(repo))
    started = time.time()
    try:
        root = sandbox.create()
    except Exception as exc:  # noqa: BLE001
        return {**record, "status": "failed",
                "error": f"песочница не создана: {type(exc).__name__}: {exc}"[:500]}
    result_fields: dict[str, Any] = {}
    task_id = record["id"]
    try:
        if task_id in _CANCELLED or _stop_active(data_dir or record.get("data_dir"), repo):
            raise _Cancelled()
        client = oc.OpenHandsClient(command=command or None,
                                    on_process=lambda tree: _ACTIVE.__setitem__(task_id, tree),
                                    env=env or None)
        extra = {"context": context} if context else {}
        request = oc.OpenHandsRequest(body.instruction, root, tuple(body.allowed_paths),
                                      tuple(body.protected_paths), model=body.model,
                                      timeout_seconds=int(body.timeout_seconds),
                                      metadata={"coding_task_id": record["id"]}, **extra)
        try:
            result = client.run(request)
        finally:
            _ACTIVE.pop(task_id, None)
        if task_id in _CANCELLED or _stop_active(data_dir or record.get("data_dir"), repo):
            raise _Cancelled()
        sidecar = {k: v for k, v in dict(result.sidecar).items() if k in SIDECAR_FIELDS}
        ok = result.status == "completed"
        result_fields = {"status": "completed" if ok else "failed",
                         "sidecar_status": result.status,
                         "changed_files": list(result.changed_files), "diff": result.diff,
                         "sidecar": sidecar,
                         "error": "" if ok else "сайдкар сообщил о неудаче"}
        if result.diff:
            # What a later owner-approved apply is bound to: the digest of the
            # exact reviewed diff and the host-read after-state of every file.
            result_fields.update(_candidate(result))
        if ok and body.verify_tests:
            verification = _verify_in_sandbox(Path(root), list(body.verify_tests),
                                              max(30, min(600, int(body.timeout_seconds))))
            result_fields["verification"] = verification
            # The test run takes up to 10 min: a cancel / STOP during it wins over its result.
            if task_id in _CANCELLED or _stop_active(data_dir or record.get("data_dir"), repo):
                raise _Cancelled()
            if not verification.get("passed"):
                result_fields["status"] = "failed"
                result_fields["error"] = "независимая проверка Bossman не прошла: " + (
                    verification.get("error") or f"exit={verification.get('exit_code')}")
    except _Cancelled:
        result_fields = {"status": "failed", "outcome": "CANCELLED", "changed_files": [], "diff": "",
                         "error": "задача отменена владельцем; процессы сайдкара остановлены"}
    except oc.OpenHandsError as exc:
        if task_id in _CANCELLED:     # the killed sidecar left no valid answer: that is the cancel
            result_fields = {"status": "failed", "outcome": "CANCELLED", "changed_files": [], "diff": "",
                             "error": "задача отменена владельцем; процессы сайдкара остановлены"}
        else:
            # The evidence boundary refused the result: out-of-scope/protected
            # change, tampering with HEAD/config/remotes/index, invalid contract.
            result_fields = {"status": "blocked", "error": str(exc)[:800], "changed_files": [], "diff": ""}
    except Exception as exc:  # noqa: BLE001
        result_fields = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"[:800],
                         "changed_files": [], "diff": ""}
    finally:
        _CANCELLED.discard(task_id)
        try:
            evidence = sandbox.derive_evidence()
        except Exception as exc:  # noqa: BLE001
            evidence = {"error": f"{type(exc).__name__}: {exc}"}
        sandbox.cleanup()
        cleanup = sandbox.cleanup_state()
    # The sandbox is a clone of the canonical branch tip: its HEAD before the
    # run is the base the candidate diff applies to (STALE_BASE otherwise).
    base = evidence.get("head_before") if isinstance(evidence, dict) else None
    out = {**record, **result_fields, "evidence": evidence, "sandbox_cleanup": cleanup,
           **({"base_commit": base} if base else {}),
           "duration_seconds": round(time.time() - started, 2), "finished_at": time.time()}
    out["receipt"] = mission_receipt(out)
    return out


def _real_zero_exit(code: Any) -> bool:
    return type(code) is int and code == 0


def mission_receipt(rec: dict) -> dict[str, Any]:
    """Unified mission workspace receipt (OpenHands contract): task_id, model,
    workspace_path, git_diff, stop_status, postcondition_verified, evidence_kind, and receipt_sha256."""
    evidence = rec.get("evidence") if isinstance(rec.get("evidence"), dict) else {}
    sidecar = rec.get("sidecar") if isinstance(rec.get("sidecar"), dict) else {}
    ver = rec.get("verification") if isinstance(rec.get("verification"), dict) else {}
    post_chk = rec.get("postcondition_check") if isinstance(rec.get("postcondition_check"), dict) else {}
    diff = str(rec.get("diff") or "")
    status = str(rec.get("status") or "unknown")
    outcome = str(rec.get("outcome") or "")
    stop_status = "CANCELLED" if outcome == "CANCELLED" else (
        "INTERRUPTED" if outcome == "UNKNOWN_INTERRUPTED" else status.upper()
    )
    verify_cmd = list(rec.get("verify_tests") or post_chk.get("cmd") or [])
    verify_exit_code = ver.get("exit_code") if ver else post_chk.get("exit_code")
    workspace_ok = (
        status == "completed"
        and bool(evidence.get("head_before"))
        and evidence.get("head_before") == evidence.get("head_after")
        and bool(rec.get("evidence_digest") or rec.get("candidate_files"))
    )
    # A real verify exit code: the int 0 of a process that ran (False == 0 and 0.0 == 0 in Python, "0" is
    # text). Bossman's own test run, when present, is THE verdict; another check cannot rescue a failed one.
    ver_ok = (bool(verify_cmd) and ver.get("ran") is not False and ver.get("passed") is True
              and _real_zero_exit(ver.get("exit_code")))
    post_ok = post_chk.get("passed") is True and _real_zero_exit(post_chk.get("exit_code"))
    verified = workspace_ok and (ver_ok if ver else post_ok)
    evidence_kind = str(rec.get("evidence_kind") or ("SYNTHETIC" if rec.get("synthetic") else "LIVE"))
    payload = {
        "schema": "bossman.mission_receipt.v1",
        "evidence_kind": evidence_kind,
        "task_id": str(rec.get("id") or ""),
        "model": str(rec.get("model") or sidecar.get("model") or "local_sidecar"),
        "workspace_path": str(rec.get("source_repo") or ""),
        "base_commit": rec.get("base_commit") or evidence.get("head_before"),
        "changed_files": list(rec.get("changed_files") or []),
        "git_diff": diff,
        "diff_digest": rec.get("evidence_digest") or (_diff_digest(diff) if diff else None),
        "verify_cmd": verify_cmd,
        "verify_exit_code": verify_exit_code,
        "stop_status": stop_status,
        "workspace_integrity_verified": workspace_ok,
        "postcondition_verified": verified,
        "sandbox_removed": bool((rec.get("sandbox_cleanup") or {}).get("removed")),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = _sha(canonical)
    return {
        **payload,
        "receipt_id": f"rcpt-{payload['task_id']}-{digest[:12]}",
        "receipt_sha256": digest,
        "sha256": digest,
    }


async def _agent_profile(svc, agent_id: int | None) -> dict | None:
    """A saved agent (the product's ``agents`` row) as the sidecar profile."""
    if agent_id is None:
        return None
    import sqlalchemy as sa  # noqa: WPS433
    from ..db import agents as agents_t  # noqa: WPS433
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(agents_t).where(agents_t.c.id == int(agent_id)))).mappings().first()
    if row is None:
        raise HTTPException(404, {"message": f"агент {agent_id} не найден"})
    if row.get("enabled") is False:
        raise HTTPException(409, {"message": f"агент {row['name']} выключен"})
    perms = row.get("permissions") if isinstance(row.get("permissions"), dict) else {}
    # A lab observer (auditor / result verifier / UX observer) is not a student: its
    # read-only tool list would otherwise reach the sidecar as "no tools" and fall
    # back to the FULL sidecar set, edit tools included.
    if row.get("role") == "lab:observer" or perms.get("lab_observer"):
        raise HTTPException(409, {"code": "LAB_OBSERVER_NOT_A_STUDENT",
                                  "message": f"агент {row['name']} — наблюдатель лаборатории; "
                                             "coding-задачи он не выполняет и инструментов правки не получает"})
    tools = row.get("tools") if isinstance(row.get("tools"), list) else []
    return {"agent_id": int(row["id"]), "name": row["name"], "system_prompt": row.get("system_prompt") or "",
            "max_steps": int(row.get("max_steps") or 0) or None,
            "tools": [str(t) for t in tools] or None,
            "require_tests_before_finish": bool(perms.get("require_tests_before_finish")),
            "use_memory": perms.get("use_memory")}


async def _memory_context(svc, body: TaskIn, profile: dict | None) -> tuple[dict, dict]:
    """(context for the sidecar, memory facts for the record). Recall failure
    costs the task its memory, never its run (same rule as the engine)."""
    use = body.use_memory and (profile is None or profile.get("use_memory") is not False)
    info: dict[str, Any] = {"requested": bool(use), "recalled": False, "sources": [], "recipe_ids": [], "error": ""}
    ctx: dict[str, Any] = {}
    if not use:
        return ctx, info
    project = body.project_id or "bossman"
    try:
        from ..v2.memory.lifecycle_wiring import recall_for_task  # noqa: WPS433
        pack = await asyncio.wait_for(recall_for_task(svc, {"id": f"coding-{secrets.token_hex(3)}",
                                                            "prompt": body.instruction,
                                                            "meta": {"project_id": project}}), timeout=20)
        if pack:
            ctx["memory_text"] = pack["text"]
            info["recalled"] = True
            info["sources"] = list(pack.get("sources") or [])[:20]
    except Exception as exc:  # noqa: BLE001
        info["error"] = f"recall: {type(exc).__name__}"
    try:
        recipes_mod = importlib.import_module("bcc.features.coding_recipes")
    except ImportError:
        recipes_mod = None
    if recipes_mod is not None:
        try:
            fn = recipes_mod.executable_recipes
            recipes = (await fn(svc, body.instruction, project) if asyncio.iscoroutinefunction(fn)
                       else await asyncio.to_thread(fn, svc, body.instruction, project))
            if recipes:
                ctx["recipes"] = recipes
                info["recipe_ids"] = [str(r.get("id")) for r in recipes]
                info["recalled"] = True
        except Exception as exc:  # noqa: BLE001
            info["error"] = (info["error"] + f"; recipes: {type(exc).__name__}").strip("; ")
    return ctx, info


async def _skills_context(svc, body: TaskIn) -> tuple[list[dict], dict]:
    """Methodology skills from the vetted catalog (features.skills). UNVERIFIED
    guidance only: they grant no tool, no permission and change no success
    criterion; the catalog itself strips policy-violating lines. Never raises."""
    info: dict[str, Any] = {"ids": [], "error": ""}
    try:
        from .skills import skills_for_task  # noqa: WPS433
        items = await skills_for_task(svc, body.instruction)
    except Exception as exc:  # noqa: BLE001 — skills cost the task its guidance, never its run
        info["error"] = f"{type(exc).__name__}"
        return [], info
    skills = [{"id": s.get("id"), "title": s.get("title"), "text": s.get("text"),
               "status": s.get("status"), "unsupported_tools": s.get("unsupported_tools") or []}
              for s in items or [] if s.get("text")]
    info["ids"] = [s["id"] for s in skills]
    return skills, info


def _call_execute(record: dict, repo: Path, body: TaskIn, context: dict | None,
                  env: dict[str, str] | None, command: list[str] | None,
                  data_dir: str | os.PathLike | None) -> dict:
    import inspect  # noqa: WPS433
    try:
        param_count = len(inspect.signature(_execute).parameters)
    except (TypeError, ValueError):
        param_count = 7
    if param_count >= 7:
        return _execute(record, repo, body, context, env, command, data_dir)
    return _execute(record, repo, body, context, env, command)


async def _run(svc, record: dict, repo: Path, body: TaskIn, context: dict | None = None) -> None:
    try:
        data_dir = getattr(getattr(svc, "settings", None), "data_dir", None)
        if body.worker:
            fallback_list = getattr(body, "fallback_workers", None) or []
            candidates = [body.worker] + [
                w for w in fallback_list if w in WORKERS and w != body.worker
            ]
            attempts: list[dict[str, Any]] = []
            final: dict[str, Any] | None = None
            for idx, wid in enumerate(candidates):
                key = await _worker_key(WORKERS[wid]["key"], svc)
                if not key:
                    if idx == 0 and len(candidates) == 1:
                        raise RuntimeError(f"нет ключа {WORKERS[wid]['key']} для исполнителя {wid}")
                    attempts.append({"worker": wid, "model": WORKERS[wid]["model"], "skipped": "missing_key"})
                    continue
                env = {_WORKER_KEY_ENV: key, **_verify_python_env()}
                command = _worker_command(wid)
                attempt_rec = {**record, "worker": wid, "model": WORKERS[wid]["model"]}
                if hasattr(body, "model_copy"):
                    attempt_body = body.model_copy(update={"worker": wid, "model": WORKERS[wid]["model"]})
                elif len(candidates) == 1:
                    attempt_body = body
                else:
                    attempt_body = type("TaskBodyProxy", (), {"worker": wid, "model": WORKERS[wid]["model"]})()
                final = await asyncio.to_thread(_call_execute, attempt_rec, repo, attempt_body, context, env, command, data_dir)
                sidecar_info = final.get("sidecar") if isinstance(final.get("sidecar"), dict) else {}
                stop_reason = sidecar_info.get("stop_reason") or ""
                attempts.append({
                    "worker": wid,
                    "model": WORKERS[wid]["model"],
                    "tier": WORKERS[wid].get("tier", "cloud"),
                    "status": final.get("status"),
                    "stop_reason": stop_reason,
                    "steps": sidecar_info.get("steps", 0),
                })
                if final.get("status") == "completed" or stop_reason != "model_error":
                    break
            if final is None:
                raise RuntimeError(f"нет ключа {WORKERS[body.worker]['key']} для исполнителя {body.worker}")
            if len(attempts) > 1 or getattr(body, "fallback_workers", None):
                final["worker_attempts"] = attempts
                final["receipt"] = mission_receipt(final)
        else:
            env = await _sidecar_env(svc)
            local_cmd: list[str] | None = None
            if (
                body.model
                and body.model in _LOCAL_CODING_MODELS
                and "DETERMINISTIC-TEST-MODEL" not in os.environ.get(COMMAND_ENV, "")
            ):
                local_cmd = [
                    sys.executable, "-I", "-m", "bossman.apprentice.local_sidecar",
                    "--endpoint", "http://127.0.0.1:11434/v1", "--model", body.model,
                ]
            final = await asyncio.to_thread(_call_execute, record, repo, body, context, env, local_cmd, data_dir)
    except Exception as exc:  # noqa: BLE001
        final = {**record, "status": "failed", "error": f"{type(exc).__name__}: {exc}"[:800],
                 "finished_at": time.time()}
    _write(svc, final)
    await svc.bus.emit(f"coding.task.{final['status']}", task_id=final["id"], repo=str(repo),
                       changed_files=len(final.get("changed_files") or []),
                       error=str(final.get("error") or "")[:300])


# ---------------------------------------------------------------------------
# Apply a verified candidate to the canonical project (owner decision).
#
#   request (no approval_id) ─ eligibility ─ preflight ─▶ 202 WAIT_APPROVAL
#                                                          (approvals row, kind=coding_apply,
#                                                           preview = task + repo + base + digest)
#   request (approval_id)    ─ eligibility ─ preflight ─ consume(approval) ─▶
#        [lock] re-read record ─ preflight again ─ backup ─ git apply ─ after-state check
#            ├─ ok       ─▶ 200 APPLIED   (applied_at / applied_digest on the task; no commit)
#            └─ failure  ─▶ tree restored, 409 APPLY_FAILED | AFTER_STATE_MISMATCH
#
# Refusals (nothing touched, no approval consumed): 409 NOT_ELIGIBLE (reason_code
# NOT_COMPLETED | NOT_VERIFIED | EMPTY_DIFF | NO_EVIDENCE_DIGEST | EVIDENCE_DIGEST_MISMATCH
# | EVIDENCE_PATHS_MISMATCH), ALREADY_APPLIED, STALE_BASE, DIRTY_TARGET, PROTECTED_PATH,
# APPLY_CHECK_FAILED; 403 APPROVAL_INVALID (not approved / spent / other task or digest).
#
# Почему байты, а не текст: владелец получил отказ `git apply` на репозитории с
# core.autocrlf=true — diff с CRLF-контекстом, переданный как ТЕКСТ, на Windows
# превращается в CR CR LF (write_text / text=True stdin). Здесь в git уходит ровно
# тот набор байт, чей sha256 записан как evidence_digest, и только в рабочее дерево
# (без --index: `--index` с CRLF-контекстом против LF-индекса тоже падает); git сам
# применяет правила EOL канонического репозитория.
# ---------------------------------------------------------------------------

APPLY_KIND = "coding_apply"
#: One apply at a time in this process: two approved requests cannot interleave
#: their check → apply → verify on the same tree.
_APPLY_LOCK = threading.Lock()


class ApplyIn(BaseModel):
    approval_id: int | None = None


class _Refusal(Exception):
    def __init__(self, status: int, code: str, message: str, **extra: Any):
        super().__init__(message)
        self.status, self.detail = status, {"code": code, "message": message, **extra}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _eol(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def _diff_digest(diff: str) -> str:
    return "sha256:" + _sha(diff.encode("utf-8"))


def _candidate(result: Any) -> dict:
    files = getattr(result, "files", None) or {}
    after: dict[str, dict] = {}
    for name in result.changed_files:
        entry = files.get(name)
        if entry is None:
            after[name] = {"deleted": True}
        else:
            after[name] = {"deleted": False, "sha256": _sha(entry.data), "sha256_eol": _sha(_eol(entry.data))}
    return {"evidence_digest": _diff_digest(result.diff), "candidate_files": after}


def apply_preview(rec: dict) -> str:
    """The exact text the owner approves; approvals.consume() compares it
    byte for byte, so the approval is bound to THIS task and THIS digest."""
    return (f"coding apply: задача {rec.get('id')} → канонический проект (без commit/push)\n"
            f"repo: {rec.get('source_repo')}\nbase: {rec.get('base_commit')}\n"
            f"evidence: {rec.get('evidence_digest')}\n"
            f"files: {', '.join(sorted(rec.get('candidate_files') or {}))}")


def _eligibility(rec: dict) -> None:
    def no(reason: str, message: str):
        raise _Refusal(409, "NOT_ELIGIBLE", message, reason_code=reason)
    if rec.get("applied_at"):
        raise _Refusal(409, "ALREADY_APPLIED", "кандидат уже применён к проекту",
                       applied_at=rec.get("applied_at"), applied_digest=rec.get("applied_digest"))
    if rec.get("status") != "completed" or rec.get("outcome"):
        no("NOT_COMPLETED", f"задача не завершена успешно (status={rec.get('status')})")
    verification = rec.get("verification")
    if not rec.get("verify_tests") or not isinstance(verification, dict) or not (
            verification.get("ran") and verification.get("passed") is True):
        no("NOT_VERIFIED", "нет пройденной независимой проверки Bossman (verify_tests)")
    diff = rec.get("diff") or ""
    if not diff:
        no("EMPTY_DIFF", "у задачи нет патча")
    if not rec.get("evidence_digest"):
        no("NO_EVIDENCE_DIGEST", "у задачи нет записанного digest доказательств (задача старше этой версии)")
    if _diff_digest(diff) != rec.get("evidence_digest"):
        no("EVIDENCE_DIGEST_MISMATCH", "патч не совпадает с записанным digest доказательств")
    if not rec.get("base_commit") or not rec.get("candidate_files"):
        no("NO_EVIDENCE_DIGEST", "у задачи нет базового коммита или состояния файлов кандидата")


def _git_b(repo: Path, *args: str, data: bytes | None = None) -> subprocess.CompletedProcess:
    # bytes in, bytes out: no newline translation anywhere on the way to git.
    return subprocess.run(["git", "-C", str(repo), "--literal-pathspecs", *args], input=data,
                          capture_output=True, timeout=120, check=False)


def _err(proc: subprocess.CompletedProcess) -> str:
    return proc.stderr.decode("utf-8", "replace").strip()[:600]


def _touched(repo: Path, patch: bytes) -> list[str]:
    proc = _git_b(repo, "apply", "--numstat", "-z", "-", data=patch)
    if proc.returncode:
        raise _Refusal(409, "APPLY_CHECK_FAILED", "патч не читается git apply: " + _err(proc))
    parts, out, i = proc.stdout.split(b"\0"), [], 0
    while i < len(parts):
        fields = parts[i].split(b"\t", 2)
        if len(fields) == 3 and fields[2]:
            out.append(fields[2]); i += 1
        elif len(fields) == 3:                       # rename/copy: src and dst follow
            out += parts[i + 1:i + 3]; i += 3
        else:
            i += 1
    return sorted({p.decode("utf-8", "surrogateescape") for p in out if p})


def _preflight(rec: dict, repo: Path) -> tuple[bytes, list[str]]:
    """Checks on the canonical repo, immediately before applying. Raises _Refusal."""
    patch = rec["diff"].encode("utf-8")
    head = _git_b(repo, "rev-parse", "--verify", "HEAD")
    if head.returncode or head.stdout.decode().strip() != rec["base_commit"]:
        raise _Refusal(409, "STALE_BASE", "HEAD проекта не совпадает с базой задачи: кандидат проверялся "
                       "на другом коммите", base=rec["base_commit"],
                       head=head.stdout.decode(errors="replace").strip() or None)
    paths = _touched(repo, patch)
    if set(paths) != set(rec["candidate_files"]):
        raise _Refusal(409, "NOT_ELIGIBLE", "пути патча не совпадают с проверенным состоянием кандидата",
                       reason_code="EVIDENCE_PATHS_MISMATCH", paths=paths)
    oc, _wt, reason = _runtime()
    if oc is None:
        raise _Refusal(503, "RUNTIME_UNAVAILABLE", reason)
    try:    # the same boundary that admitted the candidate, against the record's scope
        oc._validate_scope(paths, rec.get("allowed_paths") or [], rec.get("protected_paths") or [])
    except oc.OpenHandsError as exc:
        raise _Refusal(409, "PROTECTED_PATH", str(exc)[:600], paths=paths)
    status = _git_b(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignored=matching",
                    "--", *paths)
    if status.returncode:
        raise _Refusal(409, "DIRTY_TARGET", "состояние проекта не читается: " + _err(status))
    dirty = sorted({e[3:].decode("utf-8", "replace") for e in status.stdout.split(b"\0") if len(e) > 3})
    if dirty:
        raise _Refusal(409, "DIRTY_TARGET", "в проекте есть локальные изменения затрагиваемых файлов",
                       paths=dirty)
    check = _git_b(repo, "apply", "--check", "--whitespace=nowarn", "-", data=patch)
    if check.returncode and b"\r\n" in patch:
        # Git for Windows can clone a clean LF checkout as CRLF when the
        # repository has core.autocrlf=true.  Keep the approved diff/digest
        # untouched in the record, but try its EOL-equivalent bytes against
        # the owner's checkout.  The after-state is still checked below.
        lf_patch = _eol(patch)
        lf_check = _git_b(repo, "apply", "--check", "--whitespace=nowarn", "-", data=lf_patch)
        if not lf_check.returncode:
            patch, check = lf_patch, lf_check
    if check.returncode:
        raise _Refusal(409, "APPLY_CHECK_FAILED", "git apply --check: " + _err(check))
    return patch, paths


def _read_state(p: Path) -> bytes | None:
    if p.is_symlink():
        return os.readlink(p).encode("utf-8", "surrogateescape")
    return p.read_bytes() if p.is_file() else None


def _restore(repo: Path, backup: dict[str, tuple[bytes, int] | None], created: list[Path]) -> list[str]:
    """Put every touched path back as it was; returns paths that could not be restored."""
    failed = []
    for rel, saved in backup.items():
        p = repo / rel
        try:
            if saved is None:
                if p.is_symlink() or p.is_file():
                    p.unlink()
            else:
                if p.is_symlink():
                    p.unlink()
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(saved[0])
                os.chmod(p, saved[1])
            if _read_state(p) != (saved[0] if saved else None):
                failed.append(rel)
        except OSError:
            failed.append(rel)
    for d in sorted(created, key=lambda x: len(x.parts), reverse=True):
        try:
            d.rmdir()
        except OSError:
            pass
    return failed


def _apply_locked(svc, task_id: str, repo: Path, approval_id: int) -> dict:
    """Blocking. Under the process lock: re-read, re-check, apply, verify, record."""
    with _APPLY_LOCK:
        rec = _read(svc, task_id)
        _eligibility(rec)                 # ALREADY_APPLIED by a concurrent request lands here
        patch, paths = _preflight(rec, repo)
        backup: dict[str, tuple[bytes, int] | None] = {}
        created: list[Path] = []
        for rel in paths:
            p = repo / rel
            if p.is_symlink():            # symlinks are not restorable byte-for-byte everywhere
                raise _Refusal(409, "APPLY_CHECK_FAILED", f"символическая ссылка в затрагиваемых путях: {rel}")
            backup[rel] = (p.read_bytes(), p.stat().st_mode & 0o777) if p.is_file() else None
            for parent in p.parents:      # directories git apply will create for new files
                if parent == repo or parent.exists():
                    break
                if parent not in created:
                    created.append(parent)
        proc = _git_b(repo, "apply", "--whitespace=nowarn", "-", data=patch)
        files: dict[str, dict] = {}
        mismatch: list[str] = []
        if not proc.returncode:
            for rel, want in rec["candidate_files"].items():
                data = _read_state(repo / rel)
                if want.get("deleted"):
                    ok, match = data is None, "deleted"
                elif data is None:
                    ok, match = False, "missing"
                elif _sha(data) == want.get("sha256"):
                    ok, match = True, "exact"
                elif _sha(_eol(data)) == want.get("sha256_eol"):
                    # the canonical repo's own EOL rules (core.autocrlf) wrote CRLF
                    # where the sandbox had LF, or back: same content for git.
                    ok, match = True, "eol"
                else:
                    ok, match = False, "different"
                files[rel] = {"match": match, "sha256": _sha(data) if data is not None else None}
                if not ok:
                    mismatch.append(rel)
        if proc.returncode or mismatch:
            failed = _restore(repo, backup, created)
            code = "APPLY_FAILED" if proc.returncode else "AFTER_STATE_MISMATCH"
            message = ("git apply: " + _err(proc)) if proc.returncode else \
                "результат не совпал с проверенным состоянием кандидата: " + ", ".join(mismatch)
            raise _Refusal(409, code, message + ("; дерево проекта восстановлено" if not failed else
                                                 "; ВНИМАНИЕ: не восстановлены: " + ", ".join(failed)),
                           restored=not failed, not_restored=failed or None, approval_id=approval_id)
        applied_digest = "sha256:" + _sha(json.dumps(sorted((k, v["sha256"]) for k, v in files.items()),
                                                     ensure_ascii=False).encode("utf-8"))
        applied = {"applied_at": time.time(), "applied_digest": applied_digest,
                   "apply": {"approval_id": approval_id, "base_commit": rec["base_commit"],
                             "evidence_digest": rec["evidence_digest"], "files": files,
                             "committed": False, "repo": str(repo)}}
        _write(svc, {**rec, **applied})
        return applied


async def apply_task(svc, task_id: str, approval_id: int | None) -> tuple[int, dict]:
    rec = _read(svc, task_id)
    _eligibility(rec)
    repo = await _confined_repo(svc, rec.get("source_repo") or "")
    await asyncio.to_thread(_preflight, rec, repo)
    preview = apply_preview(rec)
    if approval_id is None:
        # Одна ожидающая заявка на одно и то же действие: повторный запрос не
        # плодит подтверждения, а возвращает ту же.
        pending = [a for a in await svc.approvals.list("pending")
                   if a.get("kind") == APPLY_KIND and a.get("preview") == preview]
        appr = pending[0] if pending else await svc.approvals.create(APPLY_KIND, preview)
        return 202, {"state": "WAIT_APPROVAL", "id": task_id, "approval_id": appr.get("id"),
                     "preview": preview, "evidence_digest": rec["evidence_digest"]}
    # F-015: подтверждение — одобренная запись с ТЕМ ЖЕ kind и preview, одноразовая.
    if not await svc.approvals.consume(approval_id, kind=APPLY_KIND, preview=preview):
        raise _Refusal(403, "APPROVAL_INVALID", "подтверждение не одобрено, уже использовано или выдано "
                       "на другую задачу/другие доказательства; запросите новое")
    try:
        applied = await asyncio.to_thread(_apply_locked, svc, task_id, repo, int(approval_id))
    except _Refusal as exc:
        await svc.bus.emit("coding.task.apply_refused", task_id=task_id, code=exc.detail["code"],
                           approval_id=approval_id)
        raise
    await svc.bus.emit("coding.task.applied", task_id=task_id, repo=str(repo), approval_id=approval_id,
                       applied_digest=applied["applied_digest"], files=len(applied["apply"]["files"]))
    return 200, {"state": "APPLIED", "id": task_id, "applied_at": applied["applied_at"],
                 "applied_digest": applied["applied_digest"], **applied["apply"]}


@router.post("/coding-tasks/{task_id}/apply")
async def apply_candidate(task_id: str, body: ApplyIn, request: Request):
    """Owner decision: bring a VERIFIED candidate into the canonical project as
    working-tree modifications. Needs an approval each time; never commits."""
    try:
        status, payload = await apply_task(request.app.state.svc, task_id, body.approval_id)
    except _Refusal as exc:
        raise HTTPException(exc.status, exc.detail)
    return JSONResponse(payload, status_code=status)


class HelperAssistIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)
    code_context: str = Field(default="", max_length=16000)
    model: str = Field(default=SPARSE_HELPER_DEFAULT, max_length=120)
    max_vram_ratio: float = Field(default=0.50, ge=0.10, le=2.0)
    allow_primary_coder: bool = False
    generation_active: bool = False
    free_ram_gb: float | None = None
    num_ctx: int = Field(default=8192, ge=1024, le=32768)
    timeout_seconds: float = Field(default=90.0, ge=5.0, le=300.0)


def invoke_sparse_coder_helper(
    prompt: str,
    *,
    code_context: str = "",
    model: str = SPARSE_HELPER_DEFAULT,
    max_vram_ratio: float = 0.50,
    allow_primary_coder: bool = False,
    generation_active: bool = False,
    free_ram_gb: float | None = None,
    num_ctx: int = 8192,
    timeout: float = 90.0,
) -> dict[str, Any]:
    """Invoke a local sparse-activation (MoE) helper coder sub-agent (<= 50% VRAM of main by default)
    or a primary local coder (including r1-70b-abliterated:latest when allow_primary_coder=True,
    guarded by >= 50 GB free RAM when photo/video generation is active)."""
    profile = LOCAL_CODER_PROFILES.get(model)
    if profile is None:
        return {
            "ok": False,
            "error": f"модель {model!r} не входит в разрешённый список локальных кодеров",
            "allowed_models": list(LOCAL_CODER_PROFILES),
        }
    vram_ratio = float(profile["vram_ratio_of_main"])
    min_gen_ram = float(profile.get("min_free_ram_gb_during_gen") or 0.0)
    if generation_active and min_gen_ram > 0.0:
        avail_gb = free_ram_gb
        if avail_gb is None:
            try:
                import psutil  # noqa: WPS433
                avail_gb = round(psutil.virtual_memory().available / (1024 ** 3), 2)
            except Exception:  # noqa: BLE001
                avail_gb = None
        if avail_gb is not None and avail_gb < min_gen_ram:
            return {
                "ok": False,
                "code": "INSUFFICIENT_RAM_DURING_GENERATION",
                "error": (
                    f"модель {model} ({profile['vram_gb']} ГБ) не запускается одновременно с генерацией "
                    f"при свободной памяти {avail_gb:.1f} ГБ < {min_gen_ram:.0f} ГБ"
                ),
                "model": model,
                "vram_gb": profile["vram_gb"],
                "free_ram_gb": avail_gb,
                "min_free_ram_gb_during_gen": min_gen_ram,
            }
    if not allow_primary_coder and vram_ratio > max_vram_ratio:
        return {
            "ok": False,
            "error": (
                f"модель {model} требует {profile['vram_gb']} ГБ VRAM "
                f"({vram_ratio * 100:.1f}% от основной 36 ГБ), что превышает лимит {max_vram_ratio * 100:.1f}%"
            ),
            "model": model,
            "vram_gb": profile["vram_gb"],
            "vram_ratio_of_main": vram_ratio,
            "within_50pct_vram_budget": False,
        }
    user_text = prompt if not code_context else f"{prompt}\n\n```python\n{code_context}\n```"
    req_body = json.dumps({
        "model": model,
        "stream": False,
        "think": False,
        "keep_alive": "5m",
        "options": {"num_ctx": int(num_ctx), "temperature": 0.1},
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are Bossman's sparse MoE coding helper sub-agent. "
                    "Return clean, production-grade code or exact patch diff without filler."
                ),
            },
            {"role": "user", "content": user_text},
        ],
    }).encode("utf-8")
    t0 = time.perf_counter()
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(
            _OLLAMA_CHAT,
            data=req_body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read(524_288)
        data = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "model": model,
            "profile": profile,
            "within_50pct_vram_budget": vram_ratio <= 0.50,
            "error": f"{type(exc).__name__}: {exc}"[:400],
        }
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    msg = data.get("message") or {}
    content = str(msg.get("content") or msg.get("thinking") or "").strip()
    eval_count = int(data.get("eval_count") or 0)
    eval_dur_ns = int(data.get("eval_duration") or 0)
    eval_tps = round(eval_count / (eval_dur_ns / 1e9), 2) if eval_dur_ns > 0 else 0.0
    return {
        "ok": bool(content),
        "model": model,
        "architecture": profile["architecture"],
        "total_params_b": profile["total_params_b"],
        "active_params_b": profile["active_params_b"],
        "active_weights_pct": profile["active_weights_pct"],
        "vram_gb": profile["vram_gb"],
        "main_vram_gb": profile["main_vram_gb"],
        "vram_ratio_of_main": vram_ratio,
        "vram_ratio_of_70b": profile["vram_ratio_of_70b"],
        "within_50pct_vram_budget": vram_ratio <= 0.50,
        "num_ctx": int(num_ctx),
        "tokens_out": eval_count,
        "eval_tps": eval_tps,
        "elapsed_ms": elapsed_ms,
        "response": content,
    }


SELF_CODING_PRESETS: list[dict[str, Any]] = [
    {
        "id": "self-opencode-diagnostics",
        "title": "Самокодинг: диагностика и метрики OpenCode в Bossman",
        "instruction": (
            "Изучи модуль command-center/bcc/features/coding_tasks.py и добавь чистую вспомогательную "
            "функцию summarize_opencode_roster() (или обнови существующую), а также напиши для неё "
            "проверочный unit-тест и убедись, что run_tests проходит."
        ),
        "allowed_paths": ["command-center/bcc/features", "command-center/tests"],
        "protected_paths": [".github", "secrets", "bossman-core/bossman/apprentice/proc_tree.py"],
        "verify_tests": ["command-center/tests/test_coding_workers.py"],
    },
    {
        "id": "self-zone-bugfix",
        "title": "Самокодинг: найти и починить точечный баг с unit-тестом",
        "instruction": (
            "Проанализируй указанные модули Bossman, воспроизведи крайний случай unit-тестом, "
            "внеси минимальное исправление в разрешённых путях и запусти run_tests до зелёного результата."
        ),
        "allowed_paths": ["command-center/bcc/features", "command-center/tests"],
        "protected_paths": [".github", "secrets"],
        "verify_tests": ["command-center/tests/test_coding_workers.py"],
    },
]


def _is_bossman_repo(path: Path) -> bool:
    try:
        return (
            (path / ".git").exists()
            and (path / "command-center" / "bcc").is_dir()
            and (path / "bossman-core").is_dir()
        )
    except OSError:
        return False


def _canonical_bossman_repo() -> Path:
    from ..config import ROOT  # noqa: WPS433
    return ROOT.parent.resolve()


async def _resolve_self_repo(svc, *, ensure_allowed: bool = False) -> tuple[Path, bool]:
    roots = await allowed_roots(svc)
    for r in roots:
        if _is_bossman_repo(r):
            return r.resolve(), True
    canonical = _canonical_bossman_repo()
    in_roots = any(_within(canonical, [r]) for r in roots)
    if ensure_allowed and not in_roots and getattr(svc, "db", None) is not None and getattr(svc, "vault", None) is not None:
        from .tools_code import CODE_ROOTS_KEY, _setting_json, _write_setting_json  # noqa: WPS433
        current = await _setting_json(svc, CODE_ROOTS_KEY, None)
        updated = list(current) if isinstance(current, list) else [str(r) for r in roots]
        if str(canonical) not in updated:
            updated.append(str(canonical))
            await _write_setting_json(svc, CODE_ROOTS_KEY, updated)
        in_roots = True
    return canonical, in_roots


def _mask_key(raw: str | None) -> str:
    if not raw:
        return ""
    s = str(raw).strip()
    if len(s) <= 8:
        return "***"
    return f"{s[:4]}…{s[-3:]}"


async def opencode_workers_catalog(svc) -> dict[str, Any]:
    key_cache: dict[str, tuple[str | None, str]] = {}
    keys_info: list[dict[str, Any]] = []
    for kname in sorted(ALLOWED_WORKER_KEY_ENVS):
        val, src = await _worker_key_with_source(kname, svc)
        key_cache[kname] = (val, src)
        keys_info.append({
            "key_env": kname,
            "connected": bool(val),
            "source": src,
            "masked_preview": _mask_key(val),
        })
    workers_out: list[dict[str, Any]] = []
    for wid, spec in WORKERS.items():
        kname = spec["key"]
        if kname not in key_cache:
            key_cache[kname] = await _worker_key_with_source(kname, svc)
        val, src = key_cache[kname]
        tier = spec.get("tier") or ("free" if ":free" in spec["model"] or wid == "nvidia-nim" else "paid")
        workers_out.append({
            "id": wid,
            "label": spec["label"],
            "provider": spec.get("provider") or ("OpenRouter" if "openrouter" in spec["endpoint"] else "Cloud"),
            "endpoint": spec["endpoint"],
            "model": spec["model"],
            "tier": tier,
            "key_env": kname,
            "key_connected": bool(val),
            "key_source": src,
            "price_display": spec.get("price_display") or ("$0 / 1M (free)" if tier == "free" else "paid"),
            "context_window": spec.get("context_window") or "128K",
            "supports_tools": True,
        })
    self_repo, in_roots = await _resolve_self_repo(svc, ensure_allowed=False)
    free_list = [w for w in workers_out if w["tier"] == "free"]
    paid_list = [w for w in workers_out if w["tier"] == "paid"]
    ready_free = [w["id"] for w in free_list if w["key_connected"]]
    ready_paid = [w["id"] for w in paid_list if w["key_connected"]]
    return {
        "workers": workers_out,
        "keys": keys_info,
        "local": {
            "default_sparse_helper": SPARSE_HELPER_DEFAULT,
            "models": LOCAL_CODER_PROFILES,
        },
        "self_coding": {
            "source_repo": str(self_repo),
            "in_allowed_roots": in_roots,
            "is_bossman_checkout": _is_bossman_repo(self_repo),
            "presets": SELF_CODING_PRESETS,
        },
        "summary": {
            "total_cloud_workers": len(workers_out),
            "free_cloud_workers": len(free_list),
            "paid_cloud_workers": len(paid_list),
            "connected_cloud_workers": len(ready_free) + len(ready_paid),
            "any_cloud_ready": bool(ready_free or ready_paid),
            "ready_free_workers": ready_free,
            "ready_paid_workers": ready_paid,
        },
        "spend_policy": {
            "priority_order": list(MODEL_TIER_PRIORITY),
            "task_spend_cap_usd": DEFAULT_TASK_SPEND_CAP_USD,
            "daily_spend_cap_usd": DEFAULT_DAILY_SPEND_CAP_USD,
            "runpod_allowed_without_owner_approval": False,
        },
    }


def summarize_opencode_roster() -> dict[str, Any]:
    """Deterministic summary of Bossman's curated OpenCode cloud & local coding models."""
    free_ids = [wid for wid, s in WORKERS.items() if s.get("tier") == "free"]
    paid_ids = [wid for wid, s in WORKERS.items() if s.get("tier") == "paid"]
    providers = sorted({s.get("provider", "Cloud") for s in WORKERS.values()})
    return {
        "total_cloud": len(WORKERS),
        "free_cloud_ids": free_ids,
        "paid_cloud_ids": paid_ids,
        "local_coder_ids": list(LOCAL_CODER_PROFILES),
        "providers": providers,
        "priority_order": list(MODEL_TIER_PRIORITY),
        "task_spend_cap_usd": DEFAULT_TASK_SPEND_CAP_USD,
        "daily_spend_cap_usd": DEFAULT_DAILY_SPEND_CAP_USD,
    }


class ConnectWorkerKeyIn(BaseModel):
    key_env: str = Field(min_length=3, max_length=64)
    api_key: str = Field(min_length=4, max_length=512)
    persist_to_owner_file: bool = False


class SelfTargetIn(BaseModel):
    ensure_allowed_root: bool = True


@router.get("/coding-tasks/workers")
async def get_workers_catalog(request: Request):
    return await opencode_workers_catalog(request.app.state.svc)


@router.post("/coding-tasks/connect-key")
async def connect_worker_key(body: ConnectWorkerKeyIn, request: Request):
    key_env = body.key_env.strip().upper()
    if key_env not in ALLOWED_WORKER_KEY_ENVS:
        raise HTTPException(422, {
            "code": "UNSUPPORTED_WORKER_KEY",
            "message": f"ключ {key_env} не входит в список ключей облачных кодеров",
            "allowed_keys": sorted(ALLOWED_WORKER_KEY_ENVS),
        })
    secret = body.api_key.strip().strip('"').strip("'")
    if not secret:
        raise HTTPException(422, {"code": "EMPTY_API_KEY", "message": "ключ не может быть пустым"})
    svc = request.app.state.svc
    saved_to: list[str] = []
    if getattr(svc, "db", None) is not None and getattr(svc, "vault", None) is not None:
        import sqlalchemy as sa  # noqa: WPS433
        from ..db import settings_kv  # noqa: WPS433
        setting_key = f"cred.{key_env}"
        enc = svc.vault.encrypt(secret)
        async with svc.db.session() as s:
            await s.execute(sa.delete(settings_kv).where(settings_kv.c.key == setting_key))
            await s.execute(sa.insert(settings_kv).values(key=setting_key, value_enc=enc))
            await s.commit()
        saved_to.append("vault")
    if body.persist_to_owner_file:
        try:
            OWNER_KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
            lines = OWNER_KEYS_FILE.read_text(encoding="utf-8").splitlines() if OWNER_KEYS_FILE.exists() else []
            out_lines: list[str] = []
            replaced = False
            for line in lines:
                if line.startswith(key_env + "="):
                    out_lines.append(f"{key_env}={secret}")
                    replaced = True
                else:
                    out_lines.append(line)
            if not replaced:
                out_lines.append(f"{key_env}={secret}")
            OWNER_KEYS_FILE.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
            saved_to.append("owner_keys_file")
        except OSError as exc:
            raise HTTPException(500, {"code": "KEY_FILE_WRITE_FAILED", "message": str(exc)[:300]}) from exc
    unlocked = [wid for wid, spec in WORKERS.items() if spec["key"] == key_env]
    return {
        "ok": True,
        "key_env": key_env,
        "connected": True,
        "saved_to": saved_to,
        "masked_preview": _mask_key(secret),
        "workers_unlocked": unlocked,
    }


@router.post("/coding-tasks/self-target")
async def post_self_target(body: SelfTargetIn, request: Request):
    svc = request.app.state.svc
    repo, in_roots = await _resolve_self_repo(svc, ensure_allowed=body.ensure_allowed_root)
    head_proc = _git_b(repo, "rev-parse", "--verify", "HEAD") if (repo / ".git").exists() else None
    head = head_proc.stdout.decode("utf-8", "replace").strip() if head_proc and not head_proc.returncode else None
    return {
        "ok": True,
        "source_repo": str(repo),
        "in_allowed_roots": in_roots,
        "is_bossman_checkout": _is_bossman_repo(repo),
        "head_commit": head,
        "default_allowed_paths": ["command-center/bcc/features", "command-center/tests"],
        "default_protected_paths": [".github", "secrets"],
        "presets": SELF_CODING_PRESETS,
    }


@router.get("/coding-tasks/readiness")
async def get_readiness(request: Request, worker: str | None = None, model: str | None = None):
    if model and model in _LOCAL_CODING_MODELS:
        return await readiness(request.app.state.svc, worker, model)
    if worker is None:
        return await readiness(request.app.state.svc)
    return await readiness(request.app.state.svc, worker)


@router.get("/coding-tasks/local-coders")
async def get_local_coders():
    return {
        "default_sparse_helper": SPARSE_HELPER_DEFAULT,
        "models": LOCAL_CODER_PROFILES,
    }


@router.post("/coding-tasks/helper-assist")
async def post_helper_assist(body: HelperAssistIn):
    return await asyncio.to_thread(
        invoke_sparse_coder_helper,
        body.prompt,
        code_context=body.code_context,
        model=body.model,
        max_vram_ratio=body.max_vram_ratio,
        allow_primary_coder=body.allow_primary_coder,
        generation_active=body.generation_active,
        free_ram_gb=body.free_ram_gb,
        num_ctx=body.num_ctx,
        timeout=body.timeout_seconds,
    )


@router.get("/coding-tasks")
async def list_tasks(request: Request):
    return {"items": [_public(r) for r in _list(request.app.state.svc)]}


@router.get("/coding-tasks/{task_id}")
async def get_task(task_id: str, request: Request):
    rec = _read(request.app.state.svc, task_id)
    for k, v in estimate_coding_task_cost(rec).items():
        rec.setdefault(k, v)
    if rec.get("status") in TERMINAL and "receipt" not in rec:
        rec["receipt"] = mission_receipt(rec)
    return rec


@router.get("/coding-tasks/{task_id}/receipt")
async def get_task_receipt(task_id: str, request: Request):
    rec = _read(request.app.state.svc, task_id)
    return rec.get("receipt") or mission_receipt(rec)


@router.post("/coding-tasks/{task_id}/cancel")
async def cancel_task(task_id: str, request: Request):
    """Owner STOP for one coding task: the sidecar's whole process tree is
    killed; the record ends failed/CANCELLED (never completed) once the worker
    returns. Cancelling a finished task changes nothing."""
    rec = _read(request.app.state.svc, task_id)
    if rec.get("status") in TERMINAL:
        return {"id": task_id, "status": rec["status"], "cancelled": False}
    _CANCELLED.add(task_id)
    tree = _ACTIVE.get(task_id)
    if tree is not None:
        await asyncio.to_thread(tree.kill)
    await request.app.state.svc.bus.emit("coding.task.cancel_requested", task_id=task_id)
    return {"id": task_id, "status": "cancelling", "cancelled": True, "process_killed": tree is not None}


@router.post("/coding-tasks")
async def create_task(body: TaskIn, request: Request):
    svc = request.app.state.svc
    if _stop_active(svc.settings.data_dir):
        raise HTTPException(423, {"code": "OWNER_STOP", "message": "Остановлено владельцем (STOP)"})
    if body.worker is not None and body.worker not in WORKERS:
        raise HTTPException(422, {"code": "UNKNOWN_WORKER", "workers": sorted(WORKERS)})
    for fw in body.fallback_workers or []:
        if fw not in WORKERS:
            raise HTTPException(422, {"code": "UNKNOWN_WORKER", "workers": sorted(WORKERS)})
    if body.worker is None and body.model and body.model in _LOCAL_CODING_MODELS:
        ready = await readiness(svc, body.worker, body.model)
    else:
        ready = await readiness(svc, body.worker)
    if not ready["available"]:
        raise HTTPException(503, {"code": "OPENHANDS_UNAVAILABLE", "message": ready["reason"],
                                  **({"hint": ready["hint"]} if ready.get("hint") else {}), "readiness": ready})
    if not body.allowed_paths:
        raise HTTPException(422, {"message": "allowed_paths обязателен: агент должен получить явную область правок"})
    raw_repo = body.source_repo
    if body.self_target or raw_repo.strip().lower() in {"self", "bossman"}:
        self_repo, _ = await _resolve_self_repo(svc, ensure_allowed=True)
        raw_repo = str(self_repo)
    repo = await _confined_repo(svc, raw_repo)
    profile = await _agent_profile(svc, body.agent_id)
    context, memory = await _memory_context(svc, body, profile)
    skills, skills_info = await _skills_context(svc, body)
    if skills:
        context["skills"] = skills
    if profile:
        context["profile"] = {k: v for k, v in profile.items() if k != "use_memory" and v is not None}
    record = {"id": secrets.token_hex(6), "status": "running", "instruction": body.instruction,
              "boot_id": BOOT_ID, "agent_id": body.agent_id,
              "agent": ({"id": profile["agent_id"], "name": profile["name"]} if profile else None),
              "project_id": body.project_id, "memory": memory, "skills": skills_info,
              "verify_tests": list(body.verify_tests),
              "source_repo": str(repo), "allowed_paths": list(body.allowed_paths),
              "protected_paths": list(body.protected_paths),
              "model": WORKERS[body.worker]["model"] if body.worker else body.model, "worker": body.worker,
              "fallback_workers": list(body.fallback_workers or []),
              "self_target": bool(body.self_target or body.source_repo.strip().lower() in {"self", "bossman"}),
              "created_at": time.time(), "finished_at": None, "changed_files": [], "diff": "",
              "error": "", "evidence": None, "sandbox_cleanup": None,
              "authority": {"push": False, "merge": False, "deploy": False}}
    _write(svc, record)
    await svc.bus.emit("coding.task.created", task_id=record["id"], repo=str(repo))
    task = asyncio.create_task(_run(svc, record, repo, body, context))
    running = getattr(svc, "_coding_tasks_running", None)
    if running is None:
        running = svc._coding_tasks_running = set()
    running.add(task)
    task.add_done_callback(running.discard)
    return _public(record)


FEATURE = Feature(name="coding_tasks", router=router)
