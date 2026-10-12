"""On-disk project store for the motion workflow: versions, assets, render jobs, locks, recovery.

Layout under ``<data_root>/motion-workflow/``::

    <project_id>/
        project.json            the current plan (always a copy of the newest version's plan)
        versions/0001.json      immutable version records: plan + parent + author + ts + diff summary
        assets/<sha256>.<ext>   copied, hashed, content-addressed (never deleted by an edit)
        jobs/<job_id>.json      render jobs with the state machine below
        .lock                   cross-process lock, held only for the few milliseconds of a write

Guarantees (and their limits):

  * every write is tmp file + fsync + ``os.replace``; a version file is written BEFORE project.json, and a
    crash in between is rolled forward on the next open, so the version log is the source of truth;
  * version files are never rewritten; each carries the sha256 of its plan and is verified on read;
  * every project id, job id, asset hash and reference name is validated as a single path component and
    the resolved path must stay under the store root;
  * a job found RUNNING at startup becomes INTERRUPTED with the last completed scene recorded, and resume
    continues from the next scene; the job stays bound to the plan version it was created from;
  * the cross-process lock is best-effort (lock file + stale detection); the supported topology is ONE
    backend process per data root, with any number of threads.

No network, no paid call: the store only reads and writes local files.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterable

from . import analyze as analyze_mod
from . import plan as planmod
from .plan import LIMITS, LimitError, PlanError

JOB_SCHEMA_VERSION = 1
JOB_STATES = ("PLANNED", "PREVIEW_RUNNING", "PREVIEW_DONE", "RENDER_RUNNING", "RENDER_DONE", "FAILED",
              "INTERRUPTED")
RUNNING_STATES = ("PREVIEW_RUNNING", "RENDER_RUNNING")
TERMINAL_STATES = ("RENDER_DONE", "FAILED")
TRANSITIONS: dict[str, set[str]] = {
    "PLANNED": {"PREVIEW_RUNNING", "FAILED"},
    "PREVIEW_RUNNING": {"PREVIEW_DONE", "FAILED", "INTERRUPTED"},
    "PREVIEW_DONE": {"RENDER_RUNNING", "FAILED"},
    "RENDER_RUNNING": {"RENDER_DONE", "FAILED", "INTERRUPTED"},
    "INTERRUPTED": {"PREVIEW_RUNNING", "RENDER_RUNNING", "FAILED"},
    "RENDER_DONE": set(),
    "FAILED": set(),
}
_PHASE_OF_RUNNING = {"PREVIEW_RUNNING": "preview", "RENDER_RUNNING": "render"}
_RUNNING_OF_PHASE = {"preview": "PREVIEW_RUNNING", "render": "RENDER_RUNNING"}

LOCK_TIMEOUT_SECONDS = 10.0
LOCK_STALE_SECONDS = 30.0
TMP_MAX_AGE_SECONDS = 300.0
COPY_CHUNK = 1 << 20
SUBDIR = "motion-workflow"

_JOB_ID_RE = re.compile(r"^job_[0-9a-f]{12}$")
_VERSION_FILE_RE = re.compile(r"^(\d{4})\.json$")
_AUTHOR_RE = re.compile(r"^[^\x00-\x1f]{1,64}$")

_IMAGE_MAGIC = {
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".bmp": (b"BM",),
}


class StoreError(RuntimeError):
    """Base class of every store failure."""


class PathEscapeError(StoreError, ValueError):
    """An identifier or path would leave the project / store directory."""


class ProjectNotFound(StoreError, LookupError):
    """No such project (or version, or job)."""


class ConflictError(StoreError):
    """The project changed since the caller read it (``expected_version`` mismatch) or already exists."""


class IntegrityError(StoreError):
    """A stored file does not match its recorded hash or the version log is inconsistent."""


class StoreBusyError(StoreError):
    """The project lock could not be taken in time."""


class JobStateError(StoreError):
    """A job transition or scene completion that the state machine does not allow."""


# --------------------------------------------------------------------------------------------- files

def _dumps(obj: Any) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _replace_with_retry(src: Path, dst: Path, attempts: int = 12) -> None:
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.02 * (attempt + 1))     # Windows: a reader or scanner briefly holds the target


def _fsync_dir(path: Path) -> None:
    if os.name == "nt":
        return
    with contextlib.suppress(OSError):
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(tmp, path)
        _fsync_dir(path.parent)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def atomic_write_json(path: Path, obj: Any) -> None:
    atomic_write_bytes(path, _dumps(obj))


def _read_json(path: Path) -> Any:
    try:
        with open(path, "rb") as handle:
            return json.loads(handle.read().decode("utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, ValueError) as exc:
        raise IntegrityError(f"{path.name} is unreadable: {exc}") from exc


# --------------------------------------------------------------------------------------------- locks

def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5                     # access denied: it exists
        try:
            code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code)):
                return code.value == 259                            # STILL_ACTIVE
            return True
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class _LockEntry:
    def __init__(self) -> None:
        self.rlock = threading.RLock()
        self.depth = 0
        self.token: str | None = None


_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, _LockEntry] = {}


def _lock_entry(key: str) -> _LockEntry:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, _LockEntry())


def _lock_is_stale(path: Path, stale_seconds: float) -> bool:
    try:
        age = time.time() - path.stat().st_mtime
    except FileNotFoundError:
        return True
    pid = 0
    with contextlib.suppress(OSError, ValueError, TypeError):
        pid = int(json.loads(path.read_text(encoding="utf-8")).get("pid", 0))
    if pid == os.getpid():
        return True      # the in-process rlock is held by us, so nobody in this process owns the file
    if pid and not _pid_alive(pid):
        return True
    return age > stale_seconds


@contextlib.contextmanager
def _project_lock(path: Path, timeout: float, stale: float):
    entry = _lock_entry(str(path))
    if not entry.rlock.acquire(timeout=timeout):
        raise StoreBusyError(f"project lock busy (in this process): {path.parent.name}")
    try:
        if entry.depth == 0:
            deadline = time.monotonic() + timeout
            token = uuid.uuid4().hex
            while True:
                try:
                    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                except FileExistsError:
                    if _lock_is_stale(path, stale):
                        with contextlib.suppress(OSError):
                            path.unlink()
                        continue
                    if time.monotonic() >= deadline:
                        raise StoreBusyError(f"project lock busy: {path.parent.name}") from None
                    time.sleep(0.02)
                    continue
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump({"pid": os.getpid(), "token": token, "ts": time.time()}, handle)
                entry.token = token
                break
        entry.depth += 1
        try:
            yield
        finally:
            entry.depth -= 1
            if entry.depth == 0:
                with contextlib.suppress(OSError, ValueError):
                    owner = json.loads(path.read_text(encoding="utf-8")).get("token")
                    if owner == entry.token:
                        path.unlink()
                entry.token = None
    finally:
        entry.rlock.release()


# --------------------------------------------------------------------------------------------- store

def _safe(value: Any, what: str) -> str:
    try:
        return planmod.safe_id(value, what)
    except PlanError as exc:
        raise PathEscapeError(str(exc)) from None


def _sniff_ok(ext: str, head: bytes) -> bool:
    if ext == ".webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    magics = _IMAGE_MAGIC.get(ext)
    return True if magics is None else any(head.startswith(m) for m in magics)


class Store:
    """Projects under ``<data_root>/motion-workflow``.  One live instance per data root is supported."""

    def __init__(self, data_root: str | os.PathLike, *, recover: bool = True,
                 allowed_import_roots: Iterable[str | os.PathLike] | None = None,
                 clock: Callable[[], str] | None = None, lock_timeout: float = LOCK_TIMEOUT_SECONDS,
                 lock_stale: float = LOCK_STALE_SECONDS) -> None:
        base = Path(data_root) / SUBDIR
        base.mkdir(parents=True, exist_ok=True)
        self.root = base.resolve()
        self._now = clock or planmod.now_iso
        self._lock_timeout = lock_timeout
        self._lock_stale = lock_stale
        self._import_roots = [Path(p).resolve() for p in allowed_import_roots] if allowed_import_roots else None
        self.recovery_report: dict[str, Any] = {}
        if recover:
            self.recover()

    # ------------------------------------------------------------------ paths and locks

    def _pdir(self, project_id: Any, *, must_exist: bool = True) -> Path:
        pid = _safe(project_id, "project_id")
        path = self.root / pid
        if path.resolve().parent != self.root:
            raise PathEscapeError(f"project path escapes the store: {pid}")
        if must_exist and not (path / "project.json").is_file() and not (path / "versions").is_dir():
            raise ProjectNotFound(f"no such project: {pid}")
        return path

    def _lock(self, pdir: Path):
        return _project_lock(pdir / ".lock", self._lock_timeout, self._lock_stale)

    @staticmethod
    def _author(author: Any) -> str:
        if not isinstance(author, str) or not _AUTHOR_RE.match(author):
            raise PlanError("author must be a 1-64 character string without control characters")
        return author

    # ------------------------------------------------------------------ versions

    @staticmethod
    def _version_numbers(pdir: Path) -> list[int]:
        folder = pdir / "versions"
        if not folder.is_dir():
            return []
        return sorted(int(m.group(1)) for m in (_VERSION_FILE_RE.match(n.name) for n in folder.iterdir()) if m)

    def _load_record(self, pdir: Path, version: int) -> dict[str, Any]:
        path = pdir / "versions" / f"{version:04d}.json"
        try:
            record = _read_json(path)
        except FileNotFoundError:
            raise ProjectNotFound(f"no version {version} in project {pdir.name}") from None
        if not isinstance(record, dict) or record.get("version") != version or not isinstance(record.get("plan"), dict):
            raise IntegrityError(f"version {version:04d} of {pdir.name} is malformed")
        if planmod.content_sha256(record["plan"]) != record.get("content_sha256"):
            raise IntegrityError(f"version {version:04d} of {pdir.name} does not match its recorded hash")
        return record

    def _write_version(self, pdir: Path, plan: dict[str, Any], *, parent: int | None, author: str,
                       summary: str, diff: dict[str, Any], revert_of: int | None) -> dict[str, Any]:
        numbers = self._version_numbers(pdir)
        version = (numbers[-1] + 1) if numbers else 1
        if version > LIMITS["max_versions"]:
            raise LimitError(f"project already has {version - 1} versions; declared limit "
                             f"{LIMITS['max_versions']}")
        plan["version"] = version
        plan["parent_version"] = parent
        record = {
            "version": version, "parent": parent, "author": author, "ts": self._now(), "summary": summary,
            "revert_of": revert_of, "diff": diff, "content_sha256": planmod.content_sha256(plan), "plan": plan,
        }
        path = pdir / "versions" / f"{version:04d}.json"
        if path.exists():
            raise IntegrityError(f"version file already exists, refusing to overwrite: {path.name}")
        atomic_write_json(path, record)
        atomic_write_json(pdir / "project.json", plan)
        return record

    def _current(self, pdir: Path) -> dict[str, Any]:
        """The current plan; rolls the project forward when a crash left project.json behind the log."""
        numbers = self._version_numbers(pdir)
        if not numbers:
            raise ProjectNotFound(f"project {pdir.name} has no versions")
        head = numbers[-1]
        current: dict[str, Any] | None = None
        with contextlib.suppress(FileNotFoundError, IntegrityError):
            loaded = _read_json(pdir / "project.json")
            if isinstance(loaded, dict):
                current = loaded
        if current is not None and current.get("version") == head:
            return current
        if current is not None and isinstance(current.get("version"), int) and current["version"] > head:
            raise IntegrityError(f"project.json of {pdir.name} is ahead of the version log")
        record = None
        for number in reversed(numbers):
            try:
                record = self._load_record(pdir, number)
                break
            except IntegrityError:
                continue
        if record is None:
            raise IntegrityError(f"project {pdir.name} has no intact version")
        with self._lock(pdir):
            atomic_write_json(pdir / "project.json", record["plan"])
        return record["plan"]

    # ------------------------------------------------------------------ projects

    def create_project(self, *, title: str = "", category: str = "motion", mode: str = "own_video",
                       author: str = "owner", project_id: str | None = None) -> dict[str, Any]:
        author = self._author(author)
        plan = planmod.new_plan(title=title, category=category, mode=mode, project_id=project_id,
                                now=self._now())
        pdir = self._pdir(plan["project_id"], must_exist=False)
        try:
            pdir.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            raise ConflictError(f"project already exists: {plan['project_id']}") from None
        for sub in ("versions", "assets", "jobs"):
            (pdir / sub).mkdir()
        with self._lock(pdir):
            planmod.validate(plan)
            self._write_version(pdir, plan, parent=None, author=author, summary="project created",
                                diff=planmod.diff(None, plan), revert_of=None)
        return copy.deepcopy(plan)

    def list_projects(self) -> list[dict[str, Any]]:
        out = []
        for child in sorted(self.root.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            with contextlib.suppress(StoreError, FileNotFoundError, PlanError, OSError):
                out.append(planmod.summary(self._current(child)))
        return out

    def get_project(self, project_id: str) -> dict[str, Any]:
        return copy.deepcopy(self._current(self._pdir(project_id)))

    def history(self, project_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """Newest first; each entry is the version record without the plan body."""
        pdir = self._pdir(project_id)
        out = []
        for number in reversed(self._version_numbers(pdir)[-max(1, limit):]):
            try:
                record = self._load_record(pdir, number)
            except IntegrityError as exc:
                out.append({"version": number, "integrity": f"BROKEN: {exc}"})
                continue
            out.append({k: v for k, v in record.items() if k != "plan"})
        return out

    def get_version(self, project_id: str, version: int) -> dict[str, Any]:
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise PathEscapeError(f"version must be a positive integer: {version!r}")
        return self._load_record(self._pdir(project_id), version)

    def verify(self, project_id: str) -> list[str]:
        """Full integrity check of the version log; returns problems (empty = intact)."""
        pdir = self._pdir(project_id)
        found: list[str] = []
        previous = 0
        for number in self._version_numbers(pdir):
            try:
                record = self._load_record(pdir, number)
            except IntegrityError as exc:
                found.append(str(exc))
                continue
            if number != previous + 1:
                found.append(f"version numbers are not contiguous at {number:04d}")
            expected_parent = previous if previous else None
            if record.get("parent") != expected_parent:
                found.append(f"version {number:04d} has parent {record.get('parent')}, expected {expected_parent}")
            previous = number
        try:
            current = self._current(pdir)
            if current.get("version") != previous:
                found.append("project.json does not match the newest version")
        except StoreError as exc:
            found.append(str(exc))
        return found

    def update(self, project_id: str, mutate: Callable[[dict[str, Any]], dict[str, Any] | None], *,
               author: str = "owner", summary: str | None = None, expected_version: int | None = None,
               revert_of: int | None = None) -> dict[str, Any]:
        """Apply ``mutate`` to a copy of the current plan and commit it as a new immutable version.

        Returns ``{"version", "parent", "changed", "summary", "diff", "plan"}``.  An edit that changes
        nothing creates no version (``changed`` is false)."""
        author = self._author(author)
        pdir = self._pdir(project_id)
        with self._lock(pdir):
            current = self._current(pdir)
            if expected_version is not None and current.get("version") != expected_version:
                raise ConflictError(f"project is at version {current.get('version')}, "
                                    f"caller expected {expected_version}")
            draft = copy.deepcopy(current)
            returned = mutate(draft)
            new_plan = returned if isinstance(returned, dict) else draft
            if new_plan.get("project_id") != current.get("project_id"):
                raise PlanError("project_id cannot be changed")
            planmod.validate(new_plan)
            if planmod.content_sha256(new_plan) == planmod.content_sha256(current):
                return {"version": current["version"], "parent": current.get("parent_version"),
                        "changed": False, "summary": "no content change", "diff": planmod.diff(current, current),
                        "plan": copy.deepcopy(current)}
            delta = planmod.diff(current, new_plan)
            record = self._write_version(pdir, new_plan, parent=current["version"], author=author,
                                         summary=(summary or delta["summary"])[:400], diff=delta,
                                         revert_of=revert_of)
        return {"version": record["version"], "parent": record["parent"], "changed": True,
                "summary": record["summary"], "diff": record["diff"], "plan": copy.deepcopy(record["plan"])}

    def edit(self, project_id: str, ops: list[dict[str, Any]], *, author: str = "owner",
             summary: str | None = None, expected_version: int | None = None) -> dict[str, Any]:
        author = self._author(author)
        return self.update(project_id, lambda plan: planmod.apply_ops(plan, ops, author=author, now=self._now()),
                           author=author, summary=summary, expected_version=expected_version)

    def revert(self, project_id: str, to_version: int, *, author: str = "owner") -> dict[str, Any]:
        """Create a NEW version whose plan equals ``to_version``; history is append-only."""
        author = self._author(author)
        target = self.get_version(project_id, to_version)

        def mutate(plan: dict[str, Any]) -> dict[str, Any]:
            restored = copy.deepcopy(target["plan"])
            restored["updated_ts"] = self._now()
            return restored

        result = self.update(project_id, mutate, author=author, summary=f"revert to version {to_version}",
                             revert_of=to_version)
        return result

    # ------------------------------------------------------------------ assets and references

    def _check_import_path(self, src: Path) -> Path:
        if not src.is_file():
            raise StoreError(f"not a file: {src}")
        if self._import_roots is not None:
            resolved = src.resolve()
            if not any(resolved == r or r in resolved.parents for r in self._import_roots):
                raise PathEscapeError(f"import path is outside the allowed roots: {src}")
        return src

    def _ingest_stream(self, pdir: Path, stream: BinaryIO, ext: str, max_bytes: int, *,
                       sniff: bool = False) -> dict[str, Any]:
        assets = pdir / "assets"
        assets.mkdir(exist_ok=True)
        tmp = assets / f".incoming.{uuid.uuid4().hex[:10]}.tmp"
        digest = hashlib.sha256()
        total = 0
        head = b""
        try:
            with open(tmp, "wb") as out:
                while True:
                    block = stream.read(COPY_CHUNK)
                    if not block:
                        break
                    if total == 0:
                        head = block[:16]
                    total += len(block)
                    if total > max_bytes:
                        raise LimitError(f"file is larger than the declared limit of {max_bytes} bytes")
                    digest.update(block)
                    out.write(block)
                out.flush()
                os.fsync(out.fileno())
            if total == 0:
                raise PlanError("file is empty")
            if sniff and not _sniff_ok(ext, head):
                raise PlanError(f"file content does not look like a {ext} image")
            sha = digest.hexdigest()
            final = assets / f"{sha}{ext}"
            with self._lock(pdir):
                created = not final.exists()
                if created:
                    _replace_with_retry(tmp, final)
                else:
                    tmp.unlink()
            return {"sha256": sha, "ext": ext, "bytes": total, "path": final, "created": created}
        except BaseException:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise

    def _ingest(self, pdir: Path, src: Path, allowed: tuple[str, ...], max_bytes: int, *,
                sniff: bool = False) -> dict[str, Any]:
        src = self._check_import_path(Path(src))
        ext = src.suffix.lower()
        if ext not in allowed:
            raise PlanError(f"extension {ext!r} is not allowed here; allowed: {allowed}")
        if src.stat().st_size > max_bytes:
            raise LimitError(f"{src.name} is {src.stat().st_size} bytes, declared limit {max_bytes}")
        with open(src, "rb") as handle:
            info = self._ingest_stream(pdir, handle, ext, max_bytes, sniff=sniff)
        info["original_name"] = src.name
        return info

    def asset_path(self, project_id: str, sha256: str, ext: str | None = None) -> Path:
        pdir = self._pdir(project_id)
        if not planmod.is_sha256(sha256):
            raise PathEscapeError(f"not a sha256: {str(sha256)[:80]!r}")
        assets = (pdir / "assets").resolve()
        matches = sorted(assets.glob(f"{sha256}.*"))
        if ext is not None:
            matches = [m for m in matches if m.suffix.lower() == ext.lower()]
        if not matches or matches[0].resolve().parent != assets:
            raise ProjectNotFound(f"no asset {sha256[:12]} in project {pdir.name}")
        return matches[0]

    def _add_reference(self, project_id: str, name: str, kind: str, opener: Callable[[Path], dict[str, Any]],
                       *, author: str, replace: bool, summary: str | None) -> dict[str, Any]:
        if not isinstance(name, str) or not re.match(r"^[A-Za-z][A-Za-z0-9_]{0,31}$", name):
            raise PlanError(f"reference name must match [A-Za-z][A-Za-z0-9_]{{0,31}}: {str(name)[:40]!r}")
        if kind not in planmod.REFERENCE_KINDS:
            raise PlanError(f"kind must be one of {planmod.REFERENCE_KINDS}: {kind!r}")
        pdir = self._pdir(project_id)
        current = self._current(pdir)
        refs = current["references"]
        if name in refs and not replace:
            raise PlanError(f"reference @{name} already exists (pass replace=True to rebind it)")
        if name not in refs and len(refs) >= LIMITS["max_references"]:
            raise LimitError(f"project already has {len(refs)} references; declared limit "
                             f"{LIMITS['max_references']}")
        info = opener(pdir)
        op = {"op": "add_reference", "name": name, "kind": kind, "sha256": info["sha256"], "ext": info["ext"],
              "bytes": info["bytes"], "original_name": info.get("original_name"), "replace": replace}
        try:
            return self.edit(project_id, [op], author=author,
                             summary=summary or f"bind @{name} ({kind}) to {info['sha256'][:12]}")
        except BaseException:
            if info.get("created"):
                with contextlib.suppress(OSError, StoreError):
                    live = self._current(pdir)["references"]
                    if all(r.get("sha256") != info["sha256"] for r in live.values()):
                        info["path"].unlink()
            raise

    def add_reference(self, project_id: str, name: str, kind: str, src: str | os.PathLike, *,
                      author: str = "owner", replace: bool = False) -> dict[str, Any]:
        """Copy a file into the project's assets and bind it as ``@name`` of the given kind."""
        if kind not in planmod.REFERENCE_KINDS:
            raise PlanError(f"kind must be one of {planmod.REFERENCE_KINDS}: {kind!r}")
        cap = LIMITS["max_reference_bytes"][planmod.KIND_SIZE_CLASS[kind]]

        def opener(pdir: Path) -> dict[str, Any]:
            info = self._ingest(pdir, Path(src), planmod.KIND_EXTENSIONS[kind], cap,
                                sniff=planmod.KIND_SIZE_CLASS[kind] == "image")
            if kind == "text":
                try:
                    body = info["path"].read_bytes().decode("utf-8")
                    if "\x00" in body or not body.strip():
                        raise ValueError("empty or contains NUL")
                except (OSError, ValueError) as exc:
                    if info.get("created"):
                        with contextlib.suppress(OSError):
                            info["path"].unlink()
                    raise PlanError(f"text reference must be non-empty UTF-8 text: {exc}") from None
            return info

        return self._add_reference(project_id, name, kind, opener, author=self._author(author),
                                   replace=replace, summary=None)

    def add_text_reference(self, project_id: str, name: str, text: str, *, author: str = "owner",
                           replace: bool = False) -> dict[str, Any]:
        if not isinstance(text, str) or not text.strip() or "\x00" in text:
            raise PlanError("text reference must be a non-empty string without NUL")
        data = text.encode("utf-8")

        def opener(pdir: Path) -> dict[str, Any]:
            info = self._ingest_stream(pdir, io.BytesIO(data), ".txt", LIMITS["max_reference_bytes"]["text"])
            info["original_name"] = None
            return info

        return self._add_reference(project_id, name, "text", opener, author=self._author(author),
                                   replace=replace, summary=None)

    def remove_reference(self, project_id: str, name: str, *, author: str = "owner") -> dict[str, Any]:
        return self.edit(project_id, [{"op": "remove_reference", "name": name}], author=author,
                         summary=f"remove reference @{name}")

    def analyze_source(self, project_id: str, video_path: str | os.PathLike, *, author: str = "owner",
                       **analyze_kwargs: Any) -> dict[str, Any]:
        """Copy the video into the project, analyze it, and commit the result as a new version."""
        author = self._author(author)
        pdir = self._pdir(project_id)
        current = self._current(pdir)
        info = self._ingest(pdir, Path(video_path), planmod.VIDEO_EXTENSIONS, LIMITS["max_source_bytes"])
        analyzed = analyze_mod.analyze_video(
            info["path"], project_id=current["project_id"], title=current.get("title", ""),
            category=current["category"], mode=current["mode"], source_sha256=info["sha256"], now=self._now(),
            **analyze_kwargs)
        analyzed["source"]["file_name"] = info["original_name"]
        count = len(analyzed["scenes"])
        threshold = analyzed["analysis"]["scene_detection"]["threshold"]
        return self.update(project_id, lambda plan: planmod.attach_analysis(plan, analyzed, now=self._now()),
                           author=author, summary=f"analysis: {count} scene(s), scene threshold {threshold}")

    # ------------------------------------------------------------------ derived views

    def estimate(self, project_id: str) -> dict[str, Any]:
        return planmod.estimate(self._current(self._pdir(project_id)))

    def readiness(self, project_id: str) -> dict[str, Any]:
        return planmod.readiness(self._current(self._pdir(project_id)))

    def limits(self, project_id: str) -> dict[str, Any]:
        return planmod.limits_report(self._current(self._pdir(project_id)))

    # ------------------------------------------------------------------ jobs

    @staticmethod
    def _job_id(job_id: Any) -> str:
        if not isinstance(job_id, str) or not _JOB_ID_RE.match(job_id):
            raise PathEscapeError(f"job id must match {_JOB_ID_RE.pattern}: {str(job_id)[:60]!r}")
        return job_id

    def _job_path(self, pdir: Path, job_id: Any) -> Path:
        path = pdir / "jobs" / f"{self._job_id(job_id)}.json"
        if path.resolve().parent != (pdir / "jobs").resolve():
            raise PathEscapeError("job path escapes the project")
        return path

    def _read_job(self, pdir: Path, job_id: str) -> dict[str, Any]:
        try:
            job = _read_json(self._job_path(pdir, job_id))
        except FileNotFoundError:
            raise ProjectNotFound(f"no job {job_id} in project {pdir.name}") from None
        if not isinstance(job, dict) or job.get("state") not in JOB_STATES:
            raise IntegrityError(f"job {job_id} is malformed")
        return job

    def _save_job(self, pdir: Path, job: dict[str, Any]) -> None:
        job["updated_ts"] = self._now()
        atomic_write_json(self._job_path(pdir, job["job_id"]), job)

    @staticmethod
    def _next_scene(job: dict[str, Any]) -> str | None:
        phase = job.get("phase")
        if phase is None:
            return job["scene_ids"][0] if job["scene_ids"] else None
        done = set(job["phases"][phase]["completed"])
        for sid in job["scene_ids"]:
            if sid not in done:
                return sid
        return None

    def create_job(self, project_id: str, *, author: str = "owner") -> dict[str, Any]:
        """A PLANNED job bound to the current plan version.  Refused while the plan has blockers."""
        author = self._author(author)
        pdir = self._pdir(project_id)
        with self._lock(pdir):
            plan = self._current(pdir)
            ready = planmod.readiness(plan)
            if not ready["ready"]:
                raise PlanError("plan is not ready for a run: " + "; ".join(ready["blockers"][:6]),
                                ready["blockers"])
            now = self._now()
            job = {
                "schema_version": JOB_SCHEMA_VERSION, "job_id": "job_" + uuid.uuid4().hex[:12],
                "project_id": plan["project_id"], "state": "PLANNED", "created_ts": now, "updated_ts": now,
                "author": author, "plan_version": plan["version"],
                "plan_content_sha256": planmod.content_sha256(plan),
                "scene_ids": [s["id"] for s in plan["scenes"]], "phase": None,
                "phases": {"preview": {"completed": [], "outputs": {}}, "render": {"completed": [], "outputs": {}}},
                "last_completed_scene": None, "next_scene": plan["scenes"][0]["id"], "attempt": 1,
                "interrupted": None, "error": None, "estimate": planmod.estimate(plan),
                "limits": planmod.limits_report(plan), "output": None,
                "history": [{"ts": now, "from": None, "to": "PLANNED", "note": "created"}],
                "cloud_spend_usd": 0.0, "paid_calls": 0,
            }
            self._save_job(pdir, job)
        return job

    def get_job(self, project_id: str, job_id: str) -> dict[str, Any]:
        return self._read_job(self._pdir(project_id), job_id)

    def list_jobs(self, project_id: str) -> list[dict[str, Any]]:
        pdir = self._pdir(project_id)
        jobs = []
        for path in sorted((pdir / "jobs").glob("job_*.json")):
            with contextlib.suppress(StoreError, FileNotFoundError):
                jobs.append(self._read_job(pdir, path.stem))
        return sorted(jobs, key=lambda j: j.get("created_ts", ""))

    def job_plan(self, project_id: str, job_id: str) -> dict[str, Any]:
        """The exact plan version a job is bound to (it does not follow later edits)."""
        job = self.get_job(project_id, job_id)
        record = self._load_record(self._pdir(project_id), job["plan_version"])
        if record["content_sha256"] != job["plan_content_sha256"]:
            raise IntegrityError(f"job {job_id}: bound plan version no longer matches its recorded hash")
        return record["plan"]

    def _transition(self, project_id: str, job_id: str, to_state: str, *, note: str = "",
                    apply: Callable[[dict[str, Any]], None] | None = None,
                    guard: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
        pdir = self._pdir(project_id)
        with self._lock(pdir):
            job = self._read_job(pdir, job_id)
            source = job["state"]
            if to_state not in TRANSITIONS[source]:
                raise JobStateError(f"job {job_id}: {source} -> {to_state} is not allowed")
            if guard is not None:
                guard(job)
            job["state"] = to_state
            if apply is not None:
                apply(job)
            job["next_scene"] = self._next_scene(job)
            job["history"].append({"ts": self._now(), "from": source, "to": to_state, "note": note[:200]})
            self._save_job(pdir, job)
            return job

    def job_start_preview(self, project_id: str, job_id: str) -> dict[str, Any]:
        def apply(job: dict[str, Any]) -> None:
            job["phase"] = "preview"
            job["phases"]["preview"]["started_ts"] = self._now()
        return self._transition(project_id, job_id, "PREVIEW_RUNNING", note="preview started", apply=apply)

    def job_start_render(self, project_id: str, job_id: str) -> dict[str, Any]:
        def apply(job: dict[str, Any]) -> None:
            job["phase"] = "render"
            job["phases"]["render"]["started_ts"] = self._now()
            job["last_completed_scene"] = None
        return self._transition(project_id, job_id, "RENDER_RUNNING", note="render started", apply=apply)

    def job_complete_scene(self, project_id: str, job_id: str, scene_id: str,
                           output: dict[str, Any] | None = None) -> dict[str, Any]:
        """Record one finished scene of the running phase.  Scenes must complete in plan order."""
        pdir = self._pdir(project_id)
        with self._lock(pdir):
            job = self._read_job(pdir, job_id)
            if job["state"] not in RUNNING_STATES:
                raise JobStateError(f"job {job_id}: cannot complete a scene in state {job['state']}")
            expected = self._next_scene(job)
            if scene_id != expected:
                raise JobStateError(f"job {job_id}: next scene is {expected!r}, not {scene_id!r}")
            block = job["phases"][job["phase"]]
            block["completed"].append(scene_id)
            if output is not None:
                block["outputs"][scene_id] = output
            job["last_completed_scene"] = scene_id
            job["next_scene"] = self._next_scene(job)
            self._save_job(pdir, job)
            return job

    def _all_done(self, phase: str) -> Callable[[dict[str, Any]], None]:
        def guard(job: dict[str, Any]) -> None:
            remaining = [s for s in job["scene_ids"] if s not in job["phases"][phase]["completed"]]
            if remaining:
                raise JobStateError(f"job {job['job_id']}: {phase} is missing scenes {remaining}")
        return guard

    def job_finish_preview(self, project_id: str, job_id: str) -> dict[str, Any]:
        def apply(job: dict[str, Any]) -> None:
            job["phases"]["preview"]["finished_ts"] = self._now()
        return self._transition(project_id, job_id, "PREVIEW_DONE", note="preview finished",
                                guard=self._all_done("preview"), apply=apply)

    def job_finish_render(self, project_id: str, job_id: str, output: dict[str, Any] | None = None
                          ) -> dict[str, Any]:
        def apply(job: dict[str, Any]) -> None:
            job["phases"]["render"]["finished_ts"] = self._now()
            job["output"] = output
        return self._transition(project_id, job_id, "RENDER_DONE", note="render finished",
                                guard=self._all_done("render"), apply=apply)

    def job_fail(self, project_id: str, job_id: str, reason: str) -> dict[str, Any]:
        def apply(job: dict[str, Any]) -> None:
            job["error"] = str(reason)[:500]
        return self._transition(project_id, job_id, "FAILED", note=f"failed: {reason}", apply=apply)

    def job_interrupt(self, project_id: str, job_id: str, reason: str = "interrupted") -> dict[str, Any]:
        def apply(job: dict[str, Any]) -> None:
            phase = job["phase"]
            done = job["phases"][phase]["completed"]
            job["interrupted"] = {"ts": self._now(), "phase": phase, "reason": reason[:200],
                                  "last_completed_scene": done[-1] if done else None,
                                  "resume_from_scene": self._next_scene(job)}
        return self._transition(project_id, job_id, "INTERRUPTED", note=reason, apply=apply)

    def job_resume(self, project_id: str, job_id: str) -> dict[str, Any]:
        """INTERRUPTED -> the running state of the interrupted phase; continues from the next scene."""
        pdir = self._pdir(project_id)
        with self._lock(pdir):
            job = self._read_job(pdir, job_id)
            info = job.get("interrupted")
            if job["state"] != "INTERRUPTED" or not info:
                raise JobStateError(f"job {job_id}: only an INTERRUPTED job can be resumed (state {job['state']})")
            phase = info["phase"]

            def apply(j: dict[str, Any]) -> None:
                j["phase"] = phase
                j["attempt"] = int(j.get("attempt", 1)) + 1
                j["interrupted"] = {**info, "resumed_ts": self._now()}

            return self._transition(project_id, job_id, _RUNNING_OF_PHASE[phase],
                                    note=f"resume {phase} from {self._next_scene({**job, 'phase': phase})}",
                                    apply=apply)

    # ------------------------------------------------------------------ recovery

    def recover(self) -> dict[str, Any]:
        """Roll back-to-front crashes forward and turn RUNNING jobs into INTERRUPTED (call at startup)."""
        report: dict[str, Any] = {"projects": 0, "rolled_forward": [], "interrupted": [], "tmp_removed": 0,
                                  "errors": []}
        for pdir in sorted(self.root.iterdir()):
            if not pdir.is_dir() or pdir.name.startswith("."):
                continue
            report["projects"] += 1
            try:
                with self._lock(pdir):
                    numbers = self._version_numbers(pdir)
                    if numbers:
                        stored: Any = None
                        with contextlib.suppress(FileNotFoundError, IntegrityError):
                            stored = _read_json(pdir / "project.json")
                        behind = not isinstance(stored, dict) or stored.get("version") != numbers[-1]
                        self._current(pdir)
                        if behind:
                            report["rolled_forward"].append(pdir.name)
                    jobs = pdir / "jobs"
                    if jobs.is_dir():
                        for path in sorted(jobs.glob("job_*.json")):
                            try:
                                job = _read_json(path)
                            except (IntegrityError, FileNotFoundError):
                                report["errors"].append(f"{pdir.name}/{path.name}: unreadable job file")
                                continue
                            if job.get("state") not in RUNNING_STATES:
                                continue
                            phase = _PHASE_OF_RUNNING[job["state"]]
                            done = job["phases"][phase]["completed"]
                            job["interrupted"] = {
                                "ts": self._now(), "phase": phase, "reason": "found running at startup",
                                "last_completed_scene": done[-1] if done else None,
                                "resume_from_scene": None}
                            job["state"] = "INTERRUPTED"
                            job["next_scene"] = self._next_scene({**job, "phase": phase})
                            job["interrupted"]["resume_from_scene"] = job["next_scene"]
                            job["history"].append({"ts": self._now(), "from": "RUNNING", "to": "INTERRUPTED",
                                                   "note": "found running at startup"})
                            self._save_job(pdir, job)
                            report["interrupted"].append({"project_id": pdir.name, "job_id": job["job_id"],
                                                          "last_completed_scene": done[-1] if done else None})
                    for folder in (pdir, pdir / "versions", pdir / "jobs", pdir / "assets"):
                        if not folder.is_dir():
                            continue
                        for tmp in folder.glob(".*.tmp"):
                            with contextlib.suppress(OSError):
                                if time.time() - tmp.stat().st_mtime > TMP_MAX_AGE_SECONDS:
                                    tmp.unlink()
                                    report["tmp_removed"] += 1
            except StoreError as exc:
                report["errors"].append(f"{pdir.name}: {exc}")
        self.recovery_report = report
        return report
