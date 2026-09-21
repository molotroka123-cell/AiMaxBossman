#!/usr/bin/env python3
"""Coaching runner: does the lesson loop change a local model's coding results?

Loop (per training task): attempt -> observable failure (hidden tests) ->
correction (student self-fix, or a TEACHER PATCH) -> lesson candidate in the
canonical learning store (``learning.lessons.LessonBook`` over
``learning.trace.LearningStore``) -> verified by the hidden tests (independent
external tool) -> retrieved for the ``coached`` profile after a store restart.

Profiles
  unassisted    baseline: no lessons retrieved, no lessons written
  teacher_patch student attempts, then the teacher (reference patch) fixes the
                failures; every teacher patch is an INTERVENTION and is NEVER a
                student pass; produces teacher-sourced lessons (train tasks only)
  coached       fresh LessonBook on the same directory (restart), lessons retrieved
                through the canonical API and injected into the prompt

Backends
  --backend local   OpenAI-compatible endpoint (llama-server style), env
                    BOSSMAN_COACH_ENDPOINT (default http://127.0.0.1:8081). No
                    endpoint reachable -> status LOCAL_LEARNING_GAIN_NOT_MEASURED,
                    exit 3, no fake numbers.
  --backend mock    MOCK scripted student (from the pack's ``mock`` block). Every
                    number it produces is labelled MOCK; it exists for CI.

Holdout tasks are evaluated but NEVER fed into learning: ``Coach.record_lesson``
raises ``HoldoutLeak`` for a holdout task, and the teacher never patches one.
No fine-tuning happens anywhere: WEIGHTS_UNCHANGED is printed on every run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
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

from learning.lessons import (CoachingEpisode, LessonBook, LessonPoisoned, Provenance,  # noqa: E402
                              format_for_prompt)

DEFAULT_PACK = REPO / "tests" / "coaching_pack"
DEFAULT_ENDPOINT = "http://127.0.0.1:8081"
ENDPOINT_ENV = "BOSSMAN_COACH_ENDPOINT"
MODEL_ENV = "BOSSMAN_COACH_MODEL"
PROFILES = ("unassisted", "teacher_patch", "coached")
STATUS_MOCK = "MOCK"
STATUS_MEASURED = "LOCAL_LEARNING_GAIN_MEASURED"
STATUS_NOT_MEASURED = "LOCAL_LEARNING_GAIN_NOT_MEASURED"
WEIGHTS_LINE = "WEIGHTS_UNCHANGED: no fine-tuning, no weight update anywhere in this runner"
TEST_TIMEOUT_S = 10
TOOL_PERMISSIONS = {"python_subprocess": "isolated (-I), tmp dir, no network use by the runner",
                    "filesystem": "task file in a temp dir only", "network": "model endpoint only",
                    "shell": False, "browser": False}
VERIFIER = {"principal_id": "tool:hidden_tests", "independence_class": "external_tool", "model_id": "", "run_id": ""}
_TEACHER = "teacher:reference-patch"


# ---------------------------------------------------------------- task pack
class HoldoutLeak(RuntimeError):
    """A holdout task tried to reach the learning store."""


@dataclass(slots=True)
class Task:
    task_id: str
    split: str
    task_class: str
    title: str
    prompt: str
    signature: str
    hidden_tests: list[str]
    reference_solution: str
    teacher_lesson: str
    mock: dict[str, Any]
    fingerprint: str = ""


def load_pack(pack_dir: Path = DEFAULT_PACK) -> dict[str, list[Task]]:
    out: dict[str, list[Task]] = {"train": [], "holdout": []}
    for split in ("train", "holdout"):
        for path in sorted((pack_dir / split).glob("*.json")):
            d = json.loads(path.read_text(encoding="utf-8"))
            if d.get("split") != split:
                raise ValueError(f"{path}: split field {d.get('split')!r} does not match folder {split!r}")
            t = Task(task_id=d["task_id"], split=split, task_class=d["task_class"], title=d["title"],
                     prompt=d["prompt"], signature=d["signature"], hidden_tests=list(d["hidden_tests"]),
                     reference_solution=d["reference_solution"], teacher_lesson=d.get("teacher_lesson", ""),
                     mock=dict(d.get("mock") or {}),
                     fingerprint=hashlib.sha256(path.read_bytes()).hexdigest()[:16])
            out[split].append(t)
    ids = [t.task_id for s in out.values() for t in s]
    if len(ids) != len(set(ids)):
        raise ValueError("task ids overlap between train and holdout")
    if len(out["train"]) < 5 or len(out["holdout"]) < 5:
        raise ValueError(f"pack needs >=5 train and >=5 holdout tasks (got {len(out['train'])}/{len(out['holdout'])})")
    return out


# ---------------------------------------------------------------- hidden tests (the observable failure)
@dataclass(slots=True)
class TestOutcome:
    passed: bool
    observation: str
    tool_error: bool = False


def run_hidden_tests(code: str, task: Task, *, timeout_s: float = TEST_TIMEOUT_S) -> TestOutcome:
    """Executes the candidate + hidden asserts in an isolated interpreter. Assertion
    failures are the task's observable failure; infra failures are tool errors."""
    if not code.strip():
        return TestOutcome(False, "no code produced", tool_error=True)
    harness = code.rstrip() + "\n\n" + "\n".join(
        f"try:\n    {t}\nexcept AssertionError:\n    print('FAIL', {json.dumps(t)})\n"
        f"except Exception as exc:\n    print('ERROR', {json.dumps(t)}, type(exc).__name__, str(exc)[:120])\n"
        f"else:\n    print('PASS', {json.dumps(t)})" for t in task.hidden_tests) + "\n"
    with tempfile.TemporaryDirectory(prefix="coach-") as tmp:
        path = Path(tmp) / "candidate.py"
        path.write_text(harness, encoding="utf-8")
        try:
            proc = subprocess.run([sys.executable, "-I", str(path)], capture_output=True, text=True,
                                  timeout=timeout_s, cwd=tmp)
        except subprocess.TimeoutExpired:
            return TestOutcome(False, f"timeout after {timeout_s}s", tool_error=True)
        except OSError as exc:
            return TestOutcome(False, f"could not start interpreter: {exc}", tool_error=True)
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith(("PASS", "FAIL", "ERROR"))]
    if proc.returncode != 0 or len(lines) != len(task.hidden_tests):
        err = (proc.stderr.strip().splitlines() or ["no output"])[-1][:200]
        return TestOutcome(False, f"candidate did not run: {err}", tool_error="SyntaxError" not in err)
    failed = [ln for ln in lines if not ln.startswith("PASS")]
    if failed:
        return TestOutcome(False, "; ".join(f[:160] for f in failed[:3]))
    return TestOutcome(True, f"{len(lines)} hidden tests passed")


