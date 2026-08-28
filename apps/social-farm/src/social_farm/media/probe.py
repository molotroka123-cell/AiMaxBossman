"""Проба медиа: измерение файла, а не догадка о нём.

ffprobe и ffmpeg — ВНЕШНИЕ программы. Их может не быть, и тогда единственный
честный ответ — `NOT_SUPPORTED`. Не «наверное, mp4 с h264»: непроверенный файл
не публикуется. Правило одно и оно жёсткое — **проба ничего не выдумывает**.
Каждый результат несёт поле `prober`, то есть имя прибора, которым он получен,
и по нему всегда видно, откуда взялось число.

Приборов два, и оба настоящие:

* `ffprobe` — полное измерение; единственный способ узнать что-либо о видео и
  звуке. Нет ffprobe — нет видео, и об этом говорится вслух.
* `header` — разбор заголовков контейнеров изображений (PNG, JPEG, GIF, WebP)
  по их форматам. Это чтение байтов файла, а не подмена ffprobe: размеры
  берутся из IHDR, SOF, дескриптора экрана и чанка VP8 соответственно.
  Он честно не умеет ничего про видео и про звук и не притворяется, что умеет.

Заголовочный разбор проверяет и целостность: PNG дочитывается до IEND, JPEG до
EOI, GIF до терминатора, WebP сверяется с длиной из RIFF. Обрезанный файл
поэтому ловится и без ffprobe — как `FAIL_CORRUPT`, а не как «прошёл».
"""
from __future__ import annotations

import json
import shutil
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .asset import AssetType

# Сколько ждать внешнюю программу. Проба, которая висит, — это очередь работ,
# которая стоит; лучше честный таймаут.
PROBE_TIMEOUT_SECONDS = 30


class ProbeUnavailable(RuntimeError):
    """Измерить нечем. Отображается на `NOT_SUPPORTED`, а не на «наверное».

    Это НЕ вердикт о файле. Файл может быть прекрасен; мы просто не можем это
    установить, а публиковать неизмеренное нельзя.
    """


class CorruptMedia(ValueError):
    """Файл не читается как заявленный формат. Это уже вердикт — `FAIL_CORRUPT`."""


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """Измерения файла. `prober` говорит, чем измерено."""

    type: AssetType
    mime: str
    container: str
    prober: str
    bytes: int
    codec: str | None = None
    audio_codec: str | None = None
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    bitrate_bps: int | None = None

    @property
    def duration_seconds(self) -> float | None:
        return None if self.duration_ms is None else self.duration_ms / 1000.0

    @property
    def aspect(self) -> float | None:
        if not self.width or not self.height:
            return None
        return self.width / self.height


def ffprobe_path() -> str | None:
    return shutil.which("ffprobe")


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def ffprobe_available() -> bool:
    return ffprobe_path() is not None


def ffmpeg_available() -> bool:
    return ffmpeg_path() is not None


def toolchain_status() -> dict[str, object]:
    """Что доступно в этой среде. Идёт в отчёт и в диагностику."""
    ff, fp = ffmpeg_path(), ffprobe_path()
    return {"ffmpeg": ff, "ffprobe": fp,
            "video_supported": bool(fp),
            "transform_supported": bool(ff and fp),
            "image_probe": "header" if not fp else "ffprobe"}


# --------------------------------------------------------------------- ffprobe

_IMAGE_FORMATS = {"png_pipe", "jpeg_pipe", "mjpeg", "image2", "gif", "webp_pipe",
                  "webp", "bmp_pipe", "tiff_pipe"}
_IMAGE_CODECS = {"png", "mjpeg", "gif", "webp", "bmp", "tiff"}


