"""Live progress of a local stable-diffusion.cpp (``sd-cli``) run, read from the engine's own output.

Before this module a Studio job sat at ``progress = 0.05`` (the value the Images worker writes when
it claims a job) for the whole run: nothing read the engine output. ``SdProgress`` parses that
output as it streams and turns it into one overall fraction, a phase, step counters, seconds per
step and an ETA.

What sd-cli prints (real Z-Image Turbo run, Radeon 8060S / Vulkan, 2026-10-13, fixture
``tests/fixtures/sdcpp/zimage_turbo_1024_8steps.log``):

* progress bars are redrawn in place: ``\\r  |======>   | 3/8 - 7.62s/it\\x1b[K`` (carriage return,
  no newline). A *step* bar ends in ``s/it`` or ``it/s``; a *tensor loading* bar ends in a byte rate
  (``MB/s``/``GB/s``) and is not a step, it is ignored;
* phase lines: ``generate_image 1024x1024`` / ``generate_video WxHxF`` (conditioning starts),
  ``get_learned_condition completed`` (text encode done), ``generating image: 1/1`` and
  ``sample(high noise) WxHxF`` (sampling starts; Wan 2.2 A14B splits ``--steps`` across a high
  and a low noise pass, each with its own bar), ``sampling completed`` /
  ``generating ... latent ... completed`` (sampling done), ``decoding N latents`` / ``Wan VAE
  temporal tile i/n`` (VAE decode, the tile counter is real progress), ``decode_first_stage
  completed`` (decode done), ``save result`` / ``images saved`` / ``generate_* completed in``.

Phase weights (fraction of one engine pass). Image weights are MEASURED on the run above:
models/tensors ~9 s, text encode 4.2 s, sampling 68.5 s, VAE decode 40.1 s, save ~0 s of ~122 s.
Video weights are an ESTIMATE (no complete Wan run log was available when this was written):
sampling dominates a video pass, decode of 81 frames is the second largest part. Decode is counted
honestly: while the VAE runs without printing tiles the percentage does not move, it is not
interpolated from a clock.

The overall fraction never exceeds ``ENGINE_CAP`` (0.97): the remainder is fetch, transcode,
segment join and byte verification. Only the Studio runtime sets 1.0, after the output file is
verified and persisted. The fraction is monotonic.
"""
from __future__ import annotations

import re
import time

PHASES = ("load", "encode", "sample", "decode", "save")
WEIGHTS = {
    # measured, direct_zimage.log 2026-10-13: 9 / 4.2 / 68.5 / 40.1 / ~0 s
    "image": {"load": 0.07, "encode": 0.04, "sample": 0.56, "decode": 0.32, "save": 0.01},
    # estimate, NOT measured on this machine (see module docstring)
    "video": {"load": 0.03, "encode": 0.03, "sample": 0.80, "decode": 0.13, "save": 0.01},
}
ENGINE_CAP = 0.97

STEP_RE = re.compile(r"\|\s*(\d+)\s*/\s*(\d+)\s*-\s*([\d.]+)\s*(s/it|it/s)")
TILE_RE = re.compile(r"temporal tile (\d+)\s*/\s*(\d+)")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
MAX_TAIL = 8192

_ENCODE = re.compile(r"\bgenerate_(?:image|video)\s+\d+x\d+")
_ENCODE_DONE = re.compile(r"get_learned_condition completed|encode_first_stage completed")
# "sampling using <method>" is printed BEFORE the text encoder runs (real log): it is a
# configuration line, not the start of sampling, and does not move the phase.
_SAMPLE = re.compile(r"sample\(high noise\) \d+x|generating image: \d+/\d+")
_SAMPLE_PASS_DONE = re.compile(r"sampling\(high noise\) completed")
_SAMPLE_DONE = re.compile(r"(?<!\()sampling completed|generating (?:\d+ )?latent (?:images|video) completed")
_DECODE = re.compile(r"decoding \d+(?:/\d+)? latents|decode_video_outputs|temporal tile \d+")
_DECODE_DONE = re.compile(r"decode_first_stage completed|computing vae decode graph completed|decode_video_outputs (?:decoded|returned)")
_SAVE = re.compile(r"save result|images saved|generate_(?:image|video) completed in")


def parse_step(line: str):
    """``(step, total, seconds_per_step)`` for a sampling/decoding step bar, else None.

    Tensor-loading bars (``46/386 - 417.07MB/s``) are byte transfers, not steps: None.
    """
    m = STEP_RE.search(line)
    if not m:
        return None
    step, total, value, unit = int(m.group(1)), int(m.group(2)), float(m.group(3)), m.group(4)
    if total <= 0 or step < 0 or step > total:
        return None
    s_per_it = value if unit == "s/it" else (1.0 / value if value > 0 else None)
    return step, total, s_per_it


