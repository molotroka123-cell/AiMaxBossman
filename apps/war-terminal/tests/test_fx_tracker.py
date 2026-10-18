"""Daily FX tracker: parsers on saved pages (no live network), append-only log, model cross-check negative controls, cross-rates, idle gate."""
import json
import pathlib
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from conftest import Env
from warterminal import fx_tracker as fx
from warterminal.translate import RouteError

FIX = pathlib.Path(__file__).parent / "fixtures_fx"
CNB = (FIX / "cnb_daily.txt").read_text(encoding="utf-8")
ECB = (FIX / "ecb_daily.xml").read_text(encoding="utf-8")
T0 = datetime(2026, 10, 18, 7, 30, tzinfo=timezone.utc)
D = Decimal


def fake_get(cnb=CNB, ecb=ECB):
    def get(url):
        if "cnb.cz" in url:
            return cnb.encode()
        if "ecb.europa.eu" in url:
            return ecb.encode()
        raise AssertionError("unexpected URL " + url)          # tests never reach the network
    return get


@pytest.fixture
def store(tmp_path):
    s = fx.FxStore(tmp_path / "fx.db")
    yield s
    s.close()


class Gw:
    """Stand-in for the Bossman gateway: a free route whose reply is decided by `reply(pair_list)`."""

    def __init__(self, values=None, cost=None, err=None):
        self.values, self.cost, self.err, self.calls = values, cost, err, 0

    def models(self):
        return [{"id": 100, "alias": "free-a", "name": "deepseek-v4.1-flash", "price_in": 0, "price_out": 0, "caps": {"free_tier": "experiential_labs_free_plan", "chat": True}, "status": "online"}]

    def free_chat(self, mid, messages, max_tokens):
        self.calls += 1
        assert max_tokens >= 512
        if self.err:
            raise self.err
        user = messages[-1]["content"]
        v = self.values("cnb" if "Country|Currency" in user else "ecb")
        return {"text": json.dumps(v), "model": "free-a", "usage": {"cost": self.cost}}


def agree(src):
    return {"USDCZK": 21.622, "EURCZK": 24.325} if src == "cnb" else {"EURUSD": 1.1253, "EURCZK": 24.323}


def run(store, **kw):
    kw.setdefault("get", fake_get())
    kw.setdefault("now", T0)
    kw.setdefault("sleep", lambda s: None)
    kw.setdefault("log", lambda *a: None)
    return fx.collect(store, **kw)


# ---------------------------------------------------------------- parsers
def test_cnb_and_ecb_parsers_read_the_saved_pages():
    c, e = fx.parse_cnb(CNB), fx.parse_ecb(ECB)
    assert c["rate_date"] == "2026-10-16" and c["pairs"]["USDCZK"][0] == D("21.622") and c["pairs"]["EURCZK"][0] == D("24.325")
    assert e["rate_date"] == "2026-10-16" and e["pairs"]["EURUSD"][0] == D("1.1253") and e["pairs"]["EURCZK"][0] == D("24.323")
    assert len(c["pairs"]["USDCZK"][1]) <= 200


def test_garbage_pages_give_no_record(store):
    assert fx.parse_cnb("<html>captcha</html>") == {} and fx.parse_ecb("<html>nope</html>") == {} and fx.parse_cnb("") == {}
    res = run(store, get=fake_get(cnb="<html>garbage</html>", ecb="not xml"))
    assert res["status"] == "NO_DATA" and res["written"] == 0 and store.rows() == []


def test_implausible_number_is_not_recorded():
    bad = CNB.replace("USA|dollar|1|USD|21.622", "USA|dollar|1|USD|0.0216")
    assert "USDCZK" not in fx.parse_cnb(bad)["pairs"] and "EURCZK" in fx.parse_cnb(bad)["pairs"]
    assert fx.parse_ecb(ECB.replace("rate='1.1253'", "rate='112.53'")).get("pairs", {}).get("EURUSD") is None


