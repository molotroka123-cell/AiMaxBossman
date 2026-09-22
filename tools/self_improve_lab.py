#!/usr/bin/env python3
"""Self-improvement lab: drive the REAL Bossman coding-tasks API through a controlled
comparison, turn a verified pass into a recipe, and test transfer after a restart.

Stdlib only. Runs from a checkout (``tools/self_improve_lab.py``) or from the Windows
bundle (``app-support/self_improve_lab.py``); it imports nothing from the repository and
needs no PYTHONPATH. Built-in sample cases (``--case sample`` / ``--case sample-transfer``)
generate their synthetic repositories themselves.

Phases
  explore   one free-exploration coding task with a chosen agent (no hidden verifier:
            the result is reported as NOT_VERIFIED, never as a pass)
  compare   ONE fixed case, every variant on a fresh ``git clone --no-local`` of the
            baseline; hard wall budget per variant (default 40 min, persisted, never
            extended on resume); then the INDEPENDENT hidden verifier applies the returned
            diff to another clean copy and runs a command the student never saw
  lesson    the best verified pass -> an executable recipe via POST /api/coding-recipes
  transfer  after a full Bossman restart (boot ``started_at`` must differ), a NEW
            analogous case: CONTROL (memory off) vs MEMORY (memory on), memory_hit and
            solve reported separately; equal outcomes -> NO_MEASURED_GAIN
  intervene record a coach/teacher intervention for a compare variant (hint |
            teacher_patch | cloud_fix); a teacher patch or a cloud fix is never a
            student pass
  status    print the state file
  make-sample  materialise the synthetic sample repositories (for inspection)

Outcome per variant, exactly one of:
  STUDENT_UNASSISTED_PASS  STUDENT_COACHED_PASS  TEACHER_PATCH  FAIL  TIMEOUT  BLOCKED
Every report carries ``weights: WEIGHTS_UNCHANGED`` — nothing here trains a model.

Auth: header ``X-BCC-Token``. Token from --token, env BCC_TOKEN, --token-file, or
``<data dir>/token`` (--data-dir, env BCC_DATA_DIR, else the platform default
``%LOCALAPPDATA%\\Bossman\\CommandCenter``).

State: ``--state`` (default ``<out>/lab-state.json``), rewritten atomically after every
step; a rerun resumes (completed variants are skipped, a running one keeps its original
deadline). ``--stop-file`` (default ``<out>/STOP``) is checked between polls.

Exit codes
  0  phase finished (outcomes recorded, whatever they are)
  1  internal error
  2  usage error / invalid case / case would leak the verifier to the student
  3  Bossman API unreachable or token refused
  4  BLOCKED precondition (coding tasks not ready, restart not detected, lab agents missing)
  5  STOPPED by the STOP file (state saved; rerun the same command to resume)
  6  NO_VERIFIED_PASS (lesson: nothing verified to learn from)
"""
from __future__ import annotations

import argparse
import copy
import io
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

STATE_SCHEMA = "bossman.self-improve-lab.state/1"
CASE_SCHEMA = "bossman.self-improve-case/1"
RECIPE_SCHEMA_VERSION = "bossman.coding-recipe/1"
WEIGHTS = "WEIGHTS_UNCHANGED"

STUDENT_UNASSISTED_PASS = "STUDENT_UNASSISTED_PASS"
STUDENT_COACHED_PASS = "STUDENT_COACHED_PASS"
TEACHER_PATCH = "TEACHER_PATCH"
FAIL = "FAIL"
TIMEOUT = "TIMEOUT"
BLOCKED = "BLOCKED"
OUTCOMES = (STUDENT_UNASSISTED_PASS, STUDENT_COACHED_PASS, TEACHER_PATCH, FAIL, TIMEOUT, BLOCKED)
STUDENT_PASSES = (STUDENT_UNASSISTED_PASS, STUDENT_COACHED_PASS)

MEASURED_GAIN = "MEASURED_GAIN"
NO_MEASURED_GAIN = "NO_MEASURED_GAIN"
MEASURED_REGRESSION = "MEASURED_REGRESSION"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"

EXIT_OK, EXIT_INTERNAL, EXIT_USAGE, EXIT_API, EXIT_BLOCKED, EXIT_STOPPED, EXIT_NO_PASS = 0, 1, 2, 3, 4, 5, 6

VARIANTS = ("RAW", "TOOL_FIRST", "MEMORY", "PLAN_EXECUTE_VERIFY", "RED_TEAM", "USER_UX")
MEMORY_VARIANT = "MEMORY"
INTERVENTION_KINDS = ("hint", "teacher_patch", "cloud_fix")
TERMINAL = ("completed", "failed", "blocked")
DEFAULT_BASE_URL = "http://127.0.0.1:8800"
DEFAULT_BUDGET_MIN = 40.0
DEFAULT_POLL_S = 10.0
EXPLORE_INSTRUCTION = ("Улучши Bossman: воспроизведи полезный реальный дефект, подготовь "
                       "минимальный patch, regression и проверенный результат")
VERIFIER_PRINCIPAL = "tool:self-improve-lab-hidden-verifier"
ALLOWED_RECIPE_TOOLS = ("read_file", "search", "list_dir", "edit_file", "write_file", "run_tests")
MAX_RECIPE_STEPS = 24
_FIXED_DATE = "2026-01-01T00:00:00+0000"


class LabError(Exception):
    exit_code = EXIT_INTERNAL


class UsageError(LabError):
    exit_code = EXIT_USAGE


class ApiUnavailable(LabError):
    exit_code = EXIT_API


class Blocked(LabError):
    exit_code = EXIT_BLOCKED


class Stopped(LabError):
    exit_code = EXIT_STOPPED


class NoVerifiedPass(LabError):
    exit_code = EXIT_NO_PASS


def utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, io.UnsupportedOperation):
            pass


def _now_iso(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if ts is None else ts))


def log(msg: str) -> None:
    print(f"[lab {time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


# ============================================================== outcome classification
def classify_outcome(*, blocked: bool, timed_out: bool, verifier_passed: bool | None,
                     interventions: list[dict] | None = None) -> str:
    """Exactly one outcome. Order matters: a blocked run is not a timeout, a timeout is
    not a fail, and a pass produced with a teacher patch or a cloud fix is never a
    student pass — whatever the verifier says."""
    if blocked:
        return BLOCKED
    if timed_out:
        return TIMEOUT
    if not verifier_passed:
        return FAIL
    kinds = {str(i.get("kind")) for i in (interventions or [])}
    if kinds & {"teacher_patch", "cloud_fix"}:
        return TEACHER_PATCH
    if "hint" in kinds:
        return STUDENT_COACHED_PASS
    return STUDENT_UNASSISTED_PASS


def is_student_pass(outcome: str | None) -> bool:
    return outcome in STUDENT_PASSES


def gain_verdict(control: str | None, memory: str | None) -> str:
    """Transfer verdict for ONE control/memory pair (n=1: an observation, not a rate)."""
    if control is None or memory is None or BLOCKED in (control, memory):
        return INSUFFICIENT_EVIDENCE
    c, m = is_student_pass(control), is_student_pass(memory)
    if m and not c:
        return MEASURED_GAIN
    if c and not m:
        return MEASURED_REGRESSION
    return NO_MEASURED_GAIN


def summarize(results: dict[str, dict]) -> dict:
    counts = {o: 0 for o in OUTCOMES}
    for r in results.values():
        if r.get("outcome") in counts:
            counts[r["outcome"]] += 1
    return {"outcomes": {k: r.get("outcome") for k, r in results.items()},
            "counts": counts,
            "student_unassisted_passes": counts[STUDENT_UNASSISTED_PASS],
            "student_coached_passes": counts[STUDENT_COACHED_PASS],
            # never merged into the student numbers above
            "teacher_patches": counts[TEACHER_PATCH],
            "variants_run": len(results), "weights": WEIGHTS}


# ============================================================== files, git
def _on_rm_error(func, path, _exc):
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def rmtree(path: Path) -> None:
    if Path(path).exists():
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=_on_rm_error)
        else:                                       # pragma: no cover
            shutil.rmtree(path, onerror=_on_rm_error)


