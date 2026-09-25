#!/usr/bin/env python3
"""Bossman 1.5 free-first owner workflow.

All inference is submitted to the running Bossman API. Public YouTube evidence
is handled by three free Nemotron roles, checked by free Ling, and only then
may Jev allow one paid GLM finalizer. Aster receives an audit packet only.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "command-center"))

from bcc.terminal_cli.api_client import BossmanError, Client, discover  # noqa: E402

NEMO = ("YT-Nemotron-Transcript", "YT-Nemotron-Chart", "YT-Nemotron-Strategy")
LING = "Ling-Fin-Verifier"
GLM = "GLM53-Finalizer"
DONE = {"completed", "failed", "blocked", "stopped", "cancelled"}
TESTS = (
    "command-center/tests/test_economy_swarm.py",
    "command-center/tests/test_market_collector.py",
    "command-center/tests/test_video_learning_primitives.py",
    "tests/test_distill_recorder.py",
)
SKILLS = {
    NEMO[0]: ["external-evidence-check", "context-builder"],
    NEMO[1]: ["measure-do-not-assume", "external-evidence-check"],
    NEMO[2]: ["variant-analysis", "negative-control"],
    LING: ["systematic-debugging", "test-driven-development", "verification-before-completion"],
    GLM: ["safe-code-change", "differential-review", "proof-before-done"],
    "Aster": ["repo-audit", "permission-auditor", "honest-verdict"],
}


def load(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def agent_map(c: Client) -> dict[str, dict]:
    return {str(x["name"]): x for x in (c.get("/api/agents") or []) if x.get("name")}


def submit(c: Client, agent: dict, title: str, prompt: str, rid: str) -> int:
    out = c.post("/api/tasks", {"title": title[:180], "prompt": prompt,
                                "agent_id": int(agent["id"]), "run_now": True,
                                "max_retries": 1, "client_request_id": rid})
    return int(out["task"]["id"])


def wait(c: Client, ids: list[int], timeout_s: int) -> dict[int, dict]:
    pending, out = set(ids), {}
    end = time.monotonic() + timeout_s
    while pending and time.monotonic() < end:
        for tid in list(pending):
            row = c.get(f"/api/tasks/{tid}")
            if str((row.get("task") or {}).get("status")) in DONE:
                out[tid] = row
                pending.remove(tid)
        if pending:
            time.sleep(2)
    for tid in pending:
        try:
            c.post(f"/api/tasks/{tid}/stop", {})
        except BossmanError:
            pass
        out[tid] = c.get(f"/api/tasks/{tid}")
    return out


def usage(row: dict) -> dict[str, Any]:
    cost = 0.0
    unknown = 0
    tin = tout = 0
    aliases = set()
    for run in row.get("runs") or []:
        if run.get("model_alias"):
            aliases.add(str(run["model_alias"]))
        if run.get("cost_usd") is None:
            unknown += 1
        else:
            cost += float(run.get("cost_usd") or 0)
        tin += int(run.get("tokens_in") or 0)
        tout += int(run.get("tokens_out") or 0)
    return {"cost_usd": round(cost, 8), "unknown_cost_runs": unknown,
            "tokens_in": tin, "tokens_out": tout, "models": sorted(aliases)}


def result_text(row: dict) -> str:
    return str(row.get("result") or row.get("error") or "")[:60000]


def parse_object(value: str) -> dict:
    s = value.strip()
    fence = chr(96) * 3
    if s.startswith(fence):
        first = s.find("\n")
        last = s.rfind(fence)
        s = s[first + 1:last if last > first else None]
    try:
        x = json.loads(s)
        return x if isinstance(x, dict) else {}
    except json.JSONDecodeError:
        a, b = s.find("{"), s.rfind("}")
        if 0 <= a < b:
            try:
                x = json.loads(s[a:b + 1])
                return x if isinstance(x, dict) else {}
            except json.JSONDecodeError:
                pass
    return {}


def route(c: Client, stage: str, *, ling_verdict: str = "NOT_RUN", ling_attempts: int = 0,
          unresolved: int = 0, allow_paid: bool = False, spent: float = 0,
          budget: float = 0.25, glm_calls: int = 0) -> dict:
    return c.post("/api/economy/route", {
        "stage": stage, "free_failures": 0, "ling_attempts": ling_attempts,
        "ling_verdict": ling_verdict, "unresolved_blockers": unresolved,
        "allow_paid": allow_paid, "paid_spent_usd": spent,
        "paid_budget_usd": budget, "glm_calls": glm_calls, "require_jev": True})


def cases_for(batch_path: pathlib.Path, video_id: str, limit: int = 80) -> list[dict]:
    p = batch_path.parent / "youtube_inbox" / video_id / "candidate_cases.jsonl"
    rows = []
    if p.is_file():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                x = json.loads(line)
            except json.JSONDecodeError:
                continue
            rows.append({
                "case_id": x.get("case_id"),
                "timestamp_seconds": x.get("timestamp_seconds"),
                "transcript_excerpt": str(x.get("transcript_excerpt") or "")[:1600],
                "observation": x.get("observation") or {},
                "deterministic_analysis": x.get("deterministic_analysis"),
                "future_outcomes": x.get("future_outcomes") or {},
                "learning_status": "UNVERIFIED"})
    if len(rows) > limit:
        idx = sorted({round(i * (len(rows) - 1) / (limit - 1)) for i in range(limit)})
        rows = [rows[i] for i in idx]
    return rows


def bundle(batch_path: pathlib.Path, video: dict) -> dict:
    return {"source": "PUBLIC_YOUTUBE_UNTRUSTED_TEACHER",
            "video_id": video["video_id"], "url": video["url"],
            "title": video.get("title"), "upload_date": video.get("upload_date"),
            "cases": cases_for(batch_path, str(video["video_id"])),
            "rules": ["UNKNOWN stays UNKNOWN", "teacher opinion is not a rule",
                      "future outcomes are evidence, not permission to trade"]}


def nemo_prompt(role: str, data: dict) -> str:
    jobs = {
        NEMO[0]: "Extract timestamped teacher claims, triggers and invalidations; separate opinion from observation.",
        NEMO[1]: "Audit Price/CVD/OI/level evidence, series identity, contradictions and missing data.",
        NEMO[2]: "Synthesize candidate hypotheses with regime, trigger, invalidation and future-outcome evidence.",
    }
    return ("SKILLS: " + ", ".join(SKILLS[role]) + "\n" + jobs[role] +
            "\nReturn compact JSON: summary,evidence,candidate_lessons,contradictions,missing_data. "
            "Everything remains UNVERIFIED. Never trade.\nEVIDENCE:\n" +
            json.dumps(data, ensure_ascii=False))


def ling_prompt(data: dict, workers: dict, failure: str = "") -> str:
    return ("SKILLS: " + ", ".join(SKILLS[LING]) +
            "\nAct as independent FREE verifier/tester. Reject unsupported claims and false DONE. "
            "For repository failures, reproduce with tools and make only a minimal fix. "
            'Return JSON {"verdict":"PASS|FAIL|BLOCKED","unresolved_blockers":[],"verified_candidates":[],' +
            '"rejected_candidates":[],"notes":""}.\nTEST_FAILURE:\n' + (failure[:12000] or "none") +
            "\nPUBLIC_EVIDENCE:\n" + json.dumps(data, ensure_ascii=False) +
            "\nNEMOTRON_OUTPUTS:\n" + json.dumps(workers, ensure_ascii=False))


def glm_prompt(failure: str) -> str:
    return ("SKILLS: " + ", ".join(SKILLS[GLM]) +
            "\nThis is the ONE paid finalizer pass after free workers. Fix only the named failing tests, "
            "make the smallest patch, run verification, do not expand scope or bypass approvals.\nFAILURE:\n" +
            failure[:16000])


def verify_repo() -> dict:
    try:
        p = subprocess.run([sys.executable, "-m", "pytest", "-q", *TESTS], cwd=ROOT,
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=1800, check=False)
        return {"pass": p.returncode == 0, "returncode": p.returncode,
                "tail": (p.stdout + p.stderr)[-12000:]}
    except subprocess.TimeoutExpired:
        return {"pass": False, "returncode": 124, "tail": "pytest timeout"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-manifest", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--url")
    ap.add_argument("--data-dir")
    ap.add_argument("--allow-paid", action="store_true")
    ap.add_argument("--glm-budget-usd", type=float, default=0.25)
    ap.add_argument("--timeout-minutes", type=int, default=90)
    ap.add_argument("--max-videos", type=int, default=0)
    ap.add_argument("--repo-polish", action="store_true")
    ns = ap.parse_args(argv)
    if not 0 <= ns.glm_budget_usd <= 25:
        ap.error("invalid GLM budget")

    bp = pathlib.Path(ns.batch_manifest)
    outdir = pathlib.Path(ns.out)
    outdir.mkdir(parents=True, exist_ok=True)
    batch = load(bp)
    videos = [v for v in batch.get("videos", []) if (v.get("ingest") or {}).get("status") == "PASS"]
    if ns.max_videos:
        videos = videos[:ns.max_videos]
    report = {"schema": "bossman.v1.5-economy/1", "weights_changed": False,
              "trading": "READ_ONLY_LEARNING", "skills": SKILLS, "videos": [],
              "paid": {"allowed": ns.allow_paid, "budget_usd": ns.glm_budget_usd,
                       "spent_usd": 0.0, "glm_calls": 0},
              "aster": {"role": "AUDIT_AND_CONTROL_ONLY", "code_writes": False}}
    lessons = []
    try:
        target = discover(ns.url, ns.data_dir)
        with Client(target, timeout=120) as c:
            agents = agent_map(c)
            missing = [x for x in (*NEMO, LING, GLM) if x not in agents]
            if missing:
                raise RuntimeError("missing agents: " + ", ".join(missing))
            status = c.get("/api/economy/status")
            if not status.get("jev_wired"):
                raise RuntimeError("Jev is not wired")

            for video in videos:
                data = bundle(bp, video)
                vr = {"video_id": video["video_id"], "url": video["url"]}
                fanout = route(c, "fanout", allow_paid=ns.allow_paid, budget=ns.glm_budget_usd,
                               spent=report["paid"]["spent_usd"], glm_calls=report["paid"]["glm_calls"])
                if fanout.get("action") != "nemotron_parallel":
                    vr["status"] = "BLOCKED_JEV"
                    report["videos"].append(vr)
                    break

                ids = {role: submit(c, agents[role], f"K1m6a {video['video_id']} {role}",
                                    nemo_prompt(role, data),
                                    f"v15-{video['video_id']}-{role.lower().replace(' ','-')}")
                       for role in NEMO}
                done = wait(c, list(ids.values()), ns.timeout_minutes * 60)
                workers, meta = {}, {}
                for role, tid in ids.items():
                    u = usage(done[tid])
                    if u["cost_usd"] > 0:
                        raise RuntimeError(f"free Nemotron worker reported paid cost: {u}")
                    workers[role] = result_text(done[tid])
                    meta[role] = {"task_id": tid, "status": done[tid]["task"]["status"], "usage": u}
                vr["nemotron"] = meta

                verify_route = route(c, "verify", allow_paid=ns.allow_paid, budget=ns.glm_budget_usd,
                                     spent=report["paid"]["spent_usd"], glm_calls=report["paid"]["glm_calls"])
                if verify_route.get("action") != "ling_verify":
                    vr["status"] = "BLOCKED_JEV"
                    report["videos"].append(vr)
                    continue

                lid = submit(c, agents[LING], f"K1m6a {video['video_id']} Ling verify",
                             ling_prompt(data, workers), f"v15-ling-{video['video_id']}")
                lr = wait(c, [lid], ns.timeout_minutes * 60)[lid]
                lu = usage(lr)
                if lu["cost_usd"] > 0:
                    raise RuntimeError(f"free Ling worker reported paid cost: {lu}")
                obj = parse_object(result_text(lr))
                verdict = str(obj.get("verdict") or "BLOCKED").upper()
                if verdict not in ("PASS", "FAIL", "BLOCKED"):
                    verdict = "BLOCKED"
                blockers = [str(x)[:500] for x in obj.get("unresolved_blockers") or []]
                vr["ling"] = {"task_id": lid, "verdict": verdict, "blockers": blockers, "usage": lu}
                vr["status"] = "FREE_LANE_PASS" if verdict == "PASS" else "FREE_LANE_INCOMPLETE"
                for item in obj.get("verified_candidates") or []:
                    lessons.append({"source": f"youtube:{video['video_id']}", "status": "UNVERIFIED",
                                    "free_verifier": LING, "candidate": item,
                                    "note": "Free-model verification is evidence, not memory promotion."})
                report["videos"].append(vr)
                (outdir / "economy-report.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            before = verify_repo()
            report["repo_tests_before"] = before
            after = before
            if ns.repo_polish and not before["pass"]:
                rr = route(c, "repair", ling_verdict="FAIL", ling_attempts=1, unresolved=1,
                           allow_paid=ns.allow_paid, budget=ns.glm_budget_usd,
                           spent=report["paid"]["spent_usd"], glm_calls=report["paid"]["glm_calls"])
                report["repo_ling_route"] = rr
                if rr.get("action") == "ling_repair":
                    tid = submit(c, agents[LING], "Bossman 1.5 free repair",
                                 ling_prompt({"scope": "repo", "tests": TESTS}, {}, before["tail"]),
                                 "v15-ling-repo-repair")
                    tr = wait(c, [tid], ns.timeout_minutes * 60)[tid]
                    tu = usage(tr)
                    if tu["cost_usd"] > 0:
                        raise RuntimeError(f"free Ling repair reported paid cost: {tu}")
                    report["repo_ling_task"] = {"task_id": tid, "status": tr["task"]["status"], "usage": tu}
                    after = verify_repo()
            report["repo_tests_after_ling"] = after

            final = after
            if ns.allow_paid and not after["pass"]:
                rr = route(c, "final", ling_verdict="FAIL", ling_attempts=1, unresolved=1,
                           allow_paid=True, budget=ns.glm_budget_usd,
                           spent=report["paid"]["spent_usd"], glm_calls=report["paid"]["glm_calls"])
                report["repo_glm_route"] = rr
                if rr.get("action") == "glm_finalize":
                    tid = submit(c, agents[GLM], "Bossman 1.5 one paid finalizer",
                                 glm_prompt(after["tail"]), "v15-glm-finalizer-once")
                    tr = wait(c, [tid], ns.timeout_minutes * 60)[tid]
                    tu = usage(tr)
                    report["paid"]["spent_usd"] = round(report["paid"]["spent_usd"] + tu["cost_usd"], 8)
                    report["paid"]["glm_calls"] += 1
                    report["repo_glm_task"] = {"task_id": tid, "status": tr["task"]["status"], "usage": tu}
                    if tu["unknown_cost_runs"] or report["paid"]["spent_usd"] > ns.glm_budget_usd:
                        report["paid"]["reconciliation_required"] = True
                    final = verify_repo()
            report["repo_tests_final"] = final
            report["final_route"] = route(c, "audit", allow_paid=ns.allow_paid,
                                          budget=ns.glm_budget_usd,
                                          spent=report["paid"]["spent_usd"],
                                          glm_calls=report["paid"]["glm_calls"])
    except (BossmanError, RuntimeError, ValueError, OSError) as exc:
        report["status"] = "BLOCKED"
        report["error"] = f"{type(exc).__name__}: {exc}"
        (outdir / "economy-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"ok": False, "error": report["error"]}, ensure_ascii=False), file=sys.stderr)
        return 2

    (outdir / "lesson-candidates.jsonl").write_text(
        "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lessons), encoding="utf-8")
    report["lesson_candidates"] = len(lessons)
    report["status"] = "ASTER_AUDIT_REQUIRED" if report.get("repo_tests_final", {}).get("pass") else "BLOCKED"
    report["savings"] = {"bulk": "FREE_NEMOTRON", "verify_repair": "FREE_LING",
                         "paid_finalizer_calls": report["paid"]["glm_calls"],
                         "paid_spent_usd": report["paid"]["spent_usd"],
                         "codex_bulk_work_required": False}
    (outdir / "economy-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (outdir / "ASTER_AUDIT_PACKET.md").write_text(
        "# Bossman 1.5 Aster audit packet\n\n"
        "Aster: audit/control only; do not write code.\n\n"
        f"- Pre-audit status: {report['status']}\n"
        f"- Videos: {len(report['videos'])}\n"
        f"- Lesson candidates still UNVERIFIED: {len(lessons)}\n"
        f"- Targeted tests pass: {bool(report.get('repo_tests_final', {}).get('pass'))}\n"
        f"- Paid GLM calls: {report['paid']['glm_calls']}\n"
        f"- Paid measured cost: USD {report['paid']['spent_usd']:.6f}\n"
        "- Audit provenance, false PASS, unsupported teacher claims, secret egress, approval/budget boundaries "
        "and fresh transfer tests.\n", encoding="utf-8")
    print(json.dumps({"ok": report["status"] == "ASTER_AUDIT_REQUIRED",
                      "status": report["status"], "report": str(outdir / "economy-report.json"),
                      "paid": report["paid"]}, ensure_ascii=False))
    return 0 if report["status"] == "ASTER_AUDIT_REQUIRED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
