#!/usr/bin/env python3
"""Owner evening acceptance for a downloaded Bossman bundle.

Shipped inside the archive as ``app-support/bundle_evening_test.py`` and run by
``Evening-Test.cmd``. The owner sees one screen: which build this is, what the
machine is, what the doctor found, what the installed product actually did, and
one verdict.

Why this is not ``scripts/evening_owner_run.py``: that wrapper binds evidence to
a Git checkout — canonical branch, clean worktree, local HEAD equal to the live
remote. A downloaded archive has no checkout, and pretending otherwise would
mean inventing a branch. The binding here is the one the archive really has:
``MANIFEST.json`` names the exact source SHA the wheels were built from and the
hash of every shipped file, and the installed product must report that same
SHA back over HTTP.

Verdicts and exit codes (``EXIT_CODES``):

* ``PASS`` (0) — the doctor ran, was readable and complete, and the installed
  product booted and answered for this exact SHA;
* ``OWNER_REQUIRED`` (2) — everything the archive can prove passed, but a
  condition only the owner's machine or account can change stands in the way
  (a busy port, an unwritable data folder, a component the first run fetches);
* ``PARTIAL`` (3) — the run was started with ``--skip-doctor``. It is a
  diagnostic, never a full acceptance, and no aggregator accepts it as one;
* ``FAIL`` (1) — this build does not do what it claims, OR the check itself
  could not be trusted: a doctor that is not shipped, that crashed, that timed
  out, that printed something other than its own report, a verifier that left
  no result, a result written for another SHA.

``--full`` (or ``--ui-sweep`` / ``--live`` separately) adds the two owner
runners CI drives from its checkout, now shipped in ``app-support`` (OA-04):
the visible-button sweep of the installed application and the free-model
smoke through the installed UI. The live smoke needs
``BOSSMAN_OPENROUTER_API_KEY`` in the environment; without it the stage is
OWNER_REQUIRED, never PASS. Stage results land in the same run folder.

The last clause is the point of the 17 September audit (OA-01): before it, a
missing or unreadable doctor left ``blocking`` empty and the verdict said PASS.
A verdict is only ever computed from the structured ``reasons`` list, every
reason carries its class (``fail`` / ``owner`` / ``partial``), and each run
writes into a fresh ``<run_id>`` folder, so no result of an earlier run can
satisfy this one.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SUPPORT = Path(__file__).resolve().parent
HOME = SUPPORT.parent

RESULT_SCHEMA_VERSION = 2
EXIT_CODES = {"PASS": 0, "FAIL": 1, "OWNER_REQUIRED": 2, "PARTIAL": 3}
DOCTOR_TIMEOUT = 300
VERIFIER_TIMEOUT = 900
SHA = re.compile(r"[0-9a-f]{40}\Z")

# The doctor's own check names (scripts/bossman_doctor.py, shipped as
# app-support/bossman_doctor.py). A report that lacks any of them is not a
# diagnostic of this archive, whatever else it says.
REQUIRED_DOCTOR_CHECKS = frozenset({
    "build-identity", "python", "python-packages", "bossman-packages", "ffmpeg",
    "state-dir", "evidence-key", "journal-anchor", "browser", "computer-operator",
    "telemetry", "port",
})
# BLOCKED here describes the owner's machine (a busy port, an unwritable data
# folder), not the archive: the owner acts, the verdict is OWNER_REQUIRED.
# BLOCKED anywhere else means the archive does not ship what it claims: FAIL.
OWNER_MACHINE_CHECKS = frozenset({"port", "state-dir"})
DOCTOR_STATUSES = frozenset({"PASS", "WARN", "BLOCKED"})
# The files whose word this verdict is. They must be the ones MANIFEST.json lists.
HARNESS_FILES = ("bundle_evening_test.py", "bossman_doctor.py", "verify_installed_product.py")


def _console_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _evidence_root(sha: str) -> Path:
    """Outside the bundle when possible: replacing the folder keeps history."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME")
    root = (Path(base) / "Bossman" / "evening") if base else (HOME / "evidence")
    target = root / sha
    target.mkdir(parents=True, exist_ok=True)
    return target


