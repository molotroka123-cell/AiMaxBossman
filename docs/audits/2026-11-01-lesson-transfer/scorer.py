"""Independent scorer for the lesson-transfer experiment (code, not an LLM). Preregistered with prereg.json.

    python scorer.py <truth.json> <raw_baseline.jsonl> <raw_after.jsonl> <lesson_fact_id> [out.json]

Reads ONLY the truth file (kept outside the repo and the Bossman data dir during the runs) and the raw answers.
Never imports the task builder or Bossman. Applies the decision rule from prereg.json literally.
"""
from __future__ import annotations

import json
import math
import re
import sys
import unicodedata
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

HERE = Path(__file__).resolve().parent
#: Thousands groups may be split by a space / nbsp / thin space, never by a newline (two lines = two numbers).
_NUM = re.compile(r"[-−]?\d+(?:[    ]\d{3})*(?:[.,]\d+)*")


def _to_decimal(token: str) -> Decimal | None:
    t = (token or "").replace("−", "-")
    t = re.sub(r"[    ]", "", t)
    if "," in t and "." in t:
        t = t.replace(",", "")
    elif "," in t:
        t = t.replace(",", ".")
    if t.count(".") > 1:
        return None
    try:
        return Decimal(t)
    except (InvalidOperation, ValueError):
        return None


def _strip_md(text: str) -> str:
    return re.sub(r"[*_`$\\]", "", text or "")


def parse(answer: str, kind: str) -> tuple[str | None, bool]:
    """(parsed value, format_fallback). Value after the LAST 'ОТВЕТ:'; else the last number / last word."""
    text = _strip_md(str(answer or ""))
    idx = text.upper().rfind("ОТВЕТ:")
    fallback = idx < 0
    tail = text[idx + len("ОТВЕТ:"):] if idx >= 0 else text
    if kind == "text":
        words = re.findall(r"[^\W\d_]+", tail.split("\n")[0] if idx >= 0 else tail)
        return (words[0] if idx >= 0 and words else (words[-1] if words else None)), fallback
    nums = _NUM.findall(tail.split("\n")[0] if idx >= 0 else tail)
    if not nums:
        return None, fallback
    token = nums[0] if idx >= 0 else nums[-1]
    return token.strip(), fallback


def correct(answer: str, kind: str, truth: str) -> tuple[bool, str | None, bool]:
    value, fb = parse(answer, kind)
    if value is None:
        return False, None, fb
    if kind == "text":
        norm = lambda s: unicodedata.normalize("NFKC", s).casefold().strip(" .!,;:«»\"'")
        return norm(value) == norm(truth), value, fb
    got = _to_decimal(value)
    return (got is not None and got == Decimal(truth)), value, fb


def sign_test_one_sided(b: int, c: int) -> float:
    """P(X >= b) for X ~ Binomial(b + c, 0.5): improvements b vs regressions c among discordant tasks."""
    n = b + c
    if n == 0:
        return 1.0
    return sum(math.comb(n, k) for k in range(b, n + 1)) / 2 ** n


def load(path: str) -> list[dict]:
    return [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]


def score(truth: dict, runs: list[dict], lesson_ref: str | None) -> dict:
    per_task: dict[str, list[dict]] = defaultdict(list)
    for r in runs:
        t = truth[r["task"]]
        ok, val, fb = correct(r.get("answer") or "", t["kind"], t["value"])
        sources = [s for e in r.get("memory_events") or [] if e.get("kind") == "memory.recalled"
                   for s in e.get("sources") or []]
        per_task[r["task"]].append({
            "rep": r["rep"], "correct": ok and r.get("status") == "completed", "parsed": val,
            "format_fallback": fb, "status": r.get("status"),
            "lesson_recalled": bool(lesson_ref and lesson_ref in sources), "recalled_sources": sources,
            "seconds": r.get("seconds")})
    tasks = {}
    for tid, reps in per_task.items():
        n_ok = sum(1 for x in reps if x["correct"])
        tasks[tid] = {"set": next(r["set"] for r in runs if r["task"] == tid), "reps": sorted(reps, key=lambda x: x["rep"]),
                      "n_correct": n_ok, "n_runs": len(reps), "majority_pass": n_ok * 2 > len(reps)}
    return tasks