def write_json_atomic(path: Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _git_env() -> dict:
    env = dict(os.environ)
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_AUTHOR_NAME": "bossman-lab",
                "GIT_AUTHOR_EMAIL": "lab@bossman.invalid", "GIT_COMMITTER_NAME": "bossman-lab",
                "GIT_COMMITTER_EMAIL": "lab@bossman.invalid"})
    return env


def git(*args: str, cwd: Path | None = None, input_text: str | None = None,
        env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["git", "-c", "core.autocrlf=false", "-c", "core.safecrlf=false", *args]
    proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None, input=input_text, text=True,
                          encoding="utf-8", errors="replace", capture_output=True,
                          env=env or _git_env())
    if check and proc.returncode != 0:
        raise LabError(f"git {' '.join(args[:3])} failed: {proc.stderr.strip()[:400]}")
    return proc


def resolve_commit(repo: Path, rev: str) -> str:
    return git("rev-parse", "--verify", f"{rev}^{{commit}}", cwd=repo).stdout.strip()


def clone_clean(src: Path, baseline: str, dest: Path) -> str:
    """A fresh, remote-less copy of ``src`` at ``baseline``. Nothing from an earlier
    variant can survive: the directory is removed and re-cloned from the source."""
    rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    git("clone", "--no-local", "--quiet", "--no-checkout", str(src), str(dest))
    git("checkout", "--quiet", "--detach", baseline, cwd=dest)
    git("remote", "remove", "origin", cwd=dest, check=False)
    dirty = git("status", "--porcelain", "--untracked-files=all", cwd=dest).stdout.strip()
    if dirty:
        raise LabError(f"clean copy is not clean: {dirty[:200]}")
    return git("rev-parse", "HEAD", cwd=dest).stdout.strip()


# ============================================================== synthetic sample repos
_INVOICE_MONEY = '''"""Денежные суммы из текстовых счетов."""


def parse_amount(text: str) -> int:
    """Сумма в копейках: '12.50' -> 1250, '7' -> 700."""
    cleaned = text.strip().replace(" ", "")
    if not cleaned:
        raise ValueError("пустая сумма")
    if "." in cleaned:
        whole, frac = cleaned.split(".", 1)
    else:
        whole, frac = cleaned, "0"
    if not whole.isdigit() or not frac.isdigit() or len(frac) > 2:
        raise ValueError(f"не сумма: {text!r}")
    return int(whole) * 100 + int(frac.ljust(2, "0"))
'''

_INVOICE_TEST = '''import unittest

from invoicekit.money import parse_amount


class ParseAmountTest(unittest.TestCase):
    def test_dot_decimal(self):
        self.assertEqual(parse_amount("12.50"), 1250)

    def test_whole(self):
        self.assertEqual(parse_amount("7"), 700)

    def test_garbage(self):
        with self.assertRaises(ValueError):
            parse_amount("abc")


if __name__ == "__main__":
    unittest.main()
'''

_STOCK_WEIGHT = '''"""Вес из складских накладных."""


def parse_weight_kg(text: str) -> int:
    """Вес в граммах из килограммов: '1.5' -> 1500, '2' -> 2000."""
    cleaned = text.strip().replace(" ", "")
    if not cleaned:
        raise ValueError("пустой вес")
    if "." in cleaned:
        whole, frac = cleaned.split(".", 1)
    else:
        whole, frac = cleaned, "0"
    if not whole.isdigit() or not frac.isdigit() or len(frac) > 3:
        raise ValueError(f"не вес: {text!r}")
    return int(whole) * 1000 + int(frac.ljust(3, "0"))
'''

_STOCK_TEST = '''import unittest

from stockkit.weight import parse_weight_kg


class ParseWeightTest(unittest.TestCase):
    def test_dot_decimal(self):
        self.assertEqual(parse_weight_kg("1.5"), 1500)

    def test_whole(self):
        self.assertEqual(parse_weight_kg("2"), 2000)

    def test_garbage(self):
        with self.assertRaises(ValueError):
            parse_weight_kg("кг")


if __name__ == "__main__":
    unittest.main()
'''

SYNTHETIC_REPOS: dict[str, dict[str, str]] = {
    "invoicekit": {
        "README.md": "# invoicekit\n\nРазбор сумм из текстовых счетов. Тесты: `python -m unittest discover -s tests -t .`\n",
        "invoicekit/__init__.py": "",
        "invoicekit/money.py": _INVOICE_MONEY,
        "tests/__init__.py": "",
        "tests/test_money.py": _INVOICE_TEST,
    },
    "stockkit": {
        "README.md": "# stockkit\n\nРазбор веса из складских накладных. Тесты: `python -m unittest discover -s tests -t .`\n",
        "stockkit/__init__.py": "",
        "stockkit/weight.py": _STOCK_WEIGHT,
        "tests/__init__.py": "",
        "tests/test_weight.py": _STOCK_TEST,
    },
}

_HIDDEN_INVOICE = '''import unittest

from invoicekit.money import parse_amount


class HiddenParseAmount(unittest.TestCase):
    def test_decimal_comma(self):
        self.assertEqual(parse_amount("12,50"), 1250)

    def test_grouped_with_space(self):
        self.assertEqual(parse_amount("1 234,50"), 123450)

    def test_grouped_with_nbsp(self):
        self.assertEqual(parse_amount("1\\u00a0234,50"), 123450)

    def test_grouped_with_narrow_nbsp(self):
        self.assertEqual(parse_amount("1\\u202f234,5"), 123450)

    def test_old_behaviour_kept(self):
        self.assertEqual(parse_amount("12.50"), 1250)
        self.assertEqual(parse_amount("7"), 700)

    def test_still_strict(self):
        for bad in ("", "abc", "1,2,3", "12,345"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_amount(bad)
'''

_HIDDEN_STOCK = '''import unittest

from stockkit.weight import parse_weight_kg


class HiddenParseWeight(unittest.TestCase):
    def test_decimal_comma(self):
        self.assertEqual(parse_weight_kg("1,5"), 1500)

    def test_grouped_with_nbsp(self):
        self.assertEqual(parse_weight_kg("1\\u00a0250,75"), 1250750)

    def test_grouped_with_space(self):
        self.assertEqual(parse_weight_kg("1 250,75"), 1250750)

    def test_old_behaviour_kept(self):
        self.assertEqual(parse_weight_kg("1.5"), 1500)
        self.assertEqual(parse_weight_kg("2"), 2000)

    def test_still_strict(self):
        for bad in ("", "кг", "1,2,3", "1,2345"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_weight_kg(bad)
'''

_UNITTEST_CMD = ["{python}", "-m", "unittest", "discover", "-s", "{hidden}", "-t", "{hidden}", "-p", "hidden_*.py"]

