"""Read-only-to-product synthetic reproduction for RT20-01/02/03.

All writes occur only inside TemporaryDirectory; no network, real media or owner data.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from bossman_shared.exchange_assistant.agent import Assistant, is_write  # noqa: E402
from bossman_shared.exchange_assistant.rates import Rate, RateBook  # noqa: E402
from bossman_shared.exchange_assistant.store import Store  # noqa: E402


def main() -> None:
    now = datetime(2026, 10, 20, 5, 0, tzinfo=timezone.utc)
    with TemporaryDirectory(prefix="bossman-redteam-synthetic-") as tmp:
        root = Path(tmp)
        store = Store(root / "synthetic.db")
        try:
            store.set_manual_rate("USDCZK", Decimal("25"), "synthetic", now, "owner")
            store.set_manual_rate("USDTUSD", Decimal("1"), "synthetic", now, "owner")
            stop = root / "STOP"
            stop.write_text("stop", encoding="utf-8")
            assistant = Assistant(store, fx_db=root / "absent.db", clock=lambda: now, stop_file=stop)
            command = "купить 1000 usdt за czk при 3%"
            before = store.db.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
            answer = assistant.handle(command, "owner")
            after = store.db.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
            print(f"STOP_WRITE {is_write(command)} quotes_before {before} quotes_after {after} answered_quote {answer.startswith('Котировка')}")
            stop.unlink()
            answer = assistant.handle("купить 1000 usdt за czk", "owner")
            print(f"NO_MARGIN_AUTO_3_PERCENT {'наценка 3%' in answer}")
        finally:
            store.close()
    newer = Rate("USDCZK", Decimal("25"), "synthetic", now, "manual", now + timedelta(hours=12))
    older = Rate("USDCZK", Decimal("20"), "older-synthetic", now - timedelta(hours=1), "manual", now + timedelta(hours=12))
    book = RateBook([newer])
    book.put(older)
    print(f"STALE_RATE_SELECTED {book.get('USDCZK', now).value}")


if __name__ == "__main__":
    main()
