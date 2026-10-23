"""Private per-participant job storage (JSON on disk, atomic writes).

Layout: <data_dir>/direct-gen/participants/<key>/jobs/<job_id>/job.json (+ result, inputs).
A participant can only ever address its own directory: the key is validated
and every lookup is built from it, so another participant's job_id is simply 404.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

PARTICIPANT = re.compile(r"^(owner|[0-9a-f]{64})$")
JOB_ID = re.compile(r"^[0-9a-f]{32}$")


# Windows refuses os.replace / open while another handle (a reader, an antivirus scan, the indexer) holds the file:
# WinError 5 / 32. Writers are serialised per target file, each writes its OWN temp file, the replace is retried a
# bounded number of times, and readers retry the same transient errors. Measured 23.10: six writers + one reader on
# one job.json failed 1751 of 1800 saves with the old shared "job.tmp".
REPLACE_RETRIES = 12
RETRY_BASE_S = 0.01
_TRANSIENT_WINERRORS = (5, 32, 33)
_locks_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


def _is_transient(exc: OSError) -> bool:
    return isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in _TRANSIENT_WINERRORS


def _lock_for(path: Path) -> threading.Lock:
    key = str(path)
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def atomic_write_text(path: Path, text: str) -> None:
    """One owner per file at a time, a unique temp name, atomic replace with bounded retry (never silently dropped)."""
    path = Path(path)
    with _lock_for(path):
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:12]}.tmp")
        try:
            tmp.write_text(text, encoding="utf-8")
            for attempt in range(REPLACE_RETRIES):
                try:
                    os.replace(tmp, path)
                    return
                except OSError as exc:
                    if not _is_transient(exc) or attempt == REPLACE_RETRIES - 1:
                        raise
                    time.sleep(min(0.25, RETRY_BASE_S * (2 ** attempt)))
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass


def read_text_retry(path: Path) -> str:
    """Read while a writer may be replacing the file; transient Windows sharing errors are retried briefly."""
    for attempt in range(REPLACE_RETRIES):
        try:
            return Path(path).read_text(encoding="utf-8")
        except FileNotFoundError:
            raise
        except OSError as exc:
            if not _is_transient(exc) or attempt == REPLACE_RETRIES - 1:
                raise
            time.sleep(min(0.25, RETRY_BASE_S * (2 ** attempt)))
    raise OSError("unreachable")


def valid_participant(value: str | None) -> str:
    if value is None:
        return "owner"
    key = value.strip().lower()
    if not PARTICIPANT.fullmatch(key):
        raise ValueError("participant must be 'owner' or a 64-hex participant key")
    return key


class JobStore:
    def __init__(self, data_dir: Path):
        self.root = Path(data_dir) / "direct-gen" / "participants"

    def participant_dir(self, participant: str) -> Path:
        return self.root / valid_participant(participant)

    def job_dir(self, participant: str, job_id: str) -> Path:
        if not JOB_ID.fullmatch(str(job_id)):
            raise KeyError(job_id)
        return self.participant_dir(participant) / "jobs" / job_id

    def save(self, job: dict[str, Any]) -> None:
        folder = self.job_dir(job["participant"], job["job_id"])
        folder.mkdir(parents=True, exist_ok=True)
        atomic_write_text(folder / "job.json", json.dumps(job, ensure_ascii=False, indent=1))

    def load(self, participant: str, job_id: str) -> dict[str, Any]:
        try:
            path = self.job_dir(participant, job_id) / "job.json"
            return json.loads(read_text_retry(path))
        except (OSError, ValueError, KeyError):
            raise KeyError(job_id) from None

    def list(self, participant: str) -> list[dict[str, Any]]:
        base = self.participant_dir(participant) / "jobs"
        out = []
        for path in base.glob("*/job.json") if base.is_dir() else []:
            try:
                out.append(json.loads(read_text_retry(path)))
            except (OSError, ValueError):
                continue
        return sorted(out, key=lambda j: j.get("created_at", ""), reverse=True)

    def all_unfinished(self) -> list[dict[str, Any]]:
        out = []
        for path in self.root.glob("*/jobs/*/job.json") if self.root.is_dir() else []:
            try:
                job = json.loads(read_text_retry(path))
            except (OSError, ValueError):
                continue
            if job.get("status") in ("queued", "loading", "generating", "postprocessing"):
                out.append(job)
        return out

    def save_assist(self, participant: str, record: dict[str, Any]) -> None:
        folder = self.participant_dir(participant) / "assist"
        folder.mkdir(parents=True, exist_ok=True)
        atomic_write_text(folder / f"{record['assist_id']}.json", json.dumps(record, ensure_ascii=False, indent=1))

    def load_assist(self, participant: str, assist_id: str) -> dict[str, Any]:
        if not JOB_ID.fullmatch(str(assist_id)):
            raise KeyError(assist_id)
        try:
            path = self.participant_dir(participant) / "assist" / f"{assist_id}.json"
            return json.loads(read_text_retry(path))
        except (OSError, ValueError):
            raise KeyError(assist_id) from None