BUILTIN_CASES: dict[str, dict] = {
    "sample": {
        "schema": CASE_SCHEMA, "case_id": "sample-decimal-comma-invoice",
        "project_id": "lab-synthetic",
        "source_repo": {"synthetic": "invoicekit"}, "baseline_commit": "HEAD",
        "instruction": ("Импорт счёта из 1С падает: parse_amount(\"1 234,50\") бросает ValueError, "
                        "а должно вернуться 123450 копеек. В выгрузке между разрядами стоят "
                        "неразрывные пробелы, десятичный разделитель — запятая. Почини "
                        "invoicekit/money.py и добавь регрессионный тест в tests/."),
        "allowed_paths": ["invoicekit", "tests"], "protected_paths": ["README.md"],
        "hidden_verifier": {"command": _UNITTEST_CMD, "files": {"hidden_test_money.py": _HIDDEN_INVOICE},
                            "timeout_seconds": 300},
        "model": None, "budget_minutes": DEFAULT_BUDGET_MIN,
        "lesson_template": {
            "id": "decimal-comma-nbsp-parse",
            "title": "Разбор числа: запятая-разделитель и неразрывные пробелы",
            "symptom": ("Разбор числа из текста бросает ValueError на вводе вида \"1 234,50\": "
                        "десятичный разделитель — запятая, между разрядами неразрывные пробелы"),
            "cause": ("Парсер знает только точку как десятичный разделитель и удаляет лишь "
                      "обычный пробел; неразрывные пробелы (U+00A0, U+202F) и запятая остаются"),
            "diagnosis": ("Воспроизвести на падающем вводе; прочитать функцию разбора; проверить, "
                          "какие пробельные символы и разделители она убирает"),
            "action": ("Удалять все пробельные символы через \"\".join(text.split()), единственную "
                       "запятую при отсутствии точки считать десятичным разделителем, остальную "
                       "проверку формата оставить строгой; добавить регрессию на запятую и NBSP"),
            "applies_when": {"task_class": "parsing", "language": "python",
                             "keywords": ["ValueError", "десятичный разделитель", "запятая",
                                          "неразрывный пробел", "разбор числа"]},
            "counterexample": ("Не применять, если запятая — разделитель тысяч (формат 1,234.50): "
                               "там её удаляют, а не превращают в точку"),
            "required_check_paths": ["tests"],
        },
    },
    "sample-transfer": {
        "schema": CASE_SCHEMA, "case_id": "sample-decimal-comma-stock-transfer",
        "project_id": "lab-synthetic",
        "source_repo": {"synthetic": "stockkit"}, "baseline_commit": "HEAD",
        "instruction": ("Приёмка со склада падает: parse_weight_kg(\"1 250,75\") бросает ValueError, "
                        "ожидается 1250750 граммов. В накладных между разрядами неразрывный "
                        "пробел, десятичный разделитель — запятая. Почини stockkit/weight.py и "
                        "добавь регрессионный тест в tests/."),
        "allowed_paths": ["stockkit", "tests"], "protected_paths": ["README.md"],
        "hidden_verifier": {"command": _UNITTEST_CMD, "files": {"hidden_test_weight.py": _HIDDEN_STOCK},
                            "timeout_seconds": 300},
        "model": None, "budget_minutes": DEFAULT_BUDGET_MIN,
    },
}


def make_synthetic_repo(name: str, dest: Path) -> str:
    """Create (or reuse) the synthetic repo ``name`` at ``dest``; returns its HEAD sha.
    Deterministic content, author and dates: the same files give the same commit."""
    files = SYNTHETIC_REPOS.get(name)
    if files is None:
        raise UsageError(f"unknown synthetic repo {name!r} (known: {', '.join(SYNTHETIC_REPOS)})")
    if (dest / ".git").is_dir():
        clean = not git("status", "--porcelain", cwd=dest).stdout.strip()
        same = all((dest / rel).is_file() and (dest / rel).read_text(encoding="utf-8") == body
                   for rel, body in files.items())
        if clean and same:
            return git("rev-parse", "HEAD", cwd=dest).stdout.strip()
        rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    git("init", "--quiet", cwd=dest)
    for rel, body in files.items():
        path = dest / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)
    git("add", "--", *files.keys(), cwd=dest)
    env = _git_env()
    env.update({"GIT_AUTHOR_DATE": _FIXED_DATE, "GIT_COMMITTER_DATE": _FIXED_DATE})
    git("commit", "--quiet", "--no-gpg-sign", "-m", f"{name}: baseline with seeded defect",
        cwd=dest, env=env)
    return git("rev-parse", "HEAD", cwd=dest).stdout.strip()


# ============================================================== cases
def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def load_case(arg: str) -> tuple[dict, Path | None]:
    """(case dict, case file path or None for a built-in case)."""
    if arg in BUILTIN_CASES:
        return copy.deepcopy(BUILTIN_CASES[arg]), None
    path = Path(arg).expanduser()
    if not path.is_file():
        raise UsageError(f"case file not found: {arg} (built-in: {', '.join(BUILTIN_CASES)})")
    try:
        case = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UsageError(f"case file unreadable: {exc}") from exc
    return case, path.resolve()


def validate_case(case: dict) -> list[str]:
    errs = []
    if not isinstance(case, dict):
        return ["case must be an object"]
    if case.get("schema") != CASE_SCHEMA:
        errs.append(f"schema must be {CASE_SCHEMA}")
    for key in ("case_id", "instruction", "baseline_commit"):
        if not str(case.get(key) or "").strip():
            errs.append(f"{key} is required")
    src = case.get("source_repo")
    if not (isinstance(src, str) and src.strip()) and not (isinstance(src, dict) and src.get("synthetic")):
        errs.append("source_repo must be a path or {\"synthetic\": name}")
    if not isinstance(case.get("allowed_paths"), list) or not case.get("allowed_paths"):
        errs.append("allowed_paths must be a non-empty list")
    hv = case.get("hidden_verifier")
    if not isinstance(hv, dict) or not isinstance(hv.get("command"), list) or not hv.get("command"):
        errs.append("hidden_verifier.command must be a non-empty argv list (never a shell string)")
    elif not (isinstance(hv.get("files"), dict) and hv["files"]) and not hv.get("dir"):
        errs.append("hidden_verifier needs files{} or dir")
    budget = case.get("budget_minutes", DEFAULT_BUDGET_MIN)
    if not isinstance(budget, (int, float)) or budget <= 0:
        errs.append("budget_minutes must be > 0")
    return errs


def resolve_source(case: dict, case_path: Path | None, work: Path) -> tuple[Path, str]:
    """(source repo path, baseline sha). Refuses a case whose verifier would be visible
    to the student: a case file or a hidden-verifier dir inside the source repository."""
    src = case["source_repo"]
    if isinstance(src, dict):
        name = str(src["synthetic"])
        repo = work / "sources" / name
        make_synthetic_repo(name, repo)
    else:
        repo = Path(src).expanduser()
        if not repo.is_absolute() and case_path is not None:
            repo = case_path.parent / repo
        repo = repo.resolve()
        if not (repo / ".git").exists():
            raise UsageError(f"source_repo is not a git repository: {repo}")
        if case_path is not None and _inside(case_path, repo):
            raise UsageError("the case file lies inside the source repository: the student would "
                             "see the hidden verifier. Move the case file outside the repo.")
        hv_dir = (case.get("hidden_verifier") or {}).get("dir")
        if hv_dir:
            d = Path(hv_dir)
            if not d.is_absolute() and case_path is not None:
                d = case_path.parent / d
            if _inside(d, repo):
                raise UsageError("hidden_verifier.dir lies inside the source repository")
    return repo, resolve_commit(repo, str(case["baseline_commit"]))


def _hidden_files(case: dict, case_path: Path | None) -> dict[str, str]:
    hv = case["hidden_verifier"]
    files = dict(hv.get("files") or {})
    if hv.get("dir"):
        d = Path(hv["dir"])
        if not d.is_absolute() and case_path is not None:
            d = case_path.parent / d
        for p in sorted(Path(d).rglob("*")):
            if p.is_file():
                files[str(p.relative_to(d)).replace("\\", "/")] = p.read_text(encoding="utf-8")
    return files


