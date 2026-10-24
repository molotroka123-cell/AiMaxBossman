"""Economical local model tournament (owner 24.10). One heavy job at a time, only while Bossman is idle, $0.

    python tools/model_tournament.py run  --out docs/audits/2026-10-24-local-model-tournament --only main,fast [--runs 1]
    python tools/model_tournament.py summarize --out docs/audits/2026-10-24-local-model-tournament

Every contestant runs on the SAME pinned runtime (llama-server b10964 Vulkan, flags of the lab run: -ngl 999 -t 16
--jinja --reasoning off), loaded from its local weights file, so load time, tokens/sec and RAM are comparable and the
OpenAI-compatible ``timings`` exist. Stages: cold load (+RAM) -> needle retrieval 8k -> 32k -> 64k (124k/256k only after
the model DECLARES them and every smaller step passed) -> bake-off A-G (tool/schema, error recovery, executed code;
tools/model_bakeoff.py) -> STOP (client abort must stop generation). Receipts hold counts, times and hashes, never prompts.
The tool measures and ranks; it never changes MAIN/FAST and never promotes a model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import os  # noqa: E402

SERVER = Path(os.environ.get("BOSSMAN_LLAMA_SERVER") or Path.home() / "Bossman" / "runtime" / "llama.cpp" / "llama-server.exe")
OLLAMA = "http://127.0.0.1:11434"
BACKEND = "http://127.0.0.1:8801"
PORT = 8091
BLOBS = Path(os.environ.get("OLLAMA_MODELS") or Path.home() / ".ollama" / "models") / "blobs"
CANDIDATES_DIR = Path("D:/Bossman/models/candidates")
BASE_STEPS = (8192, 32768, 65536)
LARGE_STEPS = (126976, 262144)               # 124k, 256k: gated by declaration + smaller steps + an explicit flag
CHARS_PER_TOKEN = 5.6
FLAGS = ["-ngl", "999", "-t", "16", "--jinja", "--reasoning", "off", "--host", "127.0.0.1"]

CONTESTANTS = {
    "main": {"tag": "bossman-main-qwen38-27b-q5:latest", "tasks": True},
    "fast": {"tag": "bossman-fast-qwen36-35b-a3b-q5:latest", "tasks": True},
    "coder": {"tag": "qwen3-coder:30b", "tasks": True},
    "gemma": {"tag": "gemma4-31b-uncensored:latest", "tasks": True},
    # same weights, two context configurations: compared as configurations (load/RAM/needle), tasks once
    "community-64k": {"tag": "bossman-community-qwen-uncensored-ctx64k:latest", "tasks": True, "ctx": 65536},
    "community-256k": {"tag": "bossman-community-qwen-uncensored-ctx256k:latest", "tasks": False, "ctx": 262144},
    "cand-b": {"gguf": CANDIDATES_DIR / "tournament-b-qwen3vl-4b" / "Qwen3VL-4B-Instruct-Q8_0.gguf",
               "mmproj": CANDIDATES_DIR / "tournament-b-qwen3vl-4b" / "mmproj-Qwen3VL-4B-Instruct-F16.gguf", "tasks": True},
    "cand-c": {"gguf": CANDIDATES_DIR / "tournament-c-mistral-small-31" / "mistralai_Mistral-Small-3.1-24B-Instruct-2503-Q4_K_M.gguf",
               "mmproj": CANDIDATES_DIR / "tournament-c-mistral-small-31" / "mmproj-mistralai_Mistral-Small-3.1-24B-Instruct-2503-f16.gguf",
               "tasks": True},
    "cand-a": {"gguf": CANDIDATES_DIR / "tournament-a-qwen3-coder-next" / "Qwen3-Coder-Next-Q4_K_M-00001-of-00004.gguf", "tasks": True},
}
# NOT in this table on purpose: the production vision route (bossman-fast-qwen36-vision) carries the FAST weights plus a
# projector, so its text results are FAST's results; its vision quality is proven by tools/prove_chat_attachments.py.


class OwnerBusy(RuntimeError):
    """The owner (or a generation job) needs the machine: stop this contestant, never compete with the owner."""


# ----------------------------------------------------------------------------- pure helpers (unit-tested)

def percentile(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile; None for no data (never a made-up 0)."""
    xs = sorted(values)
    if not xs:
        return None
    return xs[max(0, min(len(xs) - 1, int(round(q * (len(xs) - 1)))))]


def call_metrics(task_results: dict) -> dict:
    calls = [c for r in task_results.values() for c in r.get("calls", [])]
    secs = [c["s"] for c in calls if c.get("s") is not None]
    tps = [c["gen_tps"] for c in calls if c.get("gen_tps")]
    return {"calls": len(secs), "p50_s": percentile(secs, 0.5), "p95_s": percentile(secs, 0.95),
            "gen_tps_median": percentile(tps, 0.5), "total_s": round(sum(secs), 1)}


