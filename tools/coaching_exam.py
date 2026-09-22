#!/usr/bin/env python3
"""Coaching EXAM: four real Bossman defects, replayed for the local student.

This is the repo-level companion of ``tools/coaching_runner.py`` (which runs
self-contained toy tasks). Nothing here re-implements the learning store, the
lesson schema, the poison filter or the backends: it reuses
``learning.lessons.LessonBook`` and ``tools.coaching_runner`` backends.

What is new here is the *exam*:

  * a frozen manifest of cases taken from defects the teacher closed on the
    owner machine (base SHA, task text, allowed tools, budgets, visible tests,
    hidden verifier, expected behavior, safety constraints, evidence manifest);
  * a **sealed** directory, outside every git worktree, holding the break/fix
    edits, the hint ladder and the hidden verifier tests. The repository the
    student can read never contains them — only their sha256;
  * the loop of the owner spec:
        student researches -> reproduces -> patch -> regression test ->
        runs visible tests -> teacher audit -> student fixes the review notes ->
        INDEPENDENT verifier (sealed hidden tests) decides;
  * graduated hints (broken invariant -> cause class -> verification strategy)
    and, only after the attempt budget is spent, a TEACHER PATCH;
  * statuses STUDENT_UNASSISTED_PASS / STUDENT_COACHED_PASS / TEACHER_PATCH /
    FAIL, never merged into one "success" number;
  * A/B: the same model, tools, budget and cases with and without the new
    lessons. Holdout cases get no hints, no teacher patch and never reach the
    learning store (``HoldoutLeak``).

No weight training happens anywhere: WEIGHTS_UNCHANGED is printed on every run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from learning.lessons import (CoachingEpisode, LessonBook, LessonPoisoned,  # noqa: E402
                              Provenance, format_for_prompt)
from tools.coaching_runner import (WEIGHTS_LINE, HoldoutLeak,  # noqa: E402
                                   OpenAICompatibleBackend)

SCHEMA = "bossman.coaching-exam/1"
PROFILES = ("no_lessons", "with_lessons")
STATUS_UNASSISTED = "STUDENT_UNASSISTED_PASS"
STATUS_COACHED = "STUDENT_COACHED_PASS"
STATUS_TEACHER = "TEACHER_PATCH"
STATUS_FAIL = "FAIL"
STATUS_NOT_RUN = "NOT_RUN"
RUN_MOCK = "MOCK"
RUN_MEASURED = "LOCAL_EXAM_MEASURED"
RUN_NOT_MEASURED = "LOCAL_EXAM_NOT_MEASURED"
DEFAULT_MANIFEST = REPO / "owner-repair" / "coaching-exam-20260922" / "manifest.json"
SEALED_ENV = "BOSSMAN_EXAM_SEALED"
REGRESSION_DIRNAME = "exam_regression"


# ------------------------------------------------------------------ errors
class ExamError(RuntimeError):
    pass


class SealError(ExamError):
    """The sealed material is missing or its hash does not match the manifest."""


# ------------------------------------------------------------------ manifest
@dataclass(slots=True)
class Case:
    case_id: str
    split: str                       # train | holdout
    title: str
    task_class: str
    base_sha: str
    workspace: dict
    task_text: str
    allowed_tools: dict
    budget: dict
    visible_tests: list[str]
    visible_tests_expected: str      # "red" | "green" on the broken state
    hidden_tests: dict               # {files: [...], node_ids_sealed: bool, count: int}
    expected_behavior: str
    safety_constraints: list[str]
    context_files: list[dict]
    editable_paths: list[str]
    evidence_manifest: dict
    lessons_relevant: list[str]
    lessons_forbidden: list[str]

    @property
    def is_holdout(self) -> bool:
        return self.split == "holdout"


def load_manifest(path: Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA:
        raise ExamError(f"{path}: schema {data.get('schema')!r} != {SCHEMA!r}")
    cases = [Case(**c) for c in data["cases"]]
    ids = [c.case_id for c in cases]
    if len(ids) != len(set(ids)):
        raise ExamError("duplicate case ids in the manifest")
    data["_cases"] = cases
    return data


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ------------------------------------------------------------------ the seal
class Seal:
    """Answers, break/fix edits, hint ladders and hidden tests. Lives OUTSIDE every
    git worktree; the repository holds only the sha256 of each file."""

    def __init__(self, root: Path, manifest: dict):
        self.root = Path(root)
        self.manifest = manifest
        if not self.root.is_dir():
            raise SealError(f"sealed directory not found: {self.root}")

    def path(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        if not str(p).startswith(str(self.root.resolve())):
            raise SealError(f"sealed path escapes the seal: {rel}")
        if not p.exists():
            raise SealError(f"sealed file missing: {rel}")
        return p

    def verify_hashes(self) -> list[str]:
        """Returns the list of mismatching sealed files ([] == the seal is intact)."""
        bad = []
        for rel, want in (self.manifest.get("sealed_sha256") or {}).items():
            try:
                got = sha256_file(self.path(rel))
            except SealError:
                bad.append(f"{rel}: MISSING")
                continue
            if got != want:
                bad.append(f"{rel}: {got[:12]} != {want[:12]}")
        return bad

    def edits(self, case_id: str, which: str) -> list[dict]:
        return json.loads(self.path(f"cases/{case_id}/{which}.json").read_text(encoding="utf-8"))["edits"]

    def hints(self, case_id: str) -> list[dict]:
        p = self.root / "cases" / case_id / "hints.json"
        return json.loads(p.read_text(encoding="utf-8"))["hints"] if p.is_file() else []

    def hidden_dir(self, case_id: str) -> Path:
        return self.path(f"cases/{case_id}/hidden")

    def fixture_dir(self, case_id: str) -> Path:
        return self.path(f"cases/{case_id}/fixture")

    def mock_script(self, brain: str, case_id: str) -> dict:
        p = self.root / "mock" / brain / f"{case_id}.json"
        if not p.is_file():
            return {}
        return json.loads(p.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ edits
def apply_edits(root: Path, edits: list[dict], *, label: str) -> None:
    """Exact-string edits. Anything that does not match is a hard failure — the exam
    never runs on a workspace it does not fully understand."""
    for e in edits:
        target = (root / e["file"]).resolve()
        if not str(target).startswith(str(Path(root).resolve())):
            raise ExamError(f"{label}: edit escapes the workspace: {e['file']}")
        text = target.read_text(encoding="utf-8")
        search, replace = e["search"], e["replace"]
        if text.count(search) != 1:
            raise ExamError(f"{label}: {e['file']}: search block found {text.count(search)}x, expected exactly 1")
        target.write_text(text.replace(search, replace, 1), encoding="utf-8")


def reverse(edits: list[dict]) -> list[dict]:
    return [{"file": e["file"], "search": e["replace"], "replace": e["search"]} for e in edits]


# ------------------------------------------------------------------ workspace
class Workspace:
    """Where a case is reproduced. ``repo`` = the lab git worktree (broken in place,
    restored in ``finally``); ``fixture`` = a temp copy of a sealed synthetic fixture."""

    def __init__(self, case: Case, seal: Seal, manifest: dict, *, repo_root: Path | None = None):
        self.case, self.seal, self.manifest = case, seal, manifest
        self.kind = case.workspace["kind"]
        self._tmp: tempfile.TemporaryDirectory | None = None
        self.root: Path
        if self.kind == "repo":
            base = repo_root or (REPO.parent / manifest["lab_worktree_name"])
            self.root = Path(base).resolve()
        elif self.kind == "fixture":
            self._tmp = tempfile.TemporaryDirectory(prefix=f"exam-{case.case_id}-")
            self.root = Path(self._tmp.name) / "work"
        else:
            raise ExamError(f"unknown workspace kind {self.kind!r}")

    @property
    def tests_cwd(self) -> Path:
        return self.root / self.case.workspace.get("tests_cwd", ".")

    def pythonpath(self) -> list[str]:
        return [str((self.root / p).resolve()) for p in self.case.workspace.get("pythonpath", ["."])]

    def __enter__(self) -> "Workspace":
        if self.kind == "fixture":
            shutil.copytree(self.seal.fixture_dir(self.case.case_id), self.root)
        else:
            if not (self.root / ".git").exists():
                raise ExamError(f"lab worktree not found or not a git worktree: {self.root}")
            dirty = subprocess.run(["git", "status", "--porcelain"], cwd=self.root, capture_output=True,
                                   text=True, timeout=60).stdout.strip()
            if dirty:
                raise ExamError(f"lab worktree {self.root} is dirty; refusing to break it:\n{dirty[:400]}")
            apply_edits(self.root, self.seal.edits(self.case.case_id, "break"), label=f"{self.case.case_id}/break")
        return self

    def __exit__(self, *exc) -> None:
        if self.kind == "fixture":
            if self._tmp:
                self._tmp.cleanup()
            return
        subprocess.run(["git", "checkout", "--", "."], cwd=self.root, capture_output=True, timeout=120)
        subprocess.run(["git", "clean", "-fdq", "--", "."], cwd=self.root, capture_output=True, timeout=120)

    # -------------------------------------------------- tests
    def run_tests(self, node_ids: list[str], *, timeout_s: float, extra_dir: Path | None = None) -> "TestRun":
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(self.pythonpath())
        env["PYTHONIOENCODING"] = "utf-8"
        # A patch and its reverse often have the SAME size and land in the same second, so a
        # cached .pyc would silently serve the old code back. No bytecode, no stale verdicts.
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        cmd = [self.manifest["python_interpreter"], "-m", "pytest", "-q", "-p", "no:cacheprovider", *node_ids]
        t0 = time.monotonic()
        try:
            proc = subprocess.run(cmd, cwd=str(extra_dir or self.tests_cwd), env=env, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=timeout_s)
        except subprocess.TimeoutExpired:
            return TestRun(False, f"pytest timed out after {timeout_s}s", True, round(time.monotonic() - t0, 2))
        except OSError as exc:
            return TestRun(False, f"could not start pytest: {exc}", True, round(time.monotonic() - t0, 2))
        out = (proc.stdout or "") + (proc.stderr or "")
        tail = "\n".join(out.strip().splitlines()[-25:])
        tool_error = proc.returncode not in (0, 1)
        return TestRun(proc.returncode == 0, tail, tool_error, round(time.monotonic() - t0, 2))

    def install_hidden(self, tmp_holder: list[Path]) -> list[str]:
        """Copies the sealed hidden tests in, returns their node ids. They are removed again
        by ``remove_hidden`` — the student never sees them on disk between attempts."""
        node_ids = []
        for spec in self.case.hidden_tests["files"]:
            src = self.seal.hidden_dir(self.case.case_id) / spec["name"]
            dst = (self.root / spec["install_at"]).resolve()
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            tmp_holder.append(dst)
            rel = os.path.relpath(dst, self.tests_cwd).replace(os.sep, "/")
            node_ids.append(rel)
        return node_ids

    @staticmethod
    def remove_hidden(tmp_holder: list[Path]) -> None:
        for p in tmp_holder:
            with_suppress_unlink(p)
        tmp_holder.clear()


def with_suppress_unlink(p: Path) -> None:
    try:
        p.unlink()
    except OSError:
        pass


@dataclass(slots=True)
class TestRun:
    passed: bool
    tail: str
    tool_error: bool
    wall_s: float


# ------------------------------------------------------------------ student protocol
_EDIT_BLOCK = re.compile(
    r"FILE:\s*(?P<file>[^\n]+)\n<{5,}\s*SEARCH\s*\n(?P<search>.*?)\n={5,}\s*\n(?P<replace>.*?)\n>{5,}\s*REPLACE",
    re.S)
_REGRESSION = re.compile(r"REGRESSION:\s*(?P<path>[^\n]+)\n```(?:python)?\s*\n(?P<code>.*?)```", re.S)
_MEMORY = re.compile(r"(?im)^\s*MEMORY:\s*(?P<ids>[^\n]*)$")
_CAUSE = re.compile(r"(?im)^\s*CAUSE:\s*(?P<text>[^\n]*)$")
_LESSON_REF = re.compile(r"\b(\d{1,2})\b")

def with_markers(text: str) -> str:
    """Expand @@SEARCH@@/@@DIVIDER@@/@@REPLACE@@ into the 7-char edit-block markers.

    The literal markers at the start of a source line are indistinguishable from
    a leftover merge conflict to `git diff --check` (root-ci hygiene failed on
    exactly these lines), so the protocol text is written with tokens and the
    real markers are produced here. The parser (_EDIT_BLOCK) is unchanged."""
    return (text.replace("@@SEARCH@@", "<" * 7 + " SEARCH")
                .replace("@@DIVIDER@@", "=" * 7)
                .replace("@@REPLACE@@", ">" * 7 + " REPLACE"))


PROTOCOL = with_markers("""Reply in EXACTLY this format and nothing else:

