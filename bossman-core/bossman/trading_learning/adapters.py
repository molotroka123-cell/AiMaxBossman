"""Адаптеры к внешним технологиям с честным статусом BLOCKED.

Правило модуля: если технологии нет в окружении, шаг возвращает BLOCKED с
причиной и списком того, чего не хватает. Подделывать возможность фикстурой
запрещено — иначе бенчмарк начнёт показывать зелёное там, где ничего не
работает, и это худший из возможных исходов для торгового модуля.

Что реально доступно здесь и сейчас проверяется вызовом probe(), а не верой в
requirements.txt.
"""
from __future__ import annotations

import shutil
import wave
from dataclasses import dataclass, field
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from .safety import EvidenceClass


@dataclass(frozen=True, slots=True)
class Capability:
    """Что умеет окружение. Ответ строится проверкой, а не декларацией."""

    name: str
    available: bool
    detail: str
    missing: tuple[str, ...] = ()


@dataclass(slots=True)
class AdapterResult:
    """Результат шага пайплайна: данные либо честный отказ."""

    step: str
    status: str                      # "OK" | "BLOCKED" | "ERROR"
    evidence_class: EvidenceClass
    payload: Any = None
    reason: str = ""
    missing: tuple[str, ...] = ()
    artifacts: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "OK"

    def as_dict(self) -> dict:
        return {"step": self.step, "status": self.status,
                "evidence_class": self.evidence_class.value,
                "reason": self.reason, "missing": list(self.missing),
                "artifacts": list(self.artifacts),
                "payload_kind": type(self.payload).__name__ if self.payload is not None else None}


def _binary(name: str) -> bool:
    return shutil.which(name) is not None


def _module(name: str) -> bool:
    try:
        return find_spec(name) is not None
    except (ImportError, ValueError):     # битый пакет тоже считается отсутствующим
        return False


# ------------------------------------------------------------------ probing
def probe_audio() -> Capability:
    """Извлечение аудио требует внешнего кодировщика: cv2 звук не отдаёт."""
    have = _binary("ffmpeg")
    return Capability("extract_audio", have,
                      "ffmpeg binary found" if have else "ffmpeg binary is not installed",
                      () if have else ("ffmpeg",))


def probe_asr() -> Capability:
    engines = [m for m in ("whisper", "faster_whisper", "vosk", "speech_recognition")
               if _module(m)]
    have = bool(engines)
    return Capability("transcribe", have,
                      f"ASR engines: {','.join(engines)}" if have else "no local ASR engine",
                      () if have else ("whisper|faster_whisper|vosk", "ffmpeg"))


def probe_frames() -> Capability:
    """Кадры реально доступны: OpenCV собран с FFMPEG-бэкендом."""
    if not _module("cv2"):
        return Capability("extract_frames", False, "opencv (cv2) is not installed", ("opencv-python",))
    try:
        import cv2  # noqa: WPS433 — импорт только после проверки наличия
        backends = {cv2.videoio_registry.getBackendName(b)
                    for b in cv2.videoio_registry.getBackends()}
    except Exception as exc:  # noqa: BLE001 — сломанная сборка = отсутствие возможности
        return Capability("extract_frames", False, f"cv2 import failed: {exc}", ("opencv-python",))
    return Capability("extract_frames", True, f"cv2 backends: {','.join(sorted(backends))}")


def probe_ocr() -> Capability:
    engines = [m for m in ("pytesseract", "easyocr", "paddleocr") if _module(m)]
    if engines and (_binary("tesseract") or "easyocr" in engines):
        return Capability("chart_ocr", True, f"OCR engines: {','.join(engines)}")
    return Capability("chart_ocr", False, "no OCR engine (tesseract/easyocr) available",
                      ("tesseract", "pytesseract|easyocr"))


def probe_all() -> dict[str, Capability]:
    return {c.name: c for c in (probe_audio(), probe_asr(), probe_frames(), probe_ocr())}


def _blocked(step: str, cap: Capability) -> AdapterResult:
    return AdapterResult(step=step, status="BLOCKED", evidence_class=EvidenceClass.BLOCKED,
                         reason=cap.detail, missing=cap.missing)


# ------------------------------------------------------------------- steps
def extract_audio(video_path: str, out_path: str) -> AdapterResult:
    """Аудиодорожка. Без ffmpeg — BLOCKED, файл не создаётся."""
    cap = probe_audio()
    if not cap.available:
        return _blocked("extract_audio", cap)
    import subprocess  # noqa: WPS433 — только на доступной ветке
    cmd = ["ffmpeg", "-nostdin", "-y", "-i", str(video_path), "-vn",
           "-ac", "1", "-ar", "16000", "-f", "wav", str(out_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, check=False)
    if proc.returncode != 0 or not Path(out_path).exists():
        return AdapterResult("extract_audio", "ERROR", EvidenceClass.BLOCKED,
                             reason=proc.stderr[-500:] or "ffmpeg produced no output")
    return AdapterResult("extract_audio", "OK", EvidenceClass.REAL_SANDBOX,
                         payload=str(out_path), artifacts=[str(out_path)])


def transcribe(audio_path: str) -> AdapterResult:
    """Транскрипт с временными метками. Локального ASR нет — BLOCKED.

    Здесь принципиально нет ветки «сходить в платный облачный ASR»: цена
    провайдера неизвестна, а выдумывать стоимость запрещено.
    """
    cap = probe_asr()
    if not cap.available:
        return _blocked("transcribe", cap)
    if not Path(audio_path).exists():          # pragma: no cover — ветка недостижима без ASR
        return AdapterResult("transcribe", "ERROR", EvidenceClass.BLOCKED,
                             reason=f"audio not found: {audio_path}")
    import whisper  # noqa: WPS433 — pragma: no cover
    model = whisper.load_model("base")         # pragma: no cover
    raw = model.transcribe(str(audio_path))    # pragma: no cover
    segments = [{"start": float(s["start"]), "end": float(s["end"]), "text": s["text"]}
                for s in raw.get("segments", [])]                      # pragma: no cover
    return AdapterResult("transcribe", "OK", EvidenceClass.REAL_SANDBOX,
                         payload=segments)                             # pragma: no cover


def probe_audio_duration(wav_path: str) -> float | None:
    """Длительность wav без ffmpeg — стандартной библиотекой. Нужна для меток."""
    try:
        with wave.open(str(wav_path), "rb") as fh:
            return fh.getnframes() / float(fh.getframerate() or 1)
    except Exception:  # noqa: BLE001 — не wav или битый файл
        return None


def chart_ocr(frame_paths: list[str]) -> AdapterResult:
    """Чтение цен/значений с кадра. Движка OCR нет — BLOCKED.

    Важно: даже при наличии OCR его вывод остаётся НЕДОВЕРЕННЫМ входом —
    распознанная «цена» проверяется против рыночных данных (см. verify.py),
    а не принимается как факт.
    """
    cap = probe_ocr()
    if not cap.available:
        return _blocked("chart_ocr", cap)
    return AdapterResult("chart_ocr", "BLOCKED", EvidenceClass.BLOCKED,   # pragma: no cover
                         reason="OCR engine present but no calibrated chart profile")