def context_steps(declared: int | None, passed: list[int], max_context: int = 65536, allow_large: bool = False) -> list[int]:
    """The NEXT step to run (a list of 0 or 1). A step is offered only after every smaller one passed; 124k/256k
    additionally need the model's own declaration and ``allow_large`` (each takes many minutes)."""
    for step in BASE_STEPS + LARGE_STEPS:
        if step in passed:
            continue
        if step > max_context and not (allow_large and step in LARGE_STEPS):
            return []
        if step in LARGE_STEPS and (not declared or declared < step):
            return []
        return [step]
    return []


def needle_prompt(target_tokens: int, canary: str) -> str:
    head = f"СЕКРЕТНЫЙ КОД ДЛЯ ПРОВЕРКИ ПАМЯТИ: {canary}. Запомни его; в самом конце я спрошу этот код.\n\n"
    filler = "Запись {i}: температура {t}, влажность {h}, давление {p}. Record {i}: temperature {t}, humidity {h}, pressure {p}.\n"
    parts, chars, i = [head], len(head), 0
    while chars < target_tokens * 0.92 * CHARS_PER_TOKEN:
        line = filler.format(i=i, t=i % 40, h=10 + i % 80, p=700 + i % 90)
        parts.append(line)
        chars += len(line)
        i += 1
    parts.append("\n\nВОПРОС: какой СЕКРЕТНЫЙ КОД был в самом начале? Ответь только кодом.")
    return "".join(parts)


def useful_per_hour(passed: int, bakeoff_s: float, load_s: float | None) -> float | None:
    """Verified-useful tasks per hour of machine time, load included (a model that loads for 20 min pays for it)."""
    seconds = (bakeoff_s or 0) + (load_s or 0)
    return round(passed / (seconds / 3600), 2) if seconds > 0 else None


def eligible(rec: dict) -> tuple[bool, str]:
    if rec.get("status") != "DONE":
        return False, f"status {rec.get('status')}"
    if not (rec.get("stop") or {}).get("ok"):
        return False, "STOP did not stop generation"
    if 32768 not in [n["target_tokens"] for n in rec.get("needle", []) if n.get("pass")]:
        return False, "needle 32k not passed"
    if rec.get("tasks") is None:
        return False, "no task results"
    return True, ""


def pick_winner(records: list[dict]) -> dict:
    """One winner by verified useful work/hour among eligible contestants; PROVISIONAL until it won on two runs."""
    table = []
    for rec in records:
        ok, why = eligible(rec)
        table.append({"id": rec["id"], "eligible": ok, "reason": why, "useful_per_hour": rec.get("useful_per_hour") if ok else None,
                      "passed": (rec.get("tasks") or {}).get("passed"), "p95_s": (rec.get("metrics") or {}).get("p95_s")})
    ranked = sorted((t for t in table if t["eligible"] and t["useful_per_hour"] is not None),
                    key=lambda t: (-t["useful_per_hour"], -(t["passed"] or 0), t["p95_s"] if t["p95_s"] is not None else 1e9))
    if not ranked:
        return {"winner": None, "verdict": "NO_ELIGIBLE_CONTESTANT", "table": table}
    best = ranked[0]
    wins = sum(1 for t in ranked if t["id"] == best["id"])
    return {"winner": best["id"], "verdict": "WINNER" if wins >= 2 else "PROVISIONAL_SINGLE_RUN",
            "production_change": "NONE - needs a separate owner approval after a repeatable win without safety regression",
            "table": table}


# ----------------------------------------------------------------------------- machine access

def _http(url: str, body: dict | None = None, timeout: float = 60.0) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=timeout) as resp:  # noqa: S310 - loopback
        return json.loads(resp.read().decode("utf-8", "replace") or "null")


def bossman_idle() -> tuple[bool, str]:
    sys.path.insert(0, str(HERE.parent / "command-center"))
    try:
        from bcc import owner_activity
        marker = owner_activity.active()
        if marker:
            return False, owner_activity.describe(marker)
    except ImportError:
        pass
    try:
        q = _http(f"{BACKEND}/api/direct-gen/status", timeout=30)["queue"]
    except Exception:  # noqa: BLE001
        return False, "backend does not answer"
    return (False, "generation queue busy") if q.get("running") or q.get("waiting") else (True, "")


def wait_idle(poll_s: float = 20.0, max_wait_s: float = 6 * 3600) -> None:
    waited = 0.0
    while True:
        if bossman_idle()[0]:
            return
        if waited >= max_wait_s:
            raise OwnerBusy("never became idle")
        time.sleep(poll_s)
        waited += poll_s