MEMORY: <the numbers of the memory lessons above that you actually used, or NONE>
CAUSE: <one line: the invariant that is broken>
FILE: <path relative to the workspace root>
@@SEARCH@@
<the exact lines to replace, copied character for character from the file>
@@DIVIDER@@
<the new lines>
@@REPLACE@@
(repeat the FILE/SEARCH/REPLACE block for every file you change)
REGRESSION: <path for a new pytest file that fails before your fix and passes after>
```python
<the pytest file content>
```
""")


@dataclass(slots=True)
class StudentReply:
    raw: str
    edits: list[dict]
    regression_path: str
    regression_code: str
    memory_ids: list[int]
    cause: str
    parse_error: str = ""


def parse_reply(text: str) -> StudentReply:
    edits = [{"file": m.group("file").strip().strip("`"), "search": m.group("search"), "replace": m.group("replace")}
             for m in _EDIT_BLOCK.finditer(text)]
    rm = _REGRESSION.search(text)
    mm = _MEMORY.search(text)
    ids = [int(i) for i in _LESSON_REF.findall(mm.group("ids"))] if mm else []
    cm = _CAUSE.search(text)
    err = "" if edits else "no FILE/SEARCH/REPLACE block found in the reply"
    return StudentReply(raw=text, edits=edits, regression_path=(rm.group("path").strip() if rm else ""),
                        regression_code=(rm.group("code") if rm else ""), memory_ids=ids,
                        cause=(cm.group("text").strip() if cm else ""), parse_error=err)


# ------------------------------------------------------------------ backends
class ExamBackend:
    name = "abstract"
    label = ""
    model = ""

    def reachable(self) -> tuple[bool, str]:
        raise NotImplementedError

    def ask(self, case: Case, prompt: str, attempt: int) -> str:
        raise NotImplementedError


class LocalExamBackend(ExamBackend):
    """Real local student. The model id is DISCOVERED from /v1/models, never taken
    from documentation."""
    name = "local"

    def __init__(self, base_url: str, *, model: str | None = None, timeout_s: float = 600.0,
                 temperature: float = 0.0, seed: int = 7, max_tokens: int = 2048):
        self.base = base_url.rstrip("/")
        if self.base.endswith("/v1"):
            self.base = self.base[: -len("/v1")]
        self.model = model or ""
        self.timeout_s, self.temperature, self.seed, self.max_tokens = timeout_s, temperature, seed, max_tokens
        self.prompt_chars = 0
        self.reply_chars = 0
        self.calls = 0
        self.label = f"local OpenAI-compatible {self.base}"

    def discover(self) -> list[str]:
        req = urllib.request.Request(self.base + "/v1/models")
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
        return [str(m.get("id")) for m in (data.get("data") or []) if m.get("id")]

    def reachable(self) -> tuple[bool, str]:
        try:
            ids = self.discover()
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            return False, f"{self.base} unreachable: {exc}"
        if not ids:
            return False, f"{self.base}/v1/models returned no model ids"
        if not self.model or self.model not in ids:
            self.model = ids[0]
        self.label = f"local OpenAI-compatible {self.base} model={self.model} (discovered: {', '.join(ids)})"
        return True, f"discovered {len(ids)} model id(s): {', '.join(ids)}"

    def ask(self, case: Case, prompt: str, attempt: int) -> str:
        body = json.dumps({"model": self.model,
                           "messages": [{"role": "system", "content":
                                         "You are a careful Python engineer fixing a real defect in an existing "
                                         "repository. Follow the reply protocol exactly."},
                                        {"role": "user", "content": prompt}],
                           "temperature": self.temperature, "seed": self.seed,
                           "max_tokens": self.max_tokens}).encode("utf-8")
        req = urllib.request.Request(self.base + "/v1/chat/completions", data=body,
                                     headers={"Content-Type": "application/json"})
        self.calls += 1
        self.prompt_chars += len(prompt)
        with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
            data = json.loads(r.read().decode("utf-8"))
        text = str(data["choices"][0]["message"]["content"])
        self.reply_chars += len(text)
        return text


class MockExamBackend(ExamBackend):
    """Deterministic scripted student read from the SEAL (never from the repository).

    ``brain`` picks the personality: ``smart`` solves unassisted, ``dumb`` needs the
    hint ladder or fails outright. Every number it produces is labelled MOCK."""
    name = "mock"

    def __init__(self, seal: Seal, brain: str):
        self.seal, self.brain = seal, brain
        self.model = f"mock:{brain}"
        self.label = f"MOCK scripted student ({brain}) — not a model, not a measurement"
        self.calls = 0
        self.prompt_chars = 0
        self.reply_chars = 0

    def reachable(self) -> tuple[bool, str]:
        return True, "mock backend needs no endpoint"

    def ask(self, case: Case, prompt: str, attempt: int) -> str:
        script = self.seal.mock_script(self.brain, case.case_id)
        self.calls += 1
        self.prompt_chars += len(prompt)
        replies = script.get("replies") or []
        trigger = script.get("lesson_trigger")
        if trigger and trigger in prompt and script.get("on_lesson"):
            text = str(script["on_lesson"])
        elif replies:
            text = str(replies[min(attempt - 1, len(replies) - 1)])
        else:
            text = "MEMORY: NONE\nCAUSE: no idea\n"
        self.reply_chars += len(text)
        return text


# ------------------------------------------------------------------ teacher
@dataclass(slots=True)
class ReviewNote:
    code: str
    text: str


def teacher_audit(case: Case, reply: StudentReply, ws: Workspace, *, visible: TestRun,
                  regression: TestRun | None, regression_caught_bug: bool | None) -> list[ReviewNote]:
    """Deterministic teacher review of the student's work. It never reveals the answer:
    it only states which of the case's own rules the submission breaks."""
    notes: list[ReviewNote] = []
    if reply.parse_error:
        notes.append(ReviewNote("PROTOCOL", reply.parse_error + ". " + PROTOCOL))
        return notes
    allowed = tuple(case.editable_paths)
    for e in reply.edits:
        norm = e["file"].replace("\\", "/").lstrip("./")
        if not norm.startswith(allowed):
            notes.append(ReviewNote("OUT_OF_SCOPE",
                                    f"{norm} is outside the files this case allows you to edit ({', '.join(allowed)})."))
        if re.search(r"(^|/)tests?/", norm) and norm != case_regression_path(case, reply):
            notes.append(ReviewNote("TEST_TAMPERING",
                                    f"{norm} is a test file. Fix the product code; do not edit the tests."))
        if _weakens(e):
            notes.append(ReviewNote("WEAKENED_CHECK",
                                    f"{norm}: the edit deletes an assertion/verification instead of fixing the cause."))
    if not visible.passed:
        notes.append(ReviewNote("VISIBLE_RED", "The visible tests are still red:\n" + visible.tail))
    if not reply.regression_code.strip():
        notes.append(ReviewNote("NO_REGRESSION", "Add a regression test that fails on the broken code and passes "
                                                 "on the fixed code."))
    elif regression is not None and not regression.passed:
        notes.append(ReviewNote("REGRESSION_RED", "Your own regression test does not pass on your patch:\n"
                                + regression.tail))
    elif regression_caught_bug is False:
        notes.append(ReviewNote("REGRESSION_BLIND", "Your regression test passes on the ORIGINAL broken code too, "
                                                    "so it does not test the defect."))
    return notes


