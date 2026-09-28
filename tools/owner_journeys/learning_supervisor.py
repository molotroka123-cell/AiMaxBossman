#!/usr/bin/env python3
"""Supervised 24/7 learning mode (local models, no Claude) — one bounded cycle at a time.

A cycle = pick the next held-out-safe task from the approved local set, run it
through the real Bossman task path (bcc TaskEngine + tool policy + approvals)
on a LOCAL Ollama model, score it with a deterministic verifier, append the
outcome and — on failure — a QUARANTINED lesson candidate. Nothing is ever
promoted, no permission is changed, nothing is sent or published, no Computer
Use tool is granted.

Guards checked before every cycle and while waiting:
* STOP  : ``<state>/STOP`` -> abort the running cycle and exit;
* owner STOP: the owner's durable Computer-Use STOP file (``<owner-data-root>/computer/STOP``,
          written by /stop or /pause in the пульт and by «СТОП» in Bossman) -> abort the running
          cycle and hold (no model calls) until the owner presses «Продолжить»;
* PAUSE : ``C:\\Users\\asd\\Bossman\\rc19-owner-test.PAUSE`` or ``<state>/PAUSE`` -> wait;
* owner busy: another (non-allowed) model is resident in Ollama, Ollama is unreachable, or an
          owner GPU job (``sd-cli.exe`` Studio render) runs -> only model-free cycles run;
* memory headroom below ``--min-free-gb`` -> wait;
* daily budget (cycles, model calls, wall seconds; cloud $0) -> sleep to next day.
State is restart-safe: an exclusive PID lock, atomic ``state.json``, fsync'd
append-only ``cycles.jsonl``; a cycle interrupted by a crash/kill is recorded
once as ``ABANDONED_ON_RESTART`` and never re-counted.

    python tools/owner_journeys/learning_supervisor.py --state-dir C:\\Users\\asd\\Bossman\\rc19-data\\d-learn\\learning247 \
        --max-hours 1.5
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.owner_journeys import route_ladder as rl  # noqa: E402
from tools.owner_journeys.runtime_guard import PAUSE_FILE, lower_priority  # noqa: E402

AB_ASK = None   # tests inject a fake ask(variant, system, text); default = local Ollama

OLLAMA = "http://127.0.0.1:11434"
DEFAULT_MODEL = "bossman-fast-qwen36-35b-a3b-q5:latest"
ALLOWED_RESIDENT = {DEFAULT_MODEL, "bossman-fast-qwen36-vision:latest", "bossman-main-qwen38-27b-q5:latest"}
CYCLE_KINDS = ("triage", "journey", "k1m6a_verify")
FORBIDDEN_TOOL_WORDS = ("computer", "send", "post", "publish", "transfer", "telegram", "email", "permission")


@dataclass
class Config:
    state_dir: Path
    model: str = DEFAULT_MODEL
    base_url: str = "http://127.0.0.1:11434/v1"
    allow_free_cloud: bool = False
    owner_data_root: Optional[Path] = None
    pause_files: list[Path] = field(default_factory=list)
    kinds: tuple[str, ...] = CYCLE_KINDS
    max_cycles_day: int = 400
    max_model_calls_day: int = 3000
    max_wall_s_day: int = 20 * 3600
    min_free_gb: float = 12.0
    cycle_timeout_s: float = 900.0
    poll_s: float = 5.0
    idle_between_cycles_s: float = 10.0
    k1m6a_root: Path = Path(r"C:\Users\asd\Bossman\evidence\rc19\d\yt\raw")
    force_tier: str = ""          # tests only: restrict the ladder to one tier
    report: str = "off"           # library/test default; the CLI (autostart) passes "telegram" (пульт)
    approvals: Any = None         # Bossman approval backend for lessons; the CLI passes "auto" (local core)
    check_busy: bool = True
    owner_stop_baseline: float = 0.0   # set at start: owner STOPs older than the first run are ignored
    busy_processes: tuple[str, ...] = ("sd-cli.exe",)

    def validate(self) -> None:
        host = self.base_url.split("//", 1)[-1].split("/", 1)[0].split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            if not (self.allow_free_cloud and self.model.endswith(":free")):
                raise ValueError("non-local model endpoint refused (only :free models with --allow-free-cloud)")


# ------------------------------------------------------------------ durable state

def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(data, indent=2, ensure_ascii=False, default=str))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _append(path: Path, rec: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def read_jsonl(path: Path) -> tuple[list[dict[str, Any]], int]:
    rows, bad = [], 0
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                bad += 1
    return rows, bad


def _pid_alive(pid: int) -> bool:
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:  # pragma: no cover
        return True


class Lock:
    def __init__(self, path: Path):
        self.path = path

    def acquire(self) -> None:
        if self.path.is_file():
            try:
                pid = int(self.path.read_text().split()[0])
            except (ValueError, IndexError):
                pid = -1
            if pid > 0 and pid != os.getpid() and _pid_alive(pid):
                raise RuntimeError(f"another supervisor holds the lock (pid {pid})")
        self.path.write_text(f"{os.getpid()} {time.time()}\n")

    def release(self) -> None:
        try:
            if self.path.read_text().split()[0] == str(os.getpid()):
                self.path.unlink()
        except (OSError, IndexError):
            pass


class Store:
    def __init__(self, state_dir: Path):
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.dir / "state.json"
        self.cycles = self.dir / "cycles.jsonl"
        self.lessons = self.dir / "lesson_candidates.jsonl"
        self.events = self.dir / "events.jsonl"

    def state(self) -> dict[str, Any]:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def save(self, st: dict[str, Any]) -> None:
        st["updated_at"] = time.time()
        _atomic_write(self.state_path, st)

    def event(self, kind: str, **data: Any) -> None:
        _append(self.events, {"ts": time.time(), "kind": kind, **data})

    def recover(self) -> dict[str, Any]:
        st = self.state()
        rows, _ = read_jsonl(self.cycles)
        done = {r["cycle_id"] for r in rows}
        inflight = st.get("in_progress")
        if inflight and inflight.get("cycle_id") not in done:
            _append(self.cycles, {"cycle_id": inflight["cycle_id"], "kind": inflight.get("kind"),
                                  "status": "ABANDONED_ON_RESTART", "started": inflight.get("started"),
                                  "finished": time.time(), "verifier": None})
            self.event("recovered_abandoned_cycle", cycle_id=inflight["cycle_id"])
        st["in_progress"] = None
        st["next_cycle_id"] = max([r["cycle_id"] for r in read_jsonl(self.cycles)[0]] + [0]) + 1
        st.setdefault("cursor", {k: 0 for k in CYCLE_KINDS})
        self.save(st)
        return st


# ------------------------------------------------------------------ guards

def stop_requested(cfg: Config) -> Optional[str]:
    if (cfg.state_dir / "STOP").is_file():
        return "state_stop_file"
    return None


def _owner_stop_file(cfg: Config) -> Optional[Path]:
    return (cfg.owner_data_root / "computer" / "STOP") if cfg.owner_data_root else None


def owner_halt(cfg: Config) -> Optional[str]:
    """An owner STOP pressed after this state dir was first used halts learning until «Продолжить».

    The Computer-Use STOP file can persist for days (CU deliberately off); a STOP that already
    existed before the learning loop was first started is not a new owner decision about learning.
    Every new STOP rewrites the file (new mtime), so it is honored, also across restarts."""
    path = _owner_stop_file(cfg)
    try:
        if path is not None and path.stat().st_mtime >= cfg.owner_stop_baseline:
            return "owner_computer_stop"
    except OSError:
        pass
    return None


def busy_reason(cfg: Config) -> Optional[str]:
    """Soft guard: the owner uses the GPU/model -> no LLM cycles (model-free cycles continue)."""
    if not cfg.check_busy:
        return None
    try:
        with urllib.request.urlopen(f"{OLLAMA}/api/ps", timeout=5) as resp:  # noqa: S310
            resident = [m.get("name") for m in json.loads(resp.read().decode()).get("models", [])]
        foreign = [m for m in resident if m not in ALLOWED_RESIDENT | {cfg.model}]
        if foreign:
            return f"owner_model_resident:{','.join(foreign)}"
    except OSError:
        return "ollama_unreachable"
    if cfg.busy_processes:
        try:
            import psutil
            want = {p.lower() for p in cfg.busy_processes}
            for proc in psutil.process_iter(["name"]):
                if (proc.info.get("name") or "").lower() in want:
                    return f"owner_gpu_job:{proc.info['name']}"
        except ImportError:  # pragma: no cover
            pass
    return None


def pause_reason(cfg: Config) -> Optional[str]:
    """Hard guard: PAUSE files and memory headroom -> no cycles at all."""
    for p in [PAUSE_FILE, cfg.state_dir / "PAUSE", *cfg.pause_files]:
        if p.is_file():
            return f"pause_file:{p.name}"
    try:
        import psutil
        free_gb = psutil.virtual_memory().available / 2**30
        if free_gb < cfg.min_free_gb:
            return f"low_memory:{free_gb:.1f}GB"
    except ImportError:  # pragma: no cover
        pass
    return None


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ------------------------------------------------------------------ cycles

class CallCounter:
    calls = 0
    tokens_in = 0
    tokens_out = 0


def _route_factory(route: "rl.Route"):
    from bcc.providers import OpenAICompatAdapter
    from tools.owner_journeys.bcc_harness import LocalOllamaAdapter

    base = LocalOllamaAdapter if route.tier == "local" else OpenAICompatAdapter

    class Counting(base):  # type: ignore[misc, valid-type]
        async def chat(self, model, messages, **kw):
            CallCounter.calls += 1
            if route.tier != "local":
                kw.setdefault("max_tokens", 800)
            res = await super().chat(model, messages, **kw)
            CallCounter.tokens_in += int(res.tokens_in or 0)
            CallCounter.tokens_out += int(res.tokens_out or 0)
            return res

    return lambda model, provider: Counting(base_url=route.base_url, api_key=route.api_key)


def _route_prices(route: "rl.Route") -> dict[str, float]:
    """bcc provider governance refuses cloud inference without known prices (per 1M tokens)."""
    if route.tier == "local":
        return {}
    return {"price_in": float(route.price_in_per_m), "price_out": float(route.price_out_per_m)}


def _route_meta(route: "rl.Route", ladder: "rl.LadderConfig") -> dict[str, Any]:
    if route.tier == "local":
        return {"cloud_allowed": False, "privacy": "private", "learning247_tier": "local"}
    # fake, privacy-safe tasks only; bcc cloud_policy needs strict True and a positive budget
    return {"cloud_allowed": True, "cloud_budget_usd": max(ladder.per_cycle_cap_usd, 1e-6), "privacy": "public",
            "learning247_tier": route.tier}


def _policy_violations(tool_calls: list[dict[str, Any]], allowed: list[str]) -> list[str]:
    out = []
    for r in tool_calls:
        name = str(r.get("tool"))
        if r.get("status") == "executed" and name not in allowed:
            out.append(f"executed_outside_allowlist:{name}")
    for name in allowed:
        if any(w in name.lower() for w in FORBIDDEN_TOOL_WORDS):
            out.append(f"forbidden_tool_granted:{name}")
    return out


async def cycle_triage(cfg: Config, work: Path, index: int, route: "rl.Route", ladder: "rl.LadderConfig"
                       ) -> dict[str, Any]:
    from tools.owner_journeys import learning_lab as lab
    from tools.owner_journeys import triage_dataset as ds
    from tools.owner_journeys.bcc_harness import BccHarness

    from tools.owner_journeys import lesson_pipeline as lp

    active = lp.Registry(cfg.state_dir).active()        # owner-approved lessons only
    taught = {lp._norm(a["text"]) for a in active}
    for step in range(len(ds.HELD_OUT)):                # never score an item whose answer is in the prompt
        if lp._norm(ds.HELD_OUT[(index + step) % len(ds.HELD_OUT)]["text"]) not in taught:
            index += step
            break
    item = ds.HELD_OUT[index % len(ds.HELD_OUT)]
    system = lab.system_prompt("candidate", active) if active else lab.system_prompt("baseline", [])
    async with BccHarness(work / "bcc", adapter_factory=_route_factory(route)) as h:
        agent = await h.agent(name="triage-lessons" if active else "triage-baseline", system_prompt=system,
                              tools=[],
                              model_name=route.model, base_url=route.base_url, max_steps=2,
                              api_key=route.api_key or "local-no-key", **_route_prices(route))
        out = await h.run_task(agent_id=agent["id"], title="learning247-triage",
                               prompt=f"Inbound message:\n{item['text']}\nReturn only the JSON.",
                               allowed_tools=[], timeout=cfg.cycle_timeout_s, meta=_route_meta(route, ladder))
    got = lab.parse_answer(out.result)
    sc = ds.score(item, got)
    return {"task": {"item": index % len(ds.HELD_OUT), "text": item["text"]}, "task_status": out.status,
            "verifier": {"pass": sc["exact"], "safety_ok": sc["safety_ok"], "fields": sc["fields"]},
            "expected": {k: item[k] for k in ds.FIELDS}, "got": got, "lessons_active": len(active),
            "violations": _policy_violations(out.tool_calls, []), "seconds": out.seconds}


async def cycle_journey(cfg: Config, work: Path, index: int, route: "rl.Route", ladder: "rl.LadderConfig"
                        ) -> dict[str, Any]:
    from tools.owner_journeys import admin_journeys as aj

    journeys = aj.all_journeys()
    j = journeys[index % len(journeys)]
    rep = await aj.run_journeys(work, [j], adapter_factory=_route_factory(route), model=route.model,
                                timeout=cfg.cycle_timeout_s, base_url=route.base_url, api_key=route.api_key,
                                meta=_route_meta(route, ladder), prices=_route_prices(route))
    r = rep["journeys"][0]
    allowed = aj.SWAPME_TOOLS if j.business == "swapme" else aj.FV_TOOLS
    calls = [{"tool": t, "status": s} for t, s in r["tool_sequence"]]
    # "served" = the product task finished as the journey expects (an owner-rejection journey ends
    # the task as failed BY DESIGN); only then is a fall-through to the next tier pointless.
    served = r["steps"][0]["status"] == "PASS"
    return {"task": {"journey": j.jid}, "task_status": "completed" if served else r["steps"][0]["evidence"]["status"],
            "product_task_status": r["steps"][0]["evidence"]["status"],
            "verifier": {"pass": r["status"] == "PASS", "safety_ok": all(
                s["status"] == "PASS" for s in r["steps"] if "outbound" in s["step"] or "dosage" in s["step"]),
                "failed_steps": [s["step"] for s in r["steps"] if s["status"] != "PASS"]},
            "violations": _policy_violations(calls, allowed), "seconds": r["seconds"]}


async def cycle_k1m6a(cfg: Config, work: Path, index: int, route: "rl.Route" = None,
                      ladder: "rl.LadderConfig" = None) -> dict[str, Any]:
    """Deterministic re-verification of stored K1m6a claims against cached exchange data (no model)."""
    from learning.claim_verification import Claim, Market, verify_claim

    runs = sorted(p for p in cfg.k1m6a_root.glob("*") if (p / "claims.jsonl").is_file() and (p / "market.json").is_file())
    if not runs:
        return {"task": {"k1m6a": None}, "task_status": "skipped", "verifier": {"pass": True, "safety_ok": True,
                                                                              "note": "no backlog"},
                "violations": [], "seconds": 0.0}
    run = runs[index % len(runs)]
    t = time.time()
    mk = json.loads((run / "market.json").read_text(encoding="utf-8"))
    market = Market(mk["klines"], mk["oi"])
    rows = [json.loads(x) for x in (run / "claims.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    fields = Claim.__dataclass_fields__
    mismatch = 0
    for r in rows:
        c = Claim(**{k: r[k] for k in fields if k in r})
        if verify_claim(c, market) != r["verification"]:
            mismatch += 1
    return {"task": {"k1m6a": run.name, "claims": len(rows)}, "task_status": "completed",
            "verifier": {"pass": mismatch == 0, "safety_ok": True, "mismatches": mismatch},
            "violations": [], "seconds": round(time.time() - t, 2)}


async def run_lesson_ab(cfg: Config, hooks: Any, lesson: dict[str, Any], ladder: "rl.LadderConfig"
                        ) -> dict[str, Any]:
    """Offline A/B of ONE quarantined lesson on held-out items: local model only, $0, no promotion."""
    from tools.owner_journeys import lesson_pipeline as lp

    model = ladder.local_models[0] if ladder.local_models else cfg.model
    ask = AB_ASK or lp.ollama_ask(model)
    ab_cfg = hooks.goal["ab"]
    t = time.time()
    ab = await lp.run_ab(lesson, hooks.registry.active(), ask, repeats=int(ab_cfg["repeats"]),
                         min_gain_items=int(ab_cfg["min_gain_items"]))
    calls = sum(2 * r["n"] for r in ab["repeats"])
    CallCounter.calls += calls
    errors = sum(r["errors"] for r in ab["repeats"])
    if errors > calls // 4:
        verdict = "INCOMPLETE"          # model unavailable: the lesson stays quarantined for a later A/B
    else:
        verdict = hooks.ab_finished(lesson["id"], ab)
    return {"task": {"lesson": lesson["id"]}, "task_status": "completed",
            "verifier": {"pass": True, "safety_ok": True, "ab_verdict": verdict,
                         "deltas": [r["delta"] for r in ab["repeats"]], "n": ab["n"], "errors": errors},
            "violations": [], "seconds": round(time.time() - t, 2),
            "route": {"tier": "local", "model": model, "usd": 0.0,
                      "tried": [{"tier": "local", "model": model, "status": "completed", "usd": 0.0}]}}


RUNNERS = {"triage": cycle_triage, "journey": cycle_journey, "k1m6a_verify": cycle_k1m6a}
NEEDS_LLM = {"triage": True, "journey": True, "k1m6a_verify": False}


async def run_ladder(cfg: Config, kind: str, work: Path, index: int, cycle_id: int, ladder: "rl.LadderConfig",
                     cap: "rl.CapLedger") -> dict[str, Any]:
    """Try routes cheapest-first; stop at the first route that SERVES the task (not at a right answer)."""
    needs = NEEDS_LLM.get(kind, True)
    routes = rl.plan(ladder, needs_llm=needs, skip_local=cfg.force_tier in ("free_cloud", "max_cloud"))
    if cfg.force_tier and needs:
        routes = [r for r in routes if r.tier == cfg.force_tier]
    tried: list[dict[str, Any]] = []
    result: dict[str, Any] = {}
    served = None
    for route in routes:
        if route.tier == "max_cloud":
            free_failed = any(t["tier"] == "free_cloud" for t in tried)
            if not (free_failed or cfg.force_tier == "max_cloud"):
                tried.append({"tier": route.tier, "model": route.model, "status": "SKIPPED_FREE_NOT_TRIED"})
                continue
            ok, why = cap.reserve(cycle_id, route.reserve_usd)
            if not ok:
                tried.append({"tier": route.tier, "model": route.model, "status": "CAP_BLOCKED", "why": why})
                continue
        t_in, t_out = CallCounter.tokens_in, CallCounter.tokens_out
        try:
            result = await RUNNERS[kind](cfg, work / route.tier, index, route, ladder)
            status = result.get("task_status")
        except Exception as exc:  # noqa: BLE001 - a failed tier falls through to the next tier
            status, result = f"ERROR:{type(exc).__name__}", {}
        usd = rl.actual_cost(route, CallCounter.tokens_in - t_in, CallCounter.tokens_out - t_out)
        if route.tier == "max_cloud":
            cap.settle(cycle_id, route.reserve_usd, usd)
        if route.tier in ("free_cloud", "max_cloud"):
            import shutil
            shutil.rmtree(work / route.tier / "bcc", ignore_errors=True)  # the provider key lived in that vault
        tried.append({"tier": route.tier, "model": route.model, "status": status, "usd": usd})
        if status in ("completed", "skipped") or route.tier == "deterministic":
            served = route
            break
    result = dict(result)
    result["route"] = {"tier": served.tier if served else "NONE", "model": served.model if served else None,
                       "tried": tried, "usd": round(sum(t.get("usd", 0.0) for t in tried), 9)}
    if served is None:
        result["verifier"] = {"pass": False, "safety_ok": True, "note": "no tier could serve"}
        result.setdefault("violations", [])
        result["task_status"] = "NO_ROUTE"
    return result


# ------------------------------------------------------------------ main loop

async def _hooks_tick(hooks: Any, force: Optional[str] = None) -> None:
    if hooks is not None:
        await asyncio.to_thread(hooks.tick, force)


async def _wait_while_paused(cfg: Config, store: Store, hooks: Any = None) -> Optional[str]:
    halt = owner_halt(cfg)
    reason = halt or pause_reason(cfg)
    if reason:
        st = store.state()
        st["mode"], st["pause_reason"] = ("HALTED_BY_OWNER" if halt else "PAUSED"), reason
        store.save(st)
        store.event("owner_halt" if halt else "paused", reason=reason)
        await _hooks_tick(hooks, "owner_stop" if halt else None)
    was_halt = bool(halt)
    while reason:
        if stop_requested(cfg):
            return "stop"
        await asyncio.sleep(cfg.poll_s)
        await _hooks_tick(hooks)
        halt = owner_halt(cfg)
        reason = halt or pause_reason(cfg)
        if not reason:
            store.event("owner_resumed" if was_halt else "resumed")
    return None


def _choose_kind(cfg: Config, next_cycle_id: int, busy: Optional[str]) -> Optional[str]:
    n = len(cfg.kinds)
    for step in range(n):
        kind = cfg.kinds[(next_cycle_id - 1 + step) % n]
        if not busy or not NEEDS_LLM.get(kind, True):
            return kind
    return None


async def run(cfg: Config, *, max_hours: float = 0.0, max_cycles: int = 0) -> dict[str, Any]:
    cfg.validate()
    rl.AnthropicBlock.install()
    store = Store(cfg.state_dir)
    ladder = rl.LadderConfig.load(cfg.state_dir)
    cap = rl.CapLedger(cfg.state_dir / "cloud_cap.json", ladder)
    lock = Lock(cfg.state_dir / "supervisor.lock")
    lock.acquire()
    started = time.time()
    hooks = None
    try:
        from tools.owner_journeys.goal_reporter import LoopHooks
        hooks = LoopHooks(cfg.state_dir, transport=cfg.report, approvals=cfg.approvals, event=store.event)
    except Exception as exc:  # noqa: BLE001 - learning continues without reports
        store.event("hooks_error", error=f"{type(exc).__name__}: {str(exc)[:200]}")
    try:
        st = store.recover()
        if "owner_stop_baseline" not in st:
            path = _owner_stop_file(cfg)
            st["owner_stop_baseline"] = (path.stat().st_mtime + 0.001) if path and path.is_file() else 0.0
        cfg.owner_stop_baseline = float(st["owner_stop_baseline"])
        st.update({"pid": os.getpid(), "mode": "RUNNING", "session_started": started, "model": cfg.model,
                   "priority": lower_priority(), "stop_reason": None})
        store.save(st)
        store.event("session_start", pid=os.getpid(), model=cfg.model, report=cfg.report)
        await _hooks_tick(hooks)
        done_this_session = 0
        while True:
            why = stop_requested(cfg)
            if why:
                st = store.state()
                st.update({"mode": "STOPPED", "stop_reason": why})
                store.save(st)
                store.event("stopped", reason=why)
                await _hooks_tick(hooks, "stop")
                break
            if max_hours and time.time() - started >= max_hours * 3600:
                break
            if max_cycles and done_this_session >= max_cycles:
                break
            if await _wait_while_paused(cfg, store, hooks) == "stop":
                continue
            st = store.state()
            # a forced cloud tier (the readiness free-call test) does not touch the local GPU
            busy = None if cfg.force_tier in ("free_cloud", "max_cloud") else await asyncio.to_thread(busy_reason, cfg)
            if busy != st.get("busy_reason"):
                store.event("owner_busy" if busy else "owner_free", reason=busy or st.get("busy_reason"))
                st["busy_reason"] = busy
                store.save(st)
            budget = st.setdefault("budget", {})
            day = budget.setdefault(_today(), {"cycles": 0, "model_calls": 0, "wall_s": 0.0, "cloud_usd": 0.0})
            if (day["cycles"] >= cfg.max_cycles_day or day["model_calls"] >= cfg.max_model_calls_day
                    or day["wall_s"] >= cfg.max_wall_s_day):
                st["mode"] = "BUDGET_EXHAUSTED"
                store.save(st)
                store.event("budget_exhausted", day=_today(), **day)
                await asyncio.sleep(cfg.poll_s)
                continue
            lesson = hooks.ab_due(st["next_cycle_id"]) if (hooks is not None and not busy) else None
            kind = "lesson_ab" if lesson else _choose_kind(cfg, st["next_cycle_id"], busy)
            if kind is None:                     # owner busy and every kind needs the model
                if st.get("mode") != "BUSY":
                    st["mode"] = "BUSY"
                    store.save(st)
                await asyncio.sleep(cfg.poll_s)
                await _hooks_tick(hooks)
                continue
            index = st["cursor"].get(kind, 0)
            cycle_id = st["next_cycle_id"]
            work = cfg.state_dir / "cycles" / f"{cycle_id:06d}-{kind}"
            st["in_progress"] = {"cycle_id": cycle_id, "kind": kind, "index": index, "started": time.time()}
            st["mode"] = "BUSY" if busy else "RUNNING"
            store.save(st)
            calls_before = CallCounter.calls
            t0 = time.time()
            status, result, error = "COMPLETED", {}, None
            if lesson:
                hooks.ab_started(cycle_id)
                task = asyncio.create_task(run_lesson_ab(cfg, hooks, lesson, ladder))
            else:
                task = asyncio.create_task(run_ladder(cfg, kind, work, index, cycle_id, ladder, cap))
            try:
                while not task.done():
                    await asyncio.wait({task}, timeout=cfg.poll_s)
                    if (stop_requested(cfg) or owner_halt(cfg)) and not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        status = "ABORTED_BY_STOP"
                        break
                if status == "COMPLETED":
                    result = task.result()
            except Exception as exc:  # noqa: BLE001 — a failed cycle is recorded, the loop survives
                status, error = "ERROR", f"{type(exc).__name__}: {str(exc)[:300]}"
            wall = round(time.time() - t0, 2)
            calls = CallCounter.calls - calls_before
            try:
                import psutil
                rss_mb = round(psutil.Process().memory_info().rss / 2**20, 1)
            except ImportError:  # pragma: no cover
                rss_mb = None
            route_info = result.pop("route", None) or {"tier": "NONE", "tried": [], "usd": 0.0}
            rec = {"cycle_id": cycle_id, "kind": kind, "index": index, "status": status, "started": t0,
                   "rss_mb": rss_mb, "finished": time.time(), "wall_s": wall, "model_calls": calls,
                   "cloud_usd": route_info.get("usd", 0.0), "tier": route_info.get("tier"),
                   "tier_model": route_info.get("model"), "route_tried": route_info.get("tried"),
                   "anthropic_attempts_total": rl.AnthropicBlock.attempts, "error": error, **result}
            _append(store.cycles, rec)
            if result and not result.get("verifier", {}).get("pass", True):
                _append(store.lessons, {"cycle_id": cycle_id, "kind": kind, "task": result.get("task"),
                                        "expected": result.get("expected"), "got": result.get("got"),
                                        "failed": result.get("verifier"), "status": "CANDIDATE_QUARANTINED",
                                        "promotion": "FORBIDDEN_AUTOMATIC"})
            st = store.state()
            day = st.setdefault("budget", {}).setdefault(_today(), {"cycles": 0, "model_calls": 0, "wall_s": 0.0,
                                                                   "cloud_usd": 0.0})
            day["cycles"] += 1
            day["model_calls"] += calls
            day["cloud_usd"] = round(day.get("cloud_usd", 0.0) + rec["cloud_usd"], 9)
            day["wall_s"] = round(day["wall_s"] + wall, 1)
            st["in_progress"] = None
            st["next_cycle_id"] = cycle_id + 1
            if status == "COMPLETED" and kind != "lesson_ab":
                st["cursor"][kind] = index + 1
            st["last_cycle"] = {"cycle_id": cycle_id, "status": status, "pass": result.get("verifier", {}).get("pass")}
            store.save(st)
            done_this_session += 1
            print(json.dumps({"cycle": cycle_id, "kind": kind, "status": status,
                              "pass": result.get("verifier", {}).get("pass"), "s": wall, "calls": calls}), flush=True)
            await _hooks_tick(hooks)
            if status == "ABORTED_BY_STOP":
                continue
            await asyncio.sleep(cfg.idle_between_cycles_s)
        st = store.state()
        if st.get("mode") != "STOPPED":
            st["mode"] = "EXITED"
        st["session_ended"] = time.time()
        store.save(st)
        store.event("session_end", cycles=done_this_session)
        return st
    finally:
        lock.release()


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state-dir", default=r"C:\Users\asd\Bossman\rc19-data\d-learn\learning247")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--owner-data-root", default="", help="watch <root>/computer/STOP (owner STOP button)")
    ap.add_argument("--kinds", default=",".join(CYCLE_KINDS))
    ap.add_argument("--max-hours", type=float, default=0.0)
    ap.add_argument("--max-cycles", type=int, default=0)
    ap.add_argument("--min-free-gb", type=float, default=12.0)
    ap.add_argument("--max-cycles-day", type=int, default=400)
    ap.add_argument("--max-model-calls-day", type=int, default=3000)
    ap.add_argument("--idle-s", type=float, default=10.0)
    ap.add_argument("--poll-s", type=float, default=5.0)
    ap.add_argument("--force-tier", default="", choices=("", "local", "free_cloud", "max_cloud"))
    ap.add_argument("--report", default="telegram", choices=("telegram", "off"),
                    help="goal reports to the owner's пульт (companion bot); off = send nothing")
    ap.add_argument("--no-busy-check", action="store_true", help="tests only: ignore the owner-busy guard")
    args = ap.parse_args(argv)
    cfg = Config(state_dir=Path(args.state_dir), model=args.model,
                 owner_data_root=Path(args.owner_data_root) if args.owner_data_root else None,
                 kinds=tuple(k for k in args.kinds.split(",") if k in RUNNERS), min_free_gb=args.min_free_gb,
                 max_cycles_day=args.max_cycles_day, max_model_calls_day=args.max_model_calls_day,
                 idle_between_cycles_s=args.idle_s, poll_s=args.poll_s, force_tier=args.force_tier,
                 report=args.report, check_busy=not args.no_busy_check,
                 approvals="auto" if args.report == "telegram" else None)
    st = asyncio.run(run(cfg, max_hours=args.max_hours, max_cycles=args.max_cycles))
    print(json.dumps({"mode": st.get("mode"), "next_cycle_id": st.get("next_cycle_id")}))
    return 0


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())
