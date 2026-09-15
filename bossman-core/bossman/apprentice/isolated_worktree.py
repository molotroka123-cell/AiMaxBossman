"""
Isolated Git Worktree Manager for OpenHands execution.

This module:
- Creates disposable Git worktrees for OpenHands tasks
- Ensures clean isolated state
- Records pre-run baseline
- Derives post-run evidence
- Cleans up worktree after completion

Security properties:
- OpenHands never edits owner's primary checkout
- Pre-existing changes don't contaminate evidence
- Worktree is destroyed after task (unless retention policy requires otherwise)
"""

import gc
import logging
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class IsolatedWorktree:
    """
    Manages an isolated Git worktree for OpenHands execution.

    Usage:
        with IsolatedWorktree('/path/to/repo') as worktree:
            # worktree.root is the isolated checkout
            # Run OpenHands here
            # Derive evidence
        # worktree is automatically cleaned up
    """

    def __init__(
        self,
        source_repo: str,
        base_branch: Optional[str] = None,
        keep_after: bool = False
    ):
        self.source_repo = Path(source_repo).resolve()
        self.base_branch = base_branch or self._get_default_branch()
        self.keep_after = keep_after
        self.root: Optional[Path] = None
        self.pre_run_state: Dict[str, Any] = {}
        self.post_run_state: Dict[str, Any] = {}
        # Cleanup is a claim about the disk, so it is recorded, not assumed:
        # `cleanup_error` names the last failure, `cleanup_removed` says whether
        # the sandbox is really gone. Astra F4 (2026-09-08): `rmtree(ignore_errors
        # =True)` on Windows left the checkout in place and reported nothing.
        self.cleanup_error: Optional[str] = None
        self.cleanup_removed: Optional[bool] = None
        self._tempdir: Optional[Path] = None

    def _get_default_branch(self) -> str:
        try:
            result = subprocess.run(
                ['git', 'symbolic-ref', 'refs/remotes/origin/HEAD'],
                cwd=str(self.source_repo),
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                return result.stdout.strip().replace('refs/remotes/origin/', '')
        except Exception as e:
            logger.warning(f"Failed to get default branch: {e}")

        # No `origin` remote — an isolated sandbox checkout usually has none.
        # Falling straight to the literal 'main' was wrong: on a repository
        # whose branch is 'master' (still git's default in many installs)
        # BOTH worktree attempts referenced a branch that does not exist and
        # the whole feature failed with "fatal: invalid reference: main".
        # The honest fallback is the branch this repository is actually on.
        try:
            current = subprocess.run(
                ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
                cwd=str(self.source_repo),
                capture_output=True,
                text=True,
                timeout=10
            )
            branch = current.stdout.strip()
            if current.returncode == 0 and branch and branch != 'HEAD':
                return branch
        except Exception as e:
            logger.warning(f"Failed to read the current branch: {e}")

        return 'main'

    def create(self) -> Path:
        """Build the sandbox as a standalone CLONE, not a linked worktree.

        `git worktree add` was the original mechanism and it produced a sandbox
        that could not be used, measured twice:

          * the worktree shares the source repository's config, so it came out
            with `origin` attached and with a `.git` FILE instead of a
            directory. `OpenHandsClient.run()` refuses both — it rejects any
            workspace with a remote ("must not have git remotes") and it reads
            `.git/config` as a path under a directory. The isolation this class
            exists to provide therefore could not be handed to the client it
            exists to serve.
          * `git worktree add -b` creates the disposable branch IN THE SOURCE
            repository, and `cleanup()` never deleted it. Every run left an
            `openhands_task_*` branch behind in the owner's repository.
            Isolation that permanently mutates what it isolates from is not
            isolation.

        A local clone costs one copy of the object store and owes the source
        nothing afterwards: no branch, no worktree registration, no shared
        config. Removing its remote is what makes "the agent cannot push" a
        structural fact rather than a policy.
        """
        logger.info(f"Cloning isolated sandbox from {self.source_repo}@{self.base_branch}")

        temp_dir = tempfile.mkdtemp(prefix='openhands_worktree_')
        self._tempdir = Path(temp_dir)
        worktree_path = Path(temp_dir) / 'worktree'

        try:
            result = subprocess.run(
                ['git', 'clone', '--local', str(self.source_repo), str(worktree_path)],
                capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                raise RuntimeError(f"Failed to clone sandbox: {result.stderr}")

            # A disposable branch, created INSIDE the clone. Bases from most to
            # least specific; plain HEAD exists in every repository, including a
            # detached one, so the last attempt cannot fail for lack of a ref.
            branch = f'openhands_task_{datetime.now().strftime("%Y%m%d_%H%M%S_%f")}'
            checkout = None
            for base in (f'origin/{self.base_branch}', self.base_branch, 'HEAD'):
                checkout = subprocess.run(
                    ['git', 'checkout', '-q', '-b', branch, base],
                    cwd=str(worktree_path), capture_output=True, text=True, timeout=60)
                if checkout.returncode == 0:
                    break
                subprocess.run(['git', 'branch', '-D', branch], cwd=str(worktree_path),
                               capture_output=True, text=True, timeout=30)
            if checkout is None or checkout.returncode != 0:
                raise RuntimeError(
                    f"Failed to check out a sandbox branch: "
                    f"{checkout.stderr if checkout else 'no attempt made'}")

            # Last, so the remote-tracking refs above were still resolvable.
            # After this the sandbox has nowhere to push and nothing to fetch.
            remove = subprocess.run(['git', 'remote', 'remove', 'origin'],
                                    cwd=str(worktree_path), capture_output=True,
                                    text=True, timeout=30)
            if remove.returncode != 0:
                raise RuntimeError(f"Failed to detach the sandbox remote: {remove.stderr}")
            remaining = subprocess.run(['git', 'remote'], cwd=str(worktree_path),
                                       capture_output=True, text=True, timeout=30)
            if remaining.stdout.strip():
                raise RuntimeError(
                    f"sandbox still has remotes: {remaining.stdout.strip()!r}")

            self.root = worktree_path
            logger.info(f"Sandbox created at {worktree_path}")

            self.pre_run_state = self._record_state()

            return worktree_path

        except Exception as e:
            logger.error(f"Failed to create sandbox: {e}")
            if worktree_path.exists():
                self._cleanup(worktree_path)
            raise

    def _record_state(self) -> Dict[str, Any]:
        if not self.root:
            return {}

        state = {
            'timestamp': datetime.now().isoformat(),
            'head': None,
            'status': [],
            'files': {}
        }

        try:
            result = subprocess.run(
                ['git', 'rev-parse', 'HEAD'],
                cwd=str(self.root),
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                state['head'] = result.stdout.strip()

            result = subprocess.run(
                ['git', 'status', '--porcelain'],
                cwd=str(self.root),
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                state['status'] = result.stdout.strip().split('\n')

        except Exception as e:
            logger.warning(f"Failed to record state: {e}")

        return state

    def derive_evidence(self) -> Dict[str, Any]:
        if not self.root:
            return {'error': 'No worktree'}

        evidence = {
            'modified_files': [],
            'added_files': [],
            'deleted_files': [],
            'patches': {},
            'head_before': self.pre_run_state.get('head'),
            'head_after': None,
            'untracked_files': []
        }

        try:
            result = subprocess.run(
                ['git', 'rev-parse', 'HEAD'],
                cwd=str(self.root),
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                evidence['head_after'] = result.stdout.strip()

            result = subprocess.run(
                ['git', 'diff', '--name-status', 'HEAD'],
                cwd=str(self.root),
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.strip().split('\n'):
                    if not line:
                        continue
                    parts = line.split('\t')
                    if len(parts) >= 2:
                        status = parts[0]
                        file_path = parts[1]

                        if status == 'M' or status.startswith('M'):
                            evidence['modified_files'].append(file_path)
                        elif status == 'A' or status.startswith('A'):
                            evidence['added_files'].append(file_path)
                        elif status == 'D' or status.startswith('D'):
                            evidence['deleted_files'].append(file_path)

            result = subprocess.run(
                ['git', 'ls-files', '--others', '--exclude-standard'],
                cwd=str(self.root),
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode == 0 and result.stdout.strip():
                evidence['untracked_files'] = result.stdout.strip().split('\n')

            for file_path in evidence['modified_files']:
                result = subprocess.run(
                    ['git', 'diff', 'HEAD', '--', file_path],
                    cwd=str(self.root),
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                if result.returncode == 0:
                    evidence['patches'][file_path] = result.stdout

        except Exception as e:
            logger.error(f"Failed to derive evidence: {e}")
            evidence['error'] = str(e)

        return evidence

    #: Deletion attempts before the failure is reported. Windows releases file
    #: locks (antivirus scanners, a git process winding down) with a delay, so
    #: a bounded retry is legitimate; an unbounded one would hide a real leak.
    CLEANUP_ATTEMPTS = 4

    @staticmethod
    def _sandbox_parent() -> Path:
        return Path(tempfile.gettempdir()).resolve()

    def _cleanup(self, path: Path) -> bool:
        """Remove the sandbox and REPORT whether it is gone.

        Never `ignore_errors=True`: a deletion that fails must be known, not
        swallowed. Read-only files (git objects on Windows are read-only) are
        made writable and retried; a still-locked tree is retried a bounded
        number of times; whatever remains is recorded in `cleanup_error`.
        Nothing outside the sandbox parent is ever removed — the source
        repository is not ours to delete."""
        path = Path(path)
        try:
            resolved = path.resolve()
        except OSError as exc:
            self.cleanup_error = f"cannot resolve {path}: {exc}"
            self.cleanup_removed = False
            return False
        parent = self._sandbox_parent()
        inside = resolved != parent and parent in resolved.parents
        if not inside or self.source_repo == resolved or self.source_repo in resolved.parents:
            self.cleanup_error = f"refusing to delete outside the sandbox root: {resolved}"
            self.cleanup_removed = False
            logger.error(self.cleanup_error)
            return False
        if not path.exists() and not path.is_symlink():
            self.cleanup_error = None
            self.cleanup_removed = True
            return True

        def _make_writable(func, target, exc_info):
            # Windows: the read-only attribute on the entry blocks unlink/rmdir.
            # POSIX: a read-only PARENT directory blocks unlinking its entries
            # (a non-root CI runner failed exactly here while root deleted
            # regardless). Make both writable, then retry the one operation.
            for candidate in (os.path.dirname(str(target)), str(target)):
                try:
                    os.chmod(candidate, stat.S_IRWXU)
                except OSError:
                    pass
            func(target)

        last_error = ""
        for attempt in range(self.CLEANUP_ATTEMPTS):
            try:
                if sys.version_info >= (3, 12):
                    shutil.rmtree(str(path), onexc=_make_writable)
                else:  # pragma: no cover - 3.11 signature
                    shutil.rmtree(str(path), onerror=_make_writable)
            except FileNotFoundError:
                pass
            except OSError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            if not path.exists():
                break
            gc.collect()                       # drop stray handles before the next try
            time.sleep(0.15 * (attempt + 1))
        removed = not path.exists()
        self.cleanup_removed = removed
        self.cleanup_error = None if removed else (last_error or f"sandbox still present: {path}")
        if removed:
            logger.info(f"Cleaned up worktree at {path}")
        else:
            logger.error(f"Failed to clean up {path}: {self.cleanup_error}")
        return removed

    def cleanup(self) -> bool:
        """Delete the sandbox. Nothing has to be unregistered in the source.

        The old implementation called `git worktree remove` in the SOURCE
        repository, which is both unnecessary for a clone and a reminder of why
        the clone is safer: a linked worktree left state in the source that the
        cleanup had to reach back and undo, and the branch it created was never
        undone at all.

        Returns whether the sandbox is gone. On failure `root` is KEPT so the
        caller can retry or report the path; `cleanup_state()` says what
        happened either way."""
        if not self.root:
            return self.cleanup_removed is not False
        if self.keep_after:
            return True
        removed = self._cleanup(self.root)
        if removed and self._tempdir is not None and self._tempdir != self.root:
            # The mkdtemp parent is ours too; an empty leftover directory per run
            # is a slow leak with a friendly name.
            try:
                self._tempdir.rmdir()
            except OSError:
                pass
        if removed:
            self.root = None
        return removed

    def cleanup_state(self) -> Dict[str, Any]:
        """What a caller may truthfully report about the sandbox on disk."""
        return {
            'removed': self.cleanup_removed,
            'path': str(self.root) if self.root else None,
            'error': self.cleanup_error,
            'kept_on_purpose': bool(self.keep_after),
        }

    def __enter__(self) -> 'IsolatedWorktree':
        self.create()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()


__all__ = ['IsolatedWorktree']
