"""Lane opsplug: LIVE receipts for the four plugin capabilities that got real handlers
(plugin-5 mcp.tool_list, plugin-7 ollama.chat, plugin-8 openrouter.chat, plugin-9 github.repo_read).

For each leaf: (1) a direct live call through bcc.tools.execute_tool in a clean subprocess
(this worktree first on PYTHONPATH, __file__ asserted, temp data dirs, all KEY/TOKEN env stripped);
(2) the authored_by_lane live test in command-center/tests/test_leaf_plugins_real_handlers.py must be
PASSED (not skipped).  Only openrouter.chat reads the owner key, through the existing cloud-worker
key-file lookup (coding_tasks._worker_key); the key is never printed (output is scrubbed).
Re-run: python tools/tree_proof/opsplug_plugins_probe.py [--only plugin-7]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVID = ROOT / "docs/architecture/bossman-tree-20261005/evidence"
OUT = EVID / "out"
PY = sys.executable
TEST = "command-center/tests/test_leaf_plugins_real_handlers.py"
SECRET_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PROXY|CREDENTIAL)", re.I)
SCRUB = re.compile(r"(sk-[A-Za-z0-9_-]{10,}|ghp_[A-Za-z0-9]{10,}|(?i:bearer)\s+[A-Za-z0-9._-]{10,})")

SNIP = r'''
import asyncio, sys, json
import bcc.features.plugins as P
from bcc.tools import REGISTRY, ToolContext, execute_tool
assert P.__file__.replace(chr(92), "/").lower().startswith(%(root)r.lower()), P.__file__
async def main():
    await P.setup(None)
    c = ToolContext(svc=None, task={}, run_id=1, agent={}, workspace="", call_id="probe")
    for name, args in %(calls)s:
        r = await execute_tool(REGISTRY.get(name), args, c)
        print("CALL", name, "error=%%s" %% r.error, "| one_line:", r.one_line)
        print("CONTENT:", str(r.content)[:400].replace("\n", "\\n"))
        print("DATA:", json.dumps(r.data, default=str)[:300])
asyncio.run(main())
'''

PLAN = {
    "plugin-5": dict(tool="mcp.tool_list", calls=None, k="mcp_tool_list_live_real_server or mcp_tool_list_refuses or tool_list_needs",
                     live="test_mcp_tool_list_live_real_server", real_key=False),
    "plugin-7": dict(tool="ollama.chat",
                     calls=[("plugin:ollama.chat", {"model": "ace-decider-08b:latest", "max_tokens": 48,
                                                    "messages": [{"role": "user", "content": "Reply with the single word: pong"}]}),
                            ("plugin:ollama.chat", {"model": "x-cloud", "messages": [{"role": "user", "content": "hi"}]})],
                     k="ollama or four_leaves or policy", live="test_ollama_chat_live_local_model", real_key=False),
    "plugin-8": dict(tool="openrouter.chat",
                     calls=[("plugin:openrouter.chat", {"model": "nvidia/nemotron-3-super-120b-a12b:free", "max_tokens": 400,
                                                        "messages": [{"role": "user", "content": "Reply with the single word: pong"}]}),
                            ("plugin:openrouter.chat", {"model": "z-ai/glm-5.3-flash",
                                                        "messages": [{"role": "user", "content": "hi"}]})],
                     k="openrouter or four_leaves or policy", live="test_openrouter_chat_live_free_model", real_key=True),
    "plugin-9": dict(tool="github.repo_read",
                     calls=[("plugin:github.repo_read", {"repo": "octocat/Hello-World"}),
                            ("plugin:github.repo_read", {"repo": "octocat/Hello-World", "path": "README"}),
                            ("plugin:github.repo_read", {"repo": "a/b", "path": "../x"})],
                     k="github or four_leaves or policy", live="test_github_repo_read_live_public_repo", real_key=False),
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def env_for(tmp: str, real_localappdata: bool) -> dict:
    env = {k: v for k, v in os.environ.items() if not SECRET_RE.search(k)}
    pp = os.pathsep.join(str(ROOT / d) for d in ("command-center", "bossman-core", "."))
    env.update(PYTHONPATH=pp, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", BCC_DATA_DIR=tmp, BOSSMAN_DATA_DIR=tmp,
               PYTHONDONTWRITEBYTECODE="1", NO_PROXY="127.0.0.1,localhost")
    if not real_localappdata:
        env.update(LOCALAPPDATA=tmp, APPDATA=tmp)
    return env


def run(cmd, cwd, env, cap=110):
    t = time.time()
    try:
        p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, timeout=cap)
        return p.returncode, p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace"), round(time.time() - t, 1)
    except subprocess.TimeoutExpired as e:
        return 124, (e.stdout or b"").decode("utf-8", "replace") + "\n[TIMEOUT]\n", cap


def probe(node, plan, sha):
    started = now()
    tmp = tempfile.mkdtemp(prefix="opsplug_pl_")
    cwd = ROOT / "command-center"
    log, cmds, ok, why = [], [], True, ""
    if plan["calls"]:
        code = SNIP % {"root": str(ROOT).replace("\\", "/"), "calls": repr(plan["calls"])}
        shown = f"python -c <execute_tool plugin:{plan['tool']} live call> [cwd=command-center]"
        cmds.append(shown)
        env = env_for(tmp, plan["real_key"])
        rc, out, dt = run([PY, "-c", code], str(cwd), env)
        log.append(f"$ {shown}\n{SCRUB.sub('[REDACTED]', out)}\n[exit {rc} in {dt}s]\n")
        first = re.search(r"CALL \S+ error=(\w+)", out)
        if rc != 0 or not first or first.group(1) != "False":
            ok, why = False, "live_call_failed"
    shown = f"(cd command-center && python -m pytest -v -rA -p no:cacheprovider --timeout=100 tests/test_leaf_plugins_real_handlers.py -k \"{plan['k']}\")"
    cmds.append(shown)
    env = env_for(tmp, plan["real_key"])
    if plan["real_key"]:
        env["BOSSMAN_LIVE_OPENROUTER"] = "1"
    rc, out, dt = run([PY, "-m", "pytest", "-v", "-rA", "-p", "no:cacheprovider", "--timeout=100",
                       "tests/test_leaf_plugins_real_handlers.py", "-k", plan["k"]], str(cwd), env)
    log.append(f"$ {shown}\n{SCRUB.sub('[REDACTED]', out)}\n[exit {rc} in {dt}s]\n")
    passed_live = re.search(r"PASSED .*::" + re.escape(plan["live"]), out) is not None
    if rc != 0 or not passed_live:
        ok, why = False, why or ("live_test_not_passed" if rc == 0 else "tests_failed")
    text = (f"node_id: {node}\ncapability: plugin:{plan['tool']}\nsha: {sha}\n\n" + "\n".join(log)
            + f"\nVERDICT: {'PASS' if ok else 'FAIL'} {why}\n")
    data = text.encode("utf-8")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{node}.txt").write_bytes(data)
    return {"node_id": node, "sha": sha, "probe": "live_call+pytest authored_by_lane", "command": " && ".join(cmds),
            "exit_code": 0 if ok else 1, "started_at": started, "finished_at": now(),
            "output_sha256": hashlib.sha256(data).hexdigest(), "output_tail": text[-1500:],
            "verdict": "PASS" if ok else "FAIL", "kind": "live_call", "reason": why,
            "model": plan["calls"][0][1]["model"] if plan["calls"] and "model" in plan["calls"][0][1] else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=",".join(PLAN))
    a = ap.parse_args()
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    rec = [probe(n, PLAN[n], sha) for n in a.only.split(",")]
    full = EVID / "opsplug.json"
    old = json.loads(full.read_text(encoding="utf-8")) if full.exists() else []
    ids = {r["node_id"] for r in rec}
    full.write_text(json.dumps([r for r in old if r["node_id"] not in ids] + rec, ensure_ascii=False, indent=1), encoding="utf-8")
    for r in rec:
        print(r["node_id"], r["verdict"], r["reason"])


if __name__ == "__main__":
    main()
