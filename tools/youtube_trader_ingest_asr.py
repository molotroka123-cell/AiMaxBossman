#!/usr/bin/env python3
"""Local ASR for public YouTube trading replays (faster-whisper, CPU, int8).

Writes ``asr.segments.json`` (list of {start,end,text}) + ``asr.meta.json`` next
to the audio. The transcript source is labeled ``local_asr:faster-whisper-<size>``.
Nothing is sent to any cloud service. Run at low priority:

    python tools/youtube_trader_ingest_asr.py <run_dir> [--model small]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.owner_journeys.runtime_guard import lower_priority  # noqa: E402


def transcribe(audio: Path, *, model_size: str = "small", threads: int = 8) -> dict:
    from faster_whisper import WhisperModel  # local, CPU

    started = time.time()
    model = WhisperModel(model_size, device="cpu", compute_type="int8", cpu_threads=threads)
    segments, info = model.transcribe(str(audio), language="en", vad_filter=True, beam_size=1,
                                      condition_on_previous_text=False)
    rows = [{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()} for s in segments]
    return {
        "segments": rows,
        "meta": {
            "source": f"local_asr:faster-whisper-{model_size}",
            "device": "cpu", "compute_type": "int8", "cpu_threads": threads,
            "language": info.language, "audio_duration_s": round(info.duration, 1),
            "runtime_s": round(time.time() - started, 1), "segments": len(rows),
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("run_dir", nargs="+")
    ap.add_argument("--model", default="small")
    ap.add_argument("--threads", type=int, default=8)
    args = ap.parse_args(argv)
    print(json.dumps({"priority": lower_priority()}))
    for raw in args.run_dir:
        run = Path(raw)
        if (run / "asr.segments.json").is_file():
            print(json.dumps({"run": str(run), "skipped": "exists"}))
            continue
        audio = next(iter(sorted(run.glob("audio.*"))), None)
        if audio is None or audio.suffix == ".log":
            audio = next((p for p in sorted(run.glob("audio.*")) if p.suffix != ".log"), None)
        if audio is None:
            print(json.dumps({"run": str(run), "error": "no audio"}))
            continue
        out = transcribe(audio, model_size=args.model, threads=args.threads)
        (run / "asr.segments.json").write_text(json.dumps(out["segments"], ensure_ascii=False), encoding="utf-8")
        (run / "asr.meta.json").write_text(json.dumps(out["meta"], indent=2), encoding="utf-8")
        print(json.dumps({"run": str(run), **out["meta"]}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
