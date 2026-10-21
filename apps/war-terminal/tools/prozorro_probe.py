"""Small polite live probe of the Prozorro feed (no store, no server): scans a few feed pages, counts relevant tenders.

    python tools/prozorro_probe.py [--hours 6] [--pages 5] [--out sample.json]

Uses the same connector code as the terminal (identified User-Agent, <= 2 req/s, backoff on 429/5xx). Prints the API
shape check and every relevant tender. --out saves scrubbed details (no contact persons) for fixtures/review."""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from warterminal.connectors import http, prozorro as pz  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=6)
    ap.add_argument("--pages", type=int, default=5)
    ap.add_argument("--page-size", type=int, default=100)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    calls, feeds, fetched = [], [], []

    def get(url, params=None):
        r = http.get_json(url, params, timeout=30)
        calls.append(url)
        if params:
            feeds.append(r)
        else:
            fetched.append(pz.scrub((r or {}).get("data") or {}))     # all detail answers, scrubbed (no contact persons)
        return r

    pages, scanned, rel = [], 0, 0
    for p in pz.iter_pages(pz.polite(get), cursor=str(int(time.time() - a.hours * 3600)), max_pages=a.pages, page_size=a.page_size):
        scanned += p["scanned"]
        rel += len(p["details"])
        pages.append(p)
        print(f"page offset={p['offset']} scanned={p['scanned']} relevant={len(p['details'])} next={p['next_offset']}")
    shape_ok = all(isinstance(f.get("data"), list) and "offset" in (f.get("next_page") or {}) for f in feeds)
    print(f"API {pz.URL}: feed shape ok={shape_ok}; scanned={scanned} candidates={len(fetched)} relevant={rel} requests={len(calls)}")
    for p in pages:
        for d in p["details"]:
            print(" ", d["tenderID"], d.get("status"), d.get("procurementMethodType"), pz.relevance(d), str(d.get("title"))[:90])
    if a.out:
        pathlib.Path(a.out).write_text(json.dumps({"pages": pages, "feeds": feeds, "details": fetched}, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
