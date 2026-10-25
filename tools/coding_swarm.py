"""Coding swarm for Bossman 2.5 (owner 25.10): one task -> Kimi K3.1 Pro + Qwen 3.8 Flash + free models, answers saved.

    python tools/coding_swarm.py review --doc docs/owner/X.md --ask "..." --out <dir>
    python tools/coding_swarm.py task --ask "..." --file path [--file path ...] --out <dir> [--models kimi,qwen,...]

Chat only: repository text goes in the message, never personal data; every prompt is scanned for secret-like strings
first and refused on a hit. Keys are read in-process (7API from the Bossman vault, the rest from provider-keys.env)
and never printed. Each call records model, seconds, tokens and the cost from the published per-token price.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("tma", ROOT / "tools" / "three_model_audit.py")
tma = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tma)

# name: (base url, model, key source, usd per Mtok in, usd per Mtok out)
MODELS = {
    "kimi": ("https://7api.st/v1", "kimi-k3.1-pro", "7api", 0.10, 0.10),
    "qwen": ("https://openrouter.ai/api/v1", "qwen/qwen3.8-flash", "OPENROUTER_API_KEY", 0.15, 0.47),
    "glm": ("https://7api.st/v1", "glm-5.3-flash-uncensored", "7api", 0.028, 0.028),
    "nemotron": ("https://openrouter.ai/api/v1", "nvidia/nemotron-3-ultra-550b-a55b:free", "OPENROUTER_API_KEY", 0.0, 0.0),
    # the same Nemotron Ultra on the NVIDIA NIM free tier (provider 7 in bcc.db); used when OpenRouter :free queues
    "nemotron-nim": ("https://integrate.api.nvidia.com/v1", "nvidia/nemotron-3-ultra-550b-a55b", "NVIDIA_API_KEY", 0.0, 0.0),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "gemini-3-flash-preview", "GEMINI_API_KEY", 0.0, 0.0),
}
DEFAULT = ["kimi", "qwen", "nemotron", "gemini"]
MAX_PROMPT_CHARS = 120_000


def leaks_eval_holdout(prompt: str) -> bool:
    """TZ 2.5 M5: a sealed EVAL500 holdout scenario never reaches a coder/swarm/auditor model."""
    import sys as _sys
    tools_dir = str(Path(__file__).resolve().parent)
    if tools_dir not in _sys.path:
        _sys.path.insert(0, tools_dir)
    from eval500.holdout_guard import leaks_holdout
    return leaks_holdout(prompt)


def _call(name: str, prompt: str, max_tokens: int = 24000, timeout: float = 900) -> dict:
    # 24k: Kimi K3.1 Pro and Qwen 3.8 Flash spent all of 8k on reasoning and returned no content (25.10)
    base, model, src, pin, pout = MODELS[name]
    t0 = time.time()
    try:
        key = tma._seven_api_key() if src == "7api" else tma._env_keys().get(src, "")
        if not key:
            raise RuntimeError("no key")
        body = {"model": model, "stream": True, "max_tokens": max_tokens, "temperature": 0.2,
                "messages": [{"role": "user", "content": prompt}]}
        if "openrouter" in base:
            body["stream_options"] = {"include_usage": True}
        req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                              "User-Agent": "Bossman/1.0", "X-Title": "bossman-coding-swarm"})
        parts, thinking, usage = [], [], {}
        with urllib.request.urlopen(req, timeout=timeout) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:") or line == "data: [DONE]":
                    continue
                try:
                    obj = json.loads(line[5:])
                except ValueError:
                    continue
                usage = obj.get("usage") or usage
                for ch in obj.get("choices") or []:
                    delta = ch.get("delta") or {}
                    parts.append(delta.get("content") or "")
                    thinking.append(delta.get("reasoning_content") or delta.get("reasoning") or "")
        text = "".join(parts)
        tin = int(usage.get("prompt_tokens") or len(prompt) // 4)
        tout = int(usage.get("completion_tokens") or len(text) // 4)
        return {"model": name, "id": model, "ok": bool(text.strip()), "seconds": round(time.time() - t0, 1),
                "tokens_in": tin, "tokens_out": tout, "tokens_estimated": not usage,
                "reasoning_chars": len("".join(thinking)), "reasoning": "".join(thinking),
                "cost_usd": round(tin / 1e6 * pin + tout / 1e6 * pout, 6), "text": text}
    except (urllib.error.URLError, OSError, RuntimeError, ValueError) as exc:
        return {"model": name, "id": model, "ok": False, "seconds": round(time.time() - t0, 1), "cost_usd": 0.0,
                "error": type(exc).__name__ + ": " + str(exc)[:200]}


# Owner 25.10: «если боссману доступен thinking process Kimi — пусть учится у него». Reasoning traces are kept LOCALLY
# (never in git) with provenance; using them for training waits for a check of the provider terms (Moonshot / 7API).
DATASET = Path(r"D:\Bossman\datasets\cloud-reasoning")


def _save_reasoning(prompt: str, results: list[dict], label: str) -> None:
    rows = [{"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "label": label, "model": r["model"], "model_id": r["id"],
             "prompt": prompt, "reasoning": r["reasoning"], "answer": r.get("text", ""),
             "training_use": "PENDING_TERMS_CHECK"} for r in results if r.get("reasoning")]
    if rows:
        DATASET.mkdir(parents=True, exist_ok=True)
        with open(DATASET / f"{time.strftime('%Y%m%d')}.jsonl", "a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def run(prompt: str, models: list[str], out: Path, label: str) -> dict:
    if tma.SECRET.search(prompt):
        raise SystemExit("secret-like string in the prompt: refused")
    if leaks_eval_holdout(prompt):
        raise SystemExit("EVAL500 sealed holdout text or location in the prompt: refused (TZ 2.5 M5)")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise SystemExit(f"prompt too long ({len(prompt)} > {MAX_PROMPT_CHARS})")
    unknown = [m for m in models if m not in MODELS]
    if unknown:
        raise SystemExit(f"unknown models: {unknown}")
    with ThreadPoolExecutor(max_workers=len(models)) as ex:
        results = list(ex.map(lambda m: _call(m, prompt), models))
    out.mkdir(parents=True, exist_ok=True)
    _save_reasoning(prompt, results, label)
    for r in results:
        (out / f"{label}-{r['model']}.md").write_text(r.get("text") or r.get("error", ""), encoding="utf-8")
    doc = {"schema": "bossman.coding-swarm.v1", "label": label, "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "prompt_chars": len(prompt), "calls": [{k: v for k, v in r.items() if k not in ("text", "reasoning")} for r in results],
           "cost_usd_total": round(sum(r.get("cost_usd", 0) for r in results), 6)}
    (out / f"{label}-receipt.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return doc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mode", choices=["review", "task"])
    ap.add_argument("--ask", required=True)
    ap.add_argument("--doc")
    ap.add_argument("--file", action="append", default=[])
    ap.add_argument("--models", default=",".join(DEFAULT))
    ap.add_argument("--out", required=True)
    ap.add_argument("--label", default="")
    a = ap.parse_args(argv)
    paths = ([a.doc] if a.doc else []) + a.file
    ctx = "".join(f"\n\n### {p}\n```\n{(ROOT / p).read_text(encoding='utf-8')}\n```" for p in paths)
    doc = run(a.ask + ctx, a.models.split(","), Path(a.out), a.label or a.mode)
    print(json.dumps([(c["model"], c["ok"], c["seconds"], c.get("cost_usd")) for c in doc["calls"]] +
                     [("total_usd", doc["cost_usd_total"])], ensure_ascii=False))
    return 0 if any(c["ok"] for c in doc["calls"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
