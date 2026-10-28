"""Resource governor 2.0 (Bossman 2.5, TZ T3/M2): lease-based arbitration of heavy GPU/memory consumers.

The PC has ~120 GB of unified CPU/GPU memory. Several heavy consumers want large slices of it: the Gemma chat model
(owner chat and Jeff, ~38 GB resident), WanGP Animate/VACE (~38-55 GB), LTX-2.5 via sd-cli (~60-70 GB), photo models.
Before this module the governor only reacted to direct-gen jobs; nothing stopped a background WanGP render from sitting
next to Gemma while the owner chatted (incident 25.10: owner chat became slow, WanGP held 38 GB alongside Gemma).

Model:
* a registry of consumers (``ConsumerProfile``) with a P95 peak in GB; a seed estimate is ``provisional`` until
  ``min_samples`` (10) measurements exist, then the measured nearest-rank P95 replaces it. A consumer without any P95 is
  REFUSED with a reason (never started "on luck");
* priorities: owner_interactive > jeff_answer > paid_render > background > eval; queue order = priority -> aging
  (bounded, never crosses a class) -> FIFO;
* grant rule: committed(active leases) + candidate <= budget_gb - headroom_gb, at most ONE gpu_heavy compute lease, and
  (when a live reading exists) available_gb - candidate >= headroom_gb. Resident leases of the same consumer share one
  copy of the model (Gemma for owner chat + Jeff is counted once);
* before refusing on the live check, idle models (Ollama ``keep_alive: 0``, confirmed by ``/api/ps``) that no granted
  lease holds are unloaded and memory is re-measured;
* preemption marks lower-priority leases ``preempting`` (they still count until their holder releases, the TTL expires
  or recovery sees the process gone) - cooperative stop at a checkpoint. A ``paid_render`` lease is NEVER preempted
  automatically, by anything. An owner_interactive lease also makes background/eval gpu_heavy compute yield even when
  memory fits (GPU contention) and new background GPU work waits while the owner chats;
* memory guard: live free < guard_free_gb (3) -> the lowest-priority non-paid lease is preempted with a logged reason;
  its process is terminated only if it provably holds this governor's token;
* fencing: every grant carries a monotonically increasing token; heartbeat/check_fence/release with a stale token are
  refused, so a late worker cannot start on a lease that was taken away;
* restart recovery: new epoch; a lease with a pid is re-adopted only when the process is alive with the recorded
  create time AND its environment carries ``BOSSMAN_RESOURCE_LEASE=<lease_id>.<token>``; a dead one expires; a
  mismatched one (pid reuse / foreign) is released and the process is NOT touched (AGENTS.md: never touch foreign
  processes). Nothing is ever restarted or repeated automatically (a paid render is not repeated).

Ledger: SQLite in WAL mode, every mutation inside ``BEGIN IMMEDIATE`` - the backend API (``/api/resources/*``) and the
governor process share it without double grants. Stdlib only; psutil is imported lazily by the real process probe.
All decisions are pure functions over rows; clock, memory reading, unload hook and probe are injected (tests use fakes).
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterator

log = logging.getLogger("governor.leases")

PRIORITIES: dict[str, int] = {"owner_interactive": 50, "jeff_answer": 40, "paid_render": 30, "background": 20,
                              "eval": 10}
PRIORITY_RU = {"owner_interactive": "чат владельца", "jeff_answer": "ответ Jeff", "paid_render": "платный рендер Studio",
               "background": "фоновая генерация", "eval": "оценка/исследование"}
ACTIVE = ("granted", "preempting")                     # counted against the budget
OPEN = ("granted", "preempting", "queued")             # need heartbeats
TERMINAL = ("released", "expired", "refused")
ENV_TOKEN = "BOSSMAN_RESOURCE_LEASE"
DB_NAME = "resource-leases.db"

LEASE_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "budget_gb": 120.0,            # controlled budget (unified memory the GPU may address); calibrate from measurements
    "headroom_gb": 8.0,            # always left free for the OS / spikes
    "guard_free_gb": 3.0,          # live free memory below this -> the memory guard preempts the lowest lease
    "guard_terminate": True,       # guard may terminate a process only if it holds this governor's token
    "ttl_s": 300,                  # a lease without heartbeat for this long expires
    "min_samples": 10,             # measurements needed before the measured P95 replaces the seed estimate
    "strict_measured": False,      # True: refuse consumers whose P95 is still provisional (seed estimate)
    "interactive_preempts_background_gpu": True,
    "aging_s": 600,                # +1 effective priority per aging_s waited, capped below the next class
    "unload_timeout_s": 30.0,
    "events_keep": 2000,
    "keep_models": [],             # Ollama models the unload hook never touches (governor.json ollama_keep_models too)
    "consumers": {},               # overrides/additions: {id: {field: value}}
}


@dataclass(frozen=True)
class ConsumerProfile:
    id: str
    label: str
    kind: str                        # resident (model held in memory) | compute (an active generation)
    gpu_heavy: bool
    p95_peak_gb: float | None        # seed estimate (provisional) or None = unknown
    adapter: str = "other"           # ollama | llama_server | sd_cli | wangp | other
    ollama_names: tuple[str, ...] = ()
    note: str = ""


SEED_CONSUMERS: dict[str, ConsumerProfile] = {p.id: p for p in (
    ConsumerProfile("gemma_chat", "Gemma 4 31B (чат владельца / Jeff)", "resident", False, 38.0, "ollama",
                    ("gemma4-31b*", "bossman-uncensored-chat*"),
                    "llama-server/Ollama Q8 + KV, ~38 GB observed 25.10"),
    ConsumerProfile("wangp_animate", "WanGP Animate", "compute", True, 55.0, "wangp", (),
                    "38-55 GB observed; P95 seed = upper bound"),
    ConsumerProfile("wangp_vace", "WanGP VACE", "compute", True, 55.0, "wangp", (), "38-55 GB observed"),
    ConsumerProfile("ltx25_sdcli", "LTX-2.5 (sd-cli)", "compute", True, 70.0, "sd_cli", (), "60-70 GB observed"),
    ConsumerProfile("acestep", "ACE-Step 1.5 (Music Studio)", "resident", True, 24.0, "other", (),
                    "PROVISIONAL seed from the 28.10 master prompt (>= 24 GB free to auto-start); not measured yet"),
    ConsumerProfile("z_image", "z-image (фото)", "compute", True, None, "sd_cli", (), "not measured yet"),
)}


class LeaseError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


# ---------------------------------------------------------------------------------------------------------------
# pure decision logic
# ---------------------------------------------------------------------------------------------------------------
@dataclass
class Lease:
    id: str
    consumer: str
    priority: str
    kind: str
    peak_gb: float
    gpu_heavy: bool
    state: str
    created_at: float
    holder: str = ""
    token: int = 0
    epoch: int = 0
    ttl_s: float = 300.0
    heartbeat_at: float = 0.0
    pid: int | None = None
    proc_create_time: float | None = None
    idempotency_key: str | None = None
    reason: str = ""

    @property
    def rank(self) -> int:
        return PRIORITIES[self.priority]


def nearest_rank_p95(samples: list[float]) -> float:
    s = sorted(samples)
    k = max(1, -(-95 * len(s) // 100))               # ceil(0.95 n), nearest-rank
    return float(s[k - 1])


def committed_gb(active: list[Lease]) -> float:
    """Resident leases of one consumer share a single copy of the model; compute leases each count."""
    resident: dict[str, float] = {}
    total = 0.0
    for lease in active:
        if lease.kind == "resident":
            resident[lease.consumer] = max(resident.get(lease.consumer, 0.0), lease.peak_gb)
        else:
            total += lease.peak_gb
    return total + sum(resident.values())


def added_gb(cand: Lease, active: list[Lease]) -> float:
    if cand.kind == "resident":
        held = max((x.peak_gb for x in active if x.kind == "resident" and x.consumer == cand.consumer), default=None)
        if held is not None:
            return max(0.0, cand.peak_gb - held)
    return cand.peak_gb


def name_matches(name: str, patterns: Any) -> bool:
    """Ollama model name vs exact names / ``prefix*`` patterns, case-insensitive, ``:latest`` implied (live 25.10: the
    owner's Gemma is ``gemma4-31b-uncensored-ctx32k:latest``, an exact list missed it)."""
    n = str(name).lower()
    for pat in patterns or ():
        p = str(pat).lower()
        if p.endswith("*"):
            if n.startswith(p[:-1]):
                return True
        elif n == p or (":" not in p and n == p + ":latest"):
            return True
    return False


def _gb(v: float) -> str:
    return f"{v:.0f} ГБ" if abs(v - round(v)) < 0.05 else f"{v:.1f} ГБ"


def _who(lease: Lease) -> str:
    return f"{lease.consumer} ({PRIORITY_RU[lease.priority]}, {_gb(lease.peak_gb)})"


def fits(cand: Lease, active: list[Lease], available_gb: float | None, cfg: dict) -> tuple[bool, str, str]:
    """(ok, kind, reason) where kind is '' | 'gpu' | 'budget' | 'live'."""
    if cand.gpu_heavy and cand.kind == "compute":
        heavy = [x for x in active if x.gpu_heavy and x.kind == "compute"]
        if heavy:
            return False, "gpu", "одна тяжёлая GPU-задача за раз: сейчас " + ", ".join(_who(x) for x in heavy)
    limit = float(cfg["budget_gb"]) - float(cfg["headroom_gb"])
    need = added_gb(cand, active)
    used = committed_gb(active)
    if used + need > limit + 1e-9:
        holders = ", ".join(_who(x) for x in sorted(active, key=lambda x: -x.peak_gb)) or "ничего"
        return False, "budget", (f"не помещается: занято арендами {_gb(used)} + нужно {_gb(need)} > "
                                 f"{_gb(limit)} (бюджет {_gb(float(cfg['budget_gb']))} минус запас "
                                 f"{_gb(float(cfg['headroom_gb']))}); держат: {holders}")
    if available_gb is not None and need > 0 and available_gb - need < float(cfg["headroom_gb"]) - 1e-9:
        return False, "live", (f"мало свободной памяти сейчас: {_gb(available_gb)} свободно, нужно {_gb(need)} + "
                               f"запас {_gb(float(cfg['headroom_gb']))}")
    return True, "", ""


def _interactive_rule(cfg: dict) -> bool:
    return bool(cfg.get("interactive_preempts_background_gpu", True))


def _yields_to_owner(lease: Lease) -> bool:
    return lease.gpu_heavy and lease.kind == "compute" and lease.rank <= PRIORITIES["background"]


def preemptible_by(cand: Lease, lease: Lease) -> bool:
    if lease.priority == "paid_render":            # never preempted automatically, by anything
        return False
    return lease.state == "granted" and lease.rank < cand.rank


@dataclass
class Plan:
    decision: str                                   # grant | queue
    victims: list[Lease] = field(default_factory=list)
    reason: str = ""
    live_blocked: bool = False                      # the live-memory check failed (an unload may help)


def plan(cand: Lease, active: list[Lease], available_gb: float | None, cfg: dict,
         waiting: tuple[Lease, ...] | list[Lease] = ()) -> Plan:
    """``waiting`` = other queued leases (a queued owner chat also holds back new background GPU work)."""
    granted = [x for x in active if x.state == "granted"]
    forced: list[Lease] = []
    if _interactive_rule(cfg):
        if cand.priority == "owner_interactive":
            forced = [x for x in granted if _yields_to_owner(x)]
        elif _yields_to_owner(cand) and any(x.priority == "owner_interactive" for x in active + list(waiting)):
            return Plan("queue", [], "ждёт: владелец сейчас в чате — фоновая GPU-работа уступает (инцидент 25.10)")
    ok, _, why = fits(cand, active, available_gb, cfg)
    if ok:
        return Plan("grant", forced, "")
    victims = list(forced)
    pool = sorted((x for x in granted if preemptible_by(cand, x) and x not in victims),
                  key=lambda x: (x.rank, -x.created_at))

    def freed_state() -> tuple[list[Lease], float | None]:
        rest = [x for x in active if x not in victims]
        if available_gb is None:
            return rest, None
        before, after = committed_gb(active), committed_gb(rest)
        return rest, available_gb + max(0.0, before - after)

    rest, avail = freed_state()
    ok2, _, _ = fits(cand, rest, avail, cfg)
    while not ok2 and pool:
        victims.append(pool.pop(0))
        rest, avail = freed_state()
        ok2, _, _ = fits(cand, rest, avail, cfg)
    if ok2:                                        # drop victims that turned out unnecessary (newest choice first)
        for v in [x for x in reversed(victims) if x not in forced]:
            victims.remove(v)
            rest, avail = freed_state()
            if not fits(cand, rest, avail, cfg)[0]:
                victims.append(v)
    live_only = fits(cand, active, None, cfg)[0]          # without the live reading it would fit: an unload may help
    if ok2 and victims:
        return Plan("queue", victims, "ждёт остановки вытесненных: " + ", ".join(_who(v) for v in victims),
                    live_only)
    paid = [x for x in active if x.priority == "paid_render"]
    tail = ("; платный рендер Studio автоматически не останавливается" if paid else "")
    return Plan("queue", forced, "ждёт: " + why + tail, live_only)


def queue_order(queued: list[Lease], now: float, cfg: dict) -> list[Lease]:
    aging = max(1.0, float(cfg.get("aging_s", 600)))

    def key(lease: Lease) -> tuple[float, float]:
        bonus = min(9.0, (now - lease.created_at) // aging)    # classes are 10 apart: aging never crosses one
        return (-(lease.rank + bonus), lease.created_at)
    return sorted(queued, key=key)


# ---------------------------------------------------------------------------------------------------------------
# idle-model unload adapters (keep_alive=0 + confirmation)
# ---------------------------------------------------------------------------------------------------------------
def _http_json(method: str, url: str, body: dict | None = None, timeout: float = 5.0) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - loopback URLs from config only
        raw = r.read()
    return json.loads(raw) if raw else {}


class OllamaUnloader:
    """Unloads idle Ollama models: ``keep_alive: 0`` then polls ``/api/ps`` until the model is gone (confirmed)."""

    def __init__(self, url: str = "http://127.0.0.1:11434", http: Callable[..., Any] = _http_json,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self.url, self.http, self.sleep, self.clock = url.rstrip("/"), http, sleep, clock

    def loaded(self) -> list[dict] | None:
        try:
            ps = self.http("GET", self.url + "/api/ps")
        except (OSError, ValueError, urllib.error.URLError):
            return None
        out = []
        for m in (ps or {}).get("models") or []:
            name = m.get("name") or m.get("model")
            if name:
                out.append({"name": str(name), "size_gb": round(float(m.get("size") or 0) / 1e9, 2)})
        return out

    def __call__(self, keep: set[str], timeout_s: float = 30.0) -> dict:
        now = self.loaded()
        if now is None:
            return {"unloaded": [], "failed": [], "confirmed": False, "reason": "ollama /api/ps unreachable"}
        targets = [m["name"] for m in now if not name_matches(m["name"], keep)]
        for name in targets:
            try:
                self.http("POST", self.url + "/api/generate", {"model": name, "keep_alive": 0})
            except (OSError, ValueError, urllib.error.URLError):
                log.warning("ollama unload request failed for %s", name)
        deadline = self.clock() + timeout_s
        left = list(targets)
        while left and self.clock() < deadline:
            cur = self.loaded()
            if cur is not None:
                names = {m["name"] for m in cur}
                left = [n for n in left if n in names]
            if left:
                self.sleep(0.5)
        done = [n for n in targets if n not in left]
        if targets:
            log.info("LEASE unload idle ollama models %s -> confirmed %s, still loaded %s", targets, done, left)
        return {"unloaded": done, "failed": left, "confirmed": not left}


# ---------------------------------------------------------------------------------------------------------------
# process identity (restart recovery / guard termination)
# ---------------------------------------------------------------------------------------------------------------
def lease_env_token(lease_id: str, token: int) -> str:
    return f"{lease_id}.{token}"


def psutil_probe(pid: int) -> dict | None:
    """{create_time, env_token} of a live process, None if it does not exist."""
    import psutil
    try:
        p = psutil.Process(pid)
        ct = p.create_time()
        try:
            env, readable = p.environ().get(ENV_TOKEN), True
        except (psutil.AccessDenied, psutil.ZombieProcess, OSError):
            env, readable = None, False
        return {"create_time": ct, "env_token": env, "env_readable": readable}
    except psutil.NoSuchProcess:
        return None
    except (psutil.AccessDenied, OSError):
        return {"create_time": None, "env_token": None, "env_readable": False}


def psutil_stop_tree(pid: int) -> bool:
    """Terminate a process verified to hold this governor's token, and its children."""
    import psutil
    try:
        p = psutil.Process(pid)
        procs = p.children(recursive=True) + [p]
        for q in procs:
            try:
                q.terminate()
            except psutil.Error:
                pass
        psutil.wait_procs(procs, timeout=10)
        return True
    except psutil.Error:
        return False


# ---------------------------------------------------------------------------------------------------------------
# ledger
# ---------------------------------------------------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS leases (
  id TEXT PRIMARY KEY, consumer TEXT NOT NULL, priority TEXT NOT NULL, kind TEXT NOT NULL, peak_gb REAL NOT NULL,
  gpu_heavy INTEGER NOT NULL, state TEXT NOT NULL, created_at REAL NOT NULL, holder TEXT NOT NULL DEFAULT '',
  token INTEGER NOT NULL DEFAULT 0, epoch INTEGER NOT NULL DEFAULT 0, ttl_s REAL NOT NULL, heartbeat_at REAL NOT NULL,
  pid INTEGER, proc_create_time REAL, idempotency_key TEXT UNIQUE, reason TEXT NOT NULL DEFAULT '',
  updated_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS leases_state ON leases(state);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, lease_id TEXT,
  event TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS measurements (id INTEGER PRIMARY KEY AUTOINCREMENT, consumer TEXT NOT NULL,
  config_hash TEXT NOT NULL DEFAULT '', peak_gb REAL NOT NULL, cold INTEGER NOT NULL DEFAULT 0, ts REAL NOT NULL);
"""
_COLS = ("id", "consumer", "priority", "kind", "peak_gb", "gpu_heavy", "state", "created_at", "holder", "token",
         "epoch", "ttl_s", "heartbeat_at", "pid", "proc_create_time", "idempotency_key", "reason")


def _row(r: sqlite3.Row) -> Lease:
    d = {k: r[k] for k in _COLS}
    d["gpu_heavy"] = bool(d["gpu_heavy"])
    return Lease(**d)


class LeaseLedger:
    def __init__(self, path: Path | str, cfg: dict | None = None, *, clock: Callable[[], float] = time.time,
                 mem: Callable[[], float | None] | None = None,
                 unload: Callable[[set[str], float], dict] | None = None,
                 loaded: Callable[[], list[dict] | None] | None = None):
        self.path = Path(path)
        self.cfg = {**LEASE_DEFAULTS, **(cfg or {})}
        self.clock, self.mem, self.unload = clock, mem, unload
        self.loaded = loaded if loaded is not None else getattr(unload, "loaded", None)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._all: list[sqlite3.Connection] = []
        self._all_lock = threading.Lock()
        with self._conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(_SCHEMA)

    # -- storage helpers --------------------------------------------------------------------------------------
    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        """One connection per thread, reused (closing the last WAL connection checkpoints: slow per call)."""
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(str(self.path), timeout=30.0, isolation_level=None, check_same_thread=True)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA busy_timeout=30000")
            c.execute("PRAGMA synchronous=NORMAL")          # WAL: durable at checkpoints, no fsync per commit
            self._local.conn = c
            with self._all_lock:
                self._all.append(c)
        yield c

    def close(self) -> None:
        with self._all_lock:
            conns, self._all = self._all, []
        for c in conns:
            try:
                c.close()
            except sqlite3.Error:
                pass
        self._local = threading.local()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                yield c
            except BaseException:
                c.execute("ROLLBACK")
                raise
            else:
                c.execute("COMMIT")

    def _meta_next(self, c: sqlite3.Connection, key: str) -> int:
        c.execute("INSERT INTO meta(k, v) VALUES(?, 1) ON CONFLICT(k) DO UPDATE SET v = v + 1", (key,))
        return int(c.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()[0])

    def _meta(self, c: sqlite3.Connection, key: str) -> int:
        r = c.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
        return int(r[0]) if r else 0

    def _event(self, c: sqlite3.Connection, lease_id: str | None, event: str, reason: str = "") -> None:
        c.execute("INSERT INTO events(ts, lease_id, event, reason) VALUES(?,?,?,?)",
                  (self.clock(), lease_id, event, reason))
        keep = int(self.cfg.get("events_keep", 2000))
        c.execute("DELETE FROM events WHERE id <= (SELECT MAX(id) FROM events) - ?", (keep,))
        log.info("LEASE %s %s %s", event, lease_id or "-", reason)

    def _set(self, c: sqlite3.Connection, lease: Lease, **kw: Any) -> Lease:
        new = replace(lease, **kw)
        c.execute("UPDATE leases SET state=?, token=?, epoch=?, heartbeat_at=?, reason=?, pid=?, proc_create_time=?,"
                  " updated_at=? WHERE id=?",
                  (new.state, new.token, new.epoch, new.heartbeat_at, new.reason, new.pid, new.proc_create_time,
                   self.clock(), new.id))
        return new

    def _insert(self, c: sqlite3.Connection, lease: Lease) -> None:
        d = asdict(lease)
        d["gpu_heavy"] = int(d["gpu_heavy"])
        cols = ",".join(_COLS) + ",updated_at"
        c.execute(f"INSERT INTO leases({cols}) VALUES({','.join('?' * (len(_COLS) + 1))})",
                  [d[k] for k in _COLS] + [self.clock()])

    def _open(self, c: sqlite3.Connection) -> list[Lease]:
        return [_row(r) for r in c.execute(
            f"SELECT * FROM leases WHERE state IN ({','.join('?' * len(OPEN))}) ORDER BY created_at", OPEN)]

    def _get(self, c: sqlite3.Connection, lease_id: str) -> Lease:
        r = c.execute("SELECT * FROM leases WHERE id=?", (lease_id,)).fetchone()
        if r is None:
            raise LeaseError("lease_not_found", f"аренда {lease_id} не найдена", 404)
        return _row(r)

    # -- registry -----------------------------------------------------------------------------------------------
    def profiles(self, c: sqlite3.Connection | None = None) -> dict[str, dict]:
        def build(conn: sqlite3.Connection) -> dict[str, dict]:
            out: dict[str, dict] = {}
            base = {k: asdict(v) for k, v in SEED_CONSUMERS.items()}
            for cid, over in (self.cfg.get("consumers") or {}).items():
                if isinstance(over, dict):
                    merged = {**base.get(cid, {"id": cid, "label": cid, "kind": "compute", "gpu_heavy": True,
                                               "p95_peak_gb": None, "adapter": "other", "ollama_names": (),
                                               "note": "config"}), **over, "id": cid}
                    base[cid] = merged
            need = int(self.cfg.get("min_samples", 10))
            for cid, p in base.items():
                rows = [r[0] for r in conn.execute("SELECT peak_gb FROM measurements WHERE consumer=?", (cid,))]
                cold = conn.execute("SELECT COUNT(*) FROM measurements WHERE consumer=? AND cold=1",
                                    (cid,)).fetchone()[0]
                p = dict(p)
                p["ollama_names"] = list(p.get("ollama_names") or ())
                p["samples"], p["cold_samples"] = len(rows), int(cold)
                if len(rows) >= need and cold >= 1:
                    p["p95_gb"], p["source"] = nearest_rank_p95(rows), "measured"
                else:
                    p["p95_gb"] = p.get("p95_peak_gb")
                    p["source"] = (f"provisional {len(rows)}/{need}" if p["p95_gb"] is not None else "unknown")
                out[cid] = p
            return out
        if c is not None:
            return build(c)
        with self._conn() as conn:
            return build(conn)

    def record_measurement(self, consumer: str, peak_gb: float, config_hash: str = "", cold: bool = False) -> dict:
        if not (0 < float(peak_gb) < 1024):
            raise LeaseError("bad_measurement", "peak_gb должен быть в (0, 1024)", 422)
        with self._tx() as c:
            if consumer not in self.profiles(c):
                raise LeaseError("unknown_consumer", f"неизвестный потребитель {consumer!r}", 422)
            c.execute("INSERT INTO measurements(consumer, config_hash, peak_gb, cold, ts) VALUES(?,?,?,?,?)",
                      (consumer, str(config_hash)[:128], float(peak_gb), int(bool(cold)), self.clock()))
            return self.profiles(c)[consumer]

    # -- requests -------------------------------------------------------------------------------------------------
    def _read_mem(self) -> float | None:
        if self.mem is None:
            return None
        try:
            v = self.mem()
            return None if v is None else float(v)
        except Exception:  # noqa: BLE001 - a broken reading means "unknown", the ledger check still applies
            log.warning("memory reading failed", exc_info=True)
            return None

    def _candidate(self, c: sqlite3.Connection, consumer: str, priority: str, holder: str, ttl_s: float | None,
                   pid: int | None, proc_create_time: float | None, key: str | None) -> tuple[Lease, str]:
        if priority not in PRIORITIES:
            raise LeaseError("bad_priority", f"приоритет {priority!r} не из {sorted(PRIORITIES)}", 422)
        prof = self.profiles(c).get(consumer)
        now = self.clock()
        ttl = float(ttl_s if ttl_s is not None else self.cfg["ttl_s"])
        ttl = min(max(ttl, 10.0), 86400.0)
        refuse = ""
        if prof is None:
            refuse = f"отказ: неизвестный потребитель {consumer!r} — нет профиля памяти"
        elif prof["p95_gb"] is None:
            refuse = f"отказ: у {consumer} нет замера P95 пика памяти ({prof['samples']} замеров) — запуск «на удачу» запрещён"
        elif self.cfg.get("strict_measured") and prof["source"] != "measured":
            refuse = f"отказ: профиль {consumer} ещё {prof['source']} (нужно ≥ {self.cfg['min_samples']} замеров)"
        lease = Lease(id=uuid.uuid4().hex, consumer=consumer, priority=priority,
                      kind=(prof or {}).get("kind", "compute"), peak_gb=float((prof or {}).get("p95_gb") or 0.0),
                      gpu_heavy=bool((prof or {}).get("gpu_heavy", True)), state="queued", created_at=now,
                      holder=str(holder or "")[:120], ttl_s=ttl, heartbeat_at=now, pid=pid,
                      proc_create_time=proc_create_time, idempotency_key=key)
        return lease, refuse

    def _keep_names(self, c: sqlite3.Connection, active: list[Lease], cand: Lease | None) -> set[str]:
        """Never unloaded: models of granted leases and of the candidate, ``keep_models`` from config, and - for work
        below a paid render - every registered resident chat model (background work never evicts the owner's Gemma)."""
        profs = self.profiles(c)
        keep: set[str] = {str(m) for m in (self.cfg.get("keep_models") or []) if isinstance(m, str)}
        for lease in active + ([cand] if cand else []):
            keep.update(profs.get(lease.consumer, {}).get("ollama_names") or ())
        if cand is not None and cand.rank < PRIORITIES["paid_render"]:
            for p in profs.values():
                if p.get("kind") == "resident":
                    keep.update(p.get("ollama_names") or ())
        return keep

    def _live_for(self, c: sqlite3.Connection, cand: Lease, avail: float | None) -> float | None:
        """A resident model that is ALREADY loaded (e.g. Gemma in Ollama) is part of the used memory: the live check
        must not ask for its size a second time (25.10 live probe: Gemma loaded, 22.6 GB free -> chat lease queued)."""
        if avail is None or cand.kind != "resident" or self.loaded is None:
            return avail
        names = self.profiles(c).get(cand.consumer, {}).get("ollama_names") or ()
        try:
            now = self.loaded() or []
        except Exception:  # noqa: BLE001
            return avail
        for m in now:
            if name_matches(m.get("name", ""), names):
                return avail + min(cand.peak_gb, float(m.get("size_gb") or cand.peak_gb))
        return avail

    def _unload_needed(self, consumer: str, priority: str, holder: str, ttl_s: float | None, pid: int | None,
                       proc_create_time: float | None, key: str | None, avail: float) -> set[str] | None:
        """Ollama names to KEEP when only the live-memory check blocks this request; None = no unload helps."""
        with self._conn() as c:
            if key and c.execute("SELECT 1 FROM leases WHERE idempotency_key=?", (key,)).fetchone():
                return None
            cand, refuse = self._candidate(c, consumer, priority, holder, ttl_s, pid, proc_create_time, key)
            if refuse:
                return None
            open_ = self._open(c)
            active = [x for x in open_ if x.state in ACTIVE]
            pre = plan(cand, active, self._live_for(c, cand, avail), self.cfg, [x for x in open_ if x.state == "queued"])
            if pre.decision == "grant" or not pre.live_blocked:
                return None
            return self._keep_names(c, [x for x in active if x.state == "granted"], cand)

    def request(self, consumer: str, priority: str, holder: str = "", *, idempotency_key: str | None = None,
                ttl_s: float | None = None, pid: int | None = None, proc_create_time: float | None = None) -> dict:
        key = (str(idempotency_key)[:200] or None) if idempotency_key else None
        avail = self._read_mem()
        # 1) read-only pre-plan: if only the live check fails, unload idle models OUTSIDE the write lock
        if self.unload is not None and avail is not None:
            keep = self._unload_needed(consumer, priority, holder, ttl_s, pid, proc_create_time, key, avail)
            if keep is not None:
                try:
                    res = self.unload(keep, float(self.cfg["unload_timeout_s"]))
                except Exception:  # noqa: BLE001
                    log.warning("unload hook failed", exc_info=True)
                    res = {"confirmed": False}
                if res.get("unloaded"):
                    avail = self._read_mem()
        # 2) the decision itself, atomically
        with self._tx() as c:
            if key:
                r = c.execute("SELECT * FROM leases WHERE idempotency_key=?", (key,)).fetchone()
                if r is not None:
                    return self._view(_row(r), idempotent=True)
            cand, refuse = self._candidate(c, consumer, priority, holder, ttl_s, pid, proc_create_time, key)
            if refuse:
                cand = replace(cand, state="refused", reason=refuse)
                self._insert(c, cand)
                self._event(c, cand.id, "refused", refuse)
                return self._view(cand)
            open_ = self._open(c)
            active = [x for x in open_ if x.state in ACTIVE]
            p = plan(cand, active, self._live_for(c, cand, avail), self.cfg,
                     [x for x in open_ if x.state == "queued"])
            for v in p.victims:
                self._preempt(c, v, f"вытеснено ради {_who(cand)}")
            if p.decision == "grant":
                cand = replace(cand, state="granted", token=self._meta_next(c, "fence"), epoch=self._meta(c, "epoch"),
                               reason="выдано")
                self._insert(c, cand)
                self._event(c, cand.id, "granted", f"{_who(cand)} holder={cand.holder}")
            else:                                           # the token is issued now and kept on promotion
                cand = replace(cand, reason=p.reason, token=self._meta_next(c, "fence"), epoch=self._meta(c, "epoch"))
                self._insert(c, cand)
                self._event(c, cand.id, "queued", p.reason)
            return self._view(cand)

    def _preempt(self, c: sqlite3.Connection, lease: Lease, reason: str) -> Lease:
        if lease.priority == "paid_render":                 # defence in depth: never, whatever the caller planned
            raise AssertionError("paid render must never be preempted")
        new = self._set(c, lease, state="preempting", reason=reason)
        self._event(c, lease.id, "preempted", reason)
        return new

    def _promote(self, c: sqlite3.Connection, avail: float | None) -> list[str]:
        promoted = []
        open_ = self._open(c)
        for q in queue_order([x for x in open_ if x.state == "queued"], self.clock(), self.cfg):
            now_open = self._open(c)
            active = [x for x in now_open if x.state in ACTIVE]
            p = plan(q, active, self._live_for(c, q, avail), self.cfg,
                     [x for x in now_open if x.state == "queued" and x.id != q.id])
            for v in p.victims:
                if v.state == "granted":
                    self._preempt(c, v, f"вытеснено ради {_who(q)}")
            if p.decision == "grant":
                g = self._set(c, q, state="granted", reason="выдано из очереди", heartbeat_at=self.clock())
                self._event(c, g.id, "granted", f"{_who(g)} из очереди")
                promoted.append(g.id)
                if avail is not None:
                    avail -= added_gb(g, active)
            elif p.reason != q.reason:
                self._set(c, q, reason=p.reason)
        return promoted

    def _check_token(self, lease: Lease, token: Any) -> None:
        try:
            ok = int(token) == lease.token and lease.token > 0
        except (TypeError, ValueError):
            ok = False
        if not ok:
            raise LeaseError("stale_token", "fencing: токен аренды не совпадает (устарел или чужой)", 409)

    def heartbeat(self, lease_id: str, token: Any) -> dict:
        with self._tx() as c:
            lease = self._get(c, lease_id)
            self._check_token(lease, token)
            if lease.state in TERMINAL:
                return self._view(lease)
            lease = self._set(c, lease, heartbeat_at=self.clock())
            return self._view(lease)

    def check_fence(self, lease_id: str, token: Any) -> bool:
        """A worker calls this right before starting heavy work: only a currently granted lease with its token passes."""
        with self._conn() as c:
            try:
                lease = self._get(c, lease_id)
                self._check_token(lease, token)
            except LeaseError:
                return False
            return lease.state == "granted"

    def release(self, lease_id: str, token: Any = None, *, force: bool = False, reason: str = "") -> dict:
        avail = self._read_mem()
        with self._tx() as c:
            lease = self._get(c, lease_id)
            if lease.state in TERMINAL:
                return self._view(lease)
            if not force:
                self._check_token(lease, token)
            lease = self._set(c, lease, state="released", reason=reason or ("снято владельцем" if force else "освобождено"))
            self._event(c, lease.id, "released", lease.reason)
            self._promote(c, avail)
            return self._view(lease)

    # -- governor tick: expiry, memory guard, queue promotion ---------------------------------------------------
    def tick(self, available_gb: float | None = None, *, stop: Callable[[Lease], bool] | None = None,
             probe: Callable[[int], dict | None] | None = None) -> dict:
        avail = available_gb if available_gb is not None else self._read_mem()
        out: dict[str, Any] = {"expired": [], "guard": None, "promoted": []}
        with self._tx() as c:
            now = self.clock()
            for lease in self._open(c):
                if now - lease.heartbeat_at > lease.ttl_s:
                    self._set(c, lease, state="expired", reason=f"нет heartbeat {int(now - lease.heartbeat_at)} с > TTL {int(lease.ttl_s)} с")
                    self._event(c, lease.id, "expired", "heartbeat timeout")
                    out["expired"].append(lease.id)
            victim = None
            if avail is not None and avail < float(self.cfg["guard_free_gb"]):
                granted = [x for x in self._open(c) if x.state == "granted" and x.priority != "paid_render"]
                if granted:
                    victim = sorted(granted, key=lambda x: (x.rank, -x.created_at))[0]
                    why = (f"предохранитель памяти: свободно {_gb(avail)} < {_gb(float(self.cfg['guard_free_gb']))}"
                           f" — остановлена самая низкая аренда {_who(victim)}")
                    victim = self._preempt(c, victim, why)
                    out["guard"] = {"lease_id": victim.id, "reason": why}
                else:
                    paid = [x.id for x in self._open(c) if x.state == "granted"]
                    why = f"предохранитель памяти: свободно {_gb(avail)}, но остались только платные рендеры — не трогаем"
                    self._event(c, None, "guard_skip", why)
                    out["guard"] = {"lease_id": None, "reason": why, "kept": paid}
            else:
                out["promoted"] = self._promote(c, avail)
        if victim is not None and stop is not None and victim.pid and self.cfg.get("guard_terminate", True):
            out["guard"]["terminated"] = self._stop_verified(victim, stop, probe)
        return out

    def _stop_verified(self, lease: Lease, stop: Callable[[Lease], bool], probe: Callable[[int], dict | None] | None) -> bool:
        if probe is None or lease.pid is None:
            return False
        info = probe(lease.pid)
        if not self._identity_ok(lease, info):
            with self._tx() as c:
                self._event(c, lease.id, "stop_skipped", "процесс без токена governor/другой create_time — не трогаем")
            return False
        ok = bool(stop(lease))
        with self._tx() as c:
            self._event(c, lease.id, "stopped" if ok else "stop_failed", f"pid {lease.pid}")
        return ok

    @staticmethod
    def _identity_ok(lease: Lease, info: dict | None) -> bool:
        if not info or lease.proc_create_time is None or info.get("create_time") is None:
            return False
        return (abs(float(info["create_time"]) - float(lease.proc_create_time)) < 1.0
                and info.get("env_token") == lease_env_token(lease.id, lease.token))

    def attach_process(self, lease_id: str, token: Any, pid: int, proc_create_time: float) -> dict:
        """The worker reports the process it started with ``BOSSMAN_RESOURCE_LEASE=<id>.<token>`` in its env."""
        with self._tx() as c:
            lease = self._get(c, lease_id)
            self._check_token(lease, token)
            if lease.state != "granted":
                raise LeaseError("not_granted", f"аренда в состоянии {lease.state}", 409)
            lease = self._set(c, lease, pid=int(pid), proc_create_time=float(proc_create_time))
            return self._view(lease)

    # -- restart recovery ----------------------------------------------------------------------------------------
    def recover(self, probe: Callable[[int], dict | None]) -> dict:
        out: dict[str, list[str]] = {"readopted": [], "expired": [], "released_foreign": [], "kept_no_pid": []}
        with self._tx() as c:
            epoch = self._meta_next(c, "epoch")
            self._event(c, None, "epoch", f"governor restart, epoch {epoch}")
            for lease in self._open(c):
                if lease.state == "queued" or lease.pid is None:
                    out["kept_no_pid"].append(lease.id)
                    continue
                info = probe(lease.pid)
                if info is None:
                    self._set(c, lease, state="expired", reason="после рестарта: процесс завершён")
                    self._event(c, lease.id, "expired", "process gone at restart")
                    out["expired"].append(lease.id)
                elif (info.get("env_readable") is False and lease.proc_create_time is not None
                      and info.get("create_time") is not None
                      and abs(float(info["create_time"]) - float(lease.proc_create_time)) < 1.0):
                    # state unchanged (a "preempting" flag would tell a paid render to stop): counted, untouched,
                    # it lives on only while its holder keeps heartbeating
                    self._set(c, lease, reason=lease.reason + "; после рестарта: процесс жив, токен не прочитать — "
                                                              "память учитывается, процесс не тронут")
                    self._event(c, lease.id, "unverified", "same create_time, env unreadable; counted, untouched")
                    out.setdefault("unverified", []).append(lease.id)
                elif self._identity_ok(lease, info):
                    self._set(c, lease, epoch=epoch, heartbeat_at=self.clock(),
                              reason=lease.reason + "; принято обратно после рестарта")
                    self._event(c, lease.id, "readopted", f"pid {lease.pid} holds the governor token")
                    out["readopted"].append(lease.id)
                else:
                    self._set(c, lease, state="released",
                              reason="после рестарта: pid занят другим процессом/нет токена — процесс не тронут")
                    self._event(c, lease.id, "released", "pid reuse or no token; process untouched")
                    out["released_foreign"].append(lease.id)
        return out

    # -- views -------------------------------------------------------------------------------------------------
    def _view(self, lease: Lease, idempotent: bool = False) -> dict:
        d = asdict(lease)
        d["priority_rank"] = lease.rank
        d["stop"] = lease.state == "preempting"
        d["env"] = {ENV_TOKEN: lease_env_token(lease.id, lease.token)} if lease.state == "granted" else {}
        if lease.state in TERMINAL:
            d.pop("token", None)
        if idempotent:
            d["idempotent_replay"] = True
        return d

    def get(self, lease_id: str) -> dict:
        with self._conn() as c:
            return self._view(self._get(c, lease_id))

    def active(self) -> list[Lease]:
        with self._conn() as c:
            return [x for x in self._open(c) if x.state in ACTIVE]

    def state(self, available_gb: float | None = None, events: int = 20) -> dict:
        avail = available_gb if available_gb is not None else self._read_mem()
        with self._conn() as c:
            open_ = self._open(c)
            active = [x for x in open_ if x.state in ACTIVE]
            evs = [dict(r) for r in c.execute("SELECT ts, lease_id, event, reason FROM events ORDER BY id DESC LIMIT ?",
                                               (int(events),))]
            profs = self.profiles(c)
            epoch = self._meta(c, "epoch")
        views = []
        for x in open_:
            v = self._view(x)
            v.pop("token", None)
            v.pop("env", None)
            views.append(v)
        return {
            "schema": "bossman.resources.state/1",
            "epoch": epoch,
            "budget_gb": float(self.cfg["budget_gb"]), "headroom_gb": float(self.cfg["headroom_gb"]),
            "guard_free_gb": float(self.cfg["guard_free_gb"]),
            "committed_gb": round(committed_gb(active), 2), "available_gb": avail,
            "heavy_gpu": [x.id for x in active if x.gpu_heavy and x.kind == "compute"],
            "leases": views,
            "queue": [{"id": q.id, "consumer": q.consumer, "priority": q.priority, "reason": q.reason}
                      for q in queue_order([x for x in open_ if x.state == "queued"], self.clock(), self.cfg)],
            "consumers": profs, "priorities": PRIORITIES, "events": evs,
        }
