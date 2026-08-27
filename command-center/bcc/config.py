"""Настройки Command Center. Всё из окружения; секреты — только в data dir (права 600)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# каталог пакета: <repo>/command-center/bcc → корень раздела <repo>/command-center
PKG_DIR = Path(__file__).resolve().parent
ROOT = PKG_DIR.parent


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _data_dir() -> Path:
    return Path(_env("BCC_DATA_DIR", str(ROOT / "data"))).expanduser()


@dataclass
class Settings:
    # data dir хранит БД, ключ шифрования и токен UI — целиком вне git
    data_dir: Path = field(default_factory=_data_dir)
    database_url: str = ""
    host: str = field(default_factory=lambda: _env("BCC_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_env("BCC_PORT", "8800")))
    # статика UI (её делает отдельный агент); монтируется, если каталог существует
    ui_dir: Path = field(default_factory=lambda: ROOT / "ui")

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        if not self.database_url:
            self.database_url = _env("DATABASE_URL", "") or \
                f"sqlite+aiosqlite:///{self.data_dir / 'bcc.db'}"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
