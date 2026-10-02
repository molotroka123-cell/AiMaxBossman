"""Claude-free route ladder for the supervised learning loop (cheapest tier first).

Tiers, in order:
  0 deterministic  — no LLM at all (e.g. K1m6a re-verification);
  1 local          — Ollama models on this machine;
  2 free_cloud     — OpenRouter ``:free`` models whose price is verified 0/0 LIVE in the catalog;
  3 max_cloud      — ONE capped paid model (default ``z-ai/glm-5.3-flash``), only after the free
                     tier failed (or for an explicit judge/replan role), under a HARD daily and
                     per-cycle USD cap reserved BEFORE the call (worst case = max_tokens x price).
Anthropic/Claude is never a tier: its hosts are refused by config and blocked in-process by
an audit hook that also counts any attempt (the readiness gate requires zero).

The owner changes the ladder in ``<state_dir>/ladder.json`` (created with defaults on first
run), e.g. ``"daily_cap_usd": 0.5`` -> ``1.0``. Cloud is skipped — the loop keeps running
locally — when the cap, a rate limit or a missing key blocks it.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

OPENROUTER = "https://openrouter.ai/api/v1"
BLOCKED_HOST_WORDS = ("anthropic.com", "claude.ai", "anthropic")
BLOCKED_MODEL_WORDS = ("claude", "anthropic")


@dataclass
class LadderConfig:
    local_models: list[str] = field(default_factory=lambda: ["bossman-fast-qwen36-35b-a3b-q5:latest",
                                                              "bossman-main-qwen38-27b-q5:latest"])
    local_base_url: str = "http://127.0.0.1:11434/v1"
    free_models: list[str] = field(default_factory=lambda: [
        "qwen/qwen3.8-27b:free", "nvidia/nemotron-3-super-120b-a12b:free",
        "google/gemma-4-31b-it:free", "nvidia/nemotron-3-ultra-550b-a55b:free"])
    cloud_base_url: str = OPENROUTER
    max_model: str = "z-ai/glm-5.3-flash"
    max_price_in_per_m: float = 0.15      # refuse if the live price is higher
    max_price_out_per_m: float = 0.50
    daily_cap_usd: float = 0.50
    per_cycle_cap_usd: float = 0.05
    max_tokens: int = 800
    allow_free_cloud: bool = True
    allow_max_cloud: bool = True
    key_env: str = "OPENROUTER_API_KEY"
    key_file: str = ""                     # optional env-file with KEY=value (never logged)

    @classmethod
    def load(cls, state_dir: Path) -> "LadderConfig":
        path = Path(state_dir) / "ladder.json"
        if not path.is_file():
            path.write_text(json.dumps(asdict(cls()), indent=2), encoding="utf-8")
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
        cfg = cls(**known)
        cfg.validate()
        return cfg

    def validate(self) -> None:
        for m in [*self.local_models, *self.free_models, self.max_model]:
            if any(w in m.lower() for w in BLOCKED_MODEL_WORDS):
                raise ValueError(f"Claude/Anthropic model refused in ladder: {m}")
        for url in (self.local_base_url, self.cloud_base_url):
            if any(w in url.lower() for w in BLOCKED_HOST_WORDS):
                raise ValueError("Anthropic endpoint refused in ladder")
        if not all(m.endswith(":free") for m in self.free_models):
            raise ValueError("free tier may only list ':free' models")
        if self.daily_cap_usd < 0 or self.per_cycle_cap_usd < 0:
            raise ValueError("caps must be >= 0")

    def api_key(self) -> Optional[str]:
        key = os.getenv(self.key_env, "").strip()
        if key:
            return key
        if self.key_file and Path(self.key_file).is_file():
            for line in Path(self.key_file).read_text(encoding="utf-8").splitlines():
                if line.strip().startswith(self.key_env + "="):
                    return line.split("=", 1)[1].strip().strip('"')
        return None


# ------------------------------------------------------------------ Anthropic block (proof)

class AnthropicBlock:
    """Process-wide audit hook: any DNS/connect to an Anthropic host is refused and counted."""

    attempts = 0
    _installed = False
    _lock = threading.Lock()

    @classmethod
    def install(cls) -> None:
        if cls._installed:
            return

        def hook(event: str, args: tuple) -> None:
            if event not in ("socket.getaddrinfo", "socket.connect"):
                return
            host = args[0] if event == "socket.getaddrinfo" else (args[1][0] if isinstance(args[1], tuple) else args[1])
            if isinstance(host, (bytes, str)) and any(w in str(host).lower() for w in BLOCKED_HOST_WORDS):
                with cls._lock:
                    cls.attempts += 1
                raise PermissionError("Anthropic egress blocked by the learning supervisor")

        sys.addaudithook(hook)
        for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
            os.environ.pop(var, None)
        cls._installed = True


# ------------------------------------------------------------------ price verification

_CATALOG: dict[str, Any] = {"t": 0.0, "rows": {}}


def live_catalog(fetch: Optional[Callable[[], dict]] = None, ttl_s: float = 600.0) -> dict[str, dict]:
    if fetch is None and time.time() - _CATALOG["t"] < ttl_s and _CATALOG["rows"]:
        return _CATALOG["rows"]

    def default_fetch() -> dict:
        req = urllib.request.Request(f"{OPENROUTER}/models", headers={"User-Agent": "bossman-learning247"})
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - public catalog
            return json.loads(resp.read().decode())
    data = (fetch or default_fetch)()
    rows = {m["id"]: m.get("pricing") or {} for m in data.get("data", [])}
    if fetch is None:
        _CATALOG.update(t=time.time(), rows=rows)
    return rows


def price_per_m(pricing: dict) -> tuple[Optional[float], Optional[float]]:
    try:
        return float(pricing["prompt"]) * 1e6, float(pricing["completion"]) * 1e6
    except (KeyError, TypeError, ValueError):
        return None, None


def verified_free(model: str, catalog: dict[str, dict]) -> bool:
    p_in, p_out = price_per_m(catalog.get(model) or {})
    return model.endswith(":free") and p_in == 0.0 and p_out == 0.0


# ------------------------------------------------------------------ hard cap ledger

class CapLedger:
    """Durable reserve-before-call ledger. A reservation that would exceed a cap is refused."""

    def __init__(self, path: Path, cfg: LadderConfig):
        self.path = Path(path)
        self.cfg = cfg

    def _read(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _write(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)

    @staticmethod
    def day() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def spent_today(self) -> float:
        d = self._read().get(self.day(), {})
        return round(float(d.get("settled", 0.0)) + float(d.get("reserved", 0.0)), 9)

    def reserve(self, cycle_id: int, usd: float) -> tuple[bool, str]:
        data = self._read()
        d = data.setdefault(self.day(), {"settled": 0.0, "reserved": 0.0, "cycles": {}})
        cyc = float(d["cycles"].get(str(cycle_id), 0.0))
        if cyc + usd > self.cfg.per_cycle_cap_usd + 1e-12:
            return False, f"per_cycle_cap {self.cfg.per_cycle_cap_usd} would be exceeded"
        if d["settled"] + d["reserved"] + usd > self.cfg.daily_cap_usd + 1e-12:
            return False, f"daily_cap {self.cfg.daily_cap_usd} would be exceeded"
        d["reserved"] = round(d["reserved"] + usd, 9)
        d["cycles"][str(cycle_id)] = round(cyc + usd, 9)
        self._write(data)
        return True, "reserved"

    def settle(self, cycle_id: int, reserved: float, actual: float) -> None:
        data = self._read()
        d = data.setdefault(self.day(), {"settled": 0.0, "reserved": 0.0, "cycles": {}})
        d["reserved"] = round(max(0.0, d["reserved"] - reserved), 9)
        d["settled"] = round(d["settled"] + actual, 9)
        d["cycles"][str(cycle_id)] = round(float(d["cycles"].get(str(cycle_id), 0.0)) - reserved + actual, 9)
        self._write(data)


# ------------------------------------------------------------------ tier plan

@dataclass
class Route:
    tier: str
    model: Optional[str]
    base_url: Optional[str]
    api_key: Optional[str] = None
    reserve_usd: float = 0.0
    price_in_per_m: float = 0.0
    price_out_per_m: float = 0.0
    note: str = ""

    def public(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("api_key", None)
        return d


def plan(cfg: LadderConfig, *, needs_llm: bool, catalog: Optional[dict[str, dict]] = None,
         skip_local: bool = False, role: str = "worker") -> list[Route]:
    """Ordered candidate routes; the caller tries them in order and stops at the first success."""
    if not needs_llm:
        return [Route("deterministic", None, None)]
    routes: list[Route] = []
    if not skip_local:
        routes += [Route("local", m, cfg.local_base_url) for m in cfg.local_models]
    key = cfg.api_key()
    if key and cfg.allow_free_cloud:
        try:
            cat = catalog if catalog is not None else live_catalog()
        except OSError:
            cat = {}
        for m in cfg.free_models:
            if verified_free(m, cat):
                routes.append(Route("free_cloud", m, cfg.cloud_base_url, key, note="price verified 0/0 live"))
        if cfg.allow_max_cloud:
            p_in, p_out = price_per_m(cat.get(cfg.max_model) or {})
            if (p_in is not None and p_out is not None and p_in <= cfg.max_price_in_per_m
                    and p_out <= cfg.max_price_out_per_m):
                worst = round((cfg.max_tokens * p_out + 4000 * p_in) / 1e6, 9)
                routes.append(Route("max_cloud", cfg.max_model, cfg.cloud_base_url, key, reserve_usd=worst,
                                    price_in_per_m=p_in, price_out_per_m=p_out,
                                    note=f"role={role}; only after free tiers failed"))
    return routes


def actual_cost(route: Route, tokens_in: int, tokens_out: int) -> float:
    return round((tokens_in * route.price_in_per_m + tokens_out * route.price_out_per_m) / 1e6, 9)
