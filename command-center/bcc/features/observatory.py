"""Owner-only read projections over existing BCC records. No second store.

Never select prompts, reasoning/checkpoints, raw results, errors, tokens, keys,
request bodies, personal filenames or memory content. Task/project scopes are
explicit. An execution record is NOT independent verification.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timezone
import math

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Query, Request

from . import Feature
from .. import db
from ..tools import REGISTRY

router = APIRouter()
LIMIT = 100


def _enum(value, allowed):
    return value if isinstance(value, str) and value in allowed else "UNKNOWN"


def _finite(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


async def _rows(session, table, columns, where=None, limit=LIMIT):
    stmt = sa.select(*(table.c[c] for c in columns)).order_by(table.c.id.desc()).limit(limit + 1)
    if where is not None:
        stmt = stmt.where(where)
    rows = [dict(row) for row in (await session.execute(stmt)).mappings()]
    return rows[:limit], len(rows) > limit


async def snapshot(svc, task_id=None, project_id=None):
    async with svc.db.session() as s:
        tasks, tasks_cut = await _rows(s, db.tasks, ["id", "agent_id", "status", "updated_at"], limit=50)
        for row in tasks:
            row["status"] = _enum(row["status"], {"draft","queued","running","paused","waiting_approval","completed","failed","stopped"})
        runs, calls, approvals, records, verifications = [], [], [], [], []
        truncation = {"tasks": tasks_cut}
        if task_id is not None:
            if not (await s.execute(sa.select(db.tasks.c.id).where(db.tasks.c.id == task_id))).first():
                raise HTTPException(404, "Task not found")
            # Join model identity, never display arbitrary model_alias or secret fields.
            result = await s.execute(sa.select(db.task_runs.c.id, db.task_runs.c.task_id,
                db.task_runs.c.status, db.task_runs.c.started_at, db.task_runs.c.finished_at,
                db.task_runs.c.tokens_in, db.task_runs.c.tokens_out, db.task_runs.c.cost_usd,
                db.models.c.id.label("model_id"), db.models.c.kind.label("model_kind"))
                .select_from(db.task_runs.outerjoin(db.models, db.models.c.alias == db.task_runs.c.model_alias))
                .where(db.task_runs.c.task_id == task_id).order_by(db.task_runs.c.id.desc()).limit(51))
            all_runs = [dict(r) for r in result.mappings()]
            runs, truncation["runs"] = all_runs[:50], len(all_runs)>50
            run_ids = [r["id"] for r in runs]
            for r in runs:
                r["status"] = _enum(r["status"], {"queued","leased","running","completed","failed","stopped"})
                r["model_kind"] = _enum(r["model_kind"], {"local","cloud"})
                # These are ledger fields, not proof of a live model/provider call.
                for key in ("tokens_in","tokens_out","cost_usd"):
                    r[key] = _finite(r[key])
            calls, truncation["calls"] = await _rows(s, db.tool_calls,
                ["id","run_id","step","tool","effect","status","duration_ms"],
                db.tool_calls.c.run_id.in_(run_ids))
            for c in calls:
                # A database string is not a safe display label. Only known registered names.
                c["tool"] = c["tool"] if c["tool"] in REGISTRY.names() else "UNREGISTERED_TOOL"
                c["effect"] = _enum(c["effect"], {"auto","ask","deny"})
                c["status"] = _enum(c["status"], {"pending_approval","approved","rejected","executed","denied","error","timeout","replayed","started","interrupted","reconciled"})
                c["duration_ms"] = _finite(c["duration_ms"])
            approvals, truncation["approvals"] = await _rows(s, db.approvals,
                ["id","run_id","status","created_at","decided_at"], db.approvals.c.task_id == task_id)
            for a in approvals:
                a["status"] = _enum(a["status"], {"pending","approved","rejected","expired","consumed","revoked"})
            # Event kind only: don't interpret free-form event messages as verified outcomes.
            records, truncation["events"] = await _rows(s, db.run_events,
                ["id","run_id","kind","level","ts"], db.run_events.c.run_id.in_(run_ids))
            safe_kinds = {"verification","verification.result","tool.call","tool.result","plan","error","warning","step","model.call","model.result","started","completed","failed"}
            for r in records:
                r["kind"] = _enum(r["kind"], safe_kinds)
                r["level"] = _enum(r["level"], {"info","warn","error","debug"})
            proof = await s.execute(sa.select(db.events.c.id, db.events.c.ts,
                db.events.c.data["run_id"].as_integer().label("run_id"),
                db.events.c.data["status"].as_string().label("status"))
                .where(db.events.c.kind == "verification.result",
                       db.events.c.data["run_id"].as_integer().in_(run_ids))
                .order_by(db.events.c.id.desc()).limit(101))
            all_proof = [dict(r) for r in proof.mappings()]
            verifications, truncation["verifications"] = all_proof[:100], len(all_proof)>100
            for r in verifications:
                r["status"] = _enum(r["status"], {"VERIFIED","UNVERIFIED","FAILED","INCONCLUSIVE"})
        facts, facts_cut = [], False
        memory_scope = "NOT_SELECTED"
        if project_id is not None:
            memory_scope = "EXACT_META_PROJECT_ID"
            where = db.facts.c.meta["project_id"].as_string() == project_id
        elif task_id is not None:
            memory_scope = "TASK_RUN_LINEAGE"
            where = db.facts.c.source_run_id.in_(sa.select(db.task_runs.c.id).where(db.task_runs.c.task_id == task_id))
        if memory_scope != "NOT_SELECTED":
            facts, facts_cut = await _rows(s, db.facts,
                ["id","source_kind","source_run_id","superseded_by","invalid_at","expired_at","created_at","confidence"], where)
            ids = {f["id"] for f in facts}
            for f in facts:
                f["source_kind"] = _enum(f["source_kind"], {"human","run","intervention","note"})
                f["confidence"] = _finite(f["confidence"])
                if f["confidence"] is not None and f["confidence"]>1:
                    f["confidence"] = None
                # No foreign fact ID leaked via a superseding relationship.
                f["superseded_outside_window"] = f["superseded_by"] is not None and f["superseded_by"] not in ids
                if f["superseded_by"] not in ids:
                    f["superseded_by"] = None
        columns = [db.skill_evaluations.c[k] for k in
            ("id","skill_id","baseline_version_id","candidate_version_id","status","verdict","applied","approval_id","updated_at")]
        metric_names = []
        for side in ("baseline", "candidate"):
            for metric in ("runs", "success_rate", "avg_duration_ms"):
                key = f"{side}_{metric}"
                metric_names.append(key)
                # Select numeric leaves only. Never load arbitrary evaluation text.
                leaf = db.skill_evaluations.c.metrics[side][metric]
                columns.append(leaf.label(key))
                # SQLite json_extract converts true to integer 1. Inspect the
                # original JSON type too, rather than certifying a boolean as
                # one measured run. PostgreSQL JSON preserves its own type.
                dialect = s.bind.dialect.name
                json_type = (sa.func.json_type(db.skill_evaluations.c.metrics, f"$.{side}.{metric}")
                             if dialect == "sqlite" else sa.func.json_typeof(leaf)
                             if dialect == "postgresql" else sa.literal(None))
                columns.append(json_type.label("_type_" + key))
        eval_rows = [dict(r) for r in (await s.execute(sa.select(*columns)
            .order_by(db.skill_evaluations.c.id.desc()).limit(51))).mappings()]
        evaluations, eval_cut = eval_rows[:50], len(eval_rows)>50
        for e in evaluations:
            for key in metric_names:
                e[key] = _finite(e[key]) if e.pop("_type_" + key) in {"integer", "real", "number"} else None
                if e[key] is not None and (key.endswith("_success_rate") and e[key]>1 or key.endswith("_runs") and int(e[key])!=e[key]):
                    e[key] = None
            e["status"] = _enum(e["status"], {"collecting","decided"})
            e["verdict"] = _enum(e["verdict"], {"promote","reject","human_review"})
        counts = dict(Counter(t["status"] for t in tasks))
    return {
        "schema_version": 1, "observed_at": datetime.now(timezone.utc).isoformat(),
        "source": "CANONICAL_BCC_DATABASE", "read_only": True,
        "tasks": tasks, "window_limit": 50, "truncated": truncation,
        "brain": {"task_id": task_id, "runs": runs, "tool_calls": calls, "approvals": approvals,
                  "events": records, "recorded_verifications": verifications, "independent_verification": "NOT_CAPTURED_BY_THIS_VIEW",
                  "note": "Execution and ledger values are recorded state, not a live-model certificate or hidden chain-of-thought."},
        "memory": {"scope": memory_scope, "project_id": project_id, "task_id": task_id if project_id is None else None,
                   "facts": facts, "truncated": facts_cut, "content_exposed": False,
                   "note": "Metadata/lineage map only. No automatic scan, no embeddings or private text displayed."},
        "learning": {"scope": "INSTANCE_EVALUATION_METADATA", "evaluations": evaluations, "truncated": eval_cut,
                     "view_can_promote": False, "note": "A proposal or promote verdict is not an applied skill."},
        "analysis": {"scope": "LATEST_50_TASK_RECORDS", "counts": counts,
                     "observations": [
                         {"code": "FAILED_TASKS", "count": counts.get("failed", 0)},
                         {"code": "WAITING_APPROVAL", "count": counts.get("waiting_approval", 0)},
                         {"code": "COMPLETED_WITHOUT_VERIFICATION_RECORD_IN_WINDOW", "count": sum(r["status"]=="completed" and r["id"] not in {v["run_id"] for v in verifications} for r in runs)},
                         {"code": "TOOL_ERROR_IN_SELECTED_WINDOW", "count": sum(c["status"] in {"error","timeout","interrupted"} for c in calls)}],
                     "automatic_fix": False, "intelligence_score": None},
    }


@router.get("/observatory/snapshot")
async def get_snapshot(request: Request, task_id: int | None = Query(None, ge=1),
                       project_id: str | None = Query(None, min_length=1, max_length=96, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")):
    try:
        async with asyncio.timeout(5):
            return await snapshot(request.app.state.svc, task_id, project_id)
    except (TimeoutError, sa.exc.SQLAlchemyError):
        raise HTTPException(503, "Observatory data unavailable; retry after database recovery") from None


@router.get("/observatory/routes")
async def get_routes(request: Request):
    """Declared FastAPI contracts, not a claimed distributed trace or DB span."""
    # Reuse the canonical traversal: recent FastAPI keeps included routers
    # lazy rather than flattening app.routes. Do not maintain a second walker.
    from .command_bar import _walk_routes
    routes = []
    for route in _walk_routes(request.app.routes):
        path = getattr(route, "path", "")
        if not path.startswith("/api/"):
            continue
        endpoint = getattr(route, "endpoint", None)
        routes.append({"path": path, "methods": sorted(getattr(route, "methods", []) or []),
                       "handler": (getattr(endpoint, "__module__", "") + "." + getattr(endpoint, "__name__", "")),
                       "evidence": "DECLARED_ROUTE_NOT_EXECUTION"})
    return {"routes": routes[:2000], "truncated": len(routes)>2000, "source": "RUNNING_FASTAPI_ROUTER"}

FEATURE = Feature(name="observatory", router=router)
