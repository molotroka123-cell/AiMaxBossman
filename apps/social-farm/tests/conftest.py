"""Общие приспособления для тестов медиа и контента.

Файлы здесь строятся НАСТОЯЩИЕ. PNG собирается по спецификации формата
(IHDR/IDAT/IEND с правильными CRC и zlib-сжатием), поэтому его одинаково
читают и наш заголовочный разбор, и ffprobe там, где он есть. Это важно:
тест, работающий на выдуманных байтах, доказывал бы только то, что выдуманные
байты разбираются выдуманным образом.

Ни ffmpeg, ни ffprobe для сборки фикстур не нужны — иначе тесты не смогли бы
запускаться в среде без них, а именно эту среду и надо проверить.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from social_farm.media.profiles import ProviderMediaProfile
from social_farm.media.store import MediaStore


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))


def make_png(width: int = 1080, height: int = 1350, *, noisy: bool = False) -> bytes:
    """Настоящий PNG заданного размера.

    `noisy=True` заполняет кадр несжимаемым шумом — так получается большой
    файл для проверки лимита размера, без мегабайтов в репозитории.
    """
    if noisy:
        import os
        rows = [b"\x00" + os.urandom(width * 3) for _ in range(height)]
    else:
        row = b"\x00" + b"\x7f\x30\x20" * width
        rows = [row] * height
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header)
            + _chunk(b"IDAT", zlib.compress(b"".join(rows), 6 if noisy else 9))
            + _chunk(b"IEND", b""))


def make_gif(width: int = 100, height: int = 100) -> bytes:
    """Минимальный настоящий GIF87a с корректным терминатором."""
    return (b"GIF87a" + struct.pack("<HH", width, height)
            + b"\x00\x00\x00"                       # флаги, фон, соотношение
            + b"\x2c" + struct.pack("<HHHH", 0, 0, width, height) + b"\x00"
            + b"\x02\x02\x44\x01\x00"               # минимальные LZW-данные
            + b"\x3b")


def truncate(data: bytes, *, keep: float = 0.5) -> bytes:
    """Обрезать файл, сохранив заголовок. Так выглядит битая загрузка."""
    return data[:max(32, int(len(data) * keep))]


@pytest.fixture()
def store(tmp_path: Path) -> MediaStore:
    return MediaStore(tmp_path / "objects")


@pytest.fixture()
def png() -> bytes:
    """1080×1350 — рекомендованный кадр ленты Instagram (4:5)."""
    return make_png(1080, 1350)


@pytest.fixture()
def png_factory():
    return make_png


@pytest.fixture()
def permissive_png_profile() -> ProviderMediaProfile:
    """Профиль, принимающий PNG как есть. Нужен, чтобы получить чистый PASS.

    Профиль Instagram принимает только JPEG, поэтому PNG против него честно
    даёт `PASS_WITH_TRANSFORM`. Чтобы проверить сам исход `PASS`, нужен
    профиль, для которого файл уже подходит, — профили это данные, и тест
    вправе принести свои.
    """
    return ProviderMediaProfile.from_dict({
        "provider": "test", "content_type": "IMAGE", "verified_at": "2026-08-28",
        "mime_allowlist": ["image/png"], "container_allowlist": ["png"],
        "codec_allowlist": ["png"], "audio_codec_allowlist": None,
        "max_bytes": 50_000_000, "min_width": 100, "max_width": 4000,
        "min_height": 100, "max_height": 4000,
        "duration_min_s": None, "duration_max_s": None,
        "aspect_rules": ["4:5..1.91:1"],
    })
