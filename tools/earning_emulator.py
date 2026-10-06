"""CLI for the earning emulator (bcc/earning_emulator.py). SIMULATION only: no application, no message, no login, no money.

    python tools/earning_emulator.py --listing-file listing.txt --source https://example/job --ledger ledger.jsonl
    python tools/earning_emulator.py --fetch https://example/job --ledger ledger.jsonl      # read-only GET of ONE public page (run on the owner's PC)

The page fetch is this tool's only network step: a plain GET, no cookies, no login, size-capped. The emulator module itself does no I/O.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "command-center"))

from bcc import earning_emulator as em  # noqa: E402

MAX_BYTES = 200_000


def html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr)>", "\n", raw)
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    return "\n".join(" ".join(line.split()) for line in text.splitlines() if line.strip())


def fetch(url: str) -> str:
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("только http(s)")
    req = urllib.request.Request(url, headers={"User-Agent": "BossmanEmulator/1 (read-only)"}, method="GET")
    with urllib.request.urlopen(req, timeout=20) as r:                                  # noqa: S310 — a single public GET, by design
        return html_to_text(r.read(MAX_BYTES).decode("utf-8", errors="replace"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--listing-file", type=Path)
    src.add_argument("--fetch", metavar="URL")
    ap.add_argument("--source", default="")
    ap.add_argument("--ledger", type=Path, required=True)
    ap.add_argument("--worker", default="template")
    a = ap.parse_args(argv)
    if a.worker not in em.FREE_WORKERS:
        print(f"REFUSED: исполнитель {a.worker!r} не входит в $0-список {list(em.FREE_WORKERS)}")
        return 3
    if a.worker != "template":
        print("REFUSED: в этой сборке подключён только worker=template; модель подключается на ПК владельца через Bossman")
        return 3
    try:
        text = a.listing_file.read_text(encoding="utf-8") if a.listing_file else fetch(a.fetch)
    except (OSError, UnicodeDecodeError, ValueError) as exc:   # URLError is an OSError
        print(f"ERROR: не удалось прочитать вакансию: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 4
    try:
        rec = em.run(text, a.source or (a.fetch or str(a.listing_file)), worker_name=a.worker, ledger=a.ledger)
    except OSError as exc:
        print(f"ERROR: не удалось записать журнал {a.ledger}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 4
    print(json.dumps({k: rec[k] for k in ("verdict", "title", "kind", "invoice", "reasons", "real_money", "SIMULATED")}, ensure_ascii=False))
    return 0 if rec["verdict"] == "SIMULATED_DONE" else 2


if __name__ == "__main__":
    sys.exit(main())
