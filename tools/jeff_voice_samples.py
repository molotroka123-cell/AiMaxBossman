"""Voice samples for the owner to choose Jeff's voice: the SAME sentence in several local Piper voices, written as Telegram-ready OGG files.

    python tools/jeff_voice_samples.py --out samples --voice ru-irina=C:\\voices\\ru_RU-irina-medium.onnx --voice en-lessac=C:\\voices\\en_US-lessac-medium.onnx

Needs only what Jeff already uses: ``BOSSMAN_PIT_TTS_EXECUTABLE`` (piper) and ffmpeg on PATH. Nothing is downloaded and nothing is sent: the
files go to ``--out`` and the caller (the local Claude / the owner) sends them to the pult. The language of each sample comes from the model
name (``ru_RU-*`` / ``en_*``), the sentence from the language, so «3 Russian + 3 English» is just six ``--voice`` options.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

LINES = {"ru": "Привет! Меня зовут Джефф. Я ваш ИИ-ассистент: могу ответить на вопрос, напомнить о деле или просто поболтать.",
         "en": "Hi! My name is Jeff. I'm your AI assistant: I can answer a question, remind you of a task, or just have a chat."}


def language_of(voice_path: str) -> str:
    name = Path(voice_path).name.lower()
    return "ru" if name.startswith("ru") else "en"


def parse_voice(spec: str) -> tuple[str, str]:
    label, sep, path = spec.partition("=")
    if not sep or not label.strip() or not path.strip() or any(c in label for c in "/\\:"):
        raise ValueError(f"--voice wants label=path-to-model.onnx, got {spec!r}")
    return label.strip(), path.strip()


def main(argv: list[str] | None = None, *, synth=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--voice", action="append", default=[], required=True, metavar="label=model.onnx")
    ap.add_argument("--out", default="voice-samples")
    ap.add_argument("--text-ru", default=LINES["ru"])
    ap.add_argument("--text-en", default=LINES["en"])
    args = ap.parse_args(argv)
    if synth is None:
        from bcc.oss.piper import synthesize_ogg as synth
    exe, ffmpeg = os.environ.get("BOSSMAN_PIT_TTS_EXECUTABLE", ""), shutil.which("ffmpeg") or ""
    if synth.__module__.startswith("bcc.") and not (Path(exe).is_file() and ffmpeg):
        print("VOICE_SAMPLES=NOT_RUN\nнужны BOSSMAN_PIT_TTS_EXECUTABLE (piper) и ffmpeg в PATH")
        return 3
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows, failed = [], 0
    for spec in args.voice:
        label, model = parse_voice(spec)
        lang = language_of(model)
        text = args.text_ru if lang == "ru" else args.text_en
        if not (Path(model).is_file() and Path(model + ".json").is_file()):
            rows.append({"voice": label, "language": lang, "status": "MISSING_MODEL", "model": Path(model).name})
            failed += 1
            continue
        try:
            data = synth(text, piper_executable=exe, model_path=model, ffmpeg_executable=ffmpeg)
        except Exception as exc:  # noqa: BLE001 - one broken voice must not hide the others
            rows.append({"voice": label, "language": lang, "status": f"FAILED:{str(exc)[:40]}", "model": Path(model).name})
            failed += 1
            continue
        target = out / f"{label}.ogg"
        target.write_bytes(data)
        rows.append({"voice": label, "language": lang, "status": "OK", "file": str(target), "bytes": len(data)})
    (out / "samples.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print("VOICE_SAMPLES=" + ("OK" if not failed else "PARTIAL"))
    for row in rows:
        print(f"  {row['voice']:<18} {row['language']}  {row['status']}  {row.get('file', '')}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
