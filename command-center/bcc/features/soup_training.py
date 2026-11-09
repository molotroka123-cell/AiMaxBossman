"""Soup in the SAME Bossman: optional read-only status and scoped planning tools.

Actual process execution stays in terminal.run with its approvals, sessions and STOP.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException

from .. import soup_training as adapter
from ..tools import REGISTRY, ToolResult, ToolSpec
from ..v2 import scratch
from . import Feature

router = APIRouter(prefix="/training/soup", tags=["training"])


@router.get("/status")
async def status(x_participant: str | None = Header(default=None)):
    if x_participant not in (None, "owner"):
        raise HTTPException(404, "not found")
    return await asyncio.to_thread(adapter.status)


async def _status(args, ctx):
    data = await asyncio.to_thread(adapter.status)
    return ToolResult(content=json.dumps(data, ensure_ascii=False), one_line="Soup: " + data["status"], data=data)


async def _prepare(args, ctx):
    from .tools_terminal import _roots
    own = scratch.ensure(scratch.for_context(ctx))
    roots = [*await _roots(ctx.svc), own]
    try:
        for name in ("base", "train", "holdout"):
            target = Path(str(args.get(name, ""))).expanduser().resolve()
            blocked = scratch.check(ctx, target)
            if blocked:
                raise ValueError(blocked)
        output_root = (own / "soup").resolve()
        if not output_root.is_relative_to(own.resolve()):
            raise ValueError("Output must remain inside own task scratch.")
        data = await asyncio.to_thread(adapter.prepare, base=str(args["base"]), train=str(args["train"]),
                                       holdout=str(args["holdout"]), roots=roots, output_root=output_root)
        return ToolResult(content=json.dumps(data, ensure_ascii=False), one_line="Soup: PREPARED_NOT_TRAINED", data=data)
    except (ValueError, OSError, KeyError, TypeError):
        # Never return a parser exception that could quote private source content.
        return ToolResult(content="Preparation rejected: check local model, allowed roots, bounded chat JSONL and source-disjoint holdout.",
                          one_line="Soup: preparation rejected", error=True)


async def setup(svc):
    REGISTRY.register(ToolSpec(name="training.soup.status", description="Offline Soup version/dependency preflight; no weights are loaded.",
                              handler=_status, category="read", default_effect="auto", source="builtin"))
    REGISTRY.register(ToolSpec(name="training.soup.prepare", description="Prepare local LLM planner SFT for Genjutsu/Jeff; not video diffusion training. Writes only own task scratch; does not train or promote weights.",
                              handler=_prepare, input_schema={name: {"type": "string"} for name in ("base", "train", "holdout")},
                              required=["base", "train", "holdout"], category="write", default_effect="ask", source="builtin",
                              idempotent=False, timeout_seconds=30))


FEATURE = Feature(name="soup_training", router=router, setup=setup)