def split_updates(buffer: bytes) -> tuple[list[str], bytes]:
    """Split a raw engine byte stream on ``\\r`` and ``\\n``; return complete lines and the tail."""
    parts = re.split(rb"[\r\n]", buffer)
    tail = parts.pop()
    lines = []
    for raw in parts:
        text = ANSI_RE.sub("", raw.decode("utf-8", "replace")).strip()
        if text:
            lines.append(text)
    return lines, tail[-MAX_TAIL:]


class SdProgress:
    """Progress of one Studio job: ``segments`` engine passes of ``steps`` sampling steps each."""

    def __init__(self, *, steps: int | None, segments: int = 1, surface: str = "image", clock=time.monotonic):
        self.steps = int(steps) if isinstance(steps, int) and not isinstance(steps, bool) and steps > 0 else None
        self.segments = max(1, int(segments or 1))
        self.weights = WEIGHTS["video" if surface == "video" else "image"]
        self.clock = clock
        self._tail = b""
        self.segment = 0                 # 0-based index of the pass now running
        self.segments_done = 0
        self.best = 0.0
        self.known = False               # True once the engine printed a real marker or step
        self.finished = False            # every pass ended with a verified raw output
        self.updated = clock()
        self._reset_pass()

    # ------------------------------------------------------------------ input
    def _reset_pass(self):
        self.phase = "load"
        self.step = 0                    # steps done in this pass, summed over sampling sub-passes
        self.bar_done = 0                # steps of sampling sub-passes that already ended
        self.bar_total = None
        self.s_per_it = None
        self.decode_frac = 0.0
        self.phase_started = self.clock()
        self.sample_s = None             # measured duration of this pass's sampling

    def begin_segment(self, index: int):
        self.segment = max(0, min(int(index), self.segments - 1))
        keep = self.s_per_it, self.sample_s
        self._reset_pass()
        # A later pass runs the same steps at the same size: the previous rate is the best guess.
        self.s_per_it, self.sample_s = keep
        self._tail = b""

    def segment_done(self, index: int):
        self.segments_done = max(self.segments_done, min(int(index) + 1, self.segments))
        if self.segments_done >= self.segments:
            self.finished = True
        self._bump()

    def feed(self, chunk: bytes):
        lines, self._tail = split_updates(self._tail + bytes(chunk))
        for line in lines:
            self.feed_line(line)
        # sd-cli starts every bar redraw with \r, so the newest bar is only terminated by the NEXT
        # redraw. A complete bar (unit printed) in the tail is applied now, not one step late;
        # re-applying it when it is terminated later changes nothing.
        if self._tail and parse_step(ANSI_RE.sub("", self._tail.decode("utf-8", "replace"))):
            self.feed_line(ANSI_RE.sub("", self._tail.decode("utf-8", "replace")).strip())

    def _enter(self, phase: str):
        if PHASES.index(phase) > PHASES.index(self.phase):
            if self.phase == "sample" and self.sample_s is None:
                self.sample_s = self.clock() - self.phase_started
            self.phase = phase
            self.phase_started = self.clock()
        self.known = True

    def feed_line(self, line: str):
        bar = parse_step(line)
        if bar is not None:
            step, total, s_per_it = bar
            self.known = True
            if self.phase in ("load", "encode"):
                self._enter("sample")
            if self.phase == "sample":
                if self.bar_total is not None and total != self.bar_total and step <= 1:
                    self.bar_done += self.bar_total          # a new sampling sub-pass (high -> low noise)
                elif self.bar_total is not None and step < self.step - self.bar_done:
                    self.bar_done += self.bar_total
                self.bar_total = total
                self.step = self.bar_done + step
                if s_per_it:
                    self.s_per_it = s_per_it
            elif self.phase == "decode":
                self.decode_frac = max(self.decode_frac, step / total)
            self._bump()
            return
        tile = TILE_RE.search(line)
        if tile:
            self._enter("decode")
            i, n = int(tile.group(1)), int(tile.group(2))
            if n > 0:
                self.decode_frac = max(self.decode_frac, (i - 1) / n)
        elif _SAVE.search(line):
            self._enter("save")
        elif _DECODE_DONE.search(line):
            self._enter("decode")
            self.decode_frac = 1.0
        elif _DECODE.search(line):
            self._enter("decode")
        elif _SAMPLE_DONE.search(line):
            self._enter("sample")
            self.step = max(self.step, self.total_steps or 0)
            self._enter("decode")
        elif _SAMPLE_PASS_DONE.search(line):
            if self.bar_total is not None:
                self.bar_done += self.bar_total
                self.step = self.bar_done
                self.bar_total = None
        elif _SAMPLE.search(line):
            self._enter("sample")
        elif _ENCODE_DONE.search(line):
            self._enter("encode")
            self._enter("sample")
        elif _ENCODE.search(line):
            self._enter("encode")
        else:
            return
        self._bump()

    # ----------------------------------------------------------------- output
    @property
    def total_steps(self) -> int | None:
        observed = (self.bar_done + self.bar_total) if self.bar_total else (self.bar_done or None)
        if self.steps and observed:
            return max(self.steps, observed)
        return self.steps or observed

    def _pass_fraction(self) -> float:
        w = self.weights
        idx = PHASES.index(self.phase)
        done = sum(w[p] for p in PHASES[:idx])
        if self.phase == "sample":
            total = self.total_steps
            done += w["sample"] * (min(self.step, total) / total if total else 0.0)
        elif self.phase == "decode":
            done += w["decode"] * min(1.0, self.decode_frac)
        return min(1.0, done)

    def fraction(self) -> float:
        if self.finished:
            raw = 1.0
        else:
            raw = (self.segments_done + (self._pass_fraction() if self.segment >= self.segments_done else 0.0)) / self.segments
        return min(ENGINE_CAP, raw * ENGINE_CAP)

    def _bump(self):
        value = self.fraction()
        if value > self.best:
            self.best = value
        self.updated = self.clock()

    def eta_s(self) -> float | None:
        """Seconds left, from the measured seconds per step; None while no rate was measured."""
        if self.finished:
            return 0.0
        total = self.total_steps
        if not self.s_per_it or not total:
            return None
        w = self.weights
        sample_s = self.sample_s if self.sample_s else self.s_per_it * total
        unit = sample_s / w["sample"]                 # seconds per unit of pass weight
        idx = PHASES.index(self.phase)
        if self.phase == "sample":
            left = max(0, total - self.step) * self.s_per_it + (w["decode"] + w["save"]) * unit
        elif self.phase == "decode":
            spent = self.clock() - self.phase_started
            expected = w["decode"] * unit
            if self.decode_frac > 0:
                expected = max(expected, spent / self.decode_frac) if self.decode_frac < 1 else spent
            left = max(0.0, expected - spent) + w["save"] * unit
        elif idx < PHASES.index("sample"):
            left = sum(w[p] for p in PHASES[idx:]) * unit
        else:
            left = w["save"] * unit
        later = max(0, self.segments - self.segment - 1)
        return round(left + later * unit, 1)

    def snapshot(self) -> dict:
        if self.finished:
            phase = "verify"
        else:
            phase = self.phase
        return {
            "progress": round(self.best, 4),
            "progress_detail": {
                "phase": phase, "known": self.known,
                "step": self.step if self.total_steps else None, "steps": self.total_steps,
                "segment": min(self.segment + 1, self.segments), "segments": self.segments,
                "segments_done": self.segments_done,
                "s_per_it": None if self.s_per_it is None else round(self.s_per_it, 2),
                "eta_s": self.eta_s(), "cap": ENGINE_CAP,
                "source": "sd-cli output",
            },
        }


