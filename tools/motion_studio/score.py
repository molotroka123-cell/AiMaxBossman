"""Spec-driven soundtrack: an original score synthesized from scratch (no samples).

The arrangement is derived from the scene spec, so music and picture share one clock:
  * intro drone + clock ticks until the first cut, riser into it;
  * minor-key groove (i-VI-III-VII) with 16th arpeggios until the logo;
  * snare roll, riser and a half-second breath, then the logo impact lifts to major;
  * after the logo an epic major section (I-vi-IV-V, choir, brass, taiko);
  * an end card gets its own build and final hit; brass/stab accents on every item.
Voice-over clips (from make_video.py) are mixed on top with sidechain ducking.
"""
from __future__ import annotations

import numpy as np
from scipy.signal import butter, fftconvolve, sosfilt

SR = 48000
KEY_OFFSET = {"C": -2, "D": 0, "E": 2, "F": 3, "G": 5, "A": 7, "B": 9}
MINOR = [[50, 53, 57, 62], [46, 50, 53, 58], [45, 48, 53, 57], [48, 52, 55, 60]]   # Dm Bb F C
MAJOR = [[50, 54, 57, 62], [47, 50, 54, 59], [43, 50, 55, 59], [45, 49, 52, 57]]   # D Bm G A


class Score:
    def __init__(self, duration: float, seed: int = 7):
        self.n = int(SR * duration)
        self.L = np.zeros(self.n)
        self.R = np.zeros(self.n)
        self.rng = np.random.default_rng(seed)

    # ---------------------------------------------------------------- dsp helpers
    @staticmethod
    def lp(x, f, order=2): return sosfilt(butter(order, min(f, SR / 2 - 100), "low", fs=SR, output="sos"), x)

    @staticmethod
    def hp(x, f, order=2): return sosfilt(butter(order, f, "high", fs=SR, output="sos"), x)

    @staticmethod
    def bp(x, lo, hi): return sosfilt(butter(2, [lo, min(hi, SR / 2 - 100)], "band", fs=SR, output="sos"), x)

    @staticmethod
    def hz(m): return 440.0 * 2 ** ((m - 69) / 12)

    @staticmethod
    def env(n, a, d, s, r, total):
        e = np.zeros(n)
        A, D, Rr = max(int(a * SR), 1), max(int(d * SR), 1), max(int(r * SR), 1)
        hold = max(int(total * SR) - A - D, 0)
        seg = np.concatenate([np.linspace(0, 1, A), np.linspace(1, s, D), np.full(hold, s), np.linspace(s, 0, Rr)])
        e[:min(n, len(seg))] = seg[:n]
        return e

    def saw(self, f, n, detune=(0,)):
        t = np.arange(n) / SR
        out = np.zeros(n)
        for d in detune:
            ph = (f * (1 + d) * t + self.rng.random()) % 1.0
            out += 2 * ph - 1
        return out / len(detune)

    def put(self, start, sig, gain=1.0, pan=0.0):
        i = int(start * SR)
        j = min(i + len(sig), self.n)
        if 0 <= i < self.n and j > i:
            self.L[i:j] += sig[:j - i] * gain * np.sqrt(0.5 * (1 - pan))
            self.R[i:j] += sig[:j - i] * gain * np.sqrt(0.5 * (1 + pan))

    # ---------------------------------------------------------------- instruments
    def kick(self, g=1.0):
        n = int(0.45 * SR); t = np.arange(n) / SR
        f = 42 + 110 * np.exp(-t * 32)
        return np.tanh(1.6 * np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 7.5)) * g

    def clap(self):
        n = int(0.35 * SR); t = np.arange(n) / SR
        e = np.zeros(n)
        for o in (0, .011, .022):
            e += (t >= o) * np.exp(-np.clip(t - o, 0, None) * (90 if o < .02 else 18))
        return self.bp(self.rng.standard_normal(n), 900, 5000) * e * 0.55

    def snare(self):
        n = int(0.9 * SR); t = np.arange(n) / SR
        return (np.sin(2 * np.pi * 190 * t) * np.exp(-t * 18) * .5
                + self.bp(self.rng.standard_normal(n), 1200, 9000) * np.exp(-t * 7)) * 0.6

    def hat(self, open_=False):
        n = int((0.22 if open_ else 0.06) * SR); t = np.arange(n) / SR
        return self.hp(self.rng.standard_normal(n), 7000) * np.exp(-t * (14 if open_ else 70)) * 0.35

    def tick(self):
        n = int(0.03 * SR); t = np.arange(n) / SR
        return self.bp(self.rng.standard_normal(n), 2500, 6000) * np.exp(-t * 220) * 0.35

    def tom(self, f0=110, g=0.8):
        n = int(0.5 * SR); t = np.arange(n) / SR
        f = f0 * (1 + 0.8 * np.exp(-t * 30))
        return np.tanh(1.5 * np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 6)) * g

    def pluck(self, m, dur=0.22):
        n = int((dur + 0.3) * SR); t = np.arange(n) / SR
        s = self.saw(self.hz(m), n, (-0.004, 0.004))
        cut = 600 + 5200 * np.exp(-t * 18)
        y = np.zeros(n)
        for k in range(0, n, 480):
            y[k:k + 480] = self.lp(s[k:k + 480], cut[k])
        return y * np.exp(-t * 9) * 0.5

    def pad(self, ms, dur, bright=1800, g=0.18):
        n = int((dur + 0.8) * SR); s = np.zeros(n)
        for m in ms:
            s += self.saw(self.hz(m), n, (-0.006, 0, 0.007))
        return self.hp(self.lp(s, bright, 3), 170) * self.env(n, .25, .3, .8, .8, dur) * g / len(ms) ** .5

    def choir(self, ms, dur, g=0.2):
        n = int((dur + 1.0) * SR); t = np.arange(n) / SR; s = np.zeros(n)
        for m in ms:
            ph = np.cumsum(self.hz(m) * (1 + 0.004 * np.sin(2 * np.pi * 5.2 * t + self.rng.random() * 6))) / SR
            for d in (-0.005, 0.0, 0.006):
                s += 2 * ((ph * (1 + d) + self.rng.random()) % 1.0) - 1
        s = self.bp(s, 550, 900) * .9 + self.bp(s, 1050, 1400) * .6 + self.bp(s, 2300, 2900) * .25
        return s * self.env(n, .35, .3, .85, 1.0, dur) * g / len(ms) ** .5

    def brass(self, ms, dur=0.45, g=0.3):
        n = int((dur + 0.4) * SR); t = np.arange(n) / SR; s = np.zeros(n)
        for m in ms:
            s += self.saw(self.hz(m), n, (-0.004, 0.004))
        y = np.zeros(n); cut = 900 + 4200 * np.exp(-t * 7)
        for k in range(0, n, 480):
            y[k:k + 480] = self.lp(s[k:k + 480], cut[k])
        return self.hp(y, 120) * self.env(n, .012, .15, .7, .35, dur) * g / len(ms) ** .5

    def sub(self, m, dur):
        n = int(dur * SR); t = np.arange(n) / SR
        return np.sin(2 * np.pi * self.hz(m) * t) * self.env(n, .005, .08, .7, .05, dur - .05) * 0.55

    def riser(self, dur, f0=300, f1=6000, g=0.25):
        n = int(dur * SR); t = np.arange(n) / SR; k = t / dur
        nz = self.rng.standard_normal(n); y = np.zeros(n)
        for i in range(0, n, 960):
            c = f0 * (f1 / f0) ** k[i]
            y[i:i + 960] = self.bp(nz[i:i + 960], c * .7, c * 1.4)
        return (y + np.sin(2 * np.pi * np.cumsum(220 * (4 ** k)) / SR) * .25) * k ** 2 * g

    def whoosh(self, dur=0.35, g=0.25):
        n = int(dur * SR); t = np.arange(n) / SR
        return self.bp(self.rng.standard_normal(n), 800, 7000) * np.sin(np.pi * t / dur) ** 2 * g

    def boom(self):
        n = int(2.6 * SR); t = np.arange(n) / SR
        f = 30 + 55 * np.exp(-t * 6)
        return (np.tanh(1.3 * np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 1.6)) * .9
                + self.hp(self.rng.standard_normal(n), 3000) * np.exp(-t * 2.2) * .35)

    def bell(self, m, dur=1.2):
        n = int(dur * SR); t = np.arange(n) / SR; f = self.hz(m)
        return (np.sin(2 * np.pi * f * t) + .4 * np.sin(2 * np.pi * f * 2.76 * t) * np.exp(-t * 6)) * np.exp(-t * 3.2) * .22

    # ---------------------------------------------------------------- sections
    def groove(self, a, b, beat, chords, k):
        """Four-on-the-floor minor groove between a and b (bar = 2 beats)."""
        bar = 2 * beat
        for i in range(int((b - a) / beat)):
            tb = a + i * beat
            self.put(tb, self.kick(.95))
            if i % 2:
                self.put(tb, self.clap(), .9)
            self.put(tb + beat / 2, self.hat(), .9, .25)
        for c in range(int(np.ceil((b - a) / bar))):
            tc = a + c * bar
            notes = [x + k for x in chords[c % 4]]
            self.put(tc, self.pad(notes, bar, 1400 + 120 * c, .16))
            for e in range(8):
                if tc + e * bar / 8 < b:
                    self.put(tc + e * bar / 8, self.sub(notes[0] - 12, bar / 8 - .01), .32 if e % 2 else .55)
            arp = [notes[0] + 12, notes[2] + 12, notes[1] + 12, notes[3] + 12]
            for s in range(8):
                ts = tc + s * bar / 8
                if a + bar / 2 <= ts < b - .1:
                    self.put(ts, self.pluck(arp[s % 4]), .55, .45 if s % 2 else -.45)
                    self.put(ts + .375, self.pluck(arp[s % 4], .12), .18, -.45 if s % 2 else .45)

    def epic(self, a, b, beat, k):
        """Half-time epic section in major: choir, pads, taiko fills."""
        bar = 2 * beat
        for c in range(int(np.ceil((b - a) / bar))):
            tc = a + c * bar
            notes = [x + k for x in MAJOR[c % 4]]
            self.put(tc, self.choir([x + 12 for x in notes[1:]] + [notes[0] + 24], bar, .24))
            self.put(tc, self.pad(notes, bar, 2600, .16))
            for e in range(8):
                if tc + e * bar / 8 < b:
                    self.put(tc + e * bar / 8, self.sub(notes[0] - 12, bar / 8 - .01), .5 if e % 2 else .7)
            for s in range(8):
                ts = tc + s * bar / 8
                if ts < b - .1:
                    self.put(ts, self.pluck(notes[s % 4] + 24, .16), .42, .5 if s % 2 else -.5)
        for i in range(int((b - a) / beat)):
            tb = a + i * beat
            self.put(tb, self.kick(1.0))
            if i % 2:
                self.put(tb, self.snare(), .85)
            if i % 4 == 3:
                for j, f0 in enumerate((140, 120, 100, 82)):
                    self.put(tb + j * beat / 4, self.tom(f0, .7), 1.0, -.4 + .27 * j)

    def build_and_hit(self, t_hit, beat, k, major=True, length=1.5):
        """Snare roll + riser into t_hit, a short breath, then boom + big chord."""
        start = t_hit - length
        rt = start
        while rt < t_hit - .5:
            step = beat / 2 if rt < t_hit - 1.0 else beat / 4
            self.put(rt, self.clap(), .35 + .5 * (rt - start) / length)
            rt += step
        self.put(start, self.riser(length - .4, 300, 9000, .3))
        self.put(t_hit - .5, self.riser(.5, 2000, 200, .2)[::-1])
        self.put(t_hit, self.boom(), 1.0)
        chord = [x + k for x in (MAJOR[0] if major else MINOR[0])]
        self.put(t_hit, self.pad(chord + [chord[2] + 12, chord[3] + 12], 1.8, 3800, .32))
        self.put(t_hit, self.choir([x + 12 for x in chord], 1.8, .3))
        self.put(t_hit, self.sub(chord[0] - 12, 1.8), .9)
        for j, m in enumerate([chord[3] + 12, chord[1] + 24, chord[2] + 24, chord[3] + 24]):
            self.put(t_hit + .08 + .12 * j, self.bell(m), .7, -.5 + .3 * j)

    # ---------------------------------------------------------------- mix
    def mix(self, vo: np.ndarray | None) -> np.ndarray:
        n_ir = int(2.4 * SR); ti = np.arange(n_ir) / SR
        irs = []
        for _ in range(2):
            ir = self.lp(self.rng.standard_normal(n_ir) * np.exp(-ti * 2.6), 6000)
            irs.append(ir / (np.abs(ir).sum() ** .5 * 6))
        mL = self.hp(self.L + .35 * fftconvolve(self.L, irs[0])[:self.n], 28)
        mR = self.hp(self.R + .35 * fftconvolve(self.R, irs[1])[:self.n], 28)
        if vo is None:
            vo = np.zeros(self.n)
        vo = self.hp(vo, 90)
        vo = np.tanh(vo * 1.4) / np.tanh(1.4)
        envv = self.lp(np.abs(vo), 12)
        duck = 1 - .55 * np.clip(envv / (envv.max() + 1e-9) * 3, 0, 1)
        vo_rev = fftconvolve(vo, irs[0][:int(.6 * SR)] * .5)[:self.n]
        out = np.stack([.62 * mL * duck + .95 * vo + .15 * vo_rev, .62 * mR * duck + .95 * vo + .15 * vo_rev], 1)
        fade = np.ones(self.n); fi = max(self.n - int(.45 * SR), 0)
        fade[fi:] = np.linspace(1, 0, self.n - fi) ** 1.5
        fade[:int(.02 * SR)] = np.linspace(0, 1, int(.02 * SR))
        out = np.tanh(out * fade[:, None] * 1.2) / np.tanh(1.2)
        return out / (np.abs(out).max() + 1e-9) * .93


