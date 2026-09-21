"""TEL-001 (owner audit 2026-09-21, P2): честная скорость модели.

Было: `tokens_out / вся латентность запроса` на ответе в 2–32 токена. В эту
латентность входит prefill и сетевые накладные, поэтому Qwen3.8 с настоящими
~10 ток/с генерации показывался как 1.6, а Qwen3.6 (~49) — как 3.3.

Стало — четыре раздельных показателя:
  ttft_ms           время до первого токена;
  prompt_tps        скорость чтения запроса (prefill);
  gen_tps           скорость генерации в установившемся режиме;
  latency_ms        полная латентность запроса.

Источник помечается в `method`:
  server_timings    llama.cpp вернул `timings` (prompt_per_second /
                    predicted_per_second) — берём их как есть;
  differential      сервер своих замеров не дал: запрос на 1 токен даёт TTFT,
                    длинный — (N−1) токенов за (L_N − L_1);
  unavailable       не измерить честно (ответ короче 8 токенов и т.п.) — null,
                    а не выдуманное число.
Через `adapter.chat` — значит через все обёртки (Fable-потолок, governance).
"""
from __future__ import annotations

import statistics
import time
from typing import Any

SPEED_PROMPT = ("Перечисли числа от 1 до 300 словами через запятую, "
                "без пояснений и без вступления.")
GEN_TOKENS = 128
MIN_GEN_TOKENS = 8


def _num(value: Any) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


async def _timed(adapter, model: str, max_tokens: int):
    t0 = time.perf_counter()
    res = await adapter.chat(model, [{"role": "user", "content": SPEED_PROMPT}],
                             max_tokens=max_tokens, temperature=0)
    return (time.perf_counter() - t0) * 1000, res


def from_timings(timings: dict, latency_ms: float, tokens_out: int) -> dict | None:
    """Показатели из собственных замеров llama.cpp. None — если их нет."""
    gen = _num(timings.get("predicted_per_second"))
    if gen is None:
        return None
    prompt_ms = _num(timings.get("prompt_ms"))
    return {
        "method": "server_timings",
        "ttft_ms": round(prompt_ms, 1) if prompt_ms else None,
        "prompt_tps": round(_num(timings.get("prompt_per_second")) or 0, 2) or None,
        "gen_tps": round(gen, 2),
        "latency_ms": round(latency_ms, 1),
        "tokens_out": int(timings.get("predicted_n") or tokens_out or 0),
        "prompt_tokens": int(timings.get("prompt_n") or 0) or None,
    }


async def measure_speed(adapter, model: str, *, gen_tokens: int = GEN_TOKENS) -> dict:
    """Один честный замер. Бросает исключение провайдера как есть."""
    latency_ms, res = await _timed(adapter, model, gen_tokens)
    meta = getattr(res, "provider_meta", None) or {}
    timings = meta.get("timings") if isinstance(meta, dict) else None
    if isinstance(timings, dict):
        out = from_timings(timings, latency_ms, res.tokens_out)
        if out is not None:
            out["answer"] = (res.text or "")[:120]
            return out

    first_ms, first = await _timed(adapter, model, 1)
    n = int(res.tokens_out or 0)
    window_ms = latency_ms - first_ms
    out: dict[str, Any] = {
        "ttft_ms": round(first_ms, 1),
        "prompt_tps": (round(first.tokens_in / (first_ms / 1000), 2)
                       if first.tokens_in and first_ms > 0 else None),
        "latency_ms": round(latency_ms, 1),
        "tokens_out": n,
        "prompt_tokens": int(res.tokens_in or 0) or None,
        "answer": (res.text or "")[:120],
    }
    if n >= MIN_GEN_TOKENS and window_ms > 0:
        out.update(method="differential", gen_tps=round((n - 1) / (window_ms / 1000), 2))
    else:
        out.update(method="unavailable", gen_tps=None,
                   note=f"ответ {n} ток. — слишком короткий для честной скорости генерации")
    return out


def median_of(runs: list[dict]) -> dict:
    """Медиана по прогонам; метод — худший из использованных (честнее)."""
    if not runs:
        return {"method": "unavailable"}
    out = dict(runs[-1])
    for key in ("ttft_ms", "prompt_tps", "gen_tps", "latency_ms"):
        vals = [r[key] for r in runs if r.get(key) is not None]
        out[key] = round(statistics.median(vals), 2) if vals else None
    order = ["server_timings", "differential", "unavailable"]
    out["method"] = max((r.get("method", "unavailable") for r in runs), key=order.index)
    out["runs"] = len(runs)
    return out
