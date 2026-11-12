"""``filesystem.read_any`` - READ-ONLY access to the whole PC's files for the PC operator and explicitly granted
local-model agents (owner 11.11.2026). Policy: :mod:`bcc.features.fs_read_policy` (DENY always wins, output redacted).

Grant = permission ``filesystem.read_any`` in ``agents.permissions`` AND the tool name in ``agents.tools``;
``PUT /api/operator {read_everything: bool}`` toggles both for the operator and every existing local-model agent.
Without the grant the handler refuses (FS_NOT_GRANTED) even if an approval was clicked. No write/delete/exec here.
"""
from __future__ import annotations

import asyncio
import fnmatch
import os
import time
from pathlib import Path

from ..tools import REGISTRY, ToolContext, ToolResult, ToolSpec
from . import Feature
from .fs_read_policy import (MAX_LIST_ENTRIES, MAX_READ_BYTES, check_path, decode_text, looks_binary, redact)

PERM = "filesystem.read_any"
TOOL = "filesystem.read_any"


def _err(code: str, why: str) -> ToolResult:
    return ToolResult(content=f"{code}: {why}", one_line=f"filesystem.read_any: {code}", error=True,
                      data={"code": code})


def _extra_roots(ctx: ToolContext) -> tuple[str, ...]:
    out: list[str] = []
    for attr in ("config", "cfg", "settings"):
        d = getattr(getattr(ctx.svc, attr, None), "data_dir", None)
        if d:
            out.append(str(d))
    return tuple(out)


def _list(real: str, extra: tuple[str, ...]) -> ToolResult:
    entries, hidden = [], 0
    try:
        with os.scandir(real) as it:
            for e in it:
                if not check_path(e.path, extra_deny_roots=extra).ok:
                    hidden += 1
                    continue
                try:
                    st = e.stat(follow_symlinks=False)
                    kind = "dir" if e.is_dir(follow_symlinks=False) else "file"
                    entries.append(f"{kind}\t{st.st_size if kind == 'file' else '-'}\t{e.name}")
                except OSError:
                    entries.append(f"?\t-\t{e.name}")
                if len(entries) >= MAX_LIST_ENTRIES:
                    break
    except OSError as exc:
        return _err("FS_IO", f"{type(exc).__name__}")
    entries.sort(key=lambda x: (x.split("\t")[0] != "dir", x.split("\t")[2].lower()))
    text, _ = redact("\n".join(entries))
    trunc = len(entries) >= MAX_LIST_ENTRIES
    return ToolResult(content=f"{real}\n{text}", one_line=f"list {len(entries)} (+{hidden} скрыто)",
                      truncated=trunc, more="уточните подпапку" if trunc else "",
                      data={"entries": len(entries), "hidden_by_policy": hidden})


def _find(real: str, pattern: str, extra: tuple[str, ...]) -> ToolResult:
    deadline, hits, pat = time.monotonic() + 8.0, [], pattern.lower()
    base_depth = real.rstrip("\\/").count(os.sep)
    for root, dirs, files in os.walk(real, followlinks=False):
        if time.monotonic() > deadline or len(hits) >= 100:
            break
        dirs[:] = [d for d in dirs if root.count(os.sep) - base_depth < 4
                   and check_path(os.path.join(root, d), extra_deny_roots=extra).ok]
        for f in files:
            if fnmatch.fnmatch(f.lower(), pat) and check_path(os.path.join(root, f), extra_deny_roots=extra).ok:
                hits.append(os.path.join(root, f))
                if len(hits) >= 100:
                    break
    return ToolResult(content="\n".join(hits) or "ничего не найдено", one_line=f"find {len(hits)}",
                      data={"hits": len(hits)})


def _read(real: str, offset: int, length: int) -> ToolResult:
    try:
        size = os.stat(real).st_size
        with open(real, "rb") as fh:
            head = fh.read(8192)
            if looks_binary(head):
                return _err("FS_BINARY", "бинарные файлы не читаем (только текст)")
            fh.seek(max(0, offset))
            data = fh.read(length)
    except OSError as exc:
        return _err("FS_IO", f"{type(exc).__name__}")
    text, masks = redact(decode_text(data))
    end = max(0, offset) + len(data)
    trunc = end < size
    return ToolResult(content=text, one_line=f"read {len(data)}B" + (f", masked {masks}" if masks else ""),
                      truncated=trunc, more=f"offset={end}" if trunc else "",
                      data={"size": size, "bytes": len(data), "redactions": masks}, external=True)


async def _t_read_any(args: dict, ctx: ToolContext) -> ToolResult:
    from ..permissions import agent_allowed
    if not agent_allowed(ctx.agent or {}, PERM):
        return _err("FS_NOT_GRANTED", "у этого агента нет права filesystem.read_any")
    action = str(args.get("action") or "auto").lower()
    extra = _extra_roots(ctx)
    v = check_path(str(args.get("path") or ""), extra_deny_roots=extra)
    if not v.ok:
        return _err(v.code, v.why)
    real = v.path
    try:
        isdir, isfile = os.path.isdir(real), os.path.isfile(real)
    except OSError:
        return _err("FS_IO", "недоступно")
    if not (isdir or isfile):
        return _err("FS_NOT_FOUND", "нет такого пути")
    if action in ("auto", "list") and isdir:
        return await asyncio.to_thread(_list, real, extra)
    if action == "find":
        if not isdir:
            return _err("FS_BAD_PATH", "find: нужна папка")
        return await asyncio.to_thread(_find, real, str(args.get("pattern") or "*"), extra)
    if not isfile:
        return _err("FS_BAD_PATH", "это папка: используйте action=list")
    try:
        off = max(0, int(args.get("offset") or 0))
        ln = min(MAX_READ_BYTES, max(1, int(args.get("length") or MAX_READ_BYTES)))
    except (TypeError, ValueError):
        return _err("FS_BAD_PATH", "offset/length должны быть числами")
    return await asyncio.to_thread(_read, real, off, ln)


SPECS = [ToolSpec(
    name=TOOL,
    description=("READ-ONLY чтение файлов и папок ВСЕГО ПК (C:, D:, E:, Рабочий стол, проекты, системные файлы, логи). "
                 "action: read (текст до 256 КБ, offset/length), list (папка), find (pattern, глубина 4). Чаты, "
                 "браузеры, ключи/токены/vault, фото и видео закрыты и не обходятся; секреты в выводе маскируются."),
    handler=_t_read_any,
    input_schema={"path": {"type": "string", "description": "абсолютный путь, например C:\\Windows\\System32\\drivers\\etc\\hosts"},
                  "action": {"type": "string", "enum": ["auto", "read", "list", "find"]},
                  "pattern": {"type": "string", "description": "для find, например *.log"},
                  "offset": {"type": "integer"}, "length": {"type": "integer"}},
    required=["path"], category="read", permission=PERM, source="builtin", default_effect="auto",
    timeout_seconds=30.0, external_output=True)]


async def _setup(svc) -> None:
    for spec in SPECS:
        REGISTRY.register(spec)


FEATURE = Feature(name="tools_fs_read", router=None, setup=_setup)
