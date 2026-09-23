"""Bossman 1.1: the ONE bounded evolution loop over the existing campaign pieces.

    OBSERVE -> SELECT -> ATTEMPT -> VERIFY -> ACCEPT/REJECT -> LEARN -> CHECKPOINT -> NEXT

Reused, not re-implemented: frozen suites and scenario selection (runner.load_suite,
failing train cases first, fewest attempts first), budget reservation BEFORE a
provider call (campaign._reserve/_account), evidence manifests (protocol.seal_evidence),
LearningStore observations (runner.remember: PARTIAL / FAILED_EXPERIMENT), candidate
refs ``evo/candidate-*``, the STOP file. New here: the RESULT_VERIFIER gate, the
product coding path as the ATTEMPT backend, owner controls and crash recovery.

Stable is never written. The campaign keeps its own bare candidate repository
(``candidates.git``, objects shared read-only with the source); ACCEPT only creates
``refs/heads/evo/candidate-<cycle>`` there. Promotion stays with LearningGuard / the
owner. Each accepted candidate becomes the base of the next cycle (the campaign's
"champion"), exactly like campaign.run.

Controls (files in the campaign folder, also exposed by the API and Telegram):
  STOP    stop now: the running attempt is cancelled (its whole process tree dies),
          evidence is kept, the loop exits; ``resume`` clears it.
  PAUSE   do not start new work: the loop exits before the next OBSERVE/ATTEMPT.
  lease   one live loop per campaign folder; a lease whose pid is dead is stale
          and is recovered (recorded), a live one refuses the second loop.
Budgets: per-attempt wall clock (default 40 min -> TIMEOUT, evidence kept, next task),
total active time, $ reservation, retries per task, consecutive failures (-> AUTO
PAUSE), disk (campaign folder), RSS of the loop's process tree, free system RAM.

Crash recovery: the state is checkpointed atomically after every phase; completed
phases are skipped. An ATTEMPT or ACCEPT that was IN_PROGRESS at the crash becomes
UNKNOWN_OUTCOME and is NEVER replayed blindly (its folder stays for inspection); only
``--redo CYCLE_ID`` replays it. Every other phase is idempotent and simply re-runs.

There is no ``while True``: cycles, polls and waits are all bounded.
"""
from __future__ import annotations

import argparse
import contextlib
from dataclasses import asdict, dataclass, field
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import sys
import threading
import time
import uuid

from . import runner as r
from . import verifier as v
from .attempts import (AttemptContext, AttemptResult, BossmanCodingBackend, CommandCenterApi, JsonProposerBackend,
                       MockPatchBackend, ApiError, resolve_token)
from .protocol import seal_evidence, verify_evidence

SCHEMA = "bossman.evolution.loop/1"
PHASES = ("OBSERVE", "SELECT", "ATTEMPT", "VERIFY", "ACCEPT", "LEARN", "CHECKPOINT")
UNREPLAYABLE = ("ATTEMPT", "ACCEPT")
STATE_FILE, REPORT_FILE, LEASE_FILE = "loop-state.json", "loop-report.json", "loop.lease.json"
STOP_FILE, PAUSE_FILE, HOLD_MARKER = "STOP", "PAUSE", "HOLDING"
HOLD_ENV = "BOSSMAN_EVOLUTION_TEST_HOLD_AT"      # test hook: "<cycle index>:<PHASE>"
MAX_CYCLES_CAP = 200
BACKENDS = ("bossman_coding", "mock_patch", "local", "claude")
CLOSED = "CLOSED"


class LeaseHeld(RuntimeError):
    """Another live loop owns this campaign folder."""


class _Halt(Exception):
    def __init__(self, status: str, reason: str = ""):
        super().__init__(reason or status)
        self.status, self.reason = status, reason


@dataclass
class LoopConfig:
    suite: str
    repo: str
    backend: str = "bossman_coding"
    model: str | None = None
    api_url: str = "http://127.0.0.1:8800"
    token_file: str = ""
    data_dir: str = ""
    project_id: str = "bossman-evolution"
    use_memory: bool = True
    add_root: bool = False
    mock_script: str = ""
    local_url: str = "http://127.0.0.1:8080/v1"
    allow_cloud: bool = False
    executor: str = "host"                  # host = the coding path's guard; docker = Aster's executor
    image: str = "bossman-evolution:1.1"
    test_python: str = ""
    python_paths: list = field(default_factory=lambda: ["."])
    test_timeout: int = 600
    max_cycles: int = 3
    attempt_minutes: float = 40.0
    total_hours: float = 8.0
    max_retries_per_task: int = 2
    max_consecutive_failures: int = 3
    max_disk_mb: float = 8192.0
    max_rss_mb: float = 16384.0
    min_free_ram_mb: float = 0.0
    max_usd: float = 2.0
    attempt_usd: float = 0.5
    poll_seconds: float = 5.0
    allow_mock_lessons: bool = False
    publish_recipes: bool = True

    def validate(self) -> None:
        if self.backend not in BACKENDS:
            raise ValueError(f"backend must be one of {BACKENDS}")
        if self.executor not in ("host", "docker"):
            raise ValueError("executor must be host or docker")
        if self.backend in ("local", "claude") and self.executor != "docker":
            # Aster's rule, kept: model-written JSON edits run only in the Docker executor.
            raise ValueError("JSON-proposer edits require --executor docker")
        if self.backend == "claude":
            if not self.allow_cloud or os.environ.get("LOCAL_ONLY", "").lower() in {"1", "true", "yes"}:
                raise ValueError("cloud requires --allow-cloud and LOCAL_ONLY must not be active")
        if self.backend in ("local", "claude") and not self.model:
            raise ValueError("JSON proposers need an explicit --model")
        if self.backend == "mock_patch" and not self.mock_script:
            raise ValueError("mock_patch needs --mock-script")
        numbers = (self.attempt_minutes, self.total_hours, self.max_disk_mb, self.max_rss_mb, self.poll_seconds)
        if any(type(x) not in (int, float) or not x > 0 for x in numbers) or self.min_free_ram_mb < 0 \
                or self.max_usd < 0 or self.attempt_usd < 0:
            raise ValueError("limits must be positive numbers")
        if not 1 <= int(self.max_cycles) <= MAX_CYCLES_CAP or not 1 <= int(self.max_retries_per_task) <= 10 \
                or not 1 <= int(self.max_consecutive_failures) <= 20:
            raise ValueError("cycle / retry / failure limits out of range")


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _write_json(path: Path, value) -> None:
    r.atomic_json(path, value)


def _read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


# ---------------------------------------------------------------- process facts
def _process_identity(pid: int) -> dict:
    try:
        import psutil  # noqa: PLC0415
        return {"pid": pid, "create_time": psutil.Process(pid).create_time()}
    except Exception:  # noqa: BLE001 — psutil optional; pid alone is weaker
        return {"pid": pid, "create_time": None}


def process_alive(identity: dict) -> bool:
    pid = int(identity.get("pid") or 0)
    if pid <= 0:
        return False
    try:
        import psutil  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        if os.name == "nt":
            return True                     # cannot tell: conservative (refuse), --break-lease exists
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    try:
        proc = psutil.Process(pid)
        if proc.status() == psutil.STATUS_ZOMBIE:
            return False
        created = identity.get("create_time")
        return created is None or abs(proc.create_time() - float(created)) < 1.0
    except psutil.Error:
        return False


def tree_rss_mb(pid: int | None = None) -> float | None:
    try:
        import psutil  # noqa: PLC0415
        root = psutil.Process(pid or os.getpid())
        procs = [root] + root.children(recursive=True)
        total = 0
        for p in procs:
            with contextlib.suppress(psutil.Error):
                total += p.memory_info().rss
        return round(total / 1_048_576, 1)
    except Exception:  # noqa: BLE001
        return None


def free_ram_mb() -> float | None:
    try:
        import psutil  # noqa: PLC0415
        return round(psutil.virtual_memory().available / 1_048_576, 1)
    except Exception:  # noqa: BLE001
        return None


def dir_size_mb(folder: Path, limit_files: int = 400_000) -> float:
    total, seen = 0, 0
    for base, dirs, files in os.walk(folder):
        for name in files:
            seen += 1
            if seen > limit_files:
                return round(total / 1_048_576, 1)
            with contextlib.suppress(OSError):
                total += os.lstat(os.path.join(base, name)).st_size
    return round(total / 1_048_576, 1)


