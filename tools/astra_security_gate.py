"""SAST/SCA with durable JSON reports; tool failures and findings both block CI."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import sys


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--component", choices=("bossman-core", "command-center"), required=True)
    p.add_argument("--output", default="astra-security-results")
    args=p.parse_args()
    out=Path(args.output); out.mkdir(parents=True, exist_ok=True)
    sources=["bossman-core/bossman", "bossman-core/bossman_v3", "bossman_shared"] if args.component == "bossman-core" else ["command-center/bcc"]
    commands={
        "bandit": [sys.executable,"-m","bandit","-r",*sources,"-q","--severity-level","high",
                   "--confidence-level","medium","-f","json","-o",str(out/"bandit.json")],
        "pip-audit": [sys.executable,"-m","pip_audit","--progress-spinner","off","--format","json",
                      "--output",str(out/"pip-audit.json")],
    }
    results={}
    for name, cmd in commands.items():
        output=out/f"{name}.json"
        output.unlink(missing_ok=True)
        try:
            with (out/f"{name}.log").open("w",encoding="utf-8") as log:
                run=subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, timeout=300, check=False)
            body=json.loads(output.read_text(encoding="utf-8"))
            if name == "bandit":
                errors=body.get("errors", [])
                findings=len(body["results"])
            else:
                dependencies=body["dependencies"]
                # Editable first-party packages have no advisory database identity.
                # A scan error still fails through the exit code/missing report.
                first_party = {"bossman-core", "bossman-command-center", "bossman-shared"}
                errors = [d for d in dependencies if d.get("skip_reason") and d.get("name") not in first_party]
                if not any(not d.get("skip_reason") for d in dependencies):
                    errors.append({"error": "no third-party dependencies were audited"})
                findings=sum(len(d.get("vulns", [])) for d in dependencies)
            status="TOOL_ERROR" if run.returncode not in (0,1) or errors else ("FINDINGS" if findings else "PASS")
            if run.returncode == 1 and findings == 0: status="TOOL_ERROR"
            results[name]={"status":status,"exit_code":run.returncode,"findings":findings,"errors":errors}
        except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
            results[name]={"status":"TOOL_ERROR","error":f"{type(exc).__name__}: {exc}"}
    (out/"summary.json").write_text(json.dumps(results,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(results,indent=2))
    return 0 if all(x["status"] == "PASS" for x in results.values()) else 1

if __name__ == "__main__": raise SystemExit(main())
