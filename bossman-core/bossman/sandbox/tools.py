"""Stage 8 — инструменты песочницы для агента (регистрируются в общем REGISTRY).

Правила безопасности этого слоя:
- `sandbox.create` для CONNECTED/HOSTILE и для любого не-OFFLINE режима требует
  подтверждения (confirm_default=True): это консеквентное действие, approvals
  остаются НАД песочницей (non-negotiable #8);
- команда принимается ТОЛЬКО массивом argv; строка не разбирается шеллом;
- artifact-выдача идёт через ArtifactGate: агент не получает сырой файл в обход
  проверок traversal/размера/карантина;
- при выключенной фиче все инструменты честно отдают SANDBOX_DISABLED.

toolkit/__init__.py НЕ правится — используем его публичный register().
"""
from __future__ import annotations

import json

from .. import errors
from ..toolkit import ToolContext, ToolDef, ToolResult, register
from . import sandbox_enabled
from .models import NetworkMode, PolicyMode, ResourceRequest, SandboxSpec

_SESSIONS: dict[str, object] = {}   # короткое имя -> SandboxSession (в пределах процесса)


def _manager():
    from .subsystem import MANAGER
    return MANAGER


def _fail(exc: Exception, tool: str) -> ToolResult:
    code = getattr(exc, "code", None)
    detail = getattr(exc, "detail", str(exc))
    label = code.value if code is not None else type(exc).__name__
    return ToolResult(f"{label}: {detail}", one_line=f"{tool}: {label}", error=True)


def _require_enabled() -> None:
    if not sandbox_enabled():
        raise errors.SandboxDisabled("sandbox feature is disabled (OFF=OFF)")


async def _create(args: dict, ctx: ToolContext) -> ToolResult:
    try:
        _require_enabled()
        mode = PolicyMode(str(args.get("policy_mode", "SAFE")).upper())
        net = NetworkMode(str(args.get("network_mode", "OFFLINE")).upper())
        argv = args.get("argv")
        if isinstance(argv, str):
            raise errors.BossmanError("argv must be a list of arguments, not a string")
        res = ResourceRequest(wall_time_seconds=int(args.get("wall_time_seconds", 120)))
        spec = SandboxSpec(
            task=str(args.get("task", "")),
            policy_mode=mode,
            network_mode=net,
            resources=res,
            allowlist=tuple(args.get("allowlist") or ()),
            workspace_source=args.get("workspace_source"),
            trusted_source=bool(args.get("trusted_source", False)),
            labels={"argv": list(argv)} if argv else {},
        )
        s = await _manager().create(spec)
        _SESSIONS[s.id] = s
        return ToolResult(
            f"sandbox: {s.id}\nstate: {s.state.value}\nrisk: {s.risk.level.value}\n"
            f"isolation: {s.policy.isolation_tier.value}\nnetwork: {s.policy.network_mode.value}",
            one_line=f"sandbox.create: {s.id}")
    except Exception as exc:  # noqa: BLE001
        return _fail(exc, "sandbox.create")


async def _run(args: dict, ctx: ToolContext) -> ToolResult:
    try:
        _require_enabled()
        s = _SESSIONS.get(str(args.get("sandbox_id", "")))
        if s is None:
            raise errors.NotFound("unknown sandbox id")
        m = _manager()
        await m.start(s)
        state = await m.poll(s)
        return ToolResult(f"sandbox: {s.id}\nstate: {state.value}\nerror: {s.error or '-'}",
                          one_line=f"sandbox.run: {state.value}")
    except Exception as exc:  # noqa: BLE001
        return _fail(exc, "sandbox.run")


