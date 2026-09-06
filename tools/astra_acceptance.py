"""Run isolated bounded acceptance tests and reject skips, missing tests or hangs."""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import platform
import time
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

PROFILES={
    "runner": [
        "command-center/tests/test_discovery.py::test_open_port_that_stays_silent_is_not_called_absent",
        "command-center/tests/test_v21_failure_injection.py::test_provider_failure_retries_are_bounded_and_status_is_honest"],
    "sandbox": ["bossman-core/tests/test_sandbox_safe_runtime.py"],
    "v5-independent-pure": ["tests/test_v5_independent_verification.py"],
    "v5-independent-durable": ["bossman-core/tests/test_v5_independent_durability.py"],
}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--profile", choices=PROFILES, default="runner")
    parser.add_argument("--output", default="astra-acceptance-results")
    parser.add_argument("--tested-sha")
    parser.add_argument("--run-id")
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--source-manifest", type=Path)
    args=parser.parse_args()
    if args.profile.startswith("v5-independent-"):
        return run_independent(args)
    out=Path(args.output).resolve(); out.mkdir(parents=True,exist_ok=True)
    env=dict(os.environ);env.pop("BCC_CI_SKIP_RUNNER_HANGS",None)
    if args.profile == "sandbox": env["BOSSMAN_RUN_REAL_SANDBOX"]="1"
    results=[]
    for i, selector in enumerate(PROFILES[args.profile]):
        xml=out/f"test-{i}.xml";xml.unlink(missing_ok=True)
        cmd=[sys.executable,"-m","pytest",selector,"-q","-o","asyncio_mode=auto",
             "--timeout=120","--timeout-method=thread",f"--junitxml={xml}"]
        record={"selector":selector,"command":cmd}
        try:
            with (out/f"test-{i}.log").open("w",encoding="utf-8") as log:
                run=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,env=env,timeout=180,check=False)
            cases=ET.parse(xml).findall(".//testcase")
            skipped=sum(c.find("skipped") is not None for c in cases)
            record.update(exit_code=run.returncode,tests=len(cases),skipped=skipped,
                          status="PASS" if run.returncode == 0 and cases and skipped == 0 else "FAIL")
        except (OSError,ET.ParseError,subprocess.TimeoutExpired) as exc:
            record.update(status="FAIL",error=f"{type(exc).__name__}: {exc}")
        results.append(record)
    (out/"summary.json").write_text(json.dumps(results,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(results,indent=2))
    return 0 if all(r["status"] == "PASS" for r in results) else 1


# The independent profiles extend this collector. They never start a model,
# desktop, service, observer tick, payment, or skill activation. Existing
# profiles and their per-test timeouts remain unchanged.
SOURCE_MODULES = {
    "bossman_shared.objective_canary": "bossman_shared/objective_canary.py",
    "bossman_shared.objective_fairness": "bossman_shared/objective_fairness.py",
    "bossman_shared.objective_improvement": "bossman_shared/objective_improvement.py",
    "bossman_shared.objective_promotion": "bossman_shared/objective_promotion.py",
    "bossman.learning_guard.evidence_ledger": "bossman-core/bossman/learning_guard/evidence_ledger.py",
}
PROFILE_MODULES = {
    "v5-independent-pure": tuple(SOURCE_MODULES)[:4],
    "v5-independent-durable": ("bossman_shared.objective_improvement",
        "bossman_shared.objective_promotion", "bossman.learning_guard.evidence_ledger"),
}


def _utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _git_blob(path):
    data = Path(path).read_bytes()
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def _git(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], text=True,
                            capture_output=True, timeout=10, check=False)
    if result.returncode:
        raise ValueError("Git provenance unavailable: " + result.stderr.strip()[:500])
    return result.stdout.strip()