# ============================================================== hidden verifier
def build_verifier_env(repo: Path, scratch: Path) -> dict:
    """A minimal environment: no tokens, no Bossman settings, no inherited PYTHONPATH."""
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(repo),
           "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
           "HOME": str(scratch), "USERPROFILE": str(scratch), "TEMP": str(scratch),
           "TMP": str(scratch), "TMPDIR": str(scratch), "LANG": "C.UTF-8"}
    if os.name == "nt":
        for key in ("SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "WINDIR"):
            if os.environ.get(key):
                env[key] = os.environ[key]
    return env


def run_hidden_verifier(*, case: dict, case_path: Path | None, src: Path, baseline: str,
                        diff: str, vdir: Path, student_dir: Path | None) -> dict:
    """Apply ``diff`` to ANOTHER clean copy and run the hidden command there, outside the
    student's sandbox, with a minimal environment. The hidden files are written only now,
    after the student has finished, into a directory the student never had."""
    repo, hidden, scratch = vdir / "repo", vdir / "verifier-private", vdir / "scratch"
    for d in (hidden, scratch):
        rmtree(d)
    out: dict[str, Any] = {"passed": False, "reason": "", "exit_code": None, "changed_files": []}
    clone_clean(src, baseline, repo)
    if not (diff or "").strip():
        out["reason"] = "NO_DIFF"
        return out
    applied = git("apply", "--whitespace=nowarn", "-", cwd=repo, input_text=diff, check=False)
    if applied.returncode != 0:
        out.update(reason="DIFF_DOES_NOT_APPLY", stderr=applied.stderr[-2000:])
        return out
    git("add", "--all", cwd=repo)
    changed = [l for l in git("diff", "--cached", "--name-only", cwd=repo).stdout.splitlines() if l]
    out["changed_files"] = changed
    protected = [str(p).strip("/\\") for p in case.get("protected_paths") or []]
    allowed = [str(p).strip("/\\") for p in case.get("allowed_paths") or []]

    def under(path: str, roots: list[str]) -> bool:
        return any(path == r or path.startswith(r + "/") for r in roots if r)
    touched_protected = [c for c in changed if under(c, protected)]
    outside = [c for c in changed if allowed and not under(c, allowed)]
    if touched_protected or outside:
        out.update(reason="SCOPE_VIOLATION", protected=touched_protected, outside_allowed=outside)
        return out
    files = _hidden_files(case, case_path)
    hidden.mkdir(parents=True, exist_ok=True)
    scratch.mkdir(parents=True, exist_ok=True)
    for rel, body in files.items():
        path = hidden / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)
    visible = []
    if student_dir is not None and student_dir.exists():
        names = {Path(rel).name for rel in files}
        visible = [str(p) for p in student_dir.rglob("*") if p.name in names]
    out["hidden_visible_to_student"] = bool(visible)
    if visible:
        out.update(reason="VERIFIER_VISIBLE_TO_STUDENT", visible=visible[:5])
        return out
    hv = case["hidden_verifier"]
    mapping = {"{python}": sys.executable, "{hidden}": str(hidden), "{repo}": str(repo)}
    argv = [mapping.get(a, a) for a in hv["command"]]
    env = build_verifier_env(repo, scratch)
    out["env_keys"] = sorted(env)
    try:
        proc = subprocess.run(argv, cwd=str(repo), env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=float(hv.get("timeout_seconds") or 600))
    except subprocess.TimeoutExpired:
        out.update(reason="VERIFIER_TIMEOUT")
        return out
    except OSError as exc:
        out.update(reason=f"VERIFIER_NOT_RUNNABLE: {exc}")
        return out
    out["exit_code"] = proc.returncode
    out["output_tail"] = (proc.stdout + proc.stderr)[-3000:]
    out["passed"] = proc.returncode == 0
    out["reason"] = "PASSED" if out["passed"] else "VERIFIER_FAILED"
    return out


# ============================================================== HTTP
class Api:
    def __init__(self, base_url: str, token: str, *, timeout: float = 30.0):
        self.base = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def request(self, method: str, path: str, payload: Any = None) -> tuple[int, Any]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("X-BCC-Token", self.token)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                status, raw = resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise ApiUnavailable(f"Bossman API unreachable at {self.base}: {exc}") from exc
        try:
            body = json.loads(raw.decode("utf-8")) if raw else None
        except ValueError:
            body = {"raw": raw[:500].decode("utf-8", "replace")}
        if status == 401:
            raise ApiUnavailable("Bossman refused the token (401): check --token / --data-dir")
        return status, body

    def get(self, path: str) -> tuple[int, Any]:
        return self.request("GET", path)

    def post(self, path: str, payload: Any) -> tuple[int, Any]:
        return self.request("POST", path, payload)


def default_data_dir() -> Path:
    configured = os.environ.get("BCC_DATA_DIR")
    if configured:
        return Path(configured).expanduser()
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "Bossman" / "CommandCenter"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Bossman" / "CommandCenter"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "bossman" / "command-center"


def resolve_token(args) -> str:
    if args.token:
        return args.token.strip()
    if os.environ.get("BCC_TOKEN"):
        return os.environ["BCC_TOKEN"].strip()
    path = Path(args.token_file) if args.token_file else Path(args.data_dir or default_data_dir()) / "token"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise UsageError(f"no token: pass --token, set BCC_TOKEN, or point --data-dir/--token-file "
                         f"at the Bossman data dir ({path}: {exc})") from exc


def boot_identity(api: Api) -> dict:
    status, body = api.get("/api/identity")
    if status != 200 or not isinstance(body, dict):
        raise ApiUnavailable(f"/api/identity answered {status}")
    return {"started_at": body.get("started_at"), "version": body.get("version"),
            "source_sha": body.get("source_sha") or body.get("sha")}


def ensure_agents(api: Api) -> dict[str, dict]:
    status, body = api.post("/api/lab-agents/ensure", {})
    if status != 200 or not isinstance(body, dict):
        raise Blocked(f"lab agents unavailable (POST /api/lab-agents/ensure -> {status}): "
                      "this Bossman build has no lab_agents feature")
    return {a["variant"]: {"id": a["id"], "name": a["name"], "use_memory": a.get("use_memory")}
            for a in body.get("agents") or [] if a.get("variant")}


def readiness(api: Api) -> dict:
    status, body = api.get("/api/coding-tasks/readiness")
    if status != 200 or not isinstance(body, dict):
        raise Blocked(f"coding tasks readiness answered {status}")
    return body


def recipe_ids(api: Api, project_id: str) -> list[str]:
    status, body = api.get("/api/coding-recipes?" + urllib.parse.urlencode({"project_id": project_id}))
    if status != 200 or not isinstance(body, dict):
        return []
    return sorted(str(r.get("id")) for r in body.get("items") or [])


# ============================================================== state
class State:
    def __init__(self, path: Path):
        self.path = Path(path)
        if self.path.is_file():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except ValueError as exc:
                raise UsageError(f"state file is corrupt: {self.path}: {exc}") from exc
        else:
            self.data = {"schema": STATE_SCHEMA, "created_at": _now_iso(), "weights": WEIGHTS}

    def save(self) -> None:
        self.data["updated_at"] = _now_iso()
        write_json_atomic(self.path, self.data)


