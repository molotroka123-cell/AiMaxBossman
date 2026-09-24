#!/usr/bin/env python3
"""Fresh owner scenarios (Phase 11) for a worker model: the model works in a
throw-away copy of `repo/` through tools, then HIDDEN tests decide the verdict.

MODEL SAYS DONE != DONE: the model's own claim is recorded but never scores.
The hidden tests live outside the sandbox and are copied in only after the
model has finished. Every model call goes through tools/worker_client.py and
is recorded to the distillation dataset with the final verdict.

    python tools/scenario_runner.py --base https://openrouter.ai/api/v1 \
        --model nvidia/nemotron-3-ultra-550b-a55b:free --out <dir>
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from distill_recorder import Recorder  # noqa: E402
from worker_client import Worker  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCEN = ROOT / "scenarios" / "owner-20260924"
REPO_SCENARIOS = ["s01_hidden_defect", "s02_misleading_test", "s03_windows_path", "s06_false_done"]
MAX_STEPS = 30

AGENT_TOOLS = [
    {"type": "function", "function": {"name": "list_files", "description": "List files in the workspace.",
                                      "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file.",
                                      "parameters": {"type": "object", "required": ["path"],
                                                     "properties": {"path": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "write_file", "description": "Create or overwrite a UTF-8 text file.",
                                      "parameters": {"type": "object", "required": ["path", "content"],
                                                     "properties": {"path": {"type": "string"},
                                                                    "content": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "run", "description": "Run a command in the workspace "
                                      "(e.g. `python -m pytest -q`, `python make_report.py`). 60 s limit.",
                                      "parameters": {"type": "object", "required": ["command"],
                                                     "properties": {"command": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "finish", "description": "Stop working and report.",
                                      "parameters": {"type": "object", "required": ["status", "summary"],
                                                     "properties": {"status": {"type": "string", "enum": ["DONE", "BLOCKED"]},
                                                                    "summary": {"type": "string"}}}}},
]
SYSTEM = ("You are a software engineer working in a small repository through tools. Inspect before editing, "
          "reproduce problems, make minimal correct changes, run tests, and verify results before calling "
          "finish. Paths are relative to the workspace root.")


def _safe(ws: pathlib.Path, rel: str) -> pathlib.Path:
    p = (ws / rel.replace("\\", "/")).resolve()
    if ws.resolve() not in p.parents and p != ws.resolve():
        raise ValueError("path outside workspace")
    return p


def tool_exec(ws: pathlib.Path, name: str, args: dict) -> str:
    try:
        if name == "list_files":
            return "\n".join(sorted(str(p.relative_to(ws)).replace("\\", "/") for p in ws.rglob("*")
                                    if p.is_file() and "__pycache__" not in p.parts and ".pytest_cache" not in p.parts))
        if name == "read_file":
            return _safe(ws, args["path"]).read_text(encoding="utf-8")[:20000]
        if name == "write_file":
            p = _safe(ws, args["path"])
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(args["content"], encoding="utf-8", newline="\n")
            return f"wrote {len(args['content'])} chars to {args['path']}"
        if name == "run":
            cmd = args["command"]
            if re.search(r"\b(rm\s+-rf\s+/|format|shutdown|curl|wget|pip\s+install)\b", cmd):
                return "refused: command not allowed in this sandbox"
            cmd = re.sub(r"^\s*python3?\b", f'"{sys.executable}"', cmd)
            cmd = re.sub(r"^\s*pytest\b", f'"{sys.executable}" -m pytest', cmd)
            r = subprocess.run(cmd, cwd=ws, shell=True, capture_output=True, text=True, timeout=60,
                               encoding="utf-8", errors="replace",
                               env={**os.environ, "PYTHONPATH": str(ws), "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
            return f"exit_code={r.returncode}\n{(r.stdout + r.stderr)[-6000:]}"
        return f"unknown tool {name}"
    except subprocess.TimeoutExpired:
        return "exit_code=timeout"
    except Exception as exc:  # noqa: BLE001 — a tool error is information for the model
        return f"ERROR: {type(exc).__name__}: {exc}"


def hidden_verdict(scen: pathlib.Path, ws: pathlib.Path, original: pathlib.Path) -> dict:
    hid = ws / "_hidden_tests"
    shutil.copytree(scen / "hidden", hid)
    env = {**os.environ, "PYTHONPATH": str(ws), "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(hid)], cwd=ws,
                       capture_output=True, text=True, env=env, timeout=120, encoding="utf-8", errors="replace")
    shutil.rmtree(hid, ignore_errors=True)
    out = {"hidden_rc": r.returncode, "hidden_tail": (r.stdout + r.stderr)[-800:]}
    vis = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=ws,
                         capture_output=True, text=True, env=env, timeout=120, encoding="utf-8", errors="replace")
    out["visible_rc"] = vis.returncode if (ws / "tests").exists() else 0
    name = scen.name
    checks = {"hidden_pass": r.returncode == 0, "visible_suite_green": out["visible_rc"] in (0, 5)}
    if name == "s01_hidden_defect":
        # the model's regression tests must FAIL against the original product code
        probe = pathlib.Path(tempfile.mkdtemp(prefix="s01probe-"))
        shutil.copy(original / "invoice.py", probe / "invoice.py")
        shutil.copytree(ws / "tests", probe / "tests")
        rr = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=probe,
                            capture_output=True, text=True, env={**env, "PYTHONPATH": str(probe)}, timeout=120)
        checks["regression_fails_on_original"] = rr.returncode != 0
        shutil.rmtree(probe, ignore_errors=True)
    if name == "s02_misleading_test":
        checks["product_contract_unchanged"] = r.returncode == 0
    out["checks"] = checks
    out["verdict"] = "PASS" if all(checks.values()) else "FAIL"
    return out


def run_repo_scenario(w: Worker, scen: pathlib.Path, rec_note: str) -> dict:
    ws = pathlib.Path(tempfile.mkdtemp(prefix=f"{scen.name}-"))
    shutil.copytree(scen / "repo", ws, dirs_exist_ok=True)
    task = (scen / "task.md").read_text(encoding="utf-8")
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": task}]
    t0 = time.time()
    claim, summary, steps, tool_errors, calls_made, record_ids = None, "", 0, 0, [], []
    for steps in range(1, MAX_STEPS + 1):
        o = w.chat(messages, tools=AGENT_TOOLS, task_class=f"scenario:{scen.name}", max_tokens=16000,
                   curator_note=rec_note)
        record_ids.append(o.get("record_id"))
        if o["error"]:
            summary = f"model error: {o['error'][:200]}"
            break
        msg = {"role": "assistant", "content": o["text"] or ""}
        if o["tool_calls"]:
            msg["tool_calls"] = [{"id": c["id"] or f"c{steps}_{i}", "type": "function", "function": c["function"]}
                                 for i, c in enumerate(o["tool_calls"])]
        messages.append(msg)
        if not o["tool_calls"]:
            messages.append({"role": "user", "content": "Use the tools to continue, or call finish."})
            continue
        done = False
        for c in msg["tool_calls"]:
            fn = c["function"]["name"]
            try:
                args = json.loads(c["function"]["arguments"] or "{}")
            except ValueError:
                args, tool_errors = {}, tool_errors + 1
                result = "ERROR: arguments are not valid JSON"
            else:
                if fn == "finish":
                    claim, summary, done = args.get("status"), args.get("summary", ""), True
                    result = "ok"
                else:
                    result = tool_exec(ws, fn, args)
                    if result.startswith("ERROR"):
                        tool_errors += 1
            calls_made.append({"tool": fn, "args": {k: (v if len(str(v)) < 200 else str(v)[:200] + "…")
                                                    for k, v in args.items()}})
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": result})
        if done:
            break
    verdict = hidden_verdict(scen, ws, scen / "repo")
    res = {"scenario": scen.name, "model": w.model, "claim": claim, "summary": summary[:1500], "steps": steps,
           "tool_errors": tool_errors, "duration_s": round(time.time() - t0, 1), "tool_calls": calls_made,
           **verdict, "false_done": claim == "DONE" and verdict["verdict"] != "PASS", "record_ids": record_ids,
           "workspace": str(ws)}
    return res


def run_tool_schema(w: Worker) -> dict:
    spec = json.loads((SCEN / "s12_tool_schema" / "cases.json").read_text(encoding="utf-8"))
    rows = []
    for case in spec["cases"]:
        msgs = [{"role": "system", "content": spec["context"] + " Use exactly one tool call with exact typed arguments."},
                {"role": "user", "content": case["prompt"]}]
        o = w.chat(msgs, tools=spec["tools"], task_class="scenario:s12_tool_schema", max_tokens=8000)
        ok, why = False, ""
        calls = o["tool_calls"]
        if o["error"]:
            why = o["error"][:200]
        elif len(calls) != 1:
            why = f"{len(calls)} tool calls"
        else:
            name = calls[0]["function"]["name"]
            try:
                args = json.loads(calls[0]["function"]["arguments"] or "{}")
            except ValueError:
                args = None
            exp = case["expect"]
            if name != exp["name"] or not isinstance(args, dict):
                why = f"wrong tool {name}"
            elif exp.get("args_check") == "glob_pdf_invoice":
                g = str(args.get("glob", "")).lower()
                ok = ("invoice" in g and g.endswith(".pdf") and args.get("max_results") == 20
                      and type(args.get("max_results")) is int)
                why = json.dumps(args, ensure_ascii=False)
            else:
                want = exp["args"]
                bad = {k: args.get(k) for k in want if args.get(k) != want[k] or type(args.get(k)) is not type(want[k])}
                extra = set(args) - set(want)
                ok = not bad and not extra
                why = json.dumps({"bad": bad, "extra": sorted(extra)}, ensure_ascii=False)
        rows.append({"case": case["id"], "pass": ok, "why": why, "record_id": o.get("record_id")})
    return {"scenario": "s12_tool_schema", "model": w.model, "cases": rows,
            "verdict": "PASS" if all(r["pass"] for r in rows) else "FAIL",
            "passed": sum(r["pass"] for r in rows), "total": len(rows)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--extra-body", default="{}")
    ns = ap.parse_args(argv)
    out = pathlib.Path(ns.out)
    out.mkdir(parents=True, exist_ok=True)
    rec = Recorder(f"scenarios-{ns.tag}")
    w = Worker(ns.base, ns.model, recorder=rec, extra_body=json.loads(ns.extra_body))
    results = []
    for name in (ns.only or REPO_SCENARIOS + ["s12_tool_schema"]):
        r = run_tool_schema(w) if name == "s12_tool_schema" else run_repo_scenario(w, SCEN / name, "")
        results.append(r)
        # verdict back into the dataset: one curator record per scenario outcome
        rec.record(model=ns.model, task_class=f"verdict:{name}", messages=[], response_text="",
                   verdict=r["verdict"], verifier="hidden tests (scenario_runner)",
                   curator_note=json.dumps({k: r.get(k) for k in ("claim", "checks", "false_done", "steps",
                                                                   "tool_errors", "passed", "total")},
                                           ensure_ascii=False),
                   extra={"record_ids": r.get("record_ids") or [c.get("record_id") for c in r.get("cases", [])]})
        print(f"{ns.tag} {name}: {r['verdict']} claim={r.get('claim')} steps={r.get('steps')} "
              f"checks={r.get('checks') or r.get('passed')}", flush=True)
    (out / f"scenarios-{ns.tag}.json").write_text(json.dumps({"tag": ns.tag, "model": ns.model, "results": results,
                                                               "retry_events": w.events, "records": rec.count},
                                                              ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