def probe_with_ffprobe(path: str | Path) -> ProbeResult:
    """Настоящая проба. Вызывается, только если ffprobe есть."""
    binary = ffprobe_path()
    if binary is None:
        raise ProbeUnavailable(
            "ffprobe не установлен: измерить файл нечем. NOT_SUPPORTED — "
            "непроверенный файл не публикуется")
    target = Path(path)
    try:
        completed = subprocess.run(
            [binary, "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(target)],
            capture_output=True, timeout=PROBE_TIMEOUT_SECONDS, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ProbeUnavailable(f"ffprobe не ответил за {PROBE_TIMEOUT_SECONDS} с") from exc
    except OSError as exc:
        raise ProbeUnavailable(f"ffprobe не запустился: {exc}") from exc
    if completed.returncode != 0:
        raise CorruptMedia(
            f"ffprobe не смог разобрать файл: "
            f"{completed.stderr.decode('utf-8', 'replace').strip()[:300]}")
    try:
        report = json.loads(completed.stdout.decode("utf-8", "replace"))
    except json.JSONDecodeError as exc:
        raise CorruptMedia(f"ffprobe вернул неразбираемый ответ: {exc}") from exc

    streams = report.get("streams") or []
    fmt = report.get("format") or {}
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None and audio is None:
        raise CorruptMedia("в файле нет ни видео-, ни аудиопотока")

    container = str(fmt.get("format_name") or "")
    duration_ms = _duration_ms(fmt.get("duration"))
    if video is not None and duration_ms is None:
        duration_ms = _duration_ms(video.get("duration"))

    codec = str(video.get("codec_name")) if video else None
    is_image = (video is not None and audio is None
                and (container in _IMAGE_FORMATS
                     or set(container.split(",")) & _IMAGE_FORMATS
                     or (codec in _IMAGE_CODECS and not duration_ms)))
    if is_image:
        asset_type, duration_ms = AssetType.IMAGE, None
    elif video is not None:
        asset_type = AssetType.VIDEO
    else:
        asset_type = AssetType.AUDIO

    width = _int_or_none(video.get("width")) if video else None
    height = _int_or_none(video.get("height")) if video else None
    # ffprobe на битом файле не всегда падает: у обрезанного PNG он выходит с
    # кодом 0 и отдаёт width=0, height=0. Нулевой кадр — это не кадр, и
    # пропустить его дальше значит опубликовать пустоту. Проверено на
    # настоящем ffprobe, а не предположено.
    if asset_type in (AssetType.IMAGE, AssetType.VIDEO) and not (width and height):
        raise CorruptMedia(
            f"ffprobe вернул нулевые размеры ({width}×{height}): файл повреждён")

    return ProbeResult(
        type=asset_type, mime=_mime_for(asset_type, codec, container),
        container=container, prober="ffprobe",
        bytes=int(fmt.get("size") or Path(target).stat().st_size),
        codec=codec, audio_codec=str(audio.get("codec_name")) if audio else None,
        width=width, height=height,
        duration_ms=duration_ms, bitrate_bps=_int_or_none(fmt.get("bit_rate")))


def _duration_ms(value: object) -> int | None:
    try:
        seconds = float(str(value))
    except (TypeError, ValueError):
        return None
    return int(round(seconds * 1000)) if seconds > 0 else None


def _int_or_none(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _mime_for(asset_type: AssetType, codec: str | None, container: str) -> str:
    if asset_type is AssetType.IMAGE:
        return {"png": "image/png", "mjpeg": "image/jpeg", "gif": "image/gif",
                "webp": "image/webp", "bmp": "image/bmp"}.get(codec or "",
                                                              "application/octet-stream")
    parts = set(container.split(","))
    if asset_type is AssetType.VIDEO:
        if "mp4" in parts:
            return "video/mp4"
        if "mov" in parts or "quicktime" in parts:
            return "video/quicktime"
        return "video/x-matroska" if "matroska" in parts else "application/octet-stream"
    if "mp3" in parts:
        return "audio/mpeg"
    return "audio/mp4" if "mp4" in parts else "application/octet-stream"


# ------------------------------------------------------- разбор заголовков

def probe_image_header(path: str | Path) -> ProbeResult:
    """Разбор заголовка изображения по спецификации его формата.

    Настоящее измерение: числа читаются из файла. Того, чего в заголовке нет
    (битрейт, кодек видео, звук), здесь нет и не появляется.
    """
    target = Path(path)
    data = target.read_bytes()
    size = len(data)
    for reader in (_read_png, _read_jpeg, _read_gif, _read_webp):
        result = reader(data, size)
        if result is not None:
            return result
    raise ProbeUnavailable(
        f"формат файла {target.name} не разбирается заголовочной пробой "
        f"(поддержаны PNG, JPEG, GIF, WebP), а ffprobe в системе нет. "
        f"NOT_SUPPORTED — непроверенный файл не публикуется")


def _read_png(data: bytes, size: int) -> ProbeResult | None:
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    if size < 24 or data[12:16] != b"IHDR":
        raise CorruptMedia("PNG без корректного блока IHDR")
    width, height = struct.unpack(">II", data[16:24])
    # Целостность: файл обязан дочитываться по цепочке чанков до IEND.
    offset, seen_end = 8, False
    while offset + 8 <= size:
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        offset += 12 + length              # длина + тип + данные + CRC
        if kind == b"IEND":
            seen_end = True
            break
    if not seen_end or offset > size:
        raise CorruptMedia("PNG обрывается: цепочка чанков не доходит до IEND")
    if not width or not height:
        raise CorruptMedia("PNG с нулевым размером")
    return ProbeResult(type=AssetType.IMAGE, mime="image/png", container="png",
                       prober="header", bytes=size, codec="png",
                       width=width, height=height)


def _read_jpeg(data: bytes, size: int) -> ProbeResult | None:
    if not data.startswith(b"\xff\xd8"):
        return None
    # Кадровые маркеры, несущие размеры. DHP/DAC/RST исключены намеренно.
    frame_markers = ({0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                      0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF})
    offset, width, height = 2, None, None
    while offset + 3 < size:
        if data[offset] != 0xFF:
            offset += 1
            continue
        marker = data[offset + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            offset += 2
            continue
        if marker == 0xD9:                 # EOI
            break
        length = struct.unpack(">H", data[offset + 2:offset + 4])[0]
        if marker in frame_markers:
            if offset + 9 > size:
                raise CorruptMedia("JPEG обрывается внутри кадрового маркера")
            height, width = struct.unpack(">HH", data[offset + 5:offset + 9])
            break
        if marker == 0xDA:                 # начались данные — размеров дальше нет
            break
        offset += 2 + length
    if not width or not height:
        raise CorruptMedia("в JPEG не найден кадровый маркер с размерами")
    if data.rstrip(b"\x00")[-2:] != b"\xff\xd9":
        raise CorruptMedia("JPEG обрывается: нет маркера конца изображения (EOI)")
    return ProbeResult(type=AssetType.IMAGE, mime="image/jpeg", container="jpeg",
                       prober="header", bytes=size, codec="mjpeg",
                       width=width, height=height)


def _read_gif(data: bytes, size: int) -> ProbeResult | None:
    if not data.startswith((b"GIF87a", b"GIF89a")):
        return None
    if size < 10:
        raise CorruptMedia("GIF короче логического дескриптора экрана")
    width, height = struct.unpack("<HH", data[6:10])
    if not width or not height:
        raise CorruptMedia("GIF с нулевым размером")
    if not data.rstrip(b"\x00").endswith(b"\x3b"):
        raise CorruptMedia("GIF обрывается: нет терминатора")
    return ProbeResult(type=AssetType.IMAGE, mime="image/gif", container="gif",
                       prober="header", bytes=size, codec="gif",
                       width=width, height=height)


def _read_webp(data: bytes, size: int) -> ProbeResult | None:
    if not (data.startswith(b"RIFF") and size >= 16 and data[8:12] == b"WEBP"):
        return None
    declared = struct.unpack("<I", data[4:8])[0] + 8
    if declared > size:
        raise CorruptMedia(
            f"WebP обрывается: RIFF обещает {declared} байт, в файле {size}")
    chunk = data[12:16]
    width = height = None
    if chunk == b"VP8 " and size >= 30:
        width, height = struct.unpack("<HH", data[26:30])
        width, height = width & 0x3FFF, height & 0x3FFF
    elif chunk == b"VP8L" and size >= 25:
        bits = struct.unpack("<I", data[21:25])[0]
        width, height = (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    elif chunk == b"VP8X" and size >= 30:
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
    if not width or not height:
        raise CorruptMedia("WebP без разбираемых размеров")
    return ProbeResult(type=AssetType.IMAGE, mime="image/webp", container="webp",
                       prober="header", bytes=size, codec="webp",
                       width=width, height=height)


# --------------------------------------------------------------------- вход

def probe(path: str | Path) -> ProbeResult:
    """Измерить файл лучшим доступным прибором и проверить его целостность.

    Прибор и проверка целостности здесь РАЗНЫЕ вещи, и это выяснилось на
    настоящем ffprobe: обрезанный PNG он разбирает без единой жалобы. С
    `-show_format -show_streams` ffprobe читает заголовок и не декодирует
    данные, поэтому файл, у которого есть IHDR и нет половины IDAT, выглядит
    для него исправным — и ушёл бы в публикацию битым.

    Поэтому для изображений структурная проверка (дочитывание до IEND, EOI,
    терминатора GIF, сверка длины RIFF) выполняется ВСЕГДА, даже когда есть
    ffprobe: она строже. Измерение при этом остаётся за ffprobe, который знает
    про форматы больше нас.

    Для видео такой второй проверки у нас нет, и это записано честно: ffprobe
    ловит повреждения контейнера, но не гарантирует целость каждого кадра.
    """
    if not ffprobe_available():
        return probe_image_header(path)
    try:
        # Ради проверки целостности, а не ради чисел. `CorruptMedia` отсюда —
        # настоящее повреждение, и оно важнее мнения ffprobe.
        probe_image_header(path)
    except ProbeUnavailable:
        pass                                   # не изображение — измеряем как есть
    return probe_with_ffprobe(path)


__all__ = ["CorruptMedia", "PROBE_TIMEOUT_SECONDS", "ProbeResult", "ProbeUnavailable",
           "ffmpeg_available", "ffmpeg_path", "ffprobe_available", "ffprobe_path",
           "probe", "probe_image_header", "probe_with_ffprobe", "toolchain_status"]