def _new_run_dir(root: Path) -> tuple[str, Path]:
    """A folder nothing has written to yet. Old evidence stays where it was."""
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)
    target = root / run_id
    target.mkdir(parents=True, exist_ok=False)
    return run_id, target


def _run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=kwargs.pop("timeout", 900), **kwargs)


def _reason(code: str, detail: str, cls: str = "fail", **extra) -> dict:
    return {"code": code, "class": cls, "detail": detail, **extra}


def verdict_for(reasons: list[dict]) -> str:
    """The only place a verdict comes from. FAIL wins, then PARTIAL, then OWNER."""
    classes = {reason.get("class") for reason in reasons}
    if "fail" in classes:
        return "FAIL"
    if "partial" in classes:
        return "PARTIAL"
    if "owner" in classes:
        return "OWNER_REQUIRED"
    return "PASS"


# ---------------------------------------------------------------- identity

def _harness_identity(manifest: dict) -> tuple[dict, list[dict]]:
    """Hashes of the checking scripts, compared with what the archive lists.

    An archive whose evening test is not the one in its own MANIFEST is
    reporting with somebody else's script; nothing it prints binds to the
    build. A file that is absent is left to the stage that needs it.
    """
    listed = {item.get("path"): item.get("sha256")
              for item in (manifest.get("files") or []) if isinstance(item, dict)}
    identity: dict = {}
    reasons: list[dict] = []
    if not listed:
        reasons.append(_reason("manifest_incomplete",
                               "MANIFEST.json lists no files: the archive cannot say what it ships"))
    for name in HARNESS_FILES:
        path = SUPPORT / name
        if not path.is_file():
            identity[name] = None
            continue
        digest = _sha256(path)
        identity[name] = digest
        expected = listed.get(f"app-support/{name}")
        if listed and expected != digest:
            reasons.append(_reason(
                "harness_not_the_shipped_one",
                f"app-support/{name} is not the file MANIFEST.json lists"
                + ("" if expected else " (it is not listed at all)")))
    return identity, reasons


def owner_run_problems(manifest: dict) -> list[dict]:
    """Комплект владельческого GUI-прогона внутри САМОГО архива.

    README комплекта обещает владельцу путь `app-support/owner-final-run/`.
    Пока этой проверки не было, обещание держалось на том, что файлы лежат в
    GitHub, — а владелец распаковывает ZIP, а не репозиторий.

    Список берётся из MANIFEST.json (`contents.owner_run.files`): он записан
    сборкой ДО упаковки, и сверить архив с ним — значит сверить его с тем, что
    было объявлено, а не с тем, что оказалось внутри. Отсутствие записи —
    тоже отказ: такой архив собран мимо контракта.
    """
    declared = (((manifest.get("contents") or {}).get("owner_run") or {}).get("files"))
    if not isinstance(declared, dict) or not declared:
        return [_reason(
            "owner_run_package_not_declared",
            "MANIFEST.json не объявляет app-support/owner-final-run: архив собран "
            "до контракта комплекта, и что в нём лежит — неизвестно")]

    root = SUPPORT / "owner-final-run"
    missing: list[str] = []
    changed: list[str] = []
    for name, expected in sorted(declared.items()):
        path = root / name
        if not path.is_file():
            missing.append(name)
        elif _sha256(path) != expected:
            changed.append(name)

    problems: list[dict] = []
    if missing:
        problems.append(_reason(
            "owner_run_package_incomplete",
            "в архиве нет файлов комплекта владельческого прогона: "
            + ", ".join(missing)))
    if changed:
        problems.append(_reason(
            "owner_run_package_not_the_shipped_one",
            "файлы комплекта не те, что перечислены в MANIFEST.json: "
            + ", ".join(changed)))
    return problems


# ------------------------------------------------------------------ doctor