# ---------------------------------------------------------------- store
def test_collect_writes_four_records_and_dedupes_the_same_minute(store):
    r = run(store)
    assert r["status"] == "OK" and r["written"] == 4 and r["model"] == "NOT_RUN"
    rows = store.rows()
    assert {(x["source"], x["pair"]) for x in rows} == {("cnb", "USDCZK"), ("cnb", "EURCZK"), ("ecb", "EURUSD"), ("ecb", "EURCZK")}
    assert all(x["parser"] == "regex" and x["flags"] == "model_unchecked" and x["observed_at"].endswith("Z") and x["source_url"].startswith("https://") for x in rows)
    r2 = run(store, force=True)
    assert r2["written"] == 0 and r2["duplicates"] == 4 and len(store.rows()) == 4
    run(store, now=datetime(2026, 10, 18, 7, 31, tzinfo=timezone.utc), force=True)
    assert len(store.rows()) == 8                                # a different minute is a new observation


def test_log_is_append_only(store):
    run(store)
    with pytest.raises(sqlite3.DatabaseError):
        store.db.execute("UPDATE fx_obs SET rate='1'")
    with pytest.raises(sqlite3.DatabaseError):
        store.db.execute("DELETE FROM fx_obs")


def test_two_visits_a_day_limit_and_one_backup_per_day(store, tmp_path):
    assert run(store)["status"] == "OK"
    assert run(store, now=datetime(2026, 10, 18, 8, 0, tzinfo=timezone.utc))["status"] == "OK"
    third = run(store, now=datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc))
    assert third["status"] == "VISIT_LIMIT" and third["written"] == 0
    assert len(list((tmp_path / "backup").glob("fx-*.db"))) == 1
    b = sqlite3.connect(next((tmp_path / "backup").glob("fx-*.db")))
    assert b.execute("SELECT COUNT(*) FROM fx_obs").fetchone()[0] >= 4


def test_sources_are_fetched_at_least_5_seconds_apart(store):
    waits = []
    run(store, sleep=waits.append)
    assert waits == [5.0]


# ---------------------------------------------------------------- model cross-check
def test_model_agreement_marks_records_model_ok(store):
    gw = Gw(values=agree)
    r = run(store, gateway=gw)
    assert r["model"] == "OK" and gw.calls == 2
    assert all(x["flags"] == "model_ok" and x["parser"] == "regex" for x in store.rows())


def test_disagreement_over_half_percent_stores_both_and_hides_from_reports(store, tmp_path):
    gw = Gw(values=lambda s: {"USDCZK": 21.95, "EURCZK": 24.325} if s == "cnb" else {"EURUSD": 1.1253, "EURCZK": 24.323})   # +1.5 % on USDCZK
    run(store, gateway=gw)
    rows = store.rows()
    usd = [x for x in rows if x["pair"] == "USDCZK"]
    assert sorted(x["parser"] for x in usd) == ["model", "regex"] and all("disagreement" in x["flags"] for x in usd)
    assert {x["pair"] for x in fx.usable(rows)} == {"EURCZK", "EURUSD"} and not any(x["pair"] == "USDCZK" for x in fx.usable(rows))
    assert fx.core_rates(fx.snapshot(rows)) is None                 # a disagreeing core pair breaks the report instead of feeding it a bad number
    gap_small = Gw(values=lambda s: {"USDCZK": 21.70, "EURCZK": 24.325} if s == "cnb" else agree("ecb"))   # +0.36 %: within tolerance
    s2 = fx.FxStore(tmp_path / "s2" / "fx.db")
    run(s2, gateway=gap_small)
    assert all("disagreement" not in x["flags"] for x in s2.rows())


def test_cost_above_zero_aborts_drops_the_reply_and_halts_the_day(store, tmp_path):
    halt = tmp_path / "halt.txt"
    gw = Gw(values=lambda s: {"USDCZK": 99.0}, cost=0.0003)
    r = run(store, gateway=gw, halt_file=halt)
    assert r["model"] == "COST_ABORT" and gw.calls == 1             # the second source is not even asked
    assert halt.exists() and not any(x["parser"] == "model" for x in store.rows())
    assert all(x["flags"] == "model_unchecked" for x in store.rows())
    again = Gw(values=agree)
    run(store, gateway=again, halt_file=halt, now=datetime(2026, 10, 18, 7, 35, tzinfo=timezone.utc), force=True)
    assert again.calls == 0                                         # halted for the rest of the UTC day


