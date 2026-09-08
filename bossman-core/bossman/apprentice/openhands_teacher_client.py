"""Adapter that makes OpenHands look like an untrusted TeacherFallback client.

The real repository is never given to OpenHands.  A sanitized ProblemBundle is
materialised in a temporary Git repository with no remotes; OpenHands may edit
that copy.  Bossman then reads the resulting files and returns a patch proposal
to the existing independent PatchVerifier, which alone may apply it to the real
verifier worktree.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any, Mapping

from .openhands_client import OpenHandsClient, OpenHandsError, OpenHandsRequest

_CONTRACT = ".bossman/OPENHANDS_TASK.json"


def _git(workspace: Path, *args: str) -> None:
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
        raise OpenHandsError(f"sanitized git {' '.join(args)} failed: {proc.stderr.strip()}")


def _safe_relative(path: str) -> Path:
    raw = str(path).replace("\\", "/")
    if not raw or raw.startswith("/") or (len(raw) > 1 and raw[1] == ":") or ".." in raw.split("/") or "\x00" in raw:
        raise OpenHandsError(f"unsafe bundle path: {path!r}")
    return Path(raw)


def _in_scope(path: str, allowed: tuple[str, ...]) -> bool:
    p = _safe_relative(path).as_posix()
    for prefix in allowed:
        a = _safe_relative(prefix).as_posix().rstrip("/")
        if p == a or p.startswith(a + "/"):
            return True
    return False


class OpenHandsTeacherClient:
    """``client.run(bundle_dict)`` implementation for existing TeacherFallback."""

    def __init__(self, client: OpenHandsClient, *, model: str | None = None, timeout_seconds: int = 900) -> None:
        self.client = client
        self.model = model
        self.timeout_seconds = int(timeout_seconds)
        self.calls = 0

    def _instruction(self, bundle: Mapping[str, Any]) -> str:
        constraints = "\n".join(f"- {x}" for x in (bundle.get("constraints") or ()))
        return (
            "You are an untrusted coding worker inside a disposable sanitized repository.\n"
            "Solve the task by editing only the explicitly allowed source paths.\n"
            "Do not change .bossman/, acceptance tests, policy/security files, git configuration, or remotes.\n"
            "Do not push, deploy, create credentials, or claim mission completion.\n"
            "Run useful local checks if available; Bossman will independently verify every resulting change.\n\n"
            f"TASK:\n{str(bundle.get('bug_description') or '')[:4000]}\n\n"
            f"FAILING TEST / OBSERVATION:\n{str(bundle.get('failing_test') or '')[:4000]}\n\n"
            f"CONSTRAINTS:\n{constraints[:6000]}"
        )

    def run(self, bundle: dict) -> dict[str, Any]:
        allowed = tuple(str(x) for x in (bundle.get("allowed_paths") or ()))
        if not allowed:
            raise OpenHandsError("OpenHands teacher requires non-empty allowed_paths")
        files = dict(bundle.get("files") or {})
        if not files:
            raise OpenHandsError("OpenHands teacher requires a non-empty sanitized file bundle")
        for path in files:
            if not _in_scope(str(path), allowed):
                raise OpenHandsError(f"bundle file outside allowed_paths: {path}")

        root = Path(tempfile.mkdtemp(prefix="bossman-openhands-"))
        try:
            for path, content in files.items():
                rel = _safe_relative(str(path))
                dest = root / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(str(content), encoding="utf-8")

            contract = root / _CONTRACT
            contract.parent.mkdir(parents=True, exist_ok=True)
            contract.write_text(json.dumps({
                "schema": "bossman.openhands.teacher.v1",
                "bundle_id": str(bundle.get("bundle_id") or ""),
                "allowed_paths": list(allowed),
                "constraints": list(bundle.get("constraints") or ()),
                "acceptance_test_ids": list(bundle.get("acceptance_tests") or ()),
            }, sort_keys=True, ensure_ascii=False, indent=2), encoding="utf-8")

            _git(root, "init")
            _git(root, "config", "user.email", "bossman-openhands@example.invalid")
            _git(root, "config", "user.name", "Bossman OpenHands Sandbox")
            _git(root, "add", ".")
            _git(root, "commit", "-m", "sanitized baseline")

            self.calls += 1
            result = self.client.run(OpenHandsRequest(
                instruction=self._instruction(bundle),
                workspace=root,
                allowed_paths=allowed,
                protected_paths=(_CONTRACT, *tuple(str(x) for x in (bundle.get("acceptance_tests") or ()))),
                model=self.model,
                timeout_seconds=self.timeout_seconds,
                metadata={"bundle_id": str(bundle.get("bundle_id") or "")},
            ))

            if result.status != "completed":
                return {
                    "opened_files": sorted(files), "symbols": [], "root_cause": "",
                    "patch": {}, "test_results": {"sidecar_status": result.status},
                    "attempt_errors": ["OpenHands sidecar reported failure"], "commands": [],
                    "artifacts": ["openhands:failed", "openhands:no-git-remote"],
                    "model_id": str(result.sidecar.get("model") or self.model or "openhands"),
                    "model_version": "provider-reported-or-unknown", "status": "UNTRUSTED_TEACHER_OUTPUT",
                }

            patch: dict[str, str] = {}
            for path in result.changed_files:
                file_path = root / _safe_relative(path)
                if not file_path.exists() or not file_path.is_file():
                    raise OpenHandsError(f"OpenHands deletion/non-file change is not admissible: {path}")
                try:
                    patch[path] = file_path.read_text(encoding="utf-8")
                except UnicodeDecodeError as exc:
                    raise OpenHandsError(f"binary/non-UTF8 OpenHands change is not admissible: {path}") from exc

            return {
                "opened_files": sorted(files),
                "symbols": [],
                "root_cause": "OpenHands produced a candidate patch in a sanitized disposable repository.",
                "patch": patch,
                "test_results": {"sidecar_status": result.status},
                "attempt_errors": [],
                "commands": [],
                "artifacts": [f"openhands:changed_files={len(result.changed_files)}", "openhands:no-git-remote"],
                "model_id": str(result.sidecar.get("model") or self.model or "openhands"),
                "model_version": "provider-reported-or-unknown",
                "status": "UNTRUSTED_TEACHER_OUTPUT",
            }
        finally:
            shutil.rmtree(root, ignore_errors=True)
