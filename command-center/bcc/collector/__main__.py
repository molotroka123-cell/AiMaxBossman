"""``python -m bcc.collector`` — the same entry point ``bossman collect`` uses."""
from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
