"""Settings from the environment. Nothing secret is stored here: the Bossman token is read from its file on use."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _default_token_file() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Bossman" / "CommandCenter" / "token"


@dataclass
class Settings:
    bossman_url: str = field(default_factory=lambda: os.environ.get("BOSSMAN_URL", "http://127.0.0.1:8801").rstrip("/"))
    token_file: Path = field(default_factory=lambda: Path(os.environ["BOSSMAN_TOKEN_FILE"]) if os.environ.get("BOSSMAN_TOKEN_FILE")
                             else _default_token_file())
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("BOSSMAN_MOBILE_DATA") or Path.home() / ".bossman-mobile"))
    ollama_url: str = field(default_factory=lambda: os.environ.get("BOSSMAN_MOBILE_OLLAMA", "http://127.0.0.1:11434").rstrip("/"))
    war_url: str = field(default_factory=lambda: os.environ.get("BOSSMAN_MOBILE_WAR", "http://127.0.0.1:8830").rstrip("/"))
    session_ttl_s: int = 30 * 24 * 3600
    login_failures_per_min: int = 5
    login_failures_global_per_min: int = 20

    def __post_init__(self) -> None:
        self.token_file = Path(self.token_file)
        self.data_dir = Path(self.data_dir)
