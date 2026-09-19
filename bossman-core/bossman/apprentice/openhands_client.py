"""Guarded OpenHands sidecar client for Bossman's Apprentice/Teacher boundary.

OpenHands runs outside the Bossman interpreter (Python 3.12+ sidecar).  The
sidecar is treated as untrusted: Bossman derives the Git delta itself, rejects
pre-existing dirt, refuses repositories with remotes, and enforces explicit
allowed/protected paths after execution.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import stat
import tempfile
from types import MappingProxyType
from typing import Mapping, Sequence


class OpenHandsError(RuntimeError):
    """Raised when the OpenHands sidecar cannot produce admissible evidence."""


@dataclass(frozen=True)
class OpenHandsRequest:
    instruction: str
    workspace: Path
    allowed_paths: tuple[str, ...]
    protected_paths: tuple[str, ...] = ()
    model: str | None = None
    timeout_seconds: int = 900
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class OpenHandsResult:
    status: str
    changed_files: tuple[str, ...]
    diff: str
    sidecar: Mapping[str, object]
    files: Mapping[str, "FileEvidence"] = field(default_factory=dict)


@dataclass(frozen=True)
class FileEvidence:
    """Host-read immutable bytes and Git-relevant type/mode, never a sidecar claim."""
    mode: str
    data: bytes
    permissions: int = 0


_MAX_FILE_BYTES = 8 * 1024 * 1024
_MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
_MAX_SNAPSHOT_FILES = 10000


def _read_entry(workspace: Path, path: Path) -> FileEvidence:
    relative = path.relative_to(workspace)
    directory_fd = None
    descriptor = None
    try:
        if os.name != "nt":
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            directory_fd = os.open(workspace, flags)
            for part in relative.parts[:-1]:
                child = os.open(part, flags, dir_fd=directory_fd)
                os.close(directory_fd)
                directory_fd = child
            name = relative.name
            before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISLNK(before.st_mode):
                return FileEvidence("120000", os.readlink(name, dir_fd=directory_fd).encode("utf-8", "surrogateescape"), stat.S_IMODE(before.st_mode))
            if not stat.S_ISREG(before.st_mode):
                raise OpenHandsError(f"non-regular workspace entry: {relative}")
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        else:
            before = path.lstat()
            if stat.S_ISLNK(before.st_mode):
                return FileEvidence("120000", os.readlink(path).encode("utf-8", "surrogateescape"), stat.S_IMODE(before.st_mode))
            if not stat.S_ISREG(before.st_mode):
                raise OpenHandsError(f"non-regular workspace entry: {relative}")
            descriptor = os.open(path, os.O_RDONLY | os.O_BINARY)
            # Resolve the opened HANDLE before reading, not a pathname which
            # can have been redirected through a junction after enumeration.
            import ctypes
            from ctypes import wintypes
            import msvcrt
            final_path = ctypes.WinDLL("kernel32", use_last_error=True).GetFinalPathNameByHandleW
            final_path.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
            final_path.restype = wintypes.DWORD
            buffer = ctypes.create_unicode_buffer(32768)
            count = final_path(msvcrt.get_osfhandle(descriptor), buffer, len(buffer), 0)
            if not count or count >= len(buffer):
                raise OpenHandsError("cannot verify opened workspace handle")
            actual = buffer.value
            if actual.startswith("\\\\?\\UNC\\"):
                actual = "\\\\" + actual[8:]
            elif actual.startswith("\\\\?\\"):
                actual = actual[4:]
            if not Path(actual).resolve().is_relative_to(workspace.resolve()):
                raise OpenHandsError(f"opened path outside workspace: {relative}")
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise OpenHandsError(f"workspace entry changed during observation: {relative}")
        if opened.st_size > _MAX_FILE_BYTES:
            raise OpenHandsError(f"workspace evidence file exceeds bounded read: {relative}")
        data = bytearray()
        while len(data) <= _MAX_FILE_BYTES:
            chunk = os.read(descriptor, min(256 * 1024, _MAX_FILE_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(descriptor)
        identity = lambda item: (item.st_dev, item.st_ino, item.st_mode, item.st_size,
                                 item.st_mtime_ns, item.st_ctime_ns)
        if len(data) > _MAX_FILE_BYTES or identity(opened) != identity(after):
            raise OpenHandsError(f"workspace evidence changed or exceeded limit: {relative}")
        mode = "100755" if os.name != "nt" and opened.st_mode & 0o111 else "100644"
        return FileEvidence(mode, bytes(data), stat.S_IMODE(opened.st_mode))
    except OSError as exc:
        raise OpenHandsError(f"cannot independently read workspace entry: {relative}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory_fd is not None:
            os.close(directory_fd)


def _snapshot(workspace: Path) -> dict[str, FileEvidence]:
    output = {}
    total = 0
    for name, path in _worktree_files(workspace).items():
        entry = _read_entry(workspace, path)
        total += len(entry.data)
        if total > _MAX_SNAPSHOT_BYTES:
            raise OpenHandsError("workspace evidence exceeds bounded snapshot size")
        output[name] = entry
    return output


def _snapshot_diff(before: Mapping[str, FileEvidence], after: Mapping[str, FileEvidence], changed: Sequence[str]) -> str:
    """Render the reviewed diff from immutable host bytes, in a separate tree."""
    if not changed:
        return ""
    with tempfile.TemporaryDirectory(prefix="bossman-reviewed-diff-") as directory:
        root = Path(directory)
        for label, entries in (("a", before), ("b", after)):
            (root / label).mkdir()
            for name in changed:
                entry = entries.get(name)
                if entry is None:
                    continue
                destination = root / label / _normalize_repo_path(name)
                destination.parent.mkdir(parents=True, exist_ok=True)
                if entry.mode == "120000":
                    os.symlink(entry.data.decode("utf-8", "surrogateescape"), destination)
                else:
                    destination.write_bytes(entry.data)
                    if os.name != "nt":
                        destination.chmod(0o755 if entry.mode == "100755" else 0o644)
        environment = dict(_minimal_process_env(), GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        result = subprocess.run(["git", "-c", "core.autocrlf=false", "diff", "--no-index", "--binary",
                                 "--no-ext-diff", "--no-textconv", "--src-prefix=", "--dst-prefix=", "a", "b"],
                                cwd=root, env=environment, capture_output=True, timeout=30)
        if result.returncode not in (0, 1):
            raise OpenHandsError("cannot render independently captured workspace diff")
        return result.stdout.decode("utf-8", "replace")


_SYSTEM_ENV_KEYS = (
    "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC",
    "HOME", "USERPROFILE", "TMP", "TEMP", "LANG", "LC_ALL",
    "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
)
_DRIVE = re.compile(r"^[A-Za-z]:")


def _minimal_process_env() -> dict[str, str]:
    """Keep only OS/runtime variables. Provider credentials are explicit input."""
    return {key: os.environ[key] for key in _SYSTEM_ENV_KEYS if os.environ.get(key)}


def _command_digest(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            for chunk in iter(lambda: stream.read(256 * 1024), b""):
                digest.update(chunk)
            after = os.fstat(stream.fileno())
        if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise OpenHandsError("sidecar command identity changed during measurement")
    except OSError as exc:
        raise OpenHandsError("sidecar command identity is unavailable") from exc
    return digest.hexdigest()


def _git(workspace: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(workspace), *args],
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode:
        raise OpenHandsError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def _untracked_files(workspace: Path) -> tuple[str, ...]:
    return tuple(sorted(
        line.strip().replace("\\", "/")
        for line in _git(workspace, "ls-files", "--others", "--exclude-standard").splitlines()
        if line.strip()))


# ----------------------------------------------------------------------------
# Evidence derived from the FILESYSTEM against HEAD, not from the index.
#
# Astra/Codex F1 (2026-09-08, reproduced 2/2 on 45027d3): the sidecar ran
# `git update-index --assume-unchanged protected.txt`, rewrote protected.txt and
# returned `completed`. `git diff HEAD` and `git status` trust the index's
# "unchanged" flag, so Bossman saw `changed_files=()`, an empty diff, and admitted
# a protected-file mutation with empty evidence. `--skip-worktree`, a poisoned
# `.git/info/exclude` or a new `.gitignore` hide changes the same way.
#
# Invariant: THE UNTRUSTED PRODUCER CANNOT CONTROL THE EVIDENCE USED TO ADMIT
# ITS OWN RESULT. So the change set is computed by hashing every file in the
# working tree and comparing with HEAD's tree — the index has no say — and
# the git-derived view is only allowed to ADD paths, never to hide one.
# ----------------------------------------------------------------------------

def _blob_digest(data: bytes) -> str:
    h = hashlib.sha1(usedforsecurity=False)  # git blob id, not a security hash
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


def _blob_sha(path: Path, *, workspace: Path | None = None) -> str:
    """Git's blob id for a working-tree path (symlinks hash their target)."""
    return _blob_digest(_read_entry(workspace or path.parent, path).data)


