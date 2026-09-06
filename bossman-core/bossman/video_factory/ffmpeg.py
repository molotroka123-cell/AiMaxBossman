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
import contextlib
import json
import math
import re
import shutil
from pathlib import Path

from .. import errors
from .model import normalize_duration

# Размер/частота синтетического кадра: маленький и быстрый — тестам хватает,
# а бокс не грузим. Прод-провайдеры переопределяют это в своих argv.
_SYNTH_SIZE = "320x240"
_SYNTH_RATE = "15"

_RE_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
# Хвост прогресса `ffmpeg -f null -`: реальная декодированная длительность.
_RE_TIME = re.compile(r"time=\s*(\d+):(\d+):(\d+(?:\.\d+)?)")

# --- таймауты подпроцессов ---------------------------------------------------
#
# VF-005: до закрытия ни один вызов ffmpeg/ffprobe здесь не имел таймаута —
# `await proc.communicate()` ждал вечно. Зависший (или просто очень длинный)
# рендер намертво занимал воркера и рос файлом на диске, а `toolkit/media.py`
# рядом уже давно гоняет ffmpeg с timeout=900. Приводим слой к тому же правилу.
_PROBE_TIMEOUT_S = 30.0


def _render_timeout_s(duration_s: float) -> float:
    """Бюджет на рендер клипа: с большим запасом к его длительности, но конечный."""
    return max(60.0, float(duration_s) * 8.0 + 30.0)


async def _communicate(proc, timeout: float, *, stage: str):
    """`proc.communicate()` с жёстким потолком. По таймауту процесс убивается
    (иначе он переживёт задачу и продолжит писать на диск) и наружу уходит
    типизированная ошибка, а не вечное ожидание."""
    try:
        return await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except (asyncio.TimeoutError, TimeoutError):
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
        raise errors.VideoProviderFailed(
            f"{stage} timed out after {timeout:.0f}s",
            extra={"stage": stage},
        ) from None


# --- разбор длительности из метаданных --------------------------------------

def _num(value) -> float:
    """Число из ffprobe-поля: только КОНЕЧНОЕ положительное, иначе 0.0.

    ffprobe отдаёт числа строками и умеет отдавать `"N/A"`, `"nan"`, `"inf"`;
    голый `float()` на них либо бросал ValueError мимо типизированных ошибок,
    либо (для nan) протаскивал NaN дальше — а `NaN <= 0.0` ложно, и битый файл
    проходил валидацию."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    return f if math.isfinite(f) and f > 0.0 else 0.0


def _fps(value) -> float:
    """`avg_frame_rate` вида "30000/1001" → float; "0/0" и мусор → 0.0."""
    if isinstance(value, str) and "/" in value:
        num, _, den = value.partition("/")
        n, d = _num(num), _num(den)
        return n / d if n and d else 0.0
    return _num(value)


def _stream_duration(stream: dict) -> float:
    """Длительность видеопотока по любому доступному признаку.

    Контейнер (matroska/webm, записанный в неперематываемый вывод — ровно то,
    что отдаёт облачный провайдер) законно не содержит `format.duration`. До
    VF-001 это читалось как «длительность 0» и валидный артефакт браковался."""
    d = _num(stream.get("duration"))
    if d:
        return d
    ts, tb = stream.get("duration_ts"), stream.get("time_base")
    if ts is not None and isinstance(tb, str) and "/" in tb:
        base = _fps(tb)  # time_base "1/1000" читается той же дробью
        if base:
            d = _num(ts) * base
            if d:
                return d
    fps = _fps(stream.get("avg_frame_rate")) or _fps(stream.get("r_frame_rate"))
    frames = _num(stream.get("nb_frames"))
    if frames and fps:
        return frames / fps
    return 0.0


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

    Длительность проходит `normalize_duration` (типизированный отказ на
    NaN/inf/выходе за границы). Прежний `max(0.1, float(...))` молча превращал
    NaN в 0.1 и пропускал duration_s=1e6 в бесконечный рендер.
    """
    dur = normalize_duration(duration_s)
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
    _, err = await _communicate(proc, _render_timeout_s(duration_s), stage="render")
    if proc.returncode != 0:
        # stderr не содержит секретов (только диагностика кодека) — но на всякий
        # случай отдаём короткий безопасный код, а не сырой вывод.
        raise errors.VideoProviderFailed(
            f"ffmpeg exit {proc.returncode}", extra={"out": out_path.name}
        )
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise errors.VideoProviderFailed("ffmpeg produced empty output", extra={"out": out_path.name})
    return str(out_path)