# ---------------------------------------------------------------- lease
class Lease:
    def __init__(self, work: Path):
        self.path = work / LEASE_FILE
        self.work = work
        self.token = uuid.uuid4().hex
        self.recovered: dict | None = None

    def acquire(self, *, break_lease: bool = False) -> "Lease":
        with r.campaign_lock(self.work):
            current = _read_json(self.path)
            if current is not None:
                alive = process_alive(current) and current.get("host") == socket.gethostname()
                if alive and not break_lease:
                    raise LeaseHeld(f"campaign is owned by live loop pid {current.get('pid')}")
                stale = self.work / f"loop.lease.stale-{int(time.time())}-{uuid.uuid4().hex[:6]}.json"
                os.replace(self.path, stale)
                self.recovered = {"stale_lease": current, "moved_to": stale.name, "at": now_iso(),
                                  "owner_alive": alive, "forced": bool(break_lease and alive)}
            record = {**_process_identity(os.getpid()), "host": socket.gethostname(), "token": self.token,
                      "acquired_at": now_iso(), "heartbeat_at": now_iso()}
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(record, fh)
        return self

    def heartbeat(self) -> None:
        current = _read_json(self.path) or {}
        if current.get("token") == self.token:
            current["heartbeat_at"] = now_iso()
            with contextlib.suppress(OSError):
                _write_json(self.path, current)

    def release(self) -> None:
        current = _read_json(self.path) or {}
        if current.get("token") == self.token:
            with contextlib.suppress(OSError):
                self.path.unlink()


# ---------------------------------------------------------------- controls
def _control(work: Path, name: str) -> dict | None:
    path = work / name
    if not path.exists():
        return None
    return _read_json(path, {}) or {"requested_at": "unknown"}


def request_stop(work: Path, by: str = "owner", reason: str = "") -> dict:
    work.mkdir(parents=True, exist_ok=True)
    rec = {"by": by, "reason": reason or "owner STOP", "requested_at": now_iso()}
    _write_json(work / STOP_FILE, rec)
    return rec


def request_pause(work: Path, by: str = "owner", reason: str = "") -> dict:
    work.mkdir(parents=True, exist_ok=True)
    rec = {"by": by, "reason": reason or "owner PAUSE", "requested_at": now_iso()}
    _write_json(work / PAUSE_FILE, rec)
    return rec


def clear_controls(work: Path, by: str = "owner") -> dict:
    cleared = []
    for name in (STOP_FILE, PAUSE_FILE):
        with contextlib.suppress(FileNotFoundError):
            (work / name).unlink()
            cleared.append(name)
    return {"cleared": cleared, "by": by, "at": now_iso()}


