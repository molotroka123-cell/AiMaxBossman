"""Voice engine comparison harness: one phrase set, every INSTALLED engine.

``python -m bcc.pit.voice_bench [--out report.json]`` synthesizes the same phrases
(stress, numbers, questions, pauses) with each engine that is actually available on
this host, measures synthesis latency and, when a local STT is available, the word
error rate of transcribing the audio back. Engines that are not installed are listed
as such and never measured; nothing is downloaded or installed. The report never
declares a winner or an adoption: a candidate voice counts as adopted only after it
has spoken inside Jeff and the owner has listened to it.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from typing import Callable

from .latency import LatencyStats

SCHEMA = "bossman.pit.voice-bench/1"

PHRASES: tuple[dict, ...] = (
    {"id": "stress-1", "category": "stress", "text": "Замок на двери сломался, а в замке живёт привидение."},
    {"id": "stress-2", "category": "stress", "text": "Я купил муку и посмотрел мультфильм про мышей."},
    {"id": "stress-3", "category": "stress", "text": "Договор подписан, звонит телефон, портфель на столе."},
    {"id": "num-1", "category": "numbers", "text": "Встреча в 14:30, комната 205, до конца месяца осталось 17 дней."},
    {"id": "num-2", "category": "numbers", "text": "Итого 1 250 рублей и 40 копеек, скидка 15 процентов."},
    {"id": "num-3", "category": "numbers", "text": "Позвони по номеру 8 800 555 35 35 в 2027 году."},
    {"id": "q-1", "category": "questions", "text": "Ты уже успел это проверить?"},
    {"id": "q-2", "category": "questions", "text": "Сколько это будет стоить, и когда ты сможешь начать?"},
    {"id": "q-3", "category": "questions", "text": "Правда? Ты серьёзно собираешься так сделать?"},
    {"id": "pause-1", "category": "pauses", "text": "Хорошо. Я понял. Давай сделаем так: сначала проверка, потом отчёт."},
    {"id": "pause-2", "category": "pauses", "text": "Во-первых, это быстро; во-вторых, это бесплатно; в-третьих, это надёжно."},
    {"id": "pause-3", "category": "pauses", "text": "Слушай... подожди минуту, и я расскажу всё по порядку."},
)


def normalize(text: str) -> list[str]:
    value = str(text).lower().replace("ё", "е")
    return re.sub(r"[^\w\s]|_", " ", value).split()


def wer(reference: str, hypothesis: str) -> float:
    """Word error rate: word-level Levenshtein distance over the reference length."""
    ref, hyp = normalize(reference), normalize(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    row = list(range(len(hyp) + 1))
    for i, word in enumerate(ref, 1):
        prev, row[0] = row[0], i
        for j, other in enumerate(hyp, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (word != other))
    return row[-1] / len(ref)


def run_bench(engines, *, transcribe: Callable[[bytes], str] | None = None,
              phrases=PHRASES, clock=time.perf_counter,
              duration_of: Callable[[bytes], float | None] | None = None) -> dict:
    report: dict = {"schema": SCHEMA, "phrases": len(phrases), "engines": [],
                    "adopted": False,
                    "note": "WER and latency are not naturalness. A candidate is adopted only "
                            "after it spoke inside Jeff and the owner listened to it."}
    for engine in engines:
        status = engine.status()
        entry: dict = {"engine": engine.name, "candidate": bool(getattr(engine, "candidate", False)),
                       "installed": bool(status.get("available")),
                       "reason_code": status.get("reason_code")}
        report["engines"].append(entry)
        if not entry["installed"]:
            continue
        latency, rtf_values = LatencyStats(), []
        errors, by_category, failures = [], {}, {}
        for phrase in phrases:
            started = clock()
            try:
                audio = engine.synthesize(phrase["text"], stopped=lambda: False)
            except Exception as exc:  # noqa: BLE001 — one bad phrase must not hide the rest
                failures[phrase["id"]] = str(exc)[:40]
                latency.add(0, ok=False)
                continue
            ms = (clock() - started) * 1000.0
            latency.add(ms)
            seconds = duration_of(audio) if duration_of else None
            if seconds:
                rtf_values.append(ms / 1000.0 / seconds)
            if transcribe is not None:
                try:
                    value = wer(phrase["text"], transcribe(audio))
                except Exception as exc:  # noqa: BLE001
                    failures[phrase["id"] + ":stt"] = str(exc)[:40]
                    continue
                errors.append(value)
                by_category.setdefault(phrase["category"], []).append(value)
        entry["latency"] = latency.snapshot()
        entry["rtf_mean"] = round(sum(rtf_values) / len(rtf_values), 4) if rtf_values else None
        entry["wer_mean"] = round(sum(errors) / len(errors), 4) if errors else None
        entry["wer_by_category"] = {key: round(sum(v) / len(v), 4) for key, v in by_category.items()}
        entry["failures"] = failures
    return report


def _default_transcribe() -> Callable[[bytes], str] | None:
    from . import speech, voice
    if not speech.asr_status()["available"]:
        return None

    def transcribe(audio: bytes) -> str:
        return speech.transcribe_wav(voice._decode_ogg_opus(audio))["text"]
    return transcribe


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bcc.pit.voice_bench", description=__doc__)
    parser.add_argument("--out", default="", help="write the JSON report here")
    args = parser.parse_args(argv)
    from .tts_engines import all_engines
    report = run_bench(all_engines(), transcribe=_default_transcribe())
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