# ---------------------------------------------------------------- backends
_CODE_BLOCK = re.compile(r"```(?:python)?\s*\n(.*?)```", re.S)
_LESSON_LINE = re.compile(r"(?im)^\s*LESSON:\s*(.+?)\s*$")


class Backend:
    name = "abstract"
    label = ""

    def reachable(self) -> tuple[bool, str]:
        raise NotImplementedError

    def student(self, task: Task, *, lessons_text: str, feedback: str | None) -> tuple[str, str]:
        """Returns (code, lesson_text_or_empty)."""
        raise NotImplementedError


class MockBackend(Backend):
    """MOCK: scripted answers from the pack. Not a model. Never a measurement."""
    name = "mock"
    label = "MOCK — scripted student from the task pack (not a model, not a measurement)"

    def reachable(self) -> tuple[bool, str]:
        return True, "mock backend needs no endpoint"

    def student(self, task: Task, *, lessons_text: str, feedback: str | None) -> tuple[str, str]:
        m = task.mock
        trigger = str(m.get("lesson_trigger") or "")
        if lessons_text and trigger and trigger in lessons_text:
            return task.reference_solution, ""
        if feedback is not None and m.get("second_attempt"):
            return str(m["second_attempt"]), str(m.get("student_lesson") or "")
        return str(m.get("first_attempt") or ""), ""


