"""Jev configuration: environment only, read at CALL time (kill switch without rebuild).

Conventions follow the other ``BOSSMAN_*_ENABLED`` feature flags
(second_opinion, watchdog, spend_meter): OFF unless the value is 1/true/yes/on.

Secrets never live in Git or in this module. The key is read from the process
environment (``BOSSMAN_JEV_API_KEY``, then upstream's ``TYPESAFE_API_KEY``) at
the moment of a request and is never stored on the config object that gets
logged — ``JevConfig.public()`` only says whether a key is present.

Immediate kill switch without restart: create the file named by
``BOSSMAN_JEV_KILL_FILE`` (default ``<BCC_DATA_DIR or cwd>/jev.disabled``).
While it exists every Jev path behaves exactly as if the flags were off.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path

# --- decision engine -------------------------------------------------------
FLAG = "BOSSMAN_JEV_ENABLED"
SHADOW_FLAG = "BOSSMAN_JEV_SHADOW"
KEY_ENVS = ("BOSSMAN_JEV_API_KEY", "TYPESAFE_API_KEY")
ENDPOINT_ENV = "BOSSMAN_JEV_ENDPOINT"
MODEL_ENV = "BOSSMAN_JEV_MODEL"
KILL_FILE_ENV = "BOSSMAN_JEV_KILL_FILE"

# Source-derived from the pinned upstream (jev_ultrafast/model.py @ UPSTREAM_COMMIT):
# ``POST https://api.typesafe.ai/v1/systemone``. Upstream docs/performance.md names
# the model ``jev-1.13.0``. NOT verified against the live API from the cloud
# (see bcc.jev.upstream.CONTRACT_STATUS) — the owner's probe confirms it.
DEFAULT_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-1.13.0"

# --- browser fast path -----------------------------------------------------
BROWSER_FLAG = "BOSSMAN_JEV_BROWSER_ENABLED"
BROWSER_SHADOW_FLAG = "BOSSMAN_JEV_BROWSER_SHADOW"
TEXT_KEY_ENVS = ("TEXT_MODEL_API_KEY",)

TRUE = ("1", "true", "yes", "on")
FALSE = ("0", "false", "no", "off")


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if raw in TRUE:
        return True
    if raw in FALSE:
        return False
    return default


def _int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(os.environ.get(name, "").strip() or default)
    except ValueError:
        value = default
    return max(lo, min(hi, value))


def _float(name: str, default: float | None) -> float | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value == value and value >= 0 else default


_default_kill_dir: Path | None = None


def set_default_kill_dir(path: Path | None) -> None:
    """The app sets its data dir here once at startup; env still wins."""
    global _default_kill_dir
    _default_kill_dir = Path(path) if path else None


def kill_file() -> Path:
    explicit = os.environ.get(KILL_FILE_ENV, "").strip()
    if explicit:
        return Path(explicit).expanduser()
    base = os.environ.get("BCC_DATA_DIR", "").strip()
    if base:
        return Path(base).expanduser() / "jev.disabled"
    return (_default_kill_dir or Path.cwd()) / "jev.disabled"


def killed() -> bool:
    try:
        return kill_file().exists()
    except OSError:
        return True          # cannot tell → treat as killed (fail closed)


def api_key() -> str:
    """The Jev key, or "" — never cached, never logged."""
    for name in KEY_ENVS:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def text_key() -> str:
    for name in TEXT_KEY_ENVS:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


@dataclass(frozen=True)
class JevConfig:
    enabled: bool
    shadow: bool
    endpoint: str
    model: str
    timeout_ms: int
    max_retries: int
    breaker_failures: int
    breaker_cooldown_s: int
    min_confidence: float
    price_per_call_usd: float | None
    price_per_1k_input_usd: float | None
    price_per_1k_output_usd: float | None
    killed: bool

    @property
    def zero_cost_confirmed(self) -> bool:
        """Jev has no reported USD bill: unknown pricing is never free."""
        return (_flag("BOSSMAN_JEV_ZERO_COST_CONFIRMED", False)
                and self.price_per_call_usd == 0
                and self.price_per_1k_input_usd == 0
                and self.price_per_1k_output_usd == 0)

    @property
    def active(self) -> bool:
        return self.enabled and not self.killed

    def public(self) -> dict:
        out = asdict(self)
        out["key_present"] = bool(api_key())
        out["zero_cost_confirmed"] = self.zero_cost_confirmed
        return out


def load() -> JevConfig:
    return JevConfig(
        enabled=_flag(FLAG, False),
        # Phase 2 still leaves Smart Router, policy, STOP and budget authoritative.
        # Live routing also requires explicit public egress and zero-cost evidence.
        shadow=_flag(SHADOW_FLAG, True),
        endpoint=os.environ.get(ENDPOINT_ENV, "").strip() or DEFAULT_ENDPOINT,
        model=os.environ.get(MODEL_ENV, "").strip() or DEFAULT_MODEL,
        timeout_ms=_int("BOSSMAN_JEV_TIMEOUT_MS", 2500, 100, 30000),
        max_retries=_int("BOSSMAN_JEV_MAX_RETRIES", 1, 0, 3),
        breaker_failures=_int("BOSSMAN_JEV_BREAKER_FAILURES", 3, 1, 100),
        breaker_cooldown_s=_int("BOSSMAN_JEV_BREAKER_COOLDOWN_S", 60, 1, 3600),
        min_confidence=(_float("BOSSMAN_JEV_MIN_CONFIDENCE", 0.6) or 0.6),
        price_per_call_usd=_float("BOSSMAN_JEV_PRICE_PER_CALL_USD", None),
        price_per_1k_input_usd=_float("BOSSMAN_JEV_PRICE_PER_1K_INPUT_USD", None),
        price_per_1k_output_usd=_float("BOSSMAN_JEV_PRICE_PER_1K_OUTPUT_USD", None),
        killed=killed(),
    )


@dataclass(frozen=True)
class BrowserConfig:
    enabled: bool
    shadow: bool
    max_steps: int
    timeout_ms: int
    verify_done: bool
    fallback: bool
    killed: bool

    @property
    def active(self) -> bool:
        return self.enabled and not self.killed

    @property
    def may_execute(self) -> bool:
        """Phase 1: never. Execution needs enabled AND shadow off AND phase 2 code,
        which deliberately does not exist yet (see browser_fastpath.execute_step)."""
        return False

    def public(self) -> dict:
        out = asdict(self)
        out["text_key_present"] = bool(text_key())
        out["may_execute"] = self.may_execute
        return out


def load_browser() -> BrowserConfig:
    return BrowserConfig(
        enabled=_flag(BROWSER_FLAG, False),
        shadow=_flag(BROWSER_SHADOW_FLAG, True),
        max_steps=_int("BOSSMAN_JEV_BROWSER_MAX_STEPS", 25, 1, 200),
        timeout_ms=_int("BOSSMAN_JEV_BROWSER_TIMEOUT_MS", 30000, 1000, 600000),
        # verify_done / fallback cannot be switched off in phase 1: the flags are
        # read for visibility only, the code path ignores a "false".
        verify_done=_flag("BOSSMAN_JEV_BROWSER_VERIFY_DONE", True),
        fallback=_flag("BOSSMAN_JEV_BROWSER_FALLBACK", True),
        killed=killed(),
    )
