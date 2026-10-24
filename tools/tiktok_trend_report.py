"""Dance-trend report from the public TikTok metadata dataset (datasocial/tiktok-5.6B-videos, CC-BY-NC-4.0).

    python tools/tiktok_trend_report.py [--glob D:/Bossman/datasets/tiktok-5.6B/data/2026-*.parquet] [--out ...json]

Metadata only (no videos, no personal profiles): hashtags, sound ids, duration, frame size, views/engagement. Answers
what a 15 s dance clip should look like to ride the current wave: which dance hashtags and sounds grow, which length
and aspect perform, and how AI-labelled videos do. Aggregates only; nothing about individual creators is stored.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

DANCE = ("dance", "dancechallenge", "jumpstyle", "shuffle", "tiktokdance", "dancetrend", "trend", "challenge",
         "танец", "танцы", "hardstyle", "phonk")


def main(argv=None) -> int:
    import duckdb
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--glob", default="D:/Bossman/datasets/tiktok-5.6B/data/2026-*.parquet")
    ap.add_argument("--out", default="docs/audits/2026-10-24-tiktok-trend/trends.json")
    a = ap.parse_args(argv)
    con = duckdb.connect()
    con.execute(f"CREATE VIEW v AS SELECT * FROM read_parquet('{a.glob}') WHERE type IS DISTINCT FROM 'photo'")
    tags = ", ".join(f"'{t}'" for t in DANCE)
    con.execute(f"""CREATE TEMP TABLE d AS SELECT video_id, posted_at, duration_ms, width, height, sound_id, hashtags,
        views, likes, shares, saves, engagement_rate, is_ai_generated FROM v
        WHERE list_has_any(list_transform(hashtags, lambda x: lower(x)), [{tags}])""")
    q = lambda s: con.execute(s).fetchall()
    total, dance = q("SELECT count(*) FROM v")[0][0], q("SELECT count(*) FROM d")[0][0]
    rep = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "source": a.glob, "license": "CC-BY-NC-4.0",
           "videos": total, "dance_videos": dance}
    rep["top_dance_hashtags"] = [{"tag": t, "videos": n, "median_views": int(mv or 0), "median_er": round(float(er or 0), 4)}
        for t, n, mv, er in q("""SELECT lower(h) t, count(*) n, median(views), median(engagement_rate)
            FROM d, unnest(hashtags) u(h) GROUP BY t HAVING n >= 500 ORDER BY median(views) * ln(n) DESC LIMIT 30""")]
    rep["rising_dance_hashtags_oct_vs_sep"] = [{"tag": t, "sep": s, "oct_per_day_x30": round(o, 1), "growth": round(g, 2)}
        for t, s, o, g in q("""WITH m AS (SELECT lower(h) t, date_trunc('month', posted_at) mo, count(*) n
                FROM d, unnest(hashtags) u(h) GROUP BY 1, 2),
            days AS (SELECT greatest(1, date_part('day', max(posted_at))) dd FROM d WHERE posted_at >= '2026-10-01')
            SELECT s.t, s.n, o.n * 30.0 / (SELECT dd FROM days), (o.n * 30.0 / (SELECT dd FROM days)) / s.n
            FROM m s JOIN m o ON s.t = o.t AND s.mo = '2026-09-01' AND o.mo = '2026-10-01'
            WHERE s.n >= 300 ORDER BY 4 DESC LIMIT 20""")]
    rep["top_dance_sounds"] = [{"sound_id": int(s), "videos": n, "median_views": int(mv or 0)}
        for s, n, mv in q("""SELECT sound_id, count(*) n, median(views) FROM d WHERE sound_id IS NOT NULL
            GROUP BY 1 HAVING n >= 200 ORDER BY median(views) * ln(n) DESC LIMIT 15""")]
    rep["duration_buckets"] = [{"seconds": b, "videos": n, "median_views": int(mv or 0), "median_er": round(float(er or 0), 4)}
        for b, n, mv, er in q("""SELECT CASE WHEN duration_ms < 10000 THEN '<10' WHEN duration_ms < 16000 THEN '10-15'
            WHEN duration_ms < 31000 THEN '16-30' WHEN duration_ms < 61000 THEN '31-60' ELSE '>60' END b,
            count(*), median(views), median(engagement_rate) FROM d GROUP BY 1 ORDER BY 1""")]
    rep["aspect"] = [{"aspect": b, "videos": n, "median_views": int(mv or 0)}
        for b, n, mv in q("""SELECT CASE WHEN height > width * 1.5 THEN '9:16' WHEN height > width THEN 'portrait-other'
            WHEN height = width THEN '1:1' ELSE 'landscape' END, count(*), median(views) FROM d GROUP BY 1 ORDER BY 2 DESC""")]
    rep["ai_generated"] = [{"is_ai_generated": int(x or 0), "videos": n, "median_views": int(mv or 0), "median_er": round(float(er or 0), 4)}
        for x, n, mv, er in q("SELECT is_ai_generated, count(*), median(views), median(engagement_rate) FROM d GROUP BY 1")]
    rep["best_hours_utc"] = [{"hour": int(h), "median_views": int(mv or 0)}
        for h, mv in q("SELECT hour(posted_at) h, median(views) FROM d GROUP BY 1 ORDER BY 2 DESC LIMIT 5")]
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print(json.dumps({k: rep[k] for k in ("videos", "dance_videos")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
