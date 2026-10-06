"""authored_by_lane (opsplug): bcc.features.benchlab - stored benchmark results, honest comparison and recommendations.

Real app + real DB (env fixture). Rows are inserted the way the background benchmark stores them.
"""
import sqlalchemy as sa

from bcc.db import benchmarks as bench_t, utcnow
from bcc.features import benchlab

from .helpers import make_stack


async def _row(env, model_id, status, results):
    async with env.svc.db.session() as s:
        bid = int((await s.execute(sa.insert(bench_t).values(
            model_id=model_id, kind="full", status=status, results=results, created_at=utcnow()))).inserted_primary_key[0])
        await s.commit()
    return bid


def test_module_exposes_the_documented_routes():
    paths = {r.path for r in benchlab.router.routes}
    assert {"/benchmarks", "/benchmarks/{bench_id:int}", "/benchmarks/compare", "/benchmarks/recommendations"} <= paths
    assert benchlab.FEATURE.name == "benchlab"


async def test_create_requires_a_model_and_unknown_benchmark_is_404(env):
    assert (await env.client.post("/api/benchmarks", json={})).status_code == 422
    assert (await env.client.get("/api/benchmarks/99999")).status_code == 404


async def test_compare_reports_stored_numbers_and_ignores_unknown_ids(env):
    stack = await make_stack(env.client)
    mid = stack["model"]["id"]
    a = await _row(env, mid, "completed", {"gen_tps": 41.5, "latency_ms_median": 800, "ttft_ms": 120, "prompt_tps": 900,
                                           "speed_method": "server_timings", "stability": {"success_rate": 1.0}})
    r = (await env.client.get(f"/api/benchmarks/compare?ids={a},424242,abc")).json()
    assert len(r["compared"]) == 1
    row = r["compared"][0]
    assert row["benchmark_id"] == a and row["model_id"] == mid
    assert (row["gen_tps"], row["latency_ms"], row["ttft_ms"], row["prompt_tps"], row["method"], row["stability"]) == \
        (41.5, 800, 120, 900, "server_timings", 1.0)


async def test_recommendation_uses_only_honest_measurements_and_the_latest_run_per_model(env):
    empty = (await env.client.get("/api/benchmarks/recommendations")).json()
    assert empty == {"for_speed": None, "based_on": 0}
    stack = await make_stack(env.client)
    mid = stack["model"]["id"]
    await _row(env, mid, "completed", {"gen_tps": 999.0})                                      # legacy: no speed_method -> ignored
    await _row(env, mid, "failed", {"gen_tps": 5000.0, "speed_method": "differential"})        # not completed -> ignored
    await _row(env, mid, "completed", {"gen_tps": 20.0, "speed_method": "differential"})       # older honest run
    await _row(env, mid, "completed", {"gen_tps": 33.0, "speed_method": "server_timings"})     # latest honest run wins
    rec = (await env.client.get("/api/benchmarks/recommendations")).json()
    assert rec == {"for_speed": {"model_id": mid, "gen_tps": 33.0}, "based_on": 1}
