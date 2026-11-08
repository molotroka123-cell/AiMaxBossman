"""Creator Mode Feature — DeepSeek-style Creator Mode in Bossman Harness & Web Pult.

Provides:
- `/api/creator/status` — service state, target types, slash commands, @context sources, local models, telemetry, and live K1m6a stream student/teacher summary
- `/api/creator/projects` — GET list / POST build (`plugin`, `design_console`, `game`, `visualizer_3d`)
- `/api/creator/projects/{slug}` — GET project details & version history
- `/api/creator/projects/{slug}/versions/{version_num}/preview` — CSP-hardened HTML preview
- `/api/creator/projects/{slug}/install-skill` — one-click install into Bossman SkillLibrary (`SKILL.md`)
- `/api/creator/projects/{slug}/send-to-pult` — send build summary & score to Owner's Telegram Pult
- `/api/creator/arena` — run multi-agent local model arena (`ace-decider-08b`, `ace-qwen3-17b`, `qwen35-3d-coder`, `qwen3-coder:30b`)
- `/api/creator/telemetry` — weight-space / LoRA layer delta telemetry for the 3D LLM Visualizer
- `/api/creator/k1m6a-live` — live Twitch K1m6a Student Watcher + Teacher Review status
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from ..creator_mode.blueprints import COMMAND_MAP, CONTEXT_FACTS, KIND_LABELS_RU, VALID_KINDS
from ..creator_mode.service import CreatorError, CreatorModeService
from ..tools import REGISTRY, ToolContext, ToolResult, ToolSpec
from . import Feature

router = APIRouter(prefix="/creator", tags=["creator-mode"])

_SERVICE: CreatorModeService | None = None
_SVC_REF: Any | None = None
K1M6A_LIVE_DIR = Path(
    os.environ.get(
        "BOSSMAN_K1M6A_LIVE_DIR",
        r"C:\Users\asd\Bossman\creative-runs\K1M6A-LIVE-20261105",
    )
)


def get_creator_service(svc: Any | None = None) -> CreatorModeService:
    global _SERVICE, _SVC_REF
    if svc is not None:
        _SVC_REF = svc
    if _SERVICE is not None:
        return _SERVICE
    data_dir = Path(os.environ.get("BOSSMAN_CREATOR_DIR", "data"))
    if svc is not None and getattr(svc, "settings", None) is not None:
        db_path = getattr(svc.settings, "db_path", None)
        if db_path and "BOSSMAN_CREATOR_DIR" not in os.environ:
            data_dir = Path(db_path).resolve().parent
    _SERVICE = CreatorModeService(
        data_dir=data_dir,
        bus=getattr(svc, "bus", None) if svc else None,
    )
    return _SERVICE


def read_k1m6a_live_summary() -> dict[str, Any]:
    status_file = K1M6A_LIVE_DIR / "live_status.json"
    obs_file = K1M6A_LIVE_DIR / "student_observations.jsonl"
    rev_file = K1M6A_LIVE_DIR / "teacher_reviews.jsonl"
    if not status_file.exists():
        return {
            "active": False,
            "state": "NOT_STARTED",
            "stream_url": "https://www.twitch.tv/k1m6a",
            "observations_count": 0,
        }
    try:
        status = json.loads(status_file.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"active": False, "state": "READ_ERROR", "error": str(exc)}

    recent_observations: list[dict[str, Any]] = []
    if obs_file.exists():
        lines = [ln for ln in obs_file.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
        for ln in lines[-5:]:
            try:
                item = json.loads(ln)
                recent_observations.append(
                    {
                        "step_idx": item.get("step_idx"),
                        "captured_at": item.get("captured_at"),
                        "ticker": item.get("ticker"),
                        "timeframe": item.get("timeframe"),
                        "observed_price": item.get("observed_price"),
                        "student_action_guess": item.get("student_action_guess"),
                        "student_diary_ru": item.get("student_diary_ru"),
                        "question_for_teacher_ru": item.get("question_for_teacher_ru"),
                        "dhash_hex": item.get("dhash_hex"),
                    }
                )
            except Exception:
                pass

    latest_review = status.get("latest_teacher_review")
    if rev_file.exists():
        rev_lines = [ln for ln in rev_file.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
        if rev_lines:
            try:
                latest_review = json.loads(rev_lines[-1])
            except Exception:
                pass

    return {
        "active": status.get("state") == "WATCHING_LIVE",
        **status,
        "recent_observations": recent_observations,
        "latest_teacher_review": latest_review,
    }


class CreatorBuildIn(BaseModel):
    prompt: str | None = Field(default=None, max_length=8000)
    request: str | None = Field(default=None, max_length=8000)
    target_type: str | None = Field(default=None)
    kind: str | None = Field(default=None)
    model: str = Field(default="instant")
    slug: str | None = Field(default=None)
    title: str | None = Field(default=None)
    privacy: str = Field(default="local_only")
    use_local_llm: bool = Field(default=False)


class CreatorArenaIn(BaseModel):
    prompt: str | None = Field(default=None, max_length=8000)
    request: str | None = Field(default=None, max_length=8000)
    target_type: str | None = Field(default=None)
    kind: str | None = Field(default=None)
    models: list[str] | None = Field(default=None)


class CreatorPultIn(BaseModel):
    dry_run: bool = Field(default=False)


@router.get("/status")
async def creator_status() -> dict[str, Any]:
    svc = get_creator_service()
    projects = svc.list_projects()
    arena_runs = svc.list_arena_runs(limit=10)
    local_models = await svc.list_local_models()
    installed_skills = [p["slug"] for p in projects if p.get("skill_installed")]
    return {
        "available": True,
        "projects_count": len(projects),
        "installed_skills": installed_skills,
        "valid_kinds": list(VALID_KINDS),
        "kind_labels_ru": KIND_LABELS_RU,
        "slash_commands": COMMAND_MAP,
        "context_tags": list(CONTEXT_FACTS.keys()),
        "local_models": local_models,
        "recent_arena_runs": arena_runs,
        "weights_telemetry": svc.load_telemetry(),
        "k1m6a_live": read_k1m6a_live_summary(),
    }


@router.get("/projects")
async def list_creator_projects() -> dict[str, Any]:
    return {"projects": get_creator_service().list_projects()}


@router.post("/projects")
async def build_creator_project(body: CreatorBuildIn) -> dict[str, Any]:
    req_text = (body.request or body.prompt or "").strip()
    kind_val = body.kind or body.target_type or "auto"
    model_val = body.model
    if body.use_local_llm and model_val in ("", "instant"):
        model_val = "auto"
    try:
        meta = await get_creator_service().create_project(
            {
                "request": req_text,
                "kind": kind_val,
                "model": model_val,
                "slug": body.slug,
                "title": body.title,
                "privacy": body.privacy,
            }
        )
        ver = meta["versions"][-1]
        return {"ok": True, "project": meta, "version": ver}
    except CreatorError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from None


@router.get("/projects/{slug}")
async def get_creator_project(slug: str) -> dict[str, Any]:
    svc = get_creator_service()
    try:
        meta = svc.get_project(slug)
        cur_v = int(meta.get("current") or 1)
        files = svc.read_version_files(slug, cur_v)
        return {**meta, "current_files": files}
    except CreatorError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from None


@router.get("/projects/{slug}/versions/{version_num}/preview")
async def preview_creator_version(slug: str, version_num: int) -> HTMLResponse:
    try:
        html, csp = get_creator_service().preview_html(slug, version_num)
    except CreatorError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from None
    return HTMLResponse(
        content=html,
        headers={
            "Content-Security-Policy": csp,
            "X-Frame-Options": "SAMEORIGIN",
            "Cache-Control": "no-store",
        },
    )


@router.post("/projects/{slug}/install-skill")
async def install_creator_skill(slug: str) -> dict[str, Any]:
    svc = get_creator_service()
    try:
        if _SVC_REF is not None:
            res = await svc.install_skill(slug, _SVC_REF)
            return {"ok": True, **res}
        # Fallback if _SVC_REF is not wired (unit tests)
        meta = svc.get_project(slug)
        v = int(meta.get("current") or 1)
        files = svc.read_version_files(slug, v)
        skill_dir = svc.root / "installed_skills" / slug
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_md_path = skill_dir / "SKILL.md"
        skill_md_path.write_text(files.get("SKILL.md", f"# {meta['title']}\n"), encoding="utf-8")
        return {"ok": True, "installed": True, "skill_md": str(skill_md_path), "project": meta}
    except CreatorError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from None


@router.post("/projects/{slug}/send-to-pult")
async def send_creator_to_pult(slug: str, body: CreatorPultIn | None = None) -> dict[str, Any]:
    dry = bool(body.dry_run) if body else False
    try:
        res = await get_creator_service().send_to_pult(slug, dry_run=dry)
        return {"ok": bool(res.get("sent") or res.get("dry_run")), **res}
    except CreatorError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from None


@router.post("/arena")
async def run_creator_arena(body: CreatorArenaIn) -> dict[str, Any]:
    req_text = (body.request or body.prompt or "").strip()
    kind_val = body.kind or body.target_type
    try:
        return await get_creator_service().run_arena(
            {
                "request": req_text,
                "kind": kind_val,
                "models": body.models,
            }
        )
    except CreatorError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from None


@router.get("/telemetry")
async def get_creator_telemetry() -> dict[str, Any]:
    return get_creator_service().load_telemetry()


@router.post("/telemetry")
async def save_creator_telemetry(payload: dict[str, Any]) -> dict[str, Any]:
    return get_creator_service().save_telemetry(payload)


@router.get("/k1m6a-live")
async def get_k1m6a_live() -> dict[str, Any]:
    return read_k1m6a_live_summary()


async def _tool_creator_build(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    prompt = str(args.get("prompt") or args.get("request") or "").strip()
    if not prompt:
        return ToolResult(content="prompt is required", one_line="prompt is required", error=True)
    meta = await get_creator_service().create_project(
        {
            "request": prompt,
            "kind": args.get("target_type") or args.get("kind") or "auto",
            "model": str(args.get("model") or "instant"),
            "slug": args.get("slug"),
        }
    )
    ver = meta["versions"][-1]
    data = {"ok": True, "project": meta, "version": ver}
    return ToolResult(
        content=json.dumps(data, ensure_ascii=False),
        one_line=f"Creator Mode: {meta['title']} v{ver['n']} ({ver['score']['total_score']}/100)",
        data=data,
    )


async def _tool_creator_arena(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    prompt = str(args.get("prompt") or args.get("request") or "").strip()
    if not prompt:
        return ToolResult(content="prompt is required", one_line="prompt is required", error=True)
    report = await get_creator_service().run_arena(
        {
            "request": prompt,
            "kind": args.get("target_type") or args.get("kind"),
            "models": args.get("models"),
        }
    )
    return ToolResult(
        content=json.dumps(report, ensure_ascii=False),
        one_line=f"Creator Arena winner: {report.get('winner')}",
        data=report,
    )


async def _setup(svc: Any) -> None:
    get_creator_service(svc)
    REGISTRY.register(
        ToolSpec(
            name="creator.build",
            description="Creator Mode: собрать плагин, дизайн-консоль, мини-игру или 3D LLM визуализатор по описанию на обычном языке.",
            handler=_tool_creator_build,
            input_schema={
                "prompt": {"type": "string"},
                "target_type": {
                    "type": "string",
                    "enum": ["plugin", "design_console", "game", "visualizer_3d"],
                },
                "model": {"type": "string"},
                "slug": {"type": "string"},
            },
            required=["prompt"],
            category="write",
            default_effect="auto",
        )
    )
    REGISTRY.register(
        ToolSpec(
            name="creator.arena",
            description="Creator Mode Multi-Agent Arena: протестировать сборку на нескольких локальных моделях и выбрать победителя.",
            handler=_tool_creator_arena,
            input_schema={
                "prompt": {"type": "string"},
                "target_type": {"type": "string"},
                "models": {"type": "array", "items": {"type": "string"}},
            },
            required=["prompt"],
            category="read",
            default_effect="auto",
        )
    )


FEATURE = Feature(
    name="creator_mode",
    router=router,
    setup=_setup,
)
