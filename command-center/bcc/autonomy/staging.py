"""Staging: run a candidate checkout on a separate free port with a temporary data dir.

``StagingRunner(launcher, probes, owner_data_dir=...).run(sha, checks)``:

* the port is free and never one of the live Bossman ports (8800/8801 by default);
* the data dir is a fresh temp directory that is neither inside nor above the
  owner data dir (the live Bossman is never touched or interrupted);
* named checks (Telegram fake, memory, Jeff role/identity, voice status, model
  routing) run through injected probes; an unknown check or a probe exception
  is a failure, never a silent pass;
* the candidate is ALWAYS stopped and the temp dir ALWAYS removed, and a failed
  teardown fails the report.
"""
from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol

from .journal import Journal, utc_now

STANDARD_CHECKS = ("telegram_fake", "memory", "jeff_identity", "voice_status", "model_routing")
LIVE_PORTS = frozenset({8800, 8801})


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str
    duration_s: float


@dataclass(frozen=True)
class StagingReport:
    sha: str
    passed: bool
    port: int | None
    data_dir: str
    checks: tuple[CheckResult, ...]
    reason: str
    torn_down: bool
    started_at: str
    finished_at: str

    def as_dict(self) -> dict:
        d = asdict(self)
        d["checks"] = {c.name: {"ok": c.ok, "detail": c.detail, "duration_s": c.duration_s} for c in self.checks}
        return d


class Launcher(Protocol):
    def start(self, sha: str, port: int, data_dir: Path, env: Mapping[str, str]) -> Any: ...

    def stop(self, handle: Any) -> None: ...


Probe = Callable[[Any, Mapping[str, Any]], "bool | tuple[bool, str]"]


def free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def _related(a: Path, b: Path) -> bool:
    a, b = a.resolve(), b.resolve()
    return a == b or a in b.parents or b in a.parents


class StagingError(RuntimeError):
    pass


