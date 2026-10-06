"""``python -m bcc.pit.master_parser [--data-dir D] ...`` (used by the пульт)."""
from __future__ import annotations

import sys
from pathlib import Path

from ..config import config_path, default_data_dir
from .cli import build_parser, cli_main


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0].startswith("-"):
        argv = ["master-parse", *argv]
    ns, _ = build_parser().parse_known_args(argv)
    data_dir = Path(ns.data_dir) if ns.data_dir else default_data_dir()
    return cli_main(config_path(data_dir), argv)


if __name__ == "__main__":
    raise SystemExit(main())
