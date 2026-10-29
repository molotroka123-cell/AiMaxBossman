"""EVAL500 runner: each scenario through the REAL chat-threads path of the live backend (:8801).

    python tools/eval500/runner.py run   --run-id baseline-cc7d1e42 [--parts dev,holdout] [--workers 1] [--limit N]
    python tools/eval500/runner.py cleanup --run-id baseline-cc7d1e42

Path per turn = POST /api/chat/threads/{id}/send (the same preflight → task → run the web app, mobile pult and CLI use),
owner token from %LOCALAPPDATA%\\Bossman\\CommandCenter\\token (header X-BCC-Token, never printed), X-Participant: owner.

Safety (never relaxed):
  * every approval raised by an EVAL task is REJECTED (counts as «approval asked»); approvals of other tasks are never
    touched; nothing is ever approved;
  * executor = a TEMPORARY clone of the production default agent with the same model, system prompt, permissions and
    tools EXCEPT the side-effect tools cli.delegate (external coder CLI, AUTO) and photo.edit (GPU job) — three-model
    decision docs/audits/2026-10-25-three-model-audit/eval500-side-effect-tools.json; the clone is deleted at the end;
  * a guard stops the task if a guarded tool is ever called anyway; the owner's global STOP, the pause file
    D:\\Bossman\\eval\\PAUSE and ANY running owner/Jeff task (created < 2 h ago) make the runner yield — the eval never
    competes with a live owner chat for the shared GPU;
  * eval threads carry the title prefix «EVAL500» and are purged at the end (tasks stay as evidence, like the
    25.10 chat cleanup); concurrency 1–2 so Jeff/Gemma keep working; state file = resume.
Transcripts: dev → D:\\Bossman\\eval\\runs\\<run>\\dev\\, holdout → D:\\Bossman\\eval\\heldout\\runs\\<run>\\ (sealed).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from eval500 import schema  # noqa: E402

BASE = os.environ.get("EVAL500_BASE", "http://127.0.0.1:8801")
DATA = Path(os.environ.get("LOCALAPPDATA", "")) / "Bossman" / "CommandCenter"
TOKEN_FILE = DATA / "token"
RUNS = Path(os.environ.get("EVAL500_RUNS_DIR", r"D:\Bossman\eval\runs"))
TITLE_PREFIX = "EVAL500"
TERMINAL = frozenset({"completed", "failed", "stopped", "cancelled", "blocked"})
SIDE_EFFECT_TOOLS = frozenset({"cli.delegate", "photo.edit"})
TURN_TIMEOUT_S = float(os.environ.get("EVAL500_TURN_TIMEOUT_S", "600"))
POLL_S = 2.0
RUNNER_BY = "eval500-runner"


class Api:
    def __init__(self, base: str = BASE, token: str | None = None) -> None:
        self.base = base.rstrip("/")
        self._token = token if token is not None else TOKEN_FILE.read_text(encoding="utf-8").strip()

    def req(self, method: str, path: str, body=None, headers: dict | None = None, timeout: float = 120):
        h = {"X-BCC-Token": self._token, "X-Participant": "owner", "Content-Type": "application/json"}
        h.update(headers or {})
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.base + "/api" + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(r, timeout=timeout) as f:
                raw = f.read()
                return f.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw) if raw else None
            except ValueError:
                return e.code, {"raw": raw[:300].decode("utf-8", "replace")}


# ---------------------------------------------------------------- approvals: reject, never approve

def reject_own_approvals(api: Api, task_ids: set[int], seen: dict[int, dict]) -> list[dict]:
    """Reject pending approvals whose task_id belongs to THIS scenario; return the ones rejected now."""
    code, rows = api.req("GET", "/approvals?status=pending")
    if code != 200 or not isinstance(rows, list):
        return []
    done = []
    for a in rows:
        try:
            tid = int(a.get("task_id")) if a.get("task_id") is not None else None
        except (TypeError, ValueError):
            tid = None
        if tid is None or tid not in task_ids or a.get("id") in seen:
            continue
        c, row = api.req("POST", f"/approvals/{int(a['id'])}", {"approve": False, "by": RUNNER_BY})
        rec = {"id": a.get("id"), "task_id": tid, "kind": a.get("kind"),
               "tool": (a.get("preview") or {}).get("tool") if isinstance(a.get("preview"), dict) else None,
               "rejected_http": c, "status_after": (row or {}).get("status") if isinstance(row, dict) else None}
        seen[int(a["id"])] = rec
        done.append(rec)
    return done


# ---------------------------------------------------------------- executor clone

def _prod_default_agent(api: Api) -> dict:
    # EVAL500_PROD_AGENT pins the agent the owner actually uses in the pult (25.10: the backend preflight default was
    # agent 42, a 70B model at ~455 s per scenario, while the pult runs agent 41 Gemma).
    pinned = os.environ.get("EVAL500_PROD_AGENT", "").strip()
    if pinned:
        agent_id = int(pinned)
    else:
        code, pre = api.req("POST", "/tasks/preflight", {"prompt": "Привет! Как дела?", "agent_id": None})
        if code != 200 or not pre.get("ok"):
            raise SystemExit(f"preflight failed: {code} {pre}")
        agent_id = int(pre["agent"]["id"])
    code, agents = api.req("GET", "/agents")
    agents = agents.get("items", agents) if isinstance(agents, dict) else agents
    return next(a for a in agents if int(a["id"]) == agent_id)


def agent_fingerprint(a: dict) -> dict:
    keep = {k: a.get(k) for k in ("model_id", "fallback_model_id", "tools", "max_steps", "max_tokens",
                                  "budget_usd", "permissions", "role")}
    return {"system_prompt_sha256": hashlib.sha256(str(a.get("system_prompt") or "").encode()).hexdigest(),
            **keep}


def clone_spec(prod: dict) -> dict:
    return {"name": f"{TITLE_PREFIX} temporary clone of agent {prod['id']} (no side-effect tools)",
            "role": "eval500:clone", "system_prompt": prod.get("system_prompt") or "",
            "model_id": prod.get("model_id"), "fallback_model_id": prod.get("fallback_model_id"),
            "tools": [t for t in (prod.get("tools") or []) if t not in SIDE_EFFECT_TOOLS],
            "max_steps": prod.get("max_steps") or 4, "max_tokens": prod.get("max_tokens") or 2048,
            "budget_usd": prod.get("budget_usd") or 0.0, "permissions": dict(prod.get("permissions") or {}),
            "enabled": True}


def ensure_clone(api: Api, state: dict) -> dict:
    prod = _prod_default_agent(api)
    spec = clone_spec(prod)
    code, agents = api.req("GET", "/agents")
    agents = agents.get("items", agents) if isinstance(agents, dict) else agents
    cid = state.get("clone_agent_id")
    row = next((a for a in agents if cid is not None and int(a["id"]) == int(cid)), None)
    if row is None:
        code, row = api.req("POST", "/agents", spec)
        if code >= 300:
            raise SystemExit(f"cannot create the eval clone: {code} {row}")
    state["clone_agent_id"] = int(row["id"])
    state["prod_agent"] = {"id": prod["id"], "name": prod.get("name"), **agent_fingerprint(prod)}
    state["clone_agent"] = {"id": row["id"], "removed_tools": sorted(set(prod.get("tools") or []) & SIDE_EFFECT_TOOLS),
                            **agent_fingerprint(row)}
    return row


def delete_clone(api: Api, state: dict) -> dict:
    cid = state.get("clone_agent_id")
    if cid is None:
        return {"clone": None}
    code, body = api.req("DELETE", f"/agents/{int(cid)}")
    return {"clone": cid, "delete_http": code}


# ---------------------------------------------------------------- one scenario

def _events(api: Api, task_id: int) -> list[dict]:
    code, body = api.req("GET", f"/tasks/{task_id}/events?limit=1000")
    return body.get("events", []) if code == 200 and isinstance(body, dict) else []


def _tools_from_events(events: list[dict]) -> list[dict]:
    out = []
    for e in events:
        kind = str(e.get("kind") or "")
        if kind.startswith(("tool.", "approval.", "run.tool")):
            out.append({k: e.get(k) for k in ("kind", "tool", "ok", "reason", "effect", "status", "error",
                                              "approval_id", "approval_kind", "id") if e.get(k) is not None})
    return out


def run_turn(api: Api, thread_id: str, text: str, agent_id: int, surface: str, stop_after: float | None,
             approvals: dict[int, dict], task_ids: set[int], stop_event: threading.Event | None = None) -> dict:
    headers = {"X-Bossman-Surface": "mobile"} if surface == "mobile_pult" else {}
    t0 = time.time()
    code, r = api.req("POST", f"/chat/threads/{thread_id}/send", {"text": text, "agent_id": agent_id},
                      headers=headers, timeout=180)
    turn = {"text": text, "send_http": code, "started": round(t0, 3)}
    if code >= 300 or not isinstance(r, dict) or not isinstance(r.get("task"), dict):
        err = (r or {}).get("error") if isinstance(r, dict) else None
        turn.update(status="not_admitted", error=err if err else r, seconds=round(time.time() - t0, 1))
        return turn
    task_id = int(r["task"]["id"])
    task_ids.add(task_id)
    turn.update(task_id=task_id, admission=r.get("admission"), model=(r.get("model") or {}).get("alias"))
    stopped_by_runner = None
    status, view = None, {}
    while True:
        reject_own_approvals(api, task_ids, approvals)
        c, view = api.req("GET", f"/tasks/{task_id}")
        status = (view or {}).get("task", {}).get("status") if c == 200 else None
        if status in TERMINAL:
            break
        elapsed = time.time() - t0
        guard_hit = None
        if stopped_by_runner is None:
            tools = {e.get("tool") for e in _tools_from_events(_events(api, task_id))} if elapsed > 4 else set()
            guard_hit = sorted(tools & SIDE_EFFECT_TOOLS)
        if stopped_by_runner is None and (guard_hit or (stop_after is not None and elapsed >= stop_after)
                                          or elapsed > TURN_TIMEOUT_S):
            why = ("side_effect_guard:" + ",".join(guard_hit)) if guard_hit else (
                "scenario_stop" if stop_after is not None and elapsed >= stop_after else "timeout")
            sc, sb = api.req("POST", f"/chat/threads/{thread_id}/stop")
            stopped_by_runner = {"why": why, "at_s": round(elapsed, 1), "http": sc,
                                 "status": (sb or {}).get("status") if isinstance(sb, dict) else None}
        if elapsed > TURN_TIMEOUT_S + 120:
            break
        if stop_event is not None and stop_event.is_set():
            break
        time.sleep(POLL_S)
    reject_own_approvals(api, task_ids, approvals)
    events = _events(api, task_id)
    runs = (view or {}).get("runs") or []
    last = runs[-1] if runs else {}
    turn.update(status=status, result=(view or {}).get("result"), error=(view or {}).get("error"),
                seconds=round(time.time() - t0, 1), tool_events=_tools_from_events(events),
                tools_called=sorted({e.get("tool") for e in _tools_from_events(events)
                                     if e.get("tool") and e.get("kind") in ("tool.called", "tool.started", "tool.completed",
                                                                            "tool.denied", "run.tool_call",
                                                                            "run.tool_result")}),
                stopped_by_runner=stopped_by_runner,
                usage={"tokens_in": last.get("tokens_in"), "tokens_out": last.get("tokens_out"),
                       "cost_usd": last.get("cost_usd"), "model_alias": last.get("model_alias")})
    return turn


def _approvals_for(api: Api, task_ids: set[int]) -> list[dict]:
    code, rows = api.req("GET", "/approvals?status=all")
    if code != 200 or not isinstance(rows, list):
        return []
    return [{"id": a.get("id"), "task_id": a.get("task_id"), "kind": a.get("kind"), "status": a.get("status"),
             "decided_by": a.get("decided_by")} for a in rows if a.get("task_id") in task_ids]


def new_thread(api: Api, sid: str) -> str:
    code, t = api.req("POST", "/chat/threads", {"title": f"{TITLE_PREFIX} {sid}"})
    if code >= 300:
        raise RuntimeError(f"create thread failed: {code} {t}")
    return t["id"]


def purge_thread(api: Api, thread_id: str) -> int:
    code, _ = api.req("DELETE", f"/chat/threads/{thread_id}?purge=true&confirm={thread_id}")
    return code


def run_scenario(api: Api, s: dict, agent_id: int, stop_event: threading.Event | None = None) -> dict:
    t0 = time.time()
    approvals: dict[int, dict] = {}
    task_ids: set[int] = set()
    threads = [new_thread(api, s["id"])]
    turns, stop_after = [], None
    try:
        for t in s["turns"]:
            action = t.get("action", "send")
            if action == "stop":
                stop_after = float(t.get("after_s", 0))
                if turns and turns[-1].get("status") not in TERMINAL and turns[-1].get("task_id"):
                    api.req("POST", f"/chat/threads/{threads[-1]}/stop")
                turns.append({"action": "stop", "after_s": stop_after})
                continue
            if action == "new_thread":
                threads.append(new_thread(api, s["id"]))
                turns.append({"action": "new_thread"})
                continue
            # a stop action right AFTER this send is applied while this turn runs
            nxt = s["turns"][len(turns) + 1] if len(turns) + 1 < len(s["turns"]) else None
            sa = float(nxt.get("after_s", 0)) if isinstance(nxt, dict) and nxt.get("action") == "stop" else None
            turns.append(run_turn(api, threads[-1], t["text"], agent_id, s["surface"], sa, approvals, task_ids,
                                  stop_event))
            if stop_event is not None and stop_event.is_set():
                break
    finally:
        purged = {tid: purge_thread(api, tid) for tid in threads}
    return {"id": s["id"], "category": s["category"], "surface": s["surface"], "turns": turns,
            "task_ids": sorted(task_ids), "approvals_rejected": list(approvals.values()),
            "approvals": _approvals_for(api, task_ids), "threads_purged": purged,
            "seconds": round(time.time() - t0, 1), "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S")}


# ---------------------------------------------------------------- run orchestration + resume

def owner_stop_active() -> bool:
    return (DATA / "computer" / "STOP").exists()


PAUSE_FILE = Path(os.environ.get("EVAL500_PAUSE_FILE", r"D:\Bossman\eval\PAUSE"))
OWNER_FRESH_S = 2 * 3600


def owner_busy(api: Api, clone_id: int | None) -> list[int]:
    """Running/queued tasks that are not ours (created < 2 h ago): the owner (or Jeff) is using the local models.

    The eval yields: it never competes with a live owner chat for the shared GPU / llama-server (owner 25.10)."""
    code, rows = api.req("GET", "/tasks?status=running,queued&limit=50")
    if code != 200 or not isinstance(rows, list):
        return []
    now = time.time()
    busy = []
    for t in rows:
        if clone_id is not None and t.get("agent_id") == clone_id:
            continue
        try:
            created = time.mktime(time.strptime(str(t.get("created_at"))[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
        except (TypeError, ValueError):
            created = now
        if now - created < OWNER_FRESH_S:
            busy.append(int(t["id"]))
    return busy


def wait_until_free(api: Api, clone_id: int | None, poll_s: float = 30.0) -> dict:
    waited, reasons = 0.0, set()
    while True:
        why = []
        if owner_stop_active():
            why.append("owner STOP")
        if PAUSE_FILE.exists():
            why.append(f"pause file {PAUSE_FILE}")
        busy = owner_busy(api, clone_id)
        if busy:
            why.append(f"owner/other tasks running {busy[:5]}")
        if not why:
            return {"waited_s": round(waited, 1), "reasons": sorted(reasons)}
        if not reasons.issuperset(why):
            print(f"[eval500] yielding: {'; '.join(why)}", flush=True)
        reasons.update(w.split(" [")[0] for w in why)
        time.sleep(poll_s)
        waited += poll_s


def run_dir(run_id: str) -> Path:
    return RUNS / run_id


def transcript_path(run_id: str, part: str, sid: str) -> Path:
    base = (schema.HOLDOUT_DIR / "runs" / run_id) if part == "holdout" else (run_dir(run_id) / "dev")
    return base / f"{sid}.json"


def load_state(run_id: str) -> dict:
    p = run_dir(run_id) / "state.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {"run_id": run_id, "done": {}}


def save_state(run_id: str, state: dict) -> None:
    p = run_dir(run_id) / "state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def _source_identity(api: Api) -> dict:
    try:
        with urllib.request.urlopen(BASE + "/health", timeout=20) as f:
            h = json.loads(f.read())
    except (urllib.error.URLError, OSError, ValueError):
        h = {}
    head = subprocess.run(["git", "-C", str(schema.ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(schema.ROOT), "status", "--porcelain", "--", "command-center",
                            "bossman-core", "bossman-shared"], capture_output=True, text=True).stdout.strip()
    return {"backend": BASE, "health_version": h.get("version"), "health_build_sha": h.get("build_sha"),
            "health_source_identity": h.get("source_identity"), "worktree": str(schema.ROOT),
            "worktree_head": head, "product_code_dirty": bool(dirty)}


def cleanup_eval_threads(api: Api) -> dict:
    removed, errors = [], []
    for archived in ("false", "true"):
        # LIST_MAX of /api/chat/threads is 200; a bigger limit is a 422 and used to look like «nothing to clean»
        code, body = api.req("GET", f"/chat/threads?archived={archived}&limit=200")
        if code != 200:
            errors.append({"archived": archived, "http": code})
            continue
        for it in (body or {}).get("items", []):
            if str(it.get("title") or "").startswith(TITLE_PREFIX):
                removed.append((it["id"], purge_thread(api, it["id"])))
    if errors:
        raise RuntimeError(f"thread listing failed during cleanup: {errors}")
    return {"purged": len(removed), "http": sorted({c for _, c in removed})}


def run(run_id: str, parts: list[str], workers: int, limit: int | None, sleep_s: float, only: list[str] | None,
        keep_clone: bool) -> dict:
    api = Api()
    state = load_state(run_id)
    state.setdefault("started_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
    state["identity"] = state.get("identity") or _source_identity(api)
    clone = ensure_clone(api, state)
    save_state(run_id, state)
    todo = []
    for part in parts:
        items = schema.load_dev() if part == "dev" else schema.load_holdout("runner")
        todo += [(part, s) for s in items if s["id"] not in state["done"] and (not only or s["id"] in only)]
    random.Random(run_id).shuffle(todo)          # interleave categories; deterministic per run id
    if limit:
        todo = todo[:limit]
    lock = threading.Lock()
    stop_event = threading.Event()
    print(f"[eval500] {run_id}: {len(todo)} to run, {len(state['done'])} done, clone agent {clone['id']}", flush=True)

    def one(item):
        part, s = item
        if stop_event.is_set():
            return
        yielded = wait_until_free(api, int(clone["id"]))
        try:
            tr = run_scenario(api, s, int(clone["id"]), stop_event)
        except Exception as exc:  # noqa: BLE001 — one scenario's harness error is recorded, the run goes on
            tr = {"id": s["id"], "category": s["category"], "harness_error": f"{type(exc).__name__}: {exc}"[:300],
                  "turns": [], "task_ids": []}
        tr["part"] = part
        tr["yielded_before"] = yielded
        p = transcript_path(run_id, part, s["id"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(tr, ensure_ascii=False, indent=1), encoding="utf-8")
        with lock:
            state["done"][s["id"]] = {"part": part, "seconds": tr.get("seconds"),
                                      "harness_error": tr.get("harness_error"),
                                      "task_ids": tr.get("task_ids")}
            save_state(run_id, state)
            n = len(state["done"])
        print(f"[eval500] {n} done | {s['id']} {tr.get('seconds')} s "
              f"{[t.get('status') for t in tr.get('turns', []) if 'status' in t]}", flush=True)
        time.sleep(sleep_s)

    try:
        with ThreadPoolExecutor(max_workers=max(1, min(2, workers))) as ex:
            list(ex.map(one, todo))
    except KeyboardInterrupt:
        stop_event.set()
    total = len(schema.load_dev()) * ("dev" in parts) + (schema.HOLDOUT_TOTAL if "holdout" in parts else 0)
    finished = len(state["done"]) >= total
    if finished:
        state["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        state["cleanup"] = cleanup_eval_threads(api)
        if not keep_clone:
            state["clone_deleted"] = delete_clone(api, state)
    save_state(run_id, state)
    return {"run_id": run_id, "done": len(state["done"]), "total": total, "finished": finished}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mode", choices=["run", "cleanup"])
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--parts", default="dev,holdout")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--only", default="")
    ap.add_argument("--sleep", type=float, default=3.0)
    ap.add_argument("--keep-clone", action="store_true")
    a = ap.parse_args(argv)
    if a.mode == "cleanup":
        api = Api()
        state = load_state(a.run_id)
        out = {"threads": cleanup_eval_threads(api), "clone": delete_clone(api, state)}
        state["cleanup_manual"] = out
        save_state(a.run_id, state)
        print(json.dumps(out))
        return 0
    out = run(a.run_id, [p for p in a.parts.split(",") if p], a.workers, a.limit, a.sleep,
              [x for x in a.only.split(",") if x] or None, a.keep_clone)
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
