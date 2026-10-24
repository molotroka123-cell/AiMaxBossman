"""The orchestrator: the only code that creates tasks, and the bounded scheduler tick.

A tick (triggered every 30 min by a Windows Scheduled Task) does bounded work and exits:
  1. not started / stopped -> no-op;
  2. deadline (start + exactly 28 days) reached -> refuse new work, write reports/final.md, mark stopped;
  3. take the tick lease (one tick at a time), recover running tasks whose lease expired (crash / restart);
  4. check budget (free-call budget, paid cap) and queue state, log them;
  5. plan every run day up to today that is not planned yet (catch-up after the PC was off);
  6. claim and run at most ``max_tasks_per_tick`` tasks within ``max_tick_seconds`` (DB-enforced cap of 4
     concurrently running tasks across processes);
  7. rewrite the day / week reports it touched and write improvement proposals (never applied).
"""
from __future__ import annotations

import os
import socket
import time
from typing import Callable

from niche_research import MAX_RUNNING_TASKS, RUN_DAYS
from niche_research.config import Config, load_config
from niche_research.models import (ModelError, ModelRouter, NoModelAvailable, PaidSpendRefused,
                                   build_default_clients, utc_day_start)
from niche_research.plan import NICHE_PIPELINE, WEEKS, day_index, day_slot, deadline_ts
from niche_research.reports import (ReportWriter, selected_hypotheses, week_niche_rows)
from niche_research.roles import ROLE_FUNCS, AgentContext, RoleError, run_weekly
from niche_research.store import Store
from niche_research.web import DuckDuckGoHtmlSearch, PoliteFetcher, ResearchLog

TICK_LEASE = "tick_lease_until"


