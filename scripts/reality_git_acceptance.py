"""Real local git acceptance: Compound -> policy -> IO -> independent proofs.

Usage: python scripts/reality_git_acceptance.py --output <NEW empty directory>
Only a newly created throwaway repository and its local bare remote are modified.
No user repository push, model, cloud request or API credential is used.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "bossman-core")]

from bossman_shared import evidence, reality_guard as guard
from bossman_shared.reality.contracts import Effect, Mission, Obligation, digest
from bossman_shared.reality.host import LocalHost, persistent_authority
from bossman_shared.reality.policy import Constitution
from bossman_v3.computer_agent.agent import UniversalComputerAgent
from bossman_v3.contracts import (ApprovalDecision, ExecutionReceipt, Observation,
                                PolicyDecision, SideEffectClass, TypedAction, VerificationResult)
from bossman_v3.execution import CompoundRunner, PlanStep
from bossman_v3.memory import TaskJournal
from bossman_v3.organization.bridges import step_to_dict


def run(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    repo, remote = output / "repo", output / "remote.git"
    env = {**os.environ, "GIT_AUTHOR_NAME": "Reality Acceptance", "GIT_AUTHOR_EMAIL": "test@example.invalid",
           "GIT_COMMITTER_NAME": "Reality Acceptance", "GIT_COMMITTER_EMAIL": "test@example.invalid",
           "GIT_AUTHOR_DATE": "2026-09-05T12:00:00+00:00", "GIT_COMMITTER_DATE": "2026-09-05T12:00:00+00:00",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
    def git(*args, cwd=repo, data=None):
        p = subprocess.run(["git", *args], cwd=cwd, input=data.encode("utf-8") if data is not None else None,
                           capture_output=True, timeout=30, env=env, check=True)
        return p.stdout.decode("utf-8").strip()
    git("init", "--initial-branch=main", str(repo), cwd=output)
    git("init", "--bare", str(remote), cwd=output)
    git("config", "core.autocrlf", "false")
    bug = "def add(a, b):\n    return a - b\n"
    fixed = "def add(a, b):\n    return a + b\n"
    (repo / "calc.py").write_text(bug, encoding="utf-8", newline="\n")
    git("add", "calc.py")
    git("commit", "-m", "base: reproduce addition bug")
    base = git("rev-parse", "HEAD")
    base_tree = git("rev-parse", "HEAD^{tree}")
    git("remote", "add", "origin", str(remote))
    git("push", "origin", "HEAD:refs/heads/main")
    # Precompute immutable expected objects without changing the working tree/ref.
    blob = git("hash-object", "-w", "--stdin", data=fixed)
    tree = git("mktree", data=f"100644 blob {blob}\tcalc.py\n")
    commit = git("commit-tree", tree, "-p", base, "-m", "fix: correct addition")
    expected_patch = git("diff", base_tree, tree, "--", "calc.py")

    def test_tree(tree_id):
        source = git("show", f"{tree_id}:calc.py")
        p = subprocess.run([sys.executable, "-I", "-c", source + "\nassert add(2, 3) == 5\n"],
                           cwd=output, capture_output=True, timeout=15)
        return {"tree": tree_id, "source_digest": digest(source), "exit_code": p.returncode}

    expected = {
        "reproduce": {"tree": base_tree, "source_digest": digest(bug.strip()), "exit_code": 1},
        "fix": fixed,
        "test": {"tree": tree, "source_digest": digest(fixed.strip()), "exit_code": 0},
        "commit": {"commit": commit, "tree": tree, "parent": base},
        "push": {"commit": commit, "tree": tree, "patch": expected_patch},
    }
    targets = {name: f"local-git:{name}:{base if name == 'reproduce' else tree}" for name in expected}
    def observe(name):
        if name == "reproduce": return test_tree(base_tree)
        if name == "test": return test_tree(tree)
        if name == "fix": return (repo / "calc.py").read_text(encoding="utf-8")
        if name == "commit":
            return {"commit": git("rev-parse", "HEAD"), "tree": git("rev-parse", "HEAD^{tree}"),
                    "parent": git("rev-parse", "HEAD^")}
        remote_commit = git("--git-dir", str(remote), "rev-parse", "refs/heads/main")
        return {"commit": remote_commit,
                "tree": git("--git-dir", str(remote), "rev-parse", remote_commit + "^{tree}"),
                "patch": git("--git-dir", str(remote), "diff", base, remote_commit, "--", "calc.py")}

    actions = {}
    actions["reproduce"] = lambda: test_tree(base_tree)
    actions["fix"] = lambda: (repo / "calc.py").write_text(fixed, encoding="utf-8", newline="\n")
    actions["test"] = lambda: test_tree(tree)
    actions["commit"] = lambda: git("commit", "-am", "fix: correct addition")
    actions["push"] = lambda: git("push", "origin", "HEAD:refs/heads/main")
    policy = Constitution("controlled-local-owner-v1", max_level=1,
                          allowed_actions=tuple("git." + n for n in actions),
                          allowed_targets=tuple(targets.values()), verifiers=tuple(actions))
    plan = [PlanStep(n, n, TypedAction("git." + n, {"target": targets[n]},
                                    side_effect=SideEffectClass.IDEMPOTENT_WRITE),
                     guard=list(actions)[i - 1] if i else "") for i, n in enumerate(actions)]
    mission = Mission("git-acceptance", "git-acceptance", "reproduce, fix, test, commit and verify remote",
        "controlled-executor", policy.fingerprint,
        tuple(Obligation(n, targets[n], digest(expected[n]), n, max_age_seconds=600) for n in actions),
        tuple(Effect(n, targets[n], "git." + n, digest({"target": targets[n]}), n,
                     "controlled-git", "read-immutable-tree") for n in actions))
    guard.STATE_ROOT = output / "protected"
    os.environ["BOSSMAN_REALITY_ENABLED"] = "1"
    os.environ[evidence.ENV_KEY_FILE] = str(output / "protected" / "keys" / "evidence.key")
    evidence.reset_cache()
    host = LocalHost(output / "protected" / "reality.sqlite", policy=policy,
        authority=persistent_authority({n: "independent-host-verifier" for n in actions}),
        observers={n: (lambda target, n=n: observe(n)) for n in actions},
        actions={"git." + n: actions[n] for n in actions},
        fence_check=lambda *a: True, level_provider=lambda: 1)
    guard.install("controlled", host)
    guard.enroll("compound", "git-acceptance", "git-acceptance", asdict(mission),
                 trusted_ir=asdict(mission), profile="controlled", plan=[step_to_dict(s) for s in plan])

    class Policy:
        def authorize(self, action, context):
            return PolicyDecision(action.action_type in policy.allowed_actions and
                                  action.args["target"] in policy.allowed_targets)
    class Approval:
        def request(self, *a): return ApprovalDecision(False, reason="no additional owner grant")
    class Executor:
        def supports(self, name): return name in policy.allowed_actions
        def execute(self, action):
            started = datetime.now(timezone.utc)
            actions[action.action_type.removeprefix("git.")]()
            return ExecutionReceipt(action.action_type, started, datetime.now(timezone.utc), action.action_type)
    class Observer:
        def observe_fresh(self, action, receipt):
            return Observation(datetime.now(timezone.utc), "fs", {"value": observe(action.action_type.removeprefix("git."))})
    class Verifier:
        def verify(self, action, receipt, observation):
            return VerificationResult(observation.state["value"] == expected[action.action_type.removeprefix("git.")])
    journal = TaskJournal.start(task_id="git-acceptance", root=output / "protected" / "journals",
                                plan=[(s.step_id, s.intent) for s in plan])
    result = CompoundRunner(UniversalComputerAgent(Policy(), Approval(), Executor(), Observer(), Verifier()),
                            journal, model=mission.executor).run(plan)
    if not result.completed:
        raise RuntimeError(result.reason)
    observed = {n: observe(n) for n in actions}
    assert observed == expected
    report = {"status": "PASS", "mode": "real local git + local bare remote; no cloud",
              "base_sha": base, "base_tree": base_tree, "fixed_sha": commit, "fixed_tree": tree,
              "remote_sha": observed["push"]["commit"], "exact_patch": expected_patch,
              "mission_fingerprint": mission.fingerprint, "obligations": 5,
              "observations": observed, "completed_steps": result.executed}
    (output / "evidence.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    run(parser.parse_args().output)
