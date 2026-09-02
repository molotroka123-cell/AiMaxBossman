"""Медиа: картинка/клип возвращаются как путь + метаданные + подпись (≤300 токенов),
никогда содержимым. ffmpeg — склейка и звук из пайплайна проектов."""
from __future__ import annotations

import asyncio
import json
import re

from . import ToolContext, ToolDef, ToolResult, clip, register


def _path_arg_ok(a: str) -> bool:
    """Аргумент-путь — только относительный и только внутрь workdir:
    отказ абсолютным (/, \\, диск C:, UNC \\\\server), «..» как компонент в
    ЛЮБОЙ записи слешей (sub\\..\\..\\x ловится так же, как sub/../../x).
    Не-путевые аргументы (фильтры, опции) без слешей не проверяются."""

    if not a:
        return True
    if "\x00" in a:
        return False
    if a[0] in "/\\":
        return False
    if re.match(r"^[A-Za-z]:", a):
        return False
    # «..» как компонент — и с разделителями, и голый (`..` резолвится в родителя
    # workdir; sibling sweep F8.4 нашёл этот пропуск после F-003).
    return ".." not in re.split(r"[/\\]+", a)


async def _run(argv: list[str], timeout: int = 900, cwd=None) -> tuple[int, str]:
    # argv-only, без шелла: аргументы из плана агента не интерпретируются.
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=cwd,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    return proc.returncode or 0, out.decode(errors="replace")


async def probe(args: dict, ctx: ToolContext) -> ToolResult:
    """Метаданные файла через ffprobe: размер, длительность, разрешение."""
    # Тот же барьер путей, что у ffmpeg: без него probe читал абсолютные и «..»
    # пути наружу (Fable5.1 red-team F-003) — оракул существования/метаданных
    # файлов вне рабочей папки. Только внутри workdir.
    if not _path_arg_ok(str(args["path"])):
        return ToolResult("абсолютные пути и «..» запрещены — только внутри рабочей папки",
                          one_line="probe: отказ по пути", error=True)
    path = (ctx.workdir / args["path"]).resolve()
    code, out = await _run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", str(path)])
    if code != 0:
        return ToolResult(f"ffprobe не смог прочитать {args['path']}", error=True,
                          one_line=f"probe {args['path']}: ошибка")
    info = json.loads(out)
    fmt = info.get("format", {})
    streams = [{"codec": s.get("codec_name"), "type": s.get("codec_type"),
                "w": s.get("width"), "h": s.get("height")} for s in info.get("streams", [])]
    body = json.dumps({"path": args["path"], "duration_s": fmt.get("duration"),
                       "size_b": fmt.get("size"), "streams": streams},
                      ensure_ascii=False, separators=(",", ":"))
    body, _ = clip(body, 300)
    return ToolResult(body, one_line=f"probe {args['path']}: {fmt.get('duration', '?')}с")


async def ffmpeg(args: dict, ctx: ToolContext) -> ToolResult:
    """Прямой вызов ffmpeg с аргументами из плана (склейка, crossfade, LUFS, 9:16)."""
    argv = [str(a) for a in args["args"]]
    # Пути только внутри рабочей папки: абсолютные, диски, UNC и «..» в любой
    # записи слешей — отказ (argv-only остаётся, но пути тоже содержатся).
    if any(not _path_arg_ok(a) for a in argv):
        return ToolResult("абсолютные пути и «..» запрещены — только внутри рабочей папки",
                          one_line="ffmpeg: отказ по пути", error=True)
    code, out = await _run(["ffmpeg", "-y", "-hide_banner", "-v", "error", *argv],
                           timeout=int(args.get("timeout", 1800)), cwd=str(ctx.workdir))
    body, cut = clip(out or "готово", 1000)
    return ToolResult(f"код выхода: {code}\n{body}",
                      one_line=f"ffmpeg → код {code}", truncated=cut, error=code != 0)


async def vision_describe(args: dict, ctx: ToolContext) -> ToolResult:
    """Подпись к кадру/клипу от модели со зрением. Всегда воркер: один клип — один вызов.
    Вызов идёт через петлю (bossman-writer локально / gemini_qa через ask)."""
    from ..llm import vision_caption  # поздний импорт: разрыв цикла toolkit ↔ llm
    caption = await vision_caption(ctx.agent, args["path"], args.get("question", "Что на изображении?"))
    body, _ = clip(caption, 300)
    return ToolResult(body, one_line=f"vision {args['path']}: подпись получена")


register(ToolDef("media.probe", "Метаданные медиа-файла (длительность, разрешение) — без содержимого.",
                 "read", probe, params={"path": {"type": "string"}}, required=["path"], token_limit=300))
register(ToolDef("ffmpeg", "ffmpeg внутри рабочей папки: склейка, переходы, звук, формат.",
                 "exec", ffmpeg,
                 params={"args": {"type": "array", "items": {"type": "string"}},
                         "timeout": {"type": "integer"}},
                 required=["args"], token_limit=1000))
register(ToolDef("vision.describe", "Подпись и ответ на вопрос по кадру/клипу (модель со зрением).",
                 "read", vision_describe,
                 params={"path": {"type": "string"}, "question": {"type": "string"}},
                 required=["path"], token_limit=300))
