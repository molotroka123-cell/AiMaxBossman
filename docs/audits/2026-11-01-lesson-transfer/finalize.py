"""After ALL runs: score with the preregistered scorer and assemble results.json (+ publish truth and builder).

    python finalize.py <work dir> <keys dir>
"""
import hashlib
import json
import shutil
import sys
from pathlib import Path

import scorer as S

HERE = Path(__file__).resolve().parent
work, keys = Path(sys.argv[1]), Path(sys.argv[2])
prereg = json.loads((HERE / "prereg.json").read_text(encoding="utf-8"))
truth_bytes = (keys / "truth.json").read_bytes()
assert hashlib.sha256(truth_bytes).hexdigest() == prereg["ground_truth"]["truth_json_sha256"], "truth changed"
truth = json.loads(truth_bytes.decode("utf-8"))["truth"]
lesson = json.loads((work / "lesson.json").read_text(encoding="utf-8"))
lid = lesson["record"]["id"]
base_rows, after_rows = S.load(work / "raw_baseline.jsonl"), S.load(work / "raw_after.jsonl")
base = S.score(truth, base_rows, f"fact:{lid}")
after = S.score(truth, after_rows, f"fact:{lid}")
decision = S.decide(base, after, truth)


def secs(rows):
    v = sorted(float(r["seconds"]) for r in rows)
    return {"median": v[len(v) // 2], "max": v[-1], "n": len(v)}


exercise = json.loads((work / "exercise.json").read_text(encoding="utf-8")) if (work / "exercise.json").is_file() else None
survive = json.loads((work / "survive.json").read_text(encoding="utf-8"))
out = {
    "schema": "lesson-transfer-results.v1",
    "prereg_commit": "cc40df39", "truth_json_sha256": prereg["ground_truth"]["truth_json_sha256"],
    "model": prereg["model"]["ollama_name"], "repetitions": prereg["repetitions"],
    "decision": decision,
    "lesson": {"id": lid, "status_at_record": lesson["record"]["status"],
               "proven_learning_flag": lesson["record"]["proven_learning"], "duplicate_deduped": lesson["duplicate"],
               "author": lesson["after_record"]["author"], "source": lesson["after_record"]["source"],
               "observed_at": lesson["after_record"]["observed_at"], "valid_from": lesson["after_record"]["valid_from"],
               "remote_writer": lesson["after_record"]["remote_writer"],
               "limitations": lesson["after_record"]["limitations"],
               "provenance_negatives_http": lesson["negatives"],
               "lessons_total_incl_private_after_negatives": lesson["private_rows_written"]},
    "restart_survival": {"all_same": survive["all_same"], "fields": survive["same"]},
    "exercise_verify": (exercise or {}).get("verify"),
    "exercise_model_answer": (exercise or {}).get("parsed_answer"),
    "timing_seconds": {"baseline": secs(base_rows), "after": secs(after_rows)},
    "per_task": {tid: {"set": base[tid]["set"], "truth": truth[tid]["value"], "diag": truth[tid].get("diag"),
                       "baseline": {"n_correct": base[tid]["n_correct"], "pass": base[tid]["majority_pass"],
                                    "parsed": [r["parsed"] for r in base[tid]["reps"]]},
                       "after": {"n_correct": after[tid]["n_correct"], "pass": after[tid]["majority_pass"],
                                 "parsed": [r["parsed"] for r in after[tid]["reps"]],
                                 "lesson_recalled": [r["lesson_recalled"] for r in after[tid]["reps"]]}}
                 for tid in sorted(base)},
}
(HERE / "results.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
(HERE / "truth.json").write_bytes(truth_bytes)                       # post-hoc disclosure
shutil.copyfile(keys / "build_truth.py", HERE / "build_truth.py")
(HERE / "raw").mkdir(exist_ok=True)
for name in ("raw_baseline.jsonl", "raw_after.jsonl", "lesson.json", "survive.json", "exercise.json", "proxy.jsonl",
             "smoke.json", "setup.json"):
    if (work / name).is_file():
        shutil.copyfile(work / name, HERE / "raw" / name)
print(json.dumps(decision, ensure_ascii=False, indent=1))