class OpenAICompatibleBackend(Backend):
    """llama-server / any OpenAI-compatible /v1/chat/completions endpoint."""
    name = "local"

    def __init__(self, endpoint: str, model: str, *, timeout_s: float = 120.0, temperature: float = 0.0,
                 seed: int = 7):
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self.temperature = temperature
        self.seed = seed
        self.label = f"local OpenAI-compatible endpoint {self.endpoint} model={self.model}"

    def reachable(self) -> tuple[bool, str]:
        try:
            with urllib.request.urlopen(urllib.request.Request(self.endpoint + "/v1/models"), timeout=3) as r:
                return r.status == 200, f"GET /v1/models -> {r.status}"
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return False, f"{self.endpoint} unreachable: {exc}"

    def _chat(self, messages: list[dict]) -> str:
        body = json.dumps({"model": self.model, "messages": messages, "temperature": self.temperature,
                           "seed": self.seed, "max_tokens": 700}).encode("utf-8")
        req = urllib.request.Request(self.endpoint + "/v1/chat/completions", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
            data = json.loads(r.read().decode("utf-8"))
        return str(data["choices"][0]["message"]["content"])

    def student(self, task: Task, *, lessons_text: str, feedback: str | None) -> tuple[str, str]:
        system = ("You are a careful Python programmer. Reply with exactly one ```python code block that "
                  "defines what is asked, no prose. Standard library only.")
        user = f"{task.prompt}\n\nStart with:\n{task.signature}\n"
        if lessons_text:
            user += "\nLessons from earlier verified corrections on similar tasks:\n" + lessons_text + "\n"
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        if feedback is not None:
            msgs.append({"role": "user", "content": "Your previous code failed the hidden tests: " + feedback +
                         "\nFix it. After the code block add one line 'LESSON: <one sentence on what to do "
                         "differently next time>'."})
        text = self._chat(msgs)
        m = _CODE_BLOCK.search(text)
        code = m.group(1) if m else text
        lm = _LESSON_LINE.search(text)
        return code, (lm.group(1) if lm else "")


# ---------------------------------------------------------------- the coach
@dataclass(slots=True)
class AttemptRecord:
    attempt_id: str
    n: int
    passed: bool
    observation: str
    tool_error: bool
    by: str                      # student | teacher


@dataclass(slots=True)
class TaskResult:
    task_id: str
    split: str
    task_class: str
    profile: str
    student_pass_at_1: bool
    student_passed: bool
    teacher_patch_passed: bool
    attempts: int
    tool_errors: int
    interventions: int
    lessons_injected: int
    lessons_written: int
    lessons_rejected: int
    wall_time_s: float
    attempt_log: list[dict] = field(default_factory=list)


class Coach:
    def __init__(self, backend: Backend, book: LessonBook | None, *, project_id: str, max_attempts: int,
                 run_id: str, model_name: str):
        self.backend = backend
        self.book = book
        self.project_id = project_id
        self.max_attempts = max(1, max_attempts)
        self.run_id = run_id
        self.model_name = model_name

    # --------------------------------------------------- learning (train only)
    def record_lesson(self, task: Task, *, attempt_id: str, failure: str, correction: str, source: str,
                      kind: str, who: str, evidence_refs: list[str]) -> str | None:
        """Candidate lesson through the canonical API, then verified by the hidden tests
        (external tool). Returns the lesson id, or None when rejected/poisoned."""
        if task.split == "holdout":
            raise HoldoutLeak(f"{task.task_id} is a holdout task; holdout results never become lessons")
        if self.book is None:
            return None
        ep = CoachingEpisode(attempt_id=attempt_id, task_id=task.task_id, project_id=self.project_id,
                             failure_observation=failure, correction=correction, source=source, kind=kind,
                             task_class=task.task_class, environment=f"python-{platform.python_version()}",
                             model=self.model_name if source == "student" else "reference-patch",
                             provenance=Provenance(who=who, what=f"{kind} on {task.task_id}", run_id=self.run_id,
                                                   evidence_refs=evidence_refs, model=self.model_name))
        try:
            self.book.save(ep)
        except LessonPoisoned:
            return None
        return ep.lesson_id

    def verify_lesson(self, lesson_id: str, task: Task, outcome: TestOutcome) -> bool:
        if self.book is None or not outcome.passed:
            return False
        self.book.verify(lesson_id, verifier=dict(VERIFIER, run_id=f"{self.run_id}:hidden_tests"),
                         evidence={"source": f"hidden_tests:{task.task_id}", "expected": "all hidden tests pass",
                                   "actual": outcome.observation, "head_sha": task.fingerprint,
                                   "environment": f"python-{platform.python_version()}"},
                         statement=f"hidden tests of {task.task_id} pass with the corrected code")
        return True

    # --------------------------------------------------- one task
    def run_task(self, task: Task, profile: str) -> TaskResult:
        t0 = time.monotonic()
        lessons: list[dict] = []
        if profile == "coached" and self.book is not None:
            lessons = self.book.retrieve(project_id=self.project_id, task_class=task.task_class, limit=5)
        lessons_text = format_for_prompt(lessons) if lessons else ""
        log: list[AttemptRecord] = []
        feedback: str | None = None
        student_passed = False
        tool_errors = written = rejected = 0
        last_failure = ""
        for n in range(1, self.max_attempts + 1):
            attempt_id = f"{self.run_id}:{profile}:{task.task_id}:a{n}"
            try:
                code, lesson_text = self.backend.student(task, lessons_text=lessons_text, feedback=feedback)
            except Exception as exc:  # noqa: BLE001 — endpoint/tool failure is a tool error, not a test failure
                out = TestOutcome(False, f"backend error: {type(exc).__name__}: {str(exc)[:160]}", tool_error=True)
                code, lesson_text = "", ""
            else:
                out = run_hidden_tests(code, task)
            tool_errors += int(out.tool_error)
            log.append(AttemptRecord(attempt_id, n, out.passed, out.observation, out.tool_error, "student"))
            if out.passed:
                student_passed = True
                if n > 1 and lesson_text and profile != "unassisted" and task.split == "train":
                    lid = self.record_lesson(task, attempt_id=attempt_id, failure=last_failure, correction=lesson_text,
                                             source="student", kind="student_fix", who=f"student:{self.model_name}",
                                             evidence_refs=[f"hidden_tests:{task.task_id}"])
                    if lid and self.verify_lesson(lid, task, out):
                        written += 1
                    elif lid is None:
                        rejected += 1
                break
            last_failure = out.observation
            feedback = out.observation
        teacher_passed = False
        interventions = 0
        if profile == "teacher_patch" and not student_passed and task.split == "train":
            interventions = 1
            attempt_id = f"{self.run_id}:{profile}:{task.task_id}:teacher"
            out = run_hidden_tests(task.reference_solution, task)
            tool_errors += int(out.tool_error)
            log.append(AttemptRecord(attempt_id, len(log) + 1, out.passed, out.observation, out.tool_error, "teacher"))
            teacher_passed = out.passed                       # recorded separately, never a student pass
            if task.teacher_lesson:
                lid = self.record_lesson(task, attempt_id=attempt_id, failure=last_failure or "student attempts failed",
                                         correction=task.teacher_lesson, source="teacher", kind="teacher_patch",
                                         who=_TEACHER, evidence_refs=[f"hidden_tests:{task.task_id}"])
                if lid and self.verify_lesson(lid, task, out):
                    written += 1
                elif lid is None:
                    rejected += 1
        return TaskResult(task_id=task.task_id, split=task.split, task_class=task.task_class, profile=profile,
                          student_pass_at_1=bool(log and log[0].passed and log[0].by == "student"),
                          student_passed=student_passed, teacher_patch_passed=teacher_passed,
                          attempts=sum(1 for a in log if a.by == "student"), tool_errors=tool_errors,
                          interventions=interventions, lessons_injected=len(lessons), lessons_written=written,
                          lessons_rejected=rejected, wall_time_s=round(time.monotonic() - t0, 4),
                          attempt_log=[asdict(a) for a in log])


# ---------------------------------------------------------------- metrics
def summarize(results: list[TaskResult]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for profile in PROFILES:
        for split in ("train", "holdout", "all"):
            rows = [r for r in results if r.profile == profile and (split == "all" or r.split == split)]
            if not rows:
                continue
            n = len(rows)
            out[f"{profile}/{split}"] = {
                "tasks": n,
                "pass_at_1": round(sum(r.student_pass_at_1 for r in rows) / n, 4),
                "student_pass_any_attempt": round(sum(r.student_passed for r in rows) / n, 4),
                "teacher_patch_passes_not_student": sum(r.teacher_patch_passed for r in rows),
                "attempts": sum(r.attempts for r in rows),
                "tool_errors": sum(r.tool_errors for r in rows),
                "interventions": sum(r.interventions for r in rows),
                "lessons_injected": sum(r.lessons_injected for r in rows),
                "lessons_written": sum(r.lessons_written for r in rows),
                "lessons_rejected": sum(r.lessons_rejected for r in rows),
                "wall_time_s": round(sum(r.wall_time_s for r in rows), 3),
            }
    return out


def learning_gain(metrics: dict[str, Any], *, mock: bool) -> dict[str, Any]:
    base = metrics.get("unassisted/holdout") or {}
    coached = metrics.get("coached/holdout") or {}
    gain = {"holdout_pass_at_1_unassisted": base.get("pass_at_1"),
            "holdout_pass_at_1_coached": coached.get("pass_at_1"),
            "delta": None if not base or not coached else round(coached["pass_at_1"] - base["pass_at_1"], 4)}
    gain["label"] = ("MOCK — scripted student, not a measurement of any model" if mock
                     else "measured on the configured local endpoint")
    return gain


def render_markdown(report: dict[str, Any]) -> str:
    m = report["metrics"]
    lines = [f"# Coaching run {report['run_id']}", "",
             f"**Status:** `{report['status']}`  ", f"**Backend:** {report['manifest']['backend']['label']}  ",
             f"**{WEIGHTS_LINE}**", ""]
    if report["status"] == STATUS_MOCK:
        lines += ["> MOCK RUN — every number below comes from a scripted student, not a model. "
                  "It proves the loop wiring, not learning gain.", ""]
    if report["status"] == STATUS_NOT_MEASURED:
        lines += [f"> {report.get('reason', '')}", ""]
    lines += ["| profile/split | tasks | pass@1 | student pass (any) | teacher passes (not student) | attempts | tool errors | interventions | lessons in | lessons written | wall s |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for key, v in m.items():
        lines.append(f"| {key} | {v['tasks']} | {v['pass_at_1']} | {v['student_pass_any_attempt']} | "
                     f"{v['teacher_patch_passes_not_student']} | {v['attempts']} | {v['tool_errors']} | "
                     f"{v['interventions']} | {v['lessons_injected']} | {v['lessons_written']} | {v['wall_time_s']} |")
    g = report.get("learning_gain") or {}
    lines += ["", f"Holdout pass@1: unassisted={g.get('holdout_pass_at_1_unassisted')} "
                  f"coached={g.get('holdout_pass_at_1_coached')} delta={g.get('delta')} ({g.get('label', '')})",
              f"Lessons in store at startup: {report['lessons_at_startup']}; after run: {report['lessons_after_run']} "
              f"(verified {report['verified_after_run']}); rejected at write: {report['lessons_rejected_at_write']}; "
              f"filtered at read: {report['lessons_filtered_at_read']}", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------- run
def build_manifest(args: argparse.Namespace, backend: Backend, pack: dict[str, list[Task]], run_id: str) -> dict:
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=REPO,
                             timeout=5).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        sha = "unknown"
    return {"run_id": run_id, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "git_head": sha, "python": platform.python_version(), "platform": platform.platform(),
            "backend": {"name": backend.name, "label": backend.label,
                        "endpoint": getattr(backend, "endpoint", None), "model": getattr(backend, "model", None),
                        "temperature": getattr(backend, "temperature", None), "seed": getattr(backend, "seed", None)},
            "runtime": {"max_attempts": args.max_attempts, "test_timeout_s": TEST_TIMEOUT_S,
                        "tool_permissions": TOOL_PERMISSIONS, "project_id": args.project_id,
                        "lessons_dir": str(Path(args.lessons_dir).resolve())},
            "pack": {"dir": str(Path(args.pack).resolve()),
                     "train": [{"task_id": t.task_id, "fingerprint": t.fingerprint} for t in pack["train"]],
                     "holdout": [{"task_id": t.task_id, "fingerprint": t.fingerprint} for t in pack["holdout"]]},
            "weights_unchanged": True, "fine_tuning": "none"}


def make_backend(args: argparse.Namespace) -> Backend:
    if args.backend == "mock":
        return MockBackend()
    endpoint = args.endpoint or os.environ.get(ENDPOINT_ENV) or DEFAULT_ENDPOINT
    model = args.model or os.environ.get(MODEL_ENV) or "local"
    return OpenAICompatibleBackend(endpoint, model)


def run(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    run_id = args.run_id or f"coach-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    pack = load_pack(Path(args.pack))
    backend = make_backend(args)
    manifest = build_manifest(args, backend, pack, run_id)
    (out_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(WEIGHTS_LINE)
    ok, why = backend.reachable()
    if not ok:
        report = {"run_id": run_id, "status": STATUS_NOT_MEASURED, "reason": why, "manifest": manifest, "metrics": {},
                  "results": [], "learning_gain": None, "lessons_at_startup": None, "lessons_after_run": None,
                  "verified_after_run": None, "lessons_rejected_at_write": 0, "lessons_filtered_at_read": 0,
                  "weights_unchanged": True}
        (out_dir / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        (out_dir / "summary.md").write_text(render_markdown(report), encoding="utf-8")
        print(f"STATUS {STATUS_NOT_MEASURED}: {why}")
        print(WEIGHTS_LINE)
        return 3

    lessons_dir = Path(args.lessons_dir)
    book0 = LessonBook(lessons_dir)
    lessons_at_startup = len(book0.all_lessons(include_candidates=False))
    model_name = getattr(backend, "model", None) or backend.name
    tasks_all = pack["train"] + pack["holdout"]
    results: list[TaskResult] = []

    # 1. baseline: no lessons read or written
    coach = Coach(backend, None, project_id=args.project_id, max_attempts=args.max_attempts, run_id=run_id,
                  model_name=model_name)
    results += [coach.run_task(t, "unassisted") for t in tasks_all]
    # 2. teacher patches on TRAIN only -> lessons (teacher + student self-corrections)
    coach = Coach(backend, book0, project_id=args.project_id, max_attempts=args.max_attempts, run_id=run_id,
                  model_name=model_name)
    results += [coach.run_task(t, "teacher_patch") for t in pack["train"]]
    # 3. restart: a NEW LessonBook on the same directory, then coached on train + holdout
    book1 = LessonBook(lessons_dir)
    coach = Coach(backend, book1, project_id=args.project_id, max_attempts=args.max_attempts, run_id=run_id,
                  model_name=model_name)
    results += [coach.run_task(t, "coached") for t in tasks_all]

    metrics = summarize(results)
    mock = backend.name == "mock"
    status = STATUS_MOCK if mock else STATUS_MEASURED
    report = {"run_id": run_id, "status": status, "manifest": manifest, "metrics": metrics,
              "learning_gain": learning_gain(metrics, mock=mock), "results": [asdict(r) for r in results],
              "lessons_at_startup": lessons_at_startup,
              "lessons_after_run": len(book1.all_lessons(include_candidates=True)),
              "verified_after_run": len(book1.all_lessons(include_candidates=False)),
              "lessons_rejected_at_write": book0.rejected_at_write + book1.rejected_at_write,
              "lessons_filtered_at_read": book0.filtered_at_read + book1.filtered_at_read,
              "holdout_task_ids": [t.task_id for t in pack["holdout"]], "weights_unchanged": True}
    (out_dir / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out_dir / "summary.md").write_text(render_markdown(report), encoding="utf-8")
    print(f"STATUS {status}")
    if mock:
        print("MOCK RUN — numbers come from a scripted student, not from a model")
    g = report["learning_gain"]
    print(f"holdout pass@1 unassisted={g['holdout_pass_at_1_unassisted']} coached={g['holdout_pass_at_1_coached']} "
          f"delta={g['delta']} [{g['label']}]")
    print(f"lessons: startup={lessons_at_startup} after={report['lessons_after_run']} "
          f"verified={report['verified_after_run']} -> {out_dir / 'results.json'}")
    print(WEIGHTS_LINE)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--backend", choices=("local", "mock"), default="local")
    p.add_argument("--endpoint", default=None, help=f"OpenAI-compatible base URL (env {ENDPOINT_ENV})")
    p.add_argument("--model", default=None, help=f"model name sent to the endpoint (env {MODEL_ENV})")
    p.add_argument("--pack", default=str(DEFAULT_PACK))
    p.add_argument("--out", required=True, help="output directory (run_manifest.json, results.json, summary.md)")
    p.add_argument("--lessons-dir", default=None, help="lesson store dir (default <out>/lessons); reuse across runs")
    p.add_argument("--project-id", default="coaching-pack")
    p.add_argument("--max-attempts", type=int, default=2)
    p.add_argument("--run-id", default=None)
    args = p.parse_args(argv)
    if args.lessons_dir is None:
        args.lessons_dir = str(Path(args.out) / "lessons")
    return args


def main(argv: list[str] | None = None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
