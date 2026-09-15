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
``MANIFEST.json`` names the exact source SHA the wheels were built from, and the
installed product must report that same SHA back over HTTP.

Verdicts:

* ``PASS`` — the installed product booted and answered for this exact SHA;
* ``OWNER_REQUIRED`` — everything repository-checkable passed, but something
  only the owner's machine/account can supply is missing (model credentials, a
  prerequisite the archive is allowed to fetch on first run);
* ``FAIL`` — this build does not do what it claims.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SUPPORT = Path(__file__).resolve().parent
HOME = SUPPORT.parent


def _console_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _evidence_root(sha: str) -> Path:
    """Outside the bundle when possible: replacing the folder keeps history."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME")
    root = (Path(base) / "Bossman" / "evening") if base else (HOME / "evidence")
    target = root / sha
    target.mkdir(parents=True, exist_ok=True)
    return target


def _run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=kwargs.pop("timeout", 900), **kwargs)


def _doctor(python: Path, evidence: Path) -> tuple[str, list[dict]]:
    """Returns the state and the checks that are actually blocking.

    Naming them matters: "BLOCKED (1)" tells the owner nothing, and the reader
    of a red build cannot tell a missing credential from a broken archive.
    """
    script = SUPPORT / "bossman_doctor.py"
    if not script.exists():
        return "NOT_SHIPPED", []
    done = _run([str(python), str(script), "--json"])
    report_path = evidence / "doctor.json"
    report_path.write_text(done.stdout or "{}", encoding="utf-8")
    try:
        report = json.loads(done.stdout)
    except json.JSONDecodeError:
        return "UNREADABLE", []
    blocking = [check for check in report.get("checks", [])
                if check.get("status") == "BLOCKED"]
    return ("BLOCKED" if blocking else "OK"), blocking


def main(argv: list[str] | None = None) -> int:
    _console_utf8()
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-doctor", action="store_true")
    args = parser.parse_args(argv)

    manifest_path = HOME / "MANIFEST.json"
    if not manifest_path.exists():
        print("OWNER_EVENING_RESULT=FAIL: MANIFEST.json is missing — "
              "this is not a complete Bossman archive", file=sys.stderr)
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sha = manifest["source_sha"]
    python = HOME / "runtime" / ("python.exe" if os.name == "nt" else "bin/python")
    evidence = _evidence_root(sha)

    print("=" * 66)
    print("BOSSMAN — вечерняя приёмка владельца (этот загруженный билд)")
    print("=" * 66)
    print(f"  BUILD SHA:   {sha}")
    print(f"  артефакт:    {manifest.get('artifact')}")
    print(f"  источник:    downloaded archive (репозиторий не требуется)")
    print(f"  платформа:   {platform.platform()}")
    print(f"  runtime:     {manifest.get('contents', {}).get('runtime', {}).get('python_version')}"
          f" ({python})")
    print(f"  улики:       {evidence}")
    required = manifest.get("required_downloads") or []
    if required:
        print(f"  докачивает при первом запуске: "
              f"{', '.join(item['component'] for item in required)}")
    print("-" * 66, flush=True)

    doctor_state, blocking = ("SKIPPED", [])
    if not args.skip_doctor:
        doctor_state, blocking = _doctor(python, evidence)
    print(f"  доктор:      {doctor_state}"
          + (f" ({len(blocking)} BLOCKED)" if blocking else ""))
    for check in blocking:
        print(f"     BLOCKED  {check.get('name')}: {check.get('detail')}")
        if check.get("remedy"):
            print(f"              {check['remedy']}")

    verifier = SUPPORT / "verify_installed_product.py"
    if not verifier.exists():
        print("OWNER_EVENING_RESULT=FAIL: verify_installed_product.py is missing",
              file=sys.stderr)
        return 2
    result_path = evidence / "installed-acceptance.json"
    done = _run([str(python), str(verifier), "--workdir", str(evidence / "work"),
                 "--out", str(result_path), "--expected-sha", sha])
    (evidence / "installed-acceptance.log").write_text(
        (done.stdout or "") + (done.stderr or ""), encoding="utf-8")

    acceptance = "FAIL"
    if result_path.exists():
        try:
            acceptance = json.loads(result_path.read_text(encoding="utf-8")).get("status", "FAIL")
        except json.JSONDecodeError:
            acceptance = "UNREADABLE"
    print(f"  приёмка:     {acceptance}")

    verdict = "PASS"
    if done.returncode or acceptance != "PASS":
        verdict = "FAIL"
    elif blocking or required:
        verdict = "OWNER_REQUIRED"

    (evidence / "OWNER_EVENING_RESULT.json").write_text(json.dumps({
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "source_sha": sha, "artifact": manifest.get("artifact"),
        "platform": platform.platform(), "doctor": doctor_state,
        "doctor_blocked": [{"name": c.get("name"), "detail": c.get("detail")}
                           for c in blocking],
        "installed_acceptance": acceptance, "required_downloads": required,
        "verdict": verdict,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print("-" * 66)
    print(f"OWNER_EVENING_SHA={sha}")
    print(f"OWNER_EVENING_EVIDENCE={evidence}")
    print(f"OWNER_EVENING_RESULT={verdict}")
    if verdict == "FAIL":
        print(done.stderr[-1500:] or done.stdout[-1500:], file=sys.stderr)
    return 0 if verdict == "PASS" else (2 if verdict == "OWNER_REQUIRED" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