_WEAKEN = re.compile(r"(?im)^\s*(assert\b|if not |raise CompanionError|sha256|ftyp|verify)")


def _weakens(edit: dict) -> bool:
    before = len(_WEAKEN.findall(edit["search"]))
    after = len(_WEAKEN.findall(edit["replace"]))
    return after < before


def case_regression_path(case: Case, reply: StudentReply) -> str:
    return (reply.regression_path or "").replace("\\", "/").lstrip("./")


# ------------------------------------------------------------------ lessons
def seed_lessons(book: LessonBook, manifest: dict, *, project_id: str, run_id: str,
                 forbidden: set[str]) -> dict:
    """Puts the teacher's candidate lessons into the canonical store and verifies them.

    Lessons attached to a HOLDOUT case are refused here — that is the second wall of the
    holdout isolation (the first is that their text is not in the source file at all)."""
    raw = Path(manifest["lessons_source"])
    src = raw if raw.is_absolute() else REPO / raw
    payload = json.loads(src.read_text(encoding="utf-8"))
    written, rejected, refused = [], [], []
    for lesson in payload["lessons"]:
        lid = lesson["id"]
        if lid in forbidden:
            refused.append(lid)
            continue
        if lid not in set(manifest["lessons_seeded"]):
            continue
        ep = CoachingEpisode(
            attempt_id=f"{run_id}:seed:{lid}", task_id=f"exam-seed:{lid}", project_id=project_id,
            failure_observation=lesson["symptoms"][:900], correction=lesson["recipe"][:1900],
            source="teacher", kind="teacher_patch", task_class=manifest["lesson_task_class"][lid],
            environment=lesson.get("model_runtime") or "owner-machine", model="teacher:claude-opus-5",
            title=lid,
            provenance=Provenance(who="teacher:claude-opus-5", what=f"closed defect {lid}", run_id=run_id,
                                  evidence_refs=[manifest["lessons_source"], lesson.get("check", "")[:120]],
                                  model="teacher:claude-opus-5"))
        try:
            book.save(ep)
        except LessonPoisoned as exc:
            rejected.append({"id": lid, "reasons": list(exc.reasons)})
            continue
        book.verify(ep.lesson_id,
                    verifier={"principal_id": "tool:owner-machine-repair", "independence_class": "external_tool",
                              "model_id": "", "run_id": f"{run_id}:seed"},
                    evidence={"source": manifest["lessons_source"], "expected": lesson.get("check", "verified fix"),
                              "actual": "defect closed and re-tested on the owner machine",
                              "head_sha": manifest["base_sha"], "environment": "owner-machine"},
                    statement=f"{lid}: the recipe is the verified correction of a reproduced failure")
        written.append({"id": lid, "lesson_id": ep.lesson_id})
    return {"written": written, "rejected": rejected, "refused_holdout_linked": refused}


