"""Точка входа приложения — та самая, что объявлена в `pyproject.toml`.

`[project.scripts]` объявлял `social-farm = "social_farm.main:main"`, а модуля
не было. Объявление — это намерение; здесь оно становится фактом. Пока его не
было, запуск приложения через Bossman порождал процесс, который умирал с
`ModuleNotFoundError` за доли секунды: в интерфейсе это выглядело как
приложение, которое «не открывается», без причины.
"""
from __future__ import annotations

import argparse
import os
import sys

DEFAULT_PORT = 8895          # `default_port` из app.manifest.yaml
DEFAULT_HOST = "127.0.0.1"   # локально, а не наружу: это приложение владельца


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="social-farm")
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="поднять HTTP-поверхность")
    serve.add_argument("--host", default=os.getenv("SF_HOST") or DEFAULT_HOST)
    serve.add_argument("--port", type=int,
                       default=int(os.getenv("APP_PORT")
                                   or os.getenv("SF_PORT") or DEFAULT_PORT))
    sub.add_parser("capabilities", help="каталог возможностей в stdout")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "capabilities":
        import json
        from .api import capability_catalogue
        print(json.dumps(capability_catalogue(), ensure_ascii=False, indent=2))
        return 0

    if args.command != "serve":
        parser.print_help()
        return 2

    try:
        import uvicorn
    except ImportError:
        print("для запуска нужен uvicorn: "
              "pip install -e 'apps/social-farm[api]'", file=sys.stderr)
        return 3

    from .api import build_app
    uvicorn.run(build_app(), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
