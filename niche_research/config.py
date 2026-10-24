"""Data dir and run configuration.

The data dir comes from ``NICHE_RESEARCH_DIR`` (default ``%LOCALAPPDATA%\\Bossman\\niche-research``). The run
config lives in ``<data dir>/config.json``; missing keys take the defaults below. Paid spend is OFF by default:
``paid_usd_limit`` is a per-day USD cap for the optional paid provider and defaults to 0. The owner approved
7API GLM 5.3 Flash with a $2/day cap (24.10), so any configured value is clamped to ``PAID_DAILY_HARD_CAP``.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

PAID_DAILY_HARD_CAP = 2.0

DEFAULTS: dict = {
    # Region / language for search queries. Empty region = not specified (reports say so).
    "region": "",
    "search_suffix": "",
    # Bounded tick: at most this many tasks and seconds per tick.
    "max_tasks_per_tick": 6,
    "max_tick_seconds": 1500,
    # A running task whose lease expired (crash, PC restart) goes back to pending.
    "task_lease_minutes": 25,
    "max_attempts": 3,
    # Free-cloud limit saving (owner rule: save free limits).
    "free_calls_per_day": 150,
    # Local Ollama: by default only use a model that is ALREADY loaded (do not evict a running GPU job).
    "ollama_url": "http://127.0.0.1:11434",
    "ollama_model": "",
    "ollama_only_loaded": True,
    "openrouter_model": "nvidia/nemotron-3-ultra-550b-a55b:free",
    "gemini_model": "gemini-3-flash-preview",
    # Optional paid provider; used only when paid_usd_limit > 0 AND its key exists in the key file.
    "paid_usd_limit": 0.0,
    "paid_provider": {
        "name": "7api",
        "base_url": "https://7api.st/v1",
        "model": "glm-5.3-flash",
        "key_env": "SEVENAPI_API_KEY",
        "usd_per_mtok_in": 0.028,
        "usd_per_mtok_out": 0.028,
    },
    "max_output_tokens": 1800,
    # Web reading.
    "max_pages_per_research": 3,
    "search_results_per_query": 5,
    "allow_private_hosts": False,
    # Extra public URLs per niche the owner may add: {"уборка": ["https://..."]}.
    "seed_urls": {},
}


def default_data_dir() -> Path:
    env = os.environ.get("NICHE_RESEARCH_DIR")
    if env:
        return Path(env)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Bossman" / "niche-research"


def default_keys_file() -> Path:
    env = os.environ.get("NICHE_RESEARCH_KEYS_FILE")
    if env:
        return Path(env)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Bossman" / "keys" / "provider-keys.env"


@dataclass
class Config:
    data_dir: Path
    values: dict = field(default_factory=dict)

    def __getitem__(self, key: str):
        return self.values[key]

    def get(self, key: str, default=None):
        return self.values.get(key, default)

    @property
    def run_dir(self) -> Path:
        return self.data_dir / "run"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "niche_research.sqlite3"

    @property
    def paid_daily_limit(self) -> float:
        try:
            limit = float(self.values.get("paid_usd_limit") or 0.0)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(limit, PAID_DAILY_HARD_CAP))

    def ensure_dirs(self) -> None:
        for sub in ("reports", "research", "samples", "proposals"):
            (self.run_dir / sub).mkdir(parents=True, exist_ok=True)


def load_config(data_dir: Path | None = None) -> Config:
    data_dir = Path(data_dir) if data_dir else default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    values = json.loads(json.dumps(DEFAULTS))
    path = data_dir / "config.json"
    if path.exists():
        user = json.loads(path.read_text(encoding="utf-8") or "{}")
        if isinstance(user, dict):
            for key, val in user.items():
                if isinstance(val, dict) and isinstance(values.get(key), dict):
                    values[key].update(val)
                else:
                    values[key] = val
    cfg = Config(data_dir=data_dir, values=values)
    cfg.ensure_dirs()
    return cfg


def read_keys(path: Path | None = None) -> dict[str, str]:
    """Read KEY=VALUE lines. Values are returned to the caller only; never print or log them."""
    path = path or default_keys_file()
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out
