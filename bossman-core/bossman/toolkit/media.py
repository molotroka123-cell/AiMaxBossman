"""Медиа: картинка/клип возвращаются как путь + метаданные + подпись (≤300 токенов),
никогда содержимым. ffmpeg — склейка и звук из пайплайна проектов."""
from __future__ import annotations

import asyncio
import json
import shlex

from . import ToolContext, ToolDef, ToolResult, clip, register


async def _sh(cmd: str, timeout: int = 900) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_shell(
        cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    return proc.returncode or 0, out.decode(errors="replace")


async def probe(args: dict, ctx: ToolContext) -> ToolResult:
    """Метаданные файла через ffprobe: размер, длительность, разрешение."""
    path = (ctx.workdir / args["path"]).resolve()
    code, out = await _sh(
        f"ffprobe -v quiet -print_format json -show_format -show_streams {shlex.quote(str(path))}")
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
    argv = args["args"]
    if any(a.startswith(("/", "..", "-i/")) for a in argv if isinstance(a, str) and "/" in a and a.startswith("/")):
        return ToolResult("абсолютные пути запрещены — только внутри рабочей папки",
                          one_line="ffmpeg: отказ по пути", error=True)
    cmd = "cd " + shlex.quote(str(ctx.workdir)) + " && ffmpeg -y -hide_banner -v error " + \
          " ".join(shlex.quote(a) for a in argv)
    code, out = await _sh(cmd, timeout=int(args.get("timeout", 1800)))
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