# ---------------------------------------------------------------- the loop
class EvolutionLoop:
    def __init__(self, work: Path, config: LoopConfig | None = None, *, backend=None, reviewer=None,
                 overrides: dict | None = None):
        self.work = work.resolve()
        self.state_path = self.work / STATE_FILE
        self._backend = backend
        self.reviewer = reviewer
        self.state: dict = _read_json(self.state_path) or {}
        if self.state and self.state.get("schema") != SCHEMA:
            raise ValueError("not an evolution loop campaign folder")
        if self.state:
            stored = dict(self.state["config"])
            stored.update({k: val for k, val in (overrides or {}).items() if val is not None})
            self.config = LoopConfig(**stored)
        elif config is None:
            raise ValueError("new campaign needs --suite and --repo")
        else:
            self.config = config
        self.config.validate()
        self.lease: Lease | None = None
        self._trees: list = []
        self._abort = ""
        self._session_start = time.monotonic()
        self._lock = threading.Lock()

    # ------------------------------------------------------------ plumbing
    @property
    def candidates(self) -> Path:
        return self.work / "candidates.git"

    def students_root(self) -> Path:
        return self.work / "cycles"

    def home(self) -> Path:
        return self.work / ".git-home"

    def save(self, action: str = "") -> None:
        if action:
            self.state["last_action"] = {"at": now_iso(), "text": action[:300]}
            events = self.state.setdefault("events", [])
            events.append(self.state["last_action"])
            del events[:-200]
        self.state["updated_at"] = now_iso()
        budget = self.state.setdefault("budget", {})
        budget["active_seconds"] = round(budget.get("active_seconds_before", 0.0)
                                         + (time.monotonic() - self._session_start), 1)
        _write_json(self.state_path, self.state)
        if self.lease:
            self.lease.heartbeat()

    def suite(self) -> dict:
        return r.load_suite(Path(self.config.repo), Path(self.config.suite))

    def backend(self):
        if self._backend is None:
            cfg = self.config
            if cfg.backend == "mock_patch":
                self._backend = MockPatchBackend(json.loads(Path(cfg.mock_script).read_text(encoding="utf-8")))
            elif cfg.backend == "bossman_coding":
                token = resolve_token("", cfg.token_file, cfg.data_dir)
                self._backend = BossmanCodingBackend(CommandCenterApi(cfg.api_url, token), project_id=cfg.project_id,
                                                     use_memory=cfg.use_memory, model=cfg.model,
                                                     poll_seconds=cfg.poll_seconds, add_root=cfg.add_root)
            elif cfg.backend == "local":
                self._backend = JsonProposerBackend("local", r.LocalProposer(cfg.local_url, cfg.model or ""),
                                                    cfg.model or "", cfg.attempt_usd)
            else:
                self._backend = JsonProposerBackend("claude", r.ClaudeProposer(cfg.model), cfg.model or "",
                                                    cfg.attempt_usd)
        return self._backend

    def local_cost(self) -> bool:
        # Loopback models and the mock cost no $; the product path runs the owner's local sidecar model.
        return self.config.backend in ("mock_patch", "local", "bossman_coding")

    def test_runner(self):
        cfg = self.config
        if cfg.executor == "docker":
            return v.DockerRunner(self.state.get("image_id") or cfg.image, timeout=cfg.test_timeout,
                                  on_process=self._register)
        return v.GuardedHostRunner(python=cfg.test_python or None, python_paths=tuple(cfg.python_paths),
                                   timeout=cfg.test_timeout, on_process=self._register)

    def _register(self, tree) -> None:
        with self._lock:
            self._trees.append(tree)
            if self._abort:
                with contextlib.suppress(Exception):
                    tree.kill()

    def _kill_trees(self) -> None:
        with self._lock:
            trees, self._trees = list(self._trees), []
        for tree in trees:
            with contextlib.suppress(Exception):
                tree.kill()

    # ------------------------------------------------------------ init / recovery
    def init_state(self) -> None:
        cfg = self.config
        repo = Path(cfg.repo).resolve()
        if self.work == repo or repo in self.work.parents:
            raise ValueError("the campaign folder must be outside the source checkout")
        suite = self.suite()
        base = v.git(repo, "rev-parse", "HEAD", home=self.home())[1].strip()
        if v.git(repo, "status", "--porcelain", "--untracked-files=normal", home=self.home())[1].strip():
            raise ValueError("commit or preserve source changes before running evolution")
        if not self.candidates.exists():
            v.git(self.work, "clone", "--quiet", "--bare", "--shared", str(repo), str(self.candidates),
                  home=self.home(), timeout=600)
            v.git(self.candidates, "remote", "remove", "origin", home=self.home(), check=False)
        image_id = ""
        if cfg.executor == "docker":
            code, out = r.command(["docker", "image", "inspect", "--format", "{{.Id}}", cfg.image], repo, timeout=30)
            if code or not out.strip().startswith("sha256:"):
                raise ValueError("prebuilt evaluation image unavailable; build config/evolution/Dockerfile")
            image_id = out.strip()
        suite_rel = ""
        with contextlib.suppress(ValueError):
            suite_rel = Path(cfg.suite).resolve().relative_to(repo).as_posix()
        self.state = {
            "schema": SCHEMA, "campaign_id": uuid.uuid4().hex[:12], "created_at": now_iso(),
            "config": asdict(cfg), "base_sha": base, "champion_sha": base, "suite_fingerprint": suite["fingerprint"],
            "suite_file_in_repo": suite_rel, "image_id": image_id, "status": "NEW", "cycles": [], "current": None,
            "tasks": {c["id"]: {"attempts": 0, "solved": False, "exhausted": False, "last_verdict": None,
                                "candidate_ref": None} for c in suite["cases"] if c["role"] == "train"},
            "consecutive_failures": 0, "reserved_usd": 0.0, "reported_cost_usd": 0.0, "unknown_cost_calls": 0,
            "budget": {"active_seconds_before": 0.0}, "lease_recoveries": [], "baselines": {},
            "invocations": [], "production_promoted": False, "stable_written": False,
            "platform": {"system": platform.system(), "machine": platform.machine(),
                         "processor": platform.processor(), "python": sys.version.split()[0]},
        }
        self.save("campaign created")

    def recover(self) -> list[dict]:
        """Phase-level crash recovery. Returns what was recovered (also in the state)."""
        notes = []
        cycle = self.open_cycle()
        if cycle is None:
            return notes
        for phase in PHASES:
            ph = cycle["phases"][phase]
            if ph["status"] != "IN_PROGRESS":
                continue
            if phase in UNREPLAYABLE:
                info = {"cycle": cycle["id"], "phase": phase, "at": now_iso(), "outcome": "UNKNOWN_OUTCOME"}
                if phase == "ATTEMPT":
                    info.update(self._inspect_inflight_attempt(cycle))
                else:
                    info.update(self._inspect_inflight_accept(cycle))
                ph.update(status="UNKNOWN_OUTCOME", recovered=info)
                cycle["outcome"] = "UNKNOWN_OUTCOME"
                cycle["unknown"] = info
                for later in ("VERIFY", "ACCEPT"):
                    if PHASES.index(later) > PHASES.index(phase) and cycle["phases"][later]["status"] == "PENDING":
                        cycle["phases"][later]["status"] = "SKIPPED"
                notes.append(info)
            else:
                ph["status"] = "PENDING"
                ph["resumed_after_crash"] = ph.get("resumed_after_crash", 0) + 1
                notes.append({"cycle": cycle["id"], "phase": phase, "at": now_iso(), "outcome": "RESUMED"})
        if notes:
            self.state.setdefault("recoveries", []).extend(notes)
            self.save("crash recovery: " + ", ".join(f"{n['phase']}={n['outcome']}" for n in notes))
        return notes

    def _inspect_inflight_attempt(self, cycle: dict) -> dict:
        attempt = cycle.get("attempt") or {}
        out = {"kept_for_inspection": str(self.cycle_dir(cycle) / "attempt"), "replayed": False}
        task_id = (attempt.get("submitted") or {}).get("coding_task_id")
        if task_id and self.config.backend == "bossman_coding":
            with contextlib.suppress(Exception):
                out["server_task"] = {"id": task_id, **self.backend().cancel(str(task_id))}
        return out

    def _inspect_inflight_accept(self, cycle: dict) -> dict:
        ref = f"refs/heads/evo/candidate-{cycle['id']}"
        code, out, _err = v.git(self.candidates, "rev-parse", "--verify", "--quiet", ref, home=self.home(), check=False)
        return {"candidate_ref": ref, "ref_exists": code == 0, "ref_target": out.strip() if code == 0 else None,
                "champion_advanced": False}

    def redo(self, cycle_id: str) -> dict:
        """Explicit owner decision: replay an UNKNOWN_OUTCOME attempt/accept."""
        cycle = next((c for c in self.state["cycles"] if c["id"] == cycle_id), None)
        if cycle is None or cycle.get("outcome") != "UNKNOWN_OUTCOME":
            raise ValueError("--redo applies only to a cycle whose outcome is UNKNOWN_OUTCOME")
        phase = (cycle.get("unknown") or {}).get("phase", "ATTEMPT")
        folder = self.cycle_dir(cycle)
        keep = folder / f"unknown-{int(time.time())}"
        if phase == "ATTEMPT":
            for name in ("attempt", "verify"):
                if (folder / name).exists():
                    keep.mkdir(parents=True, exist_ok=True)
                    (folder / name).rename(keep / name)       # the unknown attempt stays for inspection
        start = PHASES.index(phase)
        for later in PHASES[start:]:
            cycle["phases"][later] = {"status": "PENDING"}
        cycle.pop("outcome", None)
        cycle["redo"] = {"phase": phase, "at": now_iso(), "previous_kept": keep.name}
        cycle["status"] = "OPEN"
        if phase == "ATTEMPT":
            cycle.pop("attempt", None)
            cycle.pop("verdict", None)
        self.state["current"] = cycle["id"]
        self.save(f"owner --redo {cycle_id} from {phase}")
        return cycle["redo"]

    # ------------------------------------------------------------ cycles
    def open_cycle(self) -> dict | None:
        cid = self.state.get("current")
        return next((c for c in self.state.get("cycles", []) if c["id"] == cid and c.get("status") != CLOSED), None)

    def closed_cycles(self) -> list[dict]:
        return [c for c in self.state.get("cycles", []) if c.get("status") == CLOSED and c.get("task_id")]

    def cycle_dir(self, cycle: dict) -> Path:
        return self.students_root() / cycle["id"]

    def new_cycle(self) -> dict:
        index = len(self.closed_cycles()) + 1
        cycle = {"id": f"c{index:03d}-{uuid.uuid4().hex[:8]}", "index": index, "status": "OPEN",
                 "started_at": now_iso(), "base_sha": self.state["champion_sha"],
                 "phases": {p: {"status": "PENDING"} for p in PHASES}}
        self.state["cycles"].append(cycle)
        self.state["current"] = cycle["id"]
        self.cycle_dir(cycle).mkdir(parents=True, exist_ok=True)
        self.save(f"cycle {index} opened")
        return cycle

    # ------------------------------------------------------------ controls
    def stop_requested(self) -> bool:
        return (self.work / STOP_FILE).exists()

    def pause_requested(self) -> bool:
        return (self.work / PAUSE_FILE).exists()

    def check_budgets(self) -> None:
        cfg = self.config
        active = self.state["budget"].get("active_seconds_before", 0.0) + (time.monotonic() - self._session_start)
        if active >= cfg.total_hours * 3600:
            raise _Halt("BUDGET_EXHAUSTED", f"total active time {active / 3600:.2f} h reached")
        if not self.local_cost() and self.state["reserved_usd"] + cfg.attempt_usd > cfg.max_usd + 1e-9:
            raise _Halt("BUDGET_EXHAUSTED", "model $ budget reserved in full")

    def check_disk(self) -> dict:
        size = dir_size_mb(self.work)
        info = {"campaign_mb": size, "limit_mb": self.config.max_disk_mb}
        if size > self.config.max_disk_mb:
            self.prune()
            info["after_prune_mb"] = size = dir_size_mb(self.work)
            if size > self.config.max_disk_mb:
                request_pause(self.work, "loop", f"disk budget: campaign folder {size} MB > {self.config.max_disk_mb} MB")
                raise _Halt("AUTO_PAUSED", "DISK_BUDGET")
        return info

    def prune(self) -> None:
        """Checkouts of closed cycles go; evidence JSON/diffs and UNKNOWN_OUTCOME folders stay."""
        for cycle in self.closed_cycles():
            if cycle.get("outcome") == "UNKNOWN_OUTCOME":
                continue
            for sub in ("attempt/student-repo", "verify/checkouts"):
                v.rmtree(self.cycle_dir(cycle) / sub)

    def watchdog(self, deadline: float, stop_event: threading.Event) -> threading.Thread:
        cfg = self.config

        def run() -> None:
            ticks = int(max(1.0, deadline - time.monotonic() + 120) / 0.5) + 10
            for _ in range(ticks):                           # bounded: the attempt deadline + grace
                if stop_event.wait(0.5):
                    return
                why = ""
                if self.stop_requested():
                    why = "STOP"
                elif time.monotonic() >= deadline:
                    why = "TIMEOUT"
                else:
                    rss = tree_rss_mb()
                    free = free_ram_mb()
                    if rss is not None and rss > cfg.max_rss_mb:
                        why = "MEMORY_BUDGET"
                    elif cfg.min_free_ram_mb and free is not None and free < cfg.min_free_ram_mb:
                        why = "LOW_SYSTEM_RAM"
                if why:
                    with self._lock:
                        self._abort = self._abort or why
                    self._kill_trees()
                    return

        thread = threading.Thread(target=run, name="evolution-watchdog", daemon=True)
        thread.start()
        return thread

    def _hold(self, cycle: dict, phase: str) -> None:
        """Test hook (deterministic kill point for the gate); inert unless the env names this point."""
        if os.environ.get(HOLD_ENV, "") != f"{cycle['index']}:{phase}":
            return
        _write_json(self.work / HOLD_MARKER, {"pid": os.getpid(), "cycle": cycle["id"], "phase": phase,
                                              "at": now_iso()})
        for _ in range(600):                                 # at most 120 s, then continue normally
            if self.stop_requested():
                return
            time.sleep(0.2)

    # ------------------------------------------------------------ run
    def run(self, *, break_lease: bool = False, redo: str | None = None) -> dict:
        self.work.mkdir(parents=True, exist_ok=True)
        self.lease = Lease(self.work).acquire(break_lease=break_lease)
        try:
            if not self.state:
                self.init_state()
            if self.lease.recovered:
                self.state["lease_recoveries"].append(self.lease.recovered)
            self.state["budget"]["active_seconds_before"] = self.state["budget"].get("active_seconds", 0.0)
            self._session_start = time.monotonic()
            self.state["invocations"].append({"pid": os.getpid(), "started_at": now_iso(),
                                              "lease_recovered": bool(self.lease.recovered)})
            self.recover()
            if redo:
                self.redo(redo)
            if self.state.get("status") == "AUTO_PAUSED" and not self.pause_requested():
                # The owner cleared the automatic PAUSE: that acknowledges the failure streak.
                self.state.setdefault("acknowledged_streaks", []).append(
                    {"consecutive_failures": self.state["consecutive_failures"], "at": now_iso()})
                self.state["consecutive_failures"] = 0
            self.state["status"] = "RUNNING"
            self.save("loop started" + (" after a stale lease" if self.lease.recovered else ""))
            try:
                self.prepare_backend()
                self._cycles()
            except _Halt as halt:
                self.state["status"] = halt.status
                self.state["halt_reason"] = halt.reason
                self.save(f"halt: {halt.status} {halt.reason}".strip())
            except (ApiError, ValueError, OSError, RuntimeError, v.VerifierError) as exc:
                self.state["status"] = "BLOCKED"
                self.state["halt_reason"] = f"{type(exc).__name__}: {v.redact_text(str(exc))[:500]}"
                self.save("blocked: " + self.state["halt_reason"])
            return self.state
        finally:
            with contextlib.suppress(Exception):
                self.state["invocations"][-1]["ended_at"] = now_iso()
                self.save()
                _write_json(self.work / REPORT_FILE, report(self.work, self.state))
            self.lease.release()

    def prepare_backend(self) -> None:
        info = self.backend().prepare(self)
        self.state["backend_info"] = info
        self.save(f"backend {self.config.backend} ready: model {info.get('model')} ({info.get('model_kind')})")

    def _cycles(self) -> None:
        for _ in range(MAX_CYCLES_CAP + 1):                   # bounded; exits via max_cycles below
            if self.open_cycle() is None and len(self.closed_cycles()) >= self.config.max_cycles:
                self.state["status"] = "COMPLETED"
                self.save(f"max cycles reached ({self.config.max_cycles})")
                return
            if self.stop_requested():
                raise _Halt("STOPPED", "STOP file")
            cycle = self.open_cycle()
            if cycle is None:
                if self.pause_requested():
                    raise _Halt("PAUSED", (_control(self.work, PAUSE_FILE) or {}).get("reason", ""))
                self.check_budgets()
                cycle = self.new_cycle()
            self.run_cycle(cycle)
            if self.state["consecutive_failures"] >= self.config.max_consecutive_failures:
                request_pause(self.work, "loop", f"{self.state['consecutive_failures']} consecutive failed cycles")
                raise _Halt("AUTO_PAUSED", "MAX_CONSECUTIVE_FAILURES")

    def run_cycle(self, cycle: dict) -> None:
        for phase in PHASES:
            ph = cycle["phases"][phase]
            if ph["status"] in ("DONE", "SKIPPED", "UNKNOWN_OUTCOME"):
                continue
            if self.stop_requested():
                raise _Halt("STOPPED", f"STOP before {phase}")
            if phase in ("OBSERVE", "ATTEMPT") and self.pause_requested():
                raise _Halt("PAUSED", f"PAUSE before {phase}")
            ph.update(status="IN_PROGRESS", started_at=now_iso(), runs=ph.get("runs", 0) + 1)
            cycle["phase"] = phase
            self.save(f"cycle {cycle['index']}: {phase}")
            self._hold(cycle, phase)
            try:
                getattr(self, "phase_" + phase.lower())(cycle)
            except _Halt:
                if ph["status"] == "IN_PROGRESS":          # halted before its effect: simply redo it later
                    ph["status"] = "PENDING"
                raise
            if ph["status"] == "IN_PROGRESS":
                ph["status"] = "DONE"
            ph["finished_at"] = now_iso()
            self.save(f"cycle {cycle['index']}: {phase} {ph['status'].lower()}")
            if cycle.get("status") == CLOSED:
                return

    # ------------------------------------------------------------ phases
    def phase_observe(self, cycle: dict) -> None:
        folder = self.cycle_dir(cycle)
        disk = self.check_disk()
        base = cycle["base_sha"]
        baseline = self.state["baselines"].get(base)
        if baseline is None:
            baseline = self.measure_baseline(base, folder / "observe")
            self.state["baselines"][base] = baseline
        stable_head = v.git(Path(self.config.repo), "rev-parse", "HEAD", home=self.home(), check=False)[1].strip()
        obs = {"base_sha": base, "baseline": baseline, "disk": disk, "loop_rss_mb": tree_rss_mb(),
               "free_ram_mb": free_ram_mb(), "stable_head": stable_head, "at": now_iso()}
        cycle["observe"] = {k: obs[k] for k in ("disk", "loop_rss_mb", "free_ram_mb", "stable_head")}
        _write_json(folder / "observe.json", obs)

    def measure_baseline(self, sha: str, folder: Path) -> dict:
        suite = self.suite()
        checkout = folder / "checkout"
        v.clean_checkout(self.candidates, sha, checkout, home=self.home())
        out = {}
        runner = self.test_runner()
        try:
            for case in suite["cases"]:
                res = runner.run(checkout, list(case["tests"]), folder / "runs" / case["id"])
                out[case["id"]] = {"status": res["status"], "reason": res.get("reason"),
                                   "failing": sorted(k for k, s in res["tests"].items() if s != "PASS")[:20]}
        finally:
            v.rmtree(checkout)
        return out

    def phase_select(self, cycle: dict) -> None:
        suite = self.suite()
        baseline = self.state["baselines"][cycle["base_sha"]]
        cfg = self.config
        order = {c["id"]: i for i, c in enumerate(suite["cases"])}
        pool = []
        for case in suite["cases"]:
            t = self.state["tasks"].get(case["id"])
            if case["role"] != "train" or not case.get("editable") or t is None:
                continue
            if t["solved"] or t["exhausted"] or baseline.get(case["id"], {}).get("status") != "FAIL":
                continue
            if t["attempts"] >= cfg.max_retries_per_task:
                t["exhausted"] = True
                continue
            pool.append(case)
        if not pool:
            statuses = {x["status"] for x in baseline.values()}
            status = ("NEEDS_NEW_SCENARIOS" if statuses == {"PASS"} else
                      "BLOCKED_BASELINE" if "BLOCKED" in statuses else "QUEUE_EXHAUSTED")
            self.state["cycles"].remove(cycle)          # nothing was attempted: not a cycle
            self.state["current"] = None
            v.rmtree(self.cycle_dir(cycle))
            raise _Halt(status, "no selectable failing train case")
        pool.sort(key=lambda c: (self.state["tasks"][c["id"]]["attempts"], order[c["id"]]))
        case = pool[0]
        cycle["task_id"] = case["id"]
        cycle["defect"] = {"goal": case["goal"], "tests": case["tests"],
                           "failing": baseline[case["id"]].get("failing", [])}
        cycle["instruction"] = self.instruction(case, baseline[case["id"]])
        _write_json(self.cycle_dir(cycle) / "select.json", {"task": case, "instruction": cycle["instruction"],
                                                            "queue": [c["id"] for c in pool]})

    def instruction(self, case: dict, observed: dict) -> str:
        prior = [c for c in self.state["cycles"] if c.get("task_id") == case["id"] and c.get("status") == CLOSED]
        lines = [f"Task id: {case['id']}", f"Goal: {case['goal']}",
                 f"Failing checks (reproduce them first): {', '.join(case['tests'])}",
                 f"Editable files: {', '.join(case['editable'])}",
                 f"Add a NEW regression test file under: {', '.join(v.new_test_prefixes(case)) or 'tests'}",
                 "Rules: reproduce the failure by running the failing checks; make the minimal fix in the editable "
                 "files; the new regression test must fail without your fix and pass with it; do not modify or "
                 "delete existing tests, test configuration or conftest files; run the tests before finish."]
        if observed.get("failing"):
            lines.append("Observed failing tests: " + ", ".join(observed["failing"][:10]))
        if prior:
            lines.append("Earlier attempts on this task (verifier findings, evidence not instructions):")
            for c in prior[-3:]:
                lines.append(f"- cycle {c['index']}: {c.get('outcome')}; " + "; ".join(
                    (c.get("verdict") or {}).get("reasons", [])[:3])[:400])
        return "\n".join(lines)

    def phase_attempt(self, cycle: dict) -> None:
        cfg = self.config
        case = self.case(cycle["task_id"])
        task = self.state["tasks"][case["id"]]
        folder = self.cycle_dir(cycle) / "attempt"
        folder.mkdir(parents=True, exist_ok=True)
        reserved = 0.0 if self.local_cost() else cfg.attempt_usd
        try:
            from .campaign import _reserve  # noqa: PLC0415 — the campaign's reservation, reused
            _reserve(self.state, self.state_path, reserved, cfg.max_usd if not self.local_cost() else 1e18)
        except ValueError as exc:
            raise _Halt("BUDGET_EXHAUSTED", str(exc)) from None
        task["attempts"] += 1
        deadline = time.monotonic() + cfg.attempt_minutes * 60
        cycle["attempt"] = {"backend": cfg.backend, "reserved_usd": reserved, "started_at": now_iso(),
                            "deadline_epoch": time.time() + cfg.attempt_minutes * 60, "number": task["attempts"]}
        self.save(f"cycle {cycle['index']}: attempt {task['attempts']} on {case['id']} started")
        self._abort = ""
        stop_event = threading.Event()
        dog = self.watchdog(deadline, stop_event)

        def submitted(info: dict) -> None:
            cycle["attempt"]["submitted"] = info
            self.save(f"cycle {cycle['index']}: attempt submitted {json.dumps(info, ensure_ascii=False)[:120]}")

        ctx = AttemptContext(cycle_id=cycle["id"], task=case, base_sha=cycle["base_sha"], source=self.candidates,
                             folder=folder, deadline=deadline, instruction=cycle["instruction"],
                             observation={"baseline": self.state["baselines"][cycle["base_sha"]],
                                          "attempt_number": task["attempts"]},
                             should_abort=lambda: self._abort or ("STOP" if self.stop_requested() else ""),
                             on_submitted=submitted, register_tree=self._register)
        self._hold(cycle, "ATTEMPT_INFLIGHT")
        try:
            result = self.backend().attempt(ctx)
        except (ApiError, v.VerifierError, OSError, ValueError) as exc:
            result = AttemptResult("BLOCKED", reason=f"{type(exc).__name__}: {v.redact_text(str(exc))[:400]}")
        finally:
            stop_event.set()
            dog.join(timeout=5)
        if self._abort and result.status not in ("TIMEOUT", "STOPPED"):
            result.status = "STOPPED" if self._abort == "STOP" else "TIMEOUT"
            result.reason = self._abort
        if reserved:
            from .campaign import _account  # noqa: PLC0415 — the campaign's accounting, reused
            try:
                _account(self.state, result.cost_usd, reserved)
            except ValueError as exc:
                cycle["attempt"]["cost_error"] = str(exc)
        (folder / "attempt.diff").write_text(result.diff, encoding="utf-8", newline="\n")
        (folder / "student-claim.txt").write_text("UNTRUSTED student claim (never a verifier input):\n"
                                                  + result.student_claim, encoding="utf-8")
        record = {k: val for k, val in asdict(result).items() if k not in ("diff", "student_claim")}
        record["diff_sha256"] = hashlib.sha256(result.diff.encode("utf-8")).hexdigest()
        _write_json(folder / "attempt.json", record)
        cycle["attempt"].update(status=result.status, model=result.model, model_kind=result.model_kind,
                                reason=result.reason, changed_files=result.changed_files,
                                diff_lines=sum(1 for line in result.diff.splitlines()
                                               if line[:1] in "+-" and not line.startswith(("+++", "---"))),
                                memory=(result.detail or {}).get("memory"),
                                sidecar=(result.detail or {}).get("sidecar"), finished_at=now_iso(),
                                coding_task_id=(result.detail or {}).get("coding_task_id"))
        if result.status in ("TIMEOUT", "STOPPED", "BLOCKED") or not result.diff.strip():
            cycle["outcome"] = result.status if result.status in ("TIMEOUT", "STOPPED", "BLOCKED") else "NO_DIFF"
            for later in ("VERIFY", "ACCEPT"):
                cycle["phases"][later]["status"] = "SKIPPED"

    def case(self, task_id: str) -> dict:
        return next(c for c in self.suite()["cases"] if c["id"] == task_id)

    def phase_verify(self, cycle: dict) -> None:
        case = self.case(cycle["task_id"])
        folder = self.cycle_dir(cycle)
        diff = (folder / "attempt" / "attempt.diff").read_text(encoding="utf-8")
        record = _read_json(folder / "attempt" / "attempt.json", {})
        suite = self.suite()
        tests = list(dict.fromkeys(list(case["tests"]) + [t for c in suite["cases"] if c["role"] == "regression"
                                                          for t in c["tests"]]))
        protected = tuple(p for p in (self.state.get("suite_file_in_repo"),) if p)
        out = folder / "verify"
        if out.exists():
            v.rmtree(out)                                   # a re-run after a crash starts clean
        self._abort = ""
        stop_event = threading.Event()
        # The verifier bounds each test run itself; this watchdog answers STOP and memory.
        dog = self.watchdog(time.monotonic() + 4 * self.config.test_timeout + 300, stop_event)
        try:
            verdict = v.verify(task=case, source=self.candidates, base_sha=cycle["base_sha"], diff=diff, tests=tests,
                               evidence=record, out=out, runner=self.test_runner(), holdout=protected,
                               require_new_regression=self.config.backend in ("bossman_coding", "mock_patch"))
        finally:
            stop_event.set()
            dog.join(timeout=5)
        if self._abort == "STOP" or self.stop_requested():
            cycle["phases"]["VERIFY"]["status"] = "PENDING"      # interrupted by the owner: re-run on resume
            raise _Halt("STOPPED", "STOP during VERIFY")
        cycle["verdict"] = {k: verdict.get(k) for k in ("verdict", "reasons", "model_kind", "counts_as_student_success",
                                                        "patch_sha256", "candidate_tree")}
        cycle["verdict"]["new_regression_tests"] = verdict.get("checks", {}).get("new_regression_tests")

    def phase_accept(self, cycle: dict) -> None:
        verdict = cycle.get("verdict") or {}
        folder = self.cycle_dir(cycle)
        decision = {"at": now_iso(), "verdict": verdict.get("verdict")}
        if verdict.get("verdict") == "PASS" and self.reviewer is not None:
            decision["review"] = self.review(cycle)
        if verdict.get("verdict") == "PASS" and decision.get("review", {}).get("status", "NOT_CONFIGURED") in (
                "ACCEPTED", "NOT_CONFIGURED"):
            decision.update(self.create_candidate(cycle, verdict))
            decision["decision"] = "ACCEPT"
            cycle["outcome"] = "ACCEPTED"
            if cycle["base_sha"] == self.state["champion_sha"]:
                self.state["champion_sha"] = decision["candidate_sha"]
                decision["champion_advanced"] = True
            else:                                          # e.g. a --redo of an older cycle
                decision["champion_advanced"] = False
                decision["champion_note"] = "candidate is based on an older champion; left for the owner"
            task = self.state["tasks"][cycle["task_id"]]
            task.update(solved=True, candidate_ref=decision["ref"])
        else:
            decision["decision"] = "REJECT"
            decision["discarded"] = "no candidate ref was created; the patch stays only as evidence"
            cycle["outcome"] = "REJECTED_" + str(verdict.get("verdict") or "NO_VERDICT")
        cycle["decision"] = decision
        _write_json(folder / "accept.json", decision)

    def review(self, cycle: dict) -> dict:
        """Aster's model reviewer, bound to nonce / base SHA / patch hash (optional)."""
        from .protocol import review_input, review_prompt, validate_review  # noqa: PLC0415
        folder = self.cycle_dir(cycle)
        diff = (folder / "attempt" / "attempt.diff").read_text(encoding="utf-8")
        case = self.case(cycle["task_id"])
        checkout = folder / "review-checkout"
        try:
            v.clean_checkout(self.candidates, cycle["base_sha"], checkout, home=self.home())
            v.git(checkout, "apply", "--index", "-", home=self.home(), input_text=diff if diff.endswith("\n") else diff + "\n")
            changed = [n for n in v.git(checkout, "diff", "--cached", "--name-only", home=self.home())[1].splitlines() if n]
            sources = {p: (checkout / p).read_text(encoding="utf-8") for p in changed if (checkout / p).is_file()}
            context = review_input(cycle["base_sha"], diff, sources, case["goal"])
            response, cost = self.reviewer(v.redact_text(review_prompt(context)), folder, 0.25,
                                           int(self.config.attempt_minutes * 60))
            _write_json(folder / "independent-review.json", {"response": response, "reported_cost_usd": cost})
            validate_review(response, context)
            return {"status": "ACCEPTED"}
        except (ValueError, RuntimeError, OSError, KeyError, TypeError, v.VerifierError) as exc:
            return {"status": "BLOCKED", "reason": v.redact_text(str(exc))[:300]}
        finally:
            v.rmtree(checkout)

    def create_candidate(self, cycle: dict, verdict: dict) -> dict:
        """Plumbing inside candidates.git only: read-tree + apply --cached + commit-tree + create-only ref."""
        folder = self.cycle_dir(cycle)
        diff = (folder / "attempt" / "attempt.diff").read_text(encoding="utf-8")
        if not diff.endswith("\n"):
            diff += "\n"
        index = folder / "accept.index"
        env = {"GIT_INDEX_FILE": str(index)}
        home, git_dir = self.home(), self.candidates
        index.unlink(missing_ok=True)
        try:
            v.git(git_dir, "read-tree", cycle["base_sha"], home=home, extra_env=env)
            v.git(git_dir, "apply", "--cached", "--whitespace=nowarn", "-", home=home, extra_env=env, input_text=diff)
            tree = v.git(git_dir, "write-tree", home=home, extra_env=env)[1].strip()
        finally:
            index.unlink(missing_ok=True)
        if verdict.get("candidate_tree") and tree != verdict["candidate_tree"]:
            raise v.VerifierError("candidate tree differs from the verified tree")
        message = (f"evo: {cycle['task_id']} candidate (cycle {cycle['index']}, verdict PASS, "
                   f"model {cycle['attempt'].get('model')} {cycle['attempt'].get('model_kind')})\n\n"
                   f"patch sha256 {verdict.get('patch_sha256')}; not promoted; LearningGuard/owner decides.")
        ref = f"refs/heads/evo/candidate-{cycle['id']}"
        code, existing, _e = v.git(git_dir, "rev-parse", "--verify", "--quiet", ref, home=home, check=False)
        if code == 0:
            # Only reachable through an explicit --redo of an UNKNOWN_OUTCOME accept.
            have = v.git(git_dir, "rev-parse", existing.strip() + "^{tree}", home=home)[1].strip()
            if have != tree:
                raise v.VerifierError(f"{ref} exists with a different tree; left for the owner")
            return {"candidate_sha": existing.strip(), "ref": ref, "tree": tree, "repository": str(git_dir),
                    "reused_existing_ref": True}
        commit = v.git(git_dir, "commit-tree", tree, "-p", cycle["base_sha"], "-m", message, home=home)[1].strip()
        self._hold(cycle, "ACCEPT_INFLIGHT")
        v.git(git_dir, "update-ref", ref, commit, "0" * 40, home=home)       # create only, never overwrite
        return {"candidate_sha": commit, "ref": ref, "tree": tree, "repository": str(git_dir)}

    def phase_learn(self, cycle: dict) -> None:
        folder = self.cycle_dir(cycle)
        case = self.case(cycle["task_id"]) if cycle.get("task_id") else {}
        attempt = cycle.get("attempt") or {}
        verdict = cycle.get("verdict") or {}
        outcome = cycle.get("outcome") or "UNKNOWN"
        store = r.LearningStore(self.work / "learning", self.work / "learning-docs")
        run_id = "LOOP-" + cycle["id"]
        after = (cycle.get("decision") or {}).get("candidate_sha") or cycle["base_sha"]
        from learning.trace import case_id  # noqa: PLC0415
        key = case_id({"task_id": "EVO-" + run_id, "start_sha": cycle["base_sha"], "end_sha": after})
        lesson = {"campaign_store": "learning", "case_id": key}
        if store.current(key) is None:                        # idempotent across a crash + resume
            summary = (f"{outcome}: verifier {verdict.get('verdict') or 'not run'}; "
                       + "; ".join((verdict.get("reasons") or [])[:3]))[:1000]
            r.remember(store, run_id=run_id, scenario=cycle.get("task_id") or "?", before=cycle["base_sha"],
                       after=after, summary=summary,
                       result="CANDIDATE_PASSES" if outcome == "ACCEPTED" else outcome,
                       paths=list(attempt.get("changed_files") or []), evidence=folder,
                       model=str(attempt.get("model") or "unknown"), detail=str(attempt.get("reason") or ""))
            lesson["written"] = True
            self._hold(cycle, "LEARN_WRITTEN")
        else:
            lesson["written"] = False
            lesson["already_present"] = True
        lesson["status"] = "PARTIAL" if outcome == "ACCEPTED" else "FAILED_EXPERIMENT"
        if outcome == "ACCEPTED" and self.config.backend == "bossman_coding" and self.config.publish_recipes:
            lesson["product_recipe"] = self.publish_recipe(cycle, case)
        cycle["lesson"] = lesson
        _write_json(folder / "learn.json", lesson)

    def publish_recipe(self, cycle: dict, case: dict) -> dict:
        kind = (cycle.get("attempt") or {}).get("model_kind")
        if kind != "REAL_MODEL" and not self.config.allow_mock_lessons:
            return {"published": False, "reason": f"{kind}: a mock/unknown model never writes product memory "
                                                  "(the gate enables it only in its private data folder)"}
        backend = self.backend()
        recipe_id = f"evo-{cycle['task_id']}-{cycle['id'][-8:]}"
        with contextlib.suppress(Exception):
            code, body = backend.api.get("/api/coding-recipes?project_id=" + self.config.project_id)
            if code == 200 and any(item.get("id") == recipe_id for item in (body or {}).get("items") or []):
                return {"published": True, "recipe_id": recipe_id, "already_present": True}
        diff = (self.cycle_dir(cycle) / "attempt" / "attempt.diff").read_text(encoding="utf-8")
        recipe = build_recipe(case, cycle, diff, recipe_id)
        verdict = cycle.get("verdict") or {}
        body = {"recipe": recipe, "project_id": self.config.project_id, "scope": "project",
                "evidence": {"source": f"evolution-loop:{self.state['campaign_id']}:{cycle['id']}:verdict.json",
                             "expected": "PASS", "actual": str(verdict.get("verdict")),
                             "head_sha": (cycle.get("decision") or {}).get("candidate_sha") or "",
                             "environment": f"{platform.system()} {platform.machine()} python {sys.version.split()[0]}"},
                "verifier": {"principal_id": v.PRINCIPAL, "independence_class": "external_tool",
                             "run_id": cycle["id"], "model_id": ""}}
        try:
            code, resp = backend.api.post("/api/coding-recipes", body)
        except ApiError as exc:
            return {"published": False, "recipe_id": recipe_id, "reason": str(exc)[:300]}
        if code != 200:
            return {"published": False, "recipe_id": recipe_id, "reason": f"http {code}: "
                    + json.dumps(resp, ensure_ascii=False)[:400]}
        return {"published": True, "recipe_id": recipe_id, "lesson_id": (resp or {}).get("lesson_id"),
                "model_kind": kind}

    def phase_checkpoint(self, cycle: dict) -> None:
        folder = self.cycle_dir(cycle)
        outcome = cycle.get("outcome") or "UNKNOWN_OUTCOME"
        cycle["outcome"] = outcome
        task = self.state["tasks"].get(cycle.get("task_id") or "")
        if task is not None:
            task["last_verdict"] = (cycle.get("verdict") or {}).get("verdict") or outcome
            if task["attempts"] >= self.config.max_retries_per_task and not task["solved"]:
                task["exhausted"] = True
        if outcome != "UNKNOWN_OUTCOME":
            v.rmtree(folder / "attempt" / "student-repo")
        if outcome == "ACCEPTED":
            self.state["consecutive_failures"] = 0
        elif outcome != "STOPPED":
            self.state["consecutive_failures"] += 1
        cycle["evidence_manifest_sha256"] = seal_evidence(folder, {
            "campaign": self.state["campaign_id"], "cycle": cycle["id"], "base_sha": cycle["base_sha"],
            "outcome": outcome, "candidate_sha": (cycle.get("decision") or {}).get("candidate_sha")}, r.atomic_json)
        cycle.update(status=CLOSED, closed_at=now_iso())
        self.state["current"] = None


