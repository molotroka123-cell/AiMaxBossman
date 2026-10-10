"""Reviewer/judge model calls for the North Star ladder harness (10.10.2026). Keys never printed or written.

Providers: OpenRouter (paid models only under the caller's cap), Google AI Studio (free tier, two keys),
NVIDIA NIM (free). Every call appends one line to <evidence>/spend.jsonl: provider, model, tokens, cost (USD,
OpenRouter-reported where available, 0 for free tiers), seconds, purpose. The ledger never holds prompts or keys.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

KEYS_FILE = Path(os.environ.get("LOCALAPPDATA", "")) / "Bossman" / "keys" / "provider-keys.env"
ENDPOINTS = {
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
    "nvidia": "https://integrate.api.nvidia.com/v1/chat/completions",
}
KEY_NAMES = {"openrouter": ["OPENROUTER_API_KEY"], "gemini": ["GEMINI_API_KEY", "GEMINI_API_KEY_2"],
             "nvidia": ["NVIDIA_API_KEY"]}


def _key(name: str) -> str | None:
    try:
        for line in KEYS_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"') or None
    except OSError:
        return None
    return os.environ.get(name) or None


def _post(url: str, key: str, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def openrouter_usage() -> dict:
    """The OpenRouter key's own usage counters (USD). Covers every session using this key, not only ours."""
    key = _key("OPENROUTER_API_KEY")
    req = urllib.request.Request("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8")).get("data", {})
    return {k: data.get(k) for k in ("usage", "usage_daily", "usage_weekly", "limit", "limit_remaining")}


def extract_json(text: str) -> dict:
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])


def chat(provider: str, model: str, prompt: str, *, ledger: Path, purpose: str, system: str = "",
         json_mode: bool = True, max_tokens: int = 4000, timeout: float = 240.0, extra: dict | None = None) -> dict:
    """One completion. Returns {text, usage, cost_usd, provider, model, seconds}; raises on total failure."""
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    body: dict = {"model": model, "messages": messages, "temperature": 0, "max_tokens": max_tokens}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if provider == "openrouter":
        body["usage"] = {"include": True}
    body.update(extra or {})
    errors = []
    for name in KEY_NAMES[provider]:
        key = _key(name)
        if not key:
            continue
        for attempt in range(3):
            t0 = time.monotonic()
            try:
                data = _post(ENDPOINTS[provider], key, body, timeout)
            except urllib.error.HTTPError as exc:
                errors.append(f"{name}: HTTP {exc.code}")
                if exc.code in (429, 500, 502, 503):
                    time.sleep(15 * (attempt + 1))
                    continue
                break
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                errors.append(f"{name}: {type(exc).__name__}")
                time.sleep(5)
                continue
            usage = data.get("usage") or {}
            cost = float(usage.get("cost") or 0.0) if provider == "openrouter" else 0.0
            text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            row = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "provider": provider, "model": model,
                   "key_slot": name, "purpose": purpose, "prompt_tokens": usage.get("prompt_tokens"),
                   "completion_tokens": usage.get("completion_tokens"), "cost_usd": cost,
                   "seconds": round(time.monotonic() - t0, 1)}
            ledger.parent.mkdir(parents=True, exist_ok=True)
            with ledger.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
            return {"text": text, "usage": usage, "cost_usd": cost, "provider": provider, "model": model,
                    "seconds": row["seconds"]}
    raise RuntimeError(f"{provider}/{model} failed: {errors[-4:]}")
