from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import psutil

ROOT = Path(__file__).parent
BASE = "http://127.0.0.1:18810"
TERMINAL = {"completed", "failed", "stopped", "blocked"}


def now():
    return datetime.now(timezone.utc).isoformat()


def clean_text(value):
    value = re.sub(r"(?<!\d)\d{6,16}:[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])", "[REDACTED]", value or "")
    value = re.sub(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*\S+", r"\1=[REDACTED]", value)
    return value


def api(method, url, headers, body=None, timeout=20):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = Request(url, data=data, headers=headers, method=method)
    with urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=["qwen36-q5", "qwen38-q5"], required=True)
    ap.add_argument("--port", type=int, choices=[18811, 18814], required=True)
    ap.add_argument("--rounds", type=int, default=2, choices=[2])
    ap.add_argument("--max-task-seconds", type=int, default=1200)
    args = ap.parse_args()
    token = (ROOT.parent.parent / "evidence/tournament-20261004/token").read_text(encoding="utf-8").strip()
    if not token:
        raise RuntimeError("missing API token")
    hdr = {"X-BCC-Token": token, "Content-Type": "application/json"}
    tasks = json.loads((ROOT / "final_tasks.json").read_text(encoding="utf-8"))["tasks"]
    code_agent, browser_agent = ((7, 8) if args.model == "qwen36-q5" else (1, 2))
    server_pid = None
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if proc.info["name"] and "llama-server" in proc.info["name"].lower() and str(args.port) in (proc.info["cmdline"] or []):
                server_pid = proc.info["pid"]
                break
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    if not server_pid:
        # Some Windows command lines quote the port as a combined token.
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if proc.info["name"] and "llama-server" in proc.info["name"].lower() and args.port in [int(x) for x in (proc.info["cmdline"] or []) if x.isdigit()]:
                    server_pid = proc.info["pid"]
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
                pass
    if not server_pid:
        raise RuntimeError("candidate llama-server process not found for selected port")
    server = psutil.Process(server_pid)
    samples = {"min_available_gib": None, "peak_server_rss_gib": 0.0}
    output = {"schema": "bossman.model-market-final-run/1", "model": args.model,
              "api": BASE, "agent_ids": {"coding": code_agent, "browser": browser_agent},
              "server_port": args.port, "server_pid": server_pid, "started_at": now(),
              "runner_sha": None, "attempts": [], "resource_sample": samples}
    for round_no in range(1, args.rounds + 1):
        for task in tasks:
            agent = code_agent if task["category"] == "coding" else browser_agent
            title = f"FINAL-{args.model}-{round_no}-{task['id']}"
            created_at = now()
            body = {"title": title, "prompt": task["prompt"], "agent_id": agent,
                    "run_now": True, "max_retries": 0, "priority": 1}
            submitted = api("POST", f"{BASE}/api/tasks", hdr, body)
            task_id = submitted.get("task", {}).get("id")
            if not isinstance(task_id, int):
                raise RuntimeError("Bossman did not return a task id")
            start = time.monotonic()
            wall_max = 0
            status = "unknown"
            detail = None
            while time.monotonic() - start < args.max_task_seconds:
                detail = api("GET", f"{BASE}/api/tasks/{task_id}", hdr)
                status = detail.get("task", {}).get("status", "unknown")
                try:
                    vm = psutil.virtual_memory()
                    gib = vm.available / 1024**3
                    samples["min_available_gib"] = gib if samples["min_available_gib"] is None else min(samples["min_available_gib"], gib)
                    if server.is_running():
                        wall_max = max(wall_max, server.memory_info().rss / 1024**3)
                        samples["peak_server_rss_gib"] = max(samples["peak_server_rss_gib"], wall_max)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
                if status in TERMINAL:
                    break
                time.sleep(2)
            if status not in TERMINAL:
                api("POST", f"{BASE}/api/tasks/{task_id}/stop", hdr, {})
                status = "timeout_stopped"
            runs = detail.get("runs") or []
            run = runs[-1] if runs else {}
            record = {"round": round_no, "item_id": task["id"], "category": task["category"],
                      "title": title, "task_id": task_id, "agent_id": agent,
                      "prompt_sha256": hashlib.sha256(task["prompt"].encode()).hexdigest(),
                      "expected": task.get("expected"), "created_at": created_at,
                      "finished_at": now(), "status": status,
                      "wall_seconds": round(time.monotonic() - start, 3),
                      "tokens_in": run.get("tokens_in"), "tokens_out": run.get("tokens_out"),
                      "model_alias": run.get("model_alias"), "route": run.get("route"),
                      "checkpoint": run.get("checkpoint"), "provenance": run.get("provenance"),
                      "response": clean_text(run.get("result") or ""),
                      "error": clean_text(str(run.get("error") or detail.get("error") or ""))}
            output["attempts"].append(record)
            print(json.dumps({"model": args.model, "round": round_no, "item": task["id"],
                              "task_id": task_id, "status": status,
                              "wall_seconds": record["wall_seconds"],
                              "tokens_out": record["tokens_out"]}), flush=True)
            path = ROOT / f"final-{args.model}.json"
            path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            if status != "completed":
                # Keep collecting the remaining rows for evidence; terminal failures are not hidden.
                pass
    output["finished_at"] = now()
    (ROOT / f"final-{args.model}.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"done": args.model, "attempts": len(output["attempts"]),
                      "peak_server_rss_gib": round(samples["peak_server_rss_gib"], 2),
                      "min_available_gib": round(samples["min_available_gib"] or 0, 2)}), flush=True)


if __name__ == "__main__":
    main()
