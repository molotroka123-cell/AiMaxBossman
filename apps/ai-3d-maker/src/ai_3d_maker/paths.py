"""Path and filename safety.

Rule: nothing this application writes may land outside the job directory that
owns it, no matter what the caller sends. Job ids and artifact names are
sanitised, then the resolved path is re-checked against the sandbox root.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from .errors import UnsafePathError

_ALLOWED = re.compile(r"[^A-Za-z0-9._-]")
_MULTI_DOT = re.compile(r"\.{2,}")

# Reserved on Windows; refused everywhere so job dirs stay portable.
_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}

MAX_NAME_LEN = 80


def sanitize_name(raw: str, *, kind: str = "name") -> str:
    """Reduce arbitrary caller input to a safe single path segment.

    Raises UnsafePathError when nothing safe remains.
    """
    if not isinstance(raw, str):
        raise UnsafePathError(f"{kind} must be a string")
    # Normalise so look-alike unicode cannot smuggle separators through.
    text = unicodedata.normalize("NFKD", raw)
    text = text.replace("\\", "/").split("/")[-1]
    text = _ALLOWED.sub("", text)
    text = _MULTI_DOT.sub(".", text)
    text = text.strip("._-")
    text = text[:MAX_NAME_LEN]
    if not text:
        raise UnsafePathError(f"{kind} {raw!r} contains no safe characters")
    if text.split(".")[0].lower() in _RESERVED:
        raise UnsafePathError(f"{kind} {raw!r} is a reserved device name")
    return text


def strict_segment(raw: str, *, kind: str = "path segment") -> str:
    """Validate caller-supplied identifiers instead of quietly rewriting them.

    `sanitize_name` is for filenames that arrive with a file (an uploaded mesh),
    where silently cleaning is the useful behaviour. For anything the caller
    chose — job ids, artifact lookups — a traversal attempt is refused outright,
    so `../../etc/passwd` can never collapse into an innocent-looking `etcpasswd`
    that silently addresses a different job.
    """
    if not isinstance(raw, str):
        raise UnsafePathError(f"{kind} must be a string")
    normalised = unicodedata.normalize("NFKD", raw)
    if "/" in normalised or "\\" in normalised or "\0" in normalised:
        raise UnsafePathError(f"{kind} {raw!r} contains a path separator")
    if ".." in normalised:
        raise UnsafePathError(f"{kind} {raw!r} contains a parent-directory reference")
    if normalised.strip().startswith("."):
        raise UnsafePathError(f"{kind} {raw!r} starts with a dot")
    return sanitize_name(raw, kind=kind)


def safe_job_id(raw: str) -> str:
    return strict_segment(raw, kind="job id")


def safe_artifact_name(raw: str) -> str:
    """Sanitise an artifact filename, keeping at most one extension."""
    name = sanitize_name(raw, kind="artifact name")
    if name.startswith("."):
        raise UnsafePathError(f"artifact name {raw!r} is hidden/extension-only")
    return name


def resolve_within(root: Path, *parts: str) -> Path:
    """Join sanitised parts under root and verify the result stays inside root.

    This is deliberately belt-and-braces: sanitize_name already removes
    separators, and the resolved-prefix check catches anything it missed
    (symlinked job dirs, for example).
    """
    root = Path(root).resolve()
    safe_parts = [strict_segment(p) for p in parts]
    candidate = root.joinpath(*safe_parts)
    try:
        resolved = candidate.resolve()
    except OSError as exc:  # pragma: no cover - filesystem dependent
        raise UnsafePathError(f"cannot resolve path: {exc}") from exc
    if resolved != root and root not in resolved.parents:
        raise UnsafePathError(f"path {candidate} escapes sandbox {root}")
    return resolved


def dir_size_bytes(path: Path) -> int:
    total = 0
    root = Path(path)
    if not root.exists():
        return 0
    for p in root.rglob("*"):
        if p.is_file() and not p.is_symlink():
            try:
                total += p.stat().st_size
            except OSError:  # pragma: no cover
                continue
    return total
