#!/usr/bin/env python3
"""Adjudicate CONFLICT_PRIOR_EVIDENCE leaves with an independent judge (Haiku 5.5 via OpenRouter).

leaf_pytest_probe never overwrites another probe's record. When a leaf already has an out/<id>.txt from an older
run (e.g. its tests were skipped back then) and its importing tests pass now, a human used to decide. Owner rule
08.10/10.10: Haiku 5.5 is the leaf verifier, hard cap $2/day. This tool:
  1. runs the leaf's importing tests exactly like the probe (same selection, same command), writes the NEW output
     next to the old one (out/<id>.<lane>.txt) — the old record is never edited;
  2. shows the judge the leaf (id, label, source), the OLD record and the NEW record, asks for strict JSON
     {"verdict": "ACCEPT_NEW" | "KEEP_OLD" | "NEEDS_TEST", "reason": "..."};
  3. only ACCEPT_NEW with exit_code 0 moves the old record to out/archive/ (kept, not deleted), installs the new one
     and appends a pytest receipt carrying the judge's verdict to the lane file.
Spend is metered from the API's reported usage and stops at --cap-usd. The key is read from the env file, never printed.

  python tools/tree_proof/haiku_adjudicate.py --leaf plugin-6 --leaf module-6bf6aca94f5f --lane l2-20261010
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import leaf_pytest_probe as probe  # noqa: E402

ROOT, SEED, EVID = probe.ROOT, probe.SEED, probe.EVID
MODEL = "anthropic/claude-haiku-5.5"
PRICE_IN, PRICE_OUT = 0.10 / 1e6, 0.50 / 1e6          # USD per token, as metered in caption_review 09.10
KEYS = Path(os.path.expandvars(r"%LOCALAPPDATA%\Bossman\keys\provider-keys.env"))
VERDICTS = ("ACCEPT_NEW", "KEEP_OLD", "NEEDS_TEST")


def key() -> str:
    for line in KEYS.read_text(encoding="utf-8").splitlines():
        if line.startswith("OPENROUTER_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"')
    raise RuntimeError("OPENROUTER_API_KEY missing")


def ask(prompt: str) -> tuple[dict, float]:
    body = json.dumps({"model": MODEL, "temperature": 0, "max_tokens": 1500,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=body, method="POST",
                                 headers={"Authorization": "Bearer " + key(), "Content-Type": "application/json",
                                          "User-Agent": "bossman-tree/1"})
    d = json.load(urllib.request.urlopen(req, timeout=120))
    u = d.get("usage") or {}
    cost = u.get("prompt_tokens", 0) * PRICE_IN + u.get("completion_tokens", 0) * PRICE_OUT
    text = d["choices"][0]["message"]["content"] or ""
    m = re.search(r"\{.*\}", text, re.S)
    try:
        v = json.loads(m.group(0)) if m else {}
    except ValueError:
        v = {}
    if v.get("verdict") not in VERDICTS:
        v = {"verdict": "KEEP_OLD", "reason": "judge answer was not valid JSON: " + text[:200]}
    return v, cost


def run_tests(tests: list[str], python: str) -> tuple[str, int, str]:
    cwd = ROOT / ("bossman-core" if tests[0].startswith("bossman-core/") else
                  "command-center" if tests[0].startswith("command-center/") else ".")
    rel = [str(Path(ROOT / t).relative_to(cwd)) for t in tests[:8]]
    r = subprocess.run([python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--timeout=300", *rel], cwd=cwd,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-6:])[-1400:]
    return tail, r.returncode, f"(cwd={cwd.relative_to(ROOT) if cwd != ROOT else '.'}) pytest -q {' '.join(rel)}"


PROMPT = """You are an independent verifier of a software capability tree. Decide whether NEW test evidence should
replace an OLD record for one leaf. Be strict and literal.

Leaf: id={nid} label={label!r} source={src}
Tests that import this module: {tests}

OLD record (older revision):
---
{old}
---

NEW record (revision {sha}):
---
{new}
---