class Orchestrator:
    def __init__(self, config: Config | None = None, *, store: Store | None = None, router: ModelRouter | None = None,
                 fetcher: PoliteFetcher | None = None, search=None, clock: Callable[[], float] = time.time,
                 sleeper: Callable[[float], None] = time.sleep, worker_id: str | None = None):
        self.config = config or load_config()
        self.clock = clock
        self.store = store or Store(self.config.db_path)
        self._key = self.store.issue_orchestrator_key()
        self.research_log = ResearchLog(self.store, self.config.run_dir, clock)
        self._router = router
        self._fetcher = fetcher
        self._search = search
        self._sleeper = sleeper
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
        self.reports = ReportWriter(self.store, self.config, clock)

    # ---- lazy dependencies (so `status` never touches the network) -------------------------------------------
    @property
    def router(self) -> ModelRouter:
        if self._router is None:
            self._router = ModelRouter(self.store, self.config, build_default_clients(self.config), self.clock)
        return self._router

    @property
    def fetcher(self) -> PoliteFetcher:
        if self._fetcher is None:
            self._fetcher = PoliteFetcher(self.store, self.research_log, clock=self.clock, sleeper=self._sleeper,
                                          allow_private=bool(self.config.get("allow_private_hosts")))
        return self._fetcher

    @property
    def search(self):
        if self._search is None:
            self._search = DuckDuckGoHtmlSearch(self.fetcher, int(self.config.get("search_results_per_query", 5)))
        return self._search

    # ---- lifecycle -------------------------------------------------------------------------------------------
    @property
    def start_ts(self) -> float | None:
        return self.store.get_meta("start_ts")

    def start(self) -> dict:
        now = self.clock()
        if not self.store.set_meta_if_absent("start_ts", now):
            return {"ok": False, "error": "already started", "start_ts": self.start_ts}
        self.store.log("start", {"deadline_ts": deadline_ts(now)}, now=now)
        return {"ok": True, "start_ts": now, "deadline_ts": deadline_ts(now)}

    def stop(self, reason: str = "manual") -> dict:
        if self.start_ts is None:
            return {"ok": False, "error": "not started"}
        if self.store.get_meta("stopped"):
            return {"ok": True, "already_stopped": True, "stopped": self.store.get_meta("stopped")}
        return self._finish(reason)

    def _finish(self, reason: str) -> dict:
        now = self.clock()
        path = self.reports.write_final(reason)
        info = {"reason": reason, "ts": now, "final": str(path)}
        self.store.set_meta("stopped", info)
        self.store.log("stopped", info, now=now)
        return {"ok": True, "stopped": info}

    def status(self) -> dict:
        start = self.start_ts
        now = self.clock()
        out = {"data_dir": str(self.config.data_dir), "started": start is not None,
               "stopped": self.store.get_meta("stopped"), "queue": self.store.counts(),
               "paid_spend_usd": round(self.store.paid_spend(), 6), "total_spend_usd": round(self.store.total_spend(), 6),
               "paid_daily_limit_usd": self.config.paid_daily_limit, "errors": len(self.store.errors(1000)),
               "max_running_tasks": MAX_RUNNING_TASKS}
        if start is not None:
            out.update({"start_ts": start, "deadline_ts": deadline_ts(start),
                        "day": min(day_index(start, now), RUN_DAYS + 1),
                        "days_left": max(0.0, round((deadline_ts(start) - now) / 86400, 2))})
        return out

    # ---- planning (orchestrator only) ------------------------------------------------------------------------
    def plan_until(self, day: int) -> int:
        start = self.start_ts
        created = 0
        tz = self.config.get("tz_offset_hours")
        for d in range(1, min(day, RUN_DAYS) + 1):
            existing = {t["key"] for t in self.store.tasks(day=d)}
            slot = day_slot(start, d, tz)
            if slot.kind == "weekly":
                key = f"d{d:02d}:weekly"
                self.store.enqueue(self._key, task_key=key, day=d, week=slot.week, niche=f"неделя {slot.week}",
                                   role="weekly", payload={"date": slot.date, "kind": "weekly"}, now=self.clock())
                created += 0 if key in existing else 1
                continue
            prev = None
            for role in NICHE_PIPELINE:
                key = f"d{d:02d}:{role}"
                existed = key in existing
                prev = self.store.enqueue(self._key, task_key=key, day=d, week=slot.week, niche=slot.niche, role=role,
                                          depends_on=prev, payload={"date": slot.date, "kind": "niche"},
                                          now=self.clock())
                created += 0 if existed else 1
        return created

    # ---- tick ------------------------------------------------------------------------------------------------
    def tick(self) -> dict:
        now = self.clock()
        start = self.start_ts
        if start is None:
            return {"state": "not_started"}
        if self.store.get_meta("stopped"):
            return {"state": "stopped", "stopped": self.store.get_meta("stopped")}
        if now >= deadline_ts(start):
            self.store.log("deadline", {"now": now, "deadline_ts": deadline_ts(start)}, now=now)
            return {"state": "stopped", **self._finish("deadline")}
        max_secs = float(self.config.get("max_tick_seconds", 1500))
        if not self.store.acquire_lease(TICK_LEASE, now, max_secs + 120):
            return {"state": "busy", "note": "another tick holds the lease"}
        try:
            return self._tick_body(start, now, max_secs)
        finally:
            self.store.release_lease(TICK_LEASE)

    def _tick_body(self, start: float, now: float, max_secs: float) -> dict:
        max_attempts = int(self.config.get("max_attempts", 3))
        recovered = self.store.recover_stale(now, max_attempts)
        today = day_index(start, now)
        created = self.plan_until(today)
        budget = self.budget_state()
        self.store.log("tick", {"day": today, "recovered": recovered, "created": created, "queue": self.store.counts(),
                                "budget": budget}, now=now)
        lease = float(self.config.get("task_lease_minutes", 25)) * 60
        processed: list[dict] = []
        touched_days: set[int] = set()
        stop_reason = ""
        t0 = self.clock()
        while len(processed) < int(self.config.get("max_tasks_per_tick", 6)):
            if self.clock() >= deadline_ts(start):
                stop_reason = "deadline"
                break
            if self.clock() - t0 >= max_secs:
                stop_reason = "tick time budget used"
                break
            if not budget["model_calls_possible"]:
                stop_reason = "no model budget (free calls used, paid off, no local model)"
                break
            task = self.store.claim_next(self.worker_id, self.clock(), lease)
            if task is None:
                stop_reason = "no claimable task (queue empty, waiting on dependencies, or cap of 4 running)"
                break
            outcome = self.run_task(task)
            processed.append({"id": task["id"], "key": task["key"], "outcome": outcome})
            touched_days.add(task["day"])
            if outcome == "no_model":
                stop_reason = "no model available right now"
                break
            budget = self.budget_state()
        if today <= RUN_DAYS:
            touched_days.add(today)
        for d in sorted(touched_days):
            self.reports.write_day(d)
            self.reports.write_proposals_if_complete(d)
        for w in sorted({(d - 1) // 7 + 1 for d in touched_days}):
            self.reports.write_week(w)
        result = {"state": "ran", "day": today, "recovered": recovered, "created": created,
                  "processed": processed, "stop_reason": stop_reason, "queue": self.store.counts()}
        if stop_reason == "deadline":
            result.update(self._finish("deadline"))
            result["state"] = "stopped"
        self.store.log("tick_end", {k: v for k, v in result.items() if k != "processed"}, now=self.clock())
        return result

    def budget_state(self) -> dict:
        router = self.router
        free_left = router.free_budget_left()
        paid_limit = self.config.paid_daily_limit
        paid_today = self.store.paid_spend(since=utc_day_start(self.clock()))
        local = free_cloud = False
        for c in router.clients:
            if c.paid:
                continue
            try:
                up = bool(c.available())
            except Exception:  # noqa: BLE001 - a failing probe means "not available", never a crash
                up = False
            if c.provider in ("ollama", "fake"):
                local = local or up
            else:
                free_cloud = free_cloud or up
        free_left = free_left and free_cloud
        paid_ok = paid_limit > 0 and paid_today < paid_limit and any(c.paid for c in router.clients)
        return {"free_calls_left": free_left, "local_available": local, "paid_limit_usd": paid_limit,
                "paid_today_usd": round(paid_today, 6), "paid_possible": paid_ok,
                "model_calls_possible": bool(free_left or local or paid_ok)}

    # ---- task execution --------------------------------------------------------------------------------------
    def _prior(self, day: int) -> dict:
        prior: dict = {}
        for r in self.store.results(day=day):
            prior[r["role"]] = r["content"]
            if r["role"] == "draft" and r["content"].get("sample_path"):
                try:
                    prior["draft_markdown"] = (self.config.run_dir / r["content"]["sample_path"]).read_text(
                        encoding="utf-8")
                except OSError:
                    pass
        return prior

    def _context(self, task: dict) -> AgentContext:
        router, tid, role = self.router, task["id"], task["role"]

        def ask(system, prompt, exclude=None):
            return router.complete(system, prompt, task_id=tid, role=role, exclude=exclude)

        seeds = (self.config.get("seed_urls") or {}).get(task["niche"], [])
        return AgentContext(task=task, _ask=ask, _search=self.search, _fetch=lambda u: self.fetcher.fetch(u, tid),
                            _note=self.research_log.record, prior=self._prior(task["day"]),
                            region=str(self.config.get("region") or ""), seed_urls=list(seeds),
                            max_pages=int(self.config.get("max_pages_per_research", 3)))

    def run_task(self, task: dict) -> str:
        now = self.clock()
        max_attempts = int(self.config.get("max_attempts", 3))
        try:
            ctx = self._context(task)
            if task["role"] == "weekly":
                out = self._run_weekly(ctx, task)
            else:
                out = ROLE_FUNCS[task["role"]](ctx)
            content = dict(out.content)
            if out.sample_markdown:
                rel = f"samples/day-{task['day']:02d}-sample.md"
                (self.config.run_dir / rel).write_text(out.sample_markdown, encoding="utf-8")
                content["sample_path"] = rel
            self.store.add_result(task, out.models, out.tools, content, self.clock())
            self.store.finish_task(task["id"], self.clock())
            return "done"
        except (NoModelAvailable, PaidSpendRefused) as exc:
            # Not the task's fault: give the attempt back and wait for the next tick.
            self.store.log_error("router", str(exc), task_id=task["id"], now=now)
            self.store.release_task(task["id"])
            return "no_model"
        except (RoleError, ModelError, KeyError, ValueError, OSError) as exc:
            self.store.log_error(f"task:{task['role']}", f"{type(exc).__name__}: {exc}", task_id=task["id"], now=now)
            return self.store.fail_task(task["id"], f"{type(exc).__name__}: {exc}", self.clock(), max_attempts)

    def _run_weekly(self, ctx: AgentContext, task: dict):
        week = task["week"]
        rows = week_niche_rows(self.store, week)
        summary = "\n".join(f"- {r['niche']}: оценка {r['total']}/25; оффер: {r['offer'].get('routine_task', '')}; "
                            f"слабые места: {'; '.join(r['weak'][:2])}" for r in rows) or "- данных пока нет"
        if rows:
            out = run_weekly(ctx, WEEKS[week]["theme"], summary)
        else:
            from niche_research.roles import RoleOutput
            out = RoleOutput({"rankings": {}, "models_compared": 0}, [], [])
        out.content["selected"] = [r["niche"] for r in selected_hypotheses(rows)]
        out.content["niches_seen"] = [r["niche"] for r in rows]
        return out