def test_missing_gateway_route_is_not_run_not_failure(store):
    gw = Gw(err=RouteError("absent", 405))
    r = run(store, gateway=gw)
    assert r["status"] == "OK" and r["model"] == "NOT_RUN" and r["written"] == 4
    assert any("перезапустить" in n for n in r["notes"])


def test_layout_change_keeps_model_value_flagged_and_unused(store):
    gw = Gw(values=lambda s: {"USDCZK": 21.62, "EURCZK": 24.33} if s == "cnb" else {"EURUSD": 1.1253, "EURCZK": 24.323})
    r = run(store, gateway=gw, get=fake_get(cnb="NEW LAYOUT\nUSD 21.62 EUR 24.33\nCountry|Currency"))
    model_rows = [x for x in store.rows() if x["parser"] == "model"]
    assert {x["pair"] for x in model_rows} == {"USDCZK", "EURCZK"} and all("model_only" in x["flags"] for x in model_rows)
    assert not [x for x in fx.usable(store.rows()) if x["source"] == "cnb"]
    assert any("не распознана" in n for n in r["notes"])


# ---------------------------------------------------------------- cross-rates, calculator, statistics
def seed(store, day, usdczk, eurczk, eurusd, minute="07:30"):
    at = f"{day}T{minute}:00Z"
    for src, pair, v in (("cnb", "USDCZK", usdczk), ("cnb", "EURCZK", eurczk), ("ecb", "EURUSD", eurusd), ("ecb", "EURCZK", eurczk)):
        store.append(observed_at=at, pair=pair, rate=v, source=src, source_url="https://x.test/", rate_date=day, excerpt="t", parser="regex")


def test_cross_rates_triangle_and_calculator(store):
    seed(store, "2026-10-18", "21.622", "24.325", "1.1253")
    seed(store, "2026-12-31", "22.000", "24.000", "1.0909")
    rows = store.rows()
    core = fx.core_rates(fx.snapshot(rows, "2026-10-18"))
    d = fx.derive(core)
    assert round(d["USDEUR"] * core["EURUSD"], 12) == 1 and round(d["CZKUSD"] * core["USDCZK"], 12) == 1
    tri = fx.triangle(core)
    assert tri["via_eur_usd_czk"] == D("24.325") / D("1.1253")
    assert round(tri["spread_pct"], 3) == round((D("24.325") / D("1.1253") - D("21.622")) / D("21.622") * 100, 3)
    k = fx.calc(rows, "1000")
    assert k["status"] == "OK" and k["baseline_day"] == "2026-10-18" and k["today_day"] == "2026-12-31"
    assert k["baseline"]["CZK"] == "21622.00" and k["today"]["CZK"] == "22000.00" and k["diff"]["CZK"] == {"abs": "378.00", "pct": "1.748"}
    assert k["baseline"]["EUR"] == str((D(1000) / D("1.1253")).quantize(D("0.01"))) and k["usdt_usd"]["measured"] is False
    assert fx.calc(rows, "1000", baseline="2026-12-31")["diff"]["CZK"]["abs"] == "0.00"
    assert fx.calc(rows, "abc")["status"] == "BAD_AMOUNT" and fx.calc([], "1")["status"] == "NO_BASELINE"


def test_statistics_are_plain_numbers(store):
    for i, v in enumerate(["21.0", "21.5", "21.0", "22.0"], start=1):
        seed(store, f"2026-10-{i:02d}", v, "24.0", "1.1")
    s = fx.build_report(store.rows())["stats"]["cnb:USDCZK"]
    assert (s["n"], s["min"], s["max"], s["mean"], s["first"], s["last"]) == (4, 21.0, 22.0, 21.375, 21.0, 22.0)
    assert s["change_pct_total"] == pytest.approx(4.7619, abs=1e-3) and s["last_daily_change_pct"] == pytest.approx(4.7619, abs=1e-3)
    assert s["volatility_daily_pct"] is not None and s["stdev"] is not None


