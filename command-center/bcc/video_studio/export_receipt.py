"""Host-bound render proof: expensive verification runs in the executor, not a hook.

The native renderer independently decodes/probes the output before publication.
This module binds that observation to the published file and exact job/run/input.
On POSIX the hook checks the signed observation and fresh change-time identity.
Windows stat ctime is not a content-change clock: rehash there before accepting.
Download still hashes the artifact; stat identity is NOT a general trust cache or
protection against an administrator restoring an entire valid machine snapshot.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from bossman_shared import evidence
from .media import blocking, digest_file

RECEIPT_KEY = "_render_receipt"
DOMAIN = "bossman.video.render.v1"
REQUIRES_CONTENT_RECHECK = os.name == "nt"


class RenderReceiptInvalid(ValueError):
    """Missing, changed, cross-run or otherwise untrusted render observation."""


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def file_identity(path: Path) -> list[int]:
    stat = path.stat()
    if not path.is_file() or stat.st_size <= 0:
        raise RenderReceiptInvalid("render artifact is missing or empty")
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


def binding(root: Path, row: dict, task_id: int, run_id: int, result: dict) -> dict:
    if row.get("task_id") != task_id or not result.get("path"):
        raise RenderReceiptInvalid("render job/task binding mismatch")
    if type(run_id) is not int or run_id <= 0:
        raise RenderReceiptInvalid("invalid render run identity")
    directory = root.resolve() / "exports" / row["id"] / str(run_id)
    path = Path(result["path"]).resolve()
    if not path.is_relative_to(directory):
        raise RenderReceiptInvalid("artifact ownership mismatch")
    proof = result.get("verification") or {}
    sha = result.get("sha256")
    if (not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha)
            or proof.get("sha256") != sha or proof.get("passed") is not True
            or proof.get("decoded") is not True or proof.get("failures") != []):
        raise RenderReceiptInvalid("independent render verification missing or invalid")
    identity = file_identity(path)
    if proof.get("bytes") != identity[2]:
        raise RenderReceiptInvalid("artifact size differs from independent verification")
    return {"domain": DOMAIN, "job_id": row["id"], "task_id": task_id,
        "run_id": run_id, "project_id": row["project_id"],
        "snapshot_hash": _hash(row["snapshot"]), "options_hash": _hash(row["options"]),
        "result_hash": _hash({k: v for k, v in result.items() if k != RECEIPT_KEY}),
        "path": str(path), "sha256": sha, "file_identity": identity}


async def certify(root: Path, row: dict, task_id: int, run_id: int, result: dict) -> dict:
    """Executor-only: bind published bytes to the renderer's full decode oracle."""
    before = binding(root, row, task_id, run_id, result)
    actual = await blocking(digest_file, Path(before["path"]))
    after = binding(root, row, task_id, run_id, result)
    if before != after or actual != before["sha256"]:
        raise RenderReceiptInvalid("artifact changed after independent render verification")
    return {**after, **evidence.sign_fields(after, signer="bcc.v2.verification")}


def validate(root: Path, row: dict, task_id: int, run_id: int, result: dict) -> None:
    """No decode, hash worker or model call is permitted inside this hook check."""
    receipt = result.get(RECEIPT_KEY)
    if (not isinstance(receipt, dict) or receipt.get("signer") != "bcc.v2.verification"
            or not evidence.verify_signed(receipt)):
        raise RenderReceiptInvalid("missing or invalid current render receipt; re-verification required")
    expected = binding(root, row, task_id, run_id, result)
    observed = {k: v for k, v in receipt.items() if k not in evidence.SIG_FIELDS}
    if observed != expected:
        raise RenderReceiptInvalid("render receipt no longer matches job, run or artifact")


async def validate_for_gate(root: Path, row: dict, task_id: int, run_id: int, result: dict) -> None:
    validate(root, row, task_id, run_id, result)
    if REQUIRES_CONTENT_RECHECK:
        # A Windows writer can restore mtime while ctime remains creation time.
        # Do not mistake those metadata for content integrity. This is a hash,
        # never a second decode. Very large Windows exports need latency tests;
        # integrity is not bypassed to meet the hook's deadline.
        expected = binding(root, row, task_id, run_id, result)
        actual = await blocking(digest_file, Path(expected["path"]))
        validate(root, row, task_id, run_id, result)
        if actual != expected["sha256"]:
            raise RenderReceiptInvalid("artifact content changed before finalization")
