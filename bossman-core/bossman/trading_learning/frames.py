"""Извлечение кадров вокруг рыночных утверждений — реальная возможность.

В отличие от аудио и OCR, здесь технология есть: OpenCV собран с FFMPEG-бэкендом.
Поэтому шаг даёт класс REAL_SANDBOX, а не BLOCKED.

Экономия токенов заложена в сам шаг: кадры дедуплицируются по грубому
перцептивному хешу. Смысл в том, что за минуту стрима график почти не меняется,
и десять одинаковых кадров — это десять оплаченных описаний одного и того же.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .adapters import AdapterResult, probe_frames
from .safety import EvidenceClass

_DHASH_SIZE = 8          # 8x9 grayscale -> 64-битный хеш
_DEFAULT_MAX_FRAMES = 40


@dataclass(frozen=True, slots=True)
class FrameRef:
    """Ссылка на кадр. Именно она подставляется в raw_quote_or_frame_ref."""

    path: str
    ts_seconds: float
    dhash: str
    sha256: str

    def as_dict(self) -> dict:
        return {"path": self.path, "ts_seconds": self.ts_seconds,
                "dhash": self.dhash, "sha256": self.sha256}


def _dhash(gray) -> str:
    """Разностный хеш: устойчив к шуму компрессии, ловит одинаковые кадры."""
    import cv2  # noqa: WPS433
    small = cv2.resize(gray, (_DHASH_SIZE + 1, _DHASH_SIZE), interpolation=cv2.INTER_AREA)
    bits = (small[:, 1:] > small[:, :-1]).flatten()
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return f"{value:016x}"


def _hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def extract_frames(video_path: str, out_dir: str, *, timestamps: list[float] | None = None,
                   every_seconds: float = 5.0, max_frames: int = _DEFAULT_MAX_FRAMES,
                   dedup_distance: int = 4) -> AdapterResult:
    """Кадры на заданных метках (или равномерно) с дедупликацией.

    timestamps задаются рыночными утверждениями: кадр нужен там, где автор
    что-то заявил, а не «каждые 5 секунд всё видео».
    """
    cap_info = probe_frames()
    if not cap_info.available:
        return AdapterResult("extract_frames", "BLOCKED", EvidenceClass.BLOCKED,
                             reason=cap_info.detail, missing=cap_info.missing)
    src = Path(video_path)
    if not src.is_file():
        return AdapterResult("extract_frames", "ERROR", EvidenceClass.BLOCKED,
                             reason=f"video not found: {src}")

    import cv2  # noqa: WPS433

    capture = cv2.VideoCapture(str(src))
    if not capture.isOpened():
        return AdapterResult("extract_frames", "ERROR", EvidenceClass.BLOCKED,
                             reason=f"cv2 cannot open {src}")
    try:
        fps = capture.get(cv2.CAP_PROP_FPS) or 0.0
        total = capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
        duration = (total / fps) if fps > 0 else 0.0
        if timestamps is None:
            step = max(every_seconds, 0.1)
            marks = [t * step for t in range(int(duration / step) + 1)] if duration else [0.0]
        else:
            marks = sorted({round(float(t), 3) for t in timestamps if t >= 0})
        marks = marks[:max_frames]

        target = Path(out_dir)
        target.mkdir(parents=True, exist_ok=True)
        kept: list[FrameRef] = []
        skipped_duplicates = 0
        for mark in marks:
            if duration and mark > duration:
                continue
            capture.set(cv2.CAP_PROP_POS_MSEC, mark * 1000.0)
            ok, frame = capture.read()
            if not ok or frame is None:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            digest = _dhash(gray)
            # Кадр, неотличимый от уже сохранённого, не пишется и не оплачивается.
            if any(_hamming(digest, k.dhash) <= dedup_distance for k in kept):
                skipped_duplicates += 1
                continue
            name = target / f"frame_{mark:09.3f}.png"
            if not cv2.imwrite(str(name), frame):
                continue
            payload = name.read_bytes()
            kept.append(FrameRef(str(name), mark, digest,
                                 hashlib.sha256(payload).hexdigest()))
    finally:
        capture.release()

    return AdapterResult(
        "extract_frames", "OK", EvidenceClass.REAL_SANDBOX,
        payload=kept, artifacts=[k.path for k in kept],
        reason=(f"fps={fps:.2f} duration={duration:.2f}s kept={len(kept)} "
                f"deduplicated={skipped_duplicates}"))
