"""Training SwapMe deal end to end on a TEMP «Обмен» backend, with a stopwatch (owner item 7, 01.11.2026).

    python tools/prove_swapme_flow.py --tmp <dir> --out docs/audits/2026-11-01-swapme-flow

What it does (no real deal, no messages, no wallets, no payments):
 1. temp data dirs for everything (BCC_DATA_DIR, LOCALAPPDATA, mobile, exchange book, FX log) — the owner's real
    exchange book and FX log are only stat()-ed before/after to prove they were not written (never opened);
 2. starts its OWN `python -m bossmobile serve` (the «Обмен» tab backend) on a free loopback port from this tree,
    upstream Bossman/War/Ollama URLs point at a closed port; logs in with the temp pairing token (cookie + CSRF);
 3. stopwatch t0: one visit of the existing War Terminal FX collector (CNB daily fixing + ECB): two public GETs through
    its allow-listed HTTP client, no language model;
 4. deal A over the JSON API: quote -> client agreed (pending) -> actual amounts confirmed -> statement CSV + text;
 5. deal B over the tab's text route (/m/api/exchange): «сделка … учебная» -> «клиент согласен» -> «обмен выполнен» -> «выписка»;
 6. negative probes over the API (each must be refused and write nothing), STOP probe, no-CSRF probe;
 7. writes receipt.json + statement.csv/.txt into --out, stops only the process it started.
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False).stdout.strip()


def stat_only(p: Path) -> dict:
    """Existence, size and mtime of an owner file — the file is never opened."""
    try:
        st = p.stat()
        return {"exists": True, "size": st.st_size, "mtime_ns": st.st_mtime_ns}
    except OSError:
        return {"exists": False}


class Client:
    def __init__(self, base: str):
        self.base, self.csrf = base, ""
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, method: str, path: str, body=None, *, csrf: bool = True, timeout: float = 60):
        h = {"Content-Type": "application/json"}
        if csrf and self.csrf:
            h["X-BM-CSRF"] = self.csrf
        req = urllib.request.Request(self.base + path, method=method, headers=h,
                                     data=json.dumps(body).encode() if body is not None else None)
        t = time.perf_counter()
        try:
            with self.opener.open(req, timeout=timeout) as r:
                raw, status, ctype = r.read(), r.status, r.headers.get("content-type", "")
        except urllib.error.HTTPError as e:
            raw, status, ctype = e.read(), e.code, e.headers.get("content-type", "")
        ms = round((time.perf_counter() - t) * 1000, 1)
        if "json" in ctype:
            return status, json.loads(raw.decode("utf-8")), ms
        return status, raw.decode("utf-8-sig", "replace"), ms


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tmp", default="", help="temp root (default: a fresh system temp dir)")
    ap.add_argument("--out", default=str(ROOT / "docs" / "audits" / "2026-11-01-swapme-flow"))
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    # the owner's real files: located with the REAL environment, stat()-ed only
    sys.path.insert(0, str(ROOT))
    from bossman_shared.exchange_assistant.rates import default_fx_db
    from bossman_shared.exchange_assistant.store import default_db
    owner_files = {"exchange_book": default_db(dict(os.environ)), "fx_log": default_fx_db(dict(os.environ))}
    owner_before = {k: stat_only(p) for k, p in owner_files.items()}

    tmp = Path(a.tmp or tempfile.mkdtemp(prefix="swapme-flow-")).resolve()
    tmp.mkdir(parents=True, exist_ok=True)
    dead = free_port()                         # nothing listens there: no upstream Bossman / War / Ollama is reached
    env = dict(os.environ)
    env.update({
        "LOCALAPPDATA": str(tmp / "localappdata"), "BCC_DATA_DIR": str(tmp / "bcc"),
        "BOSSMAN_MOBILE_DATA": str(tmp / "mobile"), "BOSSMAN_EXCHANGE_DIR": str(tmp / "exchange"),
        "BOSSMAN_FX_DIR": str(tmp / "fx"), "BOSSMAN_TOKEN_FILE": str(tmp / "bcc" / "token"),
        "NICHE_RESEARCH_DIR": str(tmp / "niche"), "BOSSMAN_URL": f"http://127.0.0.1:{dead}",
        "BOSSMAN_MOBILE_WAR": f"http://127.0.0.1:{dead}", "BOSSMAN_MOBILE_OLLAMA": f"http://127.0.0.1:{dead}",
        "PYTHONPATH": os.pathsep.join([str(ROOT), str(ROOT / "apps" / "bossman-mobile"), str(ROOT / "apps" / "war-terminal")]),
        "PYTHONIOENCODING": "utf-8",
    })
    for k in ("LOCALAPPDATA", "BCC_DATA_DIR", "BOSSMAN_MOBILE_DATA", "BOSSMAN_EXCHANGE_DIR", "BOSSMAN_FX_DIR"):
        Path(env[k]).mkdir(parents=True, exist_ok=True)
        assert Path(env[k]).resolve().is_relative_to(tmp), k
    (tmp / "bcc" / "computer").mkdir(parents=True, exist_ok=True)

    # temp pairing token, created exactly as the server will read it (same Windows user)
    sys.path.insert(0, str(ROOT / "apps" / "bossman-mobile"))
    from bossmobile.auth import load_or_create_pairing
    token = load_or_create_pairing(Path(env["BOSSMAN_MOBILE_DATA"]))

    port = free_port()
    log = open(tmp / "bossmobile.log", "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-m", "bossmobile", "serve", "--port", str(port)], cwd=str(tmp), env=env,
                            stdout=log, stderr=subprocess.STDOUT)
    receipt: dict = {"topic": "SwapMe practical flow — training deal (owner item 7)", "kind": "TRAINING_SYNTHETIC",
                     "source_sha": git("rev-parse", "HEAD"), "source_dirty_files": git("status", "--porcelain").splitlines(),
                     "branch": git("rev-parse", "--abbrev-ref", "HEAD"), "started_at": datetime.now(timezone.utc).isoformat(),
                     "runtime": {"server": "python -m bossmobile serve (this tree, PYTHONPATH)", "port": port, "pid": proc.pid,
                                 "upstream_urls_point_to_closed_port": dead, "temp_root": "<temp>"},
                     "rates": "live: CNB daily fixing + ECB reference rates (public, read-only)"}
    steps: list[dict] = []
    try:
        c = Client(f"http://127.0.0.1:{port}")
        t_up = time.perf_counter()
        while time.perf_counter() - t_up < 60:
            try:
                if c.call("GET", "/m/api/health", csrf=False, timeout=2)[0] == 200:
                    break
            except OSError:
                pass
            time.sleep(0.3)
        else:
            raise RuntimeError("temp server did not come up")
        receipt["runtime"]["startup_s"] = round(time.perf_counter() - t_up, 2)
        st, body, _ = c.call("POST", "/m/api/login", {"token": token}, csrf=False)
        assert st == 200, (st, body)
        c.csrf = body["csrf"]
        st_nocsrf = c.call("POST", "/m/api/exchange/flow/quote", {"client": "x", "side": "client_buys", "base": "USD",
                                                                     "amount": "1", "markup_pct": "1"}, csrf=False)[0]

        def step(name: str, fn):
            t = time.perf_counter()
            out = fn()
            steps.append({"step": name, "ms": round((time.perf_counter() - t) * 1000, 1), **out})
            return out

        # ---------------------------------------------------------------- stopwatch: rate -> statement
        t0 = time.perf_counter()
        from bossman_shared.exchange_assistant import flow
        sys.path.insert(0, str(ROOT / "apps" / "war-terminal"))
        from warterminal.connectors import http as http_mod
        fetch = lambda u: http_mod.get_bytes(u, timeout=60)   # noqa: E731  (the collector's own allow-listed client)
        col = step("1_rate_fetch_war_terminal_collector", lambda: {"result": {k: v for k, v in flow.collect_official_rates(
            env["BOSSMAN_FX_DIR"], fetch).items() if k in ("status", "written", "duplicates", "observed_at", "notes", "sources")}})
        if col["result"]["status"] != "OK":
            raise RuntimeError(f"rate fetch failed: {col['result']}")
        step("2_rates_in_tab", lambda: (lambda r: {"http": r[0], "reply": r[1]["reply"]})(
            c.call("POST", "/m/api/exchange", {"text": "курс"})))
        deal_a = {"client": "train-anna", "side": "client_buys", "base": "USDT", "amount": "1000", "markup_pct": "3",
                  "assume_usdt_usd_parity": True, "training": True}
        qa = step("3_quote_api", lambda: (lambda r: {"http": r[0], "record": r[1].get("record"), "detail": r[1].get("detail")})(
            c.call("POST", "/m/api/exchange/flow/quote", deal_a)))
        rec = qa["record"]
        assert qa["http"] == 200 and rec["status"] == "quote", qa
        qid = rec["quote_id"]
        pa = step("4_client_agreed_pending", lambda: (lambda r: {"http": r[0], "status": r[1]["record"]["status"],
                                                                 "counts_as_revenue": r[1]["record"]["counts_as_revenue"]})(
            c.call("POST", f"/m/api/exchange/flow/{qid}/accept")))
        assert pa["status"] == "pending"
        ca = step("5_confirm_actual_amounts", lambda: (lambda r: {"http": r[0], "record": r[1]["record"]})(
            c.call("POST", f"/m/api/exchange/flow/{qid}/confirm",
                   {"actual_in": rec["client_gives"]["quoted"], "actual_out": rec["client_gets"]["quoted"]})))
        assert ca["record"]["status"] == "confirmed"
        today = datetime.now().strftime("%Y-%m-%d")
        csv_out = step("6_statement_csv", lambda: (lambda r: {"http": r[0], "bytes": len(r[1].encode("utf-8")), "_body": r[1]})(
            c.call("GET", f"/m/api/exchange/statement?client=train-anna&start={today}&end={today}&format=csv")))
        txt_out = step("7_statement_text", lambda: (lambda r: {"http": r[0], "_body": r[1]})(
            c.call("GET", "/m/api/exchange/statement?client=train-anna&format=text")))
        stopwatch_a = round(time.perf_counter() - t0, 3)

        # ---------------------------------------------------------------- deal B through the tab's text route
        t1 = time.perf_counter()
        nb = step("8_tab_deal_text", lambda: (lambda r: {"http": r[0], "ok": r[1]["ok"], "quote_id": r[1]["quote_id"],
                                                         "reply": r[1]["reply"]})(
            c.call("POST", "/m/api/exchange", {"text": "сделка train-bob продать 200 eur за czk наценка 2% учебная"})))
        assert nb["ok"], nb
        qb = nb["quote_id"]
        step("9_tab_client_agreed", lambda: (lambda r: {"http": r[0], "ok": r[1]["ok"], "reply": r[1]["reply"]})(
            c.call("POST", "/m/api/exchange", {"text": f"клиент согласен {qb}"})))
        rec_b = c.call("GET", f"/m/api/exchange/flow/{qb}")[1]["record"]
        step("10_tab_confirm", lambda: (lambda r: {"http": r[0], "ok": r[1]["ok"], "reply": r[1]["reply"]})(
            c.call("POST", "/m/api/exchange", {"text": f"обмен выполнен {qb} приняли {rec_b['client_gives']['quoted']} "
                                                       f"выдали {rec_b['client_gets']['quoted']}"})))
        sb = step("11_tab_statement", lambda: (lambda r: {"http": r[0], "ok": r[1]["ok"], "reply": r[1]["reply"]})(
            c.call("POST", "/m/api/exchange", {"text": "выписка train-bob все"})))
        stopwatch_b = round(time.perf_counter() - t1, 3)

        # ---------------------------------------------------------------- negatives (must be refused, write nothing)
        crm_before = len(c.call("GET", "/m/api/exchange/statement")[1]["rows"])
        neg = {}
        for name, patch in (("markup_missing", {"markup_pct": None}), ("markup_out_of_range", {"markup_pct": "12"}),
                            ("amount_negative", {"amount": "-1000"}), ("amount_zero", {"amount": "0"}),
                            ("amount_nan", {"amount": "NaN"}), ("amount_not_a_number", {"amount": "тысяча"}),
                            ("usdt_without_rate_or_assumption", {"assume_usdt_usd_parity": False}),
                            ("rate_older_than_1h", {"max_rate_age_hours": 1})):
            st, b, _ = c.call("POST", "/m/api/exchange/flow/quote", {**deal_a, "client": "train-neg", **patch})
            neg[name] = {"http": st, "message": (b.get("detail") or {}).get("message") if isinstance(b, dict) else str(b)}
        fresh = c.call("POST", "/m/api/exchange/flow/quote", {**deal_a, "client": "train-tol"})[1]["record"]
        st, b, _ = c.call("POST", f"/m/api/exchange/flow/{fresh['quote_id']}/confirm",
                          {"actual_in": "1", "actual_out": fresh["client_gets"]["quoted"]})
        neg["confirm_beyond_tolerance"] = {"http": st, "message": b["detail"]["message"]}
        stop = tmp / "bcc" / "computer" / "STOP"
        stop.write_text("training STOP probe", encoding="utf-8")
        st, b, _ = c.call("POST", "/m/api/exchange/flow/quote", {**deal_a, "client": "train-stop"})
        stop.unlink()
        neg["stop_blocks_write"] = {"http": st, "message": b["detail"]["message"]}
        neg["no_csrf"] = {"http": st_nocsrf}
        crm_after = len(c.call("GET", "/m/api/exchange/statement")[1]["rows"])
        neg_ok = (all(v["http"] == 422 for k, v in neg.items() if k not in ("stop_blocks_write", "no_csrf"))
                  and neg["stop_blocks_write"]["http"] == 423 and neg["no_csrf"]["http"] == 403
                  and crm_after == crm_before + 1)          # +1 = the train-tol quote that stays an unconfirmed quote

        # ---------------------------------------------------------------- outputs
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "statement_train-anna.csv").write_text(csv_out.pop("_body"), encoding="utf-8")
        (out / "statement_train-anna.txt").write_text(txt_out.pop("_body"), encoding="utf-8")
        (out / "statement_train-bob_tab.txt").write_text(sb["reply"], encoding="utf-8")
        owner_after = {k: stat_only(p) for k, p in owner_files.items()}
        final = c.call("GET", "/m/api/exchange/statement")[1]
        receipt.update({
            "steps": steps,
            "stopwatch": {"deal_a_api_rate_to_statement_s": stopwatch_a, "deal_b_tab_text_route_s": stopwatch_b,
                          "note": "deal A includes one visit of the CNB+ECB collector (two GETs ≥5 s apart by its own politeness rule)"},
            "rate": {"leg": rec["rate_source"], "value": rec["market_rate"], "as_of": rec["rate_as_of"],
                     "rate_date": rec["rate_date"], "assumptions": rec["assumptions"]},
            "deal_a": {k: ca["record"][k] for k in ("quote_id", "deal_id", "client", "status", "counts_as_revenue", "training",
                                                    "client_gives", "client_gets", "markup_pct", "manager_rate", "formula",
                                                    "rounding", "markup_value")},
            "final_statement_counts": final["counts"], "final_confirmed_totals": final["confirmed_totals"],
            "negatives": neg, "negatives_ok": neg_ok,
            "owner_data_untouched": {"before": owner_before, "after": owner_after, "equal": owner_before == owner_after,
                                     "method": "stat() only, files never opened"},
            "external_effects": {"messages_sent": 0, "payments": 0, "wallets": 0,
                                 "network": "two public GETs (cnb.cz, ecb.europa.eu) by the existing War Terminal collector"},
        })
        ok = (neg_ok and owner_before == owner_after and ca["record"]["status"] == "confirmed"
              and csv_out["http"] == 200 and sb["ok"] and "подтверждена фактическими суммами" in sb["reply"])
        receipt["verdict"] = "PASS" if ok else "FAIL"
        receipt["finished_at"] = datetime.now(timezone.utc).isoformat()
        (out / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        print(json.dumps({"verdict": receipt["verdict"], "stopwatch": receipt["stopwatch"], "negatives_ok": neg_ok,
                          "owner_untouched": owner_before == owner_after}, ensure_ascii=False))
        return 0 if ok else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
