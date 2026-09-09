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


def _installed() -> bool:
    """Продукт установлен (колесо), а не запущен из чекаута."""
    return not (ROOT / "pyproject.toml").is_file()


def _default_data_dir() -> Path:
    """Данные владельца НИКОГДА не пишутся внутрь установленного пакета.

    В чекауте это по-прежнему <repo>/command-center/data. У установленного
    продукта ROOT — это site-packages: туда нельзя писать БД, ключ и токен
    (каталог бывает только для чтения, а переустановка стирала бы данные).
    """
    if _installed():
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME")
        root = Path(base) if base else Path.home() / ".local" / "share"
        return root / "Bossman" / "command-center"
    return ROOT / "data"


def _data_dir() -> Path:
    return Path(_env("BCC_DATA_DIR", str(_default_data_dir()))).expanduser()


def _default_ui_dir() -> Path:
    """Интерфейс: сначала упакованный в колесо, потом каталог чекаута.

    Установленный продукт отдавал 404 на `/`, потому что ui/ лежит вне пакета
    и в колесо не попадал. Сборка кладёт его в bcc/ui (см. setup.py).
    """
    packaged = PKG_DIR / "ui"
    return packaged if packaged.is_dir() else ROOT / "ui"


@dataclass
class Settings:
    # data dir хранит БД, ключ шифрования и токен UI — целиком вне git
    data_dir: Path = field(default_factory=_data_dir)
    database_url: str = ""
    host: str = field(default_factory=lambda: _env("BCC_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_env("BCC_PORT", "8800")))
    # статика UI (её делает отдельный агент); монтируется, если каталог существует
    ui_dir: Path = field(default_factory=_default_ui_dir)
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

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