def guard() -> None:
    idle, why = bossman_idle()
    if not idle:
        raise OwnerBusy(why)


def unload_ollama() -> list[str]:
    names = []
    for m in _http(f"{OLLAMA}/api/ps").get("models", []):
        _http(f"{OLLAMA}/api/generate", {"model": m["name"], "keep_alive": 0}, timeout=120)
        names.append(m["name"])
    return names


def resolve_weights(c: dict) -> tuple[Path, Path | None]:
    if c.get("gguf"):
        return Path(c["gguf"]), (Path(c["mmproj"]) if c.get("mmproj") else None)
    from model_tournament_inventory import weights_blob
    blob = weights_blob(_http(f"{OLLAMA}/api/show", {"model": c["tag"]}).get("modelfile", ""))
    if not blob:
        raise FileNotFoundError(f"no local weights blob for {c['tag']}")
    return BLOBS / f"sha256-{blob}", None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb", buffering=0) as f:
        while True:
            b = f.read(16 << 20)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


class Ram:
    """Lowest free system RAM and highest server RSS while a stage runs."""

    def __init__(self, pid: int | None = None):
        import psutil
        self.ps, self.pid, self.min_free, self.max_rss, self._stop = psutil, pid, None, 0, threading.Event()
        self.base_free = psutil.virtual_memory().available

    def _loop(self):
        while not self._stop.wait(0.5):
            free = self.ps.virtual_memory().available
            self.min_free = free if self.min_free is None else min(self.min_free, free)
            if self.pid:
                try:
                    self.max_rss = max(self.max_rss, self.ps.Process(self.pid).memory_info().rss)
                except self.ps.Error:
                    pass

    def __enter__(self):
        threading.Thread(target=self._loop, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self._stop.set()

    def report(self) -> dict:
        return {"free_before_gb": round(self.base_free / 1e9, 1), "min_free_gb": round((self.min_free or self.base_free) / 1e9, 1),
                "server_rss_max_gb": round(self.max_rss / 1e9, 1)}


def start_server(gguf: Path, mmproj: Path | None, ctx: int, load_timeout: float = 1800.0):
    args = [str(SERVER), "-m", str(gguf), "-c", str(ctx), "--port", str(PORT), *FLAGS]
    if mmproj:
        args += ["--mmproj", str(mmproj)]
    t0 = time.monotonic()
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    while time.monotonic() - t0 < load_timeout:
        if proc.poll() is not None:
            raise RuntimeError(f"llama-server exited with {proc.returncode} (architecture or flags not supported?)")
        try:
            if _http(f"http://127.0.0.1:{PORT}/health", timeout=5).get("status") == "ok":
                return proc, round(time.monotonic() - t0, 1)
        except Exception:  # noqa: BLE001 - still loading
            time.sleep(2)
    proc.kill()
    raise TimeoutError("llama-server did not become healthy")


def stop_server(proc) -> None:
    import psutil
    try:
        parent = psutil.Process(proc.pid)
        for child in parent.children(recursive=True):
            child.kill()
        parent.kill()
    except psutil.Error:
        pass


def needle_step(target: int) -> dict:
    canary = "".join(random.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(10))
    t0 = time.monotonic()
    try:
        r = _http(f"http://127.0.0.1:{PORT}/completion", {"prompt": needle_prompt(target, canary), "n_predict": 48,
                                                          "temperature": 0, "cache_prompt": False}, timeout=3600)
    except Exception as exc:  # noqa: BLE001
        return {"target_tokens": target, "pass": False, "error": f"{type(exc).__name__}: {str(exc)[:120]}",
                "seconds": round(time.monotonic() - t0, 1)}
    evaluated = int(r.get("tokens_evaluated") or 0)
    return {"target_tokens": target, "tokens_evaluated": evaluated, "truncated": bool(r.get("truncated")),
            "pass": canary in str(r.get("content") or "") and not r.get("truncated") and evaluated >= 0.7 * target,
            "seconds": round(time.monotonic() - t0, 1),
            "pp_tps": (r.get("timings") or {}).get("prompt_per_second"), "gen_tps": (r.get("timings") or {}).get("predicted_per_second")}


def stop_test(pid: int) -> dict:
    import psutil
    proc = psutil.Process(pid)
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/completion", data=json.dumps(
        {"prompt": "Count slowly from 1 to 5000, one number per line.", "n_predict": 4000, "temperature": 0, "stream": True}).encode(),
        headers={"Content-Type": "application/json"})
    resp = urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=120)
    for _ in range(30):
        resp.readline()
    proc.cpu_percent(None)
    time.sleep(1.5)
    busy = proc.cpu_percent(None) / 100
    resp.close()                                              # the client abort = what the STOP button does to a request
    t0, idle_cores, stopped_after = time.monotonic(), None, None
    while time.monotonic() - t0 < 15:
        time.sleep(1)
        idle_cores = proc.cpu_percent(None) / 100
        if idle_cores < 0.5:
            stopped_after = round(time.monotonic() - t0, 1)
            break
    try:
        healthy = _http(f"http://127.0.0.1:{PORT}/health", timeout=10).get("status") == "ok"
    except Exception:  # noqa: BLE001
        healthy = False
    return {"ok": stopped_after is not None and healthy and busy > 0.5, "busy_cores_before": round(busy, 2),
            "stopped_after_s": stopped_after, "cores_after": None if idle_cores is None else round(idle_cores, 2),
            "server_healthy_after": healthy}


