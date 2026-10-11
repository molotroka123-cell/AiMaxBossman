"""Writes promo_v4_plan.json (timeline + titles of promo_v4.py) from the match-cut windows chosen for ad (c) (c_plan.json),
so every swapped-face shot of the promo uses a window whose restored frames exist.
"""
from __future__ import annotations

import json
from pathlib import Path

Q = Path.home() / "Videos" / "Bossman-Genjutsu" / "ads-20261011" / "quality"
P = 0.4597551020408164
HQ = (Q / "hq").as_posix() + "/"


def main() -> None:
    cp = json.loads((Q / "c_plan.json").read_text(encoding="utf-8"))
    by = {(round(float(s["b0"]), 1), s["clip"]): s for s in cp}

    def t0(b0, clip):
        return round(float(by[(float(b0), clip)]["t0"]), 3)
    tee_a = 2.47                                # hook window (span A of the swapped clip, restored frames 74-118)
    segs = [
        dict(b0=0, nb=1.5, kind="swap_orig", t0=tee_a, speed=0.55, z0=1.0, z1=1.03),
        dict(b0=1.5, nb=4, kind="swap_her", t0=tee_a, speed=0.55, off=round(1.5 * P, 4), z0=1.03, z1=1.12),
        dict(b0=5.5, nb=2, kind="cut", clip="dress", t0=t0(8, "dress"), z0=1.0, z1=1.06),
        dict(b0=7.5, nb=2, kind="cut", clip="pilot", t0=t0(5, "pilot"), z0=1.06, z1=1.0),
        dict(b0=9.5, nb=1, kind="cut", clip="btee", t0=t0(4, "btee"), z0=1.0, z1=1.06),
        dict(b0=10.5, nb=1, kind="cut", clip="dress", t0=t0(14, "dress"), z0=1.06, z1=1.0),
        dict(b0=11.5, nb=2, kind="cut", clip="pilot", t0=t0(18, "pilot"), z0=1.0, z1=1.06),
        dict(b0=13.5, nb=2, kind="cut", clip="dress", t0=t0(26, "dress"), z0=1.06, z1=1.0),
        dict(b0=15.5, nb=2, kind="cut", clip="tee", t0=t0(16, "tee"), z0=1.0, z1=1.06),
        dict(b0=17.5, nb=1.5, kind="master", file=HQ + "d/work_hq/bg_teleport_master_hq.mp4", t0=2.15, z0=1.0, z1=1.05),
        dict(b0=19.0, nb=1.5, kind="master", file=HQ + "d/work_hq/bg_teleport_master_hq.mp4", t0=3.95, z0=1.0, z1=1.05),
        dict(b0=20.5, nb=3.5, kind="master", file=HQ + "d/work_hq/bg_teleport_master_hq.mp4", t0=9.5, z0=1.0, z1=1.04),
        dict(b0=24.0, nb=13, kind="screencast", t0=0.0, speed=1.0),
        dict(b0=37.0, nb=7, kind="card"),
    ]
    T = []

    def t(text, b0, b1, **kw):
        T.append(dict(text=text, b0=b0, b1=b1, kw=kw))
    t("GENJUTSU STUDIO", 0, 24, size=44, y="70", border=3, pop=0.2, box=False)
    t("ОДНО ВИДЕО.", 0.0, 1.5, y="h-560", size=92)
    t("ОРИГИНАЛ", 0.1, 1.5, y="h-430", size=60, color="0xffd23c")
    t("ТВОЁ ЛИЦО.", 1.5, 5.5, y="h-560", size=96)
    t("ТО ЖЕ ДВИЖЕНИЕ.", 1.8, 5.5, y="h-430", size=64, color="0xffd23c")
    t("ЛИЦО — В ЛЮБОМ ВИДЕО", 5.5, 17.5, y="h-470", size=64, pop=0.1)
    t("лицо: FaceFusion", 5.5, 17.5, y="h-350", size=36, color="white", border=3)
    t("ФОН ЗА СЕКУНДУ", 17.5, 24, y="h-470", size=80, color="0xffd23c")
    t("маска человека: RVM, считается на этом ПК", 17.5, 24, y="h-350", size=34, color="white", border=3)
    t("перекраска по маскам, считается на этом ПК", 24, 37, y="h-80", size=30, color="white", border=3)
    t("ТВОЁ ЛИЦО.", 37.2, 44, y="h*0.28", size=96, border=0, box=False)
    t("ЛЮБОЕ ВИДЕО.", 37.5, 44, y="h*0.28+120", size=96, color="0xffd23c", border=0, box=False)
    t("GENJUTSU STUDIO", 38, 44, y="h*0.28+300", size=70, border=0, box=False)
    t("лицо: FaceFusion  |  фон: маска RVM", 38.5, 44, y="h*0.66", size=36, color="white", border=0, box=False)
    t("рендер локально", 38.5, 44, y="h*0.66+54", size=36, color="white", border=0, box=False)
    t("в разработке: смена одежды и причёски на видео", 38.5, 44, y="h*0.66+130", size=34, color="0xffd23c", border=0, box=False)
    (Q / "promo_v4_plan.json").write_text(json.dumps(dict(segments=segs, texts=T), ensure_ascii=False, indent=1), encoding="utf-8")
    print("plan written")


if __name__ == "__main__":
    main()
