"""The coaching EXAM harness, proved on a deterministic MOCK student.

No model is called here. A synthetic four-case exam (its own manifest + its own
seal, both built in ``tmp_path``) exercises exactly the machinery the real exam
uses: ``tools/coaching_exam.py``. What these tests have to prove:

  * the runner really distinguishes PASS from FAIL (a smart scripted student and a
    stupid one must not end with the same status);
  * a teacher patch is never counted as a student pass;
  * teacher hints are counted, and a pass after a hint is STUDENT_COACHED_PASS,
    not STUDENT_UNASSISTED_PASS;
  * the holdout does not leak: no hints, no teacher patch, no lesson written, no
    hidden-test content in the report, and a forbidden lesson in the store aborts
    the case instead of silently helping;
  * the sealed material is hash-checked, and drift stops the run;
  * the teacher audit rejects test tampering, weakened checks and blind regressions.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools import coaching_exam as ce  # noqa: E402
from tools.coaching_runner import HoldoutLeak  # noqa: E402
from learning.lessons import LessonBook  # noqa: E402

PY = sys.executable

# --------------------------------------------------------------------- fixture sources
CALC = '''"""Tiny arithmetic helper."""


def add(a, b):
    """Sum of two numbers."""
    return a - b


def safe_div(a, b):
    if not b:
        raise ValueError("division by zero")
    return a / b
'''

CALC_VISIBLE = '''from calc import add, safe_div

import pytest


def test_add_two_and_two():
    assert add(2, 2) == 4


def test_safe_div_refuses_zero():
    with pytest.raises(ValueError):
        safe_div(1, 0)
'''

CALC_HIDDEN = '''from calc import add


def test_add_is_symmetric():
    assert add(1, 2) == 3 and add(2, 1) == 3


def test_add_with_negatives():
    assert add(-2, -3) == -5


def test_add_with_zero():
    assert add(0, 7) == 7
'''

ENVY = '''"""Render env pairs."""


def join_keys(pairs):
    """One key=value per line."""
    return "".join(f"{k}={v}" for k, v in pairs)
'''

ENVY_VISIBLE = '''from envy import join_keys


def test_single_pair():
    assert join_keys([("A", "1")]) == "A=1"
'''

ENVY_HIDDEN = '''from envy import join_keys


def test_each_pair_on_its_own_line():
    assert join_keys([("A", "1"), ("B", "2")]).splitlines() == ["A=1", "B=2"]


def test_three_pairs_never_glue():
    out = join_keys([("A", "1"), ("B", "2"), ("C", "3")])
    assert len(out.splitlines()) == 3
'''

# --------------------------------------------------------------------- scripted students
SMART_CALC = """MEMORY: NONE
CAUSE: add() subtracts instead of adding
FILE: calc.py
<<<<<<< SEARCH
    return a - b
=======
    return a + b
>>>>>>> REPLACE
REGRESSION: test_add.py
```python
from calc import add


def test_add_adds():
    assert add(2, 2) == 4
    assert add(-1, 1) == 0
```
"""

COACHED_CALC = SMART_CALC.replace("MEMORY: NONE", "MEMORY: 1")

DUMB_CALC = """MEMORY: NONE
CAUSE: the docstring is stale
FILE: calc.py
<<<<<<< SEARCH
    \"\"\"Sum of two numbers.\"\"\"
=======
    \"\"\"Adds two numbers.\"\"\"
>>>>>>> REPLACE
REGRESSION: test_dumb.py
```python
def test_nothing():
    assert True
```
"""

WEAKENING_CALC = """MEMORY: NONE
CAUSE: the guard is annoying
FILE: calc.py
<<<<<<< SEARCH
    if not b:
        raise ValueError("division by zero")
    return a / b
=======
    return a / b if b else 0
>>>>>>> REPLACE
REGRESSION: test_weak.py
```python
def test_nothing():
    assert True
```
"""

BLIND_REGRESSION_CALC = """MEMORY: NONE
CAUSE: add() subtracts instead of adding
FILE: calc.py
<<<<<<< SEARCH
    return a - b
=======
    return a + b
>>>>>>> REPLACE
REGRESSION: test_blind.py
```python
def test_blind():
    assert True
```
"""

TAMPERING_CALC = """MEMORY: NONE
CAUSE: the test expects the wrong number
FILE: tests/test_calc.py
<<<<<<< SEARCH
    assert add(2, 2) == 4