# ------------------------------------------------------------------ one case
@dataclass(slots=True)
class AttemptLog:
    n: int
    by: str                       # student | teacher
    parse_ok: bool
    visible_passed: bool
    regression_present: bool
    regression_catches_bug: bool | None
    review_notes: list[str]
    verifier_passed: bool
    hint_level_before: int
    memory_ids: list[str]
    failure_signature: str
    wall_s: float
    tool_error: bool


@dataclass(slots=True)
class CaseResult:
    case_id: str
    split: str
    profile: str
    status: str
    attempts: int
    hints_used: int
    hint_levels: list[int]
    teacher_patch_used: bool
    reproduced: bool
    lessons_injected: int
    lessons_injected_ids: list[str]
    memory_ids_cited: list[str]
    memory_lookup_correct: bool | None
    repeated_failures: int
    tool_errors: int
    wall_s: float
    verifier: dict
    attempt_log: list[dict] = field(default_factory=list)
    note: str = ""
    student: str = ""
    model: str = ""


def _signature(text: str) -> str:
    return hashlib.sha256(re.sub(r"\d+", "#", text or "").encode("utf-8")).hexdigest()[:12]


def build_prompt(case: Case, ws: Workspace, *, lessons_text: str, hints: list[dict],
                 review: list[ReviewNote], visible: TestRun | None) -> str:
    parts = [f"# Case {case.case_id} — {case.title}", "", case.task_text, "",
             f"Expected observable behavior after your fix:\n{case.expected_behavior}", "",
             "Safety constraints (breaking one of these is an automatic FAIL):"]
    parts += [f"  - {s}" for s in case.safety_constraints]
    parts += ["", f"Allowed tools: {json.dumps(case.allowed_tools, ensure_ascii=False)}",
              f"You may edit only: {', '.join(case.editable_paths)}",
              f"Attempt budget: {case.budget['max_attempts']} attempts, "
              f"{case.budget['wall_clock_s']}s wall clock.", ""]
    if lessons_text:
        parts += ["## Lessons retrieved from Bossman memory", lessons_text, ""]
    parts += ["## Source under repair"]
    for spec in case.context_files:
        path = ws.root / spec["path"]
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        a, b = int(spec.get("start", 1)), int(spec.get("end", len(lines)))
        body = "\n".join(f"{i}: {lines[i - 1]}" for i in range(max(1, a), min(len(lines), b) + 1))
        parts += [f"### {spec['path']} (lines {a}-{b})", "```python", body, "```", ""]
    if visible is not None and not visible.passed:
        parts += ["## Visible tests are RED right now", "```", visible.tail, "```", ""]
    elif visible is not None:
        parts += ["## Visible tests are GREEN right now — the defect is invisible to them. "
                  "You must still fix it and prove it with a new regression test.", ""]
    if hints:
        parts += ["## Teacher hints (use them, they are cumulative)"]
        parts += [f"  {h['level']}. [{h['kind']}] {h['text']}" for h in hints]
        parts.append("")
    if review:
        parts += ["## Teacher review of your previous attempt — fix every point"]
        parts += [f"  - [{n.code}] {n.text}" for n in review]
        parts.append("")
    parts += ["## Reply protocol", PROTOCOL]
    return "\n".join(parts)


