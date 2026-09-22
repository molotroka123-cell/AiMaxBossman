"""Local model bake-off A-G on the owner machine (auto-graded, same prompts for every model).

python bakeoff.py --url http://127.0.0.1:8081/v1 --tag main-q5 --out results/
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

BUGGY = '''def moving_sum(xs, k):
    """Sum of every window of k consecutive items."""
    out = []
    for i in range(len(xs) - k):
        out.append(sum(xs[i:i + k]))
    return out
'''

TREE = """command-center/bcc/engine.py            task engine: claims runs, calls models, tool loop
command-center/bcc/registry.py          providers/models registry, model test probe
command-center/bcc/health.py            health snapshot of models/providers
command-center/bcc/studio/runtime.py    Studio model listing and generation planes
command-center/bcc/telegram_companion/service.py   Telegram bot dispatch
command-center/bcc/telegram_companion/agent_bridge.py  /claude and /codex from Telegram
command-center/bcc/v2/memory/lifecycle_wiring.py   memory recall at task start
bossman-core/bossman/toolkit/files.py   fs.write / fs.edit for agents
tools/windows_bundle_lock.py            Windows build input lock
"""

TOOLS = [
    {"type": "function", "function": {"name": "read_file", "description": "Read a text file from the repo",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "run_tests", "description": "Run pytest on a path",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "k": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "search_code", "description": "Search the repository for a regex",
     "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]}}},
]


def code_block(text):
    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", text or "", flags=re.S)
    return max(blocks, key=len) if blocks else (text or "")


def run_py(src, timeout=30):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "t.py"
        p.write_text(src, encoding="utf-8")
        r = subprocess.run([sys.executable, str(p)], capture_output=True, text=True, timeout=timeout, cwd=d)
        return r.returncode, (r.stdout + r.stderr)[-400:]


def task_a(chat):
    ans = chat([{"role": "user", "content": "Find the bug and return ONLY the corrected function in one python code block.\n\n```python\n" + BUGGY + "```"}])
    code = code_block(ans["text"])
    test = code + "\nassert moving_sum([1,2,3,4], 2) == [3,5,7]\nassert moving_sum([5], 1) == [5]\nassert moving_sum([], 3) == []\nprint('ok')\n"
    rc, out = run_py(test)
    return rc == 0, out


def task_b(chat):
    ans = chat([{"role": "user", "content": "Repository map:\n" + TREE + "\nWhich single file should be changed so that a model that "
                 "thinks before answering is not marked silent by the model test? Answer with the path only."}])
    return "bcc/registry.py" in (ans["text"] or ""), ans["text"][:120]


def task_c(chat):
    ans = chat([{"role": "user", "content": "The function below is buggy. Return ONE python code block containing: the fixed "
                 "function AND a function `test_regression()` that fails on the buggy version and passes on the fixed one "
                 "(plain asserts, no pytest import).\n\n```python\n" + BUGGY + "```"}])
    code = code_block(ans["text"])
    if "def test_regression" not in code:
        return False, "no test_regression"
    rc_fixed, out1 = run_py(code + "\ntest_regression()\nprint('ok')\n")
    tests_only = re.sub(r"def moving_sum\(.*?\n(?=def |\Z)", "", code, flags=re.S)
    rc_buggy, out2 = run_py(BUGGY + "\n" + tests_only + "\ntest_regression()\n")
    return rc_fixed == 0 and rc_buggy != 0, f"fixed_rc={rc_fixed} buggy_rc={rc_buggy} {out1[-120:]}"


def task_d(chat):
    ans = chat([{"role": "user", "content": "Run only the tests in command-center/tests/test_health_model_components.py."}], tools=TOOLS)
    calls = ans["tool_calls"]
    ok = bool(calls) and calls[0]["function"]["name"] == "run_tests" and \
        "test_health_model_components" in calls[0]["function"].get("arguments", "")
    return ok, json.dumps(calls)[:200]


def task_e(chat):
    ans = chat([{"role": "user", "content": "Return ONLY a JSON object (no prose) with keys: file (string), line (integer), "
                 "severity (one of low|medium|high), fix (string), for this finding: moving_sum in utils.py line 4 skips "
                 "the last window; high severity; fix the range bound."}])
    txt = (ans["text"] or "").strip()
    txt = re.sub(r"^```(?:json)?|```$", "", txt, flags=re.M).strip()
    try:
        d = json.loads(txt)
        ok = (set(d) == {"file", "line", "severity", "fix"} and isinstance(d["line"], int)
              and d["severity"] in ("low", "medium", "high"))
        return ok, txt[:160]
    except Exception as e:  # noqa: BLE001
        return False, f"invalid json: {txt[:120]}"


def task_f(chat):
    msgs = [{"role": "user", "content": "Read the file command-center/bcc/health.py."},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function",
             "function": {"name": "read_file", "arguments": json.dumps({"path": "bcc/health.py"})}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "ERROR: file not found: bcc/health.py (paths are relative to the repository root)"}]
    ans = chat(msgs, tools=TOOLS)
    calls = ans["tool_calls"]
    ok = bool(calls) and calls[0]["function"]["name"] == "read_file" and \
        "command-center/bcc/health.py" in calls[0]["function"].get("arguments", "")
    return ok, json.dumps(calls)[:200] or ans["text"][:120]


def task_g(chat):
    filler = []
    for i in range(700):
        filler.append(f"Record {i}: service node-{i % 37} reported status nominal at tick {i * 7}.")
    filler.insert(430, "Record 430: the rollback password for the lab build is AMBER-4471.")
    ans = chat([{"role": "user", "content": "\n".join(filler) + "\n\nWhat is the rollback password for the lab build? Answer with the password only."}])
    return "AMBER-4471" in (ans["text"] or ""), (ans["text"] or "")[:80]


TASKS = {"A_bug": task_a, "B_repo_nav": task_b, "C_patch_regression": task_c, "D_tool_select": task_d,
         "E_json": task_e, "F_tool_recovery": task_f, "G_context": task_g}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    client = httpx.Client(timeout=900)
    model = client.get(a.url + "/models").json()["data"][0]["id"]
    stats = []

    def chat(messages, tools=None):
        body = {"model": model, "messages": messages, "max_tokens": 3000, "temperature": 0.2}
        if tools:
            body["tools"] = tools
        t = time.time()
        r = client.post(a.url + "/chat/completions", json=body).json()
        m = r["choices"][0]["message"]
        tm = r.get("timings") or {}
        stats.append({"s": round(time.time() - t, 1), "gen_tps": tm.get("predicted_per_second"),
                      "pp_tps": tm.get("prompt_per_second"), "out": (r.get("usage") or {}).get("completion_tokens")})
        return {"text": m.get("content") or "", "tool_calls": m.get("tool_calls") or []}

    results = {}
    for name, fn in TASKS.items():
        n = len(stats)
        try:
            ok, note = fn(chat)
        except Exception as e:  # noqa: BLE001
            ok, note = False, f"harness/model error: {type(e).__name__}: {str(e)[:120]}"
        results[name] = {"pass": bool(ok), "note": note, "calls": stats[n:]}
        print(f"{a.tag} {name}: {'PASS' if ok else 'FAIL'} {[c['s'] for c in stats[n:]]}", flush=True)
    tps = [c["gen_tps"] for c in stats if c["gen_tps"]]
    summary = {"tag": a.tag, "model": model, "passed": sum(r["pass"] for r in results.values()), "total": len(TASKS),
               "seconds": round(sum(c["s"] for c in stats), 1),
               "gen_tps_median": sorted(tps)[len(tps) // 2] if tps else None, "results": results}
    Path(a.out).mkdir(parents=True, exist_ok=True)
    (Path(a.out) / f"{a.tag}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("tag", "passed", "total", "seconds", "gen_tps_median")}))


if __name__ == "__main__":
    main()
