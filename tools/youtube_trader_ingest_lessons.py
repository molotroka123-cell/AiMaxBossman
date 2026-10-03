#!/usr/bin/env python3
"""Distill a public trading video into evidence-linked, UNVERIFIED study notes.

This pass ranks transferable ideas above date-specific opinions and stream noise.
It never converts the notes into an order or verified strategy. Exact transcript
quotes are checked locally before a note is kept.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
OLLAMA = "http://127.0.0.1:11434"
DEFAULT_MODEL = "bossman-community-qwen-uncensored:latest"
SYSTEM = """You are an evidence-first trading-video study curator. You do not give trading advice or place trades.
Transcript lines are noisy auto-captions. Extract at most 3 useful observations for this window.
Classify each as exactly one of: transferable_principle, dated_view, teaching_example, noise.
transferable_principle means a reusable, observable if/then method explicitly taught here.
dated_view means a time-sensitive forecast, bias, target, or personal opinion.
teaching_example means a worked explanation or chart example, not a proven rule.
noise means greetings, chat, giveaways, jokes, repetition, or unrelated promotion.
Rate learning_value 1..5 (usefulness only, not truth). Prefer methods with conditions, trigger, invalidation, and risk.
Copy an exact short quote from the supplied lines and cite its integer line id. Do not repair caption errors or invent context.
If a number, asset, timeframe, venue, trigger, invalidation, or risk is not explicit, use null. Do not turn a forecast into an entry rule.
Return JSON only: {"items":[{"line_id":0,"quote":"...","class":"...","learning_value":1,"idea":"...","conditions":"... or null","trigger":"... or null","invalidation":"... or null","risk_rule":"... or null","asset":"... or null","timeframe":"... or null","uncertainty":"..."}]}"""
WORD = re.compile(r"[a-z0-9]+")


def normalize(text: str) -> str:
    return " ".join(WORD.findall((text or "").casefold()))


def ollama(model: str, user: str, timeout: int = 240) -> tuple[dict[str, Any], float]:
    payload = {"model": model, "stream": False, "think": False, "keep_alive": "10m",
               "format": "json", "options": {"temperature": 0},
               "messages": [{"role": "system", "content": SYSTEM},
                            {"role": "user", "content": user}]}
    req = urllib.request.Request(f"{OLLAMA}/api/chat", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    start = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as response:
        result = json.loads(response.read().decode("utf-8"))
    content = (result.get("message") or {}).get("content") or "{}"
    return json.loads(content), round(time.monotonic() - start, 2)


def run(run_dir: Path, *, model: str = DEFAULT_MODEL, window_seconds: int = 240,
        transcript_source: str = "auto") -> dict[str, Any]:
    from tools.youtube_trader_ingest import parse_vtt
    from tools.youtube_trader_ingest import Cue

    run_dir = run_dir.resolve()
    if transcript_source not in {"auto", "captions", "asr", "both"}:
        raise ValueError("transcript_source must be auto, captions, asr, or both")
    vtt = next(iter(sorted(run_dir.glob("subs.*.vtt"))), None)
    asr_path = run_dir / "asr.segments.json"
    if transcript_source == "auto":
        transcript_source = "both" if asr_path.is_file() and vtt is not None else (
            "asr" if asr_path.is_file() else "captions")
    sources: list[tuple[str, list[Any]]] = []
    if transcript_source in {"captions", "both"}:
        if vtt is None:
            if transcript_source == "captions":
                raise FileNotFoundError("subtitle VTT not found")
        else:
            cap_cues = parse_vtt(vtt.read_text(encoding="utf-8", errors="replace"))
            if cap_cues:
                sources.append(("original_subtitles", cap_cues))
    if transcript_source in {"asr", "both"}:
        if not asr_path.is_file():
            if transcript_source == "asr":
                raise FileNotFoundError("local asr.segments.json not found")
        else:
            raw_asr = json.loads(asr_path.read_text(encoding="utf-8"))
            if isinstance(raw_asr, dict):
                raw_asr = raw_asr.get("segments", [])
            asr_cues = [Cue(float(row["start"]), float(row["end"]), str(row["text"]).strip())
                        for row in raw_asr if isinstance(row, dict) and row.get("text")]
            if asr_cues:
                sources.append(("local_asr", asr_cues))
    if not sources:
        raise ValueError("no usable caption or local ASR transcript")
    cue_rows = sorted(((cue.start, source, cue) for source, rows in sources for cue in rows),
                      key=lambda row: (float(row[0]), row[1]))
    cues = [(index, source, cue) for index, (_, source, cue) in enumerate(cue_rows)]
    groups: list[list[tuple[int, str, Any]]] = []
    group: list[tuple[int, str, Any]] = []
    start = float(cues[0][2].start)
    for index, source, cue in cues:
        if cue.start - start >= window_seconds and group:
            groups.append(group)
            group, start = [], float(cue.start)
        group.append((index, source, cue))
    if group:
        groups.append(group)

    accepted: list[dict[str, Any]] = []
    rejected = 0
    latencies: list[float] = []
    for chunk in groups:
        lines = "\n".join(f"[{i}] [{source}] {cue.text}" for i, source, cue in chunk)
        prompt = (f"Video window {chunk[0][2].start:.1f}-{chunk[-1][2].end:.1f}s. "
                  "Separate reusable teaching from that day's outlook and irrelevant stream chatter.\n"
                  "Audio transcript and source captions may disagree. Do not silently merge or fix them; "
                  "cite the line whose exact wording supports each observation.\n"
                  "Transcript lines (id, text):\n" + lines)
        response, elapsed = ollama(model, prompt)
        latencies.append(elapsed)
        by_id = {i: (source, cue) for i, source, cue in chunk}
        for item in response.get("items", []) if isinstance(response.get("items"), list) else []:
            try:
                line_id = int(item["line_id"])
                value = int(item["learning_value"])
                category = item["class"]
                quote = str(item["quote"]).strip()
            except (KeyError, TypeError, ValueError):
                rejected += 1
                continue
            if category not in {"transferable_principle", "dated_view", "teaching_example", "noise"} or not 1 <= value <= 5:
                rejected += 1
                continue
            context = " ".join(by_id[i][1].text for i in (line_id - 1, line_id, line_id + 1) if i in by_id)
            words = normalize(quote)
            if line_id not in by_id or len(words.split()) < 4 or words not in normalize(context):
                rejected += 1
                continue
            source, cue = by_id[line_id]
            accepted.append({"timestamp_seconds": round(float(cue.start), 2),
                             "line_id": line_id, "quote": quote, "class": category,
                             "evidence_source": source,
                             "learning_value": value, "idea": str(item.get("idea") or "").strip(),
                             "conditions": item.get("conditions"), "trigger": item.get("trigger"),
                             "invalidation": item.get("invalidation"), "risk_rule": item.get("risk_rule"),
                             "asset": item.get("asset"), "timeframe": item.get("timeframe"),
                             "uncertainty": str(item.get("uncertainty") or ""),
                             "evidence_status": "UNVERIFIED_TRANSCRIPT_CANDIDATE"})
        print(json.dumps({"window_start": round(float(chunk[0][2].start), 1),
                          "accepted_total": len(accepted), "seconds": elapsed}), flush=True)

    video_id = run_dir.name
    payload = {"schema": "bossman.youtube-study-notes/1", "video_id": video_id,
               "model": model, "source": [name for name, _ in sources], "window_seconds": window_seconds,
               "windows": len(groups), "accepted_items": len(accepted), "rejected_items": rejected,
               "latency_seconds": latencies, "status": "UNVERIFIED_NOT_PROMOTED",
               "items": accepted}
    out = run_dir / "study_notes.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--window-seconds", type=int, default=240)
    parser.add_argument("--transcript-source", choices=("auto", "captions", "asr", "both"), default="auto")
    args = parser.parse_args()
    if args.window_seconds < 60:
        parser.error("--window-seconds must be >= 60")
    result = run(args.run_dir, model=args.model, window_seconds=args.window_seconds,
                 transcript_source=args.transcript_source)
    print(json.dumps({k: result[k] for k in ("video_id", "windows", "accepted_items", "rejected_items", "status")},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