def decide(base: dict, after: dict, truth: dict) -> dict:
    sets = defaultdict(list)
    for tid, t in base.items():
        sets[t["set"]].append(tid)
    count = lambda phase, ids: sum(1 for i in ids if phase[i]["majority_pass"])
    H, I, C = sorted(sets["heldout"]), sorted(sets["independent"]), sorted(sets["control"])
    hb, ha = count(base, H), count(after, H)
    improved = [i for i in H if not base[i]["majority_pass"] and after[i]["majority_pass"]]
    worsened = [i for i in H if base[i]["majority_pass"] and not after[i]["majority_pass"]]
    p = sign_test_one_sided(len(improved), len(worsened))
    ctrl_reg = [i for i in C if base[i]["majority_pass"] and not after[i]["majority_pass"]]
    ctrl_gain = [i for i in C if not base[i]["majority_pass"] and after[i]["majority_pass"]]
    runs_after_h = [r for i in H for r in after[i]["reps"]]
    recall_rate_h = sum(r["lesson_recalled"] for r in runs_after_h) / max(1, len(runs_after_h))
    base_recalls = sum(r["lesson_recalled"] for t in base.values() for r in t["reps"])
    infra = {ph: sum(1 for t in d.values() for r in t["reps"] if r["status"] != "completed") /
             max(1, sum(len(t["reps"]) for t in d.values())) for ph, d in (("baseline", base), ("after", after))}
    delta = ha - hb
    reasons = []
    if hb >= 17:
        verdict = "INCONCLUSIVE"
        reasons.append("no headroom - transfer not demonstrable (baseline heldout >= 17/20)")
    elif recall_rate_h < 0.95 or base_recalls:
        verdict = "FAIL"
        reasons.append(f"recall gate: after heldout recall rate {recall_rate_h:.2f}, baseline recalls {base_recalls}")
    elif max(infra.values()) > 0.10:
        verdict = "INCONCLUSIVE"
        reasons.append(f"infra gate: non-completed run share {infra}")
    elif delta >= 8 and p <= 0.01 and not ctrl_reg:
        verdict = "PASS"
    elif delta <= 3 or len(ctrl_reg) >= 2:
        verdict = "FAIL"
        reasons.append(f"delta {delta} <= 3" if delta <= 3 else f"control regressions {ctrl_reg}")
    else:
        verdict = "INCONCLUSIVE"
        reasons.append(f"delta {delta}, p {p:.4g}, control regressions {ctrl_reg}")
    ib, ia = count(base, I), count(after, I)
    run_level = {ph: {s: f"{sum(r['correct'] for i in ids for r in d[i]['reps'])}/{sum(len(d[i]['reps']) for i in ids)}"
                      for s, ids in (("heldout", H), ("independent", I), ("control", C))}
                 for ph, d in (("baseline", base), ("after", after))}
    return {
        "verdict": verdict, "reasons": reasons,
        "heldout": {"baseline": hb, "after": ha, "of": len(H), "delta": delta, "improved": improved,
                    "worsened": worsened, "sign_test_p_one_sided": round(p, 6)},
        "independent": {"baseline": ib, "after": ia, "of": len(I), "delta": ia - ib,
                        "generalises": (ia - ib) >= 3},
        "control": {"baseline": count(base, C), "after": count(after, C), "of": len(C),
                    "regressions": ctrl_reg, "gains": ctrl_gain},
        "recall": {"after_heldout_rate": round(recall_rate_h, 4), "baseline_lesson_recalls": base_recalls,
                   "after_independent_rate": round(sum(r["lesson_recalled"] for i in I for r in after[i]["reps"])
                                                   / max(1, 3 * len(I)), 4),
                   "after_control_rate": round(sum(r["lesson_recalled"] for i in C for r in after[i]["reps"])
                                               / max(1, 3 * len(C)), 4)},
        "infra_failure_share": infra, "run_level": run_level,
    }


def main() -> None:
    truth_path, base_path, after_path, lesson_id = sys.argv[1:5]
    out = Path(sys.argv[5]) if len(sys.argv) > 5 else None
    truth = json.loads(Path(truth_path).read_text(encoding="utf-8"))["truth"]
    ref = f"fact:{lesson_id}"
    base = score(truth, load(base_path), ref)
    after = score(truth, load(after_path), ref)
    result = {"decision": decide(base, after, truth),
              "per_task": {tid: {"set": base[tid]["set"], "truth": truth[tid]["value"],
                                 "diag": truth[tid].get("diag"),
                                 "baseline": {"n_correct": base[tid]["n_correct"], "pass": base[tid]["majority_pass"],
                                              "parsed": [r["parsed"] for r in base[tid]["reps"]]},
                                 "after": {"n_correct": after[tid]["n_correct"], "pass": after[tid]["majority_pass"],
                                           "parsed": [r["parsed"] for r in after[tid]["reps"]],
                                           "lesson_recalled": [r["lesson_recalled"] for r in after[tid]["reps"]]}}
                           for tid in sorted(base)}}
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if out:
        out.write_text(text + "\n", encoding="utf-8")
    print(json.dumps(result["decision"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