class ExamRunner:
    def __init__(self, manifest: dict, seal: Seal, backend: ExamBackend, *, run_id: str, project_id: str,
                 book: LessonBook | None, lab_root: Path | None = None,
                 lesson_titles: dict[str, str] | None = None, student_role: str = "STUDENT"):
        self.manifest, self.seal, self.backend = manifest, seal, backend
        self.run_id, self.project_id, self.book = run_id, project_id, book
        self.lab_root = lab_root
        self.lesson_titles = dict(lesson_titles or {})     # store lesson_id -> LSN-... id
        self.student_role = student_role

    # ---------------------------------------------------------- guards
    def guard_holdout(self, case: Case) -> None:
        if case.is_holdout and self.book is not None:
            for lid in case.lessons_forbidden:
                for rec in self.book.all_lessons(include_candidates=True):
                    if lid and lid in json.dumps(rec, ensure_ascii=False):
                        raise HoldoutLeak(f"{case.case_id}: forbidden lesson {lid} is present in the store")

    # ---------------------------------------------------------- one case
    def run_case(self, case: Case, profile: str) -> CaseResult:
        t0 = time.monotonic()
        self.guard_holdout(case)
        lessons: list[dict] = []
        if profile == "with_lessons" and self.book is not None:
            # holdout asks for TRANSFER, so the whole verified corpus is offered there; a train
            # case gets its own class. Forbidden (holdout-answering) lessons are dropped either way.
            lessons = self.book.retrieve(project_id=self.project_id,
                                         task_class=None if case.is_holdout else case.task_class, limit=5)
            forbidden = set(case.lessons_forbidden)
            lessons = [l for l in lessons
                       if self.lesson_titles.get(str(l.get("lesson_id")), "") not in forbidden]
        lessons_text = format_for_prompt(lessons) if lessons else ""
        injected_ids = [self.lesson_titles.get(str(l.get("lesson_id")), str(l.get("lesson_id")))
                        for l in lessons]
        hints_available = [] if case.is_holdout else self.seal.hints(case.case_id)
        max_hints = 0 if case.is_holdout else int(case.budget.get("max_hints", 0))
        max_attempts = int(case.budget["max_attempts"])
        deadline = t0 + float(case.budget["wall_clock_s"])

        log: list[AttemptLog] = []
        hints_used = 0
        signatures: list[str] = []
        repeated = tool_errors = 0
        status = STATUS_FAIL
        verifier_info: dict = {"ran": False}
        memory_cited: list[str] = []
        note = ""

        with Workspace(case, self.seal, self.manifest, repo_root=self.lab_root) as ws:
            broken_visible = ws.run_tests(case.visible_tests, timeout_s=case.budget["test_timeout_s"])
            reproduced = (broken_visible.passed is False) if case.visible_tests_expected == "red" else True
            if case.visible_tests_expected == "red" and broken_visible.passed:
                note = "case did not reproduce: visible tests are green on the broken workspace"
            baseline_state = _snapshot(ws.root, case)
            created: list[Path] = []
            review: list[ReviewNote] = []
            for n in range(1, max_attempts + 1):
                if time.monotonic() > deadline:
                    note = note or "wall-clock budget exhausted"
                    break
                _restore(ws.root, baseline_state, created)
                prompt = build_prompt(case, ws, lessons_text=lessons_text, hints=hints_available[:hints_used],
                                      review=review, visible=broken_visible)
                a_t0 = time.monotonic()
                try:
                    raw = self.backend.ask(case, prompt, n)
                except Exception as exc:  # noqa: BLE001 — endpoint failures are tool errors, not student failures
                    tool_errors += 1
                    log.append(AttemptLog(n, "student", False, False, False, None,
                                          [f"BACKEND_ERROR: {type(exc).__name__}: {str(exc)[:160]}"], False,
                                          hints_used, [], _signature(str(exc)), round(time.monotonic() - a_t0, 2),
                                          True))
                    review = [ReviewNote("BACKEND", "the previous call to the student failed; retrying")]
                    continue
                reply = parse_reply(raw)
                cited_now = [injected_ids[i - 1] for i in reply.memory_ids if 1 <= i <= len(injected_ids)]
                memory_cited += [m for m in cited_now if m not in memory_cited]
                applied = True
                # Any file the student touches is snapshotted before the edit, so an
                # out-of-scope or tampering edit cannot survive into the next attempt.
                for e in reply.edits:
                    rel = str(e.get("file", "")).replace("\\", "/").lstrip("./")
                    target = (ws.root / rel)
                    if rel and rel not in baseline_state and target.is_file() \
                            and str(target.resolve()).startswith(str(ws.root.resolve())):
                        baseline_state[rel] = target.read_text(encoding="utf-8")
                try:
                    if reply.edits:
                        apply_edits(ws.root, reply.edits, label=f"{case.case_id}/student")
                except ExamError as exc:
                    applied = False
                    review = [ReviewNote("PATCH_DID_NOT_APPLY", str(exc))]
                reg_run: TestRun | None = None
                reg_catches: bool | None = None
                reg_rel = case_regression_path(case, reply)
                if applied and reply.regression_code.strip() and reg_rel:
                    reg_path = _install_regression(ws, reg_rel, reply.regression_code)
                    if reg_path is None:
                        review = [ReviewNote("REGRESSION_PATH",
                                             f"{reg_rel} is not an allowed place for a new test file; use "
                                             f"{case.workspace.get('regression_hint', REGRESSION_DIRNAME)}/")]
                    else:
                        created.append(reg_path)
                        node = os.path.relpath(reg_path, ws.tests_cwd).replace(os.sep, "/")
                        reg_run = ws.run_tests([node], timeout_s=case.budget["test_timeout_s"])
                        reg_catches = _regression_catches_bug(ws, case, reply, node, reg_path)
                visible = ws.run_tests(case.visible_tests, timeout_s=case.budget["test_timeout_s"]) if applied \
                    else TestRun(False, "patch did not apply", False, 0.0)
                tool_errors += int(visible.tool_error)
                notes = review if not applied else teacher_audit(case, reply, ws, visible=visible,
                                                                 regression=reg_run,
                                                                 regression_caught_bug=reg_catches)
                hard = [x for x in notes if x.code in
                        ("OUT_OF_SCOPE", "TEST_TAMPERING", "WEAKENED_CHECK", "PATCH_DID_NOT_APPLY", "PROTOCOL")]
                ver = TestRun(False, "not run", False, 0.0)
                if applied and not hard:
                    ver = self._verify(ws, case)
                    verifier_info = {"ran": True, "passed": ver.passed, "wall_s": ver.wall_s,
                                     "detail": _redact(case, ver.tail)}
                sig = _signature("|".join(x.code for x in notes) + visible.tail[-400:])
                repeated += int(sig in signatures)
                signatures.append(sig)
                log.append(AttemptLog(n, "student", not reply.parse_error, visible.passed,
                                      bool(reply.regression_code.strip()), reg_catches,
                                      [f"{x.code}: {x.text[:200]}" for x in notes], ver.passed, hints_used,
                                      cited_now, sig, round(time.monotonic() - a_t0, 2), visible.tool_error))
                if ver.passed and not notes:
                    status = STATUS_UNASSISTED if hints_used == 0 else STATUS_COACHED
                    break
                review = notes
                if hints_used < min(max_hints, len(hints_available)) and n < max_attempts:
                    hints_used += 1
            else:
                note = note or "attempt budget exhausted"

            if status == STATUS_FAIL and not case.is_holdout and case.budget.get("teacher_patch_allowed", True):
                _restore(ws.root, baseline_state, created)
                apply_edits(ws.root, self.seal.edits(case.case_id, "fix"), label=f"{case.case_id}/teacher-fix")
                ver = self._verify(ws, case)
                log.append(AttemptLog(len(log) + 1, "teacher", True, True, True, True,
                                      ["TEACHER_PATCH applied after the student budget was spent"], ver.passed,
                                      hints_used, [], "teacher", ver.wall_s, False))
                verifier_info = {"ran": True, "passed": ver.passed, "wall_s": ver.wall_s,
                                 "detail": _redact(case, ver.tail), "by": "teacher"}
                status = STATUS_TEACHER if ver.passed else STATUS_FAIL
                note = note or "student did not solve it inside the budget"

        relevant = set(case.lessons_relevant)
        mem_correct: bool | None = None
        if profile == "with_lessons" and relevant:
            cited = {m for m in memory_cited if m != "NONE"}
            mem_correct = bool(cited & relevant) and not (cited & set(case.lessons_forbidden))
        return CaseResult(
            case_id=case.case_id, split=case.split, profile=profile, status=status,
            attempts=sum(1 for a in log if a.by == "student"), hints_used=hints_used,
            hint_levels=[h["level"] for h in hints_available[:hints_used]],
            teacher_patch_used=any(a.by == "teacher" for a in log), reproduced=reproduced,
            lessons_injected=len(lessons), lessons_injected_ids=injected_ids, memory_ids_cited=memory_cited,
            memory_lookup_correct=mem_correct, repeated_failures=repeated, tool_errors=tool_errors,
            wall_s=round(time.monotonic() - t0, 2), verifier=verifier_info,
            attempt_log=[asdict(a) for a in log], note=note, student=self.student_role,
            model=getattr(self.backend, "model", "") or self.backend.name)

    def _verify(self, ws: Workspace, case: Case) -> TestRun:
        """INDEPENDENT verifier: the sealed hidden tests, installed only for the run and
        removed immediately after. Their content never reaches the student or the report."""
        holder: list[Path] = []
        try:
            nodes = ws.install_hidden(holder)
            return ws.run_tests(nodes, timeout_s=case.budget["test_timeout_s"])
        finally:
            Workspace.remove_hidden(holder)


