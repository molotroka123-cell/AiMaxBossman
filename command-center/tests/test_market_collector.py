"""Twitch OI/CVD collector: data-only, no fabrication, fresh frames only.

Owner run 2026-09-24 (docs/tomorrow-2026-09-24/JEV_TWITCH_OI_CVD_COLLECTOR.md)
plus owner scenarios 14 (Jev escalates video/canvas) and 15 (UNREADABLE, not a
hallucinated number). The fake reader stands in for the local vision model; the
frames are synthetic images with badges drawn where the real layout puts them.
"""
from __future__ import annotations

import asyncio
import csv
import importlib
import json
import pkgutil
import re
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from bcc import market
from bcc.market import collector, extract, routing, schema
from bcc.market.collector import Capture, Collector, build_record
from bcc.market.ledger import Ledger


class FakeReader:
    """Answers by badge colour marker; counts calls. `script` maps a metric to a
    list of answers returned in order (to simulate disagreeing reads)."""

    identity = "fake-vision"

    def __init__(self, answers: dict[str, list[str]] | None = None):
        self.answers = answers or {}
        self.calls = 0

    def read(self, image: Image.Image) -> str:
        self.calls += 1
        w, h = image.size
        tag = _tag_of(image)
        seq = self.answers.get(tag)
        if seq:
            return seq.pop(0) if len(seq) > 1 else seq[0]
        return {"cvd": "CVD 74.44B", "oi": "Open Interest 19.57B", "price": "83,865",
                "symbol": "BTC1! · 30 · CME", "liq": "Long Liquidations 65.16K"}.get(tag, "UNREADABLE")


def _tag_of(image: Image.Image) -> str:
    """Synthetic badges encode their identity in the exact fill colour."""
    colours = {(220, 52, 70): "cvd", (234, 52, 77): "oi", (4, 147, 123): "liq",
               (24, 28, 30): "price", (40, 40, 200): "symbol"}
    counts: dict[str, int] = {}
    rgb = image.convert("RGB")
    for px in (rgb.get_flattened_data() if hasattr(rgb, "get_flattened_data") else rgb.getdata()):
        tag = colours.get(px)
        if tag:
            counts[tag] = counts.get(tag, 0) + 1
    return max(counts, key=counts.get) if counts else "none"


def frame(*, cvd_y: int = 660, oi_y: int = 760, with_cvd: bool = True, with_oi: bool = True,
          price: bool = True, symbol: bool = True) -> Image.Image:
    img = Image.new("RGB", (1920, 1080), (11, 19, 37))
    d = ImageDraw.Draw(img)
    if symbol:
        d.rectangle((325, 93, 470, 111), fill=(40, 40, 200))
    if price:
        d.rectangle((1256, 456, 1300, 479), fill=(24, 28, 30))
    if with_cvd:
        d.rectangle((1230, cvd_y, 1295, cvd_y + 11), fill=(220, 52, 70))
    if with_oi:
        d.rectangle((1193, oi_y, 1295, oi_y + 11), fill=(234, 52, 77))
    d.rectangle((1176, 780, 1295, 789), fill=(4, 147, 123))          # liquidations badge (ignored)
    return img


def live(img: Image.Image, sha: str = "a" * 64, t0: float = 10.0, t1: float = 10.4, paused: bool = False):
    return Capture("LIVE", collector.URL, frame=img, frame_sha256=sha, video_t0=t0, video_t1=t1, paused=paused,
                   routing=routing.route({"features": {"video": 1, "canvas": 0}}, target="pixels"))


# ------------------------------------------------------------------ schema


def test_parse_units_and_prices():
    assert schema.parse_abbrev("74.44B") == (74.44, "B")
    assert schema.parse_abbrev("19.57 B") == (19.57, "B")
    assert schema.parse_abbrev("−712.49K") == (-712.49, "K")
    assert schema.parse_abbrev("74.448") is None            # B misread as 8 -> not a number+unit
    assert schema.parse_abbrev("74.44") is None             # unit missing -> not accepted
    assert schema.parse_price("83,865") == 83865.0 and schema.parse_price("84 000.5") == 84000.5
    assert schema.classify_badge("Open Interest 19.57B") == ("oi", "19.57B")
    assert schema.classify_badge("19.57B") is None           # no label -> never assigned to a metric