# ------------------------------------------------------------------ job record
RECORD_EVERY_S = 2.0


class JobProgressWriter:
    """Writes a provider's progress snapshot into the Studio job record, throttled.

    ``image_jobs.progress`` gets the overall fraction (only ever raised, and only while the job is
    ``running``); the detail lives in ``image_jobs.options['progress_detail']`` and is returned as
    ``progress_detail`` by ``GET /api/studio/jobs/{id}`` (runtime.get_job). 1.0 is never written
    here: the runtime writes it after the output file is verified and persisted.
    """

    def __init__(self, svc, job: dict, *, every_s: float | None = None, clock=time.monotonic):
        self.svc, self.jid = svc, job["id"]
        self.options = dict(job.get("options") or {})
        self.every_s = RECORD_EVERY_S if every_s is None else every_s
        self.clock = clock
        self.last_at = None
        self.last = None

    async def __call__(self, snapshot: dict | None, *, force: bool = False) -> bool:
        if not snapshot:
            return False
        detail = snapshot.get("progress_detail") or {}
        key = (snapshot.get("progress"), detail.get("phase"), detail.get("step"), detail.get("segment"))
        now = self.clock()
        if key == self.last and not force:
            return False
        if self.last_at is not None and now - self.last_at < self.every_s and not force:
            return False
        import sqlalchemy as sa
        from bcc.db import utcnow
        from bcc.v2.images_tables import image_jobs
        value = min(float(snapshot.get("progress") or 0.0), ENGINE_CAP)
        async with self.svc.db.session() as s:
            # Re-read options in the same session: other keys (e.g. a storyboard recipe) are kept.
            current = (await s.execute(sa.select(image_jobs.c.options).where(image_jobs.c.id == self.jid))).scalar()
            options = {**(current if isinstance(current, dict) else self.options), "progress_detail": detail}
            await s.execute(sa.update(image_jobs).where(image_jobs.c.id == self.jid, image_jobs.c.status == "running")
                            .values(options=options, updated_at=utcnow()))
            await s.execute(sa.update(image_jobs).where(
                image_jobs.c.id == self.jid, image_jobs.c.status == "running",
                sa.or_(image_jobs.c.progress.is_(None), image_jobs.c.progress < value)).values(progress=value))
            await s.commit()
        self.last_at, self.last = now, key
        return True
