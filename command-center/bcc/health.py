"""Read-only health snapshots. Registered or reachable does not mean ready."""
from __future__ import annotations

import asyncio

import sqlalchemy as sa

from . import db as dbm, model_health


async def model_components(svc) -> dict:
    async with asyncio.timeout(2.0):
        async with svc.db.session() as session:
            models = dbm.rows_dicts((await session.execute(sa.select(dbm.models))).fetchall())
            providers = dbm.rows_dicts((await session.execute(sa.select(dbm.providers))).fetchall())

    def status(rows):
        if not rows:
            return "empty"
        records = [model_health.HealthRecord.from_dict(row.get("health")) for row in rows]
        fresh = sum(record.usable() and not record.stale for record in records)
        if fresh == len(records):
            return "ok"
        if fresh:
            return "degraded"
        if all(record.status == model_health.UNMEASURED for record in records):
            return "unknown"
        if all(record.status == model_health.HEALTHY for record in records):
            return "stale"
        return "error"

    available = sum(record.usable() and not record.stale for record in
                    (model_health.HealthRecord.from_dict(row.get("health")) for row in models))
    result = {"models": {"status": status(models), "detail": f"моделей: {len(models)}",
                          "available": available},
              "providers": {"status": status(models) if providers else "empty",
                            "detail": f"провайдеров: {len(providers)}", "available": available}}
    for provider in providers:
        rows = [row for row in models if row["provider_id"] == provider["id"]]
        result[f"provider:{provider['id']}"] = {"status": status(rows),
                                               "detail": f"моделей: {len(rows)}"}
    return result


def snapshot(svc, components: dict, *, public: bool) -> dict:
    """Public endpoint omits paths, model IDs, names and exception messages."""
    codes = {"ok": "HEALTHY", "empty": "NOT_CONFIGURED", "unknown": "UNKNOWN",
             "stale": "STALE", "starting": "STARTING", "stopped": "STOPPED",
             "offline": "UNHEALTHY", "error": "UNHEALTHY", "degraded": "DEGRADED"}
    components = {name: {**entry, "status": codes.get(entry["status"], "UNKNOWN")}
                  for name, entry in components.items()}
    components["process"] = {"status": "ALIVE"}
    components["startup"] = {"status": "HEALTHY" if svc.startup.ready else "STARTING"}
    components["ui"] = {"status": "HEALTHY" if (svc.settings.ui_dir / "index.html").is_file()
                         else "NOT_CONFIGURED"}
    if svc._stopping.is_set():
        components["startup"] = {"status": "STOPPING"}
    required = [name for name in components if name in {
        "startup", "db", "queue_worker", "scheduler", "metrics", "ui", "models", "providers"}
                or name.startswith("tick:")]
    ready = all(components[name]["status"] == "HEALTHY" or
                (name in {"models", "providers"} and components[name]["status"] == "DEGRADED"
                 and components[name].get("available", 0) > 0) for name in required)
    broken = any(components[name]["status"] in {"UNHEALTHY", "STALE", "STOPPED", "STOPPING"}
                 for name in required)
    optional_degraded = any(entry["status"] not in {"HEALTHY", "ALIVE"}
                            for name, entry in components.items() if name not in required)
    any_degraded = optional_degraded or any(components[name]["status"] == "DEGRADED" for name in required)
    status = ("UNHEALTHY" if broken else "HEALTHY" if ready and not any_degraded else "DEGRADED")
    if public:
        components = {name: {"status": entry["status"]} for name, entry in components.items()
                      if not name.startswith("provider:")}
    return {"alive": True, "ready": ready, "status": status, "components": components}
