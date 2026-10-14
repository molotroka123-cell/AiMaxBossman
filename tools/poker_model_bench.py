"""Poker math bench: the SAME decision spots to local and FREE cloud models, scored against the recorded math.

    python tools/poker_model_bench.py --spots spots1000.jsonl --n 60 --out bench/

Spots come from pokertrain `tools/gto-lab.mjs` (every hero decision of a seeded 1000-hand mini-tournament with
equity, pot odds, EV). Each model gets the situation (cards, board, pot, to call, stack, opponents) and must answer
JSON {action, required_equity_pct, equity_pct_estimate, reason}. Scored: action vs the math-best action, pot-odds
arithmetic error, equity estimate error, valid-JSON rate, latency. Models are called through Bossman's own registry
(keys stay inside the process). Only models whose price is exactly 0 and pass free_only_refusal are used.

HONEST LIMIT: the reference is "equity vs random hands + pot odds" (a math baseline), not a solver/GTO solution.
For study and post-session review only — never as real-time assistance at a real table.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "command-center"))

SYSTEM = ("Ты эксперт по математике турнирного покера. Отвечай ТОЛЬКО одним JSON-объектом без пояснений вокруг: "
          '{"action": "fold|check|call|bet|raise|allin", "required_equity_pct": число, '
          '"equity_pct_estimate": число, "reason": "одна фраза"}. required_equity_pct — шансы банка для колла '
          "(toCall/(pot+toCall)*100), 0 если ставить не нужно.")
FAMILY = {"raise": "aggr", "bet": "aggr", "allin": "aggr", "call": "call", "check": "check", "fold": "fold"}


def spot_prompt(s: dict) -> str:
    return (f"Турнир 6-max, блайнды {s['blinds']['sb']}/{s['blinds']['bb']}"
            + (f" анте {s['blinds']['ante']}" if s['blinds'].get('ante') else "")
            + f". Улица: {s['stage']}. Позиция героя: {s['position']}. Карты героя: {' '.join(s['heroCards'])}. "
            f"Борд: {' '.join(s['board']) or 'нет'}. Банк: {s['pot']}. К коллу: {s['toCall']}. "
            f"Стек героя: {s['heroStack']} ({s['stackBB']} BB). Соперников в раздаче: {s['opponents']} "
            "(считай их руки случайными). Что математически правильно?")


def parse(text: str) -> dict | None:
    body = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    for cand in reversed(re.findall(r"\{[^{}]*\}", body)):          # reasoning models: the LAST JSON object counts
        try:
            d = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict) and str(d.get("action", "")).lower() in FAMILY:
            return d
    return None


def pick(spots: list[dict], n: int, seed: int) -> list[dict]:
    """Stratified: facing-a-bet spots first (unambiguous math), every street represented."""
    rnd = random.Random(seed)
    by = {}
    for s in spots:
        by.setdefault((s["stage"], s["toCall"] > 0), []).append(s)
    out = []
    keys = sorted(by, key=lambda k: (not k[1], k[0]))
    while len(out) < n and any(by.values()):
        for k in keys:
            if by[k] and len(out) < n:
                out.append(by[k].pop(rnd.randrange(len(by[k]))))
    return out


def score(spot: dict, ans: dict | None) -> dict:
    if ans is None:
        return {"valid": False}
    act = str(ans.get("action")).lower()
    best = spot["mathBest"]
    agree = FAMILY.get(act) == FAMILY.get(best) or (best == "call" and FAMILY.get(act) == "aggr" and spot["equity"] > 0.6)
    try:
        req_err = abs(float(ans.get("required_equity_pct", -1)) - spot["requiredEquity"] * 100) if spot["toCall"] > 0 else None
    except (TypeError, ValueError):
        req_err = 999.0
    try:
        eq_err = abs(float(ans.get("equity_pct_estimate", -1)) - spot["equity"] * 100)
    except (TypeError, ValueError):
        eq_err = 999.0
    return {"valid": True, "action": act, "agree": agree, "req_err": req_err, "eq_err": eq_err}


async def models_to_test(registry, include: list[str]) -> list[dict]:
    from bcc.provider_governance import free_only_refusal
    provs = {p["id"]: p for p in await registry.list_providers()}
    out = []
    for m in await registry.list_models():
        name = str(m.get("name") or "")
        if include and not any(x.lower() in name.lower() for x in include):
            continue
        local = m.get("kind") == "local"
        if not local:
            if m.get("price_in") not in (0, 0.0) or m.get("price_out") not in (0, 0.0):
                continue                                         # unknown price is NOT free
            if free_only_refusal(provs.get(m.get("provider_id")) or {}, m):
                continue
        out.append({**m, "_where": "локально" if local else f"облако ({(provs.get(m['provider_id']) or {}).get('name')})"})
    return out


async def run_model(registry, m: dict, spots: list[dict], timeout: float) -> dict:
    rows = []
    try:
        adapter, _ = await registry.adapter_for(m["id"])
    except Exception as exc:                                      # noqa: BLE001
        return {"model": m["name"], "where": m["_where"], "error": f"{type(exc).__name__}: {exc}"[:200], "rows": []}
    for s in spots:
        t = time.monotonic()
        try:
            res = await asyncio.wait_for(adapter.chat(m["name"], [{"role": "system", "content": SYSTEM},
                                                                  {"role": "user", "content": spot_prompt(s)}],
                                                      max_tokens=1500, temperature=0.0), timeout=timeout)
            text = res.text
        except Exception as exc:                                  # noqa: BLE001 — recorded as invalid
            text = f"ERROR {type(exc).__name__}"
        rows.append({"hand": s["hand"], "stage": s["stage"], "mathBest": s["mathBest"],
                     "seconds": round(time.monotonic() - t, 2), **score(s, parse(text)), "raw": text[:300]})
        if len(rows) >= 3 and not any(r.get("valid") for r in rows):
            break                                                 # a model that answers nothing is not waited on
    return {"model": m["name"], "where": m["_where"], "rows": rows}


def table(results: list[dict], n: int) -> str:
    lines = ["| Модель | Где | Ответов | Совпало с математикой | Ошибка шансов банка, п.п. (медиана) | "
             "Ошибка оценки эквити, п.п. (медиана) | Сек/ответ |", "|---|---|---|---|---|---|---|"]
    ranked = []
    for r in results:
        v = [x for x in r["rows"] if x.get("valid")]
        agree = sum(1 for x in v if x["agree"])
        req = [x["req_err"] for x in v if x.get("req_err") is not None]
        eq = [x["eq_err"] for x in v if x.get("eq_err") is not None]
        sec = [x["seconds"] for x in r["rows"]]
        ranked.append((agree / n if n else 0, r, len(v), agree, req, eq, sec))
    for share, r, nv, agree, req, eq, sec in sorted(ranked, key=lambda x: -x[0]):
        cells = [r["model"], r["where"], f"{nv}/{n}", f"{agree}/{n} ({share:.0%})",
                 f"{statistics.median(req):.1f}" if req else "—", f"{statistics.median(eq):.1f}" if eq else "—",
                 f"{statistics.median(sec):.1f}" if sec else "—"]
        lines.append("| " + " | ".join(cells) + " |" + (f" ошибка: {r['error']}" if r.get("error") else ""))
    return "\n".join(lines)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spots", required=True)
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--seed", type=int, default=14)
    ap.add_argument("--out", default="bench")
    ap.add_argument("--models", default="", help="comma-separated name fragments; empty = all free+local")
    ap.add_argument("--timeout", type=float, default=120)
    ap.add_argument("--ids", default="", help="comma-separated registry model ids (overrides --models)")
    a = ap.parse_args()
    from bcc.config import settings
    from bcc.db import Database
    from bcc.events import EventBus
    from bcc.registry import Registry
    from bcc.secrets import Vault
    db = Database(settings.database_url)
    registry = Registry(db, Vault(settings.data_dir), EventBus(db))
    spots = [json.loads(x) for x in Path(a.spots).read_text(encoding="utf-8").splitlines() if x.strip()]
    chosen = pick(spots, a.n, a.seed)
    models = await models_to_test(registry, [x.strip() for x in a.models.split(",") if x.strip()])
    if a.ids:
        wanted = [int(x) for x in a.ids.split(",") if x.strip()]
        models = [m for m in models if m["id"] in wanted]          # still filtered to free/local above
    print(f"spots {len(chosen)} of {len(spots)}; models {len(models)}: " + ", ".join(m["name"] for m in models), flush=True)
    cloud_sem, local_sem = asyncio.Semaphore(6), asyncio.Semaphore(1)   # local models one at a time (one GPU)

    async def one(m):
        async with (local_sem if m.get("kind") == "local" else cloud_sem):
            r = await run_model(registry, m, chosen, a.timeout)
            print(f"done {m['name']}: {sum(1 for x in r['rows'] if x.get('valid'))}/{len(chosen)} valid", flush=True)
            return r
    results = await asyncio.gather(*(one(m) for m in models))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "bench_results.json").write_text(json.dumps({"spots": chosen, "results": results}, ensure_ascii=False,
                                                       indent=1), encoding="utf-8")
    (out / "bench_table.md").write_text(table(results, len(chosen)), encoding="utf-8")
    print(table(results, len(chosen)))


if __name__ == "__main__":
    asyncio.run(main())
    sys.stdout.flush()
    import os
    os._exit(0)                     # the registry's DB pool keeps a thread alive; results are already written