def compose(spec: dict, hits: list[float], vo: np.ndarray | None = None, seed: int = 7) -> np.ndarray:
    meta, scenes = spec["meta"], spec["scenes"]
    dur, beat, k = float(meta["duration"]), 60.0 / float(meta.get("bpm", 120)), KEY_OFFSET[meta.get("key", "D")]
    sc = Score(dur, seed)
    t_drop = scenes[1]["start"] if len(scenes) > 1 else dur
    logo = next((s for s in scenes if s["type"] == "logo"), None)
    end_card = next((s for s in scenes if s["type"] == "end_card" and s is not scenes[0]), None)
    # intro
    sc.put(0.0, sc.pad([38 + k, 50 + k, 57 + k], min(t_drop, dur) + .2, 700, .22))
    for i in range(int(min(t_drop, dur) / .25)):
        sc.put(.25 * i, sc.tick(), .8 if i % 4 == 0 else .45, .3 if i % 2 else -.3)
    if t_drop < dur:
        sc.put(max(0.0, t_drop - 1.8), sc.riser(min(1.8, t_drop), 200, 8000, .22))
    # main groove until the logo (or the end card, or the end)
    main_end = logo["start"] - .5 if logo else (end_card["start"] if end_card else dur)
    if logo:
        sc.groove(t_drop, max(t_drop, main_end - 1.0), beat, MINOR, k)
        sc.build_and_hit(logo["start"], beat, k, major=True, length=min(2.5, logo["start"] - t_drop))
    elif t_drop < main_end:
        sc.groove(t_drop, main_end, beat, MINOR, k)
    # after the logo: epic major section
    if logo and logo["end"] < dur - .5:
        epic_end = end_card["start"] - (1.0 if end_card else 0) if end_card else dur
        sc.put(logo["end"] - 1.0, sc.riser(1.0, 250, 9000, .3))
        sc.put(logo["end"], sc.boom(), .75)
        if epic_end > logo["end"]:
            sc.epic(logo["end"], epic_end, beat, k)
    if end_card:
        sc.build_and_hit(end_card["start"], beat, k, major=True, length=1.0)
    # accents on items
    for s in scenes:
        for j, it in enumerate(s.get("items", [])):
            notes = [x + k + 12 for x in (MAJOR if logo and s["start"] >= logo["start"] else MINOR)[j % 4]]
            sc.put(it["t"], sc.brass(notes, .4, .3), 1.0, (j - 1.5) * .15)
            sc.put(it["t"] - .12, sc.whoosh(.14, .18), 1.0, (j - 2) * .3)
    return sc.mix(vo)