def build_recipe(case: dict, cycle: dict, diff: str, recipe_id: str) -> dict:
    """An executable coding recipe (product grammar) from a VERIFIED diff."""
    lesson = dict(case.get("lesson") or {})
    tests = list(case["tests"]) + list((cycle.get("verdict") or {}).get("new_regression_tests") or [])
    steps = _steps_from_diff(diff) + [{"tool": "run_tests", "args": {"paths": tests}}]
    keywords = lesson.get("keywords") or [w for w in case["goal"].replace(":", " ").split() if len(w) > 4][:8]
    attempt = cycle.get("attempt") or {}
    return {"schema_version": "bossman.coding-recipe/1", "id": recipe_id,
            "title": lesson.get("title") or f"{case['id']}: verified repair",
            "symptom": lesson.get("symptom") or f"{case['goal']} Failing checks: {', '.join(case['tests'])}",
            "cause": lesson.get("cause") or f"Defect in {', '.join(case['editable'])} (verified patch, see steps)",
            "diagnosis": lesson.get("diagnosis") or "Reproduce with the failing checks, then read the editable files",
            "action": lesson.get("action") or "Apply the minimal change shown in the steps and add a regression test",
            "required_check": {"tool": "run_tests", "args": {"paths": tests}},
            "applies_when": {"language": "python", "keywords": keywords},
            "counterexample": lesson.get("counterexample") or "Do not apply when the failing checks differ",
            "steps": steps[:24],
            "provenance": {"who": f"model:{attempt.get('model') or 'unknown'}", "source": "student",
                           "assistance_level": "none", "model": str(attempt.get("model") or ""),
                           "run_id": cycle["id"], "what": "evolution loop verified repair"},
            "status": "VERIFIED"}


