"""CLI entry point for `python -m nautilus`."""
from __future__ import annotations

from .telegram_report import main

if __name__ == "__main__":
    raise SystemExit(main())