def run_contestant(cid: str, out: Path, *, allow_large: bool = False, max_context: int = 65536) -> dict:
    c = CONTESTANTS[cid]
    rec = {"id": cid, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "status": "NOT_RUN", "cost_usd": 0.0}
    try:
        wait_idle()
        rec["unloaded_ollama"] = unload_ollama()
        gguf, mmproj = resolve_weights(c)
        rec["weights"] = {"file": gguf.name, "bytes": gguf.stat().st_size}
        try:
            info = _http(f"{OLLAMA}/api/show", {"model": c["tag"]}) if c.get("tag") else {}
            declared = next((v for k, v in info.get("model_info", {}).items() if k.endswith(".context_length")), None)
        except Exception:  # noqa: BLE001
            declared = None
        rec["declared_context"] = declared
        ctx = c.get("ctx") or max_context
        rec["runtime"] = {"binary": str(SERVER), "binary_sha256": sha256_file(SERVER), "ctx": ctx, "flags": FLAGS}
        guard()
        proc, load_s = start_server(gguf, mmproj, ctx)
        rec["load_s"] = load_s
        try:
            with Ram(proc.pid) as ram:
                passed, rec["needle"] = [], []
                while True:
                    guard()
                    steps = context_steps(declared, passed, min(max_context, ctx) if not allow_large else ctx, allow_large)
                    if not steps:
                        break
                    res = needle_step(steps[0])
                    rec["needle"].append(res)
                    if not res["pass"]:
                        break
                    passed.append(steps[0])
                rec["tasks"], rec["metrics"] = None, None
                if c.get("tasks"):
                    guard()
                    subprocess.run([sys.executable, str(HERE / "model_bakeoff.py"), "--url", f"http://127.0.0.1:{PORT}/v1", "--tag", cid,
                                    "--out", str(out / "bakeoff"), "--json", "--latency-probe", "--server-pid", str(proc.pid)],
                                   capture_output=True, text=True, timeout=7200)
                    bake = json.loads((out / "bakeoff" / f"{cid}.json").read_text(encoding="utf-8"))
                    rec["tasks"] = {"passed": bake["passed"], "total": bake["total"], "failed": bake["failed"], "seconds": bake["seconds"]}
                    rec["metrics"] = call_metrics(bake["results"])
                    rec["latency_probe"] = bake.get("latency_probe")
                    rec["useful_per_hour"] = useful_per_hour(bake["passed"], bake["seconds"], load_s)
                guard()
                rec["stop"] = stop_test(proc.pid)
            rec["ram"] = ram.report()
            rec["status"] = "DONE"
        finally:
            stop_server(proc)
    except OwnerBusy as exc:
        rec.update(status="INTERRUPTED_OWNER_ACTIVE", reason=str(exc))
    except Exception as exc:  # noqa: BLE001 - one contestant's failure is its FAIL, written next to it
        rec.update(status="FAIL", reason=f"{type(exc).__name__}: {str(exc)[:200]}")
    (out / "runs").mkdir(parents=True, exist_ok=True)
    (out / "runs" / f"{cid}-{int(time.time())}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1),
                                                                 encoding="utf-8", newline="\n")
    return rec


def summarize(out: Path) -> dict:
    recs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((out / "runs").glob("*.json"))]
    summary = pick_winner(recs)
    summary["at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    summary["contestants_run"] = sorted({r["id"] for r in recs})
    summary["not_run"] = sorted(set(CONTESTANTS) - set(summary["contestants_run"]))
    (out / "99-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=("run", "summarize"))
    ap.add_argument("--out", default="docs/audits/2026-10-24-local-model-tournament")
    ap.add_argument("--only", default="main,fast,coder,gemma")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--allow-large-context", action="store_true")
    a = ap.parse_args(argv)
    out = Path(a.out)
    if a.action == "run":
        for _ in range(a.runs):
            for cid in a.only.split(","):
                rec = run_contestant(cid.strip(), out, allow_large=a.allow_large_context)
                print(cid, rec["status"], rec.get("reason", ""), flush=True)
    print(json.dumps({k: v for k, v in summarize(out).items() if k != "table"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
