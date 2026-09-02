from __future__ import annotations

import logging

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


def main() -> None:
    # Наблюдаемость (аудит): bossman.gateway пишет строку лога на запрос;
    # без настройки root INFO-записи не доходили бы до stderr под uvicorn.
    configure_gateway_logging()
    cfg = load_gateway_config()
    uvicorn.run(create_gateway_app(cfg), host=cfg.bind_host, port=cfg.bind_port, log_level="info")


if __name__ == "__main__":
    main()
