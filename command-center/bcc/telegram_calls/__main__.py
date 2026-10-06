"""``python -I -m bcc.telegram_calls`` — the calls worker process (started by the Command Center, never by hand)."""
from __future__ import annotations

import sys

from .call.worker import main

if __name__ == "__main__":
    sys.exit(main())
