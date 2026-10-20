"""Nightly backup of the owner's live SQLite databases.

    python tools/nightly_backup.py                  # back up, verify, prune (exit 0 PASS, 2 FAIL, 3 busy/locked)
    python tools/nightly_backup.py --dry-run        # show what would happen; writes nothing
    python tools/nightly_backup.py --restore-drill  # restore the newest PASS run into a temp dir and verify it

How a copy is made: the sqlite3 online backup API, from a READ-ONLY connection that
holds one read transaction. The table row counts and the page copy come from that
same snapshot, so the copy is consistent while the app keeps writing (WAL readers
never block writers). Live files are never copied byte-by-byte, never written,
never checkpointed. Each copy is switched to journal_mode=DELETE so it is one
self-contained file.

Output: ``<dest>/<YYYYMMDD-HHMM>/<name>.db`` + ``manifest.json`` (sha256, size,
row counts per table, integrity_check, source path, duration, status).

Verify: every copy is reopened read-only (immutable), ``PRAGMA integrity_check``
must be ``ok``, sha256 must match, the table list and row counts must equal the
source snapshot, and the exchange DB must still carry its append-only triggers.
Any failure -> status FAIL in the manifest and exit code 2.

Retention: the newest ``KEEP_RUNS`` run directories are kept; the newest PASS
run is never deleted, however old it is. Only this tool's own runs inside
``--dest`` are ever removed: a run-shaped name (``YYYYMMDD-HHMM[-N]``) AND a
manifest with ``"tool": "nightly_backup"`` (written when the run starts).

Owner priority: the process asks Windows for background IO/memory priority (best
effort), and refuses to start while a direct-gen job is active on the backend.
Backend unreachable -> nothing is generating -> proceed. Backend reachable but
the job list unreadable -> refuse (cannot prove it is idle), exit 3.

Restore instructions for the owner: docs/owner/BACKUPS_RU.md.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

TOOL = "nightly_backup"
TOOL_VERSION = 1
KEEP_RUNS = 14
MANIFEST = "manifest.json"
LOCK_NAME = ".nightly_backup.lock"
LOCK_STALE_S = 6 * 3600
RUN_RE = re.compile(r"^\d{8}-\d{4}(?:-\d+)?$")
DEFAULT_BACKEND = "http://127.0.0.1:8801"
#: direct-gen job states that are finished (command-center/bcc/direct_gen/service.py TERMINAL + aliases).
TERMINAL_JOB = frozenset({"completed", "done", "failed", "error", "cancelled", "canceled", "rejected", "expired"})

EXIT_OK, EXIT_FAIL, EXIT_BUSY = 0, 2, 3
PASS, FAIL, MISSING = "PASS", "FAIL", "MISSING"


@dataclass(frozen=True)
class Source:
    """One live database. ``path`` is a template: ``{data_dir}`` = the Command Center
    data dir, ``{bossman}`` = its parent (``%LOCALAPPDATA%\\Bossman``)."""
    name: str
    path: str
    triggers: tuple[str, ...] = ()        # must exist in the copy (append-only guarantees)


#: The owner's live SQLite stores. Paths come from the code that owns each store:
#:   bcc.db                    command-center/bcc/config.py (data_dir / "bcc.db")
#:   assistant.db              bossman_shared/exchange_assistant/store.py default_db()
#:   fx.db (FX rates journal)  bossman_shared/exchange_assistant/rates.py default_fx_db(),
#:                             apps/war-terminal/warterminal/fx_tracker.py open_store()
#:   companion.sqlite3         command-center/bcc/telegram_companion/store.py; the legacy
#:                             machine-global home (paths.legacy_home()) and the canonical
#:                             <data_dir>/telegram-companion home (paths.companion_home()).
#: A missing file is reported in the manifest, never fatal.
SOURCES: tuple[Source, ...] = (
    Source("bcc", "{data_dir}/bcc.db"),
    Source("exchange-assistant", "{data_dir}/exchange-assistant/assistant.db",
           triggers=("events_no_update", "events_no_delete", "deals_no_delete")),
    Source("fx", "{data_dir}/fx/fx.db"),
    Source("telegram-companion", "{bossman}/telegram-companion/companion.sqlite3"),
    Source("telegram-companion-home", "{data_dir}/telegram-companion/companion.sqlite3"),
)

JobsProbe = Callable[[], list]


class BackupError(Exception):
    pass


# ---------------------------------------------------------------- locations

def _localappdata() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))


def default_data_dir() -> Path:
    return _localappdata() / "Bossman" / "CommandCenter"


def default_dest() -> Path:
    return _localappdata() / "Bossman" / "Backups"


def resolve_sources(data_dir: Path, sources: Iterable[Source] = SOURCES) -> list[tuple[Source, Path]]:
    data_dir = Path(data_dir)
    out = []
    for src in sources:
        raw = src.path.format(data_dir=data_dir, bossman=data_dir.parent)
        out.append((src, Path(raw)))
    return out


# ---------------------------------------------------------------- sqlite helpers

def _ro_uri(path: Path, immutable: bool = False) -> str:
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    return uri + "&immutable=1" if immutable else uri


def _qi(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _table_counts(db: sqlite3.Connection) -> dict[str, int | None]:
    names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    counts: dict[str, int | None] = {}
    for name in names:
        try:
            counts[name] = db.execute(f"SELECT COUNT(*) FROM {_qi(name)}").fetchone()[0]
        except sqlite3.OperationalError:   # a virtual table whose module is not compiled in
            counts[name] = None
    return counts


def _triggers(db: sqlite3.Connection) -> set[str]:
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def online_copy(source: Path, target: Path) -> dict:
    """Snapshot ``source`` into ``target`` through the backup API.

    One read transaction on a read-only connection covers both the row counts and
    the page copy, so they describe the same snapshot. Returns {"tables": {...}}.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise BackupError(f"target already exists: {target}")
    src = sqlite3.connect(_ro_uri(source), uri=True, timeout=30, isolation_level=None)
    try:
        src.execute("BEGIN")
        try:
            tables = _table_counts(src)
            dst = sqlite3.connect(target, isolation_level=None)
            try:
                src.backup(dst)              # pages=-1: one step, inside our read transaction
                dst.execute("PRAGMA journal_mode=DELETE")
            finally:
                dst.close()
        finally:
            src.execute("COMMIT")
    finally:
        src.close()
    return {"tables": tables}


