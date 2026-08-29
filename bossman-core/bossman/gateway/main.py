from __future__ import annotations

import logging

import uvicorn

from .app import create_gateway_app
from .config import load_gateway_config


def main() -> None:
    # Наблюдаемость (аудит): bossman.gateway пишет строку лога на запрос;
    # без basicConfig INFO-записи не доходили бы до stderr под uvicorn.
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = load_gateway_config()
    uvicorn.run(create_gateway_app(cfg), host=cfg.bind_host, port=cfg.bind_port, log_level="info")


if __name__ == "__main__":
    main()
