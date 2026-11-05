"""Harness guards as tools (03.11.2026): G3 config refs, G4 media provenance, G5 content policy, G6 publish gate.

The checks live in `bcc.guards.*` (pure, unit-tested); this module only registers them in the one tool registry so
the Harness agent (id 80) and any agent the owner grants them can call them. G1 (foreground guard) and G2 (empty-
result recovery) are not tools: they act inside pc.studio / computer.act and Engine._call_model automatically.

Path rule: only project folders under the Bossman work roots (BCC_GUARD_ROOTS, default ~/Bossman and D:/Bossman)
or the run's workspace. G3/G4/G5 only read; G6 also runs the project's build-gate command, so it is `terminal.run`
(dangerous: ASK unless granted)."""
from __future__ import annotations

import asyncio

import os
from pathlib import Path
from typing import Any

from ..tools import REGISTRY, ToolContext, ToolResult, ToolSpec
from ..guards import config_refs, content_policy, media_provenance, publish_gate
from . import Feature

TOOL_NAMES = ("guard.config_refs", "guard.media_provenance", "guard.content_policy", "guard.publish_gate")


def guard_roots(workspace: str = "") -> list[Path]:
    raw = os.environ.get("BCC_GUARD_ROOTS")
    roots = [Path(p) for p in raw.split(os.pathsep) if p.strip()] if raw else [Path.home() / "Bossman",
                                                                                Path("D:/Bossman")]
    if workspace:
        roots.append(Path(workspace))
    out = []
    for r in roots:
        try:
            out.append(r.resolve())
        except OSError:
            continue
    return out


def resolve_project(raw: Any, workspace: str = "") -> Path:
    text = str(raw or workspace or "").strip().strip('"')
    if not text:
        raise ValueError("не указан путь к проекту (path)")
    p = Path(os.path.expanduser(text))
    if not p.is_absolute() and workspace:
        p = Path(workspace) / p
    real = p.resolve(strict=True)
    if not any(real == r or r in real.parents for r in guard_roots(workspace)):
        raise PermissionError(f"{real} вне рабочих папок Bossman — guard.* проверяет только проекты")
    return real


def _registry_path(raw: Any, svc: Any) -> Path | None:
    text = str(raw or os.environ.get("BCC_MEDIA_REGISTRY") or "").strip()
    if text:
        return Path(text)
    data_dir = getattr(getattr(svc, "settings", None), "data_dir", None)
    cand = Path(data_dir) / "guards" / "third_party_registry.json" if data_dir else None
    return cand if cand is not None and cand.is_file() else None


def _reviewer(ctx: ToolContext):
    """The agent's own model as G5 reviewer (one short call); None when it cannot be built."""
    svc = ctx.svc
    reg = getattr(svc, "registry", None)
    model_id = (ctx.agent or {}).get("model_id")
    if reg is None or model_id is None:
        return None

    async def review(prompt: str) -> str:
        adapter, model = await reg.adapter_for(int(model_id))
        res = await adapter.chat(model["name"], [{"role": "user", "content": prompt}], max_tokens=200)
        return res.text
    return review


def _err(name: str, exc: BaseException) -> ToolResult:
    return ToolResult(content=f"ERROR: {exc}", error=True, one_line=f"{name}: {type(exc).__name__}")


async def _config_refs(args: dict, ctx: ToolContext) -> ToolResult:
    try:
        root = resolve_project(args.get("path"), ctx.workspace)
    except (OSError, ValueError, PermissionError) as exc:
        return _err("guard.config_refs", exc)
    rep = await asyncio.to_thread(config_refs.check, root)
    return ToolResult(content=rep.summary_ru(), one_line=rep.summary_ru().splitlines()[0][:180], data=rep.as_dict(),
                      error=False)


async def _media(args: dict, ctx: ToolContext) -> ToolResult:
    try:
        paths = [resolve_project(p, ctx.workspace) for p in (args.get("paths") or [args.get("path")])]
    except (OSError, ValueError, PermissionError) as exc:
        return _err("guard.media_provenance", exc)
    reg_path = _registry_path(args.get("registry"), ctx.svc)
    if reg_path is None or not reg_path.is_file():
        return ToolResult(content="G4 FAIL: реестр чужих файлов не задан (registry или BCC_MEDIA_REGISTRY) — "
                                  "проверить происхождение нечем, загрузка запрещена", error=True,
                          one_line="guard.media_provenance: no registry", data={"ok": False})
    res = await asyncio.to_thread(media_provenance.check, paths, media_provenance.load_registry(reg_path))
    text = media_provenance.summary_ru(res)
    return ToolResult(content=text, one_line=text.splitlines()[0][:180], data=res, error=False)


async def _content(args: dict, ctx: ToolContext) -> ToolResult:
    lines: list[tuple[str, int, str]] = []
    if args.get("text"):
        lines = [("<text>", i + 1, t) for i, t in enumerate(str(args["text"]).splitlines())]
    else:
        try:
            root = resolve_project(args.get("path"), ctx.workspace)
        except (OSError, ValueError, PermissionError) as exc:
            return _err("guard.content_policy", exc)
        lines = await asyncio.to_thread(publish_gate.diff_lines, root, args.get("base") or None)
    res = await content_policy.check(lines, reviewer=_reviewer(ctx) if args.get("review") else None)
    text = res.summary_ru()
    return ToolResult(content=text, one_line=text.splitlines()[0][:180], data=res.as_dict(), error=False)


