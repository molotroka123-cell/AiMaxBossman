"""Настройки Command Center. Всё из окружения; секреты — только в data dir (права 600)."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

# каталог пакета: <repo>/command-center/bcc → корень раздела <repo>/command-center
PKG_DIR = Path(__file__).resolve().parent
ROOT = PKG_DIR.parent


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _data_dir() -> Path:
    configured = _env("BCC_DATA_DIR")
    if configured:
        return Path(configured).expanduser()
    # Preserve existing source installations; wheels must never write to the
    # interpreter's site-packages (often read-only, and removed on upgrades).
    if (ROOT / "pyproject.toml").is_file() and (ROOT / "ui" / "index.html").is_file():
        return ROOT / "data"
    if sys.platform == "win32":
        base = Path(_env("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        return base / "Bossman" / "CommandCenter"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Bossman" / "CommandCenter"
    return Path(_env("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "bossman" / "command-center"


def _ui_dir() -> Path:
    override = _env("BCC_UI_DIR")
    if override:
        return Path(override).expanduser()
    packaged = PKG_DIR / "_ui"
    return packaged if (packaged / "index.html").is_file() else ROOT / "ui"


@dataclass
class Settings:
    # data dir хранит БД, ключ шифрования и токен UI — целиком вне git
    data_dir: Path = field(default_factory=_data_dir)
    database_url: str = ""
    host: str = field(default_factory=lambda: _env("BCC_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_env("BCC_PORT", "8800")))
    # Installed wheels carry their reviewed static assets. Explicit overrides
    # remain available for deployments that deliberately supply another bundle.
    ui_dir: Path = field(default_factory=_ui_dir)
    # V2.1 фаза N: браузер ходит по HttpOnly-cookie-сессии. Заголовок X-BCC-Token
    # остаётся для CLI/скриптов и переходного периода — выключается
    # BCC_LEGACY_TOKEN=0, когда всё перееxало на сессии.
    legacy_token_auth: bool = field(
        default_factory=lambda: _env("BCC_LEGACY_TOKEN", "1") not in ("0", "false", "no"))
    session_ttl_hours: int = field(
        default_factory=lambda: max(1, int(_env("BCC_SESSION_TTL_HOURS", "720"))))
    # Secure-флаг у cookie: по HTTP на localhost/Tailscale его ставить нельзя,
    # иначе браузер её просто не сохранит. Ставим автоматически для https.
    cookie_secure: str = field(default_factory=lambda: _env("BCC_COOKIE_SECURE", "auto"))

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        if not self.database_url:
            self.database_url = _env("DATABASE_URL", "") or \
                f"sqlite+aiosqlite:///{self.data_dir / 'bcc.db'}"

    @property
    def skills_workspace(self) -> Path:
        # Never infer a write location from a UI override or site-packages.
        if (ROOT / "pyproject.toml").is_file() and (ROOT / "ui" / "index.html").is_file():
            return ROOT.parent
        return self.data_dir

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
