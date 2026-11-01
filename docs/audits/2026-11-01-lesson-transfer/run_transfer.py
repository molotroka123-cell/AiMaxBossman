"""Lesson-transfer experiment driver: talks ONLY to a temp Bossman backend through its public HTTP API.

    python run_transfer.py <cmd> --url http://127.0.0.1:8897 --data-dir <temp data dir> --work <work dir> [...]

cmd:
  setup            provider (sampling proxy) + model + agent from prereg.json -> <work>/setup.json
  smoke            one non-experimental prompt through the task path (pipeline check only)
  phase NAME       every preregistered task x reps through POST /api/tasks (run_now) -> <work>/raw_NAME.jsonl
                   (resumable: (task, rep) pairs already recorded are skipped)
  lesson           POST /api/memory/lessons + provenance/negative checks -> <work>/lesson.json
  survive          after a backend restart: the lesson, its limits and status are unchanged -> <work>/survive.json
  exercise         the desk exercise through the task path, the model's answer -> /verify -> <work>/exercise.json

Refuses ports 8800/8801 (owner backends). The model never sees ground truth: truth lives outside the repo and the
data dir and is read only by scorer.py after the runs.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

HERE = Path(__file__).resolve().parent
PREREG = json.loads((HERE / "prereg.json").read_text(encoding="utf-8"))
TERMINAL = {"completed", "failed", "stopped", "blocked", "waiting_approval", "cancelled"}


def client(url: str, data_dir: Path) -> httpx.Client:
    if urlparse(url).port in (8800, 8801, None):
        raise SystemExit("refusing: owner ports 8800/8801; use the temp backend")
    tok = (data_dir / "token").read_text(encoding="utf-8").strip()
    return httpx.Client(base_url=url, headers={"X-BCC-Token": tok}, timeout=60, trust_env=False)


def ok(r: httpx.Response) -> dict:
    if r.status_code >= 400:
        raise RuntimeError(f"{r.request.method} {r.request.url.path} -> {r.status_code}: {r.text[:400]}")
    return r.json()


def setup(c: httpx.Client, work: Path) -> dict:
    m = PREREG["model"]
    prov = ok(c.post("/api/providers", json={"name": "local-ollama-sampling-proxy", "kind": "openai_compat",
                                              "base_url": m["proxy_base_url"], "api_key": "ollama-local"}))
    model = ok(c.post("/api/models", json={"provider_id": prov["id"], "name": m["ollama_name"],
                                           "alias": "lt-local", "kind": "local"}))
    a = PREREG["agent"]
    agent = ok(c.post("/api/agents", json={"name": a["name"], "role": "assistant", "system_prompt": a["system_prompt"],
                                           "model_id": model["id"], "max_steps": a["max_steps"],
                                           "max_tokens": a["max_tokens"], "tools": [], "permissions": {}}))
    out = {"provider_id": prov["id"], "model_id": model["id"], "agent_id": agent["id"],
           "agent_tools": agent.get("tools"), "created": time.time()}
    (work / "setup.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def run_one(c: httpx.Client, agent_id: int, prompt: str, title: str, timeout: float = 900) -> dict:
    t0 = time.time()
    task = ok(c.post("/api/tasks", json={"title": title[:160], "prompt": prompt, "agent_id": agent_id,
                                         "run_now": True, "max_retries": 0}))["task"]
    tid = int(task["id"])
    data: dict = {}
    while time.time() - t0 < timeout:
        data = ok(c.get(f"/api/tasks/{tid}"))
        if data["task"]["status"] in TERMINAL:
            break
        time.sleep(1.0)
    runs = data.get("runs") or []
    recalled: list[dict] = []
    for run in runs:
        for e in ok(c.get(f"/api/runs/{run['id']}/events", params={"limit": 1000})):
            if e.get("kind") in ("memory.recalled", "memory.recall_skipped"):
                d = e.get("data") or {}
                recalled.append({"kind": e["kind"], "sources": d.get("sources") or [],
                                 "preview_head": str(d.get("preview") or "")[:1200], "message": e.get("message")})
    last = runs[-1] if runs else {}
    answer = data.get("result")
    if answer is None:
        answer = last.get("result") or ""
    return {"task_id": tid, "status": data.get("task", {}).get("status"), "answer": answer,
            "error": data.get("error"), "run_ids": [r["id"] for r in runs], "model_alias": last.get("model_alias"),
            "tokens_in": last.get("tokens_in"), "tokens_out": last.get("tokens_out"),
            "seconds": round(time.time() - t0, 2), "memory_events": recalled}


def phase(c: httpx.Client, work: Path, name: str) -> None:
    agent_id = json.loads((work / "setup.json").read_text(encoding="utf-8"))["agent_id"]
    out = work / f"raw_{name}.jsonl"
    done = set()
    if out.is_file():
        for line in out.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            done.add((row["task"], row["rep"]))
    order = PREREG["run_order"]
    tasks = {t["id"]: t for t in PREREG["tasks"]}
    for rep in range(1, PREREG["repetitions"] + 1):
        for tid in order:
            if (tid, rep) in done:
                continue
            t = tasks[tid]
            res = run_one(c, agent_id, t["prompt"], f"lt-{name}-{tid}-r{rep}")
            row = {"phase": name, "task": tid, "set": t["set"], "rep": rep, **res}
            with out.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"{name} rep{rep} {tid} {res['status']} {res['seconds']}s "
                  f"recall={[s for e in res['memory_events'] for s in e['sources']]} "
                  f"ans={str(res['answer'])[-60:]!r}", flush=True)


def lesson(c: httpx.Client, work: Path) -> dict:
    L = PREREG["lesson"]["payload"]
    out: dict = {}
    r = ok(c.post("/api/memory/lessons", json=L))
    lid = r["lesson"]["id"]
    out["record"] = {"id": lid, "status": r["status"], "proven_learning": r["proven_learning"],
                     "deduped": r["deduped"], "lesson": r["lesson"]}
    out["duplicate"] = ok(c.post("/api/memory/lessons", json=L))["deduped"]
    # provenance: a cloud/remote writer cannot write private owner categories, whatever it calls itself
    neg = {}
    neg["cloud_header_owner_personal"] = c.post("/api/memory/lessons", json={**L, "title": "neg1", "author": "owner",
                                                 "category": "personal"}, headers={"X-Bossman-Writer": "cloud"}).status_code
    neg["codex_finance_private"] = c.post("/api/memory/lessons", json={**L, "title": "neg2", "author": "codex",
                                                                         "category": "finance_private"}).status_code
    neg["claude_credentials"] = c.post("/api/memory/lessons", json={**L, "title": "neg3", "author": "claude",
                                                                      "category": "credentials"}).status_code
    neg["writer_sets_verified"] = c.post("/api/memory/lessons", json={**L, "title": "neg4",
                                                                        "status": "verified_by_test"}).status_code
    neg["no_source"] = c.post("/api/memory/lessons", json={**L, "title": "neg5", "source": ""}).status_code
    neg["verify_by_claim"] = c.post(f"/api/memory/lessons/{lid}/verify", json={"eval_ref": "pytest:desk"}).status_code
    neg["verify_wrong_exercise_kind"] = c.post(f"/api/memory/lessons/{lid}/verify",
                                               json={"eval_ref": "trust-me:ok"}).status_code
    out["negatives"] = neg
    out["private_rows_written"] = ok(c.get("/api/memory/lessons", params={"include_private": "true"}))["total"]
    out["after_record"] = ok(c.get(f"/api/memory/lessons/{lid}"))
    (work / "lesson.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "after_record"}, ensure_ascii=False)[:1500])
    return out


def survive(c: httpx.Client, work: Path) -> dict:
    before = json.loads((work / "lesson.json").read_text(encoding="utf-8"))["after_record"]
    now = ok(c.get(f"/api/memory/lessons/{before['id']}"))
    keys = ["id", "title", "body", "author", "source", "observed_at", "valid_from", "limitations", "category",
            "content_hash", "status", "remote_writer"]
    out = {"same": {k: before.get(k) == now.get(k) for k in keys}, "after_restart": now}
    out["all_same"] = all(out["same"].values())
    (work / "survive.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out["same"]), out["all_same"])
    return out


def _json_tail(text: str) -> dict | None:
    for m in reversed(list(re.finditer(r"\{.*\}", text or "", re.S))):
        blob = m.group(0)
        for start in [i for i, ch in enumerate(blob) if ch == "{"]:
            try:
                obj = json.loads(blob[start:])
                if isinstance(obj, dict):
                    return obj
            except ValueError:
                continue
    return None


def exercise(c: httpx.Client, work: Path) -> dict:
    agent_id = json.loads((work / "setup.json").read_text(encoding="utf-8"))["agent_id"]
    lid = json.loads((work / "lesson.json").read_text(encoding="utf-8"))["record"]["id"]
    ex = PREREG["exercise"]
    task = ok(c.get(f"/api/learning/business-exercises/{ex['exercise_id']}"))
    prompt = (f"{task['task_ru']}\n\nrequests.csv:\n{task['inputs']['requests.csv']}\n"
              f"Ответь в конце ОДНИМ JSON-объектом вида {json.dumps(task['answer_schema'], ensure_ascii=False)}.")
    res = run_one(c, agent_id, prompt, "lt-exercise-swap-desk")
    answer = _json_tail(str(res["answer"]))
    out = {"run": res, "parsed_answer": answer}
    if answer is not None:
        v = ok(c.post(f"/api/memory/lessons/{lid}/verify",
                      json={"eval_ref": f"business_exercise:{ex['exercise_id']}", "answer": answer}))
        out["verify"] = {"eval_passed": v["eval_passed"], "status": v["lesson"]["status"],
                         "receipt": v.get("receipt"), "proven_learning": v["lesson"]["proven_learning"]}
    else:
        out["verify"] = {"skipped": "model answer had no JSON object"}
    (work / "exercise.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out["verify"], ensure_ascii=False))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("name", nargs="?")
    ap.add_argument("--url", required=True)
    ap.add_argument("--data-dir", required=True, type=Path)
    ap.add_argument("--work", required=True, type=Path)
    a = ap.parse_args()
    a.work.mkdir(parents=True, exist_ok=True)
    c = client(a.url, a.data_dir)
    if a.cmd == "setup":
        print(setup(c, a.work))
    elif a.cmd == "smoke":
        agent_id = json.loads((a.work / "setup.json").read_text(encoding="utf-8"))["agent_id"]
        r = run_one(c, agent_id, "Сколько будет 2 + 3? В последней строке напиши: ОТВЕТ: <число>", "lt-smoke")
        (a.work / "smoke.json").write_text(json.dumps(r, indent=1, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(r, ensure_ascii=False)[:2000])
    elif a.cmd == "phase":
        phase(c, a.work, a.name)
    elif a.cmd == "lesson":
        lesson(c, a.work)
    elif a.cmd == "survive":
        survive(c, a.work)
    elif a.cmd == "exercise":
        exercise(c, a.work)
    else:
        sys.exit(f"unknown cmd {a.cmd}")


if __name__ == "__main__":
    main()
