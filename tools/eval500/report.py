"""EVAL500 report: baseline receipt + failure clusters, and the pre-registered before/after comparison.

    python tools/eval500/report.py baseline --run-id baseline-cc7d1e42 --out docs/audits/2026-10-25-eval500-baseline
    python tools/eval500/report.py compare  --before baseline-cc7d1e42 --after <run> --out <dir>

Holdout privacy in git: failure clusters list scenario ids, reasons and inputs only for DEV; for the holdout only
counts per cluster tag (improvers must not learn the sealed scenarios from the report).
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from eval500 import judge, runner, schema  # noqa: E402

PREREG_FILE = schema.ROOT / "docs" / "audits" / "2026-10-25-eval500-baseline" / "preregistration.json"
BOOT_N = 10_000
BOOT_SEED = 20261025


def load_verdicts(run_id: str, part: str) -> dict:
    p = judge.verdicts_path(run_id, part)
    return json.loads(p.read_text(encoding="utf-8"))["verdicts"] if p.is_file() else {}


def _passed(v: dict | None) -> bool:
    return bool(v) and v.get("verdict") == "pass"


def rates(verdicts: dict, scenarios: list[dict]) -> dict:
    per = defaultdict(Counter)
    for s in scenarios:
        v = verdicts.get(s["id"])
        per[s["category"]][(v or {}).get("verdict") or "not_run"] += 1
    out = {}
    for cat in schema.CATEGORIES:
        c = per.get(cat, Counter())
        n = sum(c.values())
        out[cat] = {"n": n, **{k: c.get(k, 0) for k in ("pass", "partial", "fail", "error", "not_run")},
                    "pass_rate": round(c.get("pass", 0) / n, 4) if n else None,
                    "score": round((c.get("pass", 0) + 0.5 * c.get("partial", 0)) / n, 4) if n else None}
    tot = Counter()
    for c in per.values():
        tot.update(c)
    n = sum(tot.values())
    out["_total"] = {"n": n, **{k: tot.get(k, 0) for k in ("pass", "partial", "fail", "error", "not_run")},
                     "pass_rate": round(tot.get("pass", 0) / n, 4) if n else None,
                     "score": round((tot.get("pass", 0) + 0.5 * tot.get("partial", 0)) / n, 4) if n else None}
    return out


def agreement(verdicts: dict) -> dict:
    pairs = [(v["verdict"], v["recheck"]["verdict"]) for v in verdicts.values()
             if v.get("recheck") and v["recheck"].get("verdict") and v.get("verdict")]
    if not pairs:
        return {"rechecked": 0}
    exact = sum(a == b for a, b in pairs)
    binary = sum((a == "pass") == (b == "pass") for a, b in pairs)
    return {"rechecked": len(pairs), "exact_agreement": round(exact / len(pairs), 4),
            "pass_vs_not_agreement": round(binary / len(pairs), 4),
            "disagreements": Counter(f"{a}->{b}" for a, b in pairs if a != b)}


def clusters(verdicts: dict, scenarios: list[dict], with_ids: bool) -> list[dict]:
    by = defaultdict(list)
    sc = {s["id"]: s for s in scenarios}
    for sid, v in verdicts.items():
        if sid not in sc or v.get("verdict") == "pass":
            continue
        tag = str(v.get("cluster") or "none").strip().lower() or "none"
        by[tag].append((sid, v))
    out = []
    for tag, items in sorted(by.items(), key=lambda kv: -len(kv[1])):
        row = {"cluster": tag, "count": len(items),
               "categories": dict(Counter(sc[i]["category"] for i, _ in items)),
               "verdicts": dict(Counter(v.get("verdict") for _, v in items))}
        if with_ids:
            row["scenarios"] = [{"id": i, "verdict": v.get("verdict"), "reason": v.get("reason"),
                                 "input": schema.scenario_text(sc[i])[:200]} for i, v in items[:25]]
        out.append(row)
    return out


def run_stats(run_id: str, parts: list[str]) -> dict:
    state = runner.load_state(run_id)
    secs = [d.get("seconds") or 0 for d in state.get("done", {}).values()]
    return {"started_at": state.get("started_at"), "finished_at": state.get("finished_at"),
            "scenarios_done": len(state.get("done", {})), "scenario_seconds_sum": round(sum(secs), 1),
            "harness_errors": sum(1 for d in state.get("done", {}).values() if d.get("harness_error")),
            "identity": state.get("identity"), "prod_agent": state.get("prod_agent"),
            "clone_agent": state.get("clone_agent"), "clone_deleted": state.get("clone_deleted"),
            "cleanup": state.get("cleanup"),
            "task_id_range": _task_range(state)}


def _task_range(state: dict) -> list[int] | None:
    ids = [t for d in state.get("done", {}).values() for t in (d.get("task_ids") or [])]
    return [min(ids), max(ids)] if ids else None


def cost(verdicts_all: list[dict]) -> dict:
    llm = [v for v in verdicts_all if v.get("judge") == "llm"]
    return {"judge_calls": sum(int(v.get("judge_calls") or 0) for v in llm),
            "judge_tokens": sum(int(v.get("judge_tokens") or 0) for v in llm),
            "judge_cost_usd": round(sum(float(v.get("judge_cost_usd") or 0) for v in llm), 6),
            "recheck_calls": sum(int((v.get("recheck") or {}).get("calls") or 0) for v in verdicts_all),
            "recheck_cost_usd": round(sum(float((v.get("recheck") or {}).get("cost_usd") or 0)
                                          for v in verdicts_all), 6),
            "judges_by_kind": dict(Counter(v.get("judge") for v in verdicts_all)),
            "executor_cost_usd": None}


def baseline(run_id: str, out: Path, parts: list[str]) -> dict:
    dev = schema.load_dev()
    hold = schema.load_holdout("report") if "holdout" in parts else []
    vd, vh = load_verdicts(run_id, "dev"), (load_verdicts(run_id, "holdout") if hold else {})
    exec_cost = 0.0
    for part, items in (("dev", dev), ("holdout", hold)):
        for s in items:
            p = runner.transcript_path(run_id, part, s["id"])
            if p.is_file():
                tr = json.loads(p.read_text(encoding="utf-8"))
                exec_cost += sum(float((t.get("usage") or {}).get("cost_usd") or 0) for t in tr.get("turns", []))
    c = cost(list(vd.values()) + list(vh.values()))
    c["executor_cost_usd"] = round(exec_cost, 6)
    c["total_cloud_cost_usd"] = round(c["judge_cost_usd"] + c["recheck_cost_usd"], 6)
    prereg = json.loads(PREREG_FILE.read_text(encoding="utf-8")) if PREREG_FILE.is_file() else None
    receipt = {
        "schema": "bossman.eval500.baseline-receipt.v1", "run_id": run_id,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "suite": {"dev_file": "tests/owner_scenarios/eval500_dev.json", "dev_sha256": schema.file_sha(schema.DEV_FILE),
                  "dev_count": len(dev), "holdout_seal": "tests/owner_scenarios/eval500_holdout.sha256.json",
                  "holdout_file_sha256": json.loads(schema.HOLDOUT_HASH_FILE.read_text(encoding="utf-8"))["file_sha256"],
                  "holdout_count": schema.HOLDOUT_TOTAL},
        "run": run_stats(run_id, parts),
        "results": {"dev": rates(vd, dev), "holdout": rates(vh, hold) if hold else None,
                    "all": rates({**vd, **vh}, dev + hold)},
        "judge": {"primary": "nvidia/nemotron-3-ultra-550b-a55b (free: NIM / OpenRouter :free)",
                  "recheck": "gemini-3-flash-preview (seeded 10 % of LLM-judged)",
                  "prompt_version": judge.JUDGE_PROMPT_VERSION,
                  "agreement_dev": agreement(vd), "agreement_holdout": agreement(vh),
                  "agreement_all": agreement({**vd, **vh})},
        "cost": c,
        "suite_receipt": "docs/audits/2026-10-25-eval500-baseline/suite-receipt.json",
        "preregistration": {"file": str(PREREG_FILE.relative_to(schema.ROOT)).replace("\\", "/"),
                            "sha256": schema.file_sha(PREREG_FILE) if prereg else None},
    }
    fc = {"schema": "bossman.eval500.failure-clusters.v1", "run_id": run_id,
          "dev": clusters(vd, dev, with_ids=True),
          "holdout_counts_only": clusters(vh, hold, with_ids=False) if hold else None}
    out.mkdir(parents=True, exist_ok=True)
    (out / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=1, default=dict) + "\n",
                                      encoding="utf-8", newline="\n")
    (out / "failure_clusters.json").write_text(json.dumps(fc, ensure_ascii=False, indent=1) + "\n",
                                               encoding="utf-8", newline="\n")
    return receipt


# ---------------------------------------------------------------- pre-registered comparison

def paired_bootstrap(before: list[int], after: list[int], n: int = BOOT_N, seed: int = BOOT_SEED) -> tuple[float, float]:
    rng = random.Random(seed)
    k = len(before)
    diffs = []
    for _ in range(n):
        idx = [rng.randrange(k) for _ in range(k)]
        diffs.append(sum(after[i] - before[i] for i in idx) / k)
    diffs.sort()
    return diffs[int(0.025 * n)], diffs[int(0.975 * n) - 1]


def mcnemar_exact(b: int, c: int) -> float:
    """b = pass->fail, c = fail->pass; two-sided exact binomial p."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * p)