def test_validator_rejects_fabricated_or_carried_values():
    rec = schema.new_observation(channel="k1m6a", requested_url=collector.URL, resolved_url=None,
                                 stream_state="LIVE")
    assert schema.validate(rec) == []
    rec["metrics"]["oi_value"], rec["metrics"]["oi_unit"] = 19.57, "B"
    errs = schema.validate(rec)
    assert "number_without_fresh_frame" in errs and "number_without_frame_evidence" in errs
    assert "oi_value_without_ocr_raw" in errs
    off = schema.new_observation(channel="k1m6a", requested_url=collector.URL, resolved_url=None,
                                 stream_state="OFFLINE")
    off["metrics"]["cvd_value"], off["metrics"]["cvd_unit"] = 1.0, "B"
    assert "number_while_not_live" in schema.validate(off)
    bad_unit = schema.new_observation(channel="k1m6a", requested_url="u", resolved_url=None, stream_state="LIVE")
    bad_unit["metrics"]["oi_unit"] = "X"
    assert schema.validate(bad_unit)


# ------------------------------------------------------------------ extraction


def test_badges_move_with_the_value_and_labels_decide_the_metric():
    reader = FakeReader()
    out = extract.extract(reader, frame(cvd_y=620, oi_y=700))
    r = out["readings"]
    assert (r["cvd"].status, r["cvd"].value, r["cvd"].unit) == ("VERIFIED", 74.44, "B")
    assert (r["oi"].status, r["oi"].value, r["oi"].unit) == ("VERIFIED", 19.57, "B")
    assert (r["price"].status, r["price"].value) == ("VERIFIED", 83865.0)
    assert out["symbol"]["symbol"] == "BTC1!" and out["symbol"]["exchange"] == "CME"
    assert "liq" not in r                                     # other badges are read but never assigned


def test_disagreeing_reads_are_low_confidence_not_a_number():
    reader = FakeReader({"cvd": ["CVD 74.44B", "CVD 74.49B"]})
    rec = build_record(live(frame()), reader, last_frame_sha=None)
    assert rec["metrics"]["cvd_value"] is None and rec["quality"]["per_metric"]["cvd"]["status"] == "LOW_CONFIDENCE"
    assert rec["metrics"]["oi_value"] == 19.57
    assert rec["evidence"]["ocr_raw"]["cvd"] == ["CVD 74.44B", "CVD 74.49B"]


def test_unit_disagreement_is_ambiguous_unit():
    reader = FakeReader({"oi": ["Open Interest 19.57B", "Open Interest 19.57M"]})
    rec = build_record(live(frame()), reader, last_frame_sha=None)
    assert rec["quality"]["per_metric"]["oi"]["status"] == "AMBIGUOUS_UNIT" and rec["metrics"]["oi_value"] is None


def test_scenario_15_illegible_frame_is_unreadable_not_hallucinated():
    reader = FakeReader({"cvd": ["UNREADABLE"], "oi": ["UNREADABLE"], "price": ["UNREADABLE"]})
    rec = build_record(live(frame()), reader, last_frame_sha=None)
    assert rec["quality"]["status"] == "UNREADABLE"
    assert all(rec["metrics"][k] is None for k in schema.METRIC_KEYS)
    assert schema.validate(rec) == []
    blank = build_record(live(frame(with_cvd=False, with_oi=False, price=False)), FakeReader(), last_frame_sha=None)
    assert blank["quality"]["status"] == "UNREADABLE" and blank["metrics"]["oi_value"] is None


def test_unknown_symbol_drops_every_number():
    rec = build_record(live(frame()), FakeReader({"symbol": ["garbage text"]}), last_frame_sha=None)
    assert rec["quality"]["status"] == "AMBIGUOUS_SYMBOL"
    assert all(rec["metrics"][k] is None for k in schema.METRIC_KEYS)


def test_vision_endpoint_must_be_local():
    with pytest.raises(ValueError):
        extract.OllamaVisionReader(url="https://api.example.com")
    extract.OllamaVisionReader(url="http://127.0.0.1:11434")


def test_cached_reader_keys_on_exact_pixels():
    inner = FakeReader()
    cached = extract.CachedReader(inner)
    a = frame().crop((1228, 658, 1297, 673))
    assert cached.read(a) == cached.read(a.copy()) and inner.calls == 1 and cached.hits == 1
    cached.read(frame(cvd_y=661).crop((1228, 658, 1297, 673)))    # different pixels -> new read
    assert inner.calls == 2


