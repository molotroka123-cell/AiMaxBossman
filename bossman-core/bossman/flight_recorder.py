"""V2.6 — Bossman Flight Recorder (модуль H): компактный трейс задачи + explain.

Первоклассная наблюдаемость БЕЗ накладных на hot path: петля уже пишет всё
нужное в канонические таблицы (`runs`, `model_calls`, `tool_calls`,
`approvals`, `cloud_calls`, `failures`, `decisions`, `working_memory`) с общим
ключом run_id/task_id. Этот модуль — READ-SIDE сборка одного связного трейса,
отвечающего на «почему»: модель/агент/инструмент/эскалация/остановка/ресурсы.
Ничего не пишет, второй EventBus не создаёт. Секреты в выводе проходят через
канонический obs.redact (защита в глубину: см. redaction в местах записи).
"""
from __future__ import annotations

from typing import Any

from . import db, obs

_log = obs.get_logger("bossman.flight_recorder")


def _iso(v: Any) -> Any:
    return v.isoformat() if hasattr(v, "isoformat") else v


def _duration_s(start: Any, end: Any) -> float | None:
    if start is None or end is None:
        return None
    try:
        return round((end - start).total_seconds(), 3)
    except Exception:  # noqa: BLE001 — трейс не должен падать из-за кривой строки
        return None