async def _probe_packet_duration(ffprobe: str, path: Path) -> float:
    """Длительность по РЕАЛЬНО присутствующим пакетам видеопотока.

    Последний рубеж для контейнеров без единой метки длительности (matroska/webm
    из неперематываемого вывода). Это демукс без декодирования — дёшево, но всё
    равно проход по файлу, поэтому вызывается ТОЛЬКО когда метаданных нет, и
    ограничен `_PROBE_TIMEOUT_S`."""
    argv = [
        ffprobe, "-v", "error", "-select_streams", "v:0", "-count_packets",
        "-show_entries", "stream=nb_read_packets,avg_frame_rate,r_frame_rate,duration",
        "-print_format", "json", str(path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, _ = await _communicate(proc, _PROBE_TIMEOUT_S, stage="probe-packets")
    if proc.returncode != 0:
        return 0.0
    try:
        data = json.loads(out or b"{}")
    except Exception:  # noqa: BLE001
        return 0.0
    for stream in data.get("streams") or []:
        d = _num(stream.get("duration"))
        if d:
            return d
        fps = _fps(stream.get("avg_frame_rate")) or _fps(stream.get("r_frame_rate"))
        packets = _num(stream.get("nb_read_packets"))
        if packets and fps:
            return packets / fps
    return 0.0


async def _probe_decoded_duration(ffmpeg: str, path: Path) -> float:
    """Фолбэк без ffprobe: прогнать файл в `-f null -` и взять последний `time=`.
    Тоже ограничен таймаутом, тоже только когда метаданных нет."""
    argv = [ffmpeg, "-nostdin", "-i", str(path), "-f", "null", "-"]
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    _, err = await _communicate(proc, _PROBE_TIMEOUT_S, stage="probe-decode")
    matches = _RE_TIME.findall((err or b"").decode("utf-8", "replace"))
    if not matches:
        return 0.0
    h, mm, ss = matches[-1]
    return _num(int(h) * 3600 + int(mm) * 60 + float(ss))


async def probe_media(path: str | Path) -> tuple[float, bool]:
    """Вернуть (duration_s, has_video). Через ffprobe (если есть) или фолбэком
    через разбор stderr `ffmpeg -i`. Всё — create_subprocess_exec, без shell.

    Длительность берётся из `format.duration`, затем из полей видеопотока и лишь
    затем — счётом пакетов/декодом. Возвращаемое число ВСЕГДА конечное: NaN и inf
    из метаданных превращаются в 0.0 (`_num`), а не протаскиваются в валидацию."""
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
        out, _ = await _communicate(proc, _PROBE_TIMEOUT_S, stage="probe")
        if proc.returncode != 0:
            return (0.0, False)
        try:
            data = json.loads(out or b"{}")
        except Exception:  # noqa: BLE001
            return (0.0, False)
        streams = data.get("streams") or []
        video = [s for s in streams if s.get("codec_type") == "video"]
        has_v = bool(video)
        dur = _num((data.get("format") or {}).get("duration"))
        if not dur and video:
            dur = max((_stream_duration(s) for s in video), default=0.0)
        if not dur and has_v:
            dur = await _probe_packet_duration(ffprobe, p)
        return (dur, has_v)

    # Фолбэк без ffprobe: ffmpeg -i печатает метаданные в stderr и выходит != 0.
    ffmpeg = ffmpeg_bin()
    if ffmpeg is None:
        return (0.0, False)
    argv = [ffmpeg, "-nostdin", "-i", str(p)]
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    _, err = await _communicate(proc, _PROBE_TIMEOUT_S, stage="probe")
    text = (err or b"").decode("utf-8", "replace")
    dur = 0.0
    m = _RE_DURATION.search(text)
    if m:
        h, mm, ss = m.groups()
        dur = _num(int(h) * 3600 + int(mm) * 60 + float(ss))
    has_v = "Video:" in text
    if not dur and has_v:  # "Duration: N/A" у потокового контейнера
        dur = await _probe_decoded_duration(ffmpeg, p)
    return (dur, has_v)


async def validate_video_output(path: str | Path) -> tuple[float, bool]:
    """Проверить, что файл — реальное видео (duration > 0 и есть видеопоток).
    Иначе бросить `errors.VideoInvalidOutput`. Имя файла (без пути/секретов)
    кладём в extra для диагностики."""
    dur, has_v = await probe_media(path)
    if not math.isfinite(dur) or dur <= 0.0 or not has_v:
        raise errors.VideoInvalidOutput(
            f"output failed validation (duration={dur}, video={has_v})",
            extra={"out": Path(path).name},
        )
    return (dur, has_v)