def _head_tree(workspace: Path) -> dict[str, tuple[str, str]]:
    """path -> (mode, blob sha) at HEAD. Submodule entries retain their
    commit id so a replaced submodule directory still reads as a change."""
    out: dict[str, tuple[str, str]] = {}
    raw = _git(workspace, "ls-tree", "-r", "-z", "HEAD")
    for entry in raw.split("\0"):
        if not entry:
            continue
        meta, _, path = entry.partition("\t")
        parts = meta.split()
        if len(parts) >= 3 and path:
            out[path.replace("\\", "/")] = (parts[0], parts[2])
    return out


def _worktree_files(workspace: Path) -> dict[str, Path]:
    """Every path under the workspace except the repository's own `.git`."""
    files: dict[str, Path] = {}
    root = workspace
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        if rel_dir == Path(".git") or str(rel_dir).startswith(".git" + os.sep):
            dirnames[:] = []
            continue
        # A symlinked directory is a file (the link) as far as git is concerned.
        kept = []
        for d in dirnames:
            full = Path(dirpath) / d
            if getattr(full, "is_junction", lambda: False)():
                raise OpenHandsError("workspace contains a directory junction")
            if full.is_symlink():
                filenames.append(d)
            elif rel_dir == Path(".") and d == ".git":
                continue
            else:
                kept.append(d)
        dirnames[:] = kept
        for name in filenames:
            full = Path(dirpath) / name
            rel = (rel_dir / name).as_posix() if rel_dir != Path(".") else name
            files[rel] = full
            if len(files) > _MAX_SNAPSHOT_FILES:
                raise OpenHandsError("workspace evidence exceeds bounded file count")
    return files


