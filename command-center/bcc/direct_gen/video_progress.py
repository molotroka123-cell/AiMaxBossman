"""Live progress of a VIDEO generation, read from the engine's own console output.

Owner 20.10: during Wan video runs the pult and the phone said «загрузка модели» for the whole run. The direct-gen
runner only matched sd-cli's ``| 3/8 - 7.62s/it`` step bar; everything else the engine printed (model loading, prompt
encoding, VAE decode, tqdm bars of Wan2GP/diffusers) was dropped, so the job stayed in ``loading`` until a step bar
appeared - for the A14B run of 19.10 that was 41 minutes (timings.jsonl: load_s 2457).

``VideoProgress`` turns the raw byte stream (``\\r``-redrawn bars, partial chunks, ANSI colours, garbage) into the
stage structure photo jobs already use: ``stage`` (load / encode / sample / decode / save / verify), ``step``,
``total``, ``pct`` and ``eta_s`` with ``eta_source`` saying where the number came from.

Understood output (real lines: ``tests/fixtures/video_progress/wan2gp_t2v_1.3b_30steps.log``, a Wan2GP 1.3B run on
this PC 18.10; sd-cli lines as in ``bcc.studio.progress``):

* tqdm / diffusers: ``  37%|███▋      | 11/30 [06:53<09:56, 31.42s/it]`` (also ``it/s`` and ``?it/s``). A bar whose
  rate is a byte rate (``43.4MB/s``: a download) is not a step. A bar whose description names the prompt
  (``Encoding Text Prompt 1/2:``) is the encode stage, not sampling;
* Wan2GP status: ``[11/30] Denoising | 6m 58s / 18m 54s`` (elapsed / the engine's own total estimate),
  ``[30/30] VAE Decoding | ...``, ``[3/12] Loading - ...``, ``[0/1] Encoding Prompt``, ``Model Loaded``;
* ``Step 11/30``;
* sd-cli: ``|=====>   | 3/8 - 7.62s/it`` and the phase markers of ``bcc.studio.progress``.

ETA order (``eta_source``): ``tqdm`` (the bar's own remaining time) -> ``engine_estimate`` (Wan2GP total estimate
minus elapsed) -> ``s_per_it`` (seconds per step x steps left) -> ``timings_median`` (medians of finished jobs of the
same model, size and frames from the direct-gen timing log) -> ``None`` (unknown, said out loud). While sampling,
the first three cover the steps only (``eta_scope: "sampling"``: the VAE decode after them is not included);
the timing medians cover the rest of the job (``eta_scope: "total"``). Nothing is divided by zero.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from statistics import median
from typing import Any

from ..studio import progress as sdp

STAGES = ("load", "encode", "sample", "decode", "save")
WEIGHTS = sdp.WEIGHTS["video"]          # share of one video pass per stage (estimate, see bcc.studio.progress)
CAP = sdp.ENGINE_CAP                    # 100 % only after the output file is verified

ANSI_RE = sdp.ANSI_RE
_PREFIX = re.compile(r"^\[\s*\d+(?:\.\d+)?\]\s*")            # a time-stamp prefix "[  185.0] " of a captured log
_TASK = re.compile(r"^\[Task \d+/\d+\]")                    # Wan2GP echoes the prompt here: never parsed
TQDM_RE = re.compile(
    r"(?P<pct>\d{1,3})%\|[^|]*\|\s*(?P<n>\d+)/(?P<t>\d+)\s*\[(?P<el>[^<\]]*)<(?P<rem>[^,\]]*),\s*(?P<rate>[^\]]*)\]")
_RATE = re.compile(r"^(?P<v>\d+(?:\.\d+)?|\?)\s*(?P<u>s/it|it/s)$")
STATUS_RE = re.compile(r"^\[(?P<n>\d+)/(?P<t>\d+)\]\s+(?P<what>[^|]*?)\s*(?:\|\s*(?P<times>.*))?$")
STEP_WORD_RE = re.compile(r"\bstep\s+(?P<n>\d+)\s*/\s*(?P<t>\d+)\b", re.I)
_DUR = re.compile(r"^\s*(?:(?P<h>\d+)\s*h)?\s*(?:(?P<m>\d+)\s*m(?!s))?\s*(?:(?P<s>\d+(?:\.\d+)?)\s*s)?\s*$")
_LOAD = re.compile(r"^Loading\b|\bLoading Model\b|\bLoading Text Encoder\b|^\s*Model Loaded\b")
_ENCODE = re.compile(r"\bEncoding (?:Text )?Prompt\b|\bPreparing (?:Image and Video )?Conditioning\b")
_DECODE = re.compile(r"\bVAE Decod|\bDecoding\b", re.I)
_SAVE = re.compile(r"\b(?:Saving|Saved)\b.*\b(?:video|file|mp4|output)\b|\bVideo saved\b", re.I)


def parse_duration(text: Any) -> float | None:
    """``"1m 04s"`` -> 64.0, ``"01:43"`` -> 103.0, ``"1:02:03"`` -> 3723.0; None for ``?``/garbage."""
    t = str(text or "").strip()
    if not t or "?" in t:
        return None
    if re.fullmatch(r"\d+(?::\d{1,2}){1,2}", t):
        out = 0.0
        for part in t.split(":"):
            out = out * 60 + int(part)
        return out
    m = _DUR.match(t)
    if not m or not any(m.group(k) for k in ("h", "m", "s")):
        return None
    return int(m.group("h") or 0) * 3600 + int(m.group("m") or 0) * 60 + float(m.group("s") or 0)


@dataclass(frozen=True)
class Event:
    stage: str | None                   # None: a step bar whose stage follows from the context (sd-cli)
    kind: str                           # tqdm | status | sdcli | step | marker
    step: int | None = None
    total: int | None = None
    s_per_it: float | None = None
    remaining_s: float | None = None    # tqdm's own remaining time
    elapsed_s: float | None = None
    total_est_s: float | None = None    # Wan2GP's own estimate of the whole stage
    frac: float | None = None           # decode progress (sd-cli VAE tiles)


def clean(line: str) -> str:
    return _PREFIX.sub("", ANSI_RE.sub("", str(line))).strip()


def _tqdm(line: str) -> Event | None:
    m = TQDM_RE.search(line)
    if not m:
        return None
    rate = _RATE.match(m.group("rate").split(",")[0].strip())     # "1.0it/s, loss=0.1": postfix dropped
    if not rate:
        return None                      # a byte rate (MB/s): a download/copy bar, not a step
    n, t = int(m.group("n")), int(m.group("t"))
    if t <= 0 or n > t:
        return None
    desc = line[:m.start()].strip().rstrip(":").lower()
    if "download" in desc or "fetch" in desc:
        return None
    s_per_it = None
    if rate.group("v") != "?":
        v = float(rate.group("v"))
        s_per_it = v if rate.group("u") == "s/it" else (1.0 / v if v > 0 else None)
    remaining = parse_duration(m.group("rem"))
    elapsed = parse_duration(m.group("el"))
    if "encod" in desc or "prompt" in desc:
        return Event("encode", "tqdm")
    if "decod" in desc or "vae" in desc:
        return Event("decode", "tqdm", frac=n / t)
    return Event("sample", "tqdm", n, t, s_per_it, remaining, elapsed)


def _status(line: str) -> Event | None:
    m = STATUS_RE.match(line)
    if not m:
        return None
    n, t, what = int(m.group("n")), int(m.group("t")), m.group("what").lower()
    if t <= 0 or n > t:
        return None
    elapsed = total = None
    times = (m.group("times") or "").split("/")
    if times and times[0].strip():
        elapsed = parse_duration(times[0])
    if len(times) > 1:
        total = parse_duration(times[1])
    if "denois" in what or "sampl" in what or what.startswith("step"):
        return Event("sample", "status", n, t, elapsed_s=elapsed, total_est_s=total)
    if what.startswith("loading"):                 # before "encod": "[0/3] Loading - ... Text Encoder Model"
        return Event("load", "status")
    if "vae" in what or "decod" in what:
        return Event("decode", "status")
    if "encod" in what or "prompt" in what or "condition" in what:
        return Event("encode", "status")
    if "sav" in what:
        return Event("save", "status")
    return None


def parse_line(raw: str) -> Event | None:
    """One cleaned engine line -> an Event, or None for anything that is not progress (garbage included)."""
    line = clean(raw)
    if not line or _TASK.match(line):
        return None
    for fn in (_tqdm, _status):
        ev = fn(line)
        if ev is not None:
            return ev
    bar = sdp.parse_step(line)                     # sd-cli "| 3/8 - 7.62s/it"; byte-rate bars are None there
    if bar is not None:
        return Event(None, "sdcli", bar[0], bar[1], bar[2])
    tile = sdp.TILE_RE.search(line)
    if tile and int(tile.group(2)) > 0:
        return Event("decode", "marker", frac=(int(tile.group(1)) - 1) / int(tile.group(2)))
    m = STEP_WORD_RE.search(line)
    if m and 0 < int(m.group("t")) and int(m.group("n")) <= int(m.group("t")):
        return Event("sample", "step", int(m.group("n")), int(m.group("t")))
    if sdp._SAVE.search(line) or _SAVE.search(line):
        return Event("save", "marker")
    if sdp._DECODE_DONE.search(line):
        return Event("decode", "marker", frac=1.0)
    if sdp._DECODE.search(line) or _DECODE.search(line):
        return Event("decode", "marker")
    if sdp._SAMPLE_DONE.search(line):
        return Event("decode", "marker")
    if sdp._SAMPLE.search(line) or sdp._ENCODE_DONE.search(line):
        return Event("sample", "marker")
    if sdp._ENCODE.search(line) or _ENCODE.search(line):
        return Event("encode", "marker")
    if _LOAD.search(line):
        return Event("load", "marker")
    return None


# ------------------------------------------------------------------------------------------------- timing medians
def timing_basis(rows: list[dict] | None, model: str, width: Any, height: Any, frames: Any,
                 steps: Any) -> dict[str, Any] | None:
    """Medians of COMPLETED direct-gen jobs of the same model, size and frame count (bcc.direct_gen.timings rows):
    seconds per step, cold load, post-processing, and the total for ``steps``. None when there is no such run."""
    try:
        steps = int(steps)
    except (TypeError, ValueError):
        return None
    if steps <= 0:
        return None
    per_step, loads, posts = [], [], []
    for r in rows or []:
        if (r.get("status") != "completed" or str(r.get("model")) != str(model) or r.get("width") != width
                or r.get("height") != height or r.get("frames") != frames):
            continue
        gen, n = r.get("generate_s"), r.get("steps")
        if not (isinstance(gen, (int, float)) and isinstance(n, int) and n > 0 and gen > 0):
            continue                         # a row without a usable step time says nothing about this job
        per_step.append(float(gen) / n)
        if isinstance(r.get("load_s"), (int, float)) and r.get("load") != "warm":
            loads.append(float(r["load_s"]))
        if isinstance(r.get("post_s"), (int, float)):
            posts.append(float(r["post_s"]))
    if not per_step:
        return None
    out = {"n": len(per_step), "per_step_s": median(per_step), "load_s": median(loads) if loads else 0.0,
           "post_s": median(posts) if posts else 0.0}
    out["total_s"] = out["load_s"] + out["per_step_s"] * steps + out["post_s"]
    return out


# ------------------------------------------------------------------------------------------------------- tracker
class VideoProgress:
    """Progress of one video job, fed with raw engine output chunks (or with step counts from ComfyUI)."""

    def __init__(self, *, steps: Any = None, basis: dict | None = None, clock=time.monotonic):
        self.steps = steps if isinstance(steps, int) and not isinstance(steps, bool) and steps > 0 else None
        self.basis = basis if isinstance(basis, dict) and basis.get("per_step_s") else None
        self.clock = clock
        self.started = clock()
        self.stage = "load"
        self.stage_started = self.started
        self.known = False
        self.finished = False
        self.pass_done = 0                  # steps of sampling sub-passes that already ended (Wan 2.2 high -> low noise)
        self.bar_total: int | None = None
        self.bar_step = 0
        self.step = 0
        self.s_per_it: float | None = None
        self.tqdm: tuple[float, float, int] | None = None     # (remaining_s, at, pass_done) of the newest tqdm bar
        self.engine: tuple[float, float] | None = None        # (remaining_s, at) from Wan2GP "elapsed / total"
        self.decode_frac = 0.0
        self.best = 0.0
        self._tail = b""

    # ------------------------------------------------------------------ input
    def feed(self, chunk: bytes) -> bool:
        """Raw bytes; ``\\r``/``\\n`` split, partial last line kept for the next chunk. True when something changed."""
        before = self._key()
        lines, self._tail = sdp.split_updates(self._tail + bytes(chunk or b""))
        for line in lines:
            self.feed_line(line)
        # tqdm and sd-cli redraw a bar with a leading \r: the newest bar is only terminated by the NEXT redraw.
        # A COMPLETE bar in the tail is applied now (a cut one does not match); re-applying it later changes nothing.
        if self._tail:
            text = clean(self._tail.decode("utf-8", "replace"))
            ev = parse_line(text) if text.endswith("]") or sdp.parse_step(text) else None
            if ev is not None and ev.kind in ("tqdm", "sdcli"):
                self._apply(ev)
        return self._key() != before

    def feed_line(self, line: str) -> None:
        ev = parse_line(line)
        if ev is not None:
            self._apply(ev)

    def observe_step(self, step: Any, total: Any) -> None:
        """A step count from an engine API (ComfyUI ``/progress``), no console line."""
        if isinstance(step, int) and isinstance(total, int) and 0 <= step <= total and total > 0:
            self._apply(Event("sample", "step", step, total))

    def mark_finished(self) -> None:
        """The engine exited with an output file: the file check (``verify``) follows."""
        self.finished = True
        self._bump()

    def _enter(self, stage: str) -> None:
        if STAGES.index(stage) > STAGES.index(self.stage):
            if self.stage == "sample":                 # sampling is over: every step of it is done
                self.step = max(self.step, self.total_steps or 0)
            self.stage, self.stage_started = stage, self.clock()

    def _apply(self, ev: Event) -> None:
        self.known = True
        stage = ev.stage
        if stage is None:                                       # sd-cli step bar: sampling, or VAE decode steps
            stage = "decode" if self.stage in ("decode", "save") else "sample"
            if stage == "decode":
                self.decode_frac = max(self.decode_frac, ev.step / ev.total)
                return self._bump()
        if stage == "sample" and ev.step is not None:
            self._sample(ev)
        elif stage == "decode":
            self._enter("decode")
            if ev.frac is not None:
                self.decode_frac = max(self.decode_frac, min(1.0, ev.frac))
        elif stage == "sample":
            self._enter("sample")
        else:
            self._enter(stage)
        self._bump()

    def _sample(self, ev: Event) -> None:
        if self.stage in ("decode", "save"):
            if ev.step >= ev.total or ev.kind == "status":
                return                       # the final bar redrawn after "VAE Decoding": not a new pass
            if self.bar_total is not None and ev.step < self.bar_total:
                self.pass_done += self.bar_total      # a new window/pass after a decode (long Wan2GP videos)
                self.bar_total, self.bar_step = None, 0
                self.stage, self.stage_started, self.decode_frac = "sample", self.clock(), 0.0
        self._enter("sample")
        if self.bar_total is not None and (
                (ev.total != self.bar_total and ev.step <= 1)
                or (ev.step < self.bar_step and self.bar_step >= self.bar_total)):
            self.pass_done += self.bar_total          # the next sampling sub-pass (high -> low noise)
            self.bar_step = 0
        self.bar_total = ev.total
        self.bar_step = max(self.bar_step, ev.step)  # Wan2GP's status line runs a step ahead of/behind its tqdm bar
        self.step = self.pass_done + self.bar_step
        if ev.s_per_it and ev.s_per_it > 0:
            self.s_per_it = ev.s_per_it
        now = self.clock()
        if ev.kind == "tqdm" and ev.remaining_s is not None:
            self.tqdm = (ev.remaining_s, now, self.pass_done)
        if ev.kind == "status" and ev.total_est_s is not None and ev.elapsed_s is not None:
            self.engine = (max(0.0, ev.total_est_s - ev.elapsed_s), now)

    # ----------------------------------------------------------------- output
    @property
    def total_steps(self) -> int | None:
        observed = self.pass_done + (self.bar_total or 0)
        if self.steps and observed:
            return max(self.steps, observed)
        return self.steps or (observed or None)

    def fraction(self) -> float:
        if self.finished:
            return CAP
        idx = STAGES.index(self.stage)
        done = sum(WEIGHTS[s] for s in STAGES[:idx])
        total = self.total_steps
        if self.stage == "sample" and total:
            done += WEIGHTS["sample"] * min(self.step, total) / total
        elif self.stage == "decode":
            done += WEIGHTS["decode"] * min(1.0, self.decode_frac)
        return min(CAP, done * CAP)

    def _bump(self) -> None:
        self.best = max(self.best, self.fraction())

    def _key(self) -> tuple:
        return (self.stage, self.step, self.total_steps, round(self.decode_frac, 3), self.finished, self.known)

    def eta(self) -> tuple[float | None, str | None, str | None]:
        """(seconds left, source, scope). Unknown is (None, None, None)."""
        if self.finished:
            return 0.0, "done", "total"
        now = self.clock()
        total = self.total_steps
        b = self.basis
        if self.stage == "sample":
            left_steps = max(0, (total or 0) - self.step)
            later = max(0, (total or 0) - self.pass_done - (self.bar_total or 0))   # steps of sub-passes not started
            if self.tqdm is not None and self.tqdm[2] == self.pass_done:     # the bar of THIS sub-pass
                return max(0.0, self.tqdm[0] - (now - self.tqdm[1])) + later * (self.s_per_it or 0.0), "tqdm", "sampling"
            if self.engine is not None:
                return max(0.0, self.engine[0] - (now - self.engine[1])) + later * (self.s_per_it or 0.0), \
                    "engine_estimate", "sampling"
            if self.s_per_it and total:
                return left_steps * self.s_per_it, "s_per_it", "sampling"
            if b and total:
                return left_steps * b["per_step_s"] + b["post_s"], "timings_median", "total"
            return None, None, None
        if self.stage in ("load", "encode") and b:
            left = b["total_s"] - (now - self.started)
            return (left, "timings_median", "total") if left > 1.0 else (None, None, None)
        if self.stage == "decode" and 0 < self.decode_frac < 1:
            spent = now - self.stage_started
            return max(0.0, spent / self.decode_frac - spent), "decode_tiles", "total"
        return None, None, None

    def snapshot(self) -> dict[str, Any]:
        """``progress_detail`` of the job record: the same fields the photo plate reads (phase/step/steps/eta_s)."""
        stage = "verify" if self.finished else self.stage
        total = self.total_steps
        eta, source, scope = self.eta()
        return {
            "stage": stage, "phase": stage, "known": self.known,
            "step": self.step if total else None, "total": total, "steps": total,
            "pct": round(self.best * 100.0, 1), "cap": CAP,
            "eta_s": None if eta is None else round(max(0.0, eta), 1), "eta_source": source, "eta_scope": scope,
            "s_per_it": None if self.s_per_it is None else round(self.s_per_it, 2),
            "basis_n": self.basis["n"] if self.basis and source == "timings_median" else None,
            "source": "engine output",
        }