# ============================================================== one student run
class Runner:
    def __init__(self, api: Api, *, work: Path, stop_file: Path, poll_s: float,
                 keep_copies: bool = False, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep):
        self.api, self.work, self.stop_file = api, work, stop_file
        self.poll_s, self.keep_copies = poll_s, keep_copies
        self.clock, self.sleep = clock, sleep

    def stop_requested(self) -> bool:
        return self.stop_file.exists()

    def run(self, res: dict, *, save: Callable[[], None], case: dict, case_path: Path | None,
            src: Path, baseline: str, vdir: Path, agent_id: int | None, use_memory: bool,
            budget_s: float, project_id: str, verify: bool = True) -> str:
        """Drive one variant to an outcome. Returns "done" or "stopped". ``res`` is the
        variant's state dict; it is mutated and ``save`` is called after every change."""
        student = vdir / "student-repo"
        if res.get("status") in (None, "pending"):
            if self.stop_requested():
                return "stopped"
            copy_sha = clone_clean(src, baseline, student)
            body = {"instruction": case["instruction"], "source_repo": str(student),
                    "allowed_paths": list(case["allowed_paths"]),
                    "protected_paths": list(case.get("protected_paths") or []),
                    "model": case.get("model"),
                    "timeout_seconds": int(min(7200, max(30, round(budget_s)))),
                    "agent_id": agent_id, "project_id": project_id, "use_memory": bool(use_memory)}
            submitted = self.clock()
            status, rec = self.api.post("/api/coding-tasks", body)
            res.update({"student_copy": str(student), "student_copy_sha": copy_sha,
                        "use_memory": bool(use_memory), "agent_id": agent_id,
                        "budget_seconds": budget_s, "submitted_at": submitted,
                        "deadline_epoch": submitted + budget_s})
            if status != 200 or not isinstance(rec, dict) or not rec.get("id"):
                res.update(status="done", blocked=True, timed_out=False, verifier=None,
                           block_reason=f"submit answered {status}: {json.dumps(rec, ensure_ascii=False)[:400]}")
                return self._finish(res, save, student)
            warnings = []
            if agent_id is not None and "agent_id" not in rec and "profile" not in rec:
                warnings.append("PRODUCT_DID_NOT_ECHO_AGENT: coding task record has no agent_id/profile")
            res.update(status="in_progress", task_id=rec["id"], contract_warnings=warnings)
            save()
        # poll — the deadline is the persisted one; a resume never extends it
        deadline = float(res["deadline_epoch"])
        record: dict = {}
        poll_errors = 0
        while True:
            try:
                status, record = self.api.get(f"/api/coding-tasks/{res['task_id']}")
            except ApiUnavailable as exc:
                status, record = 0, {"error": str(exc)}
                poll_errors += 1
            if status == 200 and isinstance(record, dict) and record.get("status") in TERMINAL:
                break
            if self.clock() >= deadline:
                return self._timeout(res, save, student, vdir, record, poll_errors)
            if self.stop_requested():
                res["last_record_status"] = (record or {}).get("status")
                save()
                return "stopped"
            self.sleep(max(0.0, min(self.poll_s, deadline - self.clock())))
        write_json_atomic(vdir / "task-record.json", record)
        with open(vdir / "student.diff", "w", encoding="utf-8", newline="\n") as fh:
            fh.write(record.get("diff") or "")
        res.update(task_status=record.get("status"), duration_seconds=record.get("duration_seconds"),
                   changed_files=record.get("changed_files") or [],
                   diff_bytes=len(record.get("diff") or ""),
                   sidecar={k: (record.get("sidecar") or {}).get(k) for k in
                            ("status", "summary", "stop_reason", "steps", "recipes_applied", "tests")},
                   memory=record.get("memory"), poll_errors=poll_errors,
                   wall_seconds=round(self.clock() - float(res["submitted_at"]), 2))
        mem = record.get("memory") if isinstance(record.get("memory"), dict) else {}
        if use_memory and "memory" not in record:
            res.setdefault("contract_warnings", []).append(
                "PRODUCT_DID_NOT_REPORT_MEMORY: record has no memory{} although use_memory=true")
        if not use_memory and mem.get("recalled"):
            res.update(blocked=True, block_reason="MEMORY_LEAK_IN_CONTROL: a memory-off variant recalled memory")
        elif use_memory and res.get("snapshot_recipe_ids") is not None:
            leaked = sorted(set(mem.get("recipe_ids") or []) - set(res["snapshot_recipe_ids"]))
            if leaked:
                res.update(blocked=True, block_reason=f"MEMORY_SNAPSHOT_VIOLATED: {leaked}")
        if record.get("status") == "blocked":
            res.update(blocked=True, block_reason=f"product blocked the task: {str(record.get('error'))[:300]}")
        if not res.get("blocked") and not verify:
            res["verifier"] = None
        if not res.get("blocked") and verify:
            res["verifier"] = run_hidden_verifier(case=case, case_path=case_path, src=src,
                                                  baseline=baseline, diff=record.get("diff") or "",
                                                  vdir=vdir / "verifier", student_dir=student)
            if res["verifier"].get("reason") == "VERIFIER_VISIBLE_TO_STUDENT":
                res.update(blocked=True, block_reason="VERIFIER_VISIBLE_TO_STUDENT")
        res.update(status="done", timed_out=False)
        return self._finish(res, save, student)

    def _timeout(self, res, save, student, vdir, record, poll_errors) -> str:
        write_json_atomic(vdir / "task-record-at-timeout.json", record or {})
        try:
            cstatus, _ = self.api.post(f"/api/coding-tasks/{res['task_id']}/cancel", {})
        except ApiUnavailable:
            cstatus = 0
        res.update(status="done", timed_out=True, blocked=False, verifier=None,
                   poll_errors=poll_errors, cancel_status=cstatus,
                   server_task_status_at_timeout=(record or {}).get("status"),
                   wall_seconds=round(self.clock() - float(res["submitted_at"]), 2),
                   timeout_note=("бюджет исчерпан; задача на стороне Bossman могла продолжить "
                                 "работу, если отмена не поддерживается (cancel_status != 200)"))
        return self._finish(res, save, student)

    def _finish(self, res, save, student) -> str:
        res["outcome"] = classify_outcome(blocked=bool(res.get("blocked")), timed_out=bool(res.get("timed_out")),
                                          verifier_passed=bool((res.get("verifier") or {}).get("passed")),
                                          interventions=res.get("interventions"))
        res["finished_at"] = self.clock()
        if not self.keep_copies:
            rmtree(student)              # restore: nothing of this variant survives into the next
            res["student_copy_removed"] = True
        save()
        return "done"


# ============================================================== recipe from a verified diff
def _parse_diff(diff: str) -> list[dict]:
    files: list[dict] = []
    cur: dict | None = None
    hunk: list[str] | None = None
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            cur = {"path": None, "new": False, "deleted": False, "hunks": []}
            files.append(cur)
            hunk = None
            continue
        if cur is None:
            continue
        if line.startswith("new file mode"):
            cur["new"] = True
        elif line.startswith("deleted file mode"):
            cur["deleted"] = True
        elif line.startswith("+++ "):
            target = line[4:].strip()
            if target != "/dev/null":
                cur["path"] = target[2:] if target.startswith("b/") else target
        elif line.startswith("--- "):
            source = line[4:].strip()
            if source != "/dev/null" and cur["path"] is None:
                cur["path"] = source[2:] if source.startswith("a/") else source
        elif line.startswith("@@"):
            hunk = []
            cur["hunks"].append(hunk)
        elif hunk is not None and line[:1] in (" ", "+", "-"):
            hunk.append(line)
        elif hunk is not None and line == "":
            hunk.append(" ")
    return [f for f in files if f["path"]]


def steps_from_diff(diff: str, check_paths: list[str]) -> tuple[list[dict], list[str]]:
    """Executable steps (allowed tools only) reproducing a verified diff, plus notes on
    anything that could not be expressed (deleted files, truncation)."""
    steps: list[dict] = []
    notes: list[str] = []
    for f in _parse_diff(diff):
        path = f["path"]
        if f["deleted"]:
            notes.append(f"deleted file not expressible as a recipe step: {path}")
            continue
        if f["new"]:
            content = "\n".join(l[1:] for h in f["hunks"] for l in h if l.startswith("+"))
            steps.append({"tool": "write_file", "args": {"path": path, "content": content + "\n"}})
            continue
        steps.append({"tool": "read_file", "args": {"path": path}})
        for h in f["hunks"]:
            old = "\n".join(l[1:] for l in h if l[:1] in (" ", "-"))
            new = "\n".join(l[1:] for l in h if l[:1] in (" ", "+"))
            if not old.strip() or old == new:
                notes.append(f"hunk without context skipped in {path}")
                continue
            steps.append({"tool": "edit_file", "args": {"path": path, "old": old, "new": new}})
    budget = MAX_RECIPE_STEPS - 1
    if len(steps) > budget:
        notes.append(f"recipe truncated to {budget} steps (diff had {len(steps)})")
        steps = steps[:budget]
    steps.append({"tool": "run_tests", "args": {"paths": list(check_paths)}})
    return steps, notes