def _agent_selection(task: dict) -> dict:
    """Почему выбран агент: pick_agent детерминирован по тексту задачи, поэтому
    скоринг честно ВОСПРОИЗВОДИТСЯ на чтении — нулевая цена на hot path."""
    try:
        from .agents import load_all
        from .runner import pick_agent
        agents = load_all()
        if not agents:
            return {"reason": "нет агентов"}
        if task.get("agent") and task["agent"] in agents:
            picked = agents[task["agent"]]
            explicit = True
        else:
            picked = pick_agent(agents, task["text"])
            explicit = False
        return {
            "picked": picked.name,
            "explicit": explicit,
            "reason": ("агент задан явно в задаче" if explicit
                       else "детерминированная эвристика pick_agent по ключевым словам"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"reason": f"недоступно: {exc}"}


async def explain_task(task_id: int) -> dict | None:
    """Собрать полный трейс задачи. None, если задачи нет."""
    task = await db.fetchrow("SELECT * FROM tasks WHERE id=$1", task_id)
    if not task:
        return None
    task = dict(task)
    runs = [dict(r) for r in await db.fetch(
        "SELECT * FROM runs WHERE task_id=$1 ORDER BY id", task_id)]
    run_ids = [r["id"] for r in runs]

    model_calls: list[dict] = []
    tool_calls: list[dict] = []
    cloud_calls: list[dict] = []
    if run_ids:
        model_calls = [dict(r) for r in await db.fetch(
            "SELECT * FROM model_calls WHERE run_id = ANY($1::bigint[]) ORDER BY id", run_ids)]
        tool_calls = [dict(r) for r in await db.fetch(
            "SELECT * FROM tool_calls WHERE run_id = ANY($1::bigint[]) ORDER BY id", run_ids)]
        cloud_calls = [dict(r) for r in await db.fetch(
            "SELECT * FROM cloud_calls WHERE run_id = ANY($1::bigint[]) ORDER BY id", run_ids)]
    approvals = [dict(r) for r in await db.fetch(
        "SELECT * FROM approvals WHERE task_id=$1 ORDER BY id", task_id)]
    failures = [dict(r) for r in await db.fetch(
        "SELECT * FROM failures WHERE task_id=$1 ORDER BY id", str(task_id))]
    decisions = [dict(r) for r in await db.fetch(
        "SELECT * FROM decisions WHERE source_run_id = ANY($1::bigint[]) ORDER BY id",
        run_ids)] if run_ids else []

    prompt_toks = sum(c["prompt_tokens"] for c in model_calls)
    completion_toks = sum(c["completion_tokens"] for c in model_calls)
    aliases = sorted({c["alias"] for c in model_calls})
    cloud_aliases = sorted({c["alias"] for c in model_calls if c["is_cloud"]})
    cache_hits = sum(1 for c in model_calls if c.get("prefix_cache_hit"))

    def _tool_row(t: dict) -> dict:
        return {
            "tool": t["tool"], "status": t["status"],
            "approved_by": t.get("approved_by"),
            "args": obs.redact_obj(t.get("args")),
            "result_preview": obs.redact(t.get("result_preview") or "")[:300],
            "at": _iso(t.get("created_at")),
        }

    # Learning-eligibility текущего состояния holdout (ретроспективно фиксировать
    # нечего: guard в петле детерминирован по тому же task_id).
    try:
        from .runner import _learning_excluded
        learning_excluded = _learning_excluded(str(task_id))
    except Exception:  # noqa: BLE001
        learning_excluded = False

    stopped_because = None
    if runs:
        last = runs[-1]
        stopped_because = last.get("error") or {
            "done": "модель вернула финальный ответ без tool-calls",
            "running": "выполняется",
        }.get(last["status"], last["status"])

    return {
        "task_id": task_id,
        "intent": task["text"][:2000],
        "source": task["source"],
        "status": task["status"],
        "agent_selection": _agent_selection(task),
        "runs": [{
            "run_id": r["id"], "agent": r["agent"], "status": r["status"],
            "steps": r["steps"], "error": obs.redact(r.get("error") or "") or None,
            "duration_s": _duration_s(r.get("started_at"), r.get("finished_at")),
        } for r in runs],
        "retries": max(0, len(runs) - 1),
        "models": {
            "aliases": aliases,
            "why": "alias закреплён за агентом (agent.yaml); маршрут до цели решает "
                   "Gateway по capability/приоритету/health/cloud-политике",
            "calls": len(model_calls),
            "cloud_aliases": cloud_aliases,
            "prefix_cache_hits": cache_hits,
        },
        "context": {
            "why": "system=prompt.md+инструменты+memory.md; retrieved=релевантные "
                   "чанки context_engine с provenance; блоки видны в block_tokens",
            "last_block_tokens": (model_calls[-1].get("block_tokens") if model_calls else None),
            "window_fill_max": max((c.get("window_fill") or 0.0) for c in model_calls) if model_calls else None,
        },
        "tools": [_tool_row(t) for t in tool_calls],
        "approvals": [{
            "id": a["id"], "kind": a["kind"], "tool": a.get("tool"),
            "status": a["status"], "decided_by": a.get("decided_by"),
            "preview": obs.redact(a.get("preview") or "")[:300],
        } for a in approvals],
        "escalations": [{
            "alias": c["alias"], "approved_by": c.get("approved_by"),
            "why": "облачный вызов по cloud-политике агента (ask → approval)",
        } for c in cloud_calls],
        "decisions": [{
            "decision_id": d["decision_id"], "scope": d["scope"],
            "decision": d["decision"], "reason": d.get("reason"),
        } for d in decisions],
        "failures": [{
            "failure_id": f["failure_id"], "error_class": f["error_class"],
            "symptom": obs.redact(f["symptom"] or "")[:300],
            "resolved": f["resolved"],
        } for f in failures],
        "result": obs.redact(task.get("result") or "")[:2000] or None,
        "stopped_because": obs.redact(stopped_because) if isinstance(stopped_because, str) else stopped_because,
        "resources": {
            "prompt_tokens": prompt_toks,
            "completion_tokens": completion_toks,
            "total_tokens": prompt_toks + completion_toks,
            "duration_s": _duration_s(task.get("started_at"), task.get("finished_at")),
            "top_cost": ("model_calls" if prompt_toks + completion_toks > 0 else None),
        },
        "learning": {
            "holdout_excluded_now": learning_excluded,
            "note": "исходы из secret holdout не пишутся в durable learning-корпус",
        },
        "evidence_of_success": [
            t["tool"] for t in tool_calls
            if t["status"] == "ok" and t["tool"] in ("tests", "run")
        ] or None,
    }
