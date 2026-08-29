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

    # ЭТАП 3 — AI Gateway: пока BOSSMAN_GATEWAY_URL пуст, ядро ходит к моделям
    # напрямую через LiteLLM (текущее поведение). Заданный URL включает единую
    # точку выхода через приватный Gateway; ядро аутентифицируется в нём
    # ключом BOSSMAN_GATEWAY_CORE_KEY (не ключом провайдера и не ключом агента).
    gateway_url: str = field(default_factory=lambda: _env("BOSSMAN_GATEWAY_URL", ""))
    gateway_core_key: str = field(default_factory=lambda: _env("BOSSMAN_GATEWAY_CORE_KEY", ""))
    database_url: str = field(default_factory=lambda: _env(
        "BOSSMAN_DATABASE_URL", "postgresql://bossman:bossman@postgres:5432/bossman"))
    redis_url: str = field(default_factory=lambda: _env("REDIS_URL", "redis://redis:6379/0"))

    agents_dir: Path = field(default_factory=lambda: Path(_env("AGENTS_DIR", str(ROOT / "agents"))))
    tools_registry: Path = field(default_factory=lambda: Path(_env("TOOLS_REGISTRY", str(ROOT / "tools" / "registry.yaml"))))
    projects_dir: Path = field(default_factory=lambda: Path(_env("PROJECTS_DIR", str(ROOT / "projects"))))
    workspace_dir: Path = field(default_factory=lambda: Path(_env("WORKSPACE_DIR", str(ROOT / "workspace"))))

    # sandbox: docker = контейнер без сети (боевой режим); local = subprocess БЕЗ
    # изоляции. local — это выполнение произвольной команды агента прямо на хосте,
    # то есть ровно то, от чего Stage 8 защищает всё остальное. Поэтому он больше
    # не включается одним лишь SANDBOX_MODE=local: нужен ещё осознанный
    # BOSSMAN_UNSAFE_LOCAL_EXEC=1. Любое неизвестное значение sandbox_mode тоже
    # ведёт к отказу, а не тихо в хостовый шелл (fail closed).
    sandbox_mode: str = field(default_factory=lambda: _env("SANDBOX_MODE", "docker"))
    sandbox_image: str = field(default_factory=lambda: _env("SANDBOX_IMAGE", "bossman-sandbox:latest"))
    allow_unsafe_local_exec: bool = field(default_factory=lambda: _env(
        "BOSSMAN_UNSAFE_LOCAL_EXEC", "").lower() in ("1", "true", "yes"))

    telegram_bot_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = field(default_factory=lambda: _env("TELEGRAM_CHAT_ID", ""))
    # Секрет вебхука Telegram: задаётся в setWebhook(secret_token=...), Telegram
    # присылает его в заголовке X-Telegram-Bot-Api-Secret-Token. Без него любой,
    # кто достучится до порта ядра, мог бы подделать «approve:<id>» и подтвердить
    # чужое действие. Пустой секрет => вебхук approve/reject запрещён (403).
    telegram_webhook_secret: str = field(default_factory=lambda: _env("TELEGRAM_WEBHOOK_SECRET", ""))

    # уведомление в Telegram, если задача заняла дольше минуты (раздел 5, шаг 6)
    notify_after_seconds: int = int(_env("NOTIFY_AFTER_SECONDS", "60"))

    # Context & Memory Engine (ЭТАП 2.222): слой поверх ContextBuilder наполняет
    # блок retrieved долговременной памятью и evidence-чанками. Отключаемо, чтобы
    # деградировать к чистому поведению ядра. Индекс — под workspace (не в git).
    context_engine_enabled: bool = field(
        default_factory=lambda: _env("CONTEXT_ENGINE_ENABLED", "1").lower() not in ("0", "false", "no", ""))
    context_db: Path = field(default_factory=lambda: Path(
        _env("CONTEXT_DB", str(Path(_env("WORKSPACE_DIR", str(ROOT / "workspace"))) / "_context" / "context.db"))))

    host: str = field(default_factory=lambda: _env("CORE_HOST", "127.0.0.1"))
    port: int = int(_env("CORE_PORT", "8700"))


settings = Settings()