# ------------------------------------------------------------------ freshness


@pytest.mark.parametrize("cap_kwargs", [dict(t0=10.0, t1=10.0), dict(paused=True), dict(sha="f" * 64)])
def test_stale_frame_never_produces_numbers(cap_kwargs):
    reader = FakeReader()
    rec = build_record(live(frame(), **cap_kwargs), reader, last_frame_sha="f" * 64)
    assert rec["quality"]["status"] == "STALE_FRAME" and rec["quality"]["fresh_frame"] is False
    assert all(rec["metrics"][k] is None for k in schema.METRIC_KEYS)
    assert reader.calls == 0                                     # nothing was even read
    assert schema.validate(rec) == []


def test_offline_and_errors_record_status_without_numbers():
    for state, status in (("OFFLINE", "STREAM_OFFLINE"), ("PLAYER_ERROR", "PLAYER_ERROR"),
                          ("LOGIN_REQUIRED", "LOGIN_REQUIRED")):
        rec = build_record(Capture(state, collector.URL, detail="x"), FakeReader(), last_frame_sha=None)
        assert rec["quality"]["status"] == status and schema.validate(rec) == []


def test_implausible_jump_is_flagged_not_corrected():
    rec = build_record(live(frame()), FakeReader(), last_frame_sha=None, last_verified={"oi": 10e9})
    assert "oi_implausible_jump" in rec["quality"]["flags"] and rec["quality"]["status"] == "LOW_CONFIDENCE"
    assert rec["metrics"]["oi_value"] == 19.57                   # kept as read, flagged


def test_classify_state():
    assert collector.classify_state({"video": None, "text": "k1m6a is offline"})[0] == "OFFLINE"
    assert collector.classify_state({"video": {"w": 1920, "rs": 4, "err": None, "paused": False},
                                     "text": ""})[0] == "LIVE"
    assert collector.classify_state({"video": {"w": 0, "rs": 0, "err": None}, "text": ""})[0] == "PLAYER_ERROR"
    assert collector.classify_state({"video": None, "text": "Log in to watch"})[0] == "LOGIN_REQUIRED"


# ------------------------------------------------------------------ ledger


def test_ledger_jsonl_sqlite_csv_and_dedupe(tmp_path):
    led = Ledger(tmp_path)
    rec = build_record(live(frame()), FakeReader(), last_frame_sha=None)
    assert led.record(rec) is True and led.record(json.loads(json.dumps(rec))) is False     # dedupe
    stale = build_record(live(frame(), t1=10.0), FakeReader(), last_frame_sha=None)
    assert led.record(stale) is True
    raw = led.raw_path(rec["captured_at_utc"][:10]).read_text(encoding="utf-8").splitlines()
    assert len(raw) == 2
    assert led.db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    rows = list(csv.DictReader(open(led.export_csv(), encoding="utf-8")))
    assert [r["status"] for r in rows] == ["VERIFIED", "STALE_FRAME"] and rows[1]["oi_value"] == ""
    counts = led.counts()
    assert counts["attempted"] == 2 and counts["oi_coverage_live"] == 0.5
    led.db.execute("DELETE FROM observations")
    led.db.commit()
    assert led.reindex() == 2                                    # JSONL is the source of truth
    with pytest.raises(ValueError):
        bad = json.loads(json.dumps(rec))
        bad["quality"]["fresh_frame"] = False
        led.record(bad)


# ------------------------------------------------------------------ loop / STOP / restart


class FakeSource:
    def __init__(self, caps):
        self.caps, self.closed, self.opened = list(caps), 0, 0

    async def capture(self):
        if not self.caps:
            raise RuntimeError("player crashed")
        return self.caps.pop(0)

    async def close(self):
        self.closed += 1


