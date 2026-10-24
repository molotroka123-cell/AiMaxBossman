"""Time-to-first-token of local models on a call-shaped turn (the stage that made a call reply take 5-11 s, 24.10).

    python tools/call_llm_ttft.py [--models a,b,c] [--out docs/audits/2026-10-24-voice/llm-ttft.json]

Streaming /api/chat on the local Ollama, a ~1k-token Russian call persona + a short caller line, two runs per model
(the first may include loading the weights from D:). Records load ms, TTFT ms, tokens/s and the reply's first words
(synthetic prompt only). Nothing leaves the PC.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path

OLLAMA = "http://127.0.0.1:11434"
SYSTEM = ("Ты — голосовой ассистент владельца в телефонном звонке. Отвечай по-русски, коротко, одним-двумя "
          "предложениями, разговорно, без списков и разметки, как живой человек по телефону. ") + (
    "Правила разговора: не перебивай, уточняй, если не расслышал, говори числа словами, не выдумывай факты, "
    "если не знаешь — так и скажи, в конце звонка кратко повтори договорённости. " * 12)
CALLER = "Привет, слушай, ты можешь напомнить, во сколько у нас завтра встреча?"
THINK = False


def ttft(model: str) -> dict:
    # think=False: thinking models otherwise spend the whole budget on hidden reasoning (first run 24.10: empty replies)
    body = {"model": model, "stream": True, "keep_alive": "10m", "think": THINK, "options": {"num_predict": 64, "temperature": 0.6},
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": CALLER}]}
    req = urllib.request.Request(OLLAMA + "/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    t0, first, text, last = time.perf_counter(), None, "", {}
    with urllib.request.urlopen(req, timeout=600) as r:
        for line in r:
            if not line.strip():
                continue
            d = json.loads(line)
            piece = (d.get("message") or {}).get("content") or ""
            if piece.strip() and first is None:
                first = time.perf_counter()
            text += piece
            if d.get("done"):
                last = d
                break
    ev, ed = last.get("eval_count") or 0, (last.get("eval_duration") or 0) / 1e9
    return {"ttft_ms": round((first - t0) * 1000) if first else None, "load_ms": round((last.get("load_duration") or 0) / 1e6),
            "prompt_tokens": last.get("prompt_eval_count"), "prompt_ms": round((last.get("prompt_eval_duration") or 0) / 1e6),
            "tok_per_s": round(ev / ed, 1) if ed else None, "reply_head": text.strip()[:80]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--models", default="gemma4-31b-uncensored:latest,bossman-fast-qwen36-35b-a3b-q5:latest,"
                                        "bossman-uncensored-chat:latest,qwen3-coder:30b,ace-qwen3-17b:latest")
    ap.add_argument("--out", default="docs/audits/2026-10-24-voice/llm-ttft.json")
    a = ap.parse_args(argv)
    rows = []
    for m in [x.strip() for x in a.models.split(",") if x.strip()]:
        try:
            runs = [ttft(m), ttft(m)]
        except Exception as exc:  # noqa: BLE001
            runs = [{"error": f"{type(exc).__name__}: {exc}"[:200]}]
        rows.append({"model": m, "cold": runs[0], "warm": runs[-1]})
        print(m, json.dumps(runs, ensure_ascii=False), flush=True)
        try:                                                     # free the RAM before the next model
            urllib.request.urlopen(urllib.request.Request(OLLAMA + "/api/generate", data=json.dumps(
                {"model": m, "keep_alive": 0}).encode(), headers={"Content-Type": "application/json"}), timeout=60).read()
        except Exception:  # noqa: BLE001
            pass
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "caller": CALLER,
                               "system_chars": len(SYSTEM), "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
