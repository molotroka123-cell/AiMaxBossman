"""Точка входа процесса Gateway.

Здесь встретились две версии этого файла, и обе нужны по частям.

От ветки — **настройка логирования**. `configure_gateway_logging()` ставит
канонический JSON-хендлер вместе с `RedactionFilter`. До неё Gateway был
единственным процессом без редактора секретов: `Bearer`, `api_key` и токены
уходили в stderr как есть. Автоматическое слияние с main вычистило эту функцию
целиком — и тест `test_gateway_logging_uses_redaction_filter` это заметил. Он
и есть причина, по которой она стоит первой строкой `main()`, а не где-нибудь
после.

От main — **аргументы командной строки и обзор провайдеров** на старте:
`--host/--port/--reload`, чтение `.env` рядом с bossman-core и печать того,
у кого есть ключ. Полезное для владельца, который поднимает шлюз руками.

Чего здесь нет и не будет: модульного объекта `app`. Настоящий шлюз собирает
аутентификацию, бюджет и маршрутизатор при создании, поэтому приложение
строится фабрикой. Готовый модульный `app` обходил бы всё это стороной.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

import uvicorn

from .. import obs
from .app import create_gateway_app
from .config import load_gateway_config


def configure_gateway_logging() -> None:
    """Логирование процесса Gateway — через канонический obs.configure_logging():
    JSON-хендлер на root + RedactionFilter. Раньше здесь был голый
    logging.basicConfig — процесс Gateway был единственным без редактора
    секретов (Bearer/api_key/token в строке лога уходили в stderr как есть).
    Вынесено в функцию, чтобы тест мог проверить конфигурацию без uvicorn."""
    obs.configure_logging(logging.INFO)
    # bossman.gateway пишет строку лога на запрос; логгер получает фильтр и
    # напрямую (на случай, если root-хендлеры позже подменит uvicorn/другой код).
    obs.get_logger("bossman.gateway")


def _load_env(env_path: str) -> None:
    """Прочитать .env рядом с bossman-core, если он там есть."""
    if not os.path.isabs(env_path):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        env_path = os.path.join(script_dir, "..", "..", env_path)
    if not os.path.exists(env_path):
        print(f"[Gateway] .env not found at {env_path}; using environment variables")
        return
    print(f"[Gateway] reading .env: {env_path}")
    from .config import load_env_file
    load_env_file(env_path)


def _print_providers() -> None:
    """Показать, у кого есть ключ. Значение ключа не печатается никогда."""
    from .config import AVAILABLE_PROVIDERS, load_provider_config
    print("\n[Gateway] providers:")
    for provider in AVAILABLE_PROVIDERS:
        config = load_provider_config(provider)
        # Локальный провайдер ключа не требует вовсе, и «✗» напротив него было
        # бы неправдой: он не «не настроен», ему нечего настраивать.
        if config.api_key_env is None:
            mark = "LOCAL"
        else:
            mark = "CONFIGURED" if config.resolved_api_key() else "NOT_CONFIGURED"
        print(f"  {mark} {provider}")
    print()


def main() -> None:
    # Windows pipes may use cp1252 even when the interactive terminal supports
    # Unicode. Keep the selected encoding; escape an unrepresentable path or
    # error instead of crashing the installed CLI. Do this only at entrypoint,
    # never while the package is imported by another application.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    parser = argparse.ArgumentParser(description="Bossman Gateway")
    parser.add_argument("--host", default=None, help="bind address")
    parser.add_argument("--port", type=int, default=None, help="bind port")
    parser.add_argument("--reload", action="store_true", help="reload when source files change")
    parser.add_argument("--env", default=".env", help="path to .env")
    args = parser.parse_args()

    # Наблюдаемость (аудит) — ПЕРВОЙ строкой: bossman.gateway пишет строку лога
    # на запрос, и до этого вызова редактора секретов в них нет.
    configure_gateway_logging()
    _load_env(args.env)
    _print_providers()

    cfg = load_gateway_config()
    host = args.host or cfg.bind_host
    port = args.port if args.port is not None else cfg.bind_port
    print(f"[Gateway] http://{host}:{port} - API routes: /docs")

    if args.reload:
        # Перезапуск по правке требует строку импорта, а не готовый объект.
        uvicorn.run("bossman.gateway.app:create_gateway_app", factory=True,
                    host=host, port=port, reload=True, log_level="info")
    else:
        uvicorn.run(create_gateway_app(cfg), host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
