"""Queue items 2-5 on the same machinery as build_promo.py (ffmpeg + numpy + OpenCV, everything local).

  neon     «Тот же ролик, другой фон по теме»: ref-promo-08 over the neon plate (RVM job master) + light grade + lower third
  trend-a  her swapped clip, hard cuts on the beat grid: jump-cut punch-ins, velocity ramps, flash frames, whip pans
  trend-b  hair-colour transformation montage, 9:16 face-centred reframing (--vertical) or the original 4:3 framing
  trend-c  outfit / background transformation reel: uniform, denim, emerald presets + neon background, kinetic text

  <gate-venv>/python tools/trend_edit/build_trends.py neon|trend-a|trend-b|trend-c --out X.mp4 [--vertical] [--preview]
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
import build_promo as bp  # noqa: E402
import edit_core as ec  # noqa: E402
import fx  # noqa: E402

WORK = bp.VID / "promo-queue-20261010"
CYAN, PINK, GREY = "0x38e8ff", "0xff4fd8", "0xbbbbbb"


# ------------------------------------------------------------------ shared
class Ctx:
    def __init__(self, W: int, H: int, need_neon: bool, name: str):
        self.W, self.H, self.S = W, H, W / 1488
        self.cv = (W, H)
        info_json = WORK / "work-promo" / "beats.json"
        info = json.loads(info_json.read_text()) if info_json.exists() else bg.detect_beats(bp.REF_ORIG)
        g = info["grid"]
        self.grid = bg.BeatGrid(period=g["period"], phase=g["phase"])
        self.P, self.phi = self.grid.period, self.grid.phase
        self.drop = int(info.get("drop_beat", 20))
        self.work = WORK / f"work-{name}"
        self.work.mkdir(parents=True, exist_ok=True)
        print("loading clips", flush=True)
        self.swap = ec.Clip(bp.REF_SWAP)
        self.masks = ec.MaskSeq(bp.MASKS)
        self.neon = None
        if need_neon:
            for n in ("master.mp4", "final.mp4"):
                if (bp.NEON_DIR / n).exists():
                    self.neon = ec.Clip(bp.NEON_DIR / n)
                    break
            if self.neon is None:
                raise SystemExit("neon master missing: promo-bg-20261010/job-neon is not finished")
        self.duration = round(self.swap.duration, 3)
        self._head: np.ndarray | None = None

    def B(self, k: float) -> float:
        return k * self.P

    def src_t(self, tau: float) -> float:
        return tau + self.phi

    def heads(self) -> np.ndarray:
        """Head centre (x, y fractions) for every source frame: hair centroid, median + EMA smoothed, cached."""
        if self._head is None:
            cache = WORK / "heads-promo08.npy"
            if cache.exists():
                self._head = np.load(cache)
            else:
                raw = np.array([self.masks.center(i, "hair", (372, 282)) for i in range(self.swap.n)], np.float32)
                k = 5
                pad = np.pad(raw, ((k, k), (0, 0)), mode="edge")
                med = np.array([np.median(pad[i:i + 2 * k + 1], axis=0) for i in range(len(raw))], np.float32)
                out, prev = [], med[0]
                for m in med:
                    prev = 0.35 * m + 0.65 * prev
                    out.append(prev.copy())
                self._head = np.array(out, np.float32)
                np.save(cache, self._head)
        return self._head

    def head_at(self, t: float) -> tuple[float, float]:
        h = self.heads()[self.swap.index(t)]
        return float(h[0]), float(h[1])

    def hair(self, base, idx, name, strength=None):
        spec = dict(ec.HAIR[name])
        return ec.recolor(base, {"hair": (self.masks.get("hair", idx, self.cv), spec)})

    def outfit(self, base, idx, preset):
        regs = {}
        for region, spec in ec.OUTFIT[preset].items():
            regs[region] = (self.masks.get(region, idx, self.cv), spec)
        return ec.recolor(base, regs)


def render(ctx: Ctx, frame_fn, out_inter: Path, n_frames: int, W: int, H: int, crf: int) -> None:
    t0 = time.time()
    w = ec.Writer(out_inter, W, H, ec.FPS, crf=crf)
    for i in range(n_frames):
        w.write(frame_fn(i / ec.FPS))
        if i % 72 == 0:
            print(f"frame {i}/{n_frames} {time.time() - t0:.0f}s", flush=True)
    w.close()


def audio_for(ctx: Ctx, total: float) -> Path:
    """The track as it is (one pass, beat 0..end), faded at the end."""
    out = ctx.work / "audio.wav"
    ec.build_audio(bp.REF_ORIG, [(ctx.grid.phase, total)], out, fade_out=0.5)
    return out


# ------------------------------------------------------------------ 2: same clip, other background
def match_grade(frame: np.ndarray, alpha: np.ndarray, W: int) -> np.ndarray:
    """Light grade so the person sits in the neon plate: warm-to-magenta cast and slightly lifted contrast on the person,
    plus a soft coloured light wrap of the background around the outline."""
    c = ec.cv2()
    a = (alpha.astype(np.float32) / 255.0)[:, :, None]
    f = frame.astype(np.float32)
    fg = f * np.array([1.07, 0.96, 1.05], np.float32)             # BGR: a little more blue/red than green
    fg = (fg - 128.0) * 1.05 + 128.0
    wrap = c.GaussianBlur(alpha, (0, 0), max(3, W // 70)).astype(np.float32)[:, :, None] / 255.0
    rim = np.array([150.0, 40.0, 150.0], np.float32)              # magenta light spilling onto the outline
    out = fg * a + f * (1 - a)
    out = out + rim * wrap * (1 - a) * 0.22
    return np.clip(out, 0, 255).astype(np.uint8)


def recipe_neon(ctx: Ctx, args):
    alpha_dir = bp.NEON_DIR / "alpha"
    n = int(round(ctx.duration * ec.FPS))
    hits = [(ctx.B(4), 1.5), (ctx.B(ctx.drop), 1.0)]
    cache: dict[int, np.ndarray] = {}

    def alpha_for(idx):
        if idx not in cache:
            m = ec.cv2().imread(str(alpha_dir / f"{idx + 1:04d}.png"), ec.cv2().IMREAD_GRAYSCALE)
            if m is None:
                m = np.full((ctx.H, ctx.W), 255, np.uint8)
            elif (m.shape[1], m.shape[0]) != ctx.cv:
                m = ec.cv2().resize(m, ctx.cv)
            cache.clear()
            cache[idx] = m
        return cache[idx]

    def frame(tau):
        t = ctx.src_t(tau)
        idx = ctx.neon.index(t)
        a = ctx.swap.get(t, ctx.cv)
        b = match_grade(ctx.neon.get(t, ctx.cv), alpha_for(idx), ctx.W)
        r0, r1 = ctx.B(4), ctx.B(5.5)
        p = fx.ease_in_out((tau - r0) / (r1 - r0))
        if p <= 0:
            f = a
        elif p >= 1:
            f = b
        else:
            cx, cy = ctx.head_at(t)
            m = fx.wipe_mask(ctx.H, ctx.W, p, "circle", soft=0.05)
            M = np.float32([[1, 0, (cx - 0.5) * ctx.W], [0, 1, (cy - 0.5) * ctx.H]])
            m = ec.cv2().warpAffine(m, M, (ctx.W, ctx.H), borderMode=ec.cv2().BORDER_REPLICATE)[:, :, None]
            f = (a.astype(np.float32) * (1 - m) + b.astype(np.float32) * m).astype(np.uint8)
        return ec.hit_fx(f, tau, hits, ctx.S)

    S = ctx.S
    lower = dict(size=int(50 * S), x=str(int(48 * S)), y=f"h-{int(150 * S)}", box=True, font=fx.FONT_BOLD, border=0, pop=0.25,
                 rise=int(30 * S))
    sub = dict(size=int(34 * S), x=str(int(48 * S)), y=f"h-{int(88 * S)}", box=True, font=fx.FONT, border=0, pop=0.25,
               rise=int(30 * S))
    texts = [
        ec.Text("Тот же ролик, другой фон по теме", ctx.B(6), ctx.duration - 0.4, lower),
        ec.Text("ночной неоновый город | матирование RVM + SAM2, локально", ctx.B(6.5), ctx.duration - 0.4, sub),
        ec.Text("ДО", ctx.B(0.5), ctx.B(4), dict(size=int(60 * S), x=str(int(40 * S)), y=str(int(40 * S)), font=fx.FONT_BOLD,
                                                    pop=0.1)),
        ec.Text("ПОСЛЕ", ctx.B(5.5), ctx.B(10), dict(size=int(60 * S), x=str(int(40 * S)), y=str(int(40 * S)),
                                                      font=fx.FONT_BOLD, pop=0.1)),
    ]
    return frame, n, texts, ctx.duration


# ------------------------------------------------------------------ 3: trend A, beat cuts on the swapped clip
ZOOMS = [1.0, 1.38, 1.0, 1.62, 1.18, 1.45, 1.0, 1.28]


def recipe_trend_a(ctx: Ctx, args):
    n = int(round(ctx.duration * ec.FPS))
    ramp_beats = [8, 14, 20, 26, 30]
    knots: list[tuple[float, float]] = [(0.0, 1.0)]
    for b in ramp_beats:
        knots += fx.velocity_knots_balanced(ctx.B(b), lead=0.30, hold=0.10, tail=0.30, slow=0.4)[1:]
    flash_beats = [8, 16, 24]
    hits = [(ctx.B(k), 1.0 if k % 4 == 0 else 0.5) for k in range(1, 31)]
    hits = [h for h in hits if abs(h[0] / ctx.P - ctx.drop) > 1e-6] + [(ctx.B(ctx.drop), 2.0)]
    hits += [(ctx.B(k), 1.0) for k in flash_beats]
    hits.sort()
    whip_beats = [12, 28]

    def frame(tau):
        t = ctx.src_t(fx.ramp_source_time(tau, knots))
        f = ctx.swap.get(t, ctx.cv)
        k = int((tau + 1e-9) / ctx.P)
        cx, cy = ctx.head_at(t)
        # jump-cut punch-in: the zoom level changes on the beat (a hard cut), centred on the face
        z = ZOOMS[k % len(ZOOMS)]
        if tau >= ctx.B(ctx.drop) and tau < ctx.B(ctx.drop + 4):
            z = max(z, 1.3)
        f = fx.zoom_punch(f, z, (cx, cy + 0.04))
        for wb in whip_beats:
            d = tau - ctx.B(wb)
            if -0.14 < d < 0.14:
                f = fx.whip_blur(f, 0.12 * (1 - abs(d) / 0.14), True)
        f = ec.hit_fx(f, tau, hits, ctx.S, (cx, cy))
        return f

    S = ctx.S
    texts = [ec.Text("TREND EDIT A  |  beat cuts, punch-in, velocity ramps", 0.0, ctx.B(6),
                     dict(size=int(40 * S), x=str(int(34 * S)), y=f"h-{int(86 * S)}", box=True, font=fx.FONT, border=0, pop=0.12)),
             ec.Text("DROP", ctx.B(ctx.drop), ctx.B(ctx.drop + 2), dict(size=int(170 * S), y="h*0.03", color=PINK, rise=int(40 * S)))]
    return frame, n, texts, ctx.duration


# ------------------------------------------------------------------ 4: trend B, hair colour montage (9:16)
HAIR_CYCLE = ["blue", "green", "white", "copper", "platinum", "black"]
HAIR_LABEL = {"blue": "BLUE", "green": "GREEN", "white": "WHITE", "copper": "COPPER", "platinum": "PLATINUM", "black": "BLACK"}
HAIR_COLOR = {"blue": "0x4f8dff", "green": "0x39d98a", "white": "white", "copper": "0xe0904f", "platinum": "0xece6da",
              "black": "0xbbbbbb"}


def recipe_trend_b(ctx: Ctx, args):
    n = int(round(ctx.duration * ec.FPS))
    chunk = 2.0       # beats per colour
    hits = [(ctx.B(k * chunk), 1.0) for k in range(1, int(32 / chunk))]
    hits += [(ctx.B(k), 0.5) for k in range(1, 32) if k % chunk != 0]
    hits = [h for h in hits if abs(h[0] / ctx.P - ctx.drop) > 1e-6] + [(ctx.B(ctx.drop), 2.0)]
    hits.sort()
    vertical = args.vertical

    def colour_frame(t, idx, ci):
        base = ctx.swap.get(t, ctx.cv)
        return ctx.hair(base, idx, HAIR_CYCLE[ci % len(HAIR_CYCLE)])

    def frame(tau):
        t = ctx.src_t(tau)
        idx = ctx.swap.index(t)
        k = int((tau + 1e-9) / (ctx.P * chunk))
        into = tau - k * ctx.P * chunk
        cur = colour_frame(t, idx, k)
        if k > 0 and into < 0.2:
            prev = colour_frame(t, idx, k - 1)
            cur = fx.wipe(prev, cur, fx.ease_in_out(into / 0.2), ["diag", "h", "circle", "v"][k % 4])
        cx, cy = ctx.head_at(t)
        cur = ec.hit_fx(cur, tau, hits, ctx.S, (cx, cy))
        if vertical:
            cw = int(round(ctx.H * 9 / 16))
            x0 = int(min(max(cx * ctx.W - cw / 2, 0), ctx.W - cw))
            crop = cur[:, x0:x0 + cw]
            return ec.cv2().resize(crop, (720, 1280), interpolation=ec.cv2().INTER_CUBIC)
        return cur

    out_w, out_h = (720, 1280) if vertical else (ctx.W, ctx.H)
    k_total = int(32 / chunk) + 1
    s = out_w / 720 if vertical else ctx.S
    texts = []
    for k in range(k_total):
        name = HAIR_CYCLE[k % len(HAIR_CYCLE)]
        t0, t1 = ctx.B(k * chunk), min(ctx.duration, ctx.B((k + 1) * chunk))
        if t0 >= ctx.duration:
            break
        texts.append(ec.Text(HAIR_LABEL[name], t0, t1 - 0.05,
                             dict(size=int((130 if vertical else 110) * s), y=f"h*{0.08 if vertical else 0.07}",
                                  color=HAIR_COLOR[name], rise=int(30 * s))))
    texts.append(ec.Text("HAIR COLOUR  |  Genjutsu, local", 0.0, ctx.duration - 0.3,
                         dict(size=int(40 * s), x=str(int(30 * s)), y=f"h-{int(70 * s)}", box=True, font=fx.FONT,
                              border=0, pop=0.12)))
    return frame, n, texts, ctx.duration, (out_w, out_h)


# ------------------------------------------------------------------ 5: trend C, outfit / background transformation
C_SEQ = [("orig", None, False), ("UNIFORM", "uniform", False), ("DENIM", "denim", False), ("EMERALD", "emerald", False),
         ("NEON CITY", None, True), ("NEON + UNIFORM", "uniform", True), ("NEON + DENIM", "denim", True),
         ("NEON + EMERALD", "emerald", True)]
C_COLOR = [None, "white", "0x7fa6e0", "0x39d98a", PINK, "white", "0x7fa6e0", "0x39d98a"]


def recipe_trend_c(ctx: Ctx, args):
    n = int(round(ctx.duration * ec.FPS))
    chunk = 4.0
    hits = [(ctx.B(k * chunk), 1.5) for k in range(1, 8)]
    hits += [(ctx.B(k), 0.5) for k in range(2, 32, 2) if k % chunk != 0]
    hits = [h for h in hits if abs(h[0] / ctx.P - ctx.drop) > 1e-6]
    hits.append((ctx.B(ctx.drop), 2.0))
    hits.sort()

    def look(t, idx, ci):
        _name, preset, with_bg = C_SEQ[ci % len(C_SEQ)]
        base = (ctx.neon if with_bg else ctx.swap).get(t, ctx.cv)
        if preset:
            base = ctx.outfit(base, idx, preset)
        return base

    def frame(tau):
        t = ctx.src_t(tau)
        idx = ctx.swap.index(t)
        k = int((tau + 1e-9) / (ctx.P * chunk))
        into = tau - k * ctx.P * chunk
        cur = look(t, idx, k)
        if k > 0 and into < 0.22:
            prev = look(t, idx, k - 1)
            p = fx.ease_in_out(into / 0.22)
            cur = fx.wipe(fx.whip_blur(prev, 0.08 * (1 - p), True), cur, p, ["circle", "diag", "h", "v"][k % 4], soft=0.06)
        cx, cy = ctx.head_at(t)
        return ec.hit_fx(cur, tau, hits, ctx.S, (cx, cy))

    S = ctx.S
    texts = []
    for k, (name, _p, _b) in enumerate(C_SEQ):
        t0, t1 = ctx.B(k * chunk), min(ctx.duration, ctx.B((k + 1) * chunk))
        if t0 >= ctx.duration:
            break
        if name == "orig":
            texts.append(ec.Text("ORIGINAL", t0, t1 - 0.05, dict(size=int(110 * S), y="h*0.07", color="white", rise=int(30 * S))))
        else:
            texts.append(ec.Text(name, t0, t1 - 0.05, dict(size=int(130 * S), y="h*0.07", color=C_COLOR[k], rise=int(30 * S))))
    texts.append(ec.Text("OUTFIT + BACKGROUND  |  Genjutsu, local | outfit = Lab recolour (not a generative swap)", 0.0,
                         ctx.duration - 0.3, dict(size=int(32 * S), x=str(int(30 * S)), y=f"h-{int(70 * S)}", box=True,
                                                   font=fx.FONT, border=0, pop=0.12)))
    return frame, n, texts, ctx.duration


RECIPES = {"neon": (recipe_neon, True), "trend-a": (recipe_trend_a, False), "trend-b": (recipe_trend_b, False),
           "trend-c": (recipe_trend_c, True)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("recipe", choices=RECIPES)
    ap.add_argument("--out", required=True)
    ap.add_argument("--vertical", action="store_true", help="trend-b: 9:16 face-centred crop")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--crf", type=int, default=21)
    a = ap.parse_args(argv)
    fn, need_neon = RECIPES[a.recipe]
    W, H = (496, 376) if a.preview else (1488, 1128)
    ctx = Ctx(W, H, need_neon, a.recipe + ("-v" if a.vertical else "") + ("-prev" if a.preview else ""))
    res = fn(ctx, a)
    frame_fn, n, texts, total = res[:4]
    inter = ctx.work / "inter.mp4"
    t0 = time.time()
    render(ctx, frame_fn, inter, n, *(res[4] if len(res) > 4 else (W, H)), crf=13 if not a.preview else 18)
    audio = audio_for(ctx, total)
    final_pass_scale = None
    ec.final_pass(inter, audio, texts, Path(a.out), crf=a.crf, duration=total, scale=final_pass_scale)
    rep = {"out": a.out, "recipe": a.recipe, "duration": total, "size": list(res[4]) if len(res) > 4 else [W, H],
           "bpm": round(ctx.grid.bpm, 2), "neon": need_neon, "seconds": round(time.time() - t0, 1)}
    (ctx.work / "report.json").write_text(json.dumps(rep, indent=1))
    print(json.dumps(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