def test_stop_restart_and_no_backfill(tmp_path):
    caps = [live(frame(), sha=f"{i:064x}", t0=i, t1=i + 0.4) for i in range(3)]
    col = Collector(tmp_path, FakeSource(caps), FakeReader(), cadence=0.01)
    assert asyncio.run(col.run(max_attempts=3)) == 3
    # restart: the same frame as the last recorded one is not fresh -> STALE, not a new data point
    col2 = Collector(tmp_path, FakeSource([live(frame(), sha=f"{2:064x}", t0=2, t1=2)]), FakeReader(), cadence=0.01)
    col2.last_frame_sha = f"{2:064x}"
    asyncio.run(col2.run(max_attempts=1))
    led = Ledger(tmp_path)
    statuses = [r[0] for r in led.db.execute("SELECT status FROM observations ORDER BY id")]
    assert statuses == ["VERIFIED", "VERIFIED", "VERIFIED", "STALE_FRAME"]
    # STOP: a collector started with STOP present takes no capture at all
    (tmp_path / "STOP").write_text("x")
    src = FakeSource([live(frame(), sha="e" * 64)])
    asyncio.run(Collector(tmp_path, src, FakeReader(), cadence=0.01).run(max_attempts=5))
    assert len(src.caps) == 1
    status = json.loads((tmp_path / "reports" / "collector-status.json").read_text(encoding="utf-8"))
    assert status["state"] == "stopped" and status["trading"].startswith("NONE")


def test_player_crash_is_recorded_and_browser_reopened(tmp_path):
    src = FakeSource([])
    col = Collector(tmp_path, src, FakeReader(), cadence=0.01)
    rec = asyncio.run(col.sample_once())
    assert rec["quality"]["status"] == "PLAYER_ERROR" and src.closed == 1
    assert "player crashed" in rec["evidence"]["detail"]


def test_stop_between_capture_and_parse_aborts_the_parse(tmp_path):
    reader = FakeReader()
    rec = build_record(live(frame()), reader, last_frame_sha=None, stopping=lambda: True)
    assert rec["quality"]["status"] == "UNREADABLE" and reader.calls == 0


# ------------------------------------------------------------------ scenario 14: Jev routing


def test_scenario_14_dom_task_stays_with_jev_video_canvas_escalates():
    dom = {"features": {"iframes": 0, "shadow_roots": 0, "canvas": 0, "file_inputs": 0, "nested_scroll": 0,
                        "popups": 0, "video": 1}}
    assert routing.route(dom, target="dom")["route"] == routing.JEV_DOM
    for feats in ({"video": 1, "canvas": 0}, {"video": 0, "canvas": 2}):
        d = routing.route({"features": feats}, target="pixels")
        assert d["route"] == routing.ESCALATE_VISION and d["jev_may_read_value"] is False
    canvas_dom = {"features": {**dom["features"], "canvas": 1}}
    assert routing.route(canvas_dom, target="dom")["route"] == routing.ESCALATE_VISION
    assert routing.route({}, target="dom")["route"] != routing.JEV_DOM          # unknown features escalate


# ------------------------------------------------------------------ negative: no trading surface


def test_no_trading_surface():
    forbidden = re.compile(r"\b(place_order|create_order|submit_order|market_order|limit_order|api_secret|"
                           r"withdraw|ccxt|binance\.client|execute_trade|buy\(|sell\()", re.I)
    pkg = Path(market.__file__).parent
    for path in pkg.glob("*.py"):
        assert not forbidden.search(path.read_text(encoding="utf-8")), path.name
    for info in pkgutil.iter_modules([str(pkg)]):
        mod = importlib.import_module(f"bcc.market.{info.name}")
        names = {n.lower() for n in dir(mod)}
        assert not {n for n in names if any(w in n for w in ("order", "trade", "withdraw", "wallet"))}, info.name
    assert all(c.startswith(("market.twitch.", "market.metrics.")) for c in market.CAPABILITIES)


# ------------------------------------------------------------------ Bossman API surface


async def test_bossman_exposes_status_export_stop_and_nothing_else(tmp_path):
    from .conftest import client_for, make_settings, start_app
    app, svc = await start_app(make_settings(tmp_path), start_workers=False)
    client = client_for(app, svc)
    try:
        empty = (await client.get("/api/market/status")).json()
        assert empty["ledger"] is None and empty["trading"].startswith("NONE")
        root = Path(svc.settings.data_dir) / "market-data" / "twitch" / "k1m6a"
        led = Ledger(root)
        led.record(build_record(live(frame()), FakeReader(), last_frame_sha=None))
        led.close()
        st = (await client.get("/api/market/status")).json()
        assert st["ledger"]["attempted"] == 1 and st["ledger"]["by_status"] == {"VERIFIED": 1}
        csv_text = (await client.get("/api/market/export")).text
        assert csv_text.splitlines()[0].startswith("captured_at_utc,status") and "19.57" in csv_text
        assert (await client.post("/api/market/stop")).json()["stop"] is True and (root / "STOP").exists()
        from bcc.features import market_metrics
        assert {(r.path, tuple(sorted(r.methods))) for r in market_metrics.router.routes} == {
            ("/market/status", ("GET",)), ("/market/export", ("GET",)), ("/market/stop", ("POST",))}
        for path in ("/api/market/start", "/api/market/order", "/api/market/trade"):
            assert (await client.post(path, json={})).status_code in (404, 405)
    finally:
        await client.aclose()


