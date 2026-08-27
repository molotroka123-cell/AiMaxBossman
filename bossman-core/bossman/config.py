"""Настройки Core. Всё из окружения, секреты — только из .env (права 600, не в git)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(os.environ.get("BOSSMAN_ROOT", Path(__file__).resolve().parent.parent))


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


@dataclass
class Settings:
    # адреса внутри docker-сети; на хосте для разработки — 127.0.0.1
    litellm_url: str = field(default_factory=lambda: _env("LITELLM_URL", "http://litellm:4000/v1"))
    litellm_master_key: str = field(default_factory=lambda: _env("LITELLM_MASTER_KEY", ""))
    llama_swap_url: str = field(default_factory=lambda: _env("LLAMA_SWAP_URL", "http://llama-swap:8080"))
    database_url: str = field(default_factory=lambda: _env(
        "BOSSMAN_DATABASE_URL", "postgresql://bossman:bossman@postgres:5432/bossman"))
    redis_url: str = field(default_factory=lambda: _env("REDIS_URL", "redis://redis:6379/0"))

    agents_dir: Path = field(default_factory=lambda: Path(_env("AGENTS_DIR", str(ROOT / "agents"))))
    tools_registry: Path = field(default_factory=lambda: Path(_env("TOOLS_REGISTRY", str(ROOT / "tools" / "registry.yaml"))))
    projects_dir: Path = field(default_factory=lambda: Path(_env("PROJECTS_DIR", str(ROOT / "projects"))))
    workspace_dir: Path = field(default_factory=lambda: Path(_env("WORKSPACE_DIR", str(ROOT / "workspace"))))

    # sandbox: docker = контейнер без сети (боевой режим); local = subprocess (только разработка)
    sandbox_mode: str = field(default_factory=lambda: _env("SANDBOX_MODE", "docker"))
    sandbox_image: str = field(default_factory=lambda: _env("SANDBOX_IMAGE", "bossman-sandbox:latest"))

    telegram_bot_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = field(default_factory=lambda: _env("TELEGRAM_CHAT_ID", ""))

    # уведомление в Telegram, если задача заняла дольше минуты (раздел 5, шаг 6)
    notify_after_seconds: int = int(_env("NOTIFY_AFTER_SECONDS", "60"))

    host: str = field(default_factory=lambda: _env("CORE_HOST", "0.0.0.0"))
    port: int = int(_env("CORE_PORT", "8700"))


settings = Settings()