def _redact(case: Case, text: str) -> str:
    if not case.is_holdout:
        return text[-1200:]
    passed = len(re.findall(r"\bpassed\b", text))
    return f"[REDACTED holdout verifier output; {'green' if 'failed' not in text else 'red'}, markers={passed}]"


def _snapshot(root: Path, case: Case) -> dict[str, str]:
    out = {}
    for rel in case.workspace.get("snapshot_files", []):
        p = root / rel
        if p.is_file():
            out[rel] = p.read_text(encoding="utf-8")
    return out


def _restore(root: Path, snap: dict[str, str], created: list[Path] | None = None) -> None:
    for rel, text in snap.items():
        (root / rel).write_text(text, encoding="utf-8")
    for p in created or []:
        with_suppress_unlink(p)
    if created is not None:
        created.clear()


def _install_regression(ws: Workspace, rel: str, code: str) -> Path | None:
    """The student's own regression test lands next to the suite it belongs to, under a
    reserved ``test_exam_reg_`` name, so it sees the same conftest and helpers."""
    name = Path(rel).name
    if not re.fullmatch(r"test_[A-Za-z0-9_]{1,60}\.py", name):
        return None
    safe = "test_exam_reg_" + name[len("test_"):]
    target = (ws.tests_cwd / ws.case.workspace.get("regression_dir", REGRESSION_DIRNAME) / safe).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(code, encoding="utf-8")
    return target


def _regression_catches_bug(ws: Workspace, case: Case, reply: StudentReply, node: str, reg_path: Path) -> bool | None:
    """Re-breaks the product code (keeping the student's regression test) and checks that the
    test goes red. A regression that stays green on the broken code proves nothing."""
    saved = {e["file"]: (ws.root / e["file"]).read_text(encoding="utf-8") for e in reply.edits}
    try:
        for e in reply.edits:
            p = ws.root / e["file"]
            text = p.read_text(encoding="utf-8")
            if text.count(e["replace"]) != 1:
                return None
            p.write_text(text.replace(e["replace"], e["search"], 1), encoding="utf-8")
        run = ws.run_tests([node], timeout_s=case.budget["test_timeout_s"])
        return not run.passed
    finally:
        for rel, text in saved.items():
            (ws.root / rel).write_text(text, encoding="utf-8")


# ------------------------------------------------------------------ self-check
def self_check(manifest: dict, seal: Seal, *, lab_root: Path | None = None) -> dict:
    """Proves the exam is well-formed WITHOUT any model: on the broken workspace the
    hidden verifier must be RED, and after the sealed fix it must be GREEN."""
    rows = []
    for case in manifest["_cases"]:
        row = {"case_id": case.case_id, "split": case.split}
        with Workspace(case, seal, manifest, repo_root=lab_root) as ws:
            broken_visible = ws.run_tests(case.visible_tests, timeout_s=case.budget["test_timeout_s"])
            holder: list[Path] = []
            try:
                nodes = ws.install_hidden(holder)
                broken_hidden = ws.run_tests(nodes, timeout_s=case.budget["test_timeout_s"])
            finally:
                Workspace.remove_hidden(holder)
            apply_edits(ws.root, seal.edits(case.case_id, "fix"), label=f"{case.case_id}/fix")
            fixed_visible = ws.run_tests(case.visible_tests, timeout_s=case.budget["test_timeout_s"])
            holder = []
            try:
                nodes = ws.install_hidden(holder)
                fixed_hidden = ws.run_tests(nodes, timeout_s=case.budget["test_timeout_s"])
            finally:
                Workspace.remove_hidden(holder)
        row.update({
            "visible_on_broken": "green" if broken_visible.passed else "red",
            "visible_expected_on_broken": case.visible_tests_expected,
            "hidden_on_broken": "green" if broken_hidden.passed else "red",
            "visible_on_fixed": "green" if fixed_visible.passed else "red",
            "hidden_on_fixed": "green" if fixed_hidden.passed else "red",
        })
        row["ok"] = (row["hidden_on_broken"] == "red" and row["hidden_on_fixed"] == "green"
                     and row["visible_on_fixed"] == "green"
                     and row["visible_on_broken"] == case.visible_tests_expected)
        if not row["ok"]:
            row["broken_visible_tail"] = broken_visible.tail[-600:]
            row["fixed_hidden_tail"] = fixed_hidden.tail[-600:]
        rows.append(row)
    return {"cases": rows, "ok": all(r["ok"] for r in rows)}


