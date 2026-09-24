"""Read-only market-metrics collector (owner run 2026-09-24).

DATA COLLECTION ONLY. This package observes a public stream, extracts the OI/CVD
numbers the streamer shows, and records them with evidence. It has no trading,
order, exchange-credential or publishing capability — by construction and pinned
by a negative test (`tests/test_market_collector.py::test_no_trading_surface`).

Pipeline (docs/tomorrow-2026-09-24/JEV_TWITCH_OI_CVD_COLLECTOR.md):
    browser (Playwright) -> fresh video frame -> Jev routing (video/canvas escalates)
    -> badge location -> LOCAL vision double-read -> schema validator
    -> JSONL raw ledger -> SQLite (WAL) index -> CSV export
"""

CAPABILITIES = (
    "market.twitch.open",
    "market.twitch.observe",
    "market.twitch.capture",
    "market.metrics.extract",
    "market.metrics.record",
    "market.metrics.export",
    "market.metrics.status",
)
