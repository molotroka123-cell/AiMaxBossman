"""Small candidate tournaments over a fixed baseline with a separate reviewer."""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
import sys
import time
import uuid

from . import runner as r
from .protocol import (signature, gains, rank_candidates, model_identity,
                       review_input, review_prompt, validate_review, seal_evidence)


def _reserve(state, path, amount, maximum):
    if state["reserved_usd"] + amount > maximum + 1e-9:
        raise ValueError("BUDGET_EXHAUSTED")
    state["reserved_usd"] += amount
    r.atomic_json(path, state)


def _account(state, cost, reserved):
    if cost is None:
        state["unknown_cost_calls"] += 1
        return
    if type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0:
        raise ValueError("Invalid provider cost")
    state["reported_cost_usd"] += cost
    if cost > reserved:
        state["reserved_usd"] += cost - reserved
        raise ValueError("Provider exceeded reserved budget")


def run(repo: Path, suite: dict, work: Path, *, proposer=None, iterations: int = 3,
        max_usd: float = 2.0, proposal_usd: float = 0.5, timeout: int = 180,
        repeats: int = 2, local: bool = False, model: str = "unknown",
        executor: str = "docker", image: str = "bossman-evolution:1.1", max_seconds: int = 1800,
        candidates: int = 1, reviewer=None, reviewer_model: str = "",
        review_local: bool = False, review_usd: float = 0.25, require_review: bool = False) -> dict:
    if (any(type(v) is not int for v in (iterations, repeats, timeout, max_seconds, candidates))
            or not 1 <= iterations <= 20 or not 1 <= candidates <= 5 or not 2 <= repeats <= 5
            or timeout < 1 or max_seconds < 1 or executor not in {"host", "docker"}
            or any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0
                   for v in (max_usd, proposal_usd, review_usd))):
        raise ValueError("Invalid experiment limits")
    if proposer and require_review and reviewer is None:
        raise ValueError("Independent reviewer required before model experiments")
    if reviewer and (not reviewer_model or model == "unknown"
                     or model_identity(model) == model_identity(reviewer_model)):
        raise ValueError("Builder and reviewer need distinct explicit model IDs")
    repo, work = repo.resolve(), work.resolve()
    if work == repo or repo in work.parents:
        raise ValueError("Experiment workspace must be outside the source checkout")
    base = r.git(repo, "rev-parse", "HEAD")
    if r.git(repo, "status", "--porcelain", "--untracked-files=normal"):
        raise ValueError("Commit or preserve source changes before running evolution")
    if executor == "docker":
        code, image_id = r.command(["docker", "image", "inspect", "--format", "{{.Id}}", image], repo, timeout=30)
        if code or not image_id.strip().startswith("sha256:"):
            raise ValueError("Prebuilt evaluation image unavailable; build config/evolution/Dockerfile")
        image = image_id.strip()
    runtime = sys.version + "|" + sys.platform + "|" + executor + "|" + (image if executor == "docker" else sys.executable)
    evaluator_hash = r.digest({p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in Path(__file__).parent.glob("*.py")})
    deadline = time.monotonic() + max_seconds
    with r.campaign_lock(work):
        path = work / "state.json"
        if path.exists():
            state = r.json.loads(path.read_text(encoding="utf-8"))
            if state["base_sha"] != base or state["suite"] != suite["fingerprint"]:
                raise ValueError("Base/suite changed: use a new campaign workspace")
            if state.get("environment") != runtime or state.get("evaluator_hash") != evaluator_hash:
                raise ValueError("Runtime/evaluator changed: use a new campaign workspace")
            if proposer and state.get("validation_consumed"):
                raise ValueError("Campaign sealed by holdout evaluation; training cannot resume")
            if proposer and state.get("review_required") != require_review:
                raise ValueError("Review policy changed: use a new campaign workspace")
            from .validation import verify_campaign
            verify_campaign(work, state)
        else:
            state = {"version": 2, "base_sha": base, "champion_sha": base, "suite": suite["fingerprint"],
                     "reserved_usd": 0.0, "reported_cost_usd": 0.0, "unknown_cost_calls": 0,
                     "attempts": [], "rounds": [], "seen_patches": [], "status": "NEW",
                     "environment": runtime, "evaluator_hash": evaluator_hash,
                     "training_files": sorted({p for c in suite["cases"] for p in c["tests"]}),
                     "production_promoted": False, "review_required": require_review}
        for attempt in state["attempts"]:
            if attempt["status"] == "STARTED":
                attempt["status"] = "INTERRUPTED"
        state["max_usd"] = max_usd
        memory = r.LearningStore(work / "learning", work / "learning-docs")

        def stopped():
            return (work / "STOP").exists() or time.monotonic() >= deadline

        def evaluate(snapshot, folder):
            return r.evaluate(snapshot, suite, folder, timeout, executor=executor, image=image, deadline=deadline)

        try:
            for _ in range(iterations if proposer else 1):
                if stopped():
                    state["status"] = "STOPPED"
                    break
                round_id = uuid.uuid4().hex[:16]
                before = state["champion_sha"]
                base_worktree = work / "worktrees" / ("baseline-" + round_id)
                base_worktree.parent.mkdir(parents=True, exist_ok=True)
                baseline_folder = work / "baselines" / round_id
                r.git(repo, "worktree", "add", "--detach", str(base_worktree), before)
                try:
                    baseline = evaluate(base_worktree, baseline_folder / "repeat-0")
                    state["last_baseline"] = baseline
                    state["last_evidence"] = str(baseline_folder)
                    if any(x["status"] == "BLOCKED" for x in baseline.values()):
                        state["status"] = "BLOCKED_BASELINE"
                        break
                    consistent = all(signature(evaluate(base_worktree, baseline_folder / f"repeat-{i}")) == signature(baseline)
                                     for i in range(1, repeats))
                    if not consistent:
                        state["status"] = "BLOCKED_FLAKY_BASELINE"
                        break
                    failing = [c for c in suite["cases"] if c["role"] == "train" and c.get("editable")
                               and baseline[c["id"]]["status"] == "FAIL"]
                    if not proposer:
                        state["status"] = "ASSESSED"
                        break
                    if not failing:
                        state["status"] = "NEEDS_NEW_SCENARIOS" if all(x["status"] == "PASS" for x in baseline.values()) else "BLOCKED_REGRESSION"
                        break
                    failing.sort(key=lambda c: sum(a.get("scenario") == c["id"] for a in state["attempts"]))
                    case = failing[0]
                    prompt = r.prompt_for(base_worktree, case, baseline[case["id"]], memory)
                finally:
                    r.git(repo, "worktree", "remove", "--force", str(base_worktree))

                baseline_hash = seal_evidence(baseline_folder, {"base_sha": before, "suite": suite["fingerprint"]}, r.atomic_json)
                round_record = {"id": round_id, "base_sha": before, "baseline": str(baseline_folder),
                                "baseline_manifest_sha256": baseline_hash, "selected": None}
                state["rounds"].append(round_record)
                completed = []
                for ordinal in range(candidates):
                    if stopped():
                        state["status"] = "STOPPED"
                        break
                    if not local and state["reserved_usd"] + proposal_usd > max_usd + 1e-9:
                        state["status"] = "BUDGET_EXHAUSTED"
                        break
                    trial_id = uuid.uuid4().hex[:16]
                    evidence = work / "runs" / trial_id
                    candidate = work / "worktrees" / trial_id
                    attempt = {"id": trial_id, "round": round_id, "scenario": case["id"],
                               "before": before, "status": "STARTED", "evidence": str(evidence),
                               "builder_model": model,
                               "reserved_usd": 0.0 if local else proposal_usd, "review_status": "PENDING"}
                    state["attempts"].append(attempt)
                    _reserve(state, path, attempt["reserved_usd"], max_usd)
                    r.git(repo, "worktree", "add", "--detach", str(candidate), before)
                    try:
                        provider_cwd = evidence / "provider"
                        provider_cwd.mkdir(parents=True)
                        remaining = min(timeout, deadline - time.monotonic())
                        if remaining <= 0:
                            raise ValueError("Campaign time limit reached")
                        variation = f"\nIndependent alternative {ordinal + 1}/{candidates}. Prefer a minimal general fix."
                        proposal, cost = proposer(prompt + variation, provider_cwd, proposal_usd, remaining)
                        attempt["reported_cost_usd"] = cost
                        _account(state, cost, attempt["reserved_usd"])
                        if not isinstance(proposal, dict):
                            raise ValueError("Proposal must be a JSON object")
                        attempt["summary"] = str(proposal.get("summary", ""))[:1000]
                        paths = r.apply_edits(candidate, proposal, case["editable"])
                        attempt["files"] = paths
                        patch = r.git(candidate, "diff", "--binary", "--no-ext-diff")
                        patch_id = r.digest([before, patch])
                        if patch_id in state["seen_patches"]:
                            raise ValueError("Duplicate patch: evaluation not repeated")
                        state["seen_patches"].append(patch_id)
                        observed, first = [], None
                        for repeat in range(repeats):
                            results = evaluate(candidate, evidence / f"candidate-{repeat}")
                            ok, reason = r.improvement(baseline, results)
                            if first is not None and signature(first) != signature(results):
                                raise ValueError("Unstable candidate results")
                            if not ok:
                                raise ValueError(reason)
                            first = results
                            observed.append({"ok": True, "reason": reason})
                        if r.git(candidate, "diff", "--binary", "--no-ext-diff") != patch:
                            raise ValueError("Tests changed candidate source")
                        if set(r.git(candidate, "diff", "--name-only").splitlines()) != set(paths):
                            raise ValueError("Unexpected candidate changes")
                        r.git(candidate, "add", "--", *paths)
                        r.git(candidate, "-c", "user.name=Bossman Evolution", "-c", "user.email=bossman@localhost",
                              "commit", "-m", "evo: measured candidate for " + case["id"])
                        after = r.git(candidate, "rev-parse", "HEAD")
                        branch = "evo/candidate-" + trial_id
                        r.git(repo, "update-ref", "refs/heads/" + branch, after, "")
                        attempt.update(status="CANDIDATE_PASSES", candidate_sha=after, branch=branch,
                                       checks=observed, gained_tests=gains(baseline, first),
                                       changed_lines=sum(line[:1] in {"+", "-"} and not line.startswith(("+++", "---"))
                                                         for line in patch.splitlines()))
                        sources = {p: r.safe_file(candidate, p).read_text(encoding="utf-8") for p in paths}
                        completed.append((attempt, review_input(before, patch, sources, case["goal"])))
                    except (ValueError, RuntimeError, OSError, KeyError, TypeError, SyntaxError, r.subprocess.TimeoutExpired) as exc:
                        attempt.update(status="REJECTED", reason=r.redact_text(str(exc))[:2000])
                    finally:
                        r.git(repo, "worktree", "remove", "--force", str(candidate))
                        r.atomic_json(path, state)

                winner = None
                contexts = {a["id"]: context for a, context in completed}
                for attempt in rank_candidates([a for a, _ in completed]):
                    if stopped():
                        break
                    if reviewer:
                        try:
                            reserved = 0.0 if review_local else review_usd
                            _reserve(state, path, reserved, max_usd)
                            attempt["review_reserved_usd"] = reserved
                            context = contexts[attempt["id"]]
                            r.atomic_json(Path(attempt["evidence"]) / "review-input.json", context)
                            reviewer_cwd = Path(attempt["evidence"]) / "reviewer"
                            reviewer_cwd.mkdir()
                            response, cost = reviewer(r.redact_text(review_prompt(context)), reviewer_cwd,
                                                      review_usd, min(timeout, deadline - time.monotonic()))
                            _account(state, cost, reserved)
                            r.atomic_json(Path(attempt["evidence"]) / "independent-review.json",
                                          {"model": reviewer_model, "builder_model": model,
                                           "response": response, "reported_cost_usd": cost})
                            validate_review(response, context)
                            attempt["review_status"] = "ACCEPTED"
                        except (ValueError, RuntimeError, OSError, KeyError, TypeError, r.subprocess.TimeoutExpired) as exc:
                            attempt.update(status="QUARANTINED", review_status="BLOCKED", reason=r.redact_text(str(exc))[:2000])
                            continue
                    winner = attempt
                    state["champion_sha"] = winner["candidate_sha"]
                    round_record["selected"] = winner["id"]
                    break
                state["status"] = "CANDIDATE_PASSES" if winner else ("QUARANTINED" if completed else state.get("status", "REJECTED"))
                if not winner and not completed and state["status"] not in {"BUDGET_EXHAUSTED", "STOPPED"}:
                    state["status"] = "REJECTED"
                for attempt in [a for a in state["attempts"] if a.get("round") == round_id]:
                    folder = Path(attempt["evidence"])
                    r.remember(memory, run_id=attempt["id"], scenario=case["id"], before=before,
                               after=attempt.get("candidate_sha", before), summary=attempt.get("summary", "Proposal incomplete"),
                               result=attempt["status"], detail=attempt.get("reason", ""), paths=attempt.get("files", []),
                               evidence=folder, model=model)
                    r.atomic_json(folder / "experiment.json", attempt)
                    r.atomic_json(folder / "review-request.json", {
                        "base_sha": before, "candidate_sha": attempt.get("candidate_sha"),
                        "required": ["alibaba-open-code-review", "cloudflare-security-audit", "independent-holdout"],
                        "model_review": attempt["review_status"], "review_status": "PENDING", "production_promoted": False})
                    attempt["evidence_manifest_sha256"] = seal_evidence(folder, {
                        "base_sha": before, "candidate_sha": attempt.get("candidate_sha"),
                        "baseline_manifest_sha256": baseline_hash, "suite": suite["fingerprint"]}, r.atomic_json)
                r.atomic_json(path, state)
                if not winner and state["reserved_usd"] >= max_usd:
                    break
            return state
        finally:
            r.atomic_json(path, state)
            r.atomic_json(work / "report.json", state)
