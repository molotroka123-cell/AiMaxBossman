"""One-shot held-out evaluation and receipt verification; never feeds training."""
from __future__ import annotations

import hashlib
from pathlib import Path
import time
import uuid

from . import runner as r
from .protocol import signature, seal_evidence, verify_evidence


def verify_campaign(work: Path, state: dict | None = None) -> dict:
    work = work.resolve()
    if state is None:
        state = r.json.loads((work / "state.json").read_text(encoding="utf-8"))
    count = 0
    for row, folder_key, hash_key in [
        *((a, "evidence", "evidence_manifest_sha256") for a in state["attempts"]),
        *((a, "baseline", "baseline_manifest_sha256") for a in state.get("rounds", [])),
        *((a, "evidence", "evidence_manifest_sha256") for a in [state.get("holdout", {})] if a),
    ]:
        if hash_key not in row:
            if row.get("status") in {"STARTED", "INTERRUPTED"}:
                continue
            raise ValueError("Completed experiment is missing an evidence receipt")
        folder = Path(row[folder_key])
        if folder.is_symlink() or not folder.resolve().is_relative_to(work):
            raise ValueError("Evidence is outside the campaign workspace")
        verify_evidence(folder, row[hash_key])
        count += 1
    return {"status": "EVIDENCE_INTACT", "receipts": count,
            "candidate_sha": state["champion_sha"], "production_promoted": False}


def validate(repo: Path, suite: dict, work: Path, *, executor="docker", image="bossman-evolution:1.1",
             timeout=180, repeats=2, max_seconds=1800) -> dict:
    """Consume a disjoint suite once, including failed/interrupted validations.

    Held-out files live in the frozen repository but are never included in model
    prompts or LearningStore. This is separation from this campaign, not a claim
    that the model has never encountered equivalent public tests.
    """
    if (executor not in {"host", "docker"} or type(repeats) is not int or not 2 <= repeats <= 5
            or type(timeout) is not int or timeout < 1 or type(max_seconds) is not int or max_seconds < 1):
        raise ValueError("Invalid validation limits")
    repo, work = repo.resolve(), work.resolve()
    if repo == work or repo in work.parents:
        raise ValueError("Validation workspace must be outside the source checkout")
    if r.git(repo, "status", "--porcelain", "--untracked-files=normal"):
        raise ValueError("Commit or preserve source changes before validation")
    if any(c["role"] != "holdout" or c.get("editable") for c in suite["cases"]):
        raise ValueError("Use a held-out suite without repair targets")
    deadline = time.monotonic() + max_seconds
    with r.campaign_lock(work):
        state_path = work / "state.json"
        state = r.json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("validation_consumed"):
            raise ValueError("Holdout already consumed; validation cannot be retried for selection")
        if r.git(repo, "rev-parse", "HEAD") != state["base_sha"]:
            raise ValueError("Validation must use the campaign base checkout")
        evaluator_hash = r.digest({p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                   for p in Path(__file__).parent.glob("*.py")})
        if evaluator_hash != state["evaluator_hash"]:
            raise ValueError("Evaluator changed since training")
        if state["champion_sha"] == state["base_sha"]:
            raise ValueError("No selected candidate to validate")
        selected = next((a for a in state["attempts"] if a.get("candidate_sha") == state["champion_sha"]), None)
        if not selected or selected.get("review_status") != "ACCEPTED":
            raise ValueError("Independent model review required before holdout")
        heldout = {p for c in suite["cases"] for p in c["tests"]}
        if heldout.intersection(state["training_files"]):
            raise ValueError("Holdout overlaps training/regression test files")
        verify_campaign(work, state)
        if executor == "docker":
            code, resolved = r.command(["docker", "image", "inspect", "--format", "{{.Id}}", image], repo, timeout=30)
            if code or not resolved.strip().startswith("sha256:"):
                raise ValueError("Prebuilt validation image unavailable")
            image = resolved.strip()
        environment = r.sys.version + "|" + r.sys.platform + "|" + executor + "|" + (image if executor == "docker" else r.sys.executable)
        if environment != state["environment"]:
            raise ValueError("Validation environment differs from training")
        if (work / "STOP").exists():
            raise ValueError("Campaign stopped")
        # Persist consumption before observing any held-out result, including crashes.
        state["validation_consumed"] = True
        state["status"] = "HOLDOUT_STARTED"
        r.atomic_json(state_path, state)
        folder = work / "holdout" / uuid.uuid4().hex[:16]
        report = {"status": "STARTED", "suite": suite["fingerprint"], "evidence": str(folder),
                  "base_sha": state["base_sha"], "candidate_sha": state["champion_sha"],
                  "production_promoted": False}
        try:
            results = {}
            for label, sha in (("baseline", state["base_sha"]), ("candidate", state["champion_sha"])):
                snapshot = work / "worktrees" / ("holdout-" + label)
                r.git(repo, "worktree", "add", "--detach", str(snapshot), sha)
                try:
                    first = None
                    for repeat in range(repeats):
                        if (work / "STOP").exists() or time.monotonic() >= deadline:
                            raise ValueError("Validation interrupted by stop/time limit")
                        result = r.evaluate(snapshot, suite, folder / label / str(repeat), timeout,
                                            executor=executor, image=image, deadline=deadline)
                        if any(v["status"] == "BLOCKED" for v in result.values()):
                            raise ValueError("Blocked held-out execution")
                        if first is not None and signature(first) != signature(result):
                            raise ValueError("Unstable held-out results")
                        first = result
                    results[label] = first
                finally:
                    r.git(repo, "worktree", "remove", "--force", str(snapshot))
            for case, before in results["baseline"].items():
                after = results["candidate"][case]
                if set(before["tests"]) != set(after["tests"]) or after["status"] != "PASS":
                    raise ValueError("Candidate failed held-out tests or changed test inventory")
            report["status"] = "HOLDOUT_PASSES"
        except (ValueError, RuntimeError, OSError, r.subprocess.TimeoutExpired) as exc:
            report.update(status="HOLDOUT_BLOCKED", reason=r.redact_text(str(exc))[:2000])
        finally:
            r.atomic_json(folder / "validation.json", report)
            report["evidence_manifest_sha256"] = seal_evidence(folder, {
                "base_sha": state["base_sha"], "candidate_sha": state["champion_sha"],
                "suite": suite["fingerprint"]}, r.atomic_json)
            state["holdout"] = report
            state["status"] = report["status"]
            r.atomic_json(state_path, state)
            r.atomic_json(work / "report.json", state)
        return report
