"""Run isolated bounded acceptance tests and reject skips, missing tests or hangs."""
from __future__ import annotations
import argparse
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
}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--profile", choices=PROFILES, default="runner")
    parser.add_argument("--output", default="astra-acceptance-results")
    args=parser.parse_args(); out=Path(args.output).resolve(); out.mkdir(parents=True,exist_ok=True)
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

if __name__ == "__main__": raise SystemExit(main())
