#!/usr/bin/env python3
"""Run and collect 5 parallel verification tasks across free cloud models via Bossman API (:8801)."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

def main():
    token_path = Path(r"C:\Users\asd\AppData\Local\Bossman\CommandCenter\token")
    if not token_path.exists():
        print(f"Error: token not found at {token_path}")
        sys.exit(1)

    token = token_path.read_text(encoding="utf-8").strip()

    def get(path: str, retries: int = 5) -> dict:
        req = urllib.request.Request(f"http://127.0.0.1:8801{path}")
        req.add_header("X-BCC-Token", token)
        for i in range(retries):
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.loads(r.read().decode("utf-8"))
            except Exception as e:
                if i == retries - 1:
                    raise
                time.sleep(2)

    # The 5 tasks we launched
    target_tasks = [
        {"task_id": 725, "agent_id": 6, "agent_label": "NVIDIA Nemotron-3 Ultra 550B:free (OpenRouter)", "title": "Cloud Horde #1 · Верификация Веток 1-15 Архитектурного Дерева"},
        {"task_id": 726, "agent_id": 44, "agent_label": "Z.AI GLM 5.3 Flash (Cloud)", "title": "Cloud Horde #2 · Верификация Art Studio v2.0 (Three.js WebGL2)"},
        {"task_id": 727, "agent_id": 47, "agent_label": "Moonshot Kimi K3.1 Pro (7API)", "title": "Cloud Horde #3 · Верификация Security, Sandbox & Secrets Hygiene"},
        {"task_id": 728, "agent_id": 45, "agent_label": "Qwen 3.8 Flash (Cloud)", "title": "Cloud Horde #4 · Верификация Mobile Pult & Swarm DAG Orchestration"},
        {"task_id": 729, "agent_id": 25, "agent_label": "NVIDIA Nemotron-Ultra NIM 550B (NVIDIA NIM)", "title": "Cloud Horde #5 · Final Freeze Check & Реестр Дерева Возможностей"},
    ]

    print("Waiting for all 5 tasks to finish...")
    results = []
    for item in target_tasks:
        tid = item["task_id"]
        status = "unknown"
        for attempt in range(90):
            try:
                res = get(f"/api/tasks/{tid}")
                status = res["task"]["status"]
                if status in ("completed", "failed", "blocked"):
                    runs = res.get("runs", [])
                    last_run = runs[-1] if runs else {}
                    cost = float(last_run.get("cost_usd") or 0.0)
                    item["run_id"] = last_run.get("id")
                    item["status"] = status
                    item["cost_usd"] = cost
                    item["result"] = res.get("result") or ""
                    item["error"] = res.get("error") or ""
                    item["model"] = last_run.get("model_alias") or ""
                    item["finished_at"] = last_run.get("finished_at") or ""
                    print(f"Task {tid} done: status={status}, cost=${cost:.6f}, answer_len={len(item['result'])}")
                    break
            except Exception as e:
                print(f"Task {tid} poll warning: {e}")
            time.sleep(3)
        if item.get("status") is None:
            item["status"] = status
            item["cost_usd"] = 0.0
            item["result"] = ""
            item["error"] = "timeout"
        results.append(item)

    out_dir = Path("docs/audits/2026-10-28-cloud-horde-execution")
    out_dir.mkdir(parents=True, exist_ok=True)
    receipt = {
        "schema": "bossman-cloud-horde-receipt-v1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "execution_type": "live_cloud_horde_verification",
        "total_tasks": len(results),
        "successful_tasks": sum(1 for r in results if r.get("status") == "completed"),
        "total_cost_usd": sum(r.get("cost_usd", 0.0) for r in results),
        "tasks": results
    }

    receipt_path = out_dir / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved receipt to {receipt_path}")
    print(f"Successful tasks: {receipt['successful_tasks']}/{receipt['total_tasks']}, Total cost: ${receipt['total_cost_usd']:.6f}")

if __name__ == "__main__":
    main()
