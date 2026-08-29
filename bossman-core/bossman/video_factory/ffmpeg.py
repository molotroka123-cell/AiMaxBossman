"""FFmpeg-слой Video Factory: локатор бинаря, сборка argv и валидация вывода.

Жёсткие инварианты безопасности (см. ТЗ Этапа 7):
- ТОЛЬКО `asyncio.create_subprocess_exec(*argv)` — НИКОГДА не shell, никакой
  строковой интерполяции текста промпта в команду. argv — список токенов, поэтому
  текст сцены физически не может «сбежать» в оболочку.
- Текст промпта в argv НЕ попадает вовсе: синтетический провайдер рисует
  детерминированный testsrc, а числовые параметры (длительность) я формирую сам
  из float, а не из пользовательской строки.
- Бинарь ищется через `shutil.which("ffmpeg")`, иначе `imageio_ffmpeg` (он
  установлен). ffprobe в imageio нет — для валидации есть ffprobe-free фолбэк
  через разбор stderr `ffmpeg -i`.
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
from pathlib import Path

from .. import errors

# Размер/частота синтетического кадра: маленький и быстрый — тестам хватает,
# а бокс не грузим. Прод-провайдеры переопределяют это в своих argv.
_SYNTH_SIZE = "320x240"
_SYNTH_RATE = "15"

_RE_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def ffmpeg_bin() -> str | None:
    """Путь к ffmpeg: сперва системный PATH, затем бинарь imageio_ffmpeg."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 — отсутствие бинаря → деградация, не падение
        return None


def ffprobe_bin() -> str | None:
    """Путь к ffprobe, если он есть в PATH (imageio его не поставляет — тогда None,
    и валидация уходит на ffmpeg-фолбэк)."""
    return shutil.which("ffprobe")


def ffmpeg_available() -> bool:
    return ffmpeg_bin() is not None


# --- дубли (takes): имя следующего артефакта без перезаписи предыдущего --------

_RE_TAKE = re.compile(r"take-(\d+)")


def next_take_path(scene_dir: Path, ext: str = ".mp4") -> Path:
    """Вернуть путь СЛЕДУЮЩЕГО дубля `take-NNN{ext}` в каталоге сцены.

    Номер = (максимум существующих) + 1, минимум 1. Это гарантирует, что
    `take-001` НИКОГДА не перезаписывается ретраем: повтор получит `take-002`.
    """
    scene_dir = Path(scene_dir)
    nums: list[int] = []
    if scene_dir.exists():
        for p in scene_dir.glob(f"take-*{ext}"):
            m = _RE_TAKE.match(p.stem)
            if m:
                nums.append(int(m.group(1)))
    n = (max(nums) + 1) if nums else 1
    return scene_dir / f"take-{n:03d}{ext}"


def build_testsrc_argv(ffmpeg: str, out_path: Path, duration_s: float) -> list[str]:
    """Собрать argv для синтетического клипа (testsrc + тон) как СПИСОК токенов.

    Внимание: `duration_s` приводится к float мною — это число, а НЕ строка
    промпта. Ни один аргумент не содержит пользовательского текста.
    """
    dur = max(0.1, float(duration_s))
    lavfi_video = f"testsrc=duration={dur}:size={_SYNTH_SIZE}:rate={_SYNTH_RATE}"
    return [
        ffmpeg,
        "-nostdin",
        "-y",
        "-f", "lavfi",
        "-i", lavfi_video,
        "-pix_fmt", "yuv420p",
        "-c:v", "libx264",
        "-t", f"{dur}",
        str(out_path),
    ]


async def run_testsrc(out_path: Path, duration_s: float) -> str:
    """Сгенерировать реальный короткий mp4 через create_subprocess_exec (без shell).

    Бросает `errors.VideoProviderFailed` при отсутствии бинаря, ненулевом коде
    выхода или пустом файле."""
    ffmpeg = ffmpeg_bin()
    if ffmpeg is None:
        raise errors.VideoProviderFailed("ffmpeg binary not available")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    argv = build_testsrc_argv(ffmpeg, out_path, duration_s)
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        # stderr не содержит секретов (только диагностика кодека) — но на всякий
        # случай отдаём короткий безопасный код, а не сырой вывод.
        raise errors.VideoProviderFailed(
            f"ffmpeg exit {proc.returncode}", extra={"out": out_path.name}
        )
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise errors.VideoProviderFailed("ffmpeg produced empty output", extra={"out": out_path.name})
    return str(out_path)


async def probe_media(path: str | Path) -> tuple[float, bool]:
    """Вернуть (duration_s, has_video). Через ffprobe (если есть) или фолбэком
    через разбор stderr `ffmpeg -i`. Всё — create_subprocess_exec, без shell."""
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return (0.0, False)

    ffprobe = ffprobe_bin()
    if ffprobe:
        argv = [
            ffprobe, "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(p),
        ]
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, _ = await proc.communicate()
        if proc.returncode == 0:
            try:
                data = json.loads(out or b"{}")
            except Exception:  # noqa: BLE001
                return (0.0, False)
            dur = float((data.get("format") or {}).get("duration") or 0.0)
            has_v = any(s.get("codec_type") == "video" for s in data.get("streams", []))
            return (dur, has_v)
        return (0.0, False)

    # Фолбэк без ffprobe: ffmpeg -i печатает метаданные в stderr и выходит != 0.
    ffmpeg = ffmpeg_bin()
    if ffmpeg is None:
        return (0.0, False)
    argv = [ffmpeg, "-nostdin", "-i", str(p)]
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    _, err = await proc.communicate()
    text = (err or b"").decode("utf-8", "replace")
    dur = 0.0
    m = _RE_DURATION.search(text)
    if m:
        h, mm, ss = m.groups()
        dur = int(h) * 3600 + int(mm) * 60 + float(ss)
    has_v = "Video:" in text
    return (dur, has_v)


async def validate_video_output(path: str | Path) -> tuple[float, bool]:
    """Проверить, что файл — реальное видео (duration > 0 и есть видеопоток).
    Иначе бросить `errors.VideoInvalidOutput`. Имя файла (без пути/секретов)
    кладём в extra для диагностики."""
    dur, has_v = await probe_media(path)
    if dur <= 0.0 or not has_v:
        raise errors.VideoInvalidOutput(
            f"output failed validation (duration={dur}, video={has_v})",
            extra={"out": Path(path).name},
        )
    return (dur, has_v)
