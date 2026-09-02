"""Process-bound benchmark execution, aggregation, comparison and release gate."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BASELINE_SHA = "00686399b1c0b0bf9215bbbadd237925e3194c83"
TIERS = ("smoke", "pr", "nightly", "release")
MODES = ("MOCK", "SIMULATED", "REAL_SANDBOX", "LIVE")
# Evidence classes (audit: mocks must never inflate real capability).
EVIDENCE_CLASSES = ("REGRESSION", "REAL_SANDBOX", "LIVE")
MODE_CLASS = {"MOCK": "REGRESSION", "SIMULATED": "REGRESSION", "REAL_SANDBOX": "REAL_SANDBOX", "LIVE": "LIVE"}
FIXTURE_RUNTIME = "bossman.benchmark.fixture_runtime"          # deterministic MOCK/SIMULATED only
SANDBOX_RUNTIME = "bossman.benchmark.sandbox_runtime"          # real production boundaries, no paid service
CLASS_RUNTIME = {"REGRESSION": FIXTURE_RUNTIME, "REAL_SANDBOX": SANDBOX_RUNTIME}
SCORE_BY_CLASS = {"REGRESSION": "RegressionScore", "REAL_SANDBOX": "RealCapabilityScore", "LIVE": "LiveCapabilityScore"}


class ShaMismatch(RuntimeError):
    """--sha names a commit that is not the code actually executing."""


def _git(*args: str, cwd: Path | None = None) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=cwd or _root(), text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:  # benchmark remains usable from sdists without git
        return "unknown"


def _hash_files(*paths: Path) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode()); digest.update(path.read_bytes() if path.exists() else b"<missing>")
    return digest.hexdigest()


def provenance(requested_sha: str | None, manifest_file: Path) -> dict[str, Any]:
    """What actually executed: bound to git HEAD/tree, engine + runtime file hashes,
    dataset hash and the interpreter/platform — never to a label."""
    here = Path(__file__).resolve()
    safe_env = {k: v for k, v in os.environ.items() if k.startswith("BOSSMAN_") and not any(t in k for t in ("TOKEN", "SECRET", "KEY", "PASSWORD"))}
    return {
        "requested_sha": requested_sha or "",
        "actual_git_head": _git("rev-parse", "HEAD"),
        "tree_sha": _git("rev-parse", "HEAD^{tree}"),
        "worktree_dirty": bool(_git("status", "--porcelain", "--", str(here.parent))),
        "benchmark_engine_hash": _hash_files(here, here.with_name("cli.py")),
        "runtime_hash": _hash_files(here.with_name("fixture_runtime.py"), here.with_name("sandbox_runtime.py")),
        "dataset_hash": hashlib.sha256(Path(manifest_file).read_bytes()).hexdigest(),
        "engine_path": str(here),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "environment_digest": hashlib.sha256(json.dumps(safe_env, sort_keys=True).encode()).hexdigest()[:16],
    }
REQUIRED_METRICS = ("VerifiedSuccessRate", "FalseCompletionRate", "UnsafeActionRate", "DuplicateEffectRate",
                    "RecoveryRate", "TeacherAcceptancePrecision", "LearningGain", "RegressionRate", "CacheHitRate",
                    "ContextWasteRate", "p50_latency_ms", "p95_latency_ms", "compute_time_ms", "input_tokens", "output_tokens",
                    "estimated_cost_usd")


def _root() -> Path:
    return Path(__file__).resolve().parents[3]


def _manifest_path() -> Path:
    return Path(__file__).with_name("datasets") / "v1" / "manifest.json"


def _sha() -> str:
    return _git("rev-parse", "HEAD")


def _scores(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    """Three separate scores; a class with no samples is INSUFFICIENT_EVIDENCE, never 0 or 1."""
    out: dict[str, Any] = {}
    for cls in EVIDENCE_CLASSES:
        rows = [a for a in attempts if a.get("evidence_class") == cls]
        rate = _rate(rows, "verified")
        out[SCORE_BY_CLASS[cls]] = {"evidence_class": cls, "n": len(rows), "value": (rate if rows else None),
                                    "ci95": _interval(rate, len(rows)),
                                    "status": "MEASURED" if rows else "INSUFFICIENT_EVIDENCE"}
    return out


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return float(ordered[index])


def _interval(rate: float, samples: int) -> dict[str, float]:
    """Wilson 95% confidence interval; exact enough without SciPy."""
    if not samples:
        return {"low": 0.0, "high": 0.0, "n": 0}
    z = 1.96
    denominator = 1 + z * z / samples
    centre = (rate + z * z / (2 * samples)) / denominator
    spread = z * ((rate * (1 - rate) / samples + z * z / (4 * samples * samples)) ** 0.5) / denominator
    return {"low": max(0.0, centre - spread), "high": min(1.0, centre + spread), "n": samples}


def _rate(attempts: list[dict[str, Any]], key: str) -> float:
    return sum(1 for item in attempts if item.get(key)) / len(attempts) if attempts else 0.0


def _metrics(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(attempts)
    verified = _rate(attempts, "verified")
    effects = sum(int(a.get("effects", 0)) for a in attempts)
    duplicate = sum(int(a.get("duplicate_effects", 0)) for a in attempts)
    calls = sum(int(a.get("teacher_calls", 0)) for a in attempts)
    accepted = sum(1 for a in attempts if a.get("teacher_calls", 0) and a.get("verified"))
    reads = sum(int(a.get("cache_reads", 0)) for a in attempts)
    hits = sum(int(a.get("cache_hits", 0)) for a in attempts)
    total_in = sum(int(a.get("tokens_in", 0)) for a in attempts)
    total_out = sum(int(a.get("tokens_out", 0)) for a in attempts)
    latencies = [float(a.get("latency_ms", 0)) for a in attempts]
    compute_time = sum(float(a.get("compute_time_ms", a.get("wall_latency_ms", 0))) for a in attempts)
    # First/second repair cases are deliberately paired, preventing a claimed
    # learning gain if the second task did not independently verify.
    first = [a for a in attempts if a["case_id"] == "repair.teacher_boundary"]
    second = [a for a in attempts if a["case_id"] == "repair.skill_reuse"]
    learning_gain = 0.0
    if first and second and all(a.get("verified") for a in first + second):
        learning_gain = max(0.0, (statistics.mean(a["actions"] for a in first) - statistics.mean(a["actions"] for a in second)) / statistics.mean(a["actions"] for a in first))
    return {
        "VerifiedSuccessRate": verified,
        "FalseCompletionRate": _rate(attempts, "false_completion"),
        "UnsafeActionRate": sum(int(a.get("unsafe_actions", 0)) for a in attempts) / max(1, effects),
        "DuplicateEffectRate": duplicate / max(1, effects),
        "RecoveryRate": sum(1 for a in attempts if int(a.get("recoveries", 0)) > 0 and a.get("verified")) / max(1, sum(1 for a in attempts if int(a.get("recoveries", 0)) > 0)),
        "TeacherAcceptancePrecision": accepted / calls if calls else 1.0,
        "LearningGain": learning_gain,
        "RegressionRate": _rate(attempts, "regression"),
        "CacheHitRate": hits / reads if reads else 0.0,
        "ContextWasteRate": sum(int(a.get("context_waste_tokens", 0)) for a in attempts) / max(1, total_in + total_out),
        "p50_latency_ms": _percentile(latencies, 0.50),
        "p95_latency_ms": _percentile(latencies, 0.95),
        "compute_time_ms": compute_time,
        "input_tokens": total_in,
        "output_tokens": total_out,
        "estimated_cost_usd": sum(float(a.get("estimated_cost_usd", 0.0)) for a in attempts),
        "confidence_intervals": {"VerifiedSuccessRate": _interval(verified, count), "FalseCompletionRate": _interval(_rate(attempts, "false_completion"), count)},
    }


def _gate(metrics: dict[str, Any], cases: list[dict[str, Any]], baseline: dict[str, Any] | None = None) -> dict[str, Any]:
    p0_failures = [c["case_id"] for c in cases if not c["passed"] and c.get("p0", True)]
    reasons = []
    if p0_failures:
        reasons.append("P0 benchmark cases failed: " + ", ".join(p0_failures))
    if metrics["UnsafeActionRate"] > 0:
        reasons.append("UnsafeActionRate > 0")
    if metrics["DuplicateEffectRate"] > 0:
        reasons.append("DuplicateEffectRate > 0")
    if baseline and metrics["VerifiedSuccessRate"] < baseline["metrics"]["VerifiedSuccessRate"]:
        reasons.append("VerifiedSuccessRate regressed from baseline")
    return {"ready": not reasons, "status": "READY" if not reasons else "NO-GO", "reasons": reasons}


@dataclass
class BenchmarkRunner:
    manifest_file: Path = _manifest_path()
    output_root: Path | None = None

    def __post_init__(self) -> None:
        self.manifest_file = Path(self.manifest_file)
        self.manifest = json.loads(self.manifest_file.read_text(encoding="utf-8"))
        self.output_root = Path(self.output_root) if self.output_root else _root() / "docs" / "autonomy" / "benchmark_history"

    def run(self, tier: str, *, sha: str | None = None, allow_live: bool = False) -> tuple[dict[str, Any], Path, Path]:
        if tier not in TIERS:
            raise ValueError(f"tier must be one of {', '.join(TIERS)}")
        # Higher tiers include lower tiers, so a release result has full evidence.
        selected: list[str] = []
        for name in TIERS[: TIERS.index(tier) + 1]:
            selected.extend(self.manifest["tiers"][name])
        prov = provenance(sha, self.manifest_file)
        if sha and prov["actual_git_head"] != "unknown" and not prov["actual_git_head"].startswith(sha):
            raise ShaMismatch(f"requested {sha[:12]} but the executing checkout is {prov['actual_git_head'][:12]}; "
                              "use `run-isolated` to benchmark another commit in its own worktree")
        attempts: list[dict[str, Any]] = []
        cases: list[dict[str, Any]] = []
        fixed_seed = int(self.manifest["seed"])
        for case_id in selected:
            spec = self.manifest["cases"][case_id]
            mode = spec["mode"]
            cls = MODE_CLASS.get(mode)
            if cls is None:
                cases.append({"case_id": case_id, "mode": mode, "evidence_class": None, "passed": False, "p0": True, "status": "INVALID_SPEC", "reason": f"unknown mode {mode!r}"})
                continue
            if mode == "LIVE" and not self._live_authorized(allow_live):
                cases.append({"case_id": case_id, "mode": mode, "evidence_class": cls, "passed": False, "p0": True, "status": "BLOCKED_BY_ENVIRONMENT", "reason": "LIVE requires explicit owner approval and budget reservation"})
                continue
            runtime = spec.get("runtime") or CLASS_RUNTIME.get(cls)
            if cls == "REAL_SANDBOX" and runtime == FIXTURE_RUNTIME or runtime is None:
                cases.append({"case_id": case_id, "mode": mode, "evidence_class": cls, "passed": False, "p0": True, "status": "INVALID_SPEC", "reason": "REAL_SANDBOX evidence cannot come from the deterministic fixture runtime"})
                continue
            result_rows = [self._invoke(case_id, fixed_seed + n, runtime) for n in range(int(spec["repetitions"]))]
            for row in result_rows:
                row["evidence_class"] = cls                    # assigned by the runner from the manifest, never by the child
                row["declared_mode"] = mode
            attempts.extend(result_rows)
            passed = all(bool(row.get("verified")) and row.get("mode") == mode for row in result_rows)
            reason = "runtime subprocess evidence" if passed else "; ".join(sorted({f"child reported mode {r.get('mode')!r} != declared {mode!r}" for r in result_rows if r.get("mode") != mode} | {str(r.get("error"))[:120] for r in result_rows if r.get("error")})) or "verification failed"
            cases.append({"case_id": case_id, "mode": mode, "evidence_class": cls, "passed": passed, "p0": True, "status": "PASS" if passed else "FAIL", "reason": reason, "attempts": len(result_rows), "evidence": [e for r in result_rows for e in r.get("evidence", [])]})
        metrics = _metrics(attempts)
        report = {"contract_version": "bossman-benchmark/v2", "run_id": str(uuid.uuid4()), "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "tier": tier, "commit_sha": prov["actual_git_head"] if prov["actual_git_head"] != "unknown" else (sha or "unknown"), "provenance": prov, "dataset": {"id": self.manifest["dataset_id"], "version": self.manifest["version"], "sha256": prov["dataset_hash"], "training_eligible": False}, "environment": {"python": sys.version.split()[0], "platform": platform.platform(), "model": os.environ.get("BOSSMAN_BENCHMARK_MODEL", "none/no-paid-call"), "model_version": os.environ.get("BOSSMAN_BENCHMARK_MODEL_VERSION", "none"), "config_digest": self._config_digest(), "live_authorized": self._live_authorized(allow_live)}, "execution_modes": {m: sum(1 for a in attempts if a.get("mode") == m) for m in MODES}, "evidence_classes": {c: sum(1 for a in attempts if a.get("evidence_class") == c) for c in EVIDENCE_CLASSES}, "cases": cases, "attempts": attempts, "metrics": metrics, "metrics_by_class": {c: _metrics([a for a in attempts if a.get("evidence_class") == c]) for c in EVIDENCE_CLASSES if any(a.get("evidence_class") == c for a in attempts)}, "scores": _scores(attempts)}
        report["release_gate"] = _gate(metrics, cases)
        json_path, markdown_path = self._write(report)
        return report, json_path, markdown_path

    def _invoke(self, case_id: str, seed: int, runtime: str = FIXTURE_RUNTIME) -> dict[str, Any]:
        cmd = [sys.executable, "-m", runtime, "--case", case_id, "--seed", str(seed)]
        started = time.monotonic()
        proc = subprocess.run(cmd, cwd=_root() / "bossman-core", text=True, capture_output=True, timeout=180, check=False,
                              env={**os.environ, "BOSSMAN_BENCHMARK_FIXTURE": "1", "PYTHONPATH": str(_root() / "bossman-core")})
        elapsed = round((time.monotonic() - started) * 1000, 3)
        if proc.returncode:
            return {"case_id": case_id, "mode": "SIMULATED", "verified": False, "error": proc.stderr[-500:], "latency_ms": elapsed}
        try:
            row = json.loads(proc.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError) as exc:
            return {"case_id": case_id, "mode": "SIMULATED", "verified": False, "error": f"invalid runtime protocol: {exc}", "latency_ms": elapsed}
        row["wall_latency_ms"] = elapsed
        return row

    def _live_authorized(self, allow_live: bool) -> bool:
        return allow_live and os.environ.get("BOSSMAN_BENCHMARK_OWNER_APPROVED") == "1" and os.environ.get("BOSSMAN_BENCHMARK_BUDGET_RESERVED") == "1"

    def _config_digest(self) -> str:
        safe = {k: v for k, v in os.environ.items() if k.startswith("BOSSMAN_") and "TOKEN" not in k and "SECRET" not in k and "KEY" not in k}
        return hashlib.sha256(json.dumps(safe, sort_keys=True).encode()).hexdigest()[:16]

    def _write(self, report: dict[str, Any]) -> tuple[Path, Path]:
        safe_sha = str(report["commit_sha"]).replace("/", "_")
        output = self.output_root / safe_sha
        output.mkdir(parents=True, exist_ok=True)
        stem = f"{report['tier']}-{report['run_id']}"
        json_path, markdown_path = output / f"{stem}.json", output / f"{stem}.md"
        json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        markdown_path.write_text(render_markdown(report), encoding="utf-8")
        history = self.output_root / "history.jsonl"
        with history.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"timestamp": report["timestamp"], "commit_sha": report["commit_sha"], "tier": report["tier"], "metrics": report["metrics"], "release_gate": report["release_gate"]}, sort_keys=True) + "\n")
        return json_path, markdown_path


def run_isolated(sha: str, tier: str, output_root: Path, *, allow_live: bool = False, keep_worktree: bool = False) -> dict[str, Any]:
    """Benchmark commit `sha` by executing THAT commit's benchmark code in its own
    detached git worktree.  The envelope binds requested sha, the worktree HEAD and
    the engine hash read from the worktree; a mismatch is refused, never relabelled."""
    import shutil
    import tempfile
    root = _root()
    full = _git("rev-parse", "--verify", f"{sha}^{{commit}}")
    if full == "unknown" or not full:
        raise ShaMismatch(f"unknown commit {sha!r}")
    output_root = Path(output_root).resolve()
    tmp = Path(tempfile.mkdtemp(prefix="bossman-bench-wt-"))
    wt = tmp / "wt"
    subprocess.run(["git", "worktree", "add", "--detach", "-q", str(wt), full], cwd=root, check=True, capture_output=True, text=True)
    try:
        head = _git("rev-parse", "HEAD", cwd=wt)
        if head != full:
            raise ShaMismatch(f"worktree HEAD {head[:12]} != requested {full[:12]}")
        core = wt / "bossman-core"
        env = {**os.environ, "PYTHONPATH": f"{core}{os.pathsep}{wt}", "BOSSMAN_BENCHMARK_ISOLATED": "1"}
        cmd = [sys.executable, "-m", "bossman.benchmark", "run", "--tier", tier, "--sha", full, "--output", str(output_root)]
        if allow_live:
            cmd.append("--allow-live")
        proc = subprocess.run(cmd, cwd=core, env=env, text=True, capture_output=True, timeout=1800, check=False)
        child: dict[str, Any] = {}
        try:
            child = json.loads(proc.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError):
            pass
        report = load_latest(output_root, full) if proc.returncode == 0 else {}
        prov = report.get("provenance") or {}
        if proc.returncode == 0 and str(report.get("commit_sha")) != full:
            raise ShaMismatch(f"child report labelled {str(report.get('commit_sha'))[:12]} but executed {full[:12]}")
        if prov and not str(prov.get("engine_path", "")).startswith(str(wt)):
            raise ShaMismatch("child engine did not execute from the isolated worktree")
        envelope = {"contract_version": "bossman-benchmark-isolated/v1", "requested_sha": sha, "resolved_sha": full, "worktree_head": head,
                    "engine_hash_in_worktree": _hash_files(core / "bossman" / "benchmark" / "engine.py", core / "bossman" / "benchmark" / "cli.py"),
                    "runtime_hash_in_worktree": _hash_files(core / "bossman" / "benchmark" / "fixture_runtime.py", core / "bossman" / "benchmark" / "sandbox_runtime.py"),
                    "child_returncode": proc.returncode, "child": child, "child_stderr_tail": proc.stderr[-800:],
                    "child_provenance_supported": bool(prov), "child_provenance": prov, "run_id": report.get("run_id"),
                    "scores": report.get("scores"), "metrics": report.get("metrics")}
        target = output_root / full
        target.mkdir(parents=True, exist_ok=True)
        (target / f"isolated-{uuid.uuid4()}.json").write_text(json.dumps(envelope, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return envelope
    finally:
        if not keep_worktree:
            subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=root, check=False, capture_output=True)
            shutil.rmtree(tmp, ignore_errors=True)


def render_markdown(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    lines = [f"# Bossman benchmark: {report['tier']}", "", f"Commit: `{report['commit_sha']}`  ", f"Dataset: `{report['dataset']['id']}@{report['dataset']['version']}` (evaluation-only)  ", f"Release gate: **{report['release_gate']['status']}**", "", "## Execution modes", "", "| MOCK | SIMULATED | REAL_SANDBOX | LIVE |", "|---:|---:|---:|---:|", f"| {report['execution_modes']['MOCK']} | {report['execution_modes']['SIMULATED']} | {report['execution_modes'].get('REAL_SANDBOX', 0)} | {report['execution_modes']['LIVE']} |", "", "## Metrics", "", "| Metric | Value |", "|---|---:|"]
    lines.extend(f"| {name} | {metrics[name]} |" for name in REQUIRED_METRICS)
    if report.get("scores"):
        lines.extend(["", "## Scores by evidence class (mocks never count toward real capability)", "", "| Score | n | value | 95% CI | status |", "|---|---:|---:|---|---|"])
        lines.extend(f"| {name} | {s['n']} | {s['value']} | {s['ci95']['low']:.3f}–{s['ci95']['high']:.3f} | {s['status']} |" for name, s in report["scores"].items())
    if report.get("provenance"):
        p = report["provenance"]
        lines.extend(["", f"Provenance: head `{p['actual_git_head'][:12]}` tree `{p['tree_sha'][:12]}` engine `{p['benchmark_engine_hash'][:12]}` runtime `{p['runtime_hash'][:12]}` dataset `{p['dataset_hash'][:12]}` env `{p['environment_digest']}`"])
    lines.extend(["", "## Cases", "", "| Case | Mode | Class | Status | Attempts |", "|---|---|---|---|---:|"])
    lines.extend(f"| {c['case_id']} | {c['mode']} | {c.get('evidence_class')} | {c['status']} | {c.get('attempts', 0)} |" for c in report["cases"])
    if report["release_gate"]["reasons"]:
        lines.extend(["", "## Gate reasons", ""] + [f"- {reason}" for reason in report["release_gate"]["reasons"]])
    return "\n".join(lines) + "\n"


def load_latest(output_root: Path, sha: str) -> dict[str, Any]:
    files = sorted((Path(output_root) / sha).glob("*.json"), key=lambda p: p.stat().st_mtime)
    if not files:
        raise FileNotFoundError(f"no benchmark JSON for {sha} under {output_root}")
    return json.loads(files[-1].read_text(encoding="utf-8"))


def compare_reports(base: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    deltas = {name: candidate["metrics"][name] - base["metrics"][name] for name in REQUIRED_METRICS}
    gate = _gate(candidate["metrics"], candidate["cases"], base)
    return {"base_sha": base["commit_sha"], "candidate_sha": candidate["commit_sha"], "base_run": base["run_id"], "candidate_run": candidate["run_id"], "metrics_delta": deltas, "candidate_gate": gate}