def doctor_schema_problems(report: dict) -> list[str]:
    """Everything that makes a doctor report NOT a doctor report."""
    problems: list[str] = []
    if report.get("schema_version") != 1:
        problems.append(f"schema_version {report.get('schema_version')!r} is not 1")
    checks = report.get("checks")
    if not isinstance(checks, list) or not checks:
        problems.append("checks is not a non-empty list")
        return problems
    names: list[str] = []
    for index, check in enumerate(checks):
        if not isinstance(check, dict) or not isinstance(check.get("name"), str) or not check["name"]:
            problems.append(f"check #{index} has no name")
            continue
        if check.get("status") not in DOCTOR_STATUSES:
            problems.append(f"check {check['name']} has status {check.get('status')!r}")
        names.append(check["name"])
    missing = sorted(REQUIRED_DOCTOR_CHECKS - set(names))
    if missing:
        problems.append("required checks absent: " + ", ".join(missing))
    blocked = sum(1 for check in checks if isinstance(check, dict) and check.get("status") == "BLOCKED")
    if report.get("blocked") != blocked:
        problems.append(f"blocked count {report.get('blocked')!r} does not match {blocked} BLOCKED checks")
    return problems


def _doctor(python: Path, evidence: Path) -> dict:
    """State, exit code, the blocking checks, and the structured reasons.

    Naming the blocking checks matters: "BLOCKED (1)" tells the owner nothing,
    and the reader of a red build cannot tell a missing credential from a
    broken archive. Everything that is not a readable, complete, consistent
    report is a reason of class ``fail`` — never an empty list.
    """
    result = {"state": "NOT_SHIPPED", "returncode": None, "blocking": [], "reasons": []}
    script = SUPPORT / "bossman_doctor.py"
    if not script.is_file():
        result["reasons"].append(_reason(
            "doctor_not_shipped", "app-support/bossman_doctor.py is missing: the archive is incomplete"))
        return result
    try:
        done = _run([str(python), str(script), "--json"], timeout=DOCTOR_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", "replace")
        (evidence / "doctor.json").write_text(output or "", encoding="utf-8")
        result["state"] = "TIMEOUT"
        result["reasons"].append(_reason(
            "doctor_timeout", f"bossman_doctor.py did not finish within {DOCTOR_TIMEOUT} s"))
        return result
    result["returncode"] = done.returncode
    (evidence / "doctor.json").write_text(done.stdout or "", encoding="utf-8")
    (evidence / "doctor.log").write_text(done.stderr or "", encoding="utf-8")
    try:
        report = json.loads(done.stdout or "")
    except (json.JSONDecodeError, TypeError):
        report = None
    if not isinstance(report, dict):
        result["state"] = "UNREADABLE"
        result["reasons"].append(_reason(
            "doctor_unreadable",
            f"bossman_doctor.py exited {done.returncode} and printed no JSON report"))
        return result
    problems = doctor_schema_problems(report)
    if problems:
        result["state"] = "INVALID_SCHEMA"
        result["reasons"].append(_reason("doctor_invalid_schema", "; ".join(problems)))
        return result
    blocking = [{"name": check["name"], "detail": check.get("detail"), "remedy": check.get("remedy")}
                for check in report["checks"] if check["status"] == "BLOCKED"]
    result["blocking"] = blocking
    expected = 1 if blocking else 0
    if done.returncode != expected:
        result["state"] = "CRASHED"
        result["reasons"].append(_reason(
            "doctor_exit_code_mismatch",
            f"bossman_doctor.py exited {done.returncode} while its report says "
            f"{len(blocking)} BLOCKED (expected exit {expected})"))
        return result
    for check in blocking:
        if check["name"] in OWNER_MACHINE_CHECKS:
            result["reasons"].append(_reason(
                "doctor_blocked_owner_machine", f"{check['name']}: {check.get('detail')}",
                cls="owner", check=check["name"], remedy=check.get("remedy")))
        else:
            result["reasons"].append(_reason(
                "doctor_blocked_delivery", f"{check['name']}: {check.get('detail')}",
                check=check["name"]))
    result["state"] = "BLOCKED" if blocking else "OK"
    return result


# ---------------------------------------------------------------- verifier

def _verifier(python: Path, evidence: Path, sha: str) -> dict:
    """The installed product, judged by the result THIS run wrote."""
    result = {"status": "NOT_RUN", "returncode": None, "reasons": [], "tail": ""}
    script = SUPPORT / "verify_installed_product.py"
    if not script.is_file():
        result["reasons"].append(_reason(
            "verifier_not_shipped", "app-support/verify_installed_product.py is missing"))
        return result
    result_path = evidence / "installed-acceptance.json"
    if result_path.exists():
        result["reasons"].append(_reason(
            "stale_evidence", "installed-acceptance.json already exists in a fresh run folder"))
        return result
    try:
        done = _run([str(python), str(script), "--workdir", str(evidence / "work"),
                     "--out", str(result_path), "--expected-sha", sha], timeout=VERIFIER_TIMEOUT)
    except subprocess.TimeoutExpired:
        result["status"] = "TIMEOUT"
        result["reasons"].append(_reason(
            "verifier_timeout", f"verify_installed_product.py did not finish within {VERIFIER_TIMEOUT} s"))
        return result
    result["returncode"] = done.returncode
    result["tail"] = (done.stderr or "")[-1500:] or (done.stdout or "")[-1500:]
    (evidence / "installed-acceptance.log").write_text(
        (done.stdout or "") + (done.stderr or ""), encoding="utf-8")
    if not result_path.is_file():
        result["status"] = "NO_RESULT"
        result["reasons"].append(_reason(
            "verifier_no_result",
            f"verify_installed_product.py exited {done.returncode} and wrote no installed-acceptance.json"))
        return result
    try:
        report = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        report = None
    if not isinstance(report, dict):
        result["status"] = "UNREADABLE"
        result["reasons"].append(_reason(
            "verifier_unreadable", "installed-acceptance.json is not a JSON object"))
        return result
    status = report.get("status")
    result["status"] = status if isinstance(status, str) and status else "INVALID"
    if status != "PASS":
        result["reasons"].append(_reason(
            "verifier_not_passed", f"installed acceptance status {status!r}, exit code {done.returncode}"))
    if report.get("source_sha") != sha:
        result["reasons"].append(_reason(
            "verifier_sha_mismatch",
            f"installed acceptance was written for source_sha {report.get('source_sha')!r}, not {sha}"))
    if (done.returncode == 0) != (status == "PASS"):
        result["reasons"].append(_reason(
            "verifier_inconsistent",
            f"exit code {done.returncode} does not agree with status {status!r}"))
    return result


# ------------------------------------------------------------ owner stages

def _stage(python: Path, evidence: Path, sha: str, name: str) -> dict:
    """One shipped owner runner, judged by the report it wrote for THIS run."""
    script = SUPPORT / f"{name}.py"
    labels = {"installed_ui_sweep": "ui-sweep", "live_openrouter_owner": "live-model"}
    label = labels[name]
    result = {"status": "NOT_RUN", "returncode": None, "reasons": []}
    if not script.is_file():
        result["reasons"].append(_reason(f"{label}_not_shipped", f"app-support/{name}.py is missing"))
        return result
    output = evidence / f"{label}.json"
    args = [str(python), "-I", str(script), "--expected-sha", sha, "--output", str(output)]
    if name == "live_openrouter_owner":
        args += ["--trajectories", str(evidence / "live-trajectories.jsonl")]
    try:
        done = _run(args, timeout=VERIFIER_TIMEOUT, cwd=str(HOME))
    except subprocess.TimeoutExpired:
        result["status"] = "TIMEOUT"
        result["reasons"].append(_reason(f"{label}_timeout", f"{name}.py did not finish within {VERIFIER_TIMEOUT} s"))
        return result
    result["returncode"] = done.returncode
    (evidence / f"{label}.log").write_text((done.stdout or "") + (done.stderr or ""), encoding="utf-8")
    try:
        report = json.loads(output.read_text(encoding="utf-8")) if output.is_file() else None
    except (OSError, ValueError):
        report = None
    if not isinstance(report, dict):
        result["status"] = "NO_RESULT"
        result["reasons"].append(_reason(f"{label}_no_result",
                                         f"{name}.py exited {done.returncode} and wrote no {label}.json"))
        return result
    status = report.get("status")
    result["status"] = status if isinstance(status, str) and status else "INVALID"
    if report.get("source_sha") != sha:
        result["reasons"].append(_reason(f"{label}_sha_mismatch",
                                         f"{label}.json was written for {report.get('source_sha')!r}, not {sha}"))
    if status == "OWNER_REQUIRED" and done.returncode == 2:
        result["reasons"].append(_reason(f"{label}_owner_required", str(report.get("reason") or
                                         "something only the owner's account can supply is missing"), cls="owner"))
    elif status != "PASS" or done.returncode != 0:
        result["reasons"].append(_reason(f"{label}_not_passed",
                                         f"status {status!r}, exit code {done.returncode}"))
    return result


# -------------------------------------------------------------------- main

def _accept(args, manifest: dict, sha: str, python: Path, evidence: Path, result: dict) -> str:
    print("=" * 66)
    print("BOSSMAN — вечерняя приёмка владельца (этот загруженный билд)")
    print("=" * 66)
    print(f"  BUILD SHA:   {sha}")
    print(f"  артефакт:    {manifest.get('artifact')}")
    print(f"  запуск:      {result['run_id']}")
    print("  источник:    downloaded archive (репозиторий не требуется)")
    print(f"  платформа:   {platform.platform()}")
    print(f"  runtime:     {manifest.get('contents', {}).get('runtime', {}).get('python_version')}"
          f" ({python})")
    print(f"  улики:       {evidence}")
    required = manifest.get("required_downloads") or []
    if required:
        print("  докачивает при первом запуске: "
              + ", ".join(str(item.get("component")) for item in required if isinstance(item, dict)))
    print("-" * 66, flush=True)

    identity, reasons = _harness_identity(manifest)
    result["harness"] = identity
    result["reasons"].extend(reasons)

    package = owner_run_problems(manifest)
    result["owner_run_package"] = "PASS" if not package else "FAIL"
    result["reasons"].extend(package)
    for problem in package:
        print("  комплект владельца: " + problem["detail"])

    if args.skip_doctor:
        result["doctor"] = "SKIPPED"
        result["reasons"].append(_reason(
            "doctor_skipped", "--skip-doctor: diagnostic run, the verdict is PARTIAL at best",
            cls="partial"))
        print("  доктор:      SKIPPED (итог не выше PARTIAL)")
    else:
        doctor = _doctor(python, evidence)
        result["doctor"] = doctor["state"]
        result["doctor_returncode"] = doctor["returncode"]
        result["doctor_blocked"] = [{"name": c["name"], "detail": c.get("detail")} for c in doctor["blocking"]]
        result["reasons"].extend(doctor["reasons"])
        print(f"  доктор:      {doctor['state']}"
              + (f" ({len(doctor['blocking'])} BLOCKED)" if doctor["blocking"] else ""))
        for check in doctor["blocking"]:
            print(f"     BLOCKED  {check['name']}: {check.get('detail')}")
            if check.get("remedy"):
                print(f"              {check['remedy']}")
        if doctor["state"] == "NOT_SHIPPED":
            # An incomplete archive: nothing further it says would bind to a build.
            print("  приёмка:     NOT_RUN (архив неполон)")
            return verdict_for(result["reasons"])

    verifier = _verifier(python, evidence, sha)
    result["installed_acceptance"] = verifier["status"]
    result["installed_acceptance_returncode"] = verifier["returncode"]
    result["reasons"].extend(verifier["reasons"])
    result["_verifier_tail"] = verifier["tail"]
    print(f"  приёмка:     {verifier['status']}")

    for item in required:
        component = item.get("component") if isinstance(item, dict) else item
        result["reasons"].append(_reason(
            "required_download", f"{component}: acquired by the first run, not verified here",
            cls="owner"))

    stages = []
    if args.full or args.ui_sweep:
        stages.append("installed_ui_sweep")
    if args.full or args.live:
        stages.append("live_openrouter_owner")
    for name in stages:
        stage = _stage(python, evidence, sha, name)
        result["stages"][name] = {"status": stage["status"], "returncode": stage["returncode"]}
        result["reasons"].extend(stage["reasons"])
        print(f"  {name}: {stage['status']}")
    return verdict_for(result["reasons"])


def main(argv: list[str] | None = None) -> int:
    _console_utf8()
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-doctor", action="store_true",
                        help="diagnostic only: the verdict becomes PARTIAL, never PASS")
    parser.add_argument("--full", action="store_true",
                        help="also the visible-button sweep and the free-model smoke (shipped runners)")
    parser.add_argument("--ui-sweep", action="store_true", help="also the visible-button sweep")
    parser.add_argument("--live", action="store_true",
                        help="also the free-model smoke; needs BOSSMAN_OPENROUTER_API_KEY, else OWNER_REQUIRED")
    args = parser.parse_args(argv)

    manifest_path = HOME / "MANIFEST.json"
    if not manifest_path.exists():
        print("OWNER_EVENING_RESULT=FAIL: MANIFEST.json is missing — "
              "this is not a complete Bossman archive", file=sys.stderr)
        return EXIT_CODES["FAIL"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        sha = manifest["source_sha"]
        if not isinstance(manifest, dict) or not isinstance(sha, str) or not SHA.fullmatch(sha):
            raise ValueError("source_sha is not a full commit SHA")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"OWNER_EVENING_RESULT=FAIL: MANIFEST.json is unreadable ({exc})", file=sys.stderr)
        return EXIT_CODES["FAIL"]
    python = HOME / "runtime" / ("python.exe" if os.name == "nt" else "bin/python")
    try:
        run_id, evidence = _new_run_dir(_evidence_root(sha))
    except OSError as exc:
        print(f"OWNER_EVENING_RESULT=FAIL: cannot create an evidence folder ({exc})", file=sys.stderr)
        return EXIT_CODES["FAIL"]

    result: dict = {
        "schema_version": RESULT_SCHEMA_VERSION, "run_id": run_id, "source_sha": sha,
        "artifact": manifest.get("artifact"), "manifest_sha256": _sha256(manifest_path),
        "platform": platform.platform(), "harness": {},
        "doctor": "NOT_RUN", "doctor_returncode": None, "doctor_blocked": [],
        "installed_acceptance": "NOT_RUN", "installed_acceptance_returncode": None,
        "required_downloads": manifest.get("required_downloads") or [],
        "stages": {},
        "reasons": [], "verdict": "FAIL", "exit_code": EXIT_CODES["FAIL"],
    }
    try:
        verdict = _accept(args, manifest, sha, python, evidence, result)
    except Exception as exc:  # noqa: BLE001 — a crashed check is a FAIL with a name, never a stale PASS
        result["reasons"].append(_reason("harness_exception", f"{type(exc).__name__}: {exc}"))
        verdict = "FAIL"
    tail = result.pop("_verifier_tail", "")
    result["verdict"] = verdict
    result["exit_code"] = EXIT_CODES[verdict]
    result["recorded_at"] = datetime.now(timezone.utc).isoformat()
    try:
        (evidence / "OWNER_EVENING_RESULT.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"OWNER_EVENING_RESULT=FAIL: cannot write the result ({exc})", file=sys.stderr)
        return EXIT_CODES["FAIL"]

    print("-" * 66)
    print(f"OWNER_EVENING_SHA={sha}")
    print(f"OWNER_EVENING_RUN_ID={run_id}")
    print(f"OWNER_EVENING_EVIDENCE={evidence}")
    for reason in result["reasons"]:
        print(f"OWNER_EVENING_REASON={reason['code']} [{reason['class']}]: {reason['detail']}")
    print(f"OWNER_EVENING_RESULT={verdict}")
    if verdict == "FAIL" and tail:
        print(tail, file=sys.stderr)
    return EXIT_CODES[verdict]


if __name__ == "__main__":
    raise SystemExit(main())