def _steps_from_diff(diff: str) -> list[dict]:
    steps, current, hunk, new_file = [], None, None, False
    hunks: dict[str, list[list[str]]] = {}
    news: dict[str, bool] = {}
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            current, hunk, new_file = None, None, False
            continue
        if line.startswith("new file mode"):
            new_file = True
        elif line.startswith("+++ "):
            target = line[4:].strip()
            current = target[2:] if target.startswith("b/") else None
            if current:
                hunks.setdefault(current, [])
                news[current] = new_file
        elif line.startswith("@@") and current:
            hunk = []
            hunks[current].append(hunk)
        elif hunk is not None and line[:1] in (" ", "+", "-"):
            hunk.append(line)
    for path, parts in hunks.items():
        if news.get(path):
            content = "\n".join(l[1:] for h in parts for l in h if l.startswith("+"))
            steps.append({"tool": "write_file", "args": {"path": path, "content": content + "\n"}})
            continue
        steps.append({"tool": "read_file", "args": {"path": path}})
        for h in parts:
            old = "\n".join(l[1:] for l in h if l[:1] in (" ", "-"))
            new = "\n".join(l[1:] for l in h if l[:1] in (" ", "+"))
            if old.strip() and old != new:
                steps.append({"tool": "edit_file", "args": {"path": path, "old": old, "new": new}})
    return steps


