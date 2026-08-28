"""Entry point. Configuration comes from the environment, never from argv.

Passing a camera password on the command line would put it in the shell
history and in the process table, so the CLI deliberately has no such flag.
"""

from __future__ import annotations

import argparse
import json
import sys

from .config import Settings
from .errors import VisionError
from .logging_setup import configure_logging, get_logger


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai-webcam-vision",
        description="AI WebCam Vision workload service (configuration via AWV_* environment variables)",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="run the HTTP service (default)")
    sub.add_parser("check", help="print capabilities and exit")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    command = args.command or "serve"

    try:
        settings = Settings.from_env()
    except VisionError as exc:
        print(json.dumps({"error": exc.safe_message, "code": exc.code}), file=sys.stderr)
        return 2

    configure_logging(settings.log_level, settings.log_file)
    log = get_logger("main")

    if command == "check":
        from .runtime.service import VisionService

        service = VisionService(settings)
        print(json.dumps(service.capabilities(), indent=2, ensure_ascii=False))
        return 0

    import uvicorn

    from .api import build_app

    log.info("starting on %s:%s", settings.host, settings.port)
    uvicorn.run(build_app(settings), host=settings.host, port=settings.port, log_level=settings.log_level.lower())
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