def verify_copy(path: Path, expected_tables: dict, *, sha256: str | None = None,
                triggers: Iterable[str] = ()) -> dict:
    """Read-only check of one copy. Returns {"ok": bool, "integrity_check": str, "errors": [...]}."""
    errors: list[str] = []
    integrity = "not_run"
    path = Path(path)
    if not path.is_file():
        return {"ok": False, "integrity_check": integrity, "errors": ["copy_missing"]}
    if sha256 is not None and sha256_file(path) != sha256:
        errors.append("sha256_mismatch")
    try:
        db = sqlite3.connect(_ro_uri(path, immutable=True), uri=True, timeout=5)
        try:
            rows = [r[0] for r in db.execute("PRAGMA integrity_check")]
            integrity = "ok" if rows == ["ok"] else "; ".join(map(str, rows[:5]))
            if integrity != "ok":
                errors.append("integrity_check_failed")
            got = _table_counts(db)
            if sorted(got) != sorted(expected_tables):
                errors.append("table_list_mismatch")
            diff = sorted(t for t in expected_tables if t in got and got[t] != expected_tables[t])
            if diff:
                errors.append("row_count_mismatch:" + ",".join(diff[:10]))
            missing_triggers = sorted(set(triggers) - _triggers(db))
            if missing_triggers:
                errors.append("append_only_triggers_missing:" + ",".join(missing_triggers))
        finally:
            db.close()
    except sqlite3.DatabaseError as exc:
        if integrity == "not_run":
            integrity = f"error: {exc}"
        errors.append(f"sqlite_error:{exc}")
    return {"ok": not errors, "integrity_check": integrity, "errors": errors}


# ---------------------------------------------------------------- owner priority

def lower_io_priority() -> str:
    """Background IO + memory priority for this process (Windows), nice on POSIX. Best effort."""
    try:
        if os.name == "nt":
            import ctypes
            kernel32 = ctypes.windll.kernel32
            PROCESS_MODE_BACKGROUND_BEGIN = 0x00100000
            if kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), PROCESS_MODE_BACKGROUND_BEGIN):
                return "background"
            return "unavailable"
        os.nice(10)
        return "nice10"
    except Exception:                                 # noqa: BLE001 - best effort only
        return "unavailable"