# ---------------------------------------------------------------- read side
def status(work: Path) -> dict:
    """Owner view: never raises for a missing campaign; says so instead."""
    work = Path(work)
    state = _read_json(work / STATE_FILE)
    lease = _read_json(work / LEASE_FILE)
    alive = bool(lease) and process_alive(lease) and lease.get("host") == socket.gethostname()
    out = {"campaign": str(work), "exists": state is not None, "loop_running": alive,
           "lease": {"pid": (lease or {}).get("pid"), "alive": alive, "stale": bool(lease) and not alive,
                     "heartbeat_at": (lease or {}).get("heartbeat_at")},
           "stop_requested": _control(work, STOP_FILE), "pause_requested": _control(work, PAUSE_FILE),
           "free_ram_mb": free_ram_mb(), "gpu": "not measured by the loop (see the Command Center metrics page)"}
    if state is None:
        out["status"] = "NO_CAMPAIGN"
        return out
    cycles = state.get("cycles") or []
    current = next((c for c in cycles if c["id"] == state.get("current")), None) or (cycles[-1] if cycles else None)
    status_value = state.get("status")
    if status_value == "RUNNING" and not alive:
        status_value = "INTERRUPTED (loop process not running)"
    started = (current or {}).get("started_at")
    elapsed = None
    if started:
        with contextlib.suppress(ValueError):
            elapsed = int(time.time() - _epoch(started))
    attempt = (current or {}).get("attempt") or {}
    verdict = (current or {}).get("verdict") or {}
    lesson = (current or {}).get("lesson") or {}
    out.update({
        "status": status_value, "halt_reason": state.get("halt_reason"),
        "backend": state["config"].get("backend"), "model": attempt.get("model") or (state.get("backend_info") or {}).get("model"),
        "model_kind": attempt.get("model_kind") or (state.get("backend_info") or {}).get("model_kind"),
        "cycle": {"index": (current or {}).get("index"), "id": (current or {}).get("id"),
                  "task": (current or {}).get("task_id"), "phase": (current or {}).get("phase"),
                  "elapsed_seconds": elapsed, "outcome": (current or {}).get("outcome")},
        "found_defect": (current or {}).get("defect"),
        "patch": {"files": attempt.get("changed_files"), "changed_lines": attempt.get("diff_lines"),
                  "student_claim_is_untrusted": True},
        "verifier_verdict": verdict.get("verdict"), "verifier_reasons": (verdict.get("reasons") or [])[:3],
        "new_lesson": ({"case_id": lesson.get("case_id"), "recipe": lesson.get("product_recipe")} if lesson else None),
        "last_action": state.get("last_action"),
        "queue": {tid: {k: t.get(k) for k in ("attempts", "solved", "exhausted", "last_verdict")}
                  for tid, t in (state.get("tasks") or {}).items()},
        "cycles_closed": sum(1 for c in cycles if c.get("status") == CLOSED),
        "max_cycles": state["config"].get("max_cycles"),
        "champion_sha": state.get("champion_sha"), "base_sha": state.get("base_sha"),
        "budget": {"reserved_usd": state.get("reserved_usd"), "reported_cost_usd": state.get("reported_cost_usd"),
                   "unknown_cost_calls": state.get("unknown_cost_calls"),
                   "active_seconds": (state.get("budget") or {}).get("active_seconds")},
        "consecutive_failures": state.get("consecutive_failures"),
        "loop_rss_mb": tree_rss_mb(int(lease["pid"])) if alive else None,
        "paused": bool(_control(work, PAUSE_FILE)) or str(status_value).endswith("PAUSED"),
        "stopped": bool(_control(work, STOP_FILE)) or status_value == "STOPPED",
    })
    return out