def build_recipe(*, template: dict, diff: str, provenance: dict) -> tuple[dict, list[str]]:
    for key in ("id", "symptom", "cause", "diagnosis", "action", "applies_when", "counterexample",
                "required_check_paths"):
        if not template.get(key):
            raise UsageError(f"lesson template needs {key!r}")
    paths = list(template["required_check_paths"])
    steps, notes = steps_from_diff(diff, paths)
    recipe = {"schema_version": RECIPE_SCHEMA_VERSION, "id": str(template["id"]),
              "title": str(template.get("title") or template["id"]),
              "symptom": template["symptom"], "cause": template["cause"],
              "diagnosis": template["diagnosis"], "action": template["action"],
              "required_check": {"tool": "run_tests", "args": {"paths": paths}},
              "applies_when": dict(template["applies_when"]),
              "counterexample": template["counterexample"], "steps": steps,
              "provenance": provenance, "status": "VERIFIED"}
    if template.get("failed_approaches"):
        recipe["failed_approaches"] = list(template["failed_approaches"])
    return recipe, notes


# ============================================================== reports
def _write_report(out: Path, name: str, report: dict) -> None:
    write_json_atomic(out / f"{name}-report.json", report)
    lines = [f"# Лаборатория самоулучшения — {name}", "",
             f"weights: **{WEIGHTS}** (модель не дообучалась)", ""]
    results = report.get("results") or {}
    if results:
        lines += ["| вариант | исход | статус задачи | верификатор | память | с |",
                  "|---|---|---|---|---|---|"]
        for v, r in results.items():
            ver = (r.get("verifier") or {}).get("reason") or "—"
            mem = r.get("memory") or {}
            lines.append(f"| {v} | **{r.get('outcome')}** | {r.get('task_status', '—')} | {ver} | "
                         f"{'да' if mem.get('recalled') else 'нет'} {mem.get('recipe_ids') or ''} | "
                         f"{r.get('wall_seconds', '—')} |")
    for key in ("summary", "verdict", "memory_hit", "solve", "note"):
        if key in report:
            lines += ["", f"**{key}:** `{json.dumps(report[key], ensure_ascii=False)}`"]
    with open(out / f"{name}-report.md", "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")


def _emit(args, report: dict, human: str) -> None:
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        print(human)


