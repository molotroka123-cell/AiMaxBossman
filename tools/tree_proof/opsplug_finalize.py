"""Lane opsplug: RETIRE receipts + the classification table (every leaf of the lane exactly one verdict).

    python tools/tree_proof/opsplug_finalize.py

Reads the seed (HEAD), evidence/opsplug.json (GREEN/FAIL receipts written by the probes) and runs the
deterministic retire audit (tools/tree_proof/opsplug_retire.py) for the RETIRE candidates, then writes
  evidence/opsplug-retire.json
  evidence/out/<retire id>.txt
  docs/architecture/bossman-tree-20261005/evidence/opsplug-classification.md
Verdict rule: GREEN = a PASS receipt exists for the node; RETIRE = an accepted audit receipt; everything else
is KEEP_BLUE with a one-line honest reason (never silently dropped).
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "command-center/bcc/capability_tree_seed.json"
EVID = ROOT / "docs/architecture/bossman-tree-20261005/evidence"
OUT = EVID / "out"
PY = sys.executable

RETIRE = {  # node -> (path, word, reason)
    "mod-v15_owner": ("command-center/bcc/features/v15_owner.py", "v15_owner",
                      "Дубликат: файл удалён из истории HEAD коммитом 8a2fbaad 'remove duplicate owner control backend'; "
                      "живой аналог v15_owner_run.py; на слово v15_owner нет ни одной ссылки в коде, CI и конфиге."),
}

TOP = {"plugin-7", "plugin-8", "plugins-security", "module-9958189ad4d4", "module-0ef2333ef23c", "module-6e615a668c7b",
       "module-d3f5977af5ba", "module-abd99037a01e", "mod-jeff_insights", "module-4780a1d5fdf7", "module-6c727703c8ea",
       "module-e11418351fd7"}
LOW = {"module-ae9598734759", "module-73976a1cf54d", "reg-leaf_usefulness_queue", "reg-tree_registry_sync",
       "reg-blue_leaf_audit_tool", "mod-agentmap", "module-575dcc5a99f5", "plugins-oss"}

KEEP_REASON = {
    "mod-autonomy": "FAIL: test_autonomy_bounded_loop::test_tree_runner_kills_the_whole_worker_tree_when_stop_appears "
                    "(TreeRunner.run returns before the killed worker pid is gone, ~50 ms race; product bug or flaky test, owner decision)",
    "mod-osiris": "FAIL: test_web_research_net::test_szhatyy_otvet_otvergaetsya_transportom (gzip guard no longer fires: "
                  "safe_get strips Content-Encoding since 20e2c879) - real regression, not fixed here by rule",
    "plugin-6": "outward/writing: mcp.tool_call runs a foreign tool (ASK, destructive); effect not exercised, needs owner approval flow",
    "plugin-10": "outward/writing: github.issue_create posts to GitHub (ASK); needs owner token + approved effect",
    "plugin-11": "needs owner: Gmail OAuth (GMAIL_OAUTH) not available in this environment",
    "plugin-12": "outward/writing: gmail.send sends mail (ASK, destructive); needs owner OAuth + approved effect",
    "plugin-13": "needs owner: Google OAuth (GOOGLE_OAUTH) not available in this environment",
    "plugin-14": "outward/writing: calendar.create writes to the owner's calendar (ASK); needs OAuth + approved effect",
    "plugin-15": "needs owner: Google OAuth (GOOGLE_OAUTH) not available in this environment",
    "plugin-16": "outward/writing: drive.write writes files to Drive (ASK); needs OAuth + approved effect",
    "plugin-17": "needs owner: Telegram bot token (TELEGRAM_BOT_TOKEN); handler is still the NOT_TESTED_LIVE stub",
    "plugin-18": "outward/writing: telegram.send messages people (ASK, destructive); needs approved effect",
    "plugin-19": "needs env: a configured n8n instance + N8N_API_KEY; handler is still the NOT_TESTED_LIVE stub",
    "plugin-20": "outward/writing: n8n.workflow_run executes workflows (ASK, destructive); needs n8n + approved effect",
    "plugin-21": "needs real work: browser.open handler not implemented (stub); must be wired to the existing browser subsystem",
    "plugin-22": "outward/writing: browser.form_submit submits forms (ASK, destructive); needs approved effect",
}


def sh(cmd):
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")


def main() -> int:
    sha = sh(["git", "rev-parse", "HEAD"]).stdout.strip()
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    nodes = {n["id"]: n for n in seed["nodes"]}
    parents = {n["parent"] for n in seed["nodes"] if n.get("parent")}

    def zone(n):
        chain, cur = [], n
        while cur.get("parent") in nodes:
            chain.append(cur["parent"])
            cur = nodes[cur["parent"]]
        return chain[-2] if len(chain) > 1 else None

    # the lane = every leaf of ops/plugins still blue at the lane's starting commit (679d019e)
    base_seed = json.loads(sh(["git", "show", "679d019e:command-center/bcc/capability_tree_seed.json"]).stdout)
    bnodes = {n["id"]: n for n in base_seed["nodes"]}
    bparents = {n["parent"] for n in base_seed["nodes"] if n.get("parent")}

    def bzone(n):
        chain, cur = [], n
        while cur.get("parent") in bnodes:
            chain.append(cur["parent"])
            cur = bnodes[cur["parent"]]
        return chain[-2] if len(chain) > 1 else None

    lane = [n for n in base_seed["nodes"] if n["status"] in ("code", "branch") and bzone(n) in ("ops", "plugins")
            and n["id"] not in bparents]
    receipts = json.loads((EVID / "opsplug.json").read_text(encoding="utf-8")) if (EVID / "opsplug.json").exists() else []
    green = {r["node_id"]: r for r in receipts if r["verdict"] == "PASS"}
    fail = {r["node_id"]: r for r in receipts if r["verdict"] == "FAIL"}

    retire_receipts = []
    for nid, (path, word, reason) in RETIRE.items():
        started = datetime.now(timezone.utc).isoformat(timespec="seconds")
        cmd = f"python tools/tree_proof/opsplug_retire.py {nid} {path} {word}"
        p = sh([PY, "tools/tree_proof/opsplug_retire.py", nid, path, word])
        out = p.stdout + p.stderr
        (OUT / f"{nid}.txt").write_bytes(out.encode("utf-8"))
        if p.returncode == 0:
            retire_receipts.append({"node_id": nid, "sha": sha, "probe": "audit: duplicate/dead (git history + git grep)",
                                    "command": cmd, "exit_code": 0, "started_at": started,
                                    "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                    "output_sha256": hashlib.sha256(out.encode("utf-8")).hexdigest(),
                                    "output_tail": out[-1500:], "verdict": "RETIRE", "kind": "audit", "reason": reason})
        else:
            print("RETIRE REFUSED", nid, out[-300:])
    (EVID / "opsplug-retire.json").write_text(json.dumps(retire_receipts, ensure_ascii=False, indent=1), encoding="utf-8")
    retired = {r["node_id"] for r in retire_receipts}

    rows, counts = [], {"GREEN": 0, "RETIRE": 0, "KEEP": 0}
    for n in sorted(lane, key=lambda x: (bzone(x), x["id"])):
        nid = n["id"]
        src = n["sources"][0]
        srcp = src["path"] if isinstance(src, dict) else str(src)
        if nid in green:
            r = green[nid]
            verdict, why = "GREEN", f"{r['kind']} receipt @ {r['sha'][:8]}: {r['probe']}"
            m = [ln for ln in r["output_tail"].splitlines() if " passed" in ln]
            if m:
                why += f" ({m[-1].strip()})"
            value = "TOP" if nid in TOP else ("LOW" if nid in LOW else "OK")
        elif nid in retired:
            verdict, why, value = "RETIRE", RETIRE[nid][2], "LOW"
        else:
            verdict, value = "KEEP", "OK"
            if nid in KEEP_REASON:
                why = KEEP_REASON[nid]
            elif n["status"] == "branch" and not (ROOT / srcp).exists():
                b = src.get("branch", "?") if isinstance(src, dict) else "?"
                why = f"code only on branch {b}; absent from this checkout, cannot be run; needs a merge decision"
            elif nid in fail:
                why = f"FAIL {fail[nid].get('reason', '')}: see evidence/out/{nid}.txt"
            else:
                why = "not proven in this lane"
            if nid in ("mod-twitter",):
                why = "needs owner (Twitter API credentials) and the code lives only on branch feature/osiris-data-acquisition"
        counts[verdict] += 1
        rows.append((nid, n["label"], verdict, why.replace("|", "/").replace("\n", " "), value))

    top_n = sum(1 for r in rows if r[4] == "TOP")
    md = ["# Lane opsplug - classification (ops + plugins leaves still blue at 679d019e)", "",
          f"Total leaves: {len(rows)}; GREEN {counts['GREEN']}, RETIRE {counts['RETIRE']}, KEEP {counts['KEEP']}; "
          f"TOP {top_n} ({100 * top_n // max(1, len(rows))}%).", "",
          "Receipts: evidence/opsplug.json (GREEN and FAIL), evidence/opsplug-retire.json (RETIRE); outputs in evidence/out/<id>.txt. "
          "Re-run: tools/tree_proof/opsplug_probe.py, opsplug_plugins_probe.py, opsplug_finalize.py.", "",
          "| id | label | verdict | reason | value |", "|---|---|---|---|---|"]
    md += [f"| {a} | {b} | {c} | {d} | {e} |" for a, b, c, d, e in rows]
    (EVID / "opsplug-classification.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(counts, "TOP", top_n, "of", len(rows))
    print("TOP ids:", sorted(r[0] for r in rows if r[4] == "TOP"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
