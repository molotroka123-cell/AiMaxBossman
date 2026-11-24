"""Fetch control plane for YouTube learning sources (ideas ported from Stream Tube's backend settings).

Stream Tube survives YouTube changes with (1) a selectable playback client chain (visionOS / Android VR / yt-dlp),
(2) yt-dlp/FFmpeg verified against a published SHA-256 before use, and (3) honest errors for the
"Sign in to confirm you're not a bot" wall. Bossman's old ingest called yt-dlp once and swallowed failures.

This module is the single controller every learning fetch goes through:

* ordered player-client fallback via `--extractor-args youtube:player_client=<client>`;
* stderr classification -> one of OK / NO_SUBS / BOT_CHECK / RATE_LIMITED / UNAVAILABLE / NETWORK / UNKNOWN;
* per-outcome policy: BOT_CHECK/UNKNOWN -> next client; RATE_LIMITED -> stop and open the breaker;
  UNAVAILABLE -> terminal (private/removed/age-gated: no retry); NETWORK -> stop, retry later;
* a persisted circuit breaker so a night batch does not hammer YouTube into an IP ban;
* optional SHA-256 pin of the yt-dlp binary (BOSSMAN_YTDLP_SHA256) -> BLOCKED_BINARY_MISMATCH before any run;
* one receipt per attempt (client, verdict, rc, elapsed) -- no transcript text, no cookies, no account.

No cookies and no sign-in are ever used: a bot wall is reported as BLOCKED, it is not bypassed.
The runner and the clock are injected; tests never touch the network.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CLIENTS = ("default", "android_vr", "tv", "web_safari", "mweb")
SUB_LANGS = "ru.*,en.*,ru,en"

_PATTERNS = (
    ("BOT_CHECK", ("confirm you're not a bot", "confirm you’re not a bot", "sign in to confirm", "--cookies-from-browser")),
    ("RATE_LIMITED", ("http error 429", "too many requests", "rate-limit", "rate limit")),
    ("UNAVAILABLE", ("video unavailable", "private video", "has been removed", "members-only", "sign in to confirm your age",
                     "age-restricted", "this video is not available", "copyright claim")),
    ("NETWORK", ("unable to download webpage", "unable to download api page", "unable to connect to proxy",
                 "tunnel connection failed", "proxyerror", "temporary failure in name resolution", "connection reset",
                 "connection refused", "timed out", "network is unreachable", "getaddrinfo failed", "ssl:")),
)


def classify(rc: int, stderr: str, produced_files: int) -> str:
    """Pure function. rc==0 with files -> OK; rc==0 without files -> NO_SUBS; otherwise by stderr text.
    Order matters: a bot wall that also mentions 'unavailable' is a BOT_CHECK (retry another client)."""
    low = (stderr or "").lower()
    if rc == 0:
        return "OK" if produced_files > 0 else "NO_SUBS"
    if produced_files > 0:  # yt-dlp wrote one language then failed on another (old ingest's known case)
        return "OK"
    for verdict, needles in _PATTERNS:
        if any(n in low for n in needles):
            return verdict
    return "UNKNOWN"


NEXT_CLIENT = {"BOT_CHECK", "UNKNOWN"}
TERMINAL = {"OK", "NO_SUBS", "UNAVAILABLE"}
STOP_AND_TRIP = {"RATE_LIMITED"}
STOP_RETRY_LATER = {"NETWORK"}


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


@dataclass
class Breaker:
    """Persisted circuit breaker. Opens after `threshold` consecutive BOT_CHECK/RATE_LIMITED videos, for `cooldown_s`."""
    path: Path | None = None
    threshold: int = 3
    cooldown_s: float = 1800.0
    clock: object = time.time
    strikes: int = 0
    open_until: float = 0.0

    def __post_init__(self):
        if self.path and Path(self.path).exists():
            try:
                d = json.loads(Path(self.path).read_text(encoding="utf-8"))
                self.strikes, self.open_until = int(d.get("strikes", 0)), float(d.get("open_until", 0.0))
            except (ValueError, OSError):
                self.strikes, self.open_until = 0, 0.0  # corrupt state fails to CLOSED + zero strikes, logged by caller

    def is_open(self) -> bool:
        return self.clock() < self.open_until

    def record(self, verdict: str) -> None:
        if verdict in ("BOT_CHECK", "RATE_LIMITED"):
            self.strikes += 1
            if verdict == "RATE_LIMITED" or self.strikes >= self.threshold:
                self.open_until = self.clock() + self.cooldown_s
        elif verdict in ("OK", "NO_SUBS"):
            self.strikes = 0
        self._save()

    def _save(self) -> None:
        if not self.path:
            return
        p = Path(self.path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps({"strikes": self.strikes, "open_until": self.open_until}), encoding="utf-8")
        os.replace(tmp, p)


def default_runner(argv: list, timeout: float):
    try:
        proc = subprocess.run(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
                              check=False)
        return proc.returncode, proc.stderr[-4000:]
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    except OSError as exc:
        return 127, "runner error: %s" % exc.__class__.__name__


@dataclass
class FetchController:
    ytdlp: str
    clients: tuple = DEFAULT_CLIENTS
    runner: object = default_runner
    breaker: Breaker = field(default_factory=Breaker)
    clock: object = time.monotonic
    expected_sha256: str | None = None
    timeout_s: float = 180.0

    def _verify_binary(self) -> str | None:
        want = (self.expected_sha256 or "").strip().lower()
        if not want:
            return None
        try:
            got = sha256_file(self.ytdlp)
        except OSError:
            return "BLOCKED_BINARY_MISSING"
        return None if got == want else "BLOCKED_BINARY_MISMATCH"

    def subtitles_argv(self, url: str, workdir: Path, client: str) -> list:
        argv = [self.ytdlp, "--no-playlist", "--skip-download", "--write-subs", "--write-auto-subs",
                "--sub-langs", SUB_LANGS, "--sub-format", "vtt", "-o", str(Path(workdir) / "subs.%(id)s.%(language)s.%(ext)s")]
        if client != "default":
            argv += ["--extractor-args", "youtube:player_client=%s" % client]
        return argv + ["--", url]

    def fetch_subtitles(self, url: str, workdir) -> dict:
        """Returns {"verdict", "files", "attempts": [receipts]}. verdict is a status, never an exception."""
        workdir = Path(workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        report = {"verdict": "NOT_RUN", "files": [], "attempts": []}
        blocked = self._verify_binary()
        if blocked:
            report["verdict"] = blocked
            return report
        if self.breaker.is_open():
            report["verdict"] = "BLOCKED_BREAKER_OPEN"
            return report
        verdict = "UNKNOWN"
        for client in self.clients:
            before = set(workdir.glob("subs.*.vtt"))
            t0 = self.clock()
            rc, err = self.runner(self.subtitles_argv(url, workdir, client), self.timeout_s)
            files = sorted(set(workdir.glob("subs.*.vtt")) - before)
            verdict = classify(rc, err, len(files))
            report["attempts"].append({"client": client, "verdict": verdict, "rc": rc,
                                       "elapsed_s": round(self.clock() - t0, 3), "files": len(files)})
            if verdict in TERMINAL:
                report["files"] = [str(f) for f in files]
                break
            if verdict in STOP_AND_TRIP or verdict in STOP_RETRY_LATER:
                break
            # NEXT_CLIENT: try the next player client
        self.breaker.record(verdict)
        report["verdict"] = {"BOT_CHECK": "BLOCKED_BOT_CHECK", "UNKNOWN": "FAILED_ALL_CLIENTS"}.get(verdict, verdict)
        return report