class StagingRunner:
    def __init__(self, launcher: Launcher, probes: Mapping[str, Probe], *, owner_data_dir: str | os.PathLike,
                 live_ports: Iterable[int] = LIVE_PORTS, port_finder: Callable[[], int] = free_port,
                 tmp_root: str | os.PathLike | None = None, journal: Journal | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.launcher = launcher
        self.probes = dict(probes)
        self.owner_data_dir = Path(owner_data_dir)
        self.live_ports = frozenset(int(p) for p in live_ports)
        self._port_finder = port_finder
        self.tmp_root = Path(tmp_root) if tmp_root is not None else None
        self.journal = journal
        self._clock = clock

    def _log(self, kind: str, payload: dict) -> None:
        if self.journal is not None:
            self.journal.append(kind, payload)

    def _pick_port(self) -> int:
        for _ in range(8):
            port = int(self._port_finder())
            if port not in self.live_ports and 1024 <= port <= 65535:
                return port
        raise StagingError("could not find a free port that is not a live Bossman port")

    def _make_data_dir(self, sha: str) -> Path:
        if self.tmp_root is not None and _related(self.tmp_root, self.owner_data_dir):
            raise StagingError("refused: the staging temp root overlaps the owner data dir")
        if self.tmp_root is not None:
            self.tmp_root.mkdir(parents=True, exist_ok=True)
        d = Path(tempfile.mkdtemp(prefix=f"bossman-staging-{sha[:8]}-",
                                  dir=str(self.tmp_root) if self.tmp_root else None))
        if _related(d, self.owner_data_dir):
            shutil.rmtree(d, ignore_errors=True)
            raise StagingError("refused: the staging data dir overlaps the owner data dir")
        return d

    def run(self, sha: str, checks: Iterable[str] = STANDARD_CHECKS) -> StagingReport:
        started = utc_now()
        names = tuple(checks)

        def fail(reason: str, port=None, data_dir="", results=(), torn_down=True) -> StagingReport:
            rep = StagingReport(sha, False, port, data_dir, tuple(results), reason, torn_down, started, utc_now())
            self._log("staging.finished", {"sha": sha, **{k: v for k, v in rep.as_dict().items() if k != "sha"}})
            return rep

        if not re.fullmatch(r"[0-9a-f]{40}([0-9a-f]{24})?", sha or ""):
            return fail("refused: staging needs a full immutable commit SHA")
        if not names:
            return fail("refused: no checks requested")
        unknown = [n for n in names if n not in self.probes]
        if unknown:
            return fail(f"refused: no probe for checks {unknown}")
        try:
            port = self._pick_port()
            data_dir = self._make_data_dir(sha)
        except StagingError as exc:
            return fail(str(exc))
        env = {"BCC_DATA_DIR": str(data_dir), "BCC_PORT": str(port), "BCC_HOST": "127.0.0.1",
               "BOSSMAN_STAGING": "1"}
        self._log("staging.started", {"sha": sha, "port": port, "data_dir": str(data_dir), "checks": list(names)})
        handle = None
        results: list[CheckResult] = []
        reason = ""
        torn_down = True
        try:
            try:
                handle = self.launcher.start(sha, port, data_dir, env)
            except Exception as exc:  # noqa: BLE001
                reason = f"candidate did not start: {type(exc).__name__}: {exc}"
            if handle is not None:
                ctx = {"sha": sha, "port": port, "data_dir": str(data_dir),
                       "base_url": f"http://127.0.0.1:{port}"}
                for name in names:
                    t0 = self._clock()
                    try:
                        out = self.probes[name](handle, ctx)
                        ok, detail = (out if isinstance(out, tuple) else (bool(out), ""))
                    except Exception as exc:  # noqa: BLE001
                        ok, detail = False, f"probe raised {type(exc).__name__}: {exc}"
                    results.append(CheckResult(name, bool(ok), str(detail)[:1000], round(self._clock() - t0, 3)))
        finally:
            if handle is not None:
                try:
                    self.launcher.stop(handle)
                except Exception as exc:  # noqa: BLE001
                    torn_down = False
                    reason = reason or f"teardown failed: {type(exc).__name__}: {exc}"
            shutil.rmtree(data_dir, ignore_errors=True)
            if data_dir.exists():
                torn_down = False
                reason = reason or "teardown failed: temp data dir not removed"
        failed = [r.name for r in results if not r.ok]
        if not reason and failed:
            reason = f"checks failed: {failed}"
        passed = not reason and len(results) == len(names) and torn_down
        rep = StagingReport(sha, passed, port, str(data_dir), tuple(results), reason, torn_down, started, utc_now())
        self._log("staging.finished", {"sha": sha, **{k: v for k, v in rep.as_dict().items() if k != "sha"}})
        return rep


class CheckoutLauncher:
    """Real launcher: ``python -m bcc --host 127.0.0.1 --port N`` from a candidate checkout.

    ``checkout_for(sha)`` returns the path of the candidate worktree (already
    at that SHA). The child gets its own process group and the staging env
    (temp BCC_DATA_DIR); readiness is ``GET /health/live``.
    """

    def __init__(self, checkout_for: Callable[[str], Path], *, python: str = sys.executable,
                 ready_timeout_s: float = 90.0):
        self.checkout_for = checkout_for
        self.python = python
        self.ready_timeout_s = ready_timeout_s

    def start(self, sha: str, port: int, data_dir: Path, env: Mapping[str, str]) -> subprocess.Popen:
        from ..rave.connectors import child_env
        cc = Path(self.checkout_for(sha)) / "command-center"
        kw: dict[str, Any] = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" \
            else {"start_new_session": True}
        proc = subprocess.Popen([self.python, "-m", "bcc", "--host", "127.0.0.1", "--port", str(port)],
                                cwd=str(cc), env={**child_env(), **env, "PYTHONPATH": str(cc)},
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
        deadline = time.monotonic() + self.ready_timeout_s
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise StagingError(f"candidate exited with {proc.returncode}")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health/live", timeout=2) as r:  # noqa: S310
                    if r.status == 200:
                        return proc
            except OSError:
                time.sleep(0.5)
        self.stop(proc)
        raise StagingError("candidate did not become live in time")

    def stop(self, handle: subprocess.Popen) -> None:
        from .lease import kill_process_group
        if handle.poll() is None:
            kill_process_group(handle.pid)
        handle.wait(timeout=30)


__all__ = ["CheckResult", "CheckoutLauncher", "LIVE_PORTS", "STANDARD_CHECKS", "StagingError", "StagingReport",
           "StagingRunner", "free_port"]
