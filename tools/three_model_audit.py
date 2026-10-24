"""Mini-audit of a decision by three models instead of a clarifying question (owner rule 24.10 night).

    python tools/three_model_audit.py --goal "..." --question "..." --option A="..." --option B="..." [--out receipt.json]

Each model gets the goal, the question and the options and must answer with one JSON line {"choice": "<key>",
"why": "..."}; the majority wins, a tie goes to the first model in the list (Kimi K3.1 Pro). Chat only: no tools, no
files. The text is scanned for secret-like strings first and refused on a hit; never put personal data in it.
Keys are read in-process (7API from the Bossman vault, OpenRouter/Gemini from provider-keys.env) and never printed.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("LOCALAPPDATA", "")) / "Bossman" / "CommandCenter"
KEYS = Path(os.environ.get("LOCALAPPDATA", "")) / "Bossman" / "keys" / "provider-keys.env"
SECRET = re.compile(r"(sk-[A-Za-z0-9]{16,}|AIza[0-9A-Za-z_-]{20,}|ghp_[A-Za-z0-9]{20,}|hf_[A-Za-z0-9]{20,}|"
                    r"BEGIN [A-Z ]*PRIVATE KEY|\b\d{8,10}:[A-Za-z0-9_-]{30,}\b)")
# (label, base url, model, key source) — order = tie-break priority
MODELS = [
    ("kimi-k3.1-pro", "https://7api.st/v1", "kimi-k3.1-pro", "7api"),
    ("glm-5.3-flash", "https://7api.st/v1", "glm-5.3-flash-uncensored", "7api"),
    ("nemotron-ultra-free", "https://openrouter.ai/api/v1", "nvidia/nemotron-3-ultra-550b-a55b:free", "OPENROUTER_API_KEY"),
]
FALLBACK = ("gemini-3-flash", "https://generativelanguage.googleapis.com/v1beta/openai", "gemini-3-flash-preview",
            "GEMINI_API_KEY")


def _env_keys() -> dict:
    out = {}
    if KEYS.is_file():
        for line in KEYS.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _seven_api_key() -> str:
    sys.path.insert(0, str(ROOT / "command-center"))
    from bcc.secrets import Vault
    db = sqlite3.connect(f"file:{DATA / 'bcc.db'}?mode=ro", uri=True)
    try:
        enc = db.execute("select api_key_enc from providers where id=15").fetchone()[0]
    finally:
        db.close()
    return Vault(DATA).decrypt(enc) or ""


def _chat(base: str, model: str, key: str, prompt: str, timeout: float = 240) -> str:
    """Streamed (7API drops non-streamed answers over ~100 s with 524); returns the concatenated content."""
    body = json.dumps({"model": model, "stream": True, "max_tokens": 4000, "temperature": 0.2,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(base + "/chat/completions", data=body, method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                          "User-Agent": "Bossman/1.0", "X-Title": "bossman-three-model-audit"})
    parts = []
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:") or line == "data: [DONE]":
                continue
            try:
                delta = json.loads(line[5:])["choices"][0].get("delta") or {}
            except (ValueError, KeyError, IndexError):
                continue
            parts.append(delta.get("content") or "")
    return "".join(parts)


def parse_choice(text: str, keys: list[str]) -> tuple[str | None, str]:
    """The last {"choice": ...} object naming a known option; reasoning models may think before it."""
    for m in reversed(list(re.finditer(r"\{[^{}]*\"choice\"[^{}]*\}", text or "", re.S))):
        try:
            obj = json.loads(m.group(0))
        except ValueError:
            continue
        if str(obj.get("choice")) in keys:
            return str(obj["choice"]), str(obj.get("why", ""))[:600]
    return None, ""


def decide(votes: list[dict], keys: list[str]) -> str | None:
    """Majority of valid votes; a tie goes to the earliest model that voted for one of the tied options."""
    valid = [v["choice"] for v in votes if v.get("choice") in keys]
    if not valid:
        return None
    counts = collections.Counter(valid)
    top = max(counts.values())
    return next(c for c in valid if counts[c] == top)


def audit(goal: str, question: str, options: dict[str, str]) -> dict:
    text = goal + question + json.dumps(options, ensure_ascii=False)
    if SECRET.search(text):
        raise SystemExit("secret-like string in the question: refused")
    prompt = ("Ты независимый аудитор решения. Цель владельца: " + goal + "\nВопрос: " + question + "\nВарианты:\n"
              + "\n".join(f"{k}: {v}" for k, v in options.items())
              + "\nВыбери лучший вариант для цели (учитывай риск, обратимость, безопасность, стоимость). Ответь в конце "
                'ОДНОЙ строкой JSON: {"choice": "<ключ варианта>", "why": "<коротко по-русски>"}')
    env = _env_keys()
    keys = list(options)
    votes = []
    for label, base, model, src in MODELS + [FALLBACK]:
        if label == FALLBACK[0] and sum(1 for v in votes if v.get("choice")) >= 3:
            break
        t0 = time.time()
        try:
            key = _seven_api_key() if src == "7api" else env.get(src, "")
            if not key:
                raise RuntimeError("no key")
            choice, why = parse_choice(_chat(base, model, key, prompt), keys)
            votes.append({"model": label, "choice": choice, "why": why, "seconds": round(time.time() - t0, 1)})
        except (urllib.error.URLError, OSError, RuntimeError, ValueError) as exc:
            votes.append({"model": label, "choice": None, "error": type(exc).__name__ + ": " + str(exc)[:120]})
    winner = decide(votes, keys)
    return {"schema": "bossman.three-model-audit.v1", "goal": goal, "question": question, "options": options,
            "votes": votes, "decision": winner, "decision_text": options.get(winner) if winner else None,
            "verdict": "DECIDED" if winner else "INSUFFICIENT_EVIDENCE",
            "at": time.strftime("%Y-%m-%dT%H:%M:%S")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--goal", required=True)
    ap.add_argument("--question", required=True)
    ap.add_argument("--option", action="append", required=True, help='KEY="text" (2 or more)')
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    options = dict(o.split("=", 1) for o in a.option)
    if len(options) < 2:
        raise SystemExit("need at least 2 options")
    doc = audit(a.goal, a.question, options)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print(json.dumps({"decision": doc["decision"], "votes": [(v["model"], v.get("choice")) for v in doc["votes"]]},
                     ensure_ascii=False))
    return 0 if doc["decision"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
