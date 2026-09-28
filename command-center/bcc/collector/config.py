"""Flags, ceilings and the one honest identity string for the collector.

One file for the constants, same reasoning as
``bcc/features/web_research/config.py``: a ceiling the model (or a CLI flag)
could raise is a safety question, not a convenience default, so every
number here is read from environment/args by name, once, with an explicit
floor.
"""
from __future__ import annotations

import os
from pathlib import Path

#: The ONE honest identity used for every collector HTTP request (robots.txt
#: fetch). Reused from the product's existing osiris module on purpose: one
#: product, one honest name, not a second identity string that could drift
#: from the first. The real-browser navigation itself uses Playwright's own
#: genuine Chromium user agent — never overridden, never spoofed.
try:
    from ..features.osiris import USER_AGENT
except Exception:  # noqa: BLE001 — osiris pulls in ..db; keep collector importable without it
    USER_AGENT = "BossmanOsiris/1.0 (+https://github.com/molotroka123-cell/AiMaxBossman)"

#: Politeness floor: the task is explicit that this must be 5-10s minimum.
#: A caller may raise it; nothing in this module lets it go lower.
MIN_DELAY_FLOOR_S = 5.0
DEFAULT_DELAY_S = 8.0

#: Default daily page cap per domain. A caller may lower it; the CLI clamps
#: any attempt to raise it above the hard ceiling below.
DEFAULT_DAILY_CAP = 50
DAILY_CAP_CEILING = 200

#: How many pages one run may visit at most, regardless of --max-pages.
MAX_PAGES_CEILING = 50

#: robots.txt fetch timeout. Unreachable/ambiguous robots.txt is fail-closed
#: (treated as disallow) EXCEPT an explicit 404/410, which is the standard
#: "no robots.txt published" signal and means allow (matches
#: urllib.robotparser's own behaviour for 4xx).
ROBOTS_FETCH_TIMEOUT_S = 10.0

#: Per-page network/render timeout for the browser navigation itself.
PAGE_TIMEOUT_S = 45.0

#: Sentence-scoring thresholds for candidate facts (§ extract.py).
MIN_FACT_SCORE = 0.12
MAX_FACTS_PER_PAGE = 6

#: STOP file name inside the run's own data dir (same name/semantics as the
#: rest of the product: bcc/features/tools_computer.py's STOP_FILE, and
#: bcc/market/collector.py's <root>/STOP).
STOP_FILE_NAME = "STOP"

#: Owner-wide pause file for this rc19 owner-test cycle. Its mere existence
#: pauses (not stops) every workstream's collector; work resumes on its own
#: once it is removed. Overridable for tests only.
PAUSE_FILE_ENV = "BOSSMAN_RC19_PAUSE_FILE"
DEFAULT_PAUSE_FILE = r"C:\Users\asd\Bossman\rc19-owner-test.PAUSE"


def pause_file_path() -> Path:
    return Path(os.environ.get(PAUSE_FILE_ENV, "").strip() or DEFAULT_PAUSE_FILE)


#: How often (seconds) the pause loop wakes up to re-check STOP/PAUSE. Kept
#: short so STOP is honoured promptly, long enough to stay a low-CPU wait.
PAUSE_POLL_S = 1.0

#: Default data directory for real (non-test) use, mirroring the convention
#: in bcc/market/ledger.py (LOCALAPPDATA/Bossman/CommandCenter/<feature>).
#: Tests and the one live run always pass --data-dir explicitly.
DATA_DIR_ENV = "BOSSMAN_COLLECTOR_DATA_DIR"


def default_data_dir() -> Path:
    configured = os.environ.get(DATA_DIR_ENV, "").strip()
    if configured:
        return Path(configured)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "Bossman" / "CommandCenter" / "collector"


#: What this collector refuses to do, by design — shown in the doc, the run
#: bundle and the owner report, not just asserted in prose.
REFUSED_BY_DESIGN = (
    "fake user-agent rotation",
    "browser fingerprint spoofing",
    "proxy rotation / IP rotation to evade rate limits",
    "CAPTCHA solving or bypass",
    "logging in / defeating login walls",
    "paywall circumvention",
    "personal-data harvesting",
    "crawling links the owner did not put on the source list",
    "ignoring robots.txt disallow rules",
    "silently merging conflicting facts from different sources",
    "guessing a value when no approved source states it (kept as UNKNOWN)",
)
