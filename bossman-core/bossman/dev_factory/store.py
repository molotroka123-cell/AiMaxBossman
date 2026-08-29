"""Stage 10 — атомарное хранение состояния задания.

Перезапуск не должен ни терять состояние, ни ПОВТОРЯТЬ консеквентные шаги:
запись идёт tmp+fsync+os.replace, а журнал `performed` переживает рестарт.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .models import (
    DevJob,
    DevStep,
    Evidence,
    JobState,
    Patch,
    RetryBudget,
    StepKind,
    Verdict,
)


def _job_dir(root: Path, job_id: str) -> Path:
    return Path(root) / job_id


def save(root: str | Path, job: DevJob) -> Path:
    d = _job_dir(Path(root), job.id)
    d.mkdir(parents=True, exist_ok=True)
    data = {
        "id": job.id, "task": job.task, "repo_path": job.repo_path,
        "state": job.state.value, "workspace": job.workspace,
        "trusted_repo": job.trusted_repo,
        "budget": {"max_attempts": job.budget.max_attempts, "used": job.budget.used},
        "created_at": job.created_at, "updated_at": job.updated_at,
        "approval_id": job.approval_id, "error": job.error,
        "performed": list(job.performed),
        "history": [[t, s, n] for t, s, n in job.history],
        "patch": None if job.patch is None else {
            "diff": job.patch.diff, "files": list(job.patch.files),
            "sha256": job.patch.sha256, "evidence_summary": job.patch.evidence_summary},
        "steps": [{
            "id": s.id, "kind": s.kind.value, "description": s.description,
            "argv": list(s.argv), "attempts": s.attempts, "done": s.done,
            "evidence": {"verdict": s.evidence.verdict.value, "summary": s.evidence.summary,
                         "stdout_path": s.evidence.stdout_path,
                         "artifacts": list(s.evidence.artifacts),
                         "passed": s.evidence.passed, "failed": s.evidence.failed},
        } for s in job.steps],
    }
    target = d / "job.json"
    tmp = d / "job.json.tmp"
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(target)          # атомарная подмена
    return target


def load(root: str | Path, job_id: str) -> DevJob | None:
    p = _job_dir(Path(root), job_id) / "job.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    job = DevJob(
        id=d["id"], task=d["task"], repo_path=d["repo_path"],
        state=JobState(d["state"]), workspace=d.get("workspace"),
        trusted_repo=bool(d.get("trusted_repo", False)),
        budget=RetryBudget(**d.get("budget", {})),
        created_at=d.get("created_at", 0.0), updated_at=d.get("updated_at", 0.0),
        approval_id=d.get("approval_id"), error=d.get("error"),
        performed=list(d.get("performed", [])),
        history=[(h[0], h[1], h[2]) for h in d.get("history", [])],
    )
    pt = d.get("patch")
    if pt:
        job.patch = Patch(diff=pt["diff"], files=tuple(pt.get("files", ())),
                          sha256=pt.get("sha256", ""),
                          evidence_summary=pt.get("evidence_summary", ""))
    for s in d.get("steps", []):
        ev = s.get("evidence", {})
        job.steps.append(DevStep(
            id=s["id"], kind=StepKind(s["kind"]), description=s["description"],
            argv=tuple(s.get("argv", ())), attempts=s.get("attempts", 0),
            done=s.get("done", False),
            evidence=Evidence(verdict=Verdict(ev.get("verdict", "UNKNOWN")),
                              summary=ev.get("summary", ""),
                              stdout_path=ev.get("stdout_path"),
                              artifacts=tuple(ev.get("artifacts", ())),
                              passed=ev.get("passed", 0), failed=ev.get("failed", 0))))
    return job


def list_jobs(root: str | Path) -> list[str]:
    r = Path(root)
    if not r.exists():
        return []
    return sorted(p.name for p in r.iterdir() if (p / "job.json").exists())