# ------------------------------------------------------------------ report
def summarize(results: list[CaseResult]) -> dict:
    out: dict[str, Any] = {}
    students = sorted({r.student for r in results}) or [""]
    for student in students:
        for profile in PROFILES:
            for split in ("train", "holdout", "all"):
                rows = [r for r in results if r.student == student and r.profile == profile
                        and (split == "all" or r.split == split)]
                if not rows:
                    continue
                out[f"{student}/{profile}/{split}"] = _rates(rows)
    return out


def _rates(rows: list[CaseResult]) -> dict:
    """Unassisted, coached and teacher-patch are reported separately, never merged."""
    n = len(rows)
    mem = [r.memory_lookup_correct for r in rows if r.memory_lookup_correct is not None]
    return {
        "cases": n,
        "unassisted_solve_rate": round(sum(r.status == STATUS_UNASSISTED for r in rows) / n, 4),
        "coached_solve_rate": round(sum(r.status == STATUS_COACHED for r in rows) / n, 4),
        "solve_rate_any_student": round(sum(r.status in (STATUS_UNASSISTED, STATUS_COACHED) for r in rows) / n, 4),
        "teacher_patch_rate": round(sum(r.status == STATUS_TEACHER for r in rows) / n, 4),
        "fail_rate": round(sum(r.status == STATUS_FAIL for r in rows) / n, 4),
        "attempts": sum(r.attempts for r in rows),
        "hints_used": sum(r.hints_used for r in rows),
        "repeated_failures": sum(r.repeated_failures for r in rows),
        "tool_errors": sum(r.tool_errors for r in rows),
        "memory_lookup_correct_rate": (round(sum(bool(x) for x in mem) / len(mem), 4) if mem else None),
        "wall_s": round(sum(r.wall_s for r in rows), 2),
    }


