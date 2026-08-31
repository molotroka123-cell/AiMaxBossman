"""file.parse / artifact.create — инструменты модулей J и M (V2.6).

file.parse — typed-разбор файла внутри workdir через file_intel (модуль J):
таблицы остаются таблицами, provenance-ссылки сохраняются. artifact.create —
создание deliverable в поддерживаемом формате + регистрация в реестре
артефактов (модуль M); недоступность Postgres НЕ роняет инструмент — файл
уже записан, регистрация превращается в честное предупреждение.

Containment путей — РОВНО тот же, что у fs.* (files.py): resolve под
ctx.workdir, любой выход (включая symlink) — PermissionError.

ВАЖНО: модуль сознательно НЕ импортируется из toolkit/__init__.py —
подключение делает оркестратор.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import ToolContext, ToolDef, ToolResult, clip, register
from .. import artifacts_engine, file_intel


def _resolve(ctx: ToolContext, rel: str) -> Path:
    """Та же дисциплина, что files._resolve: за workdir не выходим."""
    p = (ctx.workdir / rel).resolve()
    if not str(p).startswith(str(ctx.workdir.resolve())):
        raise PermissionError(f"путь вне рабочей папки: {rel}")
    return p


async def file_parse(args: dict, ctx: ToolContext) -> ToolResult:
    path = _resolve(ctx, args["path"])
    if not path.exists():
        return ToolResult(f"нет файла: {args['path']}",
                          one_line=f"нет файла {args['path']}", error=True)
    try:
        art = file_intel.parse_file(path)
    except file_intel.ParseUnavailable as exc:
        # честный unavailable: сообщение — контент, а не трейс
        return ToolResult(str(exc), one_line="file.parse: парсер недоступен",
                          error=True)
    except ValueError as exc:                 # напр. файл больше лимита
        return ToolResult(str(exc), one_line=f"file.parse: {exc}", error=True)
    body, cut = clip(file_intel.render_compact(art), 4000)
    return ToolResult(body,
                      one_line=f"file.parse: {art.kind}, {len(art.sections)} секций",
                      truncated=cut,
                      more=f"fs.read(path='{args['path']}')" if cut else "")


def _payload(fmt: str, content) -> object:
    """Аргумент content для build_content: json-строку разворачиваем в объект,
    csv/zip приходят уже структурой (список строк / dict)."""
    if fmt == "json" and isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return content                    # честно сериализуем как строку
    return content


async def artifact_create(args: dict, ctx: ToolContext) -> ToolResult:
    fmt = str(args["format"]).lower().lstrip(".")
    target = _resolve(ctx, args["path"])
    try:
        data = artifacts_engine.build_content(fmt, _payload(fmt, args["content"]))
    except ValueError as exc:
        return ToolResult(str(exc), one_line=f"artifact.create: {exc}", error=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)

    lines = [f"артефакт записан: {args['path']} ({len(data)} байт, формат {fmt})"]
    one = f"artifact.create: {args['path']} ({fmt})"
    try:
        rec = await artifacts_engine.register_artifact(
            type=fmt, path=str(target), content=data,
            creator_task=str(ctx.run_id) if ctx.run_id is not None else None)
        lines.append(f"artifact_id={rec.artifact_id} version={rec.version} "
                     f"sha256={rec.content_hash[:12]}")
        one += f" → {rec.artifact_id}"
    except Exception as exc:  # noqa: BLE001 — файл уже на диске, реестр не критичен
        lines.append(f"предупреждение: артефакт не зарегистрирован в реестре "
                     f"({type(exc).__name__}); файл записан")
    return ToolResult("\n".join(lines), one_line=one)


register(ToolDef(
    "file.parse",
    "Typed-разбор файла (pdf/docx/xlsx/csv/pptx/md/json/zip/png…): секции с "
    "provenance-ссылками, таблицы — таблицами.",
    "read", file_parse,
    params={"path": {"type": "string"}}, required=["path"],
    confirm_default=False, token_limit=4000))

register(ToolDef(
    "artifact.create",
    "Создать файл-результат (md/txt/json/csv/zip) и зарегистрировать его в "
    "реестре артефактов (id, версия, hash). csv: content — список строк; "
    "zip: dict {имя: контент}.",
    "write", artifact_create,
    params={"path": {"type": "string"},
            "format": {"type": "string", "enum": sorted(artifacts_engine.SUPPORTED_CREATE_FORMATS)},
            "content": {"description": "str (md/txt/json), список строк (csv) или dict (zip)"}},
    required=["path", "format", "content"],
    confirm_default=False))