def _epoch(iso: str) -> float:
    import calendar  # noqa: PLC0415
    return calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ"))


def report(work: Path, state: dict | None = None) -> dict:
    state = state or _read_json(Path(work) / STATE_FILE) or {}
    cycles = [c for c in state.get("cycles") or [] if c.get("status") == CLOSED]
    kinds = {((c.get("attempt") or {}).get("model_kind") or "UNKNOWN") for c in cycles}
    rows = [{"index": c.get("index"), "id": c["id"], "task": c.get("task_id"), "outcome": c.get("outcome"),
             "verdict": (c.get("verdict") or {}).get("verdict"), "model": (c.get("attempt") or {}).get("model"),
             "model_kind": (c.get("attempt") or {}).get("model_kind"),
             "counts_as_student_success": bool((c.get("verdict") or {}).get("counts_as_student_success")),
             "candidate_ref": (c.get("decision") or {}).get("ref"),
             "lesson": (c.get("lesson") or {}).get("product_recipe") or (c.get("lesson") or {}).get("status"),
             "memory": (c.get("attempt") or {}).get("memory"),
             "recipes_applied": ((c.get("attempt") or {}).get("sidecar") or {}).get("recipes_applied"),
             "evidence_manifest_sha256": c.get("evidence_manifest_sha256")} for c in cycles]
    counts: dict[str, int] = {}
    for c in cycles:
        counts[c.get("outcome") or "?"] = counts.get(c.get("outcome") or "?", 0) + 1
    return {"schema": "bossman.evolution.loop-report/1", "campaign_id": state.get("campaign_id"),
            "status": state.get("status"), "halt_reason": state.get("halt_reason"), "base_sha": state.get("base_sha"),
            "champion_sha": state.get("champion_sha"), "cycles": rows, "outcomes": counts,
            "model_kinds": sorted(kinds), "student_verified_passes": sum(r_["counts_as_student_success"] for r_ in rows),
            "mock_only": kinds == {"MOCK_MODEL"},
            "note": ("MOCK_MODEL cycles prove plumbing only; they are never counted as student success"
                     if "MOCK_MODEL" in kinds else ""),
            "lease_recoveries": len(state.get("lease_recoveries") or []), "recoveries": state.get("recoveries") or [],
            "production_promoted": False, "stable_written": False,
            "weights": "WEIGHTS_UNCHANGED",
            "budget": {"reserved_usd": state.get("reserved_usd"), "reported_cost_usd": state.get("reported_cost_usd"),
                       "unknown_cost_calls": state.get("unknown_cost_calls"),
                       "active_seconds": (state.get("budget") or {}).get("active_seconds")}}