def http_jobs_probe(backend: str = DEFAULT_BACKEND, token_file: Path | None = None,
                    timeout: float = 5.0) -> JobsProbe:
    """GET <backend>/api/direct-gen/jobs as the owner. ConnectionError = backend down."""
    import urllib.error
    import urllib.request

    def probe() -> list:
        tf = token_file or (default_data_dir() / "token")
        try:
            token = Path(tf).read_text(encoding="utf-8").strip()
        except OSError:
            token = ""
        req = urllib.request.Request(backend.rstrip("/") + "/api/direct-gen/jobs",
                                     headers={"X-BCC-Token": token, "X-Participant": "owner"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code}") from None
        except (urllib.error.URLError, OSError) as exc:
            raise ConnectionError(type(exc).__name__) from None
        jobs = payload.get("jobs") if isinstance(payload, dict) else None
        if not isinstance(jobs, list):
            raise RuntimeError("unexpected /api/direct-gen/jobs payload")
        return jobs
    return probe


def active_jobs(probe: JobsProbe) -> dict:
    """{"busy": bool, "reason": str, "active": [ids]}. Unreachable backend is idle."""
    try:
        jobs = probe()
    except ConnectionError as exc:
        return {"busy": False, "reason": f"backend_unreachable:{exc}", "active": []}
    except Exception as exc:                          # noqa: BLE001 - unreadable queue is never "idle"
        return {"busy": True, "reason": f"jobs_unreadable:{type(exc).__name__}:{exc}", "active": []}
    active = [str(j.get("job_id") or j.get("id") or "?") for j in jobs or []
              if isinstance(j, dict) and str(j.get("status") or "").lower() not in TERMINAL_JOB]
    if active:
        return {"busy": True, "reason": "direct_gen_job_active", "active": active}
    return {"busy": False, "reason": "idle", "active": []}


# ---------------------------------------------------------------- runs, lock, retention

def _now() -> _dt.datetime:
    return _dt.datetime.now()


def _iso(ts: _dt.datetime) -> str:
    return ts.astimezone().isoformat(timespec="seconds")


def new_run_dir(dest: Path, now: _dt.datetime) -> Path:
    base = now.strftime("%Y%m%d-%H%M")
    if not (dest / base).exists():
        return dest / base
    serials = [int(p.name.rsplit("-", 1)[1]) for p in Path(dest).glob(base + "-*")
               if RUN_RE.match(p.name) and p.name.count("-") == 2]
    return dest / f"{base}-{max(serials + [1]) + 1}"


def read_manifest(run: Path) -> dict | None:
    try:
        data = json.loads((Path(run) / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def is_our_run(path: Path) -> bool:
    """A run directory made by this tool: run-shaped name AND our manifest (written at start)."""
    return (Path(path).is_dir() and bool(RUN_RE.match(Path(path).name))
            and (read_manifest(path) or {}).get("tool") == TOOL)


def list_runs(dest: Path) -> list[Path]:
    """This tool's run directories, oldest first. Anything else in <dest> is invisible to retention."""
    if not Path(dest).is_dir():
        return []

    def key(p: Path):
        parts = p.name.split("-")                     # YYYYMMDD, HHMM[, N]
        return (parts[0], parts[1], int(parts[2]) if len(parts) > 2 else 1)
    return sorted((p for p in Path(dest).iterdir() if is_our_run(p)), key=key)


def run_status(run: Path) -> str:
    manifest = read_manifest(run)
    return str((manifest or {}).get("status") or FAIL)


def newest_pass(dest: Path) -> Path | None:
    for run in reversed(list_runs(dest)):
        if run_status(run) == PASS:
            return run
    return None


def retention_plan(dest: Path, keep: int = KEEP_RUNS) -> dict:
    runs = list_runs(dest)
    kept = set(runs[-keep:]) if keep > 0 else set()
    protected = newest_pass(dest)
    if protected is not None:
        kept.add(protected)
    delete = [r for r in runs if r not in kept]
    return {"keep": [r.name for r in runs if r in kept], "delete": [r.name for r in delete],
            "protected_newest_pass": protected.name if protected else None}


def apply_retention(dest: Path, keep: int = KEEP_RUNS) -> dict:
    plan = retention_plan(dest, keep)
    deleted, errors = [], []
    root = Path(dest).resolve()
    for name in plan["delete"]:
        target = (Path(dest) / name).resolve()
        if target.parent != root or not RUN_RE.match(target.name):   # never leave <dest>
            errors.append(f"refused:{name}")
            continue
        try:
            shutil.rmtree(target)
            deleted.append(name)
        except OSError as exc:
            errors.append(f"{name}:{type(exc).__name__}")
    return {**plan, "deleted": deleted, "errors": errors}


class RunLock:
    """One backup at a time per destination (O_EXCL lock file, stale after LOCK_STALE_S)."""

    def __init__(self, dest: Path):
        self.path = Path(dest) / LOCK_NAME
        self.held = False

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > LOCK_STALE_S:
                        self.path.unlink()
                        continue
                except OSError:
                    pass
                return False
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                out.write(json.dumps({"pid": os.getpid(), "at": _iso(_now())}))
            self.held = True
            return True
        return False

    def release(self) -> None:
        if self.held:
            try:
                self.path.unlink()
            except OSError:
                pass
            self.held = False


# ---------------------------------------------------------------- the run

def _write_manifest(run: Path, manifest: dict) -> None:
    tmp = run / (MANIFEST + ".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, run / MANIFEST)


def backup_run(data_dir: Path, dest: Path, *, sources: Iterable[Source] = SOURCES,
               now: _dt.datetime | None = None, io_priority: str = "not_requested") -> tuple[Path, dict]:
    """Back up every present source into a new run dir, verify, write the manifest."""
    started = now or _now()
    t0 = time.monotonic()
    dest = Path(dest)
    run = new_run_dir(dest, started)
    run.mkdir(parents=True)
    # Claim the directory first: a run that crashes stays recognisable (status RUNNING = not PASS).
    _write_manifest(run, {"tool": TOOL, "tool_version": TOOL_VERSION, "status": "RUNNING",
                          "run": run.name, "started_at": _iso(started)})
    live ={os.path.normcase(str(p.resolve())) for _s, p in resolve_sources(data_dir, sources)}
    databases, missing = [], []
    for src, path in resolve_sources(data_dir, sources):
        entry: dict = {"name": src.name, "source": str(path)}
        if not path.is_file():
            entry["status"] = MISSING
            missing.append(src.name)
            databases.append(entry)
            continue
        target = run / f"{src.name}.db"
        if os.path.normcase(str(target.resolve())) in live:
            raise BackupError("destination resolves onto a live database")
        d0 = time.monotonic()
        try:
            snap = online_copy(path, target)
            entry.update({"file": target.name, "size": target.stat().st_size,
                          "sha256": sha256_file(target), "tables": snap["tables"]})
            check = verify_copy(target, snap["tables"], sha256=entry["sha256"], triggers=src.triggers)
            entry.update({"integrity_check": check["integrity_check"],
                          "status": PASS if check["ok"] else FAIL, "errors": check["errors"]})
            if src.triggers:
                entry["append_only_triggers"] = list(src.triggers)
        except Exception as exc:                      # noqa: BLE001 - one DB failing must not hide the others
            entry.update({"status": FAIL, "errors": [f"{type(exc).__name__}: {exc}"]})
        entry["duration_s"] = round(time.monotonic() - d0, 3)
        databases.append(entry)
    backed = [d for d in databases if d["status"] != MISSING]
    status = PASS if backed and all(d["status"] == PASS for d in backed) else FAIL
    manifest = {
        "tool": TOOL, "tool_version": TOOL_VERSION, "status": status,
        "run": run.name, "started_at": _iso(started), "finished_at": _iso(_now()),
        "duration_s": round(time.monotonic() - t0, 3), "data_dir": str(data_dir), "dest": str(dest),
        "io_priority": io_priority, "databases": databases, "missing": missing,
    }
    if not backed:
        manifest["errors"] = ["no_source_database_found"]
    _write_manifest(run, manifest)
    return run, manifest


def verify_run(run: Path, sources: Iterable[Source] = SOURCES) -> dict:
    """Re-verify a finished run from its manifest (used by the restore drill)."""
    manifest = read_manifest(run)
    if manifest is None:
        return {"ok": False, "run": Path(run).name, "results": [], "errors": ["manifest_unreadable"]}
    triggers = {s.name: s.triggers for s in sources}
    results = []
    for entry in manifest.get("databases", []):
        if entry.get("status") == MISSING:
            continue
        check = verify_copy(Path(run) / str(entry.get("file") or f"{entry['name']}.db"), entry.get("tables") or {},
                            sha256=entry.get("sha256"), triggers=triggers.get(entry["name"], ()))
        results.append({"name": entry["name"], **check})
    ok = bool(results) and all(r["ok"] for r in results) and manifest.get("status") == PASS
    return {"ok": ok, "run": Path(run).name, "results": results, "errors": [] if results else ["nothing_to_verify"]}


def restore_drill(dest: Path, sources: Iterable[Source] = SOURCES, *, keep_dir: bool = False,
                  live_paths: Iterable[Path] = ()) -> dict:
    """Restore the newest PASS run into a fresh temp dir and verify the restored files."""
    run = newest_pass(dest)
    if run is None:
        return {"ok": False, "errors": ["no_pass_run"], "results": []}
    manifest = read_manifest(run) or {}
    triggers = {s.name: s.triggers for s in sources}
    live = {os.path.normcase(str(Path(p).resolve())) for p in live_paths}
    workdir = Path(tempfile.mkdtemp(prefix="bossman-restore-drill-"))
    results = []
    try:
        for entry in manifest.get("databases", []):
            if entry.get("status") == MISSING:
                continue
            backup_file = run / str(entry.get("file") or f"{entry['name']}.db")
            target = workdir / f"{entry['name']}.db"
            if os.path.normcase(str(target.resolve())) in live:
                raise BackupError("restore target resolves onto a live database")
            item = {"name": entry["name"], "restored_to": str(target)}
            try:
                if entry.get("sha256") and sha256_file(backup_file) != entry["sha256"]:
                    raise BackupError("backup file sha256 differs from manifest")
                online_copy(backup_file, target)
                item.update(verify_copy(target, entry.get("tables") or {}, triggers=triggers.get(entry["name"], ())))
            except Exception as exc:                  # noqa: BLE001
                item.update({"ok": False, "errors": [f"{type(exc).__name__}: {exc}"]})
            results.append(item)
    finally:
        if not keep_dir:
            shutil.rmtree(workdir, ignore_errors=True)
    ok = bool(results) and all(r["ok"] for r in results)
    return {"ok": ok, "run": run.name, "workdir": str(workdir), "kept": keep_dir, "results": results,
            "errors": [] if results else ["nothing_to_restore"]}


# ---------------------------------------------------------------- CLI

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Nightly backup of the owner's live SQLite databases.")
    p.add_argument("--data-dir", type=Path, default=None, help="Command Center data dir (default %%LOCALAPPDATA%%\\Bossman\\CommandCenter)")
    p.add_argument("--dest", type=Path, default=None, help="backup root (default %%LOCALAPPDATA%%\\Bossman\\Backups)")
    p.add_argument("--keep", type=int, default=KEEP_RUNS, help="run directories to keep (newest PASS is always kept)")
    p.add_argument("--dry-run", action="store_true", help="show the plan; write nothing")
    p.add_argument("--restore-drill", action="store_true", help="restore the newest PASS run into a temp dir and verify")
    p.add_argument("--keep-drill-dir", action="store_true", help="leave the restore-drill temp dir in place")
    p.add_argument("--backend", default=DEFAULT_BACKEND, help="Bossman backend for the direct-gen idle check")
    p.add_argument("--token-file", type=Path, default=None)
    return p


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def main(argv: list[str] | None = None, *, jobs_probe: JobsProbe | None = None,
         sources: Iterable[Source] = SOURCES) -> int:
    args = _parser().parse_args(argv)
    data_dir = Path(args.data_dir) if args.data_dir else default_data_dir()
    dest = Path(args.dest) if args.dest else default_dest()
    resolved = resolve_sources(data_dir, sources)
    live_paths = [p for _s, p in resolved]

    if args.restore_drill:
        report = restore_drill(dest, sources, keep_dir=args.keep_drill_dir, live_paths=live_paths)
        _emit({"mode": "restore-drill", "status": PASS if report["ok"] else FAIL, **report})
        return EXIT_OK if report["ok"] else EXIT_FAIL

    probe = jobs_probe or http_jobs_probe(args.backend, args.token_file or (data_dir / "token"))
    idle = active_jobs(probe)

    if args.dry_run:
        def _size(path: Path):
            return path.stat().st_size if path.is_file() else None
        plan = [{"name": s.name, "source": str(p), "exists": p.is_file(), "size": _size(p),
                 "wal_size": _size(Path(str(p) + "-wal"))} for s, p in resolved]
        _emit({"mode": "dry-run", "data_dir": str(data_dir), "dest": str(dest),
               "would_create": str(new_run_dir(dest, _now())), "sources": plan,
               "direct_gen": idle, "would_refuse": idle["busy"],
               "retention": retention_plan(dest, args.keep)})
        return EXIT_OK

    if idle["busy"]:
        _emit({"mode": "backup", "status": "SKIPPED_BUSY", "direct_gen": idle})
        return EXIT_BUSY

    lock = RunLock(dest)
    if not lock.acquire():
        _emit({"mode": "backup", "status": "SKIPPED_LOCKED", "lock": str(lock.path)})
        return EXIT_BUSY
    try:
        priority = lower_io_priority()
        run, manifest = backup_run(data_dir, dest, sources=sources, io_priority=priority)
        retention = apply_retention(dest, args.keep)
    finally:
        lock.release()
    _emit({"mode": "backup", "status": manifest["status"], "run": str(run), "direct_gen": idle,
           "databases": [{k: d.get(k) for k in ("name", "status", "size", "errors")} for d in manifest["databases"]],
           "retention": retention})
    return EXIT_OK if manifest["status"] == PASS else EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
