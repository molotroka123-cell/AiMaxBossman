"""Does a local ollama model really hold N tokens of context? Probe before raising the chat window.

    python tools/context_probe.py --model gemma4-31b-uncensored:latest --target 126976 [--apply --model-id 98]

Builds a prompt of ~target tokens: a canary code at the very start, neutral filler, and a question at the
end asking for the code. Sends it with num_ctx=target to ollama (127.0.0.1:11434). PASS only when the
model echoes the canary AND ollama reports prompt_eval_count >= 85 % of the target (no truncation).
With --apply (and the Bossman model id) the registry row's context_window is set through
PATCH /api/models/<id> — only on PASS, never otherwise. Receipt: <data>/context-probes/.
Loads the model: run it when the generation queue is empty (owner rule: never slow a running render).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import string
import sys
import time
import urllib.request
from pathlib import Path

OLLAMA = "http://127.0.0.1:11434"
# Measured 23.10 on gemma4-31b: 355 304 chars of this filler = 63 491 prompt tokens (the old «~3 chars/token» guess built
# a prompt of HALF the target, so a 124k probe silently tested 63k).
CHARS_PER_TOKEN = 5.6
FILLER = ("Запись {i}: температура {t} градусов, влажность {h} процентов, давление {p} мм, ветер {w} м/с. "
          "Record {i}: temperature {t}, humidity {h}, pressure {p}, wind {w}.")


def build_prompt(target_tokens: int, canary: str) -> str:
    rnd = random.Random(7)
    head = f"СЕКРЕТНЫЙ КОД ДЛЯ ПРОВЕРКИ ПАМЯТИ: {canary}. Запомни его; в самом конце я спрошу этот код.\n\n"
    parts, approx = [head], len(head) / CHARS_PER_TOKEN
    i = 0
    while approx < target_tokens * 0.92:
        line = FILLER.format(i=i, t=rnd.randint(-30, 40), h=rnd.randint(10, 99), p=rnd.randint(700, 790), w=rnd.randint(0, 25))
        parts.append(line + "\n")
        approx += len(line) / CHARS_PER_TOKEN
        i += 1
    parts.append("\n\nВОПРОС: какой СЕКРЕТНЫЙ КОД был в самом начале? Ответь только кодом.")
    return "".join(parts)


def probe(model: str, target: int, timeout: int = 1800) -> dict:
    canary = "".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(10))
    prompt = build_prompt(target, canary)
    body = json.dumps({"model": model, "prompt": prompt, "stream": False,
                       "options": {"num_ctx": target, "temperature": 0, "num_predict": 32}}).encode()
    req = urllib.request.Request(OLLAMA + "/api/generate", data=body, headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        doc = json.load(resp)
    answer = str(doc.get("response") or "")
    evaluated = int(doc.get("prompt_eval_count") or 0)
    ok = canary in answer and evaluated >= target * 0.85
    return {"model": model, "target_num_ctx": target, "prompt_chars": len(prompt), "prompt_eval_count": evaluated,
            "canary_recalled": canary in answer, "verdict": "PASS" if ok else "FAIL", "seconds": round(time.time() - t0, 1),
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def _data_dir() -> Path:
    return Path(os.environ.get("BCC_DATA_DIR") or Path(os.environ.get("LOCALAPPDATA", ".")) / "Bossman" / "CommandCenter")


def apply(model_id: int, context_window: int) -> dict:
    token = (_data_dir() / "token").read_text(encoding="utf-8").strip()
    req = urllib.request.Request(f"http://127.0.0.1:8801/api/models/{model_id}",
                                 data=json.dumps({"context_window": context_window}).encode(),
                                 headers={"X-BCC-Token": token, "Content-Type": "application/json"}, method="PATCH")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", required=True)
    ap.add_argument("--target", type=int, default=126976, help="num_ctx to prove (124k = 126976)")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--model-id", type=int, default=None, help="Bossman registry id for --apply")
    a = ap.parse_args(argv)
    res = probe(a.model, a.target)
    out = _data_dir() / "context-probes"
    out.mkdir(parents=True, exist_ok=True)
    safe = a.model.replace("/", "_").replace(":", "_")
    (out / f"{safe}-{time.strftime('%Y%m%d-%H%M')}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1),
                                                                      encoding="utf-8", newline="\n")
    print(json.dumps(res, ensure_ascii=False))
    if a.apply and res["verdict"] == "PASS" and a.model_id:
        print("applied:", apply(a.model_id, a.target).get("context_window"))
    return 0 if res["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
