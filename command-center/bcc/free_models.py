"""Keep Bossman's OpenRouter ``:free`` workers alive by themselves (owner 22.10: «это надо обновлять»).

OpenRouter retires and rotates free models (22.10: qwen3-coder:free, llama-3.3-70b:free, minimax-m3.1-flash:free gone;
nemotron-3-super-120b:free 404). Instead of a hand-edited list, before a worker run we:
  1. read the live catalogue ``GET /api/v1/models`` (ids ending ``:free``);
  2. ping the configured model with a 5-token request;
  3. if it is gone or answers 403/404/429/5xx, swap in the first live free model from ``PREFERRED`` that answers.
Results are cached (``TTL_S``) and written to ``<data_dir>/free-models/status.json`` for the pult/UI.
Only ``:free`` ids are ever chosen, so the swap can never make a call paid.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

API = "https://openrouter.ai/api/v1"
TTL_S = 6 * 3600
#: Replacement order (best coder/agent first). Unknown or unlisted ids are skipped automatically.
PREFERRED = (
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "poolside/laguna-s-2.1:free",
    "cohere/north-mini-code:free",
    "nvidia/nemotron-3.5-super-vl:free",
    "dots-studio/dots-3-note-preview:free",
    "recursive/flint-v1.0:free",
    "nvidia/nemotron-3.5-lightning:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
)
_EXCLUDE = ("content-safety",)      # not general workers; banned families via bcc.pit.model_policy

_cache: dict = {"at": 0.0, "live": [], "ping": {}}


def _get(url: str, key: str, timeout: float = 30.0) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def live_free_ids(key: str, *, fetch: Callable[[str, str], dict] = _get) -> list[str]:
    data = fetch(f"{API}/models", key).get("data", [])
    return [m["id"] for m in data if str(m.get("id", "")).endswith(":free")]


def ping(model: str, key: str, timeout: float = 40.0) -> str:
    """'ok' or the HTTP status / error class. A tiny request; free ids cost $0."""
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": "Reply: OK"}],
                       "max_tokens": 5}).encode()
    req = urllib.request.Request(f"{API}/chat/completions", data=body, method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()
        return "ok"
    except urllib.error.HTTPError as e:
        return str(e.code)
    except Exception as e:  # noqa: BLE001 — network trouble = not usable now
        return type(e).__name__


def choose(current: str, live: list[str], probe: Callable[[str], str], tried: dict[str, str]) -> tuple[str, str]:
    """(model_to_use, reason). Keeps ``current`` when it is listed and answers."""
    def ok(mid: str) -> bool:
        if mid not in tried:
            tried[mid] = probe(mid)
        return tried[mid] == "ok"
    if current in live and ok(current):
        return current, "ok"
    why = "retired" if current not in live else f"unavailable:{tried.get(current)}"
    order = [m for m in PREFERRED if m in live] + [m for m in live if m not in PREFERRED]
    from .pit.model_policy import banned_refusal  # Liquid/LFM ban lives in one place (model policy)
    for mid in order:
        if mid != current and not any(x in mid for x in _EXCLUDE) and not banned_refusal(mid) and ok(mid):
            return mid, f"{why} -> {mid}"
    return current, f"{why}; no live free replacement"


def refresh_workers(workers: dict[str, dict], key: str, data_dir: Path | None = None, *, force: bool = False,
                    fetch: Callable[[str, str], dict] = _get, probe: Callable[[str, str], str] = ping) -> dict:
    """Swap dead OpenRouter ``:free`` models in ``workers`` in place. Cached for TTL_S unless ``force``."""
    now = time.time()
    if not force and now - _cache["at"] < TTL_S and _cache["live"]:
        return {"cached": True, "changes": []}
    live = live_free_ids(key, fetch=fetch)
    tried: dict[str, str] = {}
    changes, report = [], {}
    for wid, spec in workers.items():
        model = str(spec.get("model", ""))
        if not model.endswith(":free") or "openrouter.ai" not in str(spec.get("endpoint", "openrouter.ai")):
            continue
        spec.setdefault("configured_model", model)
        new, reason = choose(model, live, lambda m: probe(m, key), tried)
        report[wid] = {"configured": spec["configured_model"], "using": new, "reason": reason}
        if new != model:
            spec["model"] = new
            changes.append({"worker": wid, "from": model, "to": new, "reason": reason})
    _cache.update(at=now, live=live, ping=tried)
    status = {"checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "live_free": len(live),
              "working": sorted(m for m, s in tried.items() if s == "ok"), "workers": report, "changes": changes}
    if data_dir is not None:
        out = Path(data_dir) / "free-models"
        out.mkdir(parents=True, exist_ok=True)
        (out / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
    return status
