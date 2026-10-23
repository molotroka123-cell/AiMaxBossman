"""War Terminal daily actuality check (owner 22.10: «тендера не существуют… проверка боссмана через веб браузер компа каждый день»).

Steps, all against the LIVE terminal on 127.0.0.1:8830 (owner role token read from ~/.war-terminal/tokens.json, never printed):
  1. refresh  - POST /ingest {"source": "ted"} so the list carries today's TED notices;
  2. freshness - GET /tenders: no tender may be `open` with a deadline before today; every TED link must use the
     resolving ``/en/notice/-/detail/<id>`` form (the bare ``/en/notice/<id>`` form answers 404);
  3. existence - for N random open TED tenders: the TED v3 API must return the notice, and a real Chrome on this PC
     (Playwright, headless) must render a page whose title carries the notice id and not «Page not found».
     Plain HTTP clients get an empty 202 bot-challenge from ted.europa.eu, so only the browser result counts.
Result JSON goes to %LOCALAPPDATA%/Bossman/CommandCenter/war-terminal-daily/<date>.json. On FAIL a short Russian note goes
to the owner's pult via tools/pult_note.py. Exit 0 = PASS, 1 = FAIL, 2 = terminal unreachable.

  python tools/war_terminal_daily_check.py [--sample 5] [--no-refresh] [--no-browser] [--no-pult]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import sys
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8830/api/v1"
TED_API = "https://api.ted.europa.eu/v3/notices/search"
DETAIL = "https://ted.europa.eu/en/notice/-/detail/"


def _req(url: str, token: str | None = None, body: dict | None = None, timeout: float = 120.0):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode() if body is not None else None
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=timeout) as r:
        return json.load(r)


def owner_token() -> str:
    return json.loads((Path.home() / ".war-terminal" / "tokens.json").read_text(encoding="utf-8-sig"))["owner"]


def freshness(items: list[dict], today: str) -> dict:
    open_ = [i for i in items if i.get("status") == "open"]
    stale = [i["key"] for i in open_ if i.get("deadline") and str(i["deadline"])[:10] < today]
    ted = [i for i in items if (i.get("source_url") or "").startswith("https://ted.europa.eu")]
    bad = [i["key"] for i in ted if not i["source_url"].startswith(DETAIL)]
    return {"total": len(items), "open": len(open_), "open_past_deadline": len(stale), "stale_keys": stale[:20],
            "ted": len(ted), "ted_bad_link_form": len(bad), "bad_link_keys": bad[:20]}


def ted_api_exists(ids: list[str]) -> set[str]:
    if not ids:
        return set()
    q = {"query": "publication-number IN (" + " ".join(ids) + ")", "fields": ["publication-number"], "limit": len(ids)}
    return {n["publication-number"] for n in _req(TED_API, body=q, timeout=60).get("notices", [])}


def browser_check(urls: list[str]) -> list[dict]:
    from playwright.sync_api import sync_playwright
    out = []
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        try:
            pg = b.new_page()
            for u in urls:
                nid = u.rsplit("/", 1)[1]
                try:
                    r = pg.goto(u, wait_until="networkidle", timeout=60000)
                    title = pg.title()
                    ok = nid in title and "not found" not in title.lower()
                    out.append({"url": u, "http": r.status if r else None, "title": title[:120], "ok": ok})
                except Exception as e:  # noqa: BLE001 - one slow page must not hide the others
                    out.append({"url": u, "error": type(e).__name__, "ok": False})
        finally:
            b.close()
    return out


def run(sample: int, refresh: bool, browser: bool) -> dict:
    today = dt.date.today().isoformat()
    rep: dict = {"date": today, "at": dt.datetime.now().isoformat(timespec="seconds"), "problems": []}
    tok = owner_token()
    if refresh:
        try:
            r = _req(BASE + "/ingest", tok, {"source": "ted", "days": 7, "limit": 200}, timeout=300)
            rep["refresh"] = {k: r.get(k) for k in ("fetched", "new", "updated")}
        except Exception as e:  # noqa: BLE001 - a failed refresh is reported, the checks still run on stored data
            rep["refresh"] = {"error": f"{type(e).__name__}: {getattr(e, 'code', '')}"}
            rep["problems"].append("обновление TED не прошло")
    items = _req(BASE + "/tenders?limit=100000", tok)["items"]
    rep["freshness"] = f = freshness(items, today)
    if f["open_past_deadline"]:
        rep["problems"].append(f"{f['open_past_deadline']} тендеров «открыты» с прошедшим сроком")
    if f["ted_bad_link_form"]:
        rep["problems"].append(f"{f['ted_bad_link_form']} ссылок TED в нерабочем формате")
    pool = [i for i in items if i.get("status") == "open" and (i.get("source_url") or "").startswith(DETAIL)]
    picks = random.sample(pool, min(sample, len(pool)))
    ids = [i["source_url"].rsplit("/", 1)[1] for i in picks]
    try:
        found = ted_api_exists(ids)
        rep["ted_api"] = {"checked": ids, "missing": [x for x in ids if x not in found]}
        if rep["ted_api"]["missing"]:
            rep["problems"].append(f"TED API не знает {len(rep['ted_api']['missing'])} из {len(ids)} тендеров")
    except Exception as e:  # noqa: BLE001
        rep["ted_api"] = {"error": type(e).__name__}
        rep["problems"].append("TED API недоступен")
    if browser:
        try:
            rep["browser"] = bc = browser_check([i["source_url"] for i in picks])
            bad = [x for x in bc if not x["ok"]]
            if bad:
                rep["problems"].append(f"браузер: {len(bad)} из {len(bc)} страниц TED не открылись")
        except Exception as e:  # noqa: BLE001
            rep["browser"] = {"error": type(e).__name__}
            rep["problems"].append("браузерная проверка не запустилась")
    rep["verdict"] = "FAIL" if rep["problems"] else "PASS"
    return rep


def out_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Bossman" / "CommandCenter" / "war-terminal-daily"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=5)
    ap.add_argument("--no-refresh", action="store_true")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--no-pult", action="store_true")
    a = ap.parse_args(argv)
    try:
        rep = run(a.sample, not a.no_refresh, not a.no_browser)
    except Exception as e:  # noqa: BLE001 - terminal down / token missing
        rep = {"date": dt.date.today().isoformat(), "verdict": "UNREACHABLE", "problems": [f"War Terminal недоступен: {type(e).__name__}"]}
    d = out_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{rep['date']}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: rep.get(k) for k in ("verdict", "problems", "freshness", "refresh")}, ensure_ascii=False))
    if rep["verdict"] != "PASS" and not a.no_pult:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        try:
            import pult_note
            pult_note.send("War Terminal, ежедневная проверка " + rep["date"] + ": " + rep["verdict"] + "\n- " + "\n- ".join(rep["problems"]))
        except Exception as e:  # noqa: BLE001 - the JSON report is already on disk
            print("pult send failed:", type(e).__name__, file=sys.stderr)
    return {"PASS": 0, "FAIL": 1}.get(rep["verdict"], 2)


if __name__ == "__main__":
    raise SystemExit(main())
