"""Save observed synthetic failures as unverified lessons in an isolated LessonBook."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from learning.lessons import CoachingEpisode, LessonBook, LessonPoisoned, Provenance  # noqa: E402

CURRICULUM = ROOT / "docs" / "learning" / "agent15" / "curriculum.json"


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass
    parser = argparse.ArgumentParser()
    parser.add_argument("--responses", type=Path, required=True)
    parser.add_argument("--memory", type=Path, required=True)
    parser.add_argument("--model", help="Only errors from this exact model")
    args = parser.parse_args()
    content = args.responses.read_bytes()
    evidence_hash = hashlib.sha256(content).hexdigest()
    curriculum = json.loads(CURRICULUM.read_text(encoding="utf-8"))
    zones = {z["id"]: z for z in curriculum["zones"]}
    book = LessonBook(args.memory)
    saved = []
    for line in content.decode("utf-8").splitlines():
        row = json.loads(line)
        if args.model and row["model"] != args.model:
            continue
        if row["arm"] != "baseline" or all(row["score"].values()):
            continue
        zone = zones[row["expected"]["zone"]]
        wrong = ", ".join(key for key, ok in row["score"].items() if not ok)
        correction = (f'For {zone["name"]} tasks, propose {zone["tool"]} as the next route. '
                      f'Check the result using {zone["verify"]}. '
                      'Keep external effects behind the owner decision and tool receipt. '
                      'A proposed call is not a completed action.')
        model = row["model"]
        case_id = row["case_id"]
        episode = CoachingEpisode(
            attempt_id=f"agent15:{model}:{case_id}:baseline",
            task_id=f"agent15:{model}:{case_id}",
            project_id="bossman-agent15-synthetic",
            failure_observation=f"Synthetic held-out case {case_id}: baseline failed {wrong}.",
            correction=correction,
            source="teacher", kind="teacher_patch", status="candidate", scope="project",
            provenance=Provenance(who="codex-agent15-evaluator", what="fixed-rubric synthetic feedback",
                                  evidence_refs=[f"sha256:{evidence_hash}", f"case:{case_id}"],
                                  run_id=f"agent15-{case_id}-{model}", model=model),
            task_class=zone["id"], environment="local-synthetic-eval", agent="agent15-evaluator",
            model=model, title=f"Agent15 candidate for {zone['name']}",
            refs={"evidence": [f"sha256:{evidence_hash}", f"case:{case_id}"]},
            recipe=["Identify the task goal and the permitted local scope.",
                    f'Propose the {zone["tool"]} route and obtain a tool observation before claiming an effect.',
                    f'Check the postcondition using {zone["verify"]}.'],
            check=f'Independent observation: {zone["verify"]}.',
            counterexample="A plausible model answer without a tool receipt is not completion.",
            assistance_level="teacher_patch", runtime="agent15-local",
        )
        try:
            record = book.save(episode)
            saved.append({"case_id": case_id, "model": model, "lesson_id": record["task_id"],
                          "status": record["learning_status"]})
        except LessonPoisoned as exc:
            saved.append({"case_id": case_id, "model": model, "status": "REJECTED_POISON_FILTER",
                          "reasons": exc.reasons})
    summary = {"status": "CANDIDATE_UNVERIFIED", "weights": "WEIGHTS_UNCHANGED",
               "evidence_sha256": evidence_hash, "saved": saved,
               "retrievable_verified": len(book.retrieve(project_id="bossman-agent15-synthetic", limit=100))}
    (args.memory / "agent15_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
