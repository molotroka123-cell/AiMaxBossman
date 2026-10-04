"""Apply model-produced unified diffs to isolated folders and run sealed tests.

Model inference must be submitted through Bossman's `/api/tasks`; this script
only grades the saved API results and never calls a model/provider.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess

from hidden_coding_tests import run_candidate


def extract_diff(text: str, expected_path: str) -> str:
    value = text.strip()
    match = re.fullmatch(r"```(?:diff|patch)?\s*\n(.*?)\n```", value, re.S)
    if match:
        value = match.group(1)
    # Normalize a path-only header when both sides clearly name the one
    # requested file. This repairs an omitted a/ and b/ prefix without
    # changing the patch body or allowing any extra path.
    lines = value.splitlines()
    if len(lines) >= 2 and lines[0] == f"--- {expected_path}" and lines[1] == f"+++ {expected_path}":
        lines[0] = f"--- a/{expected_path}"
        lines[1] = f"+++ b/{expected_path}"
        value = "\n".join(lines)
    paths = []
    for line in value.splitlines():
        if line.startswith(("--- a/", "+++ b/")):
            path = line[6:]
            if path != "/dev/null":
                paths.append(path)
    if not paths or any(p != expected_path for p in paths):
        raise ValueError(f"diff must modify only {expected_path}; got {paths!r}")
    if "@@" not in value:
        raise ValueError("not a unified diff")
    return value + "\n"


def apply_one(text: str, expected_path: str, folder: Path) -> dict:
    patch = extract_diff(text, expected_path)
    patch_path = folder / "candidate.patch"
    patch_path.write_text(patch, encoding="utf-8", newline="\n")
    # Some model-produced unified diffs get the @@ old/new line counts wrong
    # while preserving all hunks. Git's --recount repairs only those headers;
    # the exact file path and diff body are still validated and applied.
    check = subprocess.run(["git", "apply", "--recount", "--check", str(patch_path)],
                           cwd=folder, capture_output=True, text=True)
    if check.returncode:
        return {"applied": False, "error": (check.stderr or check.stdout).strip()[-1200:]}
    result = subprocess.run(["git", "apply", "--recount", str(patch_path)], cwd=folder,
                            capture_output=True, text=True)
    if result.returncode:
        return {"applied": False, "error": (result.stderr or result.stdout).strip()[-1200:]}
    return {"applied": True}


def score(responses_path: Path, corpus_path: Path, candidate_root: Path) -> dict:
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    responses = json.loads(responses_path.read_text(encoding="utf-8"))
    items = {item["id"]: item for item in corpus["items"]}
    reports = {}
    for slug, values in responses["models"].items():
        folder = (candidate_root / slug).resolve()
        folder.mkdir(parents=True, exist_ok=True)
        model_report = {}
        for task_id, response in values.items():
            item = items[task_id]
            try:
                applied = apply_one(response, item["path"], folder)
            except (ValueError, TypeError, AttributeError) as exc:
                # Invalid artifact format is an evaluation failure for this task,
                # but it must not abort scoring the other blinded responses.
                applied = {"applied": False, "error": str(exc)}
            model_report[task_id] = applied
        model_report["hidden_tests"] = run_candidate(folder)
        reports[slug] = model_report
    return {"schema": "bossman.model-market-coding-score/1", "models": reports}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("responses", type=Path)
    parser.add_argument("--corpus", type=Path, default=Path(__file__).with_name("coding_corpus.json"))
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = score(args.responses, args.corpus, args.candidate_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