def _source_binding(args, root):
    if args.source_manifest is not None:
        manifest = json.loads(args.source_manifest.read_text(encoding="utf-8"))
        if manifest.get("source_mode") != "verified_source_subset":
            raise ValueError("Only explicitly labelled verified_source_subset manifests are supported")
        tested_sha = manifest["tested_sha"]
        if args.tested_sha != tested_sha:
            raise ValueError("requested SHA does not match source manifest")
        expected = manifest["git_blob_shas"]
        mode = "verified_source_subset"
    else:
        tested_sha = _git(root, "rev-parse", "HEAD")
        if args.tested_sha != tested_sha:
            raise ValueError("requested SHA does not match checkout HEAD")
        expected = {relative: _git(root, "rev-parse", f"HEAD:{relative}")
                    for relative in SOURCE_MODULES.values()}
        mode = "git_checkout"
    if len(tested_sha) != 40 or any(c not in "0123456789abcdef" for c in tested_sha):
        raise ValueError("a full lower-case Git SHA is required")
    rows = {}
    for module, relative in SOURCE_MODULES.items():
        path = root / relative
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError("missing or redirected source: " + relative)
        actual = _git_blob(path)
        if actual != expected.get(relative):
            raise ValueError("source blob mismatch: " + relative)
        rows[module] = {"path": str(path.resolve()), "git_blob_sha": actual}
    return {"tested_sha": tested_sha, "source_mode": mode, "modules": rows}


def pytest_sessionfinish(session, exitstatus):
    """Explicit pytest plugin: record imports in the TEST process, not a probe.

    A prior importlib probe cannot prove what conftest/path shadowing loaded.
    Activated only by the collector's explicit -p and metadata environment.
    """
    target = os.environ.get("BOSSMAN_INDEPENDENT_IMPORT_REPORT")
    binding_path = os.environ.get("BOSSMAN_INDEPENDENT_BINDING")
    if not target or not binding_path:
        return
    binding = json.loads(Path(binding_path).read_text(encoding="utf-8"))
    required = json.loads(os.environ["BOSSMAN_INDEPENDENT_REQUIRED_MODULES"])
    rows = {}
    for name in required:
        module = sys.modules.get(name)
        origin = getattr(module, "__file__", None)
        actual = None
        if origin is not None and Path(origin).is_file():
            actual = _git_blob(origin)
        wanted = binding["modules"][name]
        rows[name] = {"origin": origin, "git_blob_sha": actual,
            "expected_origin": wanted["path"], "expected_git_blob_sha": wanted["git_blob_sha"],
            "matches": (origin is not None and str(Path(origin).resolve()) == wanted["path"]
                        and actual == wanted["git_blob_sha"])}
    matched = bool(rows) and all(row["matches"] for row in rows.values())
    payload = {"pid": os.getpid(), "python": sys.executable,
               "pytest_origin": getattr(sys.modules.get("pytest"), "__file__", None),
               "tested_sha": binding["tested_sha"], "source_mode": binding["source_mode"],
               "modules": rows, "imports_match": matched}
    Path(target).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if not matched:
        session.exitstatus = 1


