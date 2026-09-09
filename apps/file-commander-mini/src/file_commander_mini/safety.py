"""File Commander policy and no-overwrite filesystem primitives.

The app never inherits all of the host filesystem just because it was started.
Moves retain a content identity and use no-follow directory handles on POSIX.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import stat
import sys


class FilePolicy:
    def __init__(self, state_dir: Path):
        self.state_dir = state_dir.resolve()

    def roots(self) -> list[Path]:
        return [Path(x).expanduser().absolute() for x in
                os.environ.get("FILE_COMMANDER_ROOTS", "").split(os.pathsep) if x.strip()]

    def allowed(self, path: Path) -> Path:
        if ".." in path.parts or (os.name != "nt" and "\\" in str(path)):
            raise PermissionError("path traversal is prohibited")
        path = path.expanduser().absolute()
        roots = self.roots()
        if not roots:
            raise PermissionError("NOT_CONFIGURED: set FILE_COMMANDER_ROOTS to an explicit workspace")
        if not any(path == root or root in path.parents for root in roots):
            raise PermissionError("path outside FILE_COMMANDER_ROOTS")
        protected = [self.state_dir, Path(sys.prefix).resolve()]
        protected += [Path(x).expanduser().absolute() for x in
                      os.environ.get("FILE_COMMANDER_PROTECTED_ROOTS", "").split(os.pathsep) if x]
        if os.name == "nt":
            protected += [Path(os.environ.get(name, "C:/Windows")) for name in
                          ("SYSTEMROOT", "PROGRAMFILES", "PROGRAMFILES(X86)")]
        else:
            protected += [Path(x) for x in ("/etc", "/usr", "/bin", "/sbin", "/var", "/proc", "/sys", "/dev", "/boot")]
        if path == Path(path.anchor) or any(path == root or root in path.parents for root in protected):
            raise PermissionError("protected system or application data path")
        secret_names = {".git", ".ssh", ".aws", ".azure", ".gnupg", ".kube", ".codex",
                        ".config", "secrets", "credentials", "token", "vault.key", "bcc.db"}
        for component in path.parts:
            name = component.casefold()
            if name in secret_names or name.startswith(".env") or name.endswith((".pem", ".key", ".p12", ".pfx")):
                raise PermissionError("secret or protected path")
            if os.name == "nt" and ":" in component and component != path.anchor:
                raise PermissionError("alternate data streams are prohibited")
        for ancestor in [path, *path.parents]:
            if ancestor.is_symlink() or (hasattr(ancestor, "is_junction") and ancestor.is_junction()):
                raise PermissionError("symlink or junction paths are prohibited")
            try:
                attrs = getattr(ancestor.lstat(), "st_file_attributes", 0)
            except FileNotFoundError:
                attrs = 0
            if attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                raise PermissionError("reparse points are prohibited")
            if (ancestor / ".git").exists():
                raise PermissionError("repository operations are prohibited")
        return path


@contextmanager
def _parent(path: Path):
    """Pin each existing parent without following a replaced directory link."""
    if os.name != "posix":
        yield None
        return
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(path.anchor, flags)
    try:
        for name in path.parent.parts[1:]:
            child = os.open(name, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def identity(path: Path) -> dict:
    with _parent(path) as parent:
        fd = os.open(path.name if parent is not None else path,
                     os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent)
        with os.fdopen(fd, "rb") as handle:
            if os.name == "posix":
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise PermissionError("only regular files are supported")
            digest = hashlib.sha256()
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
            after = os.fstat(handle.fileno())
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError("file changed while it was measured; refresh the preview")
            return {"sha256": digest.hexdigest(), "size": after.st_size,
                    "device": after.st_dev, "inode": after.st_ino}


def require_identity(path: Path, expected: dict) -> None:
    if not isinstance(expected, dict) or identity(path) != expected:
        raise ValueError("source changed since preview; refresh the plan")


def mkdir_no_follow(path: Path, policy: FilePolicy) -> dict:
    policy.allowed(path)
    with _parent(path) as parent:
        name = path.name if parent is not None else path
        os.mkdir(name, dir_fd=parent)
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        return {"device": info.st_dev, "inode": info.st_ino}


def rmdir_verified(path: Path, expected: dict, policy: FilePolicy) -> None:
    policy.allowed(path)
    with _parent(path) as parent:
        name = path.name if parent is not None else path
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode) or expected != {"device": info.st_dev, "inode": info.st_ino}:
            raise ValueError("created directory identity is unverified; owner recovery required")
        os.rmdir(name, dir_fd=parent)


def move_no_replace(src: Path, dst: Path, expected: dict, policy: FilePolicy) -> None:
    """Atomic destination creation; never shutil.move's implicit overwrite/copy.

    Cross-device moves are explicitly refused. A crash between link/unlink is
    reconciled from the durable batch journal, never treated as success.
    """
    policy.allowed(src)
    policy.allowed(dst)
    require_identity(src, expected)
    if not src.parent.stat().st_mode & 0o222 or not dst.parent.stat().st_mode & 0o222:
        raise PermissionError("source or destination parent is read-only")
    with _parent(src) as source_fd, _parent(dst) as target_fd:
        source = src.name if source_fd is not None else src
        target = dst.name if target_fd is not None else dst
        # The directories are pinned above. Re-evaluate current policy exactly
        # before the effect and again before removing the original name.
        policy.allowed(src)
        policy.allowed(dst)
        os.link(source, target, src_dir_fd=source_fd, dst_dir_fd=target_fd, follow_symlinks=False)
        try:
            source_stat = os.stat(source, dir_fd=source_fd, follow_symlinks=False)
            target_stat = os.stat(target, dir_fd=target_fd, follow_symlinks=False)
            if (source_stat.st_dev, source_stat.st_ino) != (expected["device"], expected["inode"]) or (
                    target_stat.st_dev, target_stat.st_ino) != (expected["device"], expected["inode"]):
                raise ValueError("file identity changed at effect time")
            policy.allowed(src)
            policy.allowed(dst)
            os.unlink(source, dir_fd=source_fd)
        except (OSError, ValueError):
            # Remove only the link created by this operation. The original
            # remains; a replaced target is never removed during cleanup.
            if os.stat(target, dir_fd=target_fd, follow_symlinks=False).st_ino == expected["inode"]:
                os.unlink(target, dir_fd=target_fd)
            raise