Rules:
- ACCEPT_NEW only if the NEW run exit_code is 0, tests actually ran (not all skipped, not "no tests ran"), and the
  tests plausibly exercise THIS leaf's module (they import it; the label matches what they test).
- KEEP_OLD if the new run failed or is weaker than the old record.
- NEEDS_TEST if tests pass but clearly do not exercise what the label claims (e.g. only import, live calls skipped).
Answer ONLY with JSON: {{"verdict": "ACCEPT_NEW|KEEP_OLD|NEEDS_TEST", "reason": "<one sentence>"}}"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--leaf", action="append", required=True)
    ap.add_argument("--lane", default="l2-20261010")
    ap.add_argument("--cap-usd", type=float, default=0.5)
    ap.add_argument("--python", default=sys.executable)
    a = ap.parse_args(argv)
    seed = {n["id"]: n for n in json.loads(SEED.read_text(encoding="utf-8"))["nodes"]}
    sha = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    out_dir, archive = EVID / "out", EVID / "out" / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    lane_file = EVID / f"{a.lane}.json"
    receipts = json.loads(lane_file.read_text(encoding="utf-8")) if lane_file.is_file() else []
    spent, report = 0.0, []
    for nid in a.leaf:
        node = seed.get(nid) or {}
        src = probe.source_of(node) if node else None
        mod = probe.dotted(src) if src else None
        tests = probe.importing_tests(mod) if mod else []
        old_path = out_dir / f"{nid}.txt"
        if not tests or not old_path.is_file():
            report.append({"leaf": nid, "verdict": "SKIPPED", "reason": "no importing tests or no prior record"})
            continue
        if spent >= a.cap_usd:
            report.append({"leaf": nid, "verdict": "NOT_RUN", "reason": f"spend cap ${a.cap_usd} reached"})
            continue
        started = datetime.now(timezone.utc).isoformat()
        tail, code, cmd = run_tests(tests, a.python)
        new_text = f"$ {cmd}\nsha: {sha}\n\n{tail}\nexit_code: {code}\n"
        new_path = out_dir / f"{nid}.{a.lane}.txt"
        new_path.write_text(new_text, encoding="utf-8", newline="\n")
        verdict, cost = ask(PROMPT.format(nid=nid, label=node.get("label", ""), src=src, tests=", ".join(tests[:8]),
                                          old=old_path.read_text(encoding="utf-8")[-2500:], new=new_text, sha=sha[:12]))
        spent += cost
        row = {"leaf": nid, "exit_code": code, **verdict, "cost_usd": round(cost, 5)}
        if verdict["verdict"] == "ACCEPT_NEW" and code == 0:
            shutil.move(str(old_path), str(archive / f"{nid}.before-{a.lane}.txt"))
            shutil.copyfile(new_path, old_path)
            receipts = [r for r in receipts if r.get("node_id") != nid]
            receipts.append({"node_id": nid, "sha": sha, "probe": f"pytest {len(tests)} test file(s) importing {mod}",
                             "command": cmd, "exit_code": 0, "started_at": started,
                             "finished_at": datetime.now(timezone.utc).isoformat(),
                             "output_sha256": hashlib.sha256(old_path.read_bytes()).hexdigest(),
                             "output_tail": tail[-1400:], "verdict": "PASS", "kind": "pytest",
                             "adjudication": {"judge": MODEL, "verdict": "ACCEPT_NEW", "reason": verdict.get("reason", "")}})
            row["installed"] = True
        report.append(row)
    if receipts:
        lane_file.write_text(json.dumps(receipts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    (EVID / f"{a.lane}-adjudication.json").write_text(
        json.dumps({"sha": sha, "judge": MODEL, "spent_usd": round(spent, 5), "rows": report}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    for r in report:
        print(f"{r['leaf']}: {r['verdict']} exit={r.get('exit_code')} ${r.get('cost_usd', 0)} — {r.get('reason', '')[:140]}")
    print(f"spent ${spent:.4f} of cap ${a.cap_usd}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
