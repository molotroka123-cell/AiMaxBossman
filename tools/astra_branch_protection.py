"""Prepare/check/apply the ASTRA GitHub branch gate. Writes require explicit --apply."""
from __future__ import annotations
import argparse
import json
import subprocess
from urllib.parse import quote

CHECKS=["compile + секреты", "секреты, JS, запрещённые файлы",
        "root pytest + hygiene (py3.11)", "root pytest + hygiene (py3.12)",
        "bossman-core container ships bossman-shared", "pytest (py3.11)", "pytest (py3.12)",
        "safety (3.11)", "safety (3.12)", "ASTRA portable (ubuntu-latest)",
        "ASTRA portable (windows-latest)", "ASTRA runner recovery"]
CHECKS += [f"pytest {group} (py{version})" for group in ("security","gateway-context","stage8-14","rest")
           for version in ("3.11","3.12")]
POLICY={"required_status_checks":{"strict":True,"contexts":CHECKS},"enforce_admins":True,
        "required_pull_request_reviews":{"dismiss_stale_reviews":True,"required_approving_review_count":1},
        "restrictions":None,"allow_force_pushes":False,"allow_deletions":False,
        "required_conversation_resolution":True}

def api(path, *, method="GET", body=None):
    cmd=["gh","api",path,"--method",method]
    if body is not None: cmd += ["--input","-"]
    result=subprocess.run(cmd,input=json.dumps(body) if body is not None else None,
                          text=True,capture_output=True,timeout=60,check=False)
    if result.returncode: raise RuntimeError(result.stderr.strip())
    return json.loads(result.stdout or "{}")

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--repo",default="molotroka123-cell/AiMaxBossman")
    p.add_argument("--branch",default="claude/bossman-control-v03-43igbk")
    g=p.add_mutually_exclusive_group();g.add_argument("--check",action="store_true");g.add_argument("--apply",action="store_true")
    args=p.parse_args()
    if not args.apply and not args.check:
        print(json.dumps(POLICY,ensure_ascii=False,indent=2));return 0
    path=f"repos/{args.repo}/branches/{quote(args.branch,safe='')}/protection"
    if args.apply:
        # Do not overwrite an existing policy/restrictions. Review the JSON and merge it manually.
        branch=api(f"repos/{args.repo}/branches/{quote(args.branch,safe='')}")
        if branch.get("protected"):
            raise RuntimeError("Branch already protected. Refusing to replace its existing policy; run --check and merge requirements manually.")
        api(path,method="PUT",body=POLICY)
    actual=api(path)
    checks=actual.get("required_status_checks") or {}
    present=set(checks.get("contexts",[])) | {c.get("context") for c in checks.get("checks",[])}
    missing=sorted(set(CHECKS)-present)
    failures=[]
    if missing: failures.append({"missing_checks":missing})
    if not checks.get("strict"): failures.append("strict status checks disabled")
    if not (actual.get("enforce_admins") or {}).get("enabled"): failures.append("admins can bypass")
    for flag in ("allow_force_pushes","allow_deletions"):
        if (actual.get(flag) or {}).get("enabled"): failures.append(flag)
    review=actual.get("required_pull_request_reviews") or {}
    if review.get("required_approving_review_count",0) < 1 or not review.get("dismiss_stale_reviews"):
        failures.append("pull request review gate incomplete")
    if not (actual.get("required_conversation_resolution") or {}).get("enabled"):
        failures.append("conversation resolution gate disabled")
    print(json.dumps({"repo":args.repo,"branch":args.branch,"failures":failures},ensure_ascii=False,indent=2))
    return bool(failures)

if __name__ == "__main__": raise SystemExit(main())
