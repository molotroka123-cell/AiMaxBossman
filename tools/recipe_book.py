"""Recipe book: closed hard cases -> lessons in a Bossman instance's LessonBook.

Owner rule: every hard case the teacher closes becomes a reusable recipe in Bossman's
memory, so a model that stalls on the same shape of problem later gets the recipe by
itself (TASK_START recall in the task engine), through context and not through weights.

    python tools/recipe_book.py --data-dir <BCC_DATA_DIR> --recipes <recipes.json> [--check]

``recipes.json`` holds ``{"schema": "bossman.recipe-book/1", "recipes": [...]}``; each
recipe carries the CoachingEpisode fields plus ``evidence`` and ``verifier``. A recipe is
promoted to *verified* only with evidence whose ``actual`` equals ``expected``; without it
the lesson stays a *candidate* and recall will not serve it. Nothing here weakens that.
``--check`` opens a fresh LessonBook over the same directory (a restart) and prints what
retrieval returns for each recipe's own symptom.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from learning.lessons import CoachingEpisode, LessonBook, Provenance  # noqa: E402

EPISODE_FIELDS = {f for f in CoachingEpisode.__dataclass_fields__} - {"provenance"}


def _episode(recipe: dict) -> CoachingEpisode:
    prov = recipe.get("provenance") or {}
    fields = {k: v for k, v in recipe.items() if k in EPISODE_FIELDS}
    fields["provenance"] = Provenance(who=prov.get("who", "teacher:claude"),
                                      what=prov.get("what", "closed hard case"),
                                      evidence_refs=list(prov.get("evidence_refs") or []))
    return CoachingEpisode(**fields)


def publish(data_dir: Path, recipes: list[dict]) -> list[dict]:
    book = LessonBook(data_dir / "learning")
    report = []
    for recipe in recipes:
        ep = _episode(recipe)
        book.save(ep)
        evidence, verifier = recipe.get("evidence"), recipe.get("verifier")
        status = "candidate"
        if evidence and verifier and evidence.get("actual") == evidence.get("expected"):
            book.verify(ep.lesson_id, verifier=verifier, evidence=evidence)
            status = "verified"
        report.append({"lesson_id": ep.lesson_id, "title": ep.title, "status": status})
    return report


def check(data_dir: Path, recipes: list[dict]) -> list[dict]:
    book = LessonBook(data_dir / "learning")          # fresh object = what a restart sees
    out = []
    for recipe in recipes:
        rows = book.retrieve(project_id=recipe.get("project_id", "bossman"),
                             task_class=recipe.get("task_class"),
                             text=" ".join(recipe.get("symptoms") or [recipe["failure_observation"]]))
        ids = [r.get("lesson_id") or r.get("id") for r in rows]
        out.append({"title": recipe.get("title"), "retrieved": ids[:5]})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-dir", type=Path, required=True)
    ap.add_argument("--recipes", type=Path, required=True)
    ap.add_argument("--check", action="store_true", help="only read back after a fresh open")
    args = ap.parse_args(argv)
    doc = json.loads(args.recipes.read_text(encoding="utf-8"))
    if doc.get("schema") != "bossman.recipe-book/1":
        print("recipe_book: unknown schema", file=sys.stderr)
        return 2
    result = check(args.data_dir, doc["recipes"]) if args.check else publish(args.data_dir, doc["recipes"])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
