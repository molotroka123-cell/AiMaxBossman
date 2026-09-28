"""Brief -> scene spec with a local model (any OpenAI-compatible endpoint: Ollama, llama-swap, LM Studio).

    python tools/motion_studio/generate_spec.py "15 s teaser for ..." --model qwen3.6:35b-a3b \
        --endpoint http://127.0.0.1:11434/v1 --out spec.json [--facts facts.json]

The model only writes JSON. Every draft goes through spec.validate(); its error list is sent
back as the next user turn until the draft is valid or --tries is exhausted. Numbers on
screen must come from --facts (real data); the prompt forbids inventing statistics, and
roadmap scenes must carry a disclaimer. The best-effort result and the full transcript are
written next to --out so failures can become training data (dataset/brief_to_spec.jsonl).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import spec as spec_mod  # noqa: E402

import lottie_assets  # noqa: E402

SYSTEM = """You write scene specs for Bossman Motion Studio. Output ONE JSON object and nothing else.

Format: {"meta": {...}, "scenes": [...]}
meta: title, duration (3-60 s), bpm (70-170, default 120), key (C D E F G A B), brand, hud, voice ("am_fenrir").
Scenes are contiguous: scene[i].start == scene[i-1].end, first starts at 0, last ends at meta.duration.
Scene types and fields (keep texts SHORT, they must fit on screen):
- title: title (<=12 chars, big), kicker (<=24), typed (<=48, a terminal line), chip (<=12)
- bars: values (3..60 real numbers), headline_label (<=10), counter_label (<=12), x_from, x_to, highlight {index, text<=20}
- cards: heading (<=32), items (1..5): {t, title<=14, sub<=24, icon in agents|memory|cursor|play|plane|chart|shield|bolt|mic|code}
- grid: value (integer 1..5200), label (<=18), caption (<=44), then_title (<=16), then_sub (<=48)
- voice: name (<=12), sub (<=44), lines (1..3 things the user says), status: [{text, state: live|next}]
- roadmap: heading, disclaimer (REQUIRED, e.g. "PROJECTION · TARGETS, NOT PROMISES"), items (1..4): {t, version<=5, when<=10, lines: 1..2 strings <=22}
- logo: name (<=12), tagline (<=44)
- end_card: text (<=18), sub (<=44)
- sticker: lottie (an id from LOTTIE), text (<=18, big caption), sub (<=44)
Card icons may also be "lottie:<id>" with an id from LOTTIE (animated emoji).
Every scene may have vo: [{t, text}] - plain English voice-over, numbers spelled out, inside the scene,
not overlapping (a line lasts about 0.3 s + characters/24 s). Item times are >= 0.5 s apart.
Rules: use only numbers given in FACTS; never invent statistics. Anything not shipped is 'next'
or goes on a roadmap with a disclaimer. Put the logo near the end; an end_card may close the video.
"""
SYSTEM += "LOTTIE ids: " + ", ".join(sorted(lottie_assets.ids())) + "\n"


def _chat(endpoint: str, model: str, messages: list[dict], timeout: float = 300.0) -> str:
    body = json.dumps({"model": model, "messages": messages, "temperature": 0.4,
                       "response_format": {"type": "json_object"}}).encode()
    req = urllib.request.Request(endpoint.rstrip("/") + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())["choices"][0]["message"]["content"]


def extract_json(text: str) -> dict | None:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def generate(brief: str, facts: dict | None, chat: Callable[[list[dict]], str], tries: int = 4,
             example: dict | None = None) -> tuple[dict | None, list[str], list[dict]]:
    """Returns (spec or None, remaining errors, transcript)."""
    messages = [{"role": "system", "content": SYSTEM}]
    if example is not None:
        messages += [{"role": "user", "content": "BRIEF: 22 s launch recap for Bossman with a roadmap.\nFACTS: see the numbers used."},
                     {"role": "assistant", "content": json.dumps(example, ensure_ascii=False)}]
    messages.append({"role": "user", "content": f"BRIEF: {brief}\nFACTS: {json.dumps(facts or {}, ensure_ascii=False)}"})
    errors: list[str] = ["no attempt"]
    best = None
    for _ in range(max(1, tries)):
        raw = chat(messages)
        messages.append({"role": "assistant", "content": raw})
        draft = extract_json(raw)
        if draft is None:
            errors = ["the reply was not a single JSON object"]
        else:
            errors = spec_mod.validate(draft)
            best = draft
            if not errors:
                return draft, [], messages
        messages.append({"role": "user", "content": "Fix these problems and reply with the full corrected JSON only:\n- "
                                                    + "\n- ".join(errors[:25])})
    return best, errors, messages


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("brief")
    ap.add_argument("--model", required=True)
    ap.add_argument("--endpoint", default="http://127.0.0.1:11434/v1")
    ap.add_argument("--facts", type=Path, help="JSON with the real numbers/texts the video may use")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--tries", type=int, default=4)
    ap.add_argument("--no-example", action="store_true", help="zero-shot (to measure a fine-tuned model)")
    args = ap.parse_args()
    facts = json.loads(args.facts.read_text(encoding="utf-8")) if args.facts else None
    example = None if args.no_example else json.loads((HERE / "examples" / "bossman_32_days.json").read_text(encoding="utf-8"))
    spec, errors, transcript = generate(args.brief, facts, lambda m: _chat(args.endpoint, args.model, m),
                                        args.tries, example)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if spec is not None:
        args.out.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    args.out.with_suffix(".transcript.json").write_text(json.dumps(
        {"brief": args.brief, "facts": facts, "model": args.model, "valid": not errors, "errors": errors,
         "messages": transcript}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("VALID" if not errors else "INVALID:\n  " + "\n  ".join(errors))
    raise SystemExit(0 if not errors else 1)


if __name__ == "__main__":
    main()