def run_independent(args):
    repo = Path(__file__).resolve().parents[1]
    source = (args.source_root or repo).resolve()
    out = Path(args.output).resolve()
    # Reusing --output could overwrite the only record of a failed attempt.
    if out.exists():
        raise ValueError("independent run output must be a NEW directory")
    out.mkdir(parents=True)
    run_id = args.run_id or dt.datetime.now(dt.timezone.utc).strftime("astra-v5-%Y%m%dT%H%M%S%fZ")
    manifest = {"run_id": run_id, "requested_sha": args.tested_sha, "tested_sha": None,
        "started_at": _utc(), "profile": args.profile, "release_certification": False,
        "live_pc_touched": False, "standing_autonomy_enabled_by_run": False,
        "environment": {"python": sys.version, "executable": sys.executable,
            "platform": platform.platform(), "machine": platform.machine(),
            "source_root": str(source), "test_root": str(repo)}, "artifacts": [],
        "status": "FAIL", "errors": []}
    results = []
    try:
        binding = _source_binding(args, source)
        manifest.update(tested_sha=binding["tested_sha"], source_mode=binding["source_mode"])
        manifest["test_workspace_git_head"] = _git(repo, "rev-parse", "HEAD")
        manifest["test_workspace_dirty"] = bool(_git(repo, "status", "--porcelain"))
        manifest["harness_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        binding_file = out / "source-binding.json"
        binding_file.write_text(json.dumps(binding, indent=2) + "\n", encoding="utf-8")
        env = dict(os.environ)
        env.pop("BCC_CI_SKIP_RUNNER_HANGS", None)
        env["PYTHONPATH"] = os.pathsep.join([str(repo), str(source), str(source / "bossman-core")])
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        env["BOSSMAN_INDEPENDENT_BINDING"] = str(binding_file)
        env["BOSSMAN_INDEPENDENT_REQUIRED_MODULES"] = json.dumps(PROFILE_MODULES[args.profile])
        state = out / "state"
        state.mkdir()
        env["BOSSMAN_STATE_DIR"] = str(state)
        env["BOSSMAN_V5_STORE"] = str(state / "objectives.sqlite3")
        env["BOSSMAN_EVIDENCE_LEDGER_PATH"] = str(state / "evidence-ledger.json")
        manifest["environment"]["pythonpath"] = env["PYTHONPATH"]
        manifest["environment"]["state_dir"] = str(state)
        for i, selector in enumerate(PROFILES[args.profile]):
            xml, log, imports = out / f"test-{i}.xml", out / f"test-{i}.log", out / f"imports-{i}.json"
            env["BOSSMAN_INDEPENDENT_IMPORT_REPORT"] = str(imports)
            command = [sys.executable, "-m", "pytest", selector, "-q", "--tb=short",
                "-p", "tools.astra_acceptance", "-p", "no:cacheprovider", "-o", "junit_family=legacy",
                f"--basetemp={state / ('pytest-' + str(i))}", f"--junitxml={xml}"]
            record = {"run_id": run_id, "tested_sha": binding["tested_sha"], "selector": selector,
                "command": command, "cwd": str(repo), "started_at": _utc(), "exit_code": None,
                "pass": None, "fail": None, "skip": None, "errors": None,
                "evidence_tier": "REAL_COMPONENT" if args.profile.endswith("durable") else "MOCK",
                "paths": {"junit": str(xml), "log": str(log), "imports": str(imports)}, "status": "FAIL"}
            test_file = repo / selector.split("::", 1)[0]
            record["test_file_sha256"] = hashlib.sha256(test_file.read_bytes()).hexdigest()
            start = time.monotonic()
            try:
                # Whole-suite bound 120 s: stricter than the existing 180 s job
                # bound, and no dependency on an unavailable timeout plugin.
                with log.open("w", encoding="utf-8") as handle:
                    completed = subprocess.run(command, cwd=repo, env=env, stdout=handle,
                        stderr=subprocess.STDOUT, timeout=120, check=False)
                record["exit_code"] = completed.returncode
                cases = ET.parse(xml).findall(".//testcase")
                skips = sum(c.find("skipped") is not None for c in cases)
                fails = sum(c.find("failure") is not None for c in cases)
                errors = sum(c.find("error") is not None for c in cases)
                record.update(tests=len(cases), skip=skips, fail=fails, errors=errors,
                              **{"pass": len(cases) - skips - fails - errors})
                recorded_imports = json.loads(imports.read_text(encoding="utf-8"))
                record["import_origins"] = recorded_imports["modules"]
                if completed.returncode == 0 and cases and skips == 0 and recorded_imports["imports_match"]:
                    record["status"] = "PASS"
            except subprocess.TimeoutExpired as exc:
                record.update(error=f"{type(exc).__name__}: whole-suite timeout", timed_out=True)
            except (OSError, ET.ParseError, ValueError, KeyError) as exc:
                record["error"] = f"{type(exc).__name__}: {exc}"
            record["test_file_sha256_after"] = hashlib.sha256(test_file.read_bytes()).hexdigest()
            if record["test_file_sha256_after"] != record["test_file_sha256"]:
                record["status"] = "FAIL"
                record["error"] = "test source changed during the run"
            record.update(ended_at=_utc(), duration_seconds=time.monotonic() - start)
            results.append(record)
            with (out / "commands.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
        # Detect concurrent edits; never mix a newly changed source into PASS.
        if _source_binding(args, source) != binding:
            raise ValueError("source changed during the run")
        manifest["status"] = "PASS" if results and all(r["status"] == "PASS" for r in results) else "FAIL"
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        manifest["errors"].append(f"{type(exc).__name__}: {exc}")
    manifest["ended_at"] = _utc()
    manifest["results"] = results
    for path in sorted(out.rglob("*")):
        if path.is_file() and not path.is_symlink():
            manifest["artifacts"].append({"path": str(path.relative_to(out)),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size})
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"run_id": run_id, "tested_sha": manifest["tested_sha"],
                      "status": manifest["status"], "errors": manifest["errors"],
                      "results": [{k: r.get(k) for k in ("selector", "pass", "fail", "skip", "exit_code")}
                                  for r in results]}, indent=2))
    return 0 if manifest["status"] == "PASS" else 1


if __name__ == "__main__": raise SystemExit(main())