def verify_cycles(work: Path) -> dict:
    """Recheck every closed cycle's evidence manifest (tamper evidence vs the checkpoint)."""
    state = _read_json(Path(work) / STATE_FILE) or {}
    checked = 0
    for c in state.get("cycles") or []:
        if c.get("status") == CLOSED and c.get("evidence_manifest_sha256"):
            verify_evidence(Path(work) / "cycles" / c["id"], c["evidence_manifest_sha256"])
            checked += 1
    return {"status": "EVIDENCE_INTACT", "cycles_checked": checked}


# ---------------------------------------------------------------- CLI
def default_suite_path() -> Path | None:
    """config/evolution/*.json next to the calling script (checkout or app-support)."""
    script = Path(sys.argv[0]).resolve().parent if sys.argv and sys.argv[0] else Path.cwd()
    for base in (script, script.parent):
        candidate = base / "config" / "evolution" / "owner-v1.1.json"
        if candidate.is_file():
            return candidate
    return None


def add_loop_arguments(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--work", type=Path, help="campaign folder (outside the source checkout)")
    ap.add_argument("--suite", type=Path)
    ap.add_argument("--repo", type=Path)
    ap.add_argument("--backend", choices=BACKENDS)
    ap.add_argument("--model")
    ap.add_argument("--api-url")
    ap.add_argument("--token-file")
    ap.add_argument("--data-dir")
    ap.add_argument("--project-id")
    ap.add_argument("--no-memory", action="store_true")
    ap.add_argument("--add-root", action="store_true", help="allow the campaign folder in the code roots")
    ap.add_argument("--mock-script")
    ap.add_argument("--local-url")
    ap.add_argument("--allow-cloud", action="store_true")
    ap.add_argument("--executor", choices=("host", "docker"))
    ap.add_argument("--image")
    ap.add_argument("--test-python")
    ap.add_argument("--python-path", action="append", dest="python_paths")
    ap.add_argument("--test-timeout", type=int)
    ap.add_argument("--max-cycles", type=int)
    ap.add_argument("--attempt-minutes", type=float)
    ap.add_argument("--total-hours", type=float)
    ap.add_argument("--max-retries", type=int, dest="max_retries_per_task")
    ap.add_argument("--max-consecutive-failures", type=int)
    ap.add_argument("--max-disk-mb", type=float)
    ap.add_argument("--max-rss-mb", type=float)
    ap.add_argument("--min-free-ram-mb", type=float)
    ap.add_argument("--max-usd", type=float)
    ap.add_argument("--attempt-usd", type=float)
    ap.add_argument("--poll-seconds", type=float)
    ap.add_argument("--allow-mock-lessons", action="store_true")
    ap.add_argument("--no-recipes", action="store_true")
    ap.add_argument("--redo", help="replay ONE cycle whose outcome is UNKNOWN_OUTCOME (explicit owner decision)")
    ap.add_argument("--break-lease", action="store_true", help="take over a lease whose owner looks alive")


def config_from_args(args) -> tuple[LoopConfig | None, dict]:
    overrides = {k: getattr(args, k, None) for k in (
        "backend", "model", "api_url", "token_file", "data_dir", "project_id", "mock_script", "local_url",
        "executor", "image", "test_python", "python_paths", "test_timeout", "max_cycles", "attempt_minutes",
        "total_hours", "max_retries_per_task", "max_consecutive_failures", "max_disk_mb", "max_rss_mb",
        "min_free_ram_mb", "max_usd", "attempt_usd", "poll_seconds")}
    for flag, key, val in (("no_memory", "use_memory", False), ("add_root", "add_root", True),
                           ("allow_cloud", "allow_cloud", True), ("allow_mock_lessons", "allow_mock_lessons", True),
                           ("no_recipes", "publish_recipes", False)):
        if getattr(args, flag, False):
            overrides[key] = val
    for key in ("token_file", "data_dir", "mock_script", "test_python"):
        if overrides.get(key):
            overrides[key] = str(Path(overrides[key]).resolve())
    if not (args.work / STATE_FILE).exists():
        suite = args.suite or default_suite_path()
        if not suite or not args.repo:
            raise ValueError("a new campaign needs --repo and --suite (config/evolution/*.json)")
        base = {k: val for k, val in overrides.items() if val is not None}
        return LoopConfig(suite=str(Path(suite).resolve()), repo=str(Path(args.repo).resolve()), **base), {}
    return None, overrides


def cli(action: str, args) -> int:
    work = args.work
    if work is None:
        raise ValueError("--work CAMPAIGN_FOLDER is required")
    work = Path(work).resolve()
    if action == "status":
        print(json.dumps(status(work), ensure_ascii=False, indent=1, default=str))
        return 0
    if action == "report":
        state = _read_json(work / STATE_FILE)
        if state is None:
            print(json.dumps({"status": "NO_CAMPAIGN", "campaign": str(work)}))
            return 2
        print(json.dumps(report(work, state), ensure_ascii=False, indent=1, default=str))
        return 0
    if action == "pause":
        print(json.dumps(request_pause(work, "owner-cli"), ensure_ascii=False))
        return 0
    if action == "stop":
        print(json.dumps(request_stop(work, "owner-cli"), ensure_ascii=False))
        return 0
    if action == "resume":
        print(json.dumps(clear_controls(work, "owner-cli"), ensure_ascii=False))
        if not getattr(args, "no_start", False):
            action = "loop"
        else:
            return 0
    if action == "loop":
        config, overrides = config_from_args(argparse.Namespace(**{**vars(args), "work": work}))
        loop = EvolutionLoop(work, config, overrides=overrides)
        state = loop.run(break_lease=bool(getattr(args, "break_lease", False)), redo=getattr(args, "redo", None))
        summary = report(work, state)
        print(json.dumps({k: summary[k] for k in ("status", "halt_reason", "outcomes", "model_kinds",
                                                  "student_verified_passes", "champion_sha")}, ensure_ascii=False))
        if "MOCK_MODEL" in summary["model_kinds"]:
            print("MOCK_MODEL: these cycles prove plumbing only, not model skill.")
        return 0 if state["status"] in ("COMPLETED", "PAUSED", "STOPPED", "NEEDS_NEW_SCENARIOS",
                                        "QUEUE_EXHAUSTED", "AUTO_PAUSED", "BUDGET_EXHAUSTED") else 2
    raise ValueError(f"unknown action {action}")


def main(argv=None) -> int:
    """``python -m bossman_v3.self_improvement.loop ACTION --work DIR`` (the API starts it this way)."""
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError, OSError):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="bossman-evolution-loop")
    ap.add_argument("action", choices=("loop", "status", "report", "pause", "resume", "stop"))
    ap.add_argument("--no-start", action="store_true", help="resume: only clear PAUSE/STOP")
    add_loop_arguments(ap)
    args = ap.parse_args(argv)
    try:
        return cli(args.action, args)
    except LeaseHeld as exc:
        print(f"Evolution loop refused: {exc}", file=sys.stderr)
        return 3
    except (OSError, ValueError, RuntimeError) as exc:
        print("Evolution loop blocked: " + v.redact_text(str(exc)), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