def test_calibration_findings_2026_09_24():
    # live calibration v1: thousands separator read as '.', cursor line read as '|', hyphen separators
    assert schema.parse_price("83.585") == schema.parse_price("83,585") == 83585.0
    assert schema.parse_price("00:020\n24:41") is None
    assert schema.classify_badge("CVD | 73.36B") == ("cvd", "73.36B")
    assert schema.classify_badge("CVD: 73.27B") == ("cvd", "73.27B")
    assert extract._SYMBOL.match("BTC1! - 30 - CME").groups() == ("BTC1!", "30", "CME")
    # a doubtful price does not downgrade verified OI/CVD
    rec = build_record(live(frame()), FakeReader({"price": ["83,865", "83,866"]}), last_frame_sha=None)
    assert rec["quality"]["status"] == "VERIFIED" and rec["metrics"]["price"] is None
    assert rec["quality"]["per_metric"]["price"]["status"] == "LOW_CONFIDENCE"


# ------------------------------------------------------------------ Phase 13: research transform


def _rows(start, minutes, day_offset=0):
    from datetime import datetime, timedelta, timezone
    t0 = datetime(2026, 9, 24, 9, 0, tzinfo=timezone.utc) + timedelta(days=day_offset)
    return [{"t": t0 + timedelta(minutes=i, seconds=5), "symbol": "BTC1!", "price": 80000.0 + 10 * i,
             "oi": 19.5e9 + 1e7 * i, "cvd": 73e9 - 1e7 * i} for i in range(start, start + minutes)]


def test_labels_exist_only_after_the_horizon_has_passed():
    from datetime import timedelta
    from bcc.market import research
    rows = _rows(0, 40)
    asof = rows[20]["t"]                                    # "now" = minute 20
    table = research.build_table(rows, asof)
    assert len(table) == 21                                 # nothing after asof is even loaded
    by_t = {r["t"]: r for r in table}
    m10 = table[10]
    assert m10["ret_fwd_5m"] is not None and m10["label_ready_5m"] is True
    assert m10["ret_fwd_15m"] is None and m10["label_ready_15m"] is False   # 10+15 > 20
    assert table[19]["ret_fwd_1m"] is None or table[19]["label_ready_1m"]
    assert all(r["ret_fwd_30m"] is None for r in table)
    assert m10["bucket_price_oi"] == "price↑_oi↑" and m10["div_price_cvd"] == "price↑_cvd↓"
    s = research.summarize(table)
    assert s["status"] == "DATA_COLLECTION" and s["trading_model_ready"] is False


def test_split_is_by_day_and_chronological_never_random():
    from datetime import datetime, timezone
    from bcc.market import research
    rows = [r for d in range(5) for r in _rows(0, 10, day_offset=d)]
    table = research.build_table(rows, datetime(2026, 10, 1, tzinfo=timezone.utc))
    info = research.split_by_day(table)
    assert info["status"] == "SPLIT_BY_DAY"
    splits_per_day = {}
    for r in table:
        splits_per_day.setdefault(r["day"], set()).add(r["split"])
    assert all(len(v) == 1 for v in splits_per_day.values())          # a day never straddles splits
    assert max(info["train"]) < min(info["valid"]) <= max(info["valid"]) < min(info["holdout"])
    one_day = research.build_table(_rows(0, 10), datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert research.split_by_day(one_day)["status"] == "INSUFFICIENT_DAYS"


def test_price_badge_band_tolerates_a_dark_compression_row():
    # calibration v4 frame 2: one row of the badge at avg 17.7 split the band -> price missed
    img = frame()
    d = ImageDraw.Draw(img)
    d.rectangle((1256, 466, 1300, 466), fill=(16, 18, 19))
    box = extract.locate_price_badge(img)
    assert box is not None and box[1] == 456
