"""Settings from the environment. Nothing secret is stored here: the Bossman token is read from its file on use."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _default_token_file() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Bossman" / "CommandCenter" / "token"


def _default_stop_file() -> Path:
    """The owner's global STOP: Bossman writes <data_dir>/computer/STOP on /stop and removes it on «Продолжить»
    (same resolution as bossman_shared.exchange_assistant.agent.default_stop_file)."""
    data = os.environ.get("BCC_DATA_DIR") or str(Path(os.environ.get("LOCALAPPDATA") or str(Path.home())) / "Bossman"
                                                 / "CommandCenter")
    return Path(data) / "computer" / "STOP"


@dataclass
class Settings:
    bossman_url: str = field(default_factory=lambda: os.environ.get("BOSSMAN_URL", "http://127.0.0.1:8801").rstrip("/"))
    token_file: Path = field(default_factory=lambda: Path(os.environ["BOSSMAN_TOKEN_FILE"]) if os.environ.get("BOSSMAN_TOKEN_FILE")
                             else _default_token_file())
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("BOSSMAN_MOBILE_DATA") or Path.home() / ".bossman-mobile"))
    stop_file: Path = field(default_factory=_default_stop_file)   # read-only here: cleared only by Bossman's resume
    ollama_url: str = field(default_factory=lambda: os.environ.get("BOSSMAN_MOBILE_OLLAMA", "http://127.0.0.1:11434").rstrip("/"))
    war_url: str = field(default_factory=lambda: os.environ.get("BOSSMAN_MOBILE_WAR", "http://127.0.0.1:8830").rstrip("/"))
    session_ttl_s: int = 30 * 24 * 3600
    login_failures_per_min: int = 5
    login_failures_global_per_min: int = 20
    # serve watchdog (incident 23.10): GET /m/api/health every interval; after `failures` misses the listener is restarted
    watchdog_interval_s: float = field(default_factory=lambda: float(os.environ.get("BOSSMAN_MOBILE_WATCHDOG_INTERVAL_S", "15")))
    watchdog_failures: int = field(default_factory=lambda: int(os.environ.get("BOSSMAN_MOBILE_WATCHDOG_FAILURES", "3")))
    watchdog_probe_timeout_s: float = field(default_factory=lambda: float(os.environ.get("BOSSMAN_MOBILE_WATCHDOG_TIMEOUT_S", "5")))

    def __post_init__(self) -> None:
        self.token_file = Path(self.token_file)
        self.data_dir = Path(self.data_dir)
        self.stop_file = Path(self.stop_file)
