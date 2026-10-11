"""Collects metrics/*.json (metrics.py) + gates (face_gate.py) + sizes into quality_summary.json and prints the table.

Pre-registered gates: sharpness band ratio >= 1.5; flicker (symmetric static diff, or self-referential noise for different
content) <= 1.10 x old; ArcFace mean not lower than old by more than 0.02 (same content only); shown face frames >= 0.59 reported.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

Q = Path.home() / "Videos" / "Bossman-Genjutsu" / "ads-20261011" / "quality"
V = Q.parent

ITEMS = {
    "a": dict(title="(a) identity swap", old=V / "v01/ad-a-identity-swap.mp4", new=Q / "hq/a/ad-a-identity-swap_hq.mp4",
              g_old=V / "gates/v01.json", g_new=Q / "gates_new/a_new.json", same=True),
    "b": dict(title="(b) hair cycle (v3 natural)", old=V / "v02/ad-b-hair-cycle_v3.mp4", new=Q / "hq/b/ad-b-hair-cycle_v3_hq.mp4",
              g_old=Q / "gates_new/b_old.json", g_new=Q / "gates_new/b_new.json", same=True),
    "c": dict(title="(c) outfit: slideshow -> real video", old=V / "v03/ad-c-outfit-reveal.mp4", new=Q / "hq/c/ad-c-outfit-cut_hq.mp4",
              g_old=V / "gates/v03.json", g_new=Q / "gates_new/c_new.json", same=False),
    "d": dict(title="(d) background teleport", old=V / "v04/ad-d-background-teleport.mp4", new=Q / "hq/d/ad-d-background-teleport_hq.mp4",
              g_old=V / "gates/v04.json", g_new=Q / "gates_new/d_new.json", same=True),
    "e": dict(title="(e) showreel", old=V / "v05/ad-e-showreel.mp4", new=Q / "hq/e/ad-e-showreel_hq.mp4",
              g_old=V / "gates/v05.json", g_new=Q / "gates_new/e_new.json", same=False),
    "promo": dict(title="promo 6 (v3 -> v4)", old=V / "v06/promo-6-genjutsu-studio_v3.mp4",
                  new=Q / "hq/promo-6-genjutsu-studio_hq_v4.mp4", g_old=Q / "gates_new/promo_old.json",
                  g_new=Q / "gates_new/promo_new.json", same=False),
}


def gate(p: Path):
    if not p.exists():
        return None
    s = json.loads(p.read_text(encoding="utf-8"))["summary"]
    return {k: s[k] for k in ("identity_mean", "identity_min", "identity_p10", "frames_below_min", "frames_checked", "lost_face_frames")}


def main() -> int:
    out = {}
    for k, it in ITEMS.items():
        m = Q / "metrics" / f"{k}.json"
        met = json.loads(m.read_text(encoding="utf-8")) if m.exists() else {}
        row = {"title": it["title"], "same_content": it["same"], "old": str(it["old"]), "new": str(it["new"]),
               "size_old_mb": round(it["old"].stat().st_size / 1e6, 1) if it["old"].exists() else None,
               "size_new_mb": round(it["new"].stat().st_size / 1e6, 1) if it["new"].exists() else None,
               "sharp_band_ratio": round(met.get("sharp_ratio_band", 0), 2) or None,
               "sharp_full_ratio": round(met.get("sharp_ratio_full", 0), 2) or None,
               "noise_ratio": round(met.get("noise_ratio", 0), 2) or None,
               "paired_static_ratio": round(met["paired_static"]["ratio"], 2) if met.get("paired_static", {}).get("ratio") else None,
               "arcface_old": gate(it["g_old"]), "arcface_new": gate(it["g_new"])}
        tg = it["new"].with_name(it["new"].stem + "_tg.mp4")
        row["tg"] = str(tg) if tg.exists() else None
        row["tg_mb"] = round(tg.stat().st_size / 1e6, 1) if tg.exists() else None
        out[k] = row
    (Q / "quality_summary.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    hdr = f"{'video':34s} {'MB old>new':>12s} {'sharp(band)':>11s} {'flicker':>8s} {'arcface mean old>new':>22s} {'min new':>8s} {'<0.59':>6s} {'lost':>5s}"
    print(hdr)
    for k, r in out.items():
        ao, an = r["arcface_old"] or {}, r["arcface_new"] or {}
        fl = r["paired_static_ratio"] if r["same_content"] else r["noise_ratio"]
        print(f"{r['title']:34s} {str(r['size_old_mb']) + '>' + str(r['size_new_mb']):>12s} {str(r['sharp_band_ratio']) + 'x':>11s} "
              f"{str(fl) + 'x':>8s} {str(ao.get('identity_mean')) + ' > ' + str(an.get('identity_mean')):>22s} "
              f"{str(an.get('identity_min')):>8s} {str(an.get('frames_below_min')) + '/' + str(an.get('frames_checked')):>6s} "
              f"{str(an.get('lost_face_frames')):>5s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