def _index_flag_tampering(workspace: Path) -> tuple[str, ...]:
    """Paths whose index entry carries assume-unchanged (`h`) or skip-worktree
    (`S`) — flags no honest coding run sets; they exist only to make git stop
    looking at a file."""
    flagged = []
    for line in _git(workspace, "ls-files", "-v", "-z").split("\0"):
        if len(line) > 2 and line[1] == " " and (line[0].islower() or line[0] == "S"):
            flagged.append(line[2:].replace("\\", "/"))
    return tuple(sorted(flagged))


def _ignored_untracked(workspace: Path, untracked: Sequence[str]) -> set[str]:
    """Untracked paths the repository's OWN ignore rules exclude.

    The global/user excludes file is disabled on purpose: it lives in the
    owner's home, which the sidecar can write to. Repository rules are trusted
    only when they were not touched by the run (the caller checks that)."""
    if not untracked:
        return set()
    proc = subprocess.run(
        ["git", "-C", str(workspace), "-c", "core.excludesFile=", "check-ignore", "--no-index",
         "--stdin", "-z"],
        input="\0".join(untracked) + "\0", text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if proc.returncode not in (0, 1):          # 1 = nothing ignored; anything else: do not filter
        return set()
    return {p.replace("\\", "/") for p in proc.stdout.split("\0") if p}


@dataclass(frozen=True)
class WorktreeDelta:
    """Independently derived change set. `changed` is what scope validation
    sees; `ignored` is what the repository's untouched ignore rules dropped."""
    changed: tuple[str, ...]
    untracked: tuple[str, ...]
    deleted: tuple[str, ...]
    ignored: tuple[str, ...]
    ignore_rules_touched: bool


def _filtered_blob_ids(workspace: Path, rels: Sequence[str]) -> dict[str, str]:
    """Blob ids for regular tracked files THE WAY GIT COMPUTES THEM — through
    the clean filter (`core.autocrlf`, `.gitattributes` eol/text). Raw bytes
    are not what a blob is: on Windows (`autocrlf=true`) every checkout holds
    CRLF while the blob holds LF, and hashing raw bytes reported an untouched
    workspace as fully modified (Core CI Windows job, 31 failures). The filter
    configuration is not sidecar-controlled: `.git/config` is compared before
    and after the run, and `.gitattributes` is a tracked file whose edit is
    itself a change. Any failure returns {} and the caller falls back to raw
    hashing, which can only over-report, never hide."""
    if not rels:
        return {}
    proc = subprocess.run(
        ["git", "-C", str(workspace), "hash-object", "--stdin-paths"],
        input="\n".join(rels) + "\n", text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if proc.returncode:
        return {}
    ids = proc.stdout.split()
    if len(ids) != len(rels):
        return {}
    return dict(zip(rels, ids))


def _worktree_delta(workspace: Path, *, exclude_before: bytes | None = None) -> WorktreeDelta:
    head = _head_tree(workspace)
    tree = _worktree_files(workspace)
    modified: list[str] = []
    untracked: list[str] = []
    regular = [rel for rel, full in tree.items() if rel in head and not full.is_symlink()]
    filtered = _filtered_blob_ids(workspace, regular)
    for rel, full in tree.items():
        baseline = head.get(rel)
        if baseline is None:
            untracked.append(rel)
            continue
        try:
            entry = _read_entry(workspace, full)
            actual_mode = entry.mode
            expected_mode, sha = baseline
            # Windows has no POSIX executable bit; type still must agree.
            modes_agree = actual_mode == expected_mode or (os.name == "nt" and
                            actual_mode.startswith("100") and expected_mode.startswith("100"))
            # Symlinks hash their target verbatim; regular files go through
            # git's clean filter so line-ending conversion is not a "change".
            digest = filtered.get(rel) if not full.is_symlink() else None
            if digest is None:
                digest = _blob_digest(entry.data)
            if digest != sha or not modes_agree:
                modified.append(rel)
        except OSError:
            modified.append(rel)              # unreadable now, readable at commit: changed
    deleted = [rel for rel in head if rel not in tree]

    # Ignore rules are trusted only if the run left them alone: any changed,
    # added or deleted `.gitignore`, or a rewritten `.git/info/exclude`, means
    # every untracked file counts.
    touched_names = set(modified) | set(untracked) | set(deleted)
    rules_touched = any(Path(p).name == ".gitignore" for p in touched_names)
    exclude_path = workspace / ".git" / "info" / "exclude"
    exclude_now = exclude_path.read_bytes() if exclude_path.exists() else b""
    if exclude_before is not None and exclude_now != exclude_before:
        rules_touched = True
    ignored = set() if rules_touched else _ignored_untracked(workspace, untracked)

    changed = sorted((set(modified) | set(untracked) | set(deleted)) - ignored)
    return WorktreeDelta(tuple(changed), tuple(sorted(set(untracked) - ignored)),
                         tuple(sorted(deleted)), tuple(sorted(ignored)), rules_touched)


def _changed_files(workspace: Path, *, exclude_before: bytes | None = None) -> tuple[str, ...]:
    """The change set a reviewer is shown. Filesystem-vs-HEAD is authoritative;
    the index-based view may only add paths (its extra excludes are honoured),
    never remove one — a path we saw that git hides is a refusal, because the
    diff the reviewer would read (`git diff`) would be lying about it."""
    flagged = _index_flag_tampering(workspace)
    if flagged:
        raise OpenHandsError("OpenHands altered index flags (assume-unchanged/skip-worktree) on: "
                             + ", ".join(flagged))
    delta = _worktree_delta(workspace, exclude_before=exclude_before)
    git_view: set[str] = set()
    for args in (("diff", "--name-only", "HEAD"), ("ls-files", "--others", "--exclude-standard")):
        git_view.update(line.strip().replace("\\", "/") for line in _git(workspace, *args).splitlines()
                        if line.strip())
    hidden = sorted(set(delta.changed) - git_view)
    if hidden:
        raise OpenHandsError("evidence mismatch: git hides changes present in the working tree: "
                             + ", ".join(hidden))
    return tuple(sorted(set(delta.changed) | git_view))


def _evidence_diff(workspace: Path, untracked: Sequence[str] = ()) -> str:
    """The diff a reviewer actually reads, INCLUDING files the agent created.

    `git diff HEAD` shows tracked changes only, so a run whose entire output was
    new files produced `changed_files=('NOTES.md',)` next to an empty diff — the
    file list said work happened and the evidence showed none. For a coding
    worker whose main product is new files that is the evidence gap that
    matters most.

    `--intent-to-add` registers the new paths in the sandbox INDEX so they
    appear in the diff. It touches neither the working tree nor HEAD, and the
    caller re-checks both afterwards; the worktree is disposable by
    construction."""
    if untracked:
        _git(workspace, "add", "--intent-to-add", "--", *untracked)
    return _git(workspace, "diff", "--binary", "--no-ext-diff", "--no-textconv", "HEAD")


def _normalize_repo_path(path: str) -> str:
    raw = str(path).replace("\\", "/")
    while raw.startswith("./"):
        raw = raw[2:]
    if not raw or raw.startswith("/") or _DRIVE.match(raw) or "\x00" in raw or ".." in raw.split("/"):
        raise OpenHandsError(f"unsafe repository path: {path!r}")
    return raw.rstrip("/")


def _in_scope(path: str, prefixes: Sequence[str]) -> bool:
    clean = _normalize_repo_path(path)
    for prefix in prefixes:
        p = _normalize_repo_path(prefix)
        if clean == p or clean.startswith(p + "/"):
            return True
    return False


def _validate_scope(changed: Sequence[str], allowed: Sequence[str], protected: Sequence[str]) -> None:
    if not allowed:
        raise OpenHandsError("allowed_paths must be non-empty (fail closed)")
    for path in (*allowed, *protected):
        _normalize_repo_path(path)
    violations = [p for p in changed if not _in_scope(p, allowed) or _in_scope(p, protected)]
    if violations:
        raise OpenHandsError("OpenHands changed protected/out-of-scope paths: " + ", ".join(violations))


class OpenHandsClient:
    """Run a JSON-over-stdio OpenHands sidecar without granting repository authority."""

    def __init__(self, command: Sequence[str] | None = None, env: Mapping[str, str] | None = None):
        configured = os.environ.get("BOSSMAN_OPENHANDS_COMMAND", "")
        raw = tuple(command or ())
        if not raw and configured.strip():
            raw = tuple(shlex.split(configured, posix=os.name != "nt"))
        if not raw:
            raise OpenHandsError("OpenHands disabled: set BOSSMAN_OPENHANDS_COMMAND or pass command=")
        command = list(raw)
        self._command_identity: dict[Path, str] = {}
        executable = shutil.which(command[0])
        if executable:
            command[0] = str(Path(executable).resolve())
        for index, arg in enumerate(command):
            # Only actual command files are pinned; -c source and -m module
            # names are arguments, not filenames to resolve or execute.
            if index and not arg.lower().endswith((".py", ".pyw", ".exe", ".cmd", ".bat")):
                continue
            path = Path(arg)
            try:
                is_file = path.is_file()
            except OSError:
                is_file = False
            if is_file:
                path = path.resolve()
                self._command_identity[path] = _command_digest(path)
                command[index] = str(path)
        self.command = tuple(command)
        self.env = {str(k): str(v) for k, v in dict(env or {}).items()}

    def run(self, request: OpenHandsRequest) -> OpenHandsResult:
        workspace = request.workspace.resolve()
        if not (workspace / ".git").exists():
            raise OpenHandsError(f"workspace is not a git checkout: {workspace}")
        _validate_scope((), request.allowed_paths, request.protected_paths)
        exclude_path = workspace / ".git" / "info" / "exclude"
        exclude_before = exclude_path.read_bytes() if exclude_path.exists() else b""
        dirty_before = _changed_files(workspace, exclude_before=exclude_before)
        if dirty_before:
            raise OpenHandsError("workspace must be clean before OpenHands run: " + ", ".join(dirty_before))
        remotes = tuple(line.strip() for line in _git(workspace, "remote").splitlines() if line.strip())
        if remotes:
            raise OpenHandsError("OpenHands workspace must not have git remotes")
        head_before = _git(workspace, "rev-parse", "HEAD").strip()
        config_before = (workspace / ".git" / "config").read_bytes()
        before_files = _snapshot(workspace)
        if _changed_files(workspace, exclude_before=exclude_before):
            raise OpenHandsError("workspace changed before sidecar dispatch")

        payload = {
            "schema": "bossman.openhands.v1",
            "instruction": request.instruction,
            "workspace": str(workspace),
            "allowed_paths": list(request.allowed_paths),
            "protected_paths": list(request.protected_paths),
            "model": request.model,
            "metadata": dict(request.metadata),
        }
        env = _minimal_process_env()
        env.update(self.env)
        for path, expected_digest in self._command_identity.items():
            if _command_digest(path) != expected_digest:
                raise OpenHandsError("sidecar command identity changed before dispatch")
        try:
            proc = subprocess.run(
                list(self.command),
                input=json.dumps(payload, ensure_ascii=False),
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                timeout=request.timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise OpenHandsError("OpenHands sidecar command is not installed") from exc
        except subprocess.TimeoutExpired as exc:
            raise OpenHandsError(f"OpenHands sidecar timed out after {request.timeout_seconds}s") from exc

        try:
            response = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise OpenHandsError("OpenHands sidecar returned invalid JSON") from exc
        if not isinstance(response, dict) or response.get("schema") != "bossman.openhands.v1" or response.get("status") not in {"completed", "failed"}:
            raise OpenHandsError("OpenHands sidecar returned an invalid response contract")

        head_after = _git(workspace, "rev-parse", "HEAD").strip()
        if head_after != head_before:
            raise OpenHandsError("OpenHands may not commit/reset/rewrite sandbox HEAD")
        if (workspace / ".git" / "config").read_bytes() != config_before:
            raise OpenHandsError("OpenHands may not modify sandbox git configuration")
        if tuple(line.strip() for line in _git(workspace, "remote").splitlines() if line.strip()):
            raise OpenHandsError("OpenHands may not add git remotes")
        # Evidence is derived from the filesystem against HEAD (see _worktree_delta):
        # the sidecar's index flags and ignore rules cannot hide a change from it.
        changed = _changed_files(workspace, exclude_before=exclude_before)
        _validate_scope(changed, request.allowed_paths, request.protected_paths)
        captured = _snapshot(workspace)
        permission_changes = [name for name in before_files.keys() & captured.keys()
                              if before_files[name].mode.startswith("100") and captured[name].mode.startswith("100")
                              and (before_files[name].permissions ^ captured[name].permissions) & ~0o111]
        if permission_changes:
            _validate_scope(permission_changes, request.allowed_paths, request.protected_paths)
            raise OpenHandsError("non-Git permission mutation is not representable in the reviewed patch: "
                                 + ", ".join(sorted(permission_changes)))
        untracked = _worktree_delta(workspace, exclude_before=exclude_before).untracked
        _evidence_diff(workspace, untracked)
        if _snapshot(workspace) != captured or _changed_files(workspace, exclude_before=exclude_before) != changed:
            raise OpenHandsError("workspace changed while deriving independent evidence")
        if _git(workspace, "rev-parse", "HEAD").strip() != head_before or (workspace / ".git" / "config").read_bytes() != config_before:
            raise OpenHandsError("repository identity changed while deriving independent evidence")
        diff = _snapshot_diff(before_files, captured, changed)
        if proc.returncode and response.get("status") != "failed":
            raise OpenHandsError(f"OpenHands sidecar exited {proc.returncode} without failed status")
        return OpenHandsResult(str(response["status"]), changed, diff, response,
                               MappingProxyType({name: captured[name] for name in changed if name in captured}))
