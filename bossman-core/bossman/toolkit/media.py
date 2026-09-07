"""Медиа: картинка/клип возвращаются как путь + метаданные + подпись (≤300 токенов),
никогда содержимым. ffmpeg — склейка и звук из пайплайна проектов."""
from __future__ import annotations

import asyncio
import json
import re

from . import ToolContext, ToolDef, ToolResult, clip, register
from .files import _contains, _resolve

# Кадр или короткий клип. Больше — не подпись, а выгрузка.
VISION_MAX_BYTES = 32 * 1024 * 1024


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


# FFmpeg понимает ПРОТОКОЛЫ, а pathlib — нет. `http://127.0.0.1/x.mp4` Python
# разрешает в `<workdir>/http:/127.0.0.1/x.mp4` (внутри рабочей папки!), а FFmpeg
# читает ту же строку как сетевой URL. Поэтому containment по путям на такие
# операнды не действует вовсе: их надо отклонять ДО запуска, а не «проверять».
#
# Это ЛОКАЛЬНЫЙ инструмент рабочей папки. Сеть, устройства, каналы и внутренние
# псевдо-протоколы ему не нужны, поэтому список разрешённого пуст: любой операнд
# со схемой отвергается. Если когда-нибудь понадобится протокол — он добавляется
# сюда явным решением, а не пролезает потому, что регулярка не заметила.
_ALLOWED_MEDIA_PROTOCOLS: frozenset[str] = frozenset()

# `схема:` в начале операнда. Один символ — это диск Windows (C:\...), он ловится
# отдельно в `_path_arg_ok`; схема FFmpeg — два и более.
# Схема может нести СВОИ опции до двоеточия — `subfile,,start,0,end,10,:/файл`
# именно так и выглядит, и без этой ветки она проезжала. Фильтры вида
# `scale=1280:720`, `[0:v][1:v]concat` и `1280:720` схемой НЕ являются: после
# имени там `=`/`[`/цифра, а не `,` или `:`.
_PROTOCOL = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]{1,})(?:,[^:]*)?\s*:")


def _protocol_of(a: str) -> str:
    """Схема операнда, если она есть: 'http', 'file', 'concat', 'pipe', 'data'…"""
    m = _PROTOCOL.match(a or "")
    return m.group(1).lower() if m else ""


def _looks_like_path(a: str) -> bool:
    """Операнд, который может оказаться объектом файловой системы.

    Расширение больше НЕ является условием: `input` без расширения — такой же
    путь, и раньше он не проверялся вовсе. Теперь любой не-опционный операнд,
    который существует на диске, обязан пройти проверку содержания, даже если он
    выглядит как слово.
    """
    return bool(a) and not a.startswith("-")