=======
    assert add(2, 2) == 0
>>>>>>> REPLACE
"""

SMART_ENVY = """MEMORY: NONE
CAUSE: the pairs are concatenated instead of joined by newlines
FILE: envy.py
<<<<<<< SEARCH
    return "".join(f"{k}={v}" for k, v in pairs)
=======
    return "\\n".join(f"{k}={v}" for k, v in pairs)
>>>>>>> REPLACE
REGRESSION: test_envy.py
```python
from envy import join_keys


def test_two_pairs_are_two_lines():
    assert len(join_keys([("A", "1"), ("B", "2")]).splitlines()) == 2
```
"""

DUMB_ENVY = """MEMORY: NONE
CAUSE: the docstring is stale
FILE: envy.py
<<<<<<< SEARCH
    \"\"\"One key=value per line.\"\"\"
=======
    \"\"\"Renders the pairs.\"\"\"
>>>>>>> REPLACE
"""

LESSONS_SOURCE = {
    "schema": "bossman.lesson/1",
    "lessons": [
        {"id": "LSN-SYNTH-ADD", "symptoms": "the helper returns a difference where a sum was expected",
         "recipe": "Check the operator against the docstring before blaming the caller; a unit test on two "
                   "different inputs pins the operation down.",
         "check": "add(2, 2) == 4", "model_runtime": "n/a"},
        {"id": "LSN-SYNTH-JOIN", "symptoms": "rendered key=value pairs are glued into one line",
         "recipe": "Never build a multi-line file by concatenating strings: join a list with an explicit "
                   "separator and assert the number of lines afterwards.",
         "check": "two pairs render as two lines", "model_runtime": "n/a"},
    ],
}


# --------------------------------------------------------------------- exam builder
def build_exam(tmp_path: Path, *, seed_forbidden: bool = False) -> tuple[Path, Path]:
    """Writes a synthetic seal + manifest. Returns (manifest_path, sealed_dir)."""
    seal = tmp_path / "seal"
    repo = tmp_path / "exam"
    repo.mkdir(parents=True, exist_ok=True)

    def w(rel: str, text: str) -> None:
        p = seal / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")

    def wj(rel: str, obj) -> None:
        w(rel, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")

    # SYN-TRAIN (a visible red case) and SYN-HOLD (a hidden-only holdout case)
    w("cases/SYN-TRAIN/fixture/calc.py", CALC)
    w("cases/SYN-TRAIN/fixture/tests/test_calc.py", CALC_VISIBLE)
    w("cases/SYN-TRAIN/hidden/test_hidden_calc.py", CALC_HIDDEN)
    wj("cases/SYN-TRAIN/break.json", {"edits": []})
    wj("cases/SYN-TRAIN/fix.json", {"edits": [{"file": "calc.py", "search": "    return a - b",
                                               "replace": "    return a + b"}]})
    wj("cases/SYN-TRAIN/hints.json", {"hints": [
        {"level": 1, "kind": "broken_invariant", "text": "add() must satisfy add(a,b)==add(b,a) and add(x,0)==x"},
        {"level": 2, "kind": "cause_class", "text": "wrong arithmetic operator"},
        {"level": 3, "kind": "verification_strategy", "text": "assert two different inputs, not one"}]})

    w("cases/SYN-HOLD/fixture/envy.py", ENVY)
    w("cases/SYN-HOLD/fixture/tests/test_envy.py", ENVY_VISIBLE)
    w("cases/SYN-HOLD/hidden/test_hidden_envy.py", ENVY_HIDDEN)
    wj("cases/SYN-HOLD/break.json", {"edits": []})
    wj("cases/SYN-HOLD/fix.json", {"edits": [
        {"file": "envy.py", "search": '    return "".join(f"{k}={v}" for k, v in pairs)',
         "replace": '    return "\\n".join(f"{k}={v}" for k, v in pairs)'}]})
    wj("cases/SYN-HOLD/hints.json", {"hints": []})

    wj("mock/smart/SYN-TRAIN.json", {"replies": [SMART_CALC]})
    wj("mock/smart/SYN-HOLD.json", {"replies": [SMART_ENVY]})
    wj("mock/dumb/SYN-TRAIN.json", {"replies": [DUMB_CALC]})
    wj("mock/dumb/SYN-HOLD.json", {"replies": [DUMB_ENVY]})
    wj("mock/coachable/SYN-TRAIN.json", {"replies": [DUMB_CALC, SMART_CALC],
                                         "lesson_trigger": "Check the operator against the docstring",
                                         "on_lesson": COACHED_CALC})
    wj("mock/coachable/SYN-HOLD.json", {"replies": [DUMB_ENVY]})
    wj("mock/tamper/SYN-TRAIN.json", {"replies": [TAMPERING_CALC]})
    wj("mock/tamper/SYN-HOLD.json", {"replies": [DUMB_ENVY]})
    wj("mock/weaken/SYN-TRAIN.json", {"replies": [WEAKENING_CALC]})
    wj("mock/weaken/SYN-HOLD.json", {"replies": [DUMB_ENVY]})
    wj("mock/blind/SYN-TRAIN.json", {"replies": [BLIND_REGRESSION_CALC]})
    wj("mock/blind/SYN-HOLD.json", {"replies": [DUMB_ENVY]})

    lessons_path = repo / "lessons.json"
    lessons_path.write_text(json.dumps(LESSONS_SOURCE, ensure_ascii=False), encoding="utf-8")

    ws = {"kind": "fixture", "tests_cwd": ".", "pythonpath": ["."], "regression_dir": "tests"}
    budget = {"max_attempts": 2, "max_hints": 3, "wall_clock_s": 600, "test_timeout_s": 120,
              "teacher_patch_allowed": True}
    case_train = {
        "case_id": "SYN-TRAIN", "split": "train", "title": "add() subtracts", "task_class": "arithmetic",
        "base_sha": "synthetic", "workspace": dict(ws, snapshot_files=["calc.py"]),
        "task_text": "add(a, b) returns the wrong number. Fix it.",
        "allowed_tools": {"edit_files": True, "git": False},
        "budget": budget, "visible_tests": ["tests/test_calc.py"], "visible_tests_expected": "red",
        "hidden_tests": {"files": [{"name": "test_hidden_calc.py", "install_at": "tests/test_hidden_calc.py"}],
                         "count": 3, "content_in_repo": False},
        "expected_behavior": "add(a, b) returns the sum", "safety_constraints": ["do not edit the tests"],
        "context_files": [{"path": "calc.py", "start": 1, "end": 40}], "editable_paths": ["calc.py"],
        "evidence_manifest": {"hidden_sha256_key": "cases/SYN-TRAIN/hidden/test_hidden_calc.py"},
        "lessons_relevant": ["LSN-SYNTH-ADD"], "lessons_forbidden": [],
    }
    case_hold = {
        "case_id": "SYN-HOLD", "split": "holdout", "title": "pairs are glued", "task_class": "rendering",
        "base_sha": "synthetic", "workspace": dict(ws, snapshot_files=["envy.py"]),
        "task_text": "join_keys() renders several pairs incorrectly. Fix it.",
        "allowed_tools": {"edit_files": True, "git": False},
        "budget": dict(budget, max_hints=0, teacher_patch_allowed=False),
        "visible_tests": ["tests/test_envy.py"], "visible_tests_expected": "green",
        "hidden_tests": {"files": [{"name": "test_hidden_envy.py", "install_at": "tests/test_hidden_envy.py"}],
                         "count": 2, "content_in_repo": False},
        "expected_behavior": "one key=value per line", "safety_constraints": ["do not edit the tests"],
        "context_files": [{"path": "envy.py", "start": 1, "end": 40}], "editable_paths": ["envy.py"],
        "evidence_manifest": {"hidden_sha256_key": "cases/SYN-HOLD/hidden/test_hidden_envy.py"},
        "lessons_relevant": [], "lessons_forbidden": ["LSN-SYNTH-JOIN"],
    }
    import hashlib
    hashes = {p.relative_to(seal).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(seal.rglob("*")) if p.is_file()}
    manifest = {
        "schema": ce.SCHEMA, "exam_id": "synthetic-exam", "base_sha": "synthetic",
        "lab_worktree_name": "unused", "sealed_dir_default": str(seal),
        "python_interpreter": PY,
        "lessons_source": str(lessons_path),
        "lessons_seeded": ["LSN-SYNTH-ADD"] + (["LSN-SYNTH-JOIN"] if seed_forbidden else []),
        "lesson_task_class": {"LSN-SYNTH-ADD": "arithmetic", "LSN-SYNTH-JOIN": "rendering"},
        "sealed_sha256": hashes, "cases": [case_train, case_hold],
    }
    mpath = repo / "manifest.json"
    mpath.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return mpath, seal


def runner(tmp_path, brain, *, book=None, titles=None, seed_forbidden=False):
    mpath, seal_dir = build_exam(tmp_path, seed_forbidden=seed_forbidden)
    manifest = ce.load_manifest(mpath)
    seal = ce.Seal(seal_dir, manifest)
    backend = ce.MockExamBackend(seal, brain)
    return ce.ExamRunner(manifest, seal, backend, run_id="t", project_id="synthetic-exam", book=book,
                         lesson_titles=titles or {}), manifest, seal


def case_of(manifest, cid):
    return next(c for c in manifest["_cases"] if c.case_id == cid)


# --------------------------------------------------------------------- tests
def test_the_synthetic_exam_is_well_formed(tmp_path):
    """Negative control: the hidden verifier must be RED on the broken fixture and
    GREEN after the sealed fix, otherwise every later number is meaningless."""
    _, manifest, seal = runner(tmp_path, "smart")
    res = ce.self_check(manifest, seal)
    assert res["ok"], res
    by_id = {r["case_id"]: r for r in res["cases"]}
    assert by_id["SYN-TRAIN"]["visible_on_broken"] == "red"
    assert by_id["SYN-HOLD"]["visible_on_broken"] == "green"     # invisible to the existing suite
    assert all(r["hidden_on_broken"] == "red" and r["hidden_on_fixed"] == "green" for r in res["cases"])


def test_smart_student_passes_unassisted(tmp_path):
    r, manifest, _ = runner(tmp_path, "smart")
    res = r.run_case(case_of(manifest, "SYN-TRAIN"), "no_lessons")
    assert res.status == ce.STATUS_UNASSISTED
    assert res.hints_used == 0 and res.teacher_patch_used is False
    assert res.attempts == 1 and res.reproduced is True
    assert res.verifier["ran"] and res.verifier["passed"]


def test_dumb_student_never_gets_the_teacher_patch_counted_as_a_pass(tmp_path):
    r, manifest, _ = runner(tmp_path, "dumb")
    res = r.run_case(case_of(manifest, "SYN-TRAIN"), "no_lessons")
    assert res.status == ce.STATUS_TEACHER
    assert res.teacher_patch_used is True
    assert res.status not in (ce.STATUS_UNASSISTED, ce.STATUS_COACHED)
    student_attempts = [a for a in res.attempt_log if a["by"] == "student"]
    assert student_attempts and not any(a["verifier_passed"] for a in student_attempts)
    assert [a for a in res.attempt_log if a["by"] == "teacher"]


def test_pass_and_fail_are_actually_distinguished(tmp_path):
    smart, manifest, _ = runner(tmp_path / "a", "smart")
    dumb, manifest2, _ = runner(tmp_path / "b", "dumb")
    good = smart.run_case(case_of(manifest, "SYN-TRAIN"), "no_lessons")
    bad = dumb.run_case(case_of(manifest2, "SYN-TRAIN"), "no_lessons")
    assert good.status != bad.status
    assert (good.status, bad.status) == (ce.STATUS_UNASSISTED, ce.STATUS_TEACHER)


def test_hints_are_counted_and_turn_a_pass_into_a_coached_pass(tmp_path):
    r, manifest, _ = runner(tmp_path, "coachable")
    res = r.run_case(case_of(manifest, "SYN-TRAIN"), "no_lessons")
    assert res.status == ce.STATUS_COACHED
    assert res.hints_used == 1 and res.hint_levels == [1]
    assert res.attempts == 2
    assert res.attempt_log[1]["hint_level_before"] == 1


def test_holdout_gets_no_hint_and_no_teacher_patch(tmp_path):
    r, manifest, _ = runner(tmp_path, "dumb")
    res = r.run_case(case_of(manifest, "SYN-HOLD"), "no_lessons")
    assert res.status == ce.STATUS_FAIL
    assert res.hints_used == 0 and res.hint_levels == []
    assert res.teacher_patch_used is False
    assert not [a for a in res.attempt_log if a["by"] == "teacher"]


def test_holdout_can_still_be_solved_by_a_good_student(tmp_path):
    r, manifest, _ = runner(tmp_path, "smart")
    res = r.run_case(case_of(manifest, "SYN-HOLD"), "no_lessons")
    assert res.status == ce.STATUS_UNASSISTED and res.hints_used == 0


def test_holdout_verifier_output_is_redacted_everywhere_it_is_written(tmp_path):
    r, manifest, _ = runner(tmp_path, "dumb")
    res = r.run_case(case_of(manifest, "SYN-HOLD"), "no_lessons")
    blob = json.dumps({"r": res.__dict__ if hasattr(res, "__dict__") else str(res)}, default=str,
                      ensure_ascii=False)
    assert "REDACTED" in res.verifier["detail"]
    for secret in ("test_each_pair_on_its_own_line", "test_three_pairs_never_glue", "splitlines() =="):
        assert secret not in blob, f"holdout hidden test content leaked: {secret}"


def test_a_forbidden_lesson_in_the_store_aborts_the_holdout_case(tmp_path):
    """Second wall: even if somebody seeds the holdout's own lesson, the case refuses
    to run rather than quietly handing the student the answer."""
    book = LessonBook(tmp_path / "lessons")
    r, manifest, _ = runner(tmp_path / "x", "smart", book=book, seed_forbidden=True)
    seeded = ce.seed_lessons(book, manifest, project_id="synthetic-exam", run_id="t", forbidden=set())
    titles = {x["lesson_id"]: x["id"] for x in seeded["written"]}
    r.book, r.lesson_titles = book, titles
    assert "LSN-SYNTH-JOIN" in {x["id"] for x in seeded["written"]}
    with pytest.raises(HoldoutLeak):
        r.run_case(case_of(manifest, "SYN-HOLD"), "with_lessons")


def test_seeding_refuses_lessons_that_answer_a_holdout_case(tmp_path):
    book = LessonBook(tmp_path / "lessons")
    _, manifest, _ = runner(tmp_path / "x", "smart", seed_forbidden=True)
    seeded = ce.seed_lessons(book, manifest, project_id="synthetic-exam", run_id="t",
                             forbidden={"LSN-SYNTH-JOIN"})
    assert seeded["refused_holdout_linked"] == ["LSN-SYNTH-JOIN"]
    assert {x["id"] for x in seeded["written"]} == {"LSN-SYNTH-ADD"}
    stored = json.dumps(book.all_lessons(include_candidates=True), ensure_ascii=False)
    assert "LSN-SYNTH-JOIN" not in stored and "join a list with an explicit separator" not in stored


def test_lessons_are_retrieved_after_a_store_restart_and_are_cited(tmp_path):
    book = LessonBook(tmp_path / "lessons")
    _, manifest, seal = runner(tmp_path / "x", "coachable")
    seeded = ce.seed_lessons(book, manifest, project_id="synthetic-exam", run_id="t", forbidden=set())
    titles = {x["lesson_id"]: x["id"] for x in seeded["written"]}
    restarted = LessonBook(tmp_path / "lessons")        # the restart path: a new book, same directory
    backend = ce.MockExamBackend(seal, "coachable")
    r = ce.ExamRunner(manifest, seal, backend, run_id="t", project_id="synthetic-exam", book=restarted,
                      lesson_titles=titles)
    res = r.run_case(case_of(manifest, "SYN-TRAIN"), "with_lessons")
    assert res.lessons_injected == 1 and res.lessons_injected_ids == ["LSN-SYNTH-ADD"]
    assert res.memory_ids_cited == ["LSN-SYNTH-ADD"]
    assert res.memory_lookup_correct is True


def test_teacher_audit_rejects_editing_the_tests(tmp_path):
    r, manifest, _ = runner(tmp_path, "tamper")
    res = r.run_case(case_of(manifest, "SYN-TRAIN"), "no_lessons")
    codes = " ".join(n for a in res.attempt_log for n in a["review_notes"])
    assert "TEST_TAMPERING" in codes or "OUT_OF_SCOPE" in codes
    assert res.status == ce.STATUS_TEACHER            # never a student pass
    assert not any(a["verifier_passed"] for a in res.attempt_log if a["by"] == "student")


def test_teacher_audit_rejects_deleting_a_check(tmp_path):
    r, manifest, _ = runner(tmp_path, "weaken")
    res = r.run_case(case_of(manifest, "SYN-TRAIN"), "no_lessons")
    codes = " ".join(n for a in res.attempt_log for n in a["review_notes"])
    assert "WEAKENED_CHECK" in codes
    assert not any(a["verifier_passed"] for a in res.attempt_log if a["by"] == "student")


def test_a_regression_that_does_not_catch_the_bug_is_called_out(tmp_path):
    r, manifest, _ = runner(tmp_path, "blind")
    res = r.run_case(case_of(manifest, "SYN-TRAIN"), "no_lessons")
    codes = " ".join(n for a in res.attempt_log for n in a["review_notes"])
    assert "REGRESSION_BLIND" in codes
    assert res.attempt_log[0]["regression_catches_bug"] is False


def test_a_good_regression_is_recognised_as_catching_the_bug(tmp_path):
    r, manifest, _ = runner(tmp_path, "smart")
    res = r.run_case(case_of(manifest, "SYN-TRAIN"), "no_lessons")
    assert res.attempt_log[0]["regression_catches_bug"] is True


def test_sealed_material_is_hash_checked(tmp_path, capsys):
    mpath, seal_dir = build_exam(tmp_path)
    (seal_dir / "cases" / "SYN-TRAIN" / "hints.json").write_text('{"hints": []}', encoding="utf-8")
    code = ce.run(ce.parse_args(["--manifest", str(mpath), "--sealed", str(seal_dir),
                                 "--backend", "mock", "--out", str(tmp_path / "out")]))
    assert code == 4
    assert "SEAL MISMATCH" in capsys.readouterr().out


def test_full_mock_run_writes_a_report_with_both_profiles(tmp_path, capsys):
    mpath, seal_dir = build_exam(tmp_path)
    code = ce.run(ce.parse_args(["--manifest", str(mpath), "--sealed", str(seal_dir), "--backend", "mock",
                                 "--mock-brain", "dumb", "--project-id", "synthetic-exam",
                                 "--out", str(tmp_path / "out")]))
    assert code == 0
    report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    assert report["run_status"] == ce.RUN_MOCK
    assert report["weights_unchanged"] is True and report["fine_tuning"] == "none"
    assert {r["profile"] for r in report["results"]} == {"no_lessons", "with_lessons"}
    assert {r["case_id"] for r in report["results"]} == {"SYN-TRAIN", "SYN-HOLD"}
    assert report["isolation"]["holdout_lessons_written"] == 0
    metrics = report["metrics"]
    assert metrics["MOCK/no_lessons/train"]["teacher_patch_rate"] == 1.0
    assert metrics["MOCK/no_lessons/train"]["unassisted_solve_rate"] == 0.0
    assert metrics["MOCK/no_lessons/holdout"]["fail_rate"] == 1.0
    md = (tmp_path / "out" / "report.md").read_text(encoding="utf-8")
    assert "WEIGHTS_UNCHANGED" in md and "MOCK RUN" in md
    body = (tmp_path / "out" / "report.json").read_text(encoding="utf-8")
    for secret in ("test_each_pair_on_its_own_line", "test_add_is_symmetric"):
        assert secret not in body, "hidden verifier content must never reach the report"
    assert ce.WEIGHTS_LINE in capsys.readouterr().out


def test_unreachable_students_are_reported_as_not_measured_not_as_failure(tmp_path):
    """Hardware off is NOT_RUN, never a zero score and never an invented number."""
    mpath, seal_dir = build_exam(tmp_path)
    code = ce.run(ce.parse_args(["--manifest", str(mpath), "--sealed", str(seal_dir), "--backend", "local",
                                 "--students", "MAIN=http://127.0.0.1:9/v1", "FAST=http://127.0.0.1:10/v1",
                                 "--out", str(tmp_path / "out")]))
    assert code == 3
    report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    assert report["run_status"] == ce.RUN_NOT_MEASURED
    assert report["results"] == [] and report["metrics"] == {}
    assert set(report["not_run"]) == {"SYN-TRAIN", "SYN-HOLD"}
    assert [s["role"] for s in report["students"]] == ["MAIN", "FAST"]
    assert not any(s["reachable"] for s in report["students"])
    assert all(s["model"] == "" for s in report["students"]), "no model id may be invented for a dead endpoint"
