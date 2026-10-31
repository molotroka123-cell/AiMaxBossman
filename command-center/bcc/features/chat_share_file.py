"""`chat.share_file` — the agent hands the owner a file in the chat (owner 31.10: «как у тебя и чата гпт … присылать
файлы и возможность скачать»).

The file is hard-linked/copied into the chat attachments and listed under the agent's turn; the owner downloads it
from the mobile pult or the window (`GET /api/chat/attachments/{id}/file`). Nothing leaves the PC: the recipient is the
owner's own authenticated session, and the model never sees the bytes. Secrets and Bossman's own data (vault, bcc.db,
tokens, keys, .ssh …) are refused before and at execution.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import HTTPException

from ..tools import REGISTRY, ToolContext, ToolResult, ToolSpec
from . import Feature
from .pc_agent_policy_paths import is_t3_secret_or_protected_path

MOBILE_DATA = Path(os.environ.get("BOSSMAN_MOBILE_DATA") or Path.home() / ".bossman-mobile")


def refusal(raw: str, data_dir: Path) -> str | None:
    """Why this path may not be shared, or None."""
    text = str(raw or "").strip().strip('"')
    if not text:
        return "не указан путь к файлу"
    path = Path(os.path.expanduser(text))
    if not path.is_absolute():
        return "нужен полный путь к файлу (например C:\\Users\\asd\\Desktop\\report.pdf)"
    secret, why = is_t3_secret_or_protected_path(text, data_dir=data_dir)
    if secret:
        return f"этот файл нельзя отдать: {why}"
    try:
        real = path.resolve(strict=True)
    except OSError:
        return f"файл не найден: {text}"
    for own in (Path(data_dir).resolve(), MOBILE_DATA.resolve()):
        if real == own or own in real.parents:
            return "файлы данных Bossman (база, ключи, токены, сессии) отдавать нельзя"
    if not real.is_file():
        return f"это не файл: {text}"
    return None


async def _context_deny(args: dict, ctx: ToolContext) -> str | None:
    return refusal(str(args.get("path") or ""), Path(ctx.svc.settings.data_dir))


async def _share(args: dict, ctx: ToolContext) -> ToolResult:
    data_dir = Path(ctx.svc.settings.data_dir)
    why = refusal(str(args.get("path") or ""), data_dir)
    if why:
        return ToolResult(content=f"REFUSED: {why}", error=True, one_line="chat.share_file: refused")
    from .chat_threads import share_file_into_chat
    src = Path(os.path.expanduser(str(args["path"]).strip().strip('"'))).resolve()
    try:
        pub = await asyncio.to_thread(share_file_into_chat, ctx.svc, src, task_id=int(ctx.task.get("id") or 0),
                                      note=str(args.get("note") or ""))
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        return ToolResult(content=f"ERROR: {detail.get('message')}", error=True, one_line="chat.share_file: failed")
    except OSError as exc:
        return ToolResult(content=f"ERROR: файл не прочитан: {exc}", error=True, one_line="chat.share_file: failed")
    size_mb = (pub.get("size") or 0) / 1048576
    return ToolResult(content=f"Файл «{pub['name']}» ({size_mb:.1f} МБ) прикреплён к ответу — владелец может его скачать.",
                      one_line=f"chat.share_file: {pub['name']}", data={"attachment": pub})


async def setup(svc) -> None:
    REGISTRY.register(ToolSpec(
        name="chat.share_file",
        description="Отдать владельцу файл в чат (он скачает его с телефона или в окне): отчёт, таблицу, картинку, видео, "
                    "архив — любой размер. Укажи полный путь к готовому файлу на ПК. Секреты и данные Bossman отдавать нельзя.",
        handler=_share, category="read", source="builtin", default_effect="auto", timeout_seconds=900, idempotent=False,
        context_deny=_context_deny,
        input_schema={"path": {"type": "string", "maxLength": 1000, "description": "Полный путь к файлу"},
                      "note": {"type": "string", "maxLength": 300, "description": "Что это за файл (необязательно)"}},
        required=["path"]))


FEATURE = Feature(name="chat_share_file", setup=setup)
