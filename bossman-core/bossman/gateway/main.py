from __future__ import annotations

import uvicorn

from .app import create_gateway_app
from .config import load_gateway_config


def main() -> None:
    cfg = load_gateway_config()
    uvicorn.run(create_gateway_app(cfg), host=cfg.bind_host, port=cfg.bind_port, log_level="info")


if __name__ == "__main__":
    main()
