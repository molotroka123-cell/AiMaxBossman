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
import time
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


def _post(url: str, payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _chat(endpoint: str, model: str, messages: list[dict], timeout: float = 240.0, max_tokens: int = 3000) -> str:
    """OpenAI-compatible /chat/completions (llama-swap, LM Studio, vLLM)."""
    data = _post(endpoint.rstrip("/") + "/chat/completions",
                 {"model": model, "messages": messages, "temperature": 0.4, "max_tokens": max_tokens,
                  "response_format": {"type": "json_object"}}, timeout)
    return data["choices"][0]["message"]["content"]


class ReplyTruncated(RuntimeError):
    """The model hit max_tokens: the reply is an unfinished JSON object."""


def _chat_ollama(endpoint: str, model: str, messages: list[dict], timeout: float = 240.0, max_tokens: int = 3000,
                 num_ctx: int | None = None, stats: list[dict] | None = None) -> str:
    """Native Ollama /api/chat with think:false and JSON mode.

    Owner-PC audit 2026-09-27: through the OpenAI-compatible endpoint the local Qwen3.6
    did not return a spec within the run (thinking models spend the budget reasoning).
    The product's Jeff route already uses native `think: false` for the same reason
    (bcc/pit/ollama_native.py).

    num_ctx is sent only when asked for: Ollama reloads a model whose loaded context size
    differs from the request, and the shared product model on the owner PC runs at 32768.
    A fixed 16384 here evicted and reloaded ~27 GB on every generation run (rc19 audit)."""
    base = re.sub(r"/v1/?$", "", endpoint.rstrip("/"))
    options: dict = {"temperature": 0.4, "num_predict": max_tokens}
    if num_ctx:
        options["num_ctx"] = int(num_ctx)
    data = _post(base + "/api/chat",
                 {"model": model, "messages": messages, "stream": False, "think": False, "format": "json",
                  "keep_alive": "30m", "options": options},
                 timeout)
    info = {"prompt_tokens": data.get("prompt_eval_count"), "output_tokens": data.get("eval_count"),
            "load_s": round((data.get("load_duration") or 0) / 1e9, 2),
            "total_s": round((data.get("total_duration") or 0) / 1e9, 2), "done_reason": data.get("done_reason")}
    if stats is not None:
        stats.append(info)
    _log("ollama: " + " ".join(f"{k}={v}" for k, v in info.items()))
    if data.get("done_reason") == "length":
        raise ReplyTruncated(f"reply cut off at max_tokens={max_tokens} ({info['output_tokens']} tokens)")
    return (data.get("message") or {}).get("content", "")


def _log(msg: str) -> None:
    print(f"[generate_spec] {msg}", file=sys.stderr, flush=True)


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
        messages += [{"role": "user", "content": "BRIEF: a short clip in this style.\nFACTS: see the values used."},
                     {"role": "assistant", "content": json.dumps(example, ensure_ascii=False)}]
    messages.append({"role": "user", "content": f"BRIEF: {brief}\nFACTS: {json.dumps(facts or {}, ensure_ascii=False)}"})
    errors: list[str] = ["no attempt"]
    best = None
    for attempt in range(1, max(1, tries) + 1):
        t0 = time.monotonic()
        try:
            raw = chat(messages)
        except ReplyTruncated as exc:   # an unfinished object teaches nothing; ask for a shorter one
            errors = [f"{exc}: reply with a complete, shorter JSON (fewer scenes, items or voice lines)"]
            _log(f"try {attempt}: {errors[0]} after {time.monotonic() - t0:.0f} s")
            messages.append({"role": "user", "content": "Your previous reply was cut off before the JSON ended. "
                                                        "Reply with the full JSON only, shorter."})
            continue
        except Exception as exc:  # timeout / connection: report per try, keep the loop honest
            errors = [f"model call failed: {exc.__class__.__name__}: {str(exc)[:160]}"]
            _log(f"try {attempt}: {errors[0]} after {time.monotonic() - t0:.0f} s")
            continue
        _log(f"try {attempt}: {len(raw)} chars in {time.monotonic() - t0:.0f} s")
        messages.append({"role": "assistant", "content": raw})
        draft = extract_json(raw)
        if draft is None:
            errors = ["the reply was not a single JSON object"]
        else:
            errors = spec_mod.validate(draft)
            best = draft
            _log(f"try {attempt}: {'VALID' if not errors else f'{len(errors)} validator errors'}")
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
    ap.add_argument("--example", type=Path, default=HERE / "examples" / "jeff_voice_12s.json",
                    help="few-shot spec (default: the short 12 s example; the 22 s one doubles the prompt)")
    ap.add_argument("--api", choices=["auto", "ollama", "openai"], default="auto",
                    help="auto = native Ollama (think:false) when the endpoint is :11434, else OpenAI-compatible")
    ap.add_argument("--timeout", type=float, default=240.0, help="seconds per model call")
    ap.add_argument("--max-tokens", type=int, default=3000)
    ap.add_argument("--num-ctx", type=int, default=None,
                    help="Ollama context size; default: the loaded model's own (a different value forces a reload)")
    args = ap.parse_args()
    facts = json.loads(args.facts.read_text(encoding="utf-8")) if args.facts else None
    example = None if args.no_example else json.loads(args.example.read_text(encoding="utf-8"))
    api = args.api if args.api != "auto" else ("ollama" if ":11434" in args.endpoint else "openai")
    _log(f"model={args.model} api={api} timeout={args.timeout:.0f}s max_tokens={args.max_tokens} "
         f"example={'none' if example is None else args.example.name}")
    calls: list[dict] = []
    if api == "ollama":
        chat = lambda m: _chat_ollama(args.endpoint, args.model, m, args.timeout, args.max_tokens,  # noqa: E731
                                      args.num_ctx, calls)
    else:
        chat = lambda m: _chat(args.endpoint, args.model, m, args.timeout, args.max_tokens)  # noqa: E731
    spec, errors, transcript = generate(args.brief, facts, chat, args.tries, example)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if spec is not None:
        args.out.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    args.out.with_suffix(".transcript.json").write_text(json.dumps(
        {"brief": args.brief, "facts": facts, "model": args.model, "valid": not errors, "errors": errors,
         "calls": calls, "messages": transcript}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("VALID" if not errors else "INVALID:\n  " + "\n  ".join(errors))
    raise SystemExit(0 if not errors else 1)


if __name__ == "__main__":
    main()
