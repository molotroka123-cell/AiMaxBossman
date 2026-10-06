"""Markdown table from ui_bench JSON files: python summarize_bench.py a.json b.json > table.md"""
import json
import sys

rows = []
for p in sys.argv[1:]:
    rows += json.load(open(p, encoding="utf-8"))
print("| scenario | locator | trials | ok | refused (no click) | dropped (stale) | **wrong click** | click not confirmed | latency p50/p95 ms | RSS MB | STOP returns s |")
print("|---|---|---|---|---|---|---|---|---|---|---|")
for r in rows:
    lat = f"{r['latency_p50_ms']}/{r['latency_p95_ms']}" if r["latency_p50_ms"] else "—"
    print(f"| {r['scenario']} | {r['candidate']} | {r['trials']} | {r['ok']} | {r['refused_safe']} | {r['obsolete']} | **{r['wrong_click']}** | {r['unverified_click']} | {lat} | {r['rss_mb_end']} | {r['stop_returns_s']} |")
