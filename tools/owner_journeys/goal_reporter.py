#!/usr/bin/env python3
"""Goal-based progress reports of the 24/7 learning loop to the owner's «Пульт» (Russian).

The owner's goal and measurable targets live in ``<state>/goal.json`` (created with
defaults on first run; the owner edits it). The supervisor calls ``LoopHooks.tick()``
between cycles; a report is queued:

* on the first run («старт»), then every ``report_every_h`` hours (default 6);
* at once on a state change: readiness READY <-> NOT_READY, owner STOP / STOP file,
  daily $ cap reached, an error streak, a lesson waiting for the owner's approval,
  a lesson promoted or rejected.

Delivery goes through ``owner_notify.Notifier`` (durable outbox, rate limit, dedup,
secret refusal, works offline). Nothing is sent to the participant (Jeff) bot.

    python tools/owner_journeys/goal_reporter.py --state-dir <state> preview     # print, send nothing
    python tools/owner_journeys/goal_reporter.py --state-dir <state> send-now    # queue + deliver one report
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.owner_journeys import lesson_pipeline as lp  # noqa: E402
from tools.owner_journeys.owner_notify import Limits, Notifier, make_transport  # noqa: E402

DEFAULT_GOAL: dict[str, Any] = {
    "goal": ("Саморазвивающийся Боссман: круглосуточно решает учебные задачи на своих моделях без Claude, "
             "находит свои ошибки, проверяет уроки A/B и предлагает владельцу только доказанные улучшения."),
    "targets": {
        "triage_pass_rate_min": 0.90,
        "uptime_min": 0.80,
        "max_error_streak": 3,
        "lessons_proposed_per_week_min": 1,
        "cost_within_daily_cap": True,
    },
    "report_every_h": 6,
    "min_interval_s": 600,
    "max_reports_per_day": 12,
    "readiness_every_s": 1800,
    "approval_core_url": "",
    "ab": dict(lp.DEFAULT_AB),
}
URGENT = {"readiness_flip", "stop", "owner_stop", "cap", "errors", "approval", "lesson_decided", "manual"}
MODE_RU = {
    "RUNNING": "работает",
    "PAUSED": "на паузе",
    "BUSY": "владелец занят (GPU/модель) — идут только задачи без модели",
    "HALTED_BY_OWNER": "СТОП владельца — ждёт «Продолжить»",
    "STOPPED": "остановлен (STOP)",
    "EXITED": "процесс завершён",
    "BUDGET_EXHAUSTED": "дневной лимит циклов исчерпан",
}
CRITERIA_RU = {
    "consecutive_unattended_cycles": "мало циклов подряд (нужно ≥ 12)",
    "unattended_hours": "мало часов без присмотра (нужно ≥ 1 ч)",
    "triage_pass_rate_vs_lab_baseline": "нет или низкий % разбора входящих",
    "pause_honored_s": "не пройдена проверка паузы",
    "stop_honored_s": "не пройдена проверка СТОП",
    "restart_safe": "не пройдена проверка перезапуска",
    "real_free_call": "нет проверенного бесплатного облачного вызова",
    "cap_enforcement_fake_test": "не проверен лимит $",
    "ladder_telemetry": "есть циклы без маршрута",
    "cycle_errors": "есть упавшие циклы",
    "policy_violations": "нарушения политики",
    "no_auto_promotion": "урок применён без вашего решения",
}
REASON_RU = {
    "start": "старт", "schedule": "плановый", "readiness_flip": "смена готовности", "stop": "СТОП",
    "owner_stop": "СТОП владельца", "cap": "достигнут лимит $", "errors": "серия ошибок",
    "approval": "нужно ваше решение", "lesson_decided": "решение по уроку", "manual": "по запросу",
}


def load_goal(state_dir: Path) -> dict[str, Any]:
    path = Path(state_dir) / "goal.json"
    if not path.is_file():
        Path(state_dir).mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(DEFAULT_GOAL, ensure_ascii=False, indent=2), encoding="utf-8")
        return json.loads(json.dumps(DEFAULT_GOAL))
    raw = json.loads(path.read_text(encoding="utf-8"))
    goal = json.loads(json.dumps(DEFAULT_GOAL))
    for k, v in raw.items():
        if isinstance(v, dict) and isinstance(goal.get(k), dict):
            goal[k].update(v)
        else:
            goal[k] = v
    if float(goal["report_every_h"]) < 1 or int(goal["max_reports_per_day"]) > 48:
        raise ValueError("goal.json: report_every_h >= 1 and max_reports_per_day <= 48")
    return goal


def tail_jsonl(path: Path, since: float = 0.0, key: str = "ts", max_bytes: int = 8 * 2**20) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    size = path.stat().st_size
    with path.open("rb") as fh:
        fh.seek(max(0, size - max_bytes))
        chunk = fh.read()
    lines = chunk.decode("utf-8", errors="replace").splitlines()
    if size > max_bytes and lines:
        lines = lines[1:]
    out = []
    for ln in lines:
        try:
            r = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if float(r.get(key) or 0) >= since:
            out.append(r)
    return out


def _pid_alive(pid: Any) -> bool:
    try:
        import psutil
        return bool(pid) and psutil.pid_exists(int(pid))
    except (ImportError, ValueError):
        return True


def _uptime(events: list[dict[str, Any]], now: float, window_s: float) -> dict[str, float]:
    lo = now - window_s
    alive = paused = 0.0
    start = pstart = None
    first = None
    for e in events:
        ts, k = float(e["ts"]), e["kind"]
        if k == "session_start":
            start = ts
            first = ts if first is None else first
        elif k in ("session_end", "stopped") and start is not None:
            alive += max(0.0, ts - max(start, lo))
            start = None
        elif k in ("paused", "owner_busy", "owner_halt") and pstart is None:
            pstart = ts
        elif k in ("resumed", "owner_free", "owner_resumed") and pstart is not None:
            paused += max(0.0, ts - max(pstart, lo))
            pstart = None
    if start is not None:
        alive += max(0.0, now - max(start, lo))
    if pstart is not None:
        paused += max(0.0, now - max(pstart, lo))
    span = min(window_s, now - first) if first is not None else 0.0
    return {"span_s": span, "alive_s": alive, "paused_s": min(paused, alive),
            "alive_pct": (alive / span) if span > 0 else None}


def collect(state_dir: Path, goal: dict[str, Any], since: float, now: Optional[float] = None) -> dict[str, Any]:
    from tools.owner_journeys import route_ladder as rl

    now = now or time.time()
    state_dir = Path(state_dir)
    try:
        st = json.loads((state_dir / "state.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        st = {}
    period = tail_jsonl(state_dir / "cycles.jsonl", since, key="started")
    last50 = tail_jsonl(state_dir / "cycles.jsonl", 0.0, key="started", max_bytes=256 * 1024)[-50:]
    streak = 0
    for r in reversed(last50):
        if r.get("status") in ("ERROR", "ABANDONED_ON_RESTART") or r.get("task_status") == "NO_ROUTE":
            streak += 1
        elif r.get("status") == "COMPLETED":
            break
    triage = [r for r in period if r.get("kind") == "triage" and r.get("status") == "COMPLETED"]
    tri_rate = (sum(bool((r.get("verifier") or {}).get("pass")) for r in triage) / len(triage)) if triage else None
    tiers: dict[str, int] = {}
    for r in period:
        if r.get("status") == "COMPLETED":
            tiers[str(r.get("tier"))] = tiers.get(str(r.get("tier")), 0) + 1
    try:
        ladder = rl.LadderConfig.load(state_dir)
    except (ValueError, OSError):
        ladder = rl.LadderConfig()
    spent = rl.CapLedger(state_dir / "cloud_cap.json", ladder).spent_today()
    cap_blocked = any(t.get("status") == "CAP_BLOCKED" for r in period for t in (r.get("route_tried") or []))
    events = tail_jsonl(state_dir / "events.jsonl", now - 24 * 3600 - 1)
    started = float(st.get("session_started") or 0)
    if started and started < now - 24 * 3600 and not any(e.get("kind") == "session_start" for e in events):
        events.insert(0, {"ts": started, "kind": "session_start"})   # a session older than the window
    reg = lp.Registry(state_dir)
    counts = reg.counts()
    waiting = [x for x in reg.read()["lessons"].values() if x["status"] == "APPROVAL_REQUESTED"]
    mode = st.get("mode") or "UNKNOWN"
    if mode not in ("STOPPED", "EXITED") and st.get("pid") and not _pid_alive(st.get("pid")):
        mode = "NOT_RUNNING"
    return {
        "now": now, "since": since, "mode": mode, "pause_reason": st.get("pause_reason"),
        "busy_reason": st.get("busy_reason"),
        "cycles_total": max(0, int(st.get("next_cycle_id") or 1) - 1),
        "cycles_period": len(period), "completed_period": sum(r.get("status") == "COMPLETED" for r in period),
        "errors_period": sum(r.get("status") == "ERROR" or r.get("task_status") == "NO_ROUTE" for r in period),
        "error_streak": streak, "triage_rate": tri_rate, "triage_n": len(triage), "tiers": tiers,
        "spent_today_usd": round(spent, 6), "daily_cap_usd": ladder.daily_cap_usd,
        "cap_reached": spent >= ladder.daily_cap_usd - 1e-9 or cap_blocked,
        "lessons": counts, "waiting_approval": [{"id": x["id"], "approval_id": x.get("approval_id")} for x in waiting],
        "uptime": _uptime(events, now, 24 * 3600),
        "busy_events_period": sum(e["kind"] == "owner_busy" for e in events if e["ts"] >= since),
    }


def _pct(x: Optional[float]) -> str:
    return "—" if x is None else f"{x * 100:.0f}%"


def _mark(ok: Optional[bool]) -> str:
    return "—" if ok is None else ("✅" if ok else "⚠️")


def render(m: dict[str, Any], goal: dict[str, Any], reasons: list[str], readiness: dict[str, Any]) -> str:
    t = goal["targets"]
    hours = max(0.0, (m["now"] - m["since"]) / 3600)
    local = datetime.fromtimestamp(m["now"]).astimezone()
    off = local.utcoffset().total_seconds() / 3600 if local.utcoffset() else 0.0
    when = local.strftime("%d.%m %H:%M") + f" (UTC{off:+g})"
    mode = MODE_RU.get(m["mode"], m["mode"])
    if m["mode"] == "PAUSED" and m.get("pause_reason"):
        mode += f" ({m['pause_reason']})"
    if m["mode"] == "NOT_RUNNING":
        mode = "процесс НЕ запущен"
    tiers = m["tiers"]
    tiers_ru = (f"без модели {tiers.get('deterministic', 0)}, локально {tiers.get('local', 0)}, "
                f"бесплатное облако {tiers.get('free_cloud', 0)}, GLM {tiers.get('max_cloud', 0)}")
    les = m["lessons"]
    proposed = sum(les.get(k, 0) for k in ("GAIN_PROVEN", "APPROVAL_REQUESTED", "APPROVAL_BACKEND_UNAVAILABLE",
                                           "PROMOTED", "REJECTED_BY_OWNER", "ROLLED_BACK"))
    tri_ok = None if m["triage_rate"] is None else m["triage_rate"] >= t["triage_pass_rate_min"]
    up = m["uptime"]["alive_pct"]
    blockers = []
    if readiness.get("verdict") != "READY":
        blockers += [CRITERIA_RU.get(f, f) for f in readiness.get("failing", [])[:4]]
    if m["error_streak"] >= t["max_error_streak"]:
        blockers.append(f"ошибок подряд: {m['error_streak']}")
    if m["cap_reached"]:
        blockers.append("лимит $ на сегодня исчерпан — облако выключено до завтра, работа идёт локально")
    if m["mode"] in ("NOT_RUNNING", "STOPPED", "HALTED_BY_OWNER"):
        blockers.append(mode)
    lines = [
        f"🎯 Боссман 24/7 — отчёт ({', '.join(REASON_RU.get(r, r) for r in reasons)}) · {when}",
        f"Цель: {goal['goal']}",
        f"Состояние: {mode}",
        f"Циклы: {m['cycles_period']} за {hours:.1f} ч (всего {m['cycles_total']}), "
        f"ошибок {m['errors_period']}, подряд {m['error_streak']}",
        f"Разбор входящих: {_pct(m['triage_rate'])} из {m['triage_n']} (цель ≥ {_pct(t['triage_pass_rate_min'])}) "
        f"{_mark(tri_ok)}",
        f"Маршрут: {tiers_ru}; Claude — 0",
        f"Расходы сегодня: ${m['spent_today_usd']:.4f} из ${m['daily_cap_usd']:.2f} "
        f"{_mark(m['spent_today_usd'] <= m['daily_cap_usd'])}",
        f"Работа за 24 ч: {_pct(up)} (цель ≥ {_pct(t['uptime_min'])}), пауз/занятости {m['busy_events_period']}",
        f"Уроки: в карантине {les.get('QUARANTINED', 0)}, A/B без прироста {les.get('NOT_PROVEN', 0)}, "
        f"предложено вам {proposed}, ждут решения {len(m['waiting_approval'])}, приняты {les.get('PROMOTED', 0)}, "
        f"откачены {les.get('ROLLED_BACK', 0)}; без A/B-стенда {les.get('no_ab_harness', 0)}",
        f"Готовность 24/7: {readiness.get('verdict', '—')}",
        "Блокеры: " + ("; ".join(blockers) if blockers else "нет"),
    ]
    if m["waiting_approval"]:
        ids = ", ".join(f"#{w['approval_id']}" for w in m["waiting_approval"])
        lines.append(f"👉 Ждут вашего решения в /approvals: {ids}")
    lines.append("Управление: пауза — файл PAUSE; стоп — /stop в пульте или файл STOP (см. LEARNING_247.md).")
    return "\n".join(lines)


class Reporter:
    def __init__(self, state_dir: Path, notifier: Notifier, goal: Optional[dict[str, Any]] = None,
                 clock: Callable[[], float] = time.time, readiness_fn: Optional[Callable[[Path], dict]] = None):
        self.state_dir = Path(state_dir)
        self.goal = goal or load_goal(self.state_dir)
        self.notifier = notifier
        self.clock = clock
        self.path = self.state_dir / "notify" / "reporter.json"
        self.readiness_fn = readiness_fn or default_readiness

    def _read(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _write(self, d: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def readiness(self, rs: dict[str, Any], now: float, fresh: bool = False) -> dict[str, Any]:
        cached = rs.get("readiness") or {}
        if fresh or not cached or now - float(cached.get("ts", 0)) >= float(self.goal["readiness_every_s"]):
            try:
                rep = self.readiness_fn(self.state_dir)
                cached = {"ts": now, "verdict": rep["verdict"],
                          "failing": [k for k, v in rep["criteria"].items() if not v["pass"]]}
            except Exception as exc:  # noqa: BLE001 - the report must still go out
                cached = {"ts": now, "verdict": "UNKNOWN", "failing": [f"оценка не удалась: {type(exc).__name__}"]}
        return cached

    def tick(self, force: Optional[str] = None) -> dict[str, Any]:
        now = self.clock()
        rs = self._read()
        t = self.goal["targets"]
        since = float(rs.get("last_report_ts") or now - float(self.goal["report_every_h"]) * 3600)
        m = collect(self.state_dir, self.goal, since, now)
        prev_verdict = (rs.get("readiness") or {}).get("verdict")
        rd = self.readiness(rs, now, fresh=force in ("stop", "manual"))
        rs["readiness"] = rd
        reasons: list[str] = [force] if force else []
        if not rs.get("last_report_ts"):
            reasons.append("start")
        elif now - float(rs.get("last_scheduled_ts") or 0) >= float(self.goal["report_every_h"]) * 3600:
            reasons.append("schedule")
        if prev_verdict and rd["verdict"] != prev_verdict and "UNKNOWN" not in (prev_verdict, rd["verdict"]):
            reasons.append("readiness_flip")
        day = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")
        if m["cap_reached"] and rs.get("cap_reported_day") != day:
            reasons.append("cap")
            rs["cap_reported_day"] = day
        if m["error_streak"] >= int(t["max_error_streak"]):
            if not rs.get("streak_reported"):
                reasons.append("errors")
                rs["streak_reported"] = True
        elif m["error_streak"] == 0:
            rs["streak_reported"] = False
        reasons = list(dict.fromkeys(reasons))
        result: dict[str, Any] = {"queued": None, "reasons": reasons}
        if reasons:
            text = render(m, self.goal, reasons, rd)
            urgent = any(r in URGENT for r in reasons)
            key = "goal-report" if not urgent else "alert-" + "-".join(sorted(r for r in reasons if r in URGENT))
            result["queued"] = self.notifier.enqueue(key, text, urgent=urgent)
            rs["last_report_ts"] = now
            if "schedule" in reasons or "start" in reasons or not rs.get("last_scheduled_ts"):
                rs["last_scheduled_ts"] = now
            result["text"] = text
        self._write(rs)
        result["delivery"] = self.notifier.flush()
        return result

    def lesson_event(self, change: dict[str, Any]) -> str:
        """A lesson needs the owner (approval) or was decided -> urgent short message."""
        lid, status = change["lesson"], change["status"]
        les = lp.Registry(self.state_dir).read()["lessons"].get(lid, {})
        if status == "APPROVAL_REQUESTED":
            runs = ", ".join(f"{r['baseline_exact']}→{r['candidate_exact']}" for r in (les.get("ab") or {}).get("repeats", []))
            text = (f"🧠 Боссман нашёл урок с доказанным приростом (A/B {runs} на отложенных задачах, "
                    f"без новых нарушений безопасности).\nУрок {lid}: «{les.get('text', '')[:160]}» → "
                    f"{les.get('labels')}\nПодтверждение #{les.get('approval_id')}: откройте /approvals в пульте "
                    "(или «Подтверждения» в Bossman). Без вашего «Разрешить» урок не применяется.\n"
                    f"Откат потом одной командой: lesson_pipeline.py rollback {lid}")
            return self.notifier.enqueue("approval-" + lid, text, urgent=True)
        if status in ("PROMOTED", "REJECTED_BY_OWNER"):
            head = "✅ Урок принят и применён" if status == "PROMOTED" else "⛔ Урок отклонён — не применяется"
            text = f"{head}: {lid} «{les.get('text', '')[:120]}»."
            if status == "PROMOTED":
                text += f"\nОткат: python tools/owner_journeys/lesson_pipeline.py rollback {lid}"
            return self.notifier.enqueue("lesson-" + lid, text, urgent=True)
        return "IGNORED"


def default_readiness(state_dir: Path) -> dict[str, Any]:
    from tools.owner_journeys import learning_247_readiness as rd
    return rd.evaluate(Path(state_dir))


class LoopHooks:
    """What the supervisor calls between cycles. Never raises into the loop."""

    def __init__(self, state_dir: Path, *, transport: str = "telegram", notifier: Optional[Notifier] = None,
                 approvals: Any = "auto", readiness_fn: Optional[Callable[[Path], dict]] = None,
                 clock: Callable[[], float] = time.time, event: Callable[..., None] = lambda *a, **k: None):
        self.state_dir = Path(state_dir)
        self.goal = load_goal(self.state_dir)
        limits = Limits(min_interval_s=float(self.goal["min_interval_s"]),
                        max_per_day=int(self.goal["max_reports_per_day"]))
        self.notifier = notifier or Notifier(self.state_dir, make_transport(transport), limits, clock=clock)
        self.reporter = Reporter(self.state_dir, self.notifier, self.goal, clock=clock, readiness_fn=readiness_fn)
        self.registry = lp.Registry(self.state_dir)
        self._approvals = approvals
        self.clock = clock
        self.event = event
        self._last_poll = 0.0
        self._last_tick = 0.0

    @property
    def approvals(self):
        if self._approvals == "auto":
            try:
                self._approvals = lp.ApprovalBackend.from_companion(self.goal.get("approval_core_url") or "")
            except (OSError, ValueError):
                self._approvals = None
        return self._approvals

    def tick(self, force: Optional[str] = None, min_gap_s: float = 30.0) -> None:
        now = self.clock()
        if force is None and now - self._last_tick < min_gap_s:
            return
        self._last_tick = now
        try:
            self.registry.ingest()
            if now - self._last_poll >= 60.0 or force:
                self._last_poll = now
                for ch in lp.poll_approvals(self.registry, self.approvals):
                    self.event("lesson_status", **ch)
                    self.reporter.lesson_event(ch)
            res = self.reporter.tick(force)
            for d in res.get("delivery") or []:
                self.event("report_" + d["status"].lower(), key=d.get("key"), message_id=d.get("message_id"),
                           error=d.get("error"))
        except Exception as exc:  # noqa: BLE001 - reporting must never stop learning
            self.event("hooks_error", error=f"{type(exc).__name__}: {str(exc)[:200]}")

    def ab_due(self, next_cycle_id: int) -> Optional[dict[str, Any]]:
        data = self.registry.read()
        ab = self.goal["ab"]
        day = datetime.fromtimestamp(self.clock(), timezone.utc).strftime("%Y-%m-%d")
        if int(data.get("ab_per_day", {}).get(day, 0)) >= int(ab["max_lessons_per_day"]):
            return None
        if next_cycle_id - int(data.get("last_ab_cycle", 0)) < int(ab["every_cycles"]):
            return None
        return self.registry.next_for_ab()

    def ab_started(self, cycle_id: int) -> None:
        data = self.registry.read()
        day = datetime.fromtimestamp(self.clock(), timezone.utc).strftime("%Y-%m-%d")
        data["last_ab_cycle"] = cycle_id
        data.setdefault("ab_per_day", {})[day] = int(data.get("ab_per_day", {}).get(day, 0)) + 1
        self.registry.write(data)

    def ab_finished(self, lid: str, ab: dict[str, Any]) -> str:
        verdict = lp.record_ab(self.registry, lid, ab)
        self.event("lesson_ab", lesson=lid, verdict=verdict, deltas=[r["delta"] for r in ab["repeats"]])
        if verdict == "GAIN_PROVEN":
            st = lp.request_approval(self.registry, lid, self.approvals)
            self.event("lesson_status", lesson=lid, status=st)
            if st == "APPROVAL_REQUESTED":
                self.reporter.lesson_event({"lesson": lid, "status": st})
                for d in self.notifier.flush():
                    self.event("report_" + d["status"].lower(), key=d.get("key"), message_id=d.get("message_id"))
        return verdict


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("preview", "send-now", "flush", "status"))
    ap.add_argument("--state-dir", default=os.path.join(os.path.expanduser("~"), "Bossman", "learning247"))
    ap.add_argument("--transport", default="telegram", choices=("telegram", "off"))
    args = ap.parse_args(argv)
    state = Path(args.state_dir)
    goal = load_goal(state)
    notifier = Notifier(state, make_transport(args.transport),
                        Limits(min_interval_s=float(goal["min_interval_s"]), max_per_day=int(goal["max_reports_per_day"])))
    if args.cmd == "status":
        print(json.dumps(notifier.status(), ensure_ascii=False))
        return 0
    if args.cmd == "flush":
        print(json.dumps(notifier.flush(), ensure_ascii=False))
        return 0
    rep = Reporter(state, notifier, goal)
    if args.cmd == "preview":
        now = time.time()
        rs = rep._read()
        m = collect(state, goal, float(rs.get("last_report_ts") or now - goal["report_every_h"] * 3600), now)
        print(render(m, goal, ["manual"], rep.readiness(rs, now, fresh=True)))
        return 0
    res = rep.tick("manual")
    print(json.dumps({"queued": res["queued"], "delivery": res["delivery"]}, ensure_ascii=False))
    return 0 if any(d.get("status") == "SENT" for d in res["delivery"]) else 1


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())