def render_markdown(report: dict) -> str:
    m = report["metrics"]
    lines = [f"# Coaching exam {report['exam_id']} — run {report['run_id']}", "",
             f"**Run status:** `{report['run_status']}`  ",
             f"**Student backend:** {report['backend']['label']}  ",
             f"**Discovered model:** `{report['backend'].get('model') or 'n/a'}`  ",
             f"**Base SHA:** `{report['base_sha']}`  ",
             f"**{WEIGHTS_LINE}**", ""]
    if report["run_status"] == RUN_MOCK:
        lines += ["> MOCK RUN — every number comes from a scripted student. It proves the harness, "
                  "not the ability of any model.", ""]
    if report["run_status"] == RUN_NOT_MEASURED:
        lines += [f"> NOT RUN: {report.get('reason', '')}", ""]
    lines += ["## Students", "",
              "| role | endpoint | reachable | discovered model |", "|---|---|---|---|"]
    for s in report.get("students", []):
        lines.append(f"| {s['role']} | `{s['endpoint']}` | {'yes' if s['reachable'] else '**NOT_RUN**'} | "
                     f"`{s['model'] or '-'}` |")
    lines += ["", "## Per case", "",
              "| student | case | split | profile | status | attempts | hints | teacher patch | memory hit | "
              "repeated | s |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in report["results"]:
        lines.append(f"| {r.get('student', '')} | {r['case_id']} | {r['split']} | {r['profile']} | "
                     f"**{r['status']}** | {r['attempts']} | "
                     f"{r['hints_used']} | {'yes' if r['teacher_patch_used'] else 'no'} | "
                     f"{r['memory_lookup_correct']} | {r['repeated_failures']} | {r['wall_s']} |")
    lines += ["", "## Rates (unassisted / coached / teacher patch are never merged)", "",
              "| student/profile/split | cases | unassisted | coached | teacher patch | fail | hints | "
              "memory ok | s |",
              "|---|---|---|---|---|---|---|---|---|"]
    for key, v in m.items():
        lines.append(f"| {key} | {v['cases']} | {v['unassisted_solve_rate']} | {v['coached_solve_rate']} | "
                     f"{v['teacher_patch_rate']} | {v['fail_rate']} | {v['hints_used']} | "
                     f"{v['memory_lookup_correct_rate']} | {v['wall_s']} |")
    iso = report["isolation"]
    lines += ["", "## Holdout isolation", "",
              f"- sealed directory: `{iso['sealed_dir']}` (outside every git worktree)",
              f"- sealed files hash-checked: {iso['sealed_files_checked']}, mismatches: {iso['sealed_mismatches']}",
              f"- holdout case ids: {', '.join(iso['holdout_case_ids'])}",
              f"- lessons refused because they belong to a holdout case: "
              f"{', '.join(iso['lessons_refused']) or 'none'}",
              f"- holdout verifier output in this report: REDACTED to a verdict",
              f"- lessons written from holdout cases: {iso['holdout_lessons_written']} (must be 0)", "",
              f"## Lessons", "",
              f"- seeded and verified: {len(report['lessons']['written'])} "
              f"({', '.join(x['id'] for x in report['lessons']['written']) or 'none'})",
              f"- rejected by the poison filter: {len(report['lessons']['rejected'])}", "",
              f"`{WEIGHTS_LINE}`", ""]
    return "\n".join(lines)


# ------------------------------------------------------------------ main
def discover_students(args, seal: Seal) -> list[dict]:
    """One entry per student role. The model id is always DISCOVERED from /v1/models;
    a role whose endpoint is down is reported NOT_RUN, it is never faked."""
    if args.backend == "mock":
        be = MockExamBackend(seal, args.mock_brain)
        ok, why = be.reachable()
        return [{"role": "MOCK", "endpoint": None, "backend": be, "reachable": ok, "why": why}]
    specs = args.students or [f"MAIN={args.endpoint}"]
    out = []
    for spec in specs:
        role, _, url = spec.partition("=")
        if not url:
            role, url = "MAIN", spec
        be = LocalExamBackend(url, model=args.model)
        ok, why = be.reachable()
        out.append({"role": role.upper(), "endpoint": url, "backend": be, "reachable": ok, "why": why})
    return out


def run(args) -> int:
    manifest = load_manifest(Path(args.manifest))
    if args.python:
        manifest["python_interpreter"] = args.python
    sealed_dir = Path(args.sealed or os.environ.get(SEALED_ENV) or manifest["sealed_dir_default"])
    seal = Seal(sealed_dir, manifest)
    mismatches = seal.verify_hashes()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    run_id = args.run_id or f"exam-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    print(WEIGHTS_LINE)
    if mismatches and not args.allow_seal_drift:
        print("SEAL MISMATCH:\n  " + "\n  ".join(mismatches))
        return 4

    lab_root = Path(args.lab) if args.lab else None
    if args.self_check:
        res = self_check(manifest, seal, lab_root=lab_root)
        (out_dir / "self_check.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
        for r in res["cases"]:
            print(f"{r['case_id']:<12} visible(broken)={r['visible_on_broken']:<5} "
                  f"hidden(broken)={r['hidden_on_broken']:<5} visible(fixed)={r['visible_on_fixed']:<5} "
                  f"hidden(fixed)={r['hidden_on_fixed']:<5} ok={r['ok']}")
        print("SELF_CHECK", "OK" if res["ok"] else "BROKEN")
        print(WEIGHTS_LINE)
        return 0 if res["ok"] else 5

    students = discover_students(args, seal)
    live = [s for s in students if s["reachable"]]
    backend = (live or students)[0]["backend"]
    ok = bool(live)
    why = "; ".join(f"{s['role']}: {s['why']}" for s in students if not s["reachable"])
    holdout_ids = [c.case_id for c in manifest["_cases"] if c.split == "holdout"]
    forbidden = {lid for c in manifest["_cases"] if c.split == "holdout" for lid in c.lessons_forbidden}
    base_report = {"schema": SCHEMA, "exam_id": manifest["exam_id"], "run_id": run_id,
                   "base_sha": manifest["base_sha"], "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "python": platform.python_version(), "platform": platform.platform(),
                   "backend": {"name": backend.name, "label": backend.label, "model": backend.model},
                   "students": [{"role": s["role"], "endpoint": s["endpoint"], "reachable": s["reachable"],
                                 "model": s["backend"].model, "discovery": s["why"]} for s in students],
                   "weights_unchanged": True, "fine_tuning": "none",
                   "isolation": {"sealed_dir": str(sealed_dir), "sealed_files_checked": len(
                       manifest.get("sealed_sha256") or {}), "sealed_mismatches": len(mismatches),
                       "holdout_case_ids": holdout_ids, "lessons_refused": [], "holdout_lessons_written": 0}}
    if not ok:
        report = dict(base_report, run_status=RUN_NOT_MEASURED, reason=why, metrics={}, results=[],
                      lessons={"written": [], "rejected": [], "refused_holdout_linked": []},
                      not_run=[c.case_id for c in manifest["_cases"]])
        _write(out_dir, report)
        print(f"STATUS {RUN_NOT_MEASURED}: {why}")
        print(WEIGHTS_LINE)
        return 3

    lessons_dir = Path(args.lessons_dir or out_dir / "lessons")
    book_seed = LessonBook(lessons_dir)
    seeded = seed_lessons(book_seed, manifest, project_id=args.project_id, run_id=run_id, forbidden=forbidden)
    base_report["isolation"]["lessons_refused"] = seeded["refused_holdout_linked"]

    titles = {x["lesson_id"]: x["id"] for x in seeded["written"]}
    results: list[CaseResult] = []
    cases = [c for c in manifest["_cases"] if not args.only or c.case_id in set(args.only)]
    book_b = LessonBook(lessons_dir)
    for student in live:
        be, role = student["backend"], student["role"]
        # A: identical model/tools/budget/cases, WITHOUT the new lessons
        runner_a = ExamRunner(manifest, seal, be, run_id=run_id, project_id=args.project_id, book=None,
                              lab_root=lab_root, lesson_titles=titles, student_role=role)
        results += [runner_a.run_case(c, "no_lessons") for c in cases]
        # B: restart of the store (a fresh LessonBook on the same directory), WITH the lessons
        book_b = LessonBook(lessons_dir)
        runner_b = ExamRunner(manifest, seal, be, run_id=run_id, project_id=args.project_id, book=book_b,
                              lab_root=lab_root, lesson_titles=titles, student_role=role)
        results += [runner_b.run_case(c, "with_lessons") for c in cases]

    holdout_written = sum(1 for rec in book_b.all_lessons(include_candidates=True)
                          for cid in holdout_ids if cid in json.dumps(rec, ensure_ascii=False))
    base_report["isolation"]["holdout_lessons_written"] = holdout_written
    status = RUN_MOCK if backend.name == "mock" else RUN_MEASURED
    report = dict(base_report, run_status=status, metrics=summarize(results),
                  results=[asdict(r) for r in results], lessons=seeded,
                  usage={s["role"]: {"student_calls": getattr(s["backend"], "calls", 0),
                                     "prompt_chars": getattr(s["backend"], "prompt_chars", 0),
                                     "reply_chars": getattr(s["backend"], "reply_chars", 0)} for s in live},
                  students_not_run=[s["role"] for s in students if not s["reachable"]],
                  lessons_dir=str(lessons_dir))
    _write(out_dir, report)
    print(f"STATUS {status}")
    for r in results:
        print(f"  {r.student:<6} {r.case_id:<12} {r.profile:<13} {r.status:<24} "
              f"attempts={r.attempts} hints={r.hints_used}")
    for s in students:
        if not s["reachable"]:
            print(f"  {s['role']:<6} NOT_RUN: {s['why']}")
    print(f"report -> {out_dir / 'report.json'} / {out_dir / 'report.md'}")
    print(WEIGHTS_LINE)
    return 0


def _write(out_dir: Path, report: dict) -> None:
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "report.md").write_text(render_markdown(report), encoding="utf-8")


def parse_args(argv: list[str] | None = None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    p.add_argument("--sealed", default=None, help=f"sealed answers dir (env {SEALED_ENV})")
    p.add_argument("--lab", default=None, help="lab git worktree used for repo cases")
    p.add_argument("--python", default=None, help="interpreter that runs pytest (default: from the manifest)")
    p.add_argument("--backend", choices=("local", "mock"), default="local")
    p.add_argument("--mock-brain", choices=("smart", "dumb", "coachable"), default="smart")
    p.add_argument("--endpoint", default="http://127.0.0.1:8081/v1")
    p.add_argument("--students", nargs="*", default=None,
                   help="ROLE=URL pairs, e.g. MAIN=http://127.0.0.1:8081/v1 FAST=http://127.0.0.1:8082/v1")
    p.add_argument("--model", default=None, help="omit: the id is discovered from /v1/models")
    p.add_argument("--out", required=True)
    p.add_argument("--lessons-dir", default=None)
    p.add_argument("--project-id", default="coaching-exam-20260922")
    p.add_argument("--run-id", default=None)
    p.add_argument("--only", nargs="*", default=None)
    p.add_argument("--self-check", action="store_true")
    p.add_argument("--allow-seal-drift", action="store_true")
    return p.parse_args(argv)


def utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    utf8_console()
    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
