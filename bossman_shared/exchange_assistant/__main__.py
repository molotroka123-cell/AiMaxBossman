"""CLI on the owner's own PC: python -m bossman_shared.exchange_assistant "500 usdt в czk"."""
from __future__ import annotations

import sys

from .agent import Assistant
from .store import Store, default_db


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    store = Store(default_db())
    try:
        print(Assistant(store).handle(" ".join(argv) or "помощь", "owner"))
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