# ============================================================== phases
def _common(args) -> tuple[Api, State, Path, Path, Runner]:
    out = Path(args.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    work = Path(args.work_dir).expanduser().resolve() if args.work_dir else out / "work"
    work.mkdir(parents=True, exist_ok=True)
    state = State(Path(args.state) if args.state else out / "lab-state.json")
    stop = Path(args.stop_file) if args.stop_file else out / "STOP"
    api = Api(args.base_url, resolve_token(args), timeout=args.http_timeout)
    runner = Runner(api, work=work, stop_file=stop, poll_s=args.poll_interval,
                    keep_copies=args.keep_copies)
    return api, state, out, work, runner


def _budget_s(args, case: dict) -> float:
    minutes = args.budget_minutes if args.budget_minutes is not None else case.get("budget_minutes", DEFAULT_BUDGET_MIN)
    return float(minutes) * 60.0


def _require_ready(api: Api) -> None:
    ready = readiness(api)
    if not ready.get("available"):
        raise Blocked(f"coding tasks are not available: {ready.get('reason')}")


def phase_compare(args) -> int:
    api, state, out, work, runner = _common(args)
    case, case_path = load_case(args.case)
    errs = validate_case(case)
    if errs:
        raise UsageError("invalid case: " + "; ".join(errs))
    cmp_ = state.data.get("compare")
    if cmp_ and (cmp_.get("case_id") != case["case_id"]) and not args.fresh:
        raise UsageError(f"state holds compare of {cmp_.get('case_id')}; pass --fresh to start {case['case_id']}")
    if runner.stop_requested():
        raise Stopped(f"STOP file present: {runner.stop_file}")
    src, baseline = resolve_source(case, case_path, work)
    if not cmp_ or args.fresh:
        _require_ready(api)
        variants = [v.strip() for v in (args.variants or ",".join(VARIANTS)).split(",") if v.strip()]
        bad = [v for v in variants if v not in VARIANTS]
        if bad:
            raise UsageError(f"unknown variants {bad}")
        project = str(case.get("project_id") or "lab")
        cmp_ = state.data["compare"] = {
            "case_id": case["case_id"], "run_id": "cmp-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()),
            "started_at": _now_iso(), "baseline_sha": baseline, "source_repo": str(src),
            "project_id": project, "budget_seconds": _budget_s(args, case), "variants": variants,
            "agents": ensure_agents(api), "boot": boot_identity(api),
            # MEMORY sees only what existed BEFORE the comparison
            "snapshot_recipe_ids": recipe_ids(api, project),
            "interventions": [], "results": {}}
        missing = [v for v in variants if v not in cmp_["agents"]]
        if missing:
            raise Blocked(f"lab agents missing for {missing}")
        state.save()
    elif baseline != cmp_["baseline_sha"]:
        raise UsageError(f"baseline moved since the comparison started ({cmp_['baseline_sha']} -> {baseline})")
    if args.budget_minutes is not None and abs(args.budget_minutes * 60 - cmp_["budget_seconds"]) > 1e-6:
        log(f"--budget-minutes ignored on resume: the comparison runs with {cmp_['budget_seconds']} s")
    for variant in cmp_["variants"]:
        res = cmp_["results"].setdefault(variant, {"status": "pending"})
        if res.get("status") == "done":
            log(f"{variant}: already done ({res.get('outcome')}), skipped")
            continue
        res["interventions"] = [i for i in cmp_["interventions"] if i.get("variant") == variant]
        use_mem = variant == MEMORY_VARIANT
        if use_mem:
            res["snapshot_recipe_ids"] = list(cmp_["snapshot_recipe_ids"])
        log(f"{variant}: run (budget {cmp_['budget_seconds']:.0f} s, memory {'on' if use_mem else 'off'})")
        agent = cmp_["agents"][variant]
        outcome = runner.run(res, save=state.save, case=case, case_path=case_path, src=src,
                             baseline=cmp_["baseline_sha"], vdir=work / cmp_["run_id"] / variant,
                             agent_id=agent["id"], use_memory=use_mem,
                             budget_s=cmp_["budget_seconds"], project_id=cmp_["project_id"])
        if outcome == "stopped":
            state.save()
            raise Stopped(f"STOP file honoured during {variant}; rerun to resume")
        log(f"{variant}: {res['outcome']}")
    cmp_["finished_at"] = _now_iso()
    cmp_["summary"] = summarize(cmp_["results"])
    state.save()
    report = {"phase": "compare", "case_id": case["case_id"], "run_id": cmp_["run_id"],
              "baseline_sha": cmp_["baseline_sha"], "results": cmp_["results"],
              "summary": cmp_["summary"], "weights": WEIGHTS}
    _write_report(out, "compare", report)
    human = "\n".join(f"{v}: {r['outcome']}" for v, r in cmp_["results"].items())
    _emit(args, report, f"{human}\nотчёт: {out / 'compare-report.md'}\n{WEIGHTS}")
    return EXIT_OK


def phase_intervene(args) -> int:
    out = Path(args.out).expanduser().resolve()
    state = State(Path(args.state) if args.state else out / "lab-state.json")
    cmp_ = state.data.get("compare")
    if not cmp_:
        raise UsageError("no comparison in the state file; interventions belong to a compare run")
    if args.variant not in cmp_["variants"]:
        raise UsageError(f"variant {args.variant} is not part of this comparison")
    entry = {"at": _now_iso(), "variant": args.variant, "kind": args.kind, "by": args.by,
             "note": args.note}
    cmp_["interventions"].append(entry)
    res = cmp_["results"].get(args.variant)
    if res is not None:
        res.setdefault("interventions", []).append(entry)
        if res.get("status") == "done":
            res["outcome"] = classify_outcome(blocked=bool(res.get("blocked")),
                                              timed_out=bool(res.get("timed_out")),
                                              verifier_passed=bool((res.get("verifier") or {}).get("passed")),
                                              interventions=res["interventions"])
            cmp_["summary"] = summarize(cmp_["results"])
    with open(out / "interventions.jsonl", "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    state.save()
    _emit(args, {"phase": "intervene", "recorded": entry,
                 "outcome": (res or {}).get("outcome")}, f"записано: {entry}")
    return EXIT_OK


def phase_lesson(args) -> int:
    api, state, out, work, _runner = _common(args)
    cmp_ = state.data.get("compare")
    if not cmp_ or not cmp_.get("summary"):
        raise UsageError("no finished comparison in the state file (run compare first)")
    case, case_path = load_case(args.case)
    if case["case_id"] != cmp_["case_id"]:
        raise UsageError(f"--case {case['case_id']} is not the compared case {cmp_['case_id']}")
    template = dict(case.get("lesson_template") or {})
    if args.notes:
        template.update(json.loads(Path(args.notes).read_text(encoding="utf-8")))
    order = [STUDENT_UNASSISTED_PASS, STUDENT_COACHED_PASS] + ([TEACHER_PATCH] if args.allow_teacher_patch else [])
    pool = [(v, r) for v, r in cmp_["results"].items() if r.get("outcome") in order and
            (not args.variant or v == args.variant)]
    pool.sort(key=lambda vr: (order.index(vr[1]["outcome"]), cmp_["variants"].index(vr[0])))
    if not pool:
        report = {"phase": "lesson", "status": "NO_VERIFIED_PASS", "weights": WEIGHTS,
                  "outcomes": cmp_["summary"]["outcomes"]}
        state.data["lesson"] = report
        state.save()
        _write_report(out, "lesson", report)
        _emit(args, report, "нет проверенного прохода — урок не записан (NO_VERIFIED_PASS)")
        return EXIT_NO_PASS
    variant, res = pool[0]
    diff_path = work / cmp_["run_id"] / variant / "student.diff"
    diff = diff_path.read_text(encoding="utf-8") if diff_path.is_file() else ""
    agent = cmp_["agents"].get(variant) or {}
    level = {STUDENT_UNASSISTED_PASS: "none", STUDENT_COACHED_PASS: "hint",
             TEACHER_PATCH: "teacher_patch"}[res["outcome"]]
    provenance = {"who": f"student:{agent.get('name') or variant}",
                  "source": "teacher" if res["outcome"] == TEACHER_PATCH else "student",
                  "assistance_level": level, "run_id": str(res.get("task_id") or ""),
                  "what": f"{res['outcome']} on {cmp_['case_id']} ({variant})",
                  "case_id": cmp_["case_id"], "variant": variant, "outcome": res["outcome"],
                  "evidence_refs": [f"lab:{cmp_['run_id']}:{variant}"]}
    if case.get("model"):
        provenance["model"] = str(case["model"])
    recipe, notes = build_recipe(template=template, diff=diff, provenance=provenance)
    ver = res.get("verifier") or {}
    evidence = {"source": f"hidden_verifier:{cmp_['case_id']}:{cmp_['run_id']}:{variant}",
                "expected": "exit 0", "actual": f"exit {ver.get('exit_code')}",
                "head_sha": cmp_["baseline_sha"], "environment": f"{platform.system().lower()}-lab"}
    verifier = {"principal_id": VERIFIER_PRINCIPAL, "independence_class": "external_tool",
                "model_id": "", "run_id": f"verifier-{cmp_['run_id']}-{variant}"}
    status, body = api.post("/api/coding-recipes", {"recipe": recipe, "evidence": evidence,
                                                    "verifier": verifier,
                                                    "project_id": cmp_["project_id"]})
    if status != 200:
        raise Blocked(f"Bossman refused the recipe ({status}): {json.dumps(body, ensure_ascii=False)[:600]}")
    report = {"phase": "lesson", "status": "RECIPE_VERIFIED", "variant": variant,
              "outcome": res["outcome"], "saved": body, "recipe_id": recipe["id"],
              "recipe_notes": notes, "boot": boot_identity(api), "weights": WEIGHTS,
              "project_id": cmp_["project_id"]}
    state.data["lesson"] = report
    state.save()
    write_json_atomic(out / "lesson-recipe.json", recipe)
    _write_report(out, "lesson", report)
    _emit(args, report, f"рецепт {recipe['id']} записан в Bossman ({body.get('lesson_id')}) "
                        f"из {variant}/{res['outcome']}; теперь перезапустите Bossman и запустите transfer")
    return EXIT_OK


def phase_transfer(args) -> int:
    api, state, out, work, runner = _common(args)
    case, case_path = load_case(args.case)
    errs = validate_case(case)
    if errs:
        raise UsageError("invalid case: " + "; ".join(errs))
    lesson = state.data.get("lesson") or {}
    if lesson.get("status") != "RECIPE_VERIFIED" and not args.allow_no_lesson:
        raise UsageError("no verified recipe in the state file (run lesson first, or --allow-no-lesson)")
    cmp_case = (state.data.get("compare") or {}).get("case_id")
    if cmp_case and cmp_case == case["case_id"]:
        raise UsageError("transfer needs a NEW analogous case, not the compared one")
    tr = state.data.get("transfer")
    if tr and tr.get("case_id") != case["case_id"] and not args.fresh:
        raise UsageError(f"state holds transfer of {tr.get('case_id')}; pass --fresh")
    if runner.stop_requested():
        raise Stopped(f"STOP file present: {runner.stop_file}")
    src, baseline = resolve_source(case, case_path, work)
    if not tr or args.fresh:
        now = boot_identity(api)
        before = (lesson.get("boot") or {}).get("started_at")
        restarted = bool(before) and now.get("started_at") != before
        if not restarted and not args.skip_restart_check:
            raise Blocked(f"RESTART_NOT_DETECTED: Bossman started_at {now.get('started_at')} is the same "
                          "as when the lesson was written; restart Bossman fully, then rerun transfer")
        _require_ready(api)
        agents = ensure_agents(api)
        if MEMORY_VARIANT not in agents:
            raise Blocked("the MEMORY lab agent is missing")
        project = str(case.get("project_id") or lesson.get("project_id") or "lab")
        arms = ["CONTROL", "MEMORY"] if not args.no_control else ["MEMORY"]
        status, probe = api.post("/api/coding-recipes/match",
                                 {"instruction": case["instruction"], "project_id": project})
        tr = state.data["transfer"] = {
            "case_id": case["case_id"], "run_id": "tr-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()),
            "started_at": _now_iso(), "baseline_sha": baseline, "source_repo": str(src),
            "project_id": project, "budget_seconds": _budget_s(args, case), "arms": arms,
            "agent": agents[MEMORY_VARIANT], "boot_before": lesson.get("boot"), "boot_now": now,
            "restart_verified": restarted, "restart_check_skipped": bool(args.skip_restart_check and not restarted),
            "expected_recipe_id": lesson.get("recipe_id"),
            "retrieval_probe": {"status": status,
                                "recipe_ids": [r.get("id") for r in (probe or {}).get("items") or []]
                                if isinstance(probe, dict) else []},
            "coaching": "NONE (interventions are not accepted for transfer)", "results": {}}
        state.save()
    for arm in tr["arms"]:
        res = tr["results"].setdefault(arm, {"status": "pending"})
        if res.get("status") == "done":
            continue
        use_mem = arm == "MEMORY"
        log(f"transfer {arm}: run (memory {'on' if use_mem else 'off'})")
        outcome = runner.run(res, save=state.save, case=case, case_path=case_path, src=src,
                             baseline=tr["baseline_sha"], vdir=work / tr["run_id"] / arm,
                             agent_id=tr["agent"]["id"], use_memory=use_mem,
                             budget_s=tr["budget_seconds"], project_id=tr["project_id"])
        if outcome == "stopped":
            raise Stopped(f"STOP file honoured during transfer {arm}; rerun to resume")
    mem = tr["results"].get("MEMORY") or {}
    mem_info = mem.get("memory") if isinstance(mem.get("memory"), dict) else {}
    expected = tr.get("expected_recipe_id")
    ids = list(mem_info.get("recipe_ids") or [])
    tr["memory_hit"] = {"recalled": bool(mem_info.get("recalled")), "recipe_ids": ids,
                        "expected_recipe_id": expected,
                        "expected_recipe_hit": bool(expected) and expected in ids,
                        "retrieval_probe_hit": bool(expected) and expected in tr["retrieval_probe"]["recipe_ids"]}
    tr["solve"] = {arm: {"outcome": r.get("outcome"), "student_pass": is_student_pass(r.get("outcome"))}
                   for arm, r in tr["results"].items()}
    tr["verdict"] = gain_verdict((tr["results"].get("CONTROL") or {}).get("outcome"), mem.get("outcome"))
    tr["note"] = ("n=1: наблюдение, не доля; memory_hit (рецепт найден) и solve (скрытый "
                  "верификатор прошёл) — разные величины и не складываются")
    tr["finished_at"] = _now_iso()
    tr["weights"] = WEIGHTS
    state.save()
    report = {"phase": "transfer", **{k: tr[k] for k in ("case_id", "run_id", "restart_verified",
                                                         "restart_check_skipped", "memory_hit", "solve",
                                                         "verdict", "note", "results")},
              "weights": WEIGHTS}
    _write_report(out, "transfer", report)
    _emit(args, report, f"transfer: {tr['verdict']}; memory_hit={tr['memory_hit']['expected_recipe_hit']}; "
                        f"solve={json.dumps(tr['solve'], ensure_ascii=False)}; {WEIGHTS}")
    return EXIT_OK


def phase_explore(args) -> int:
    api, state, out, work, runner = _common(args)
    if runner.stop_requested():
        raise Stopped(f"STOP file present: {runner.stop_file}")
    repo = Path(args.repo).expanduser().resolve()
    if not (repo / ".git").exists():
        raise UsageError(f"--repo is not a git repository: {repo}")
    ex = state.data.get("explore")
    if not ex or args.fresh:
        _require_ready(api)
        agents = ensure_agents(api)
        if args.agent not in agents:
            raise UsageError(f"--agent must be one of {sorted(agents)}")
        ex = state.data["explore"] = {
            "run_id": "ex-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()), "started_at": _now_iso(),
            "agent": {"variant": args.agent, **agents[args.agent]}, "repo": str(repo),
            "baseline_sha": resolve_commit(repo, args.baseline),
            "budget_seconds": float(args.budget_minutes or DEFAULT_BUDGET_MIN) * 60.0,
            "instruction": EXPLORE_INSTRUCTION, "result": {"status": "pending"}}
        state.save()
    case = {"instruction": ex["instruction"], "allowed_paths": list(args.allowed),
            "protected_paths": list(args.protected), "model": args.model}
    res = ex["result"]
    # explore has no hidden verifier: the run is reported, never scored as a pass
    outcome = runner.run(res, save=state.save, case=case, case_path=None, src=repo,
                         baseline=ex["baseline_sha"], vdir=work / ex["run_id"] / "explore",
                         agent_id=ex["agent"]["id"], use_memory=ex["agent"]["variant"] == MEMORY_VARIANT,
                         budget_s=ex["budget_seconds"], project_id=args.project_id, verify=False)
    if outcome == "stopped":
        raise Stopped("STOP file honoured during explore; rerun to resume")
    if res.get("outcome") not in (TIMEOUT, BLOCKED):
        res["outcome"] = None
    res["verification"] = "NOT_VERIFIED: explore has no hidden verifier; the diff is evidence for review"
    res.pop("verifier", None)
    state.save()
    report = {"phase": "explore", "run_id": ex["run_id"], "agent": ex["agent"], "result": res,
              "weights": WEIGHTS}
    _write_report(out, "explore", report)
    _emit(args, report, f"explore: task {res.get('task_status') or res.get('outcome')}, "
                        f"{len(res.get('changed_files') or [])} файлов, {res['verification']}")
    return EXIT_OK


def phase_status(args) -> int:
    out = Path(args.out).expanduser().resolve()
    state = State(Path(args.state) if args.state else out / "lab-state.json")
    print(json.dumps(state.data, ensure_ascii=False, indent=1))
    return EXIT_OK


def phase_make_sample(args) -> int:
    dest = Path(args.dest).expanduser().resolve()
    made = {name: make_synthetic_repo(name, dest / name) for name in SYNTHETIC_REPOS}
    _emit(args, {"phase": "make-sample", "repos": made}, "\n".join(f"{k}: {v}" for k, v in made.items()))
    return EXIT_OK


# ============================================================== CLI
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="self_improve_lab.py", description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--base-url", default=os.environ.get("BCC_BASE_URL", DEFAULT_BASE_URL))
    common.add_argument("--token")
    common.add_argument("--token-file")
    common.add_argument("--data-dir", help="Bossman data dir (token is read from <data-dir>/token)")
    common.add_argument("--out", default="self-improve-lab", help="reports + default state/STOP location")
    common.add_argument("--work-dir", help="clean copies live here; must be inside Bossman's allowed code roots")
    common.add_argument("--state")
    common.add_argument("--stop-file")
    common.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_S)
    common.add_argument("--http-timeout", type=float, default=30.0)
    common.add_argument("--budget-minutes", type=float, default=None)
    common.add_argument("--keep-copies", action="store_true")
    common.add_argument("--fresh", action="store_true", help="start the phase over instead of resuming")
    common.add_argument("--json", action="store_true")
    sub = p.add_subparsers(dest="phase", required=True)

    s = sub.add_parser("explore", parents=[common])
    s.add_argument("--agent", default="TOOL_FIRST", choices=VARIANTS)
    s.add_argument("--repo", required=True)
    s.add_argument("--baseline", default="HEAD")
    s.add_argument("--allowed", action="append",
                   default=None, help="repeatable; default: command-center learning bossman-core tools tests docs")
    s.add_argument("--protected", action="append", default=None)
    s.add_argument("--model")
    s.add_argument("--project-id", default="bossman")

    s = sub.add_parser("compare", parents=[common])
    s.add_argument("--case", required=True, help="case JSON path, or built-in: sample")
    s.add_argument("--variants", help="comma list; default all six")

    s = sub.add_parser("lesson", parents=[common])
    s.add_argument("--case", required=True)
    s.add_argument("--variant")
    s.add_argument("--notes", help="JSON overriding the case's lesson_template fields")
    s.add_argument("--allow-teacher-patch", action="store_true")

    s = sub.add_parser("transfer", parents=[common])
    s.add_argument("--case", required=True, help="a NEW analogous case, or built-in: sample-transfer")
    s.add_argument("--no-control", action="store_true")
    s.add_argument("--skip-restart-check", action="store_true",
                   help="recorded in the report as restart_check_skipped")
    s.add_argument("--allow-no-lesson", action="store_true")

    s = sub.add_parser("intervene", parents=[common])
    s.add_argument("--variant", required=True, choices=VARIANTS)
    s.add_argument("--kind", required=True, choices=INTERVENTION_KINDS)
    s.add_argument("--note", required=True)
    s.add_argument("--by", default="teacher")

    sub.add_parser("status", parents=[common])
    s = sub.add_parser("make-sample", parents=[common])
    s.add_argument("--dest", required=True)
    return p


PHASES = {"explore": phase_explore, "compare": phase_compare, "lesson": phase_lesson,
          "transfer": phase_transfer, "intervene": phase_intervene, "status": phase_status,
          "make-sample": phase_make_sample}


def main(argv: list[str] | None = None) -> int:
    utf8_console()
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return EXIT_USAGE if exc.code else EXIT_OK
    if args.phase == "explore":
        args.allowed = args.allowed or ["command-center", "learning", "bossman-core", "tools", "tests", "docs"]
        args.protected = args.protected or [".github", "tools/windows_bundle_lock.json"]
    try:
        return PHASES[args.phase](args)
    except LabError as exc:
        payload = {"phase": args.phase, "error": type(exc).__name__, "message": str(exc),
                   "exit_code": exc.exit_code, "weights": WEIGHTS}
        if getattr(args, "json", False):
            print(json.dumps(payload, ensure_ascii=False, indent=1))
        print(f"[lab] {type(exc).__name__}: {exc}", file=sys.stderr)
        return exc.exit_code


if __name__ == "__main__":
    sys.exit(main())
