"""Collect CURRENT public procurement notices by driving ONE headless browser over public pages (no API, no login).

    python tools/browser_contracts.py --dry-run out.json          # read pages, write records to a file only
    python tools/browser_contracts.py --post                      # also POST them to the running terminal (analyst token, never printed)

Rules (owner 18.10.2026): public notices only, read-only, no CAPTCHA/login/paid services, robots.txt honoured, one page at a time,
>= robots Crawl-delay (10 s on nen.nipez.cz) between page loads, <= 150 page loads per run, browser closed at the end.
Parsing lives in warterminal/connectors/browser_nen.py (tested on saved pages). TED search pages are NOT used: its robots.txt
disallows /search/result?... and the result list does not render in headless mode (see .agents/skills/browser-contract-scraping).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from warterminal.connectors import browser_nen as bn  # noqa: E402

UA = "bossman-war-terminal/0.1 (+owner research; read-only; headless browser)"


class Budget:
    def __init__(self, max_loads: int, delay: float) -> None:
        self.left, self.delay, self.last = max_loads, delay, 0.0

    def wait(self) -> None:
        if self.left <= 0:
            raise RuntimeError("page-load budget exhausted")
        gap = self.delay - (time.monotonic() - self.last)
        if gap > 0:
            time.sleep(gap)
        self.left -= 1

    def done(self) -> None:
        self.last = time.monotonic()


def allowed(url: str) -> bool:
    """Our own allow-list on top of robots.txt: only the public procurement list/detail pages, never /file*, /portal/client/, etc."""
    return url.startswith(bn.BASE + bn.LIST_PATH) and "detail-provozni-informace" not in url and "/portal/client/" not in url


def run(args) -> dict:
    from playwright.sync_api import sync_playwright
    bud = Budget(args.max_loads, max(args.delay, bn.CRAWL_DELAY_S))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fetched = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    rows: dict[str, dict] = {}
    log: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=UA)

            def load(url: str, wait_sel: str) -> str:
                assert allowed(url), url
                bud.wait()
                try:
                    page.goto(url, wait_until="networkidle", timeout=60000)
                    page.wait_for_selector(wait_sel, timeout=20000)
                except Exception as e:                       # noqa: BLE001 - one bad page must not kill the run
                    log.append(f"FAIL {url}: {type(e).__name__}")
                    bud.done()
                    return ""
                html = page.content()
                bud.done()
                return html

            queries = [dict(buyer=b) for b in bn.BUYERS[: args.buyers]] + [dict(name=w) for w in bn.SUBJECTS]
            for q in queries:
                first = bn.list_url(deadline_from=today, **q)
                html = load(first, "tbody tr")
                if not html:
                    continue
                res = bn.parse_list(html)
                got = res["rows"]
                for pg in range(2, min(res["last_page"], args.pages) + 1):
                    h = load(bn.list_url(deadline_from=today, page=pg, **q), "tbody tr")
                    got += bn.parse_list(h)["rows"] if h else []
                for r in got:
                    if r["status"] == "Not terminated":
                        rows.setdefault(r["nen_number"], dict(r, query=q))
                log.append(f"list {q}: {len(got)} rows (last_page={res['last_page']})")
            # details: most relevant first (buyer/title heuristics on list data), within the remaining budget
            def prelim(r):
                return bn.relevance({"buyer": r["buyer"], "title": r["title"], "cpv": [], "estimated_value": None})
            todo = sorted(rows.values(), key=lambda r: -prelim(r))
            docs = []
            for r in todo:
                if bud.left <= 0:
                    break
                url = bn.BASE + r["detail_path"]
                html = load(url, ".gov-grid-tile")
                if not html:
                    continue
                d = bn.normalize(bn.parse_detail(html), url, fetched())
                if d:
                    d["relevance"] = bn.relevance(d)
                    docs.append(d)
        finally:
            browser.close()
    return {"portal": "nen", "list_rows": len(rows), "detail_read": len(docs), "loads_used": args.max_loads - bud.left, "log": log, "tenders": docs}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-loads", type=int, default=150)
    ap.add_argument("--delay", type=float, default=10.0)
    ap.add_argument("--buyers", type=int, default=5, help="how many buyers from browser_nen.BUYERS")
    ap.add_argument("--pages", type=int, default=6, help="max list pages per query")
    ap.add_argument("--dry-run", metavar="FILE", help="write records to FILE")
    ap.add_argument("--post", action="store_true", help="POST to the terminal /ingest/browser")
    ap.add_argument("--url", default="http://127.0.0.1:8830")
    ap.add_argument("--token-file", default=str(Path.home() / ".war-terminal" / "tokens.json"))
    a = ap.parse_args()
    a.max_loads = min(a.max_loads, 150)
    out = run(a)
    if a.dry_run:
        Path(a.dry_run).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.post:
        import httpx
        tok = json.loads(Path(a.token_file).read_text(encoding="utf-8"))["analyst"]
        r = httpx.post(a.url + "/api/v1/ingest/browser", json={"portal": "nen", "tenders": out["tenders"]},
                       headers={"Authorization": "Bearer " + tok}, timeout=120)
        print("POST", r.status_code, r.text[:300])
    print(json.dumps({k: v for k, v in out.items() if k != "tenders"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
