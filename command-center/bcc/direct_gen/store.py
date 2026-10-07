"""Private per-participant job storage (JSON on disk, atomic writes).

Layout: <data_dir>/direct-gen/participants/<key>/jobs/<job_id>/job.json (+ result, inputs).
A participant can only ever address its own directory: the key is validated
and every lookup is built from it, so another participant's job_id is simply 404.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

PARTICIPANT = re.compile(r"^(owner|[0-9a-f]{64})$")
JOB_ID = re.compile(r"^[0-9a-f]{32}$")


def valid_participant(value: str | None) -> str:
    key = (value or "owner").strip().lower()
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
        path = folder / "job.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)

    def load(self, participant: str, job_id: str) -> dict[str, Any]:
        try:
            path = self.job_dir(participant, job_id) / "job.json"
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, KeyError):
            raise KeyError(job_id) from None

    def list(self, participant: str) -> list[dict[str, Any]]:
        base = self.participant_dir(participant) / "jobs"
        out = []
        for path in base.glob("*/job.json") if base.is_dir() else []:
            try:
                out.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return sorted(out, key=lambda j: j.get("created_at", ""), reverse=True)

    def all_unfinished(self) -> list[dict[str, Any]]:
        out = []
        for path in self.root.glob("*/jobs/*/job.json") if self.root.is_dir() else []:
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if job.get("status") in ("queued", "loading", "generating", "postprocessing"):
                out.append(job)
        return out

    def save_assist(self, participant: str, record: dict[str, Any]) -> None:
        folder = self.participant_dir(participant) / "assist"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{record['assist_id']}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)

    def load_assist(self, participant: str, assist_id: str) -> dict[str, Any]:
        if not JOB_ID.fullmatch(str(assist_id)):
            raise KeyError(assist_id)
        try:
            path = self.participant_dir(participant) / "assist" / f"{assist_id}.json"
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise KeyError(assist_id) from None