def compare(before_run: str, after_run: str) -> dict:
    prereg = json.loads(PREREG_FILE.read_text(encoding="utf-8"))
    th = prereg["thresholds"]
    dev, hold = schema.load_dev(), schema.load_holdout("report")
    bd, ad = load_verdicts(before_run, "dev"), load_verdicts(after_run, "dev")
    bh, ah = load_verdicts(before_run, "holdout"), load_verdicts(after_run, "holdout")
    valid = [s for s in hold if (bh.get(s["id"]) or {}).get("verdict") not in (None, "error")
             and (ah.get(s["id"]) or {}).get("verdict") not in (None, "error")]
    b = [int(_passed(bh[s["id"]])) for s in valid]
    a = [int(_passed(ah[s["id"]])) for s in valid]
    delta = (sum(a) - sum(b)) / len(valid) if valid else 0.0
    lo, hi = paired_bootstrap(b, a) if valid else (0.0, 0.0)
    pf = sum(1 for x, y in zip(b, a) if x and not y)
    fp = sum(1 for x, y in zip(b, a) if y and not x)
    rb, ra = rates({**bd, **bh}, dev + hold), rates({**ad, **ah}, dev + hold)
    cat_drop = {cat: round(((ra[cat]["pass_rate"] or 0) - (rb[cat]["pass_rate"] or 0)) * 100, 2)
                for cat in schema.CATEGORIES}
    safety_regressions = [s["id"] for s in dev + hold if s["category"] in schema.SAFETY_CATEGORIES
                          and _passed({**bd, **bh}.get(s["id"])) and not _passed({**ad, **ah}.get(s["id"]))]
    checks = {
        "holdout_gain_pp": round(delta * 100, 2), "holdout_gain_ci95_pp": [round(lo * 100, 2), round(hi * 100, 2)],
        "holdout_paired_n": len(valid), "mcnemar_p": round(mcnemar_exact(pf, fp), 5),
        "pass_to_fail": pf, "fail_to_pass": fp,
        "category_change_pp": cat_drop, "safety_regressions": safety_regressions,
    }
    ok = (checks["holdout_gain_pp"] >= th["holdout_min_gain_pp"]
          and checks["holdout_gain_ci95_pp"][0] > th["holdout_ci95_lower_bound_gt_pp"]
          and all(v >= -th["max_category_drop_pp"] for v in cat_drop.values())
          and len(safety_regressions) <= th["max_safety_regressions"])
    return {"schema": "bossman.eval500.compare.v1", "before": before_run, "after": after_run,
            "preregistration_sha256": schema.file_sha(PREREG_FILE), "checks": checks,
            "verdict": "SELF_IMPROVEMENT_PROVEN" if ok else "NOT_PROVEN"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mode", choices=["baseline", "compare"])
    ap.add_argument("--run-id")
    ap.add_argument("--before")
    ap.add_argument("--after")
    ap.add_argument("--parts", default="dev,holdout")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    out = Path(a.out)
    if a.mode == "baseline":
        r = baseline(a.run_id, out, [p for p in a.parts.split(",") if p])
        print(json.dumps({"total": r["results"]["all"]["_total"], "cost": r["cost"]}, ensure_ascii=False))
    else:
        r = compare(a.before, a.after)
        out.mkdir(parents=True, exist_ok=True)
        (out / "compare.json").write_text(json.dumps(r, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(json.dumps(r["checks"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
