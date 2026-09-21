"""Feature 04 — Model Benchmark Lab.

Фоновый benchmark: TTFT, prefill tok/s, generation tok/s (server timings
llama.cpp или дифференциальный замер, медиана 3 — см. bcc/model_speed.py), latency, coding/reasoning-семплы, stability (5 запросов). НИКАКИХ
хардкод-скоров — только измеренное. Не блокирует API/UI (фон, одна за раз).
Повторный прогон — новая запись. Сравнение и рекомендации из stored results.
"""
from __future__ import annotations

import asyncio
import statistics
import time

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request

from ..db import benchmarks as bench_t, utcnow
from . import Feature

router = APIRouter()
_running = asyncio.Lock()      # одна тяжёлая проба за раз — не душим машину


async def _measure(adapter, model_name: str) -> dict:
    """Реальные замеры. Всё, что не удалось измерить, помечается null/estimated."""
    async def one(prompt: str, max_tokens: int = 32):
        t0 = time.perf_counter()
        res = await adapter.chat(model_name, [{"role": "user", "content": prompt}],
                                 max_tokens=max_tokens)
        dt = time.perf_counter() - t0
        return dt, res

    # TEL-001: 3 честных замера скорости (server timings или дифференциальный),
    # медиана. TTFT/prefill/генерация/латентность — раздельно.
    from ..model_speed import measure_speed, median_of
    speed = median_of([await measure_speed(adapter, model_name) for _ in range(3)])
    # coding + reasoning семплы (сохраняем длину/выдержку, не оцениваем «на глаз»)
    _, coding = await one("Напиши функцию сложения двух чисел на Python.", 128)
    _, reasoning = await one("Если A>B и B>C, что больше — A или C? Кратко.", 64)
    # stability: 5 коротких, доля успеха + разброс латентности
    ok, stab_lat = 0, []
    for _ in range(5):
        try:
            dt, _ = await one("ок", 8)
            ok += 1
            stab_lat.append(dt * 1000)
        except Exception:
            pass
    return {
        "speed_method": speed.get("method"),
        "ttft_ms": speed.get("ttft_ms"),
        "prompt_tps": speed.get("prompt_tps"),
        "gen_tps": speed.get("gen_tps"),
        "latency_ms_median": speed.get("latency_ms"),
        "gen_tokens": speed.get("tokens_out"),
        "coding_sample_len": len(coding.text), "coding_sample": coding.text[:200],
        "reasoning_sample": reasoning.text[:200],
        "tool_calling": "not_tested (адаптер не пробрасывает tools)",
        "stability": {"success_rate": ok / 5,
                      "latency_stdev_ms": round(statistics.pstdev(stab_lat), 1)
                      if len(stab_lat) > 1 else 0.0},
        "measured_at": utcnow().isoformat(),
    }


async def _run_bench(svc, bench_id: int, model_id: int) -> None:
    async with _running:       # одна тяжёлая проба за раз
        await _set(svc, bench_id, status="running", started_at=utcnow())
        await svc.bus.emit("benchmark.started", benchmark_id=bench_id, model_id=model_id)
        try:
            adapter, model = await svc.registry.adapter_for(model_id)
            results = await _measure(adapter, model["name"])
            await _set(svc, bench_id, status="completed", results=results, finished_at=utcnow())
            await svc.bus.emit("benchmark.completed", benchmark_id=bench_id, model_id=model_id)
        except Exception as exc:
            await _set(svc, bench_id, status="failed", error=str(exc)[:500], finished_at=utcnow())
            await svc.bus.emit("benchmark.failed", benchmark_id=bench_id, error=str(exc)[:200])


async def _set(svc, bench_id: int, **values) -> None:
    async with svc.db.session() as s:
        await s.execute(sa.update(bench_t).where(bench_t.c.id == bench_id).values(**values))
        await s.commit()


@router.post("/benchmarks")
async def create_bench(request: Request):
    """Запустить benchmark В ФОНЕ — запрос не блокируется."""
    svc = request.app.state.svc
    body = await request.json()
    model_id = body.get("model_id")
    if not model_id:
        raise HTTPException(422, {"message": "нужен model_id"})
    async with svc.db.session() as s:
        bid = int((await s.execute(sa.insert(bench_t).values(
            model_id=model_id, kind=body.get("kind", "full"), status="queued",
            created_at=utcnow()))).inserted_primary_key[0])
        await s.commit()
    task = asyncio.create_task(_run_bench(svc, bid, model_id))
    if hasattr(svc, "_tasks"):
        svc._tasks.append(task)
    return {"benchmark_id": bid, "status": "queued"}


@router.get("/benchmarks")
async def list_bench(request: Request, model_id: int | None = None, limit: int = 50):
    svc = request.app.state.svc
    async with svc.db.session() as s:
        q = sa.select(bench_t)
        if model_id:
            q = q.where(bench_t.c.model_id == model_id)
        rows = (await s.execute(q.order_by(bench_t.c.id.desc()).limit(min(limit, 200)))).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/benchmarks/{bench_id:int}")
async def get_bench(bench_id: int, request: Request):
    svc = request.app.state.svc
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(bench_t).where(bench_t.c.id == bench_id))).first()
    if row is None:
        raise HTTPException(404, {"message": "benchmark не найден"})
    return dict(row._mapping)


@router.get("/benchmarks/compare")
async def compare(request: Request, ids: str):
    """Сравнение по реальным stored-результатам."""
    svc = request.app.state.svc
    id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()]
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(bench_t).where(bench_t.c.id.in_(id_list)))).fetchall()
    out = []
    for r in rows:
        b = dict(r._mapping)
        res = b.get("results") or {}
        out.append({"benchmark_id": b["id"], "model_id": b["model_id"],
                    "gen_tps": res.get("gen_tps"), "latency_ms": res.get("latency_ms_median"),
                    "ttft_ms": res.get("ttft_ms", res.get("ttft_ms_approx")),
                    "prompt_tps": res.get("prompt_tps"), "method": res.get("speed_method"),
                    "stability": (res.get("stability") or {}).get("success_rate")})
    return {"compared": out}


@router.get("/benchmarks/recommendations")
async def recommendations(request: Request):
    """Рекомендации по последним завершённым замерам (из данных, не констант)."""
    svc = request.app.state.svc
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(bench_t).where(
            bench_t.c.status == "completed").order_by(bench_t.c.id.desc()).limit(50))).fetchall()
    latest: dict[int, dict] = {}
    for r in rows:
        b = dict(r._mapping)
        latest.setdefault(b["model_id"], b)     # последний по модели
    # TEL-001: сравниваем только честные замеры (с методом), не старые «ток/латентность».
    ranked = [b for b in latest.values() if (b.get("results") or {}).get("gen_tps")
              and (b.get("results") or {}).get("speed_method") in ("server_timings", "differential")]
    fastest = max(ranked, key=lambda b: b["results"]["gen_tps"], default=None)
    return {"for_speed": (fastest and {"model_id": fastest["model_id"],
                                       "gen_tps": fastest["results"]["gen_tps"]}) or None,
            "based_on": len(ranked)}


FEATURE = Feature(name="benchlab", router=router)
