"""Bossman Motion Studio 2.0 promo, v2 (owner brief 10.10 + external review of v1).

Real generation first, recolour only where it is stated:
  0-6 beats    her own video circle (ref-clip-05, original, nothing generated)
  6-14         hero swap on the reference (FaceFusion x3: her / Kisliy / p4ela), hard cuts, 3-frame RGB-split flash
  14-22        outfit change LIVE on the reference clip (Wan2.1 VACE 1.3B, uniform reference) — trial
  22-30        hair colour (Lab recolour that keeps strand detail), wipes
  30-38        background swap: RVM + SegFormer repair + light match on the neon plate, lower-third titles
  38-50        montage of every capability: 2x2 before / after tiles
  50-58        LoRA character cards (SDXL stills, honest labels) + «motion transfer: not working yet» card
  58-65.2      end card with claims that are true

Review rules (docs/owner/TREND_EDIT_RECIPES_RU.md): every capability gets a CLEAN segment first (no zoom punch, no
glitch, no title over a face), titles live in the lower third; frames where the swap failed are never shown.

  <gate-venv>/python tools/trend_edit/build_promo_v2.py --out promo-v2.mp4 [--preview] [--no-vace]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import beat_grid as bg  # noqa: E402
import edit_core as ec  # noqa: E402
import fx  # noqa: E402

BOSS = Path.home() / "Bossman"
VID = Path.home() / "Videos" / "Bossman-Genjutsu"
Q = VID / "promo-queue-20261010"
V2 = Q / "v2"
TS = BOSS / "video-testset-private"
REF = TS / "ref-promo-09.mp4"
ORIG = TS / "ref-clip-01.mp4"
ORIG_CROP = (166, 0, 948, 719)
CIRCLE = TS / "ref-clip-05.mp4"
MASKS = Q / "masks-promo08"
LORA_EVAL = BOSS / "lora-work-20261010" / "eval"
TOTAL = 30.0
F = 24.0
HAIR_SEQ = ["blue", "green", "white"]

# ---- swap schedule: (beat_start, beats, who, first_frame, last_frame); frames 46..55 of the reference are a hand over the
# face (ArcFace 0.30-0.36 on every clip incl. the reference itself), so no segment spans them: the time jump sits on a cut
SWAP_SCHEDULE = [
    (6.0, 2.0, "her", 0, 21),
    (8.0, 1.0, "kisliy", 22, 32),
    (9.0, 1.0, "p4ela", 33, 43),
    (10.0, 1.0, "kisliy", 56, 66),
    (11.0, 1.0, "p4ela", 67, 77),
    (12.0, 2.0, "her", 78, 95),
]
BAD_FRAMES = range(44, 56)


def swap_choice(beat: float):
    for s, n, who, f0, f1 in SWAP_SCHEDULE:
        if s - 1e-9 <= beat < s + n - 1e-9:
            return s, n, who, f0, f1
    return SWAP_SCHEDULE[-1]


def frame_for(beat: float):
    """(who, source frame float) for a swap-segment beat; never inside BAD_FRAMES."""
    s, n, who, f0, f1 = swap_choice(beat)
    x = min(1.0, max(0.0, (beat - s) / n))
    return who, f0 + x * (f1 - f0)


SECTIONS = {"live": (0, 6), "swap": (6, 14), "outfit": (14, 22), "hair": (22, 30), "bg": (30, 38), "montage": (38, 50),
            "cards": (50, 58), "end": (58, 66)}


def section_of(beat: float) -> str:
    for k, (a, b) in SECTIONS.items():
        if a <= beat < b:
            return k
    return "end"


def lora_cards() -> list[dict]:
    """Best SDXL+LoRA still per person (highest ArcFace cosine at scale 0.8 / 1.0) with the numbers read from results.json."""
    out = []
    spec = [("HER", "gf1500", "gfbase", "1500 steps"), ("KISLIY", "k_1200", "kbase", "1200 steps"),
            ("P4ELA", "p4_1500", "p4base", "1500 steps")]
    for name, d, base, steps in spec:
        r = json.loads((LORA_EVAL / d / "results.json").read_text(encoding="utf-8"))
        cand = [x for x in r["results"] if x["scale"] in (0.8, 1.0) and x.get("faces") == 1]
        best = max(cand, key=lambda x: x["cos"])
        scale_mean = r["summary"][str(best["scale"])]["mean_cos"]
        b = json.loads((LORA_EVAL / base / "results.json").read_text(encoding="utf-8"))["summary"]
        base_mean = next(iter(b.values()))["mean_cos"]
        out.append({"name": name, "png": str(LORA_EVAL / d / best["png"]), "cos": round(best["cos"], 2), "scale": best["scale"],
                    "mean": round(scale_mean, 2), "base": round(base_mean, 2), "steps": steps, "n": r["summary"][str(best["scale"])]["n_total"]})
    return out


class Promo:
    def __init__(self, W: int, H: int, grid: bg.BeatGrid, use_vace: bool, neon_path: Path):
        self.W, self.H, self.S = W, H, W / 1488
        self.cv = (W, H)
        self.P = grid.period
        self.grid = grid
        print("loading clips", flush=True)
        self.ref = ec.Clip(REF)
        self.orig = ec.Clip(ORIG, crop=ORIG_CROP)
        self.sw = {"her": ec.Clip(V2 / "swap-gf.mp4"), "kisliy": ec.Clip(V2 / "swap-kisliy.mp4"),
                   "p4ela": ec.Clip(V2 / "swap-p4ela.mp4")}
        self.circle = ec.Clip(CIRCLE)
        self.neon = ec.Clip(neon_path)
        self.masks = ec.MaskSeq(MASKS)
        self.vsrc = self.vgen = None
        vj = V2 / "job-vace"
        if use_vace and (vj / "master.mp4").exists():
            self.vsrc, self.vgen = ec.Clip(vj / "source.mp4"), ec.Clip(vj / "master.mp4")
        self.cards = lora_cards()
        self.card_imgs = []
        c = ec.cv2()
        for card in self.cards:
            img = c.imread(card["png"])
            self.card_imgs.append(ec.fit(img, self.cv))
        self.tile_cache: dict = {}
        self.cut_hits = [(self.B(s), 1.0) for s, _n, _w, _a, _b in SWAP_SCHEDULE[1:]]
        self.cut_hits += [(self.B(14), 1.0), (self.B(22), 1.0), (self.B(38), 0.5), (self.B(50), 0.5), (self.B(58), 1.0)]

    def B(self, k: float) -> float:
        return k * self.P

    def beat(self, tau: float) -> float:
        return tau / self.P

    # ------------------------------------------------ shots
    def shot_live(self, tau):
        t = 1.4 + tau                                     # ref-clip-05: face + uniform visible from 1.4 s
        fr = self.circle.frames[self.circle.index(t)]
        c = ec.cv2()
        d = int(self.H * 0.88)
        sq = c.resize(fr, (d, d), interpolation=c.INTER_CUBIC)
        yy, xx = np.mgrid[0:d, 0:d]
        m = (np.hypot(xx - d / 2, yy - d / 2) <= d / 2 - 2).astype(np.float32)
        m = c.GaussianBlur(m, (0, 0), 1.5)[:, :, None]
        cover = c.resize(fr[40:360, 40:360], (self.W, self.H), interpolation=c.INTER_CUBIC)
        base = c.GaussianBlur(cover, (0, 0), max(8, self.W // 40)).astype(np.float32) * 0.45
        x0, y0 = (self.W - d) // 2, (self.H - d) // 2
        roi = base[y0:y0 + d, x0:x0 + d]
        base[y0:y0 + d, x0:x0 + d] = roi * (1 - m) + sq.astype(np.float32) * m
        return np.clip(base, 0, 255).astype(np.uint8)

    def shot_swap(self, tau):
        who, fr = frame_for(self.beat(tau))
        return self.sw[who].get(fr / F, self.cv, blend=False)

    def shot_outfit(self, tau):
        b = self.beat(tau) - SECTIONS["outfit"][0]
        if self.vgen is None:                              # preview without the VACE result: the reference itself
            return self.ref.get(1.79 + b * self.P * 0.87, self.cv)
        idx = min(self.vsrc.n - 1, b / 8.0 * (self.vsrc.n - 1))
        a = self.vsrc.get(idx / F, self.cv, blend=False)
        g = self.vgen.get(idx / F, self.cv, blend=False)
        p = fx.ease_in_out((b - 1.5) / 2.5)                 # diagonal reveal between beats 1.5 and 4
        return fx.wipe(a, g, p, "diag", soft=0.05)

    def hair_t(self, tau):
        u = tau - self.B(SECTIONS["hair"][0])
        return 2.4 + u * 0.46                              # 2.4 .. 4.1 s: full-frame part, after the hands-over-face frames

    def hair_colour(self, base, idx, name):
        h = self.masks.get("hair", idx, self.cv)
        s = self.masks.get("skin", idx, self.cv)
        return ec.recolor_hair_natural(base, h, s, ec.HAIR[name]["color"])

    def shot_hair(self, tau):
        t = self.hair_t(tau)
        idx = self.ref.index(t)
        base = self.ref.get(t, self.cv)
        b = self.beat(tau) - SECTIONS["hair"][0]
        k = min(len(HAIR_SEQ), int(b // 2))                # 0,1,2 -> colours; 3 -> natural again
        into = (b - 2 * k) * self.P
        names = HAIR_SEQ + [None]
        cur_n = names[k] if k < len(names) else None
        cur = self.hair_colour(base, idx, cur_n) if cur_n else base
        if into < 0.28:
            prev_n = names[k - 1] if k > 0 else None
            prev = self.hair_colour(base, idx, prev_n) if prev_n else base
            return fx.wipe(prev, cur, fx.ease_in_out(into / 0.28), ["diag", "circle", "h", "v"][k % 4], soft=0.05)
        return cur

    def neon_t(self, tau):
        u = tau - self.B(SECTIONS["bg"][0])
        return 3.3 + u * 0.40                               # 3.3 .. 4.8 s: arms down, before the pillarboxed close-up

    def head(self, t):
        idx = self.ref.index(t)
        return self.masks.center(idx, "hair", self.cv)

    def shot_bg(self, tau):
        t = self.neon_t(tau)
        n = self.neon.get(t, self.cv)
        a = self.ref.get(t, self.cv)
        b = self.beat(tau) - SECTIONS["bg"][0]
        p = fx.ease_in_out((b - 1.0) / 1.8)
        if p <= 0:
            return a
        if p >= 1:
            return n
        cx, cy = self.head(t)
        m = fx.wipe_mask(self.H, self.W, p, "circle", soft=0.05)
        M = np.float32([[1, 0, (cx - 0.5) * self.W], [0, 1, (cy - 0.5) * self.H]])
        m = ec.cv2().warpAffine(m, M, (self.W, self.H), borderMode=ec.cv2().BORDER_REPLICATE)[:, :, None]
        return (a.astype(np.float32) * (1 - m) + n.astype(np.float32) * m).astype(np.uint8)

    def tile_frames(self, tau):
        """The four before / after pairs of the montage at this time, at full canvas size."""
        b = self.beat(tau) - SECTIONS["montage"][0]
        t = 3.2 + (tau - self.B(SECTIONS["montage"][0])) * 0.25          # 3.2 .. 4.6 s of the reference
        t_sw = min(3.95, 2.4 + (tau - self.B(SECTIONS["montage"][0])) * 0.28)
        who = ["kisliy", "p4ela", "her"][int(b // 3) % 3]
        face_a = self.orig.get(t_sw, self.cv)
        face_b = self.sw[who].get(t_sw, self.cv)
        if self.vgen is not None:
            idx = min(self.vsrc.n - 1, max(0.0, (b / 12.0) * (self.vsrc.n - 1)))
            out_a, out_b = self.vsrc.get(idx / F, self.cv), self.vgen.get(idx / F, self.cv)
        else:
            out_a = out_b = self.ref.get(t, self.cv)
        ref = self.ref.get(t, self.cv)
        idx = self.ref.index(t)
        name = HAIR_SEQ[int(b // 2) % len(HAIR_SEQ)]
        hair_b = self.hair_colour(ref, idx, name)
        bg_b = self.neon.get(t, self.cv)
        return [(face_a, face_b), (out_a, out_b), (ref, hair_b), (ref, bg_b)]

    def shot_montage(self, tau):
        b = self.beat(tau) - SECTIONS["montage"][0]
        c = ec.cv2()
        W, H = self.W, self.H
        canvas = np.zeros((H, W, 3), np.uint8)
        canvas[:] = (22, 16, 14)
        pairs = self.tile_frames(tau)
        tw, th = W // 2, H // 2
        for i, (a, bimg) in enumerate(pairs):
            appear = i * 1.0
            if b < appear:
                continue
            pop = fx.ease_out_cubic((b - appear) * self.P / 0.25)
            at = c.resize(a, (tw, th), interpolation=c.INTER_AREA)
            bt = c.resize(bimg, (tw, th), interpolation=c.INTER_AREA)
            # the divider travels left -> right -> left, period 6 beats, tiles offset by a beat
            ph = ((b - appear) / 6.0) % 1.0
            pos = 0.5 - 0.42 * np.cos(2 * np.pi * ph)
            tile = fx.split_before_after(at, bt, float(pos), line=max(2, int(4 * self.S)))
            if pop < 1.0:
                s = 0.9 + 0.1 * pop
                tile = c.resize(tile, (int(tw * s), int(th * s)), interpolation=c.INTER_AREA)
                pad = np.zeros((th, tw, 3), np.uint8)
                oy, ox = (th - tile.shape[0]) // 2, (tw - tile.shape[1]) // 2
                pad[oy:oy + tile.shape[0], ox:ox + tile.shape[1]] = tile
                tile = pad
            r, col = divmod(i, 2)
            gap = max(2, int(3 * self.S))
            canvas[r * th + gap:(r + 1) * th - gap, col * tw + gap:(col + 1) * tw - gap] = tile[gap:th - gap, gap:tw - gap]
        return canvas

    def shot_cards(self, tau):
        b = self.beat(tau) - SECTIONS["cards"][0]
        k = int(b // 2)
        c = ec.cv2()
        if k < 3:
            img = self.card_imgs[k]
            base = c.GaussianBlur(img, (0, 0), 1.0)
            # slow push-in on the still (no punch, no glitch)
            z = 1.0 + 0.04 * ((b - 2 * k) / 2.0)
            return fx.zoom_punch(base, z)
        card = np.zeros((self.H, self.W, 3), np.uint8)
        card[:] = (26, 18, 16)
        ys = np.linspace(0, 1, self.H, dtype=np.float32)[:, None, None]
        card = np.clip(card.astype(np.float32) + 28 * ys, 0, 255).astype(np.uint8)
        return card

    def shot_end(self, tau):
        base = self.neon.get(self.neon.duration - 0.2, self.cv)
        c = ec.cv2()
        base = c.GaussianBlur(base, (0, 0), max(4, self.W // 80))
        return fx.vignette_grade(base, gain=0.42, vignette=0.5)

    # ------------------------------------------------ frame
    def frame(self, tau: float) -> np.ndarray:
        sec = section_of(self.beat(tau))
        f = getattr(self, "shot_" + {"live": "live", "swap": "swap", "outfit": "outfit", "hair": "hair", "bg": "bg",
                                      "montage": "montage", "cards": "cards", "end": "end"}[sec])(tau)
        return ec.hit_fx(f, tau, self.cut_hits, self.S, window=0.16, punch=False)


def text_plan(P: float, S: float, cards: list[dict], vace: bool) -> list[ec.Text]:
    B = lambda k: round(k * P, 4)  # noqa: E731
    T = ec.Text
    lt = lambda size: dict(size=int(size * S), x=str(int(40 * S)), y=f"h-{int(96 * S)}", box=True, font=fx.FONT_BOLD, border=0, pop=0.15)  # noqa: E731
    small = dict(size=int(34 * S), x=str(int(40 * S)), y=f"h-{int(50 * S)}", box=True, font=fx.FONT, border=0, pop=0.15)
    title = dict(size=int(74 * S), x=str(int(40 * S)), y=f"h-{int(215 * S)}", box=True, font=fx.FONT_BOLD, border=0, pop=0.2, rise=int(24 * S))
    sub = dict(size=int(46 * S), x=str(int(40 * S)), y=f"h-{int(130 * S)}", box=True, font=fx.FONT_BOLD, border=0, pop=0.2, rise=int(24 * S))
    t = [T("HER OWN VIDEO  |  original 400x400 circle, nothing generated", B(0), B(6), lt(40))]
    for s, n, who, _a, _b in SWAP_SCHEDULE:
        label = {"her": "HER", "kisliy": "KISLIY", "p4ela": "P4ELA"}[who]
        t.append(T(f"FACE SWAP  |  {label}", B(s), B(s + n), lt(44)))
    t.append(T("OUTFIT  |  generated live on this clip, Wan2.1 VACE" + ("  |  TRIAL" if vace else ""), B(14), B(22), lt(40)))
    for i, name in enumerate(["BLUE", "GREEN", "WHITE", "NATURAL BLACK"]):
        t.append(T(f"HAIR  |  {name}", B(22 + 2 * i), B(24 + 2 * i), lt(44)))
    t.append(T("BACKGROUND  |  matting + night-city plate", B(30), B(34), lt(40)))
    t.append(T("BOSSMAN MOTION STUDIO 2.0", B(34), B(38), title))
    t.append(T("Genjutsu", B(35), B(38), sub))
    pos = [("FACE SWAP", 0, 0), ("OUTFIT (trial)", 1, 0), ("HAIR", 0, 1), ("BACKGROUND", 1, 1)]
    for i, (name, col, row) in enumerate(pos):
        t.append(T(name, B(38 + i), B(50), dict(size=int(34 * S), x=str(int((col * 744 + 24) * S)),
                                                 y=str(int((row * 564 + 564 - 70) * S)), box=True, font=fx.FONT_BOLD, border=0, pop=0.12)))
    for i, c in enumerate(cards):
        t.append(T(f"{c['name']}  |  SDXL + LoRA STILL, not video  |  {c['steps']}", B(50 + 2 * i), B(52 + 2 * i), lt(36)))
        t.append(T(f"ArcFace to her photos {c['cos']} (this still), set mean {c['mean']} at scale {c['scale']}, no-LoRA {c['base']}"
                   .replace("her photos", {"HER": "her photos", "KISLIY": "Kisliy's photos", "P4ELA": "p4ela's photos"}[c["name"]]),
                   B(50 + 2 * i), B(52 + 2 * i), small))
    cx = dict(size=int(84 * S), y="h*0.30", color="white", font=fx.FONT_BOLD, rise=int(30 * S))
    t.append(T("MOTION TRANSFER", B(56), B(58), dict(size=int(90 * S), y="h*0.34", color="white", font=fx.FONT_BOLD, rise=int(30 * S))))
    t.append(T("NOT WORKING YET on this PC", B(56.3), B(58), dict(size=int(48 * S), y="h*0.50", color="0xff9f6b", font=fx.FONT_BOLD)))
    t.append(T("BOSSMAN MOTION STUDIO 2.0", B(58), B(66), dict(cx, size=int(80 * S), y="h*0.22")))
    t.append(T("Genjutsu", B(59), B(66), dict(size=int(60 * S), y="h*0.36", color="0x38e8ff", font=fx.FONT_BOLD)))
    t.append(T("face  |  outfit  |  hair  |  background", B(60), B(66), dict(size=int(40 * S), y="h*0.50", color="white", font=fx.FONT)))
    t.append(T("swaps, outfit, matting and edit: rendered on one local PC (Radeon 8060S)", B(61), B(66),
               dict(size=int(30 * S), y="h*0.62", color="0xdddddd", font=fx.FONT)))
    t.append(T("LoRA cards: trained on a rented RunPod GPU, stills made locally", B(61.5), B(66),
               dict(size=int(30 * S), y="h*0.69", color="0xdddddd", font=fx.FONT)))
    t.append(T("outfit swap is a trial: Stage 2 gate not fully passed", B(62), B(66),
               dict(size=int(30 * S), y="h*0.76", color="0xdddddd", font=fx.FONT)))
    return t


def run(args) -> dict:
    t0 = time.time()
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    info_json = Q / "work-promo" / "beats.json"
    info = json.loads(info_json.read_text()) if info_json.exists() else bg.detect_beats(ORIG)
    g = info["grid"]
    grid = bg.BeatGrid(period=g["period"], phase=g["phase"])
    drop = int(info.get("drop_beat", 20))
    W, H = (496, 376) if args.preview else (1488, 1128)
    neon = V2 / "neon-matched.mp4"
    pr = Promo(W, H, grid, not args.no_vace, neon)
    inter = work / ("inter-preview.mp4" if args.preview else "inter.mp4")
    w = ec.Writer(inter, W, H, ec.FPS, crf=18 if args.preview else 12)
    n = int(round(TOTAL * ec.FPS))
    for i in range(n):
        w.write(pr.frame(i / ec.FPS))
        if i % 60 == 0:
            print(f"frame {i}/{n} {time.time() - t0:.0f}s", flush=True)
    w.close()
    # the track's drop (beat 20) lands on promo beat 14 = start of the outfit change
    segs = bg.audio_loop_plan(grid, TOTAL, intro_beats=14, loop_beats=8, start_beat=drop - 14)
    audio = work / "audio.wav"
    ec.build_audio(ORIG, segs, audio, fade_out=0.8)
    texts = text_plan(grid.period, W / 1488, pr.cards, pr.vgen is not None)
    out = Path(args.out)
    ec.final_pass(inter, audio, texts, out, crf=23 if args.preview else 20, duration=TOTAL)
    rep = {"out": str(out), "duration": TOTAL, "size": [W, H], "fps": ec.FPS, "bpm": round(grid.bpm, 2), "period": grid.period,
           "drop_beat_in_track": drop, "audio_segments": segs, "sections_beats": SECTIONS, "swap_schedule": SWAP_SCHEDULE,
           "skipped_bad_frames": [BAD_FRAMES.start, BAD_FRAMES.stop - 1], "vace_used": pr.vgen is not None,
           "lora_cards": pr.cards, "seconds": round(time.time() - t0, 1)}
    (work / ("report-preview.json" if args.preview else "report.json")).write_text(json.dumps(rep, indent=1), encoding="utf-8")
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--work", default=str(V2 / "work-promo2"))
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--no-vace", action="store_true")
    a = ap.parse_args(argv)
    print(json.dumps(run(a), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
