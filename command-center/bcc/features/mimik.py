"""Mimik guide skill: local text transformation through the canonical tool loop."""
from __future__ import annotations

import json

from fastapi import APIRouter

from ..mimik_skill import GuideInputError, MAX_MARKDOWN, UPSTREAM_SHA, prepare_guide
from ..tools import REGISTRY, ToolContext, ToolResult, ToolSpec
from . import Feature

router = APIRouter()


async def _supplied_scope(args: dict, ctx: ToolContext) -> str | None:
    meta = ctx.task.get("meta") if isinstance(ctx.task, dict) else None
    if isinstance(meta, dict) and meta.get("skill") == "mimik":
        inputs = meta.get("skill_input")
        if type(inputs) is not dict or inputs != args:
            return "mimik_input_differs_from_supplied_guide"
    return None


async def _guide(args: dict, ctx: ToolContext) -> ToolResult:
    try:
        data = prepare_guide(args)
    except GuideInputError as exc:
        return ToolResult(content=str(exc), error=True, one_line="mimik: invalid guide")
    return ToolResult(content=json.dumps(data, ensure_ascii=False), data=data,
                      one_line=f"mimik: {data['status']} ({data['action_count']} actions, NOT_RUN)",
                      external=True)


@router.get("/mimik/status")
async def status():
    return {"skill_id": "mimik", "upstream_sha": UPSTREAM_SHA,
            "integration": "text_export_adapter", "tools": ["mimik.guide"],
            "offline_processing": True, "background_running": False,
            "recorder_installed_by_bossman": False, "live_capture_verified": False,
            "not_integrated": ["extension_install", "capture", "screenshots", "guide_me_replay",
                               "voice", "cloud_ai", "pdf_docx_video_export"]}


async def setup(svc) -> None:
    REGISTRY.register(ToolSpec(
        name="mimik.guide", description="Локально разобрать переданный Markdown-экспорт или Snapshot Mimik и подготовить текстовый чек-лист. Не записывает экран и не выполняет шаги.",
        handler=_guide, category="read", source="builtin", default_effect="auto",
        timeout_seconds=10, external_output=True, context_deny=_supplied_scope,
        input_schema={
            "markdown": {"type": "string", "maxLength": MAX_MARKDOWN,
                         "description": "Mimik Markdown export; supply this OR snapshot, not both."},
            "snapshot": {"type": "object", "required": ["title", "stepIds", "steps"],
                         "description": "Pinned upstream Snapshot shape; supply this OR markdown."},
        }))


FEATURE = Feature(name="mimik", router=router, setup=setup)
