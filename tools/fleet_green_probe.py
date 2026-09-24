#!/usr/bin/env python3
"""MODEL FLEET GREEN probe for one model: DISCOVER -> VERIFY SOURCE -> LOAD ->
CHAT -> JSON -> TOOL -> CODING -> ERROR RECOVERY -> BENCHMARK.

Reuses the graded bake-off tasks A–G (tools/model_bakeoff.py: code executed,
tool calls inspected, JSON parsed) so a remote model is comparable with the
lab table, and adds measured streaming latency, a strict typed-JSON check and
a rate-limit burst. Every call is recorded by tools/distill_recorder.py.

    python tools/fleet_green_probe.py --base https://openrouter.ai/api/v1 \
        --model nvidia/nemotron-3-ultra-550b-a55b:free --runs 3 --out <dir>

Verdict: GREEN only if every stage passes in >= 2/3 runs; DEGRADED if chat
works but a stage is below that; BROKEN if chat itself fails.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import pathlib
import statistics
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import model_bakeoff as bk  # noqa: E402
from distill_recorder import Recorder  # noqa: E402
from worker_client import Worker  # noqa: E402

STAGE_OF = {"A_bug": "CODING", "B_repo_nav": "CHAT", "C_patch_regression": "CODING", "D_tool_select": "TOOL",
            "E_json": "JSON", "F_tool_recovery": "ERROR_RECOVERY", "G_context": "CONTEXT"}


def strict_json(w: Worker) -> tuple[bool, str]:
    prompt = ("Return ONLY JSON matching this schema, no prose, no code fence: "
              '{"symbol": string (upper case ticker), "oi": number, "oi_unit": one of "K"|"M"|"B", '
              '"confidence": number between 0 and 1, "unreadable": boolean}. '
              "Input: a chart caption reads 'BTCUSDT  Open Interest 84.21K BTC' and is sharp.")
    o = w.chat([{"role": "user", "content": prompt}], task_class="strict_json", max_tokens=4000)
    txt = (o["text"] or "").strip()
    try:
        d = json.loads(txt)
    except ValueError:
        return False, f"not raw JSON: {txt[:120]}"
    ok = (set(d) == {"symbol", "oi", "oi_unit", "confidence", "unreadable"} and d["symbol"] == "BTCUSDT"
          and isinstance(d["oi"], (int, float)) and abs(d["oi"] - 84.21) < 1e-6 and d["oi_unit"] == "K"
          and isinstance(d["confidence"], (int, float)) and 0 <= d["confidence"] <= 1 and d["unreadable"] is False)
    return ok, txt[:160]


def latency(w: Worker, n: int) -> dict:
    rows = []
    for _ in range(n):
        o = w.chat([{"role": "user", "content": "Write three sentences about the Vltava river."}],
                   task_class="latency_probe", max_tokens=1500, temperature=0.7)
        rows.append({"ttft_ms": o["ttft_ms"], "tps": o["tps"], "latency_s": o["latency_s"],
                     "completion_tokens": (o["usage"] or {}).get("completion_tokens"), "error": o["error"]})
    ok = [r for r in rows if not r["error"] and r["ttft_ms"]]
    med = lambda k: statistics.median([r[k] for r in ok if r[k]]) if ok else None  # noqa: E731
    return {"runs": rows, "ttft_ms_median": med("ttft_ms"), "tps_median": med("tps")}


def burst(w: Worker, n: int) -> dict:
    solo = Worker(w.base, w.model, recorder=w.recorder, max_retries=0)
    def one(i):
        return solo.chat([{"role": "user", "content": f"Reply with the number {i} only."}],
                         task_class="rate_limit_burst", max_tokens=800)
    with cf.ThreadPoolExecutor(n) as ex:
        outs = list(ex.map(one, range(n)))
    return {"parallel": n, "ok": sum(1 for o in outs if not o["error"]),
            "errors": [o["error"][:120] for o in outs if o["error"]], "events": solo.events}


def main(argv=None) -> int:
    bk.utf8_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tag", default="")
    ap.add_argument("--burst", type=int, default=8)
    ap.add_argument("--max-tokens", type=int, default=16000)
    ap.add_argument("--extra-body", default="{}", help="JSON merged into each request (e.g. local think off)")
    ns = ap.parse_args(argv)
    out = pathlib.Path(ns.out)
    out.mkdir(parents=True, exist_ok=True)
    tag = ns.tag or ns.model.replace("/", "_").replace(":", "_")
    rec = Recorder(f"fleet-probe-{tag}")
    w = Worker(ns.base, ns.model, recorder=rec, extra_body=json.loads(ns.extra_body))

    def chat_adapter(messages, tools=None):
        o = w.chat(messages, tools=tools, task_class="bakeoff", max_tokens=ns.max_tokens)
        if o["error"]:
            raise RuntimeError(o["error"])
        return {"text": o["text"], "tool_calls": o["tool_calls"]}

    runs = []
    for i in range(ns.runs):
        t0 = time.time()
        stats: list[dict] = []
        res = bk.run_tasks(chat_adapter, stats, tag=f"{tag} run{i + 1}")
        ok_json, note_json = strict_json(w)
        res["S_strict_typed_json"] = {"pass": ok_json, "note": note_json}
        runs.append({"run": i + 1, "seconds": round(time.time() - t0, 1), "results": res})
        print(f"{tag} run{i + 1}: {sum(r['pass'] for r in res.values())}/{len(res)} "
              f"S_strict={'PASS' if ok_json else 'FAIL'}", flush=True)
    lat = latency(w, max(3, ns.runs))
    rl = burst(w, ns.burst) if ns.burst else {"skipped": True}

    names = list(runs[0]["results"])
    per_task = {n: sum(r["results"][n]["pass"] for r in runs) for n in names}
    stages: dict[str, bool] = {}
    for n, c in per_task.items():
        st = STAGE_OF.get(n, "JSON")
        stages[st] = stages.get(st, True) and c * 3 >= 2 * ns.runs
    chat_ok = lat["ttft_ms_median"] is not None
    verdict = "BROKEN" if not chat_ok else ("GREEN" if all(stages.values()) else "DEGRADED")
    summary = {"schema": "bossman.fleet_green.v1", "model": ns.model, "base": ns.base,
               "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "runs": ns.runs,
               "per_task_pass": per_task, "stages": stages, "latency": lat, "rate_limit_burst": rl,
               "retry_events": w.events, "verdict": verdict, "records": rec.count,
               "dataset": str(rec.path), "weights_changed": False, "detail": runs}
    (out / f"fleet-green-{tag}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("model", "per_task_pass", "stages", "verdict", "records")},
                     ensure_ascii=False))
    print("latency:", {k: lat[k] for k in ("ttft_ms_median", "tps_median")}, "burst:",
          {k: rl.get(k) for k in ("parallel", "ok")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
