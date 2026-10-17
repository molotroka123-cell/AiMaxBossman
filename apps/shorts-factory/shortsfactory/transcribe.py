"""Transcript with faster-whisper (local, CPU int8 by default), word timestamps, cached per source fingerprint."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

MODEL_DIRS = [Path(os.environ.get("SHORTS_WHISPER_DIR", "")) if os.environ.get("SHORTS_WHISPER_DIR") else None,
              Path(r"C:\Users\asd\Bossman\models\voice\whisper")]


def resolve_model(name: str) -> str:
    """Local model dir if present (no download), else the faster-whisper name (uses the HF cache)."""
    for base in MODEL_DIRS:
        if base and (base / f"faster-whisper-{name}").is_dir():
            return str(base / f"faster-whisper-{name}")
    return name


def transcribe(path: str | Path, *, model: str = "small", language: str | None = None, cpu_threads: int = 4,
               cache_dir: Path | None = None, cache_key: str | None = None) -> dict:
    if cache_dir and cache_key:
        cp = Path(cache_dir) / f"transcript_{cache_key.replace(':', '_')}_{model}.json"
        if cp.is_file():
            return dict(json.loads(cp.read_text(encoding="utf-8")), cached=True)
    from faster_whisper import WhisperModel
    t0 = time.time()
    wm = WhisperModel(resolve_model(model), device="cpu", compute_type="int8", cpu_threads=cpu_threads)
    segs, info = wm.transcribe(str(path), language=language, word_timestamps=True, vad_filter=True, beam_size=1)
    out_segs = []
    for s in segs:
        words = [{"word": w.word, "start": round(w.start, 3), "end": round(w.end, 3), "p": round(w.probability, 3)}
                 for w in (s.words or [])]
        out_segs.append({"start": round(s.start, 3), "end": round(s.end, 3), "text": s.text.strip(), "words": words})
    res = {"language": info.language, "language_probability": round(info.language_probability, 3),
           "duration": round(info.duration, 3), "model": model, "segments": out_segs,
           "wall_s": round(time.time() - t0, 1), "engine": "faster-whisper"}
    if cache_dir and cache_key:
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    return res


def words_in(transcript: dict, start: float, end: float) -> list[dict]:
    """Words fully inside [start, end), times shifted to clip time 0."""
    out = []
    for s in transcript.get("segments", []):
        if s["end"] < start or s["start"] > end:
            continue
        for w in s.get("words") or []:
            if w["start"] >= start - 0.05 and w["end"] <= end + 0.05:
                out.append({"word": w["word"], "start": round(max(0.0, w["start"] - start), 3), "end": round(min(end, w["end"]) - start, 3)})
    return out


def text_in(transcript: dict, start: float, end: float) -> str:
    return " ".join(s["text"] for s in transcript.get("segments", []) if s["start"] >= start - 0.05 and s["end"] <= end + 0.05).strip()