async def _status(args: dict, ctx: ToolContext) -> ToolResult:
    try:
        m = _manager()
        sid = args.get("sandbox_id")
        if sid:
            s = _SESSIONS.get(str(sid))
            if s is None:
                raise errors.NotFound("unknown sandbox id")
            body = {"id": s.id, "state": s.state.value, "error": s.error,
                    "lease": s.lease_id}
        else:
            body = {"enabled": sandbox_enabled(), "runtime": m.runtime.name,
                    "sessions": [{"id": x.id, "state": x.state.value} for x in m.sessions.values()]}
        return ToolResult(json.dumps(body, ensure_ascii=False, indent=2),
                          one_line="sandbox.status: ok")
    except Exception as exc:  # noqa: BLE001
        return _fail(exc, "sandbox.status")


async def _collect(args: dict, ctx: ToolContext) -> ToolResult:
    """Забрать артефакт ТОЛЬКО через ArtifactGate (traversal/размер/карантин)."""
    try:
        _require_enabled()
        s = _SESSIONS.get(str(args.get("sandbox_id", "")))
        if s is None:
            raise errors.NotFound("unknown sandbox id")
        gate = _manager().artifact_gate(s)
        art = gate.inspect(str(args.get("path", "")))
        verdict = "QUARANTINED" if art.quarantined else "accepted"
        return ToolResult(
            f"artifact: {art.rel_path}\nsize: {art.size}\nsha256: {art.sha256}\n"
            f"verdict: {verdict}\nreasons: {', '.join(art.reasons) or '-'}",
            one_line=f"sandbox.collect: {verdict}")
    except Exception as exc:  # noqa: BLE001
        return _fail(exc, "sandbox.collect")


async def _destroy(args: dict, ctx: ToolContext) -> ToolResult:
    try:
        s = _SESSIONS.pop(str(args.get("sandbox_id", "")), None)
        if s is None:
            raise errors.NotFound("unknown sandbox id")
        await _manager().destroy(s)
        return ToolResult(f"sandbox {s.id}: {s.state.value}", one_line="sandbox.destroy: ok")
    except Exception as exc:  # noqa: BLE001
        return _fail(exc, "sandbox.destroy")


_ARGV = {"type": "array", "items": {"type": "string"},
         "description": "command as an argument array; never a shell string"}
_SID = {"type": "string", "description": "sandbox id from sandbox.create"}

_TOOLS = (
    ToolDef(name="sandbox.create",
            description="Create an isolated sandbox (policy/risk resolved, resources leased). "
                        "Consequential: requires approval.",
            rights="exec", handler=_create,
            params={"task": {"type": "string"}, "argv": _ARGV,
                    "policy_mode": {"type": "string", "enum": ["SAFE", "DEVELOPER", "CONNECTED", "HOSTILE"]},
                    "network_mode": {"type": "string", "enum": ["OFFLINE", "ALLOWLIST", "INTERNET"]},
                    "allowlist": {"type": "array", "items": {"type": "string"}},
                    "workspace_source": {"type": "string"},
                    "trusted_source": {"type": "boolean"},
                    "wall_time_seconds": {"type": "integer"}},
            required=["task"], confirm_default=True, token_limit=600),
    ToolDef(name="sandbox.run", description="Start and await the sandbox workload.",
            rights="exec", handler=_run, params={"sandbox_id": _SID},
            required=["sandbox_id"], confirm_default=True, token_limit=600),
    ToolDef(name="sandbox.status", description="Read sandbox feature/session status.",
            rights="read", handler=_status, params={"sandbox_id": _SID}, token_limit=1200),
    ToolDef(name="sandbox.collect",
            description="Import one sandbox output file through the Artifact Gate "
                        "(traversal/size/quarantine checks).",
            rights="read", handler=_collect,
            params={"sandbox_id": _SID, "path": {"type": "string"}},
            required=["sandbox_id", "path"], token_limit=800),
    ToolDef(name="sandbox.destroy", description="Destroy the sandbox and release its lease.",
            rights="exec", handler=_destroy, params={"sandbox_id": _SID},
            required=["sandbox_id"], token_limit=300),
)


def register_tools() -> None:
    """Идемпотентная регистрация в общем REGISTRY (публичный API toolkit)."""
    for t in _TOOLS:
        register(t)
