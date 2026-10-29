"""M4 Transfer Report (TZ 2.5 §4 M4 & Argon Pair Prompt §3.3):
Evaluates transfer from ALLOWED_BY_TERMS cloud reasoning rows in D:\\Bossman\\datasets\\cloud-reasoning\\
onto >= 30 sealed holdout scenarios in coding/self-repair/pc_control/security/stop with paired bootstrap 95% CI
and McNemar exact p-value.

Privacy & Holdout Seal:
- Rows with training_use != 'ALLOWED_BY_TERMS' (e.g. PENDING_TERMS_CHECK) are strictly excluded and verified
  against coding_swarm.export_training_rows().
- Sealed holdout scenario texts and IDs are NEVER written to the report (only category counts and paired stats).
- If paired holdout runs are incomplete (< 30 paired verdicts) or 95% CI lower bound <= 0, the verdict is
  honestly recorded as NO_TRANSFER and lessons are not promoted into production prompts.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))
from eval500 import report, schema  # noqa: E402

DATASET_DIR = Path(r"D:\Bossman\datasets\cloud-reasoning")
TRANSFER_CATEGORIES = ("coding", "pc_control", "security", "stop")
MIN_HIDDEN_TASKS = 30


def _load_coding_swarm():
    spec = importlib.util.spec_from_file_location("coding_swarm", ROOT / "tools" / "coding_swarm.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def inspect_m4_dataset(dataset_dir: Path = DATASET_DIR) -> dict:
    cs = _load_coding_swarm()
    allowed_rows: list[dict] = []
    pending_rows: list[dict] = []
    files_read: list[str] = []

    if dataset_dir.is_dir():
        for fpath in sorted(dataset_dir.glob("*.jsonl")):
            files_read.append(fpath.name)
            for line in fpath.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("training_use") == "ALLOWED_BY_TERMS":
                    cs.validate_row(row)
                    allowed_rows.append(row)
                else:
                    pending_rows.append(row)

    exported = cs.export_training_rows(allowed_rows) if allowed_rows else []
    pending_blocked = True
    if pending_rows:
        try:
            cs.export_training_rows(pending_rows[:1])
            pending_blocked = False
        except (PermissionError, ValueError):
            pending_blocked = True

    lessons = []
    for r in allowed_rows:
        lessons.append({
            "id": r.get("id"),
            "provider": r.get("provider"),
            "model_id": r.get("model_id"),
            "task_id": r.get("task_id"),
            "prompt_sha256": r.get("prompt_sha256"),
            "reasoning_chars": len(r.get("reasoning") or ""),
            "answer_chars": len(r.get("answer") or ""),
            "has_answer": bool((r.get("answer") or "").strip()),
            "terms_ref": r.get("terms_ref"),
        })

    return {
        "dataset_dir": str(dataset_dir),
        "files": files_read,
        "allowed_by_terms_count": len(allowed_rows),
        "pending_terms_excluded_count": len(pending_rows),
        "exported_clean_count": len(exported),
        "pending_export_blocked_verified": pending_blocked,
        "lessons_summary": lessons,
    }


def evaluate_transfer(
    *,
    dataset_dir: Path = DATASET_DIR,
    before_run: str = "baseline-gemma41",
    after_run: str = "m4-lessons-candidate",
    holdout_scenarios: list[dict] | None = None,
    before_verdicts: dict | None = None,
    after_verdicts: dict | None = None,
) -> dict:
    ds_info = inspect_m4_dataset(dataset_dir)
    if holdout_scenarios is None:
        holdout_scenarios = (
            schema.load_holdout("report") if schema.HOLDOUT_FILE.is_file() else []
        )

    pool = [s for s in holdout_scenarios if s.get("category") in TRANSFER_CATEGORIES]
    by_cat: dict[str, int] = {}
    for s in pool:
        cat = s["category"]
        by_cat[cat] = by_cat.get(cat, 0) + 1

    bv = before_verdicts if before_verdicts is not None else report.load_verdicts(before_run, "holdout")
    av = after_verdicts if after_verdicts is not None else report.load_verdicts(after_run, "holdout")

    paired = [
        s for s in pool
        if (bv.get(s["id"]) or {}).get("verdict") not in (None, "error", "not_run")
        and (av.get(s["id"]) or {}).get("verdict") not in (None, "error", "not_run")
    ]

    b_scores = [int(bv[s["id"]].get("verdict") == "pass") for s in paired]
    a_scores = [int(av[s["id"]].get("verdict") == "pass") for s in paired]

    if len(paired) >= MIN_HIDDEN_TASKS:
        delta = (sum(a_scores) - sum(b_scores)) / len(paired)
        lo, hi = report.paired_bootstrap(b_scores, a_scores)
        pf = sum(1 for x, y in zip(b_scores, a_scores) if x and not y)
        fp = sum(1 for x, y in zip(b_scores, a_scores) if y and not x)
        p_val = report.mcnemar_exact(pf, fp)
        proven = bool(delta >= 0.05 and lo > 0.0 and pf == 0)
        verdict = "TRANSFER_PROVEN" if proven else "NO_TRANSFER"
        reason = (
            "paired holdout gain >= 5 pp with 95% CI > 0 and 0 regressions"
            if proven
            else f"gain={round(delta * 100, 2)}pp, CI95=[{round(lo * 100, 2)}, {round(hi * 100, 2)}]pp, regressions={pf}"
        )
    else:
        delta = (sum(a_scores) - sum(b_scores)) / len(paired) if paired else 0.0
        lo, hi = report.paired_bootstrap(b_scores, a_scores) if paired else (0.0, 0.0)
        pf = sum(1 for x, y in zip(b_scores, a_scores) if x and not y)
        fp = sum(1 for x, y in zip(b_scores, a_scores) if y and not x)
        p_val = report.mcnemar_exact(pf, fp) if paired else 1.0
        verdict = "NO_TRANSFER"
        reason = (
            f"paired holdout verdicts={len(paired)} < {MIN_HIDDEN_TASKS} required hidden tasks "
            f"(pool has {len(pool)} sealed tasks across { list(TRANSFER_CATEGORIES) }); "
            "per TZ 2.5 M4, lessons remain marked NO_TRANSFER and are not injected into production prompts"
        )

    return {
        "schema": "bossman.m4.transfer-report.v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "before_run": before_run,
        "after_run": after_run,
        "dataset": ds_info,
        "holdout_subset": {
            "categories": list(TRANSFER_CATEGORIES),
            "counts_by_category": by_cat,
            "total_hidden_tasks_in_pool": len(pool),
            "min_required_hidden_tasks": MIN_HIDDEN_TASKS,
            "paired_completed_tasks": len(paired),
            "holdout_ids_disclosed": False,
        },
        "statistics": {
            "baseline_pass_count": sum(b_scores),
            "with_lessons_pass_count": sum(a_scores),
            "gain_pp": round(delta * 100, 2),
            "ci95_pp": [round(lo * 100, 2), round(hi * 100, 2)],
            "mcnemar_p": round(p_val, 5),
            "pass_to_fail": pf,
            "fail_to_pass": fp,
        },
        "verdict": verdict,
        "production_prompt_status": "ACTIVE" if verdict == "TRANSFER_PROVEN" else "NO_TRANSFER_HELD_BACK",
        "reason": reason,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Generate M4 reasoning transfer report on >=30 sealed holdout tasks.")
    ap.add_argument("--before", default="baseline-gemma41")
    ap.add_argument("--after", default="m4-lessons-candidate")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    rep = evaluate_transfer(before_run=args.before, after_run=args.after)
    (out_dir / "receipt.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(json.dumps({
        "verdict": rep["verdict"],
        "allowed_rows": rep["dataset"]["allowed_by_terms_count"],
        "pending_excluded": rep["dataset"]["pending_terms_excluded_count"],
        "hidden_pool": rep["holdout_subset"]["total_hidden_tasks_in_pool"],
        "paired": rep["holdout_subset"]["paired_completed_tasks"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