def _path_contained(ctx: ToolContext, rel: str) -> bool:
    """Существующий путь проверяется по своей цели, будущий — по родителю."""
    root = ctx.workdir.resolve()
    raw = ctx.workdir / rel
    try:
        if raw.exists() or raw.is_symlink():
            return _contains(root, raw.resolve())
        parent = raw.parent.resolve()
        return _contains(root, parent) and not raw.is_symlink()
    except (OSError, RuntimeError):
        return False


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
    # RT-03: лексическая проверка ловит «..» и абсолютные пути, но НЕ symlink.
    # `leak.mp4 -> /etc/secret` не содержит «..», не абсолютен и проходил её —
    # а разрешённая цель уже вне workdir, и именно её получал ffprobe. Реальное
    # содержание проверяется тем же резолвером, что и fs.*: сравниваются
    # .resolve()-нутые пути, поэтому и symlink, и junction ловятся по цели.
    if _protocol_of(str(args["path"])):
        return ToolResult("протокол запрещён — только файлы внутри рабочей папки",
                          one_line="probe: отказ по протоколу", error=True)
    if not _path_arg_ok(str(args["path"])):
        return ToolResult("абсолютные пути и «..» запрещены — только внутри рабочей папки",
                          one_line="probe: отказ по пути", error=True)
    try:
        path = _resolve(ctx, str(args["path"]))
    except PermissionError:
        return ToolResult("путь ведёт за пределы рабочей папки (symlink/junction)",
                          one_line="probe: отказ по содержанию", error=True)
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
    # RT-03, вторая половина: лексики мало и здесь. Каждый аргумент, который
    # похож на путь, обязан РЕАЛЬНО оставаться внутри workdir — и существующий
    # вход по своей цели, и ещё не созданный выход по своему родителю. Иначе
    # `out.mp4 -> /outside/x.mp4` писал наружу, не содержа ни «..», ни абсолюта.
    for a in argv:
        if not _looks_like_path(a):
            continue
        proto = _protocol_of(a)
        if proto and proto not in _ALLOWED_MEDIA_PROTOCOLS:
            return ToolResult(
                f"протокол '{proto}:' запрещён — инструмент работает только с файлами "
                "внутри рабочей папки",
                one_line="ffmpeg: отказ по протоколу", error=True)
        # Существующий объект файловой системы проверяется всегда, даже без
        # расширения; несуществующее имя без разделителей — это опция/фильтр
        # (`copy`, `veryfast`, `30`), и путём не является.
        raw = ctx.workdir / a
        try:
            is_fs_object = raw.exists() or raw.is_symlink()
        except (OSError, ValueError):
            is_fs_object = False
        if not is_fs_object and "/" not in a and "\\" not in a:
            continue
        if not _path_contained(ctx, a):
            return ToolResult("путь ведёт за пределы рабочей папки (symlink/junction)",
                              one_line="ffmpeg: отказ по содержанию", error=True)
    code, out = await _run(["ffmpeg", "-y", "-hide_banner", "-v", "error", *argv],
                           timeout=int(args.get("timeout", 1800)), cwd=str(ctx.workdir))
    body, cut = clip(out or "готово", 1000)
    return ToolResult(f"код выхода: {code}\n{body}",
                      one_line=f"ffmpeg → код {code}", truncated=cut, error=code != 0)


async def vision_describe(args: dict, ctx: ToolContext) -> ToolResult:
    """Подпись к кадру/клипу от модели со зрением. Всегда воркер: один клип — один вызов.
    Вызов идёт через петлю (bossman-writer локально / gemini_qa через ask)."""
    from ..llm import vision_caption  # поздний импорт: разрыв цикла toolkit ↔ llm
    # RT-02: раньше сюда уходил СЫРОЙ путь агента, и `vision_caption` делал
    # `Path(path).read_bytes()` — то есть любой абсолютный путь хоста (или symlink
    # изнутри workdir наружу) превращался в base64 внутри запроса к модели. Это
    # канал вывода данных, а не подпись к кадру. Проверка стоит ЗДЕСЬ, а модели
    # передаются уже проверенные байты: забыть её у другого вызывающего нельзя,
    # потому что путь до адаптера больше не доходит.
    if _protocol_of(str(args["path"])):
        return ToolResult("протокол запрещён — только файлы внутри рабочей папки",
                          one_line="vision: отказ по протоколу", error=True)
    if not _path_arg_ok(str(args["path"])):
        return ToolResult("абсолютные пути и «..» запрещены — только внутри рабочей папки",
                          one_line="vision: отказ по пути", error=True)
    try:
        path = _resolve(ctx, str(args["path"]))
    except PermissionError:
        return ToolResult("путь ведёт за пределы рабочей папки (symlink/junction)",
                          one_line="vision: отказ по содержанию", error=True)
    if not path.is_file():
        return ToolResult(f"нет файла: {args['path']}",
                          one_line=f"vision {args['path']}: нет файла", error=True)
    size = path.stat().st_size
    if size > VISION_MAX_BYTES:
        return ToolResult(f"файл больше предела зрения ({size} Б > {VISION_MAX_BYTES} Б)",
                          one_line="vision: отказ по размеру", error=True)
    data = path.read_bytes()
    caption = await vision_caption(ctx.agent, args.get("question", "Что на изображении?"),
                                   data=data, source=str(args["path"]))
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