async def _publish(args: dict, ctx: ToolContext) -> ToolResult:
    try:
        root = resolve_project(args.get("path"), ctx.workspace)
    except (OSError, ValueError, PermissionError) as exc:
        return _err("guard.publish_gate", exc)
    reg_path = _registry_path(args.get("registry"), ctx.svc)
    registry = media_provenance.load_registry(reg_path) if reg_path and reg_path.is_file() else None
    cmd = args.get("check_cmd")
    cmd = [str(x) for x in cmd] if isinstance(cmd, list) and cmd else None
    res = await publish_gate.evaluate(root, check_cmd=cmd, expect=str(args.get("expect") or publish_gate.DEFAULT_EXPECT),
                                      registry=registry, base=args.get("base") or None,
                                      reviewer=_reviewer(ctx) if args.get("review") else None)
    return ToolResult(content=res.report_ru(), one_line=res.report_ru().splitlines()[0][:180], data=res.as_dict(),
                      error=False)


_PATH = {"type": "string", "maxLength": 1000, "description": "Папка проекта (полный путь или относительно workspace)"}


async def setup(svc) -> None:
    REGISTRY.register(ToolSpec(
        name="guard.config_refs",
        description="G3: найти в Luau/Python-проекте обращения Module.FIELD к полям, которых модуль не определяет "
                    "(как MatchConfig.DAILY_MATCH_POINT_CAP, из-за которого падали награды). Только чтение.",
        handler=_config_refs, input_schema={"path": _PATH}, required=["path"], category="read",
        permission="filesystem.read", source="builtin", default_effect="auto", timeout_seconds=300))
    REGISTRY.register(ToolSpec(
        name="guard.media_provenance",
        description="G4: перед загрузкой аудио/видео сверить файлы с локальным реестром чужих треков (sha256, "
                    "длительность, огибающая громкости, chromaprint) — ловит переименованные копии. Помеченные не грузить.",
        handler=_media, input_schema={"path": _PATH, "paths": {"type": "array", "items": _PATH, "maxItems": 50},
                                      "registry": {"type": "string", "maxLength": 1000}},
        category="read", permission="filesystem.read", source="builtin", default_effect="auto", timeout_seconds=900))
    REGISTRY.register(ToolSpec(
        name="guard.content_policy",
        description="G5: проверить текст или дифф игры (с base) на реальных людей/политиков, бренды/табак/никотин, "
                    "крипту, алкоголь, азартные игры: список запретов + (review=true) модель-ревьюер.",
        handler=_content, input_schema={"path": _PATH, "base": {"type": "string", "maxLength": 200},
                                        "text": {"type": "string", "maxLength": 200000},
                                        "review": {"type": "boolean", "default": False}},
        category="read", permission="filesystem.read", source="builtin", default_effect="auto", timeout_seconds=300))
    REGISTRY.register(ToolSpec(
        name="guard.publish_gate",
        description="G6: можно ли публиковать/выпускать: дерево закоммичено, сборочная проверка (bash tools/check.sh "
                    "→ GATE PASS) проходит, G3/G4/G5 чисты. Возвращает короткий отчёт по-русски. Без PASS не публиковать.",
        handler=_publish, input_schema={"path": _PATH, "check_cmd": {"type": "array", "items": {"type": "string"},
                                                                     "maxItems": 20},
                                        "expect": {"type": "string", "maxLength": 100},
                                        "registry": {"type": "string", "maxLength": 1000},
                                        "base": {"type": "string", "maxLength": 200},
                                        "review": {"type": "boolean", "default": False}},
        required=["path"], category="exec", permission="terminal.run", source="builtin", default_effect="ask",
        timeout_seconds=1200, idempotent=True))


def profile_tools(existing: list[str] | None) -> list[str]:
    """Agent 80 (Bossman Harness) tool list with the guard tools added (idempotent, order kept)."""
    out = list(existing or [])
    for name in TOOL_NAMES:
        if name not in out:
            out.append(name)
    return out


def harness_profile_patch(agent: dict) -> dict[str, Any]:
    """Full Agent 80 (Bossman Harness) profile patch: guard.* + pc.studio tools, plus local-model permissions
    (removes erroneous cloud_pc_access=False that turned the local agent chat-only while keeping cloud fallback
    blocked from PC tools because cloud_pc_access is not True)."""
    tools = profile_tools(agent.get("tools"))
    if "pc.studio" not in tools:
        tools.append("pc.studio")
    perms = dict(agent.get("permissions") or {}) if isinstance(agent.get("permissions"), dict) else {}
    if perms.get("cloud_pc_access") is False:
        perms.pop("cloud_pc_access", None)
    perms.setdefault("filesystem.read", True)
    perms.setdefault("filesystem.write", True)
    perms.setdefault("computer.control", True)
    perms.setdefault("empty_recovery", True)
    return {"tools": tools, "permissions": perms}


FEATURE = Feature(name="harness_guards", setup=setup)

__all__ = ["FEATURE", "TOOL_NAMES", "profile_tools", "harness_profile_patch", "resolve_project", "guard_roots"]
