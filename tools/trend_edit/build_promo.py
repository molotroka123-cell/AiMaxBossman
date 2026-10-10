"""Queue item 1 — the 30 s PROMO: "Motion Studio Bossman 2 / Genjutsu", every cut on the track's beat grid.

  0-2 s    the original reference (ref-clip-01 cropped to the 4:3 picture of the swapped clip = same framing)
  2-5 s    face swap back and forth, hard cuts on beats, RGB-split flashes, punch-ins
  5-8 s    hair colour blue -> green -> white -> black (diagonal / circle wipes, velocity ramps on the beat)
  8-12 s   outfit swap (Stage 2, TRIAL) as a before / after split wipe, slowed 2x
  12-30 s  background swap to the neon night city (reveal wipe), kinetic titles, end card

The source is only 14.7 s: the background section replays it from its start (stated in the report), the music is the
reference's own track — intro once, then the drop block (8 beats) looped on the beat grid.

  <gate-venv>/python tools/trend_edit/build_promo.py --out promo.mp4 [--preview] [--no-neon]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import beat_grid as bg  # noqa: E402
import edit_core as ec  # noqa: E402
import fx  # noqa: E402

BOSS = Path.home() / "Bossman"
VID = Path.home() / "Videos" / "Bossman-Genjutsu"
REF_ORIG = BOSS / "video-testset-private" / "ref-clip-01.mp4"
REF_SWAP = BOSS / "video-testset-private" / "ref-promo-08.mp4"
ORIG_CROP = (166, 0, 948, 719)        # ref-clip-01 picture area that ref-promo-08 shows (registration, mean abs err ~8.7/255)
CLOTHES_BEFORE = VID / "stage2-20261010" / "run81v2" / "source.mp4"
CLOTHES_AFTER = VID / "stage2-20261010" / "run81v2" / "master-smooth.mp4"
#: background-swap job of ref-promo-08 over the neon plate (RVM, optional SAM2 subject gate); override with TREND_NEON_DIR
NEON_DIR = Path(os.environ.get("TREND_NEON_DIR", str(VID / "promo-bg-20261010" / "job-neon")))
MASKS = VID / "promo-queue-20261010" / "masks-promo08"
HAIR_SEQ = ["blue", "green", "white", "black"]
WIPE_KINDS = ["diag", "circle", "h", "v"]
TOTAL = 30.0


# ------------------------------------------------------------------ plan (pure, unit-tested)
@dataclass(frozen=True)
class Plan:
    period: float
    sections: dict          # name -> (beat_start, beat_end)
    hits: list              # [(beat, strength)]
    hair_boundaries: list   # beats where the next hair colour starts


def make_plan() -> Plan:
    sections = {"original": (0, 4), "swap": (4, 12), "hair": (12, 20), "outfit": (20, 28), "background": (28, 60),
                "endcard": (60, 66)}
    hits: list[tuple[float, float]] = []
    hits += [(4, 1.0), (5, 1.0), (6, 1.0), (7, 1.0)]                      # swap, bar 1: a cut every beat
    hits += [(8 + 0.5 * i, 1.0) for i in range(8)]                         # swap, bar 2: every half beat
    hair_b = [12, 14, 16, 18]
    hits += [(b, 1.0) for b in hair_b]
    hits += [(20, 2.0), (22, 1.0), (24, 0.8), (26, 1.0)]                   # the drop + outfit wipes
    hits += [(28, 1.5), (30.5, 1.0)]                                       # background reveal
    hits += [(b, 1.0 if b % 4 == 0 else 0.5) for b in range(32, 60, 2)]   # punches every 2 beats
    hits += [(60, 2.0)]
    return Plan(period=0.0, sections=sections, hits=sorted(hits), hair_boundaries=hair_b)


def hit_times(plan: Plan, period: float) -> list[tuple[float, float]]:
    return [(round(b * period, 6), s) for b, s in plan.hits]


def text_plan(period: float, W: int = 1488, H: int = 1128, S: float = 1.0) -> list[ec.Text]:
    B = lambda k: round(k * period, 4)  # noqa: E731
    T = ec.Text
    tag = dict(size=int(40 * S), x=str(int(34 * S)), y=f"h-{int(86 * S)}", box=True, font=fx.FONT, border=0, pop=0.12)
    big = lambda size, y, color="white", **k: dict(size=int(size * S), y=y, color=color, **k)  # noqa: E731
    cy = "0x38e8ff"
    pk = "0xff4fd8"
    t = [
        T("ORIGINAL REFERENCE", 0.0, B(4), tag),
        T("FACE SWAP  |  her face, FaceFusion, local", B(4), B(12), tag),
        T("HAIR COLOUR  |  blue, green, white, black", B(12), B(20), tag),
        T("OUTFIT SWAP  |  trial: Stage 2 gate not fully passed", B(20), B(28), tag),
        T("BEFORE", B(20), B(23.4), dict(size=int(54 * S), x=str(int(40 * S)), y=str(int(40 * S)), font=fx.FONT_BOLD, pop=0.1)),
        T("AFTER", B(22), B(28), dict(size=int(54 * S), x=f"w-text_w-{int(40 * S)}", y=str(int(40 * S)), font=fx.FONT_BOLD, pop=0.1)),
        T("BACKGROUND SWAP  |  neon night city", B(28), B(32), tag),
        T("MOTION STUDIO", B(32), B(40), big(118, f"h*0.66", cy, rise=int(40 * S))),
        T("BOSSMAN 2", B(34), B(40), big(168, f"h*0.77", "white", rise=int(40 * S))),
        T("GENJUTSU", B(40), B(48), big(210, "h*0.70", pk, rise=int(50 * S))),
        T("face  |  hair  |  outfit  |  background", B(42), B(48), big(52, "h*0.88", "white")),
        T("100% LOCAL", B(48), B(52), big(150, "h*0.68", cy, rise=int(40 * S))),
        T("ONE PC  |  NO CLOUD", B(50), B(56), big(88, "h*0.80", "white", rise=int(30 * S))),
        T("FACE", B(56), B(57), big(230, "h*0.70", "white")),
        T("HAIR", B(57), B(58), big(230, "h*0.70", cy)),
        T("OUTFIT", B(58), B(59), big(230, "h*0.70", pk)),
        T("BACKGROUND", B(59), B(60), big(190, "h*0.70", "white")),
        T("BOSSMAN 2", B(60), B(66), big(190, "h*0.34", "white", rise=int(40 * S))),
        T("Motion Studio  |  Genjutsu", B(61), B(66), big(70, "h*0.52", cy)),
        T("rendered locally on one PC, no cloud", B(62), B(66), big(46, "h*0.64", "white")),
        T("outfit swap = trial", B(63), B(66), big(40, "h*0.72", "0xbbbbbb")),
    ]
    return t


# ------------------------------------------------------------------ renderer
class Promo:
    def __init__(self, W: int, H: int, neon: Path | None, grid: bg.BeatGrid, plan: Plan):
        self.W, self.H, self.S = W, H, W / 1488
        self.cv = (W, H)
        self.P = grid.period
        self.phi = grid.phase
        self.plan = plan
        self.hits = hit_times(plan, self.P)
        print("loading clips", flush=True)
        self.orig = ec.Clip(REF_ORIG, crop=ORIG_CROP)
        self.swap = ec.Clip(REF_SWAP)
        self.cl_before = ec.Clip(CLOTHES_BEFORE)
        self.cl_after = ec.Clip(CLOTHES_AFTER)
        self.neon = ec.Clip(neon) if neon else None
        self.masks = ec.MaskSeq(MASKS)
        self.B = lambda k: k * self.P  # noqa: E731
        # velocity knots for the hair section, in seconds from its start
        hk: list[tuple[float, float]] = []
        h0 = self.B(plan.sections["hair"][0])
        for b in plan.hair_boundaries[1:]:
            hk += fx.velocity_knots_balanced(self.B(b) - h0, lead=0.30, hold=0.10, tail=0.30, slow=0.4)[1:]
        self.hair_knots = [(0.0, 1.0)] + hk
        self.last_neon_frame: np.ndarray | None = None

    # -------- shots
    def src_t(self, tau: float) -> float:
        return tau + self.phi

    def shot_original(self, tau):
        t = self.src_t(tau)
        return self.orig.get(t, self.cv)

    def shot_swap(self, tau):
        t = self.src_t(tau)
        b0 = self.plan.sections["swap"][0]
        first = tau < self.B(8)
        step = self.P if first else self.P / 2
        k = int((tau - self.B(b0) + 1e-9) / step)
        which = k % 2          # 0 = swapped (her), 1 = original; start on HER face at the first cut
        return (self.swap if which == 0 else self.orig).get(t, self.cv)

    def hair_idx_and_t(self, tau):
        h0 = self.B(self.plan.sections["hair"][0])
        t = self.src_t(h0) + fx.ramp_source_time(tau - h0, self.hair_knots)
        return t, self.swap.index(t)

    def hair_color(self, base, idx, name):
        return ec.recolor(base, {"hair": (self.masks.get("hair", idx, self.cv), ec.HAIR[name])})

    def shot_hair(self, tau):
        t, idx = self.hair_idx_and_t(tau)
        base = self.swap.get(t, self.cv)
        bs = [self.B(b) for b in self.plan.hair_boundaries]
        ci = max(0, sum(1 for b in bs if tau >= b - 1e-9) - 1)
        cur = self.hair_color(base, idx, HAIR_SEQ[ci])
        p = (tau - bs[ci]) / 0.24
        if ci == 0 or p >= 1.0:
            # the first colour also wipes in from the original black hair
            if ci == 0 and p < 1.0:
                return fx.wipe(base, cur, p, WIPE_KINDS[0])
            return cur
        prev = self.hair_color(base, idx, HAIR_SEQ[ci - 1])
        return fx.wipe(prev, cur, fx.ease_in_out(p), WIPE_KINDS[ci % 4])

    def shot_outfit(self, tau):
        o0 = self.B(self.plan.sections["outfit"][0])
        u = tau - o0
        dur = self.B(8)
        half = self.cl_before.duration
        speed = 0.5
        pos = u * speed
        if pos <= half:
            src = pos
        else:                                  # ping-pong: back from the end, still slow
            src = max(0.0, half - (pos - half))
        a = self.cl_before.get(src, self.cv)
        b = self.cl_after.get(src, self.cv)
        p = fx.ease_in_out((u - 0.25 * self.P) / (4.5 * self.P))        # divider travels beats 0.25 -> 4.75
        if u > 5.0 * self.P:                                           # after the wipe: only AFTER, then back to BEFORE flash
            return b
        del dur
        return fx.split_before_after(a, b, p, line=max(2, int(6 * self.S)))

    def neon_t(self, tau):
        return max(0.0, tau - self.B(self.plan.sections["background"][0])) + 0.0

    def shot_background(self, tau):
        s = self.neon_t(tau)
        t = self.src_t(s)
        a = self.swap.get(t, self.cv)
        if self.neon is None:
            n = fx.vignette_grade(a, tint=(1.3, 0.8, 1.2), gain=0.9)     # placeholder (no neon master yet)
        else:
            n = self.neon.get(t, self.cv)
            self.last_neon_frame = n
        r0, r1 = self.B(30), self.B(31)
        p = fx.ease_in_out((tau - r0) / (r1 - r0))
        if p <= 0:
            return a
        if p >= 1:
            return n
        cx, cy = self.masks.center(self.swap.index(t), "hair", self.cv)
        m = fx.wipe_mask(self.H, self.W, p, "circle", soft=0.05)
        # re-centre the circle on the head
        if abs(cx - 0.5) > 1e-3 or abs(cy - 0.5) > 1e-3:
            M = np.float32([[1, 0, (cx - 0.5) * self.W], [0, 1, (cy - 0.5) * self.H]])
            m = ec.cv2().warpAffine(m, M, (self.W, self.H), borderMode=ec.cv2().BORDER_REPLICATE)
        m = m[:, :, None]
        return (a.astype(np.float32) * (1 - m) + n.astype(np.float32) * m).astype(np.uint8)

    def shot_endcard(self, tau):
        if self.neon is not None:
            base = self.neon.get(self.neon.duration - 0.05, self.cv)
        else:
            base = self.swap.get(self.swap.duration - 0.05, self.cv)
        c = ec.cv2()
        base = c.GaussianBlur(base, (0, 0), max(3, self.W // 90))
        return fx.vignette_grade(base, gain=0.38, vignette=0.55)

    # -------- compositing of one output frame
    def frame(self, tau: float) -> np.ndarray:
        sec = self.plan.sections
        B = self.B
        if tau < B(sec["swap"][0]):
            f = self.shot_original(tau)
            # slow push-in over the bar, no hits yet
            f = fx.zoom_punch(f, 1.0 + 0.05 * (tau / B(4)))
        elif tau < B(sec["hair"][0]):
            f = self.shot_swap(tau)
        elif tau < B(sec["outfit"][0]):
            f = self.shot_hair(tau)
        elif tau < B(sec["background"][0]):
            f = self.shot_outfit(tau)
        elif tau < B(sec["endcard"][0]):
            f = self.shot_background(tau)
        else:
            f = self.shot_endcard(tau)
            e = fx.ease_out_cubic((tau - B(60)) / 0.35)
            if e < 1.0 and self.neon is not None:
                f = fx.wipe(self.shot_background(B(60) - 1e-3), f, e, "h")
        return self.post(f, tau)

    def post(self, f: np.ndarray, tau: float) -> np.ndarray:
        return ec.hit_fx(f, tau, self.hits, self.S)


def run(args) -> dict:
    t0 = time.time()
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    info_json = work / "beats.json"
    if info_json.exists():
        info = json.loads(info_json.read_text())
    else:
        info = bg.detect_beats(REF_ORIG)
        info_json.write_text(json.dumps(info))
    g = info["grid"]
    grid = bg.BeatGrid(period=g["period"], phase=g["phase"])
    plan = make_plan()
    W = 496 if args.preview else 1488
    H = 376 if args.preview else 1128
    neon = None
    if not args.no_neon:
        for name in ("master.mp4", "final.mp4"):
            if (NEON_DIR / name).exists():
                neon = NEON_DIR / name
                break
        if neon is None:
            raise SystemExit("neon master missing: job-neon not finished (use --no-neon for a preview)")
    pr = Promo(W, H, neon, grid, plan)
    inter = work / ("inter-preview.mp4" if args.preview else "inter.mp4")
    w = ec.Writer(inter, W, H, ec.FPS, crf=18 if args.preview else 13)
    n = int(round(TOTAL * ec.FPS))
    for i in range(n):
        w.write(pr.frame(i / ec.FPS))
        if i % 60 == 0:
            print(f"frame {i}/{n} {time.time() - t0:.0f}s", flush=True)
    w.close()
    # audio: the track's own beat grid, intro once then the drop block looped
    drop = 20
    segs = bg.audio_loop_plan(grid, TOTAL, intro_beats=drop, loop_beats=8)
    audio = work / "audio.wav"
    ec.build_audio(REF_ORIG, segs, audio, fade_out=0.6)
    texts = text_plan(grid.period, W, H, W / 1488)
    ec.final_pass(inter, audio, texts, Path(args.out), crf=22 if args.preview else 21, duration=TOTAL)
    rep = {"out": str(args.out), "duration": TOTAL, "size": [W, H], "fps": ec.FPS, "bpm": round(grid.bpm, 2),
           "period": grid.period, "phase": grid.phase, "neon": str(neon) if neon else None, "audio_segments": segs,
           "plan_beats": plan.sections, "seconds": round(time.time() - t0, 1)}
    (work / ("report-preview.json" if args.preview else "report.json")).write_text(json.dumps(rep, indent=1))
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--work", default=str(VID / "promo-queue-20261010" / "work-promo"))
    ap.add_argument("--preview", action="store_true", help="1/3 size, quick")
    ap.add_argument("--no-neon", action="store_true", help="placeholder for the background shots (preview only)")
    a = ap.parse_args(argv)
    print(json.dumps(run(a), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
