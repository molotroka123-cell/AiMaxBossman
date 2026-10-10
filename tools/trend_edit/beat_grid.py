"""Beat grid of a track (librosa) and everything that is snapped to it: cuts, bars, audio loops.

Pure functions (fit, snap, plan) are unit-tested without audio; `detect_beats` needs librosa + ffmpeg.

  python tools/trend_edit/beat_grid.py clip.mp4            # prints bpm, phase, first beats
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BeatGrid:
    """A constant-tempo grid: beat k is at phase + k * period (seconds)."""
    period: float
    phase: float = 0.0
    beats_per_bar: int = 4

    @property
    def bpm(self) -> float:
        return 60.0 / self.period

    def time(self, k: float) -> float:
        return self.phase + k * self.period

    def index(self, t: float) -> float:
        return (t - self.phase) / self.period

    def nearest(self, t: float, division: int = 1) -> float:
        """Nearest grid time; division=2 snaps to half beats, 4 to 16ths."""
        step = self.period / division
        return self.phase + round((t - self.phase) / step) * step

    def bar(self, n: int) -> float:
        """Start of bar n (0-based)."""
        return self.time(n * self.beats_per_bar)

    def beats_between(self, t0: float, t1: float) -> list[float]:
        k0 = int(-(-(t0 - self.phase) // self.period))      # ceil
        out, k = [], k0
        while self.time(k) < t1 - 1e-9:
            out.append(round(self.time(k), 6))
            k += 1
        return out


def fit_grid(beat_times: list[float], beats_per_bar: int = 4) -> BeatGrid:
    """Least-squares line through (beat number, time). Beat numbers come from the median interval, so a missed or
    doubled beat in the tracker does not bend the grid."""
    if len(beat_times) < 4:
        raise ValueError("need at least 4 beats")
    t = sorted(beat_times)
    gaps = sorted(b - a for a, b in zip(t, t[1:]))
    period = gaps[len(gaps) // 2]
    n = len(t)
    phase = t[0]
    # iterate: beat numbers from the current period, then a least-squares line; converges in 2-3 rounds and is not
    # fooled by a noisy median gap or a missed / doubled beat
    for _ in range(4):
        ks = [round((x - t[0]) / period) for x in t]
        mk, mt = sum(ks) / n, sum(t) / n
        var = sum((k - mk) ** 2 for k in ks)
        if var == 0:
            break
        period = sum((k - mk) * (x - mt) for k, x in zip(ks, t)) / var
        phase = mt - period * mk
    # normalise the phase into [0, period)
    phase = phase % period
    return BeatGrid(period=period, phase=phase, beats_per_bar=beats_per_bar)


def snap_cuts(cuts: list[float], grid: BeatGrid, division: int = 1, min_gap_beats: float = 0.5) -> list[float]:
    """Snap every cut to the grid; cuts that land on the same grid point or closer than min_gap_beats are dropped."""
    out: list[float] = []
    for c in sorted(cuts):
        s = grid.nearest(c, division)
        if out and s - out[-1] < min_gap_beats * grid.period - 1e-9:
            continue
        out.append(round(s, 6))
    return out


def cut_on_beat_share(cuts: list[float], grid: BeatGrid, tol: float = 0.06) -> float:
    if not cuts:
        return 0.0
    hit = sum(1 for c in cuts if abs(c - grid.nearest(c)) <= tol)
    return hit / len(cuts)


def section_edges(grid: BeatGrid, wanted: list[float]) -> list[float]:
    """Wanted boundary times (seconds) -> the nearest BAR starts, strictly increasing."""
    out: list[float] = []
    for w in wanted:
        n = max(0, round((w - grid.phase) / (grid.period * grid.beats_per_bar)))
        t = grid.bar(n)
        if out and t <= out[-1] + 1e-9:
            t = out[-1] + grid.period * grid.beats_per_bar
        out.append(round(t, 6))
    return out


def audio_loop_plan(grid: BeatGrid, total: float, intro_beats: int, loop_beats: int) -> list[tuple[float, float]]:
    """Source segments (start, duration) in the track that fill `total` seconds: the intro once (beat 0..intro_beats),
    then the block [intro_beats, intro_beats+loop_beats) repeated, the last one truncated. Every seam is on a beat."""
    segs: list[tuple[float, float]] = []
    intro = intro_beats * grid.period
    take = min(intro, total)
    segs.append((grid.time(0), take))
    got = take
    block = loop_beats * grid.period
    while got < total - 1e-9:
        d = min(block, total - got)
        segs.append((grid.time(intro_beats), d))
        got += d
    return segs


def find_drop(energy_per_beat: list[float], lo: int = 4, window: int = 2) -> int:
    """Beat index of the biggest energy jump (the 'drop'): mean of the next `window` beats minus the previous ones."""
    best, best_i = -1e9, lo
    for i in range(max(lo, window), len(energy_per_beat) - window + 1):
        prev = sum(energy_per_beat[i - window:i]) / window
        nxt = sum(energy_per_beat[i:i + window]) / window
        if nxt - prev > best:
            best, best_i = nxt - prev, i
    return best_i


def comb_fit(env, times, period_hint: float, rel_range: float = 0.04, n_period: int = 161, n_phase: int = 96) -> BeatGrid:
    """Fit a constant-tempo grid to an onset-strength envelope: the (period, phase) whose grid points collect the most
    onset energy. A tracker's own beats are flexible (they follow micro-timing) and give a biased period; a comb over
    the whole envelope does not. Search is limited to period_hint * (1 +- rel_range)."""
    import numpy as np
    env = np.asarray(env, float)
    times = np.asarray(times, float)
    end = float(times[-1])
    best = (-1.0, period_hint, 0.0)
    for p in np.linspace(period_hint * (1 - rel_range), period_hint * (1 + rel_range), n_period):
        for ph in np.linspace(0.0, p, n_phase, endpoint=False):
            pts = np.arange(ph, end, p)
            score = float(np.interp(pts, times, env).mean())
            if score > best[0]:
                best = (score, float(p), float(ph))
    return BeatGrid(period=best[1], phase=best[2])


def detect_beats(media: Path) -> dict:
    """librosa beat tracking on the audio of `media` (video or audio file), then a comb fit for the exact grid."""
    import librosa
    import numpy as np
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "a.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(media), "-vn", "-ac", "1", "-ar", "22050", str(wav)],
                       check=True)
        y, sr = librosa.load(str(wav), sr=22050)
    tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units="time", trim=False)
    tempo = float(np.atleast_1d(tempo)[0])
    beats = [round(float(b), 4) for b in beats]
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=128)
    times = librosa.times_like(env, sr=sr, hop_length=128)
    grid = comb_fit(env, times, 60.0 / tempo)
    rms = librosa.feature.rms(y=y, hop_length=512)[0]
    rt = librosa.times_like(rms, sr=sr, hop_length=512)
    per_beat = []
    k = 0
    while grid.time(k + 1) < len(y) / sr:
        m = (rt >= grid.time(k)) & (rt < grid.time(k + 1))
        per_beat.append(float(rms[m].mean()) if m.any() else 0.0)
        k += 1
    return {"duration": round(len(y) / sr, 3), "tracker_bpm": round(tempo, 2), "beats": beats,
            "grid": {"period": grid.period, "phase": grid.phase, "bpm": round(grid.bpm, 3)},
            "drop_beat": find_drop(per_beat), "rms_per_beat": [round(x, 4) for x in per_beat]}


def main(argv=None) -> int:
    path = Path((argv or sys.argv[1:])[0])
    info = detect_beats(path)
    print(json.dumps({k: v for k, v in info.items() if k != "beats"}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