def test_report_has_no_forecast_words(store):
    seed(store, "2026-10-18", "21.622", "24.325", "1.1253")
    text = fx.report_text(fx.build_report(store.rows()))
    low = text.lower()
    assert "прогноз" in low and "комисси" in low and "не измерено" in low            # the honest disclaimers are present
    assert "will grow" not in low and "рекомендуем" not in low and "купить" not in low


def test_year_end_files(store, tmp_path):
    seed(store, "2026-10-18", "21.622", "24.325", "1.1253")
    out = fx.write_year_end(store, tmp_path / "out", amount="500", today="2026-12-31")
    md, cs = pathlib.Path(out["markdown"]), pathlib.Path(out["csv"])
    assert md.name == "fx-year-end-2026-12-31.md" and "500" in md.read_text(encoding="utf-8")
    head, *body = cs.read_text(encoding="utf-8").splitlines()
    assert head.startswith("observed_at,rate_date,pair,rate") and len(body) == 4


# ---------------------------------------------------------------- idle gate
def test_loading_or_low_ram_blocks_but_a_generating_job_does_not():
    assert fx.jobs_busy([{"status": "loading"}]) == ["loading"]
    assert fx.jobs_busy([{"status": "generating"}, {"status": "queued"}], free_gb=64) == []          # owner rule 18.10: generating does not block
    assert fx.jobs_busy([{"status": "completed"}, {"status": "failed"}, {"status": "cancelled"}]) == []
    assert fx.jobs_busy([], free_gb=12.0) == ["low_ram_12GB"] and fx.jobs_busy([], free_gb=25.0) == []


def test_busy_pc_retries_three_times_then_skips():
    sleeps = []
    out = fx.wait_idle(lambda: [{"status": "loading"}], sleep=sleeps.append, log=lambda *a: None, ram=lambda: 64.0)
    assert out["status"] == "SKIPPED_BUSY" and sleeps == [1200, 1200, 1200] and out["attempts"] == 4


def test_idle_after_one_retry_and_bossman_down_and_unreadable_queue():
    seq = iter([[{"status": "loading"}], []])
    sleeps = []
    assert fx.wait_idle(lambda: next(seq), ram=lambda: 64.0, sleep=sleeps.append, log=lambda *a: None)["status"] == "IDLE" and sleeps == [1200]

    def down():
        raise ConnectionError("refused")
    assert fx.wait_idle(down, sleep=lambda s: None, ram=lambda: 64.0)["status"] == "IDLE"

    def broken():
        raise RuntimeError("HTTP 500")
    assert fx.wait_idle(broken, ram=lambda: 64.0, sleep=lambda s: None, log=lambda *a: None)["status"] == "SKIPPED_BUSY"      # unreadable queue is never read as idle


# ---------------------------------------------------------------- API + UI wiring
def test_api_report_and_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("BOSSMAN_FX_DIR", str(tmp_path / "fxdir"))
    e = Env(tmp_path / "wt")
    assert e.req("GET", "/fx/track/report", None).status_code == 401
    empty = e.req("GET", "/fx/track/report", "viewer").json()
    assert empty["n_rows"] == 0 and empty["calc"]["status"] == "NO_BASELINE" and empty["xe"]["used"] is False
    st = fx.open_store()
    seed(st, "2026-10-18", "21.622", "24.325", "1.1253")
    st.close()
    r = e.req("GET", "/fx/track/report?amount=250&baseline=2026-10-18", "viewer").json()
    assert r["calc"]["status"] == "OK" and r["calc"]["baseline"]["CZK"] == "5405.50" and r["cross"]["USDCZK"] == "21.622"
    assert e.req("GET", "/fx/track/report?amount=-5", "viewer").status_code == 422
    assert e.req("GET", "/fx/track/report?baseline=18.10.2026", "viewer").status_code == 422


def test_ui_has_the_fx_tab():
    ui = pathlib.Path(__file__).resolve().parents[1] / "warterminal" / "ui" / "app.js"
    src = ui.read_text(encoding="utf-8")
    assert 'T("Курсы / FX")' in src and "fx: viewFx" in src and "/fx/track/report" in src
