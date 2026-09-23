"""RESULT_VERIFIER: an independent verdict on one candidate patch.

Input is the ORIGINAL task, the baseline SHA, the diff, the test list and the
evidence record. The executor's explanation (summary, notes, "tests passed")
is never an input: keys that carry it are dropped before anything is read.

What happens, every subprocess through ``proc_tree.run_tree`` with a timeout:

  1. a fresh clean checkout of the baseline without a remote; the diff must
     apply there exactly (``git apply --index``), or the verdict is FAIL;
  2. static review of what the diff touches, BEFORE any of its code runs:
     evaluator / holdout / config / CI / conftest / interpreter hooks, symlinks,
     submodules, binary or secret-like content, and any path outside the
     task's editable scope -> UNSAFE (the patch is never executed);
     an existing test deleted, renamed or with a removed line (assert removed,
     expected value changed), a task test touched, skip/xfail/exit added to a
     test, an existing test name shadowed -> INVALID_TEST;
  3. the fixed tests run on the untouched baseline and on the candidate; the
     candidate must keep the whole test inventory (nothing disappears or turns
     into a skip), must not break a test that passed, and must pass the tests
     the task names;
  4. negative control: a third checkout gets ONLY the test part of the patch
     (the non-test part reverted); the new regression test must FAIL there,
     which proves it tests the change and not nothing;
  5. a result from the DETERMINISTIC TEST MODEL / MOCK_MODEL is labelled and can
     never count as a student success, whatever the verdict.

Verdicts: PASS, FAIL, PARTIAL (measured improvement without regressions, but
not every task test fixed or no new regression test), INVALID_TEST, UNSAFE.

Test isolation is the coding path's own: the sidecar guard (minimal env,
private HOME/TEMP, audit-hook refusing reads/writes outside the checkout and
non-loopback network) for the host runner, or the offline read-only Docker
executor. The guard is NOT an OS sandbox; see docs/evolution/EVOLUTION_LOOP.md.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET

from bossman.apprentice.proc_tree import run_tree
from learning.trace import has_secret, redact_obj, redact_text

VERDICTS = ("PASS", "FAIL", "PARTIAL", "INVALID_TEST", "UNSAFE")
MOCK_MARKER = "DETERMINISTIC-TEST-MODEL"
PRINCIPAL = "tool:bossman-evolution-result-verifier"
#: Evidence keys that carry the executor's own story; the verifier never reads them.
EXPLANATION_KEYS = frozenset({"summary", "explanation", "notes", "claim", "claims", "message",
                              "student_summary", "reasoning", "tests_passed", "report"})
GIT_TIMEOUT = 120
DEFAULT_TEST_TIMEOUT = 600
MAX_DIFF_BYTES = 2_000_000
_SKIP_RX = re.compile(r"(?:pytest\.(?:skip|xfail|importorskip|exit)\b|mark\.(?:skip|skipif|xfail)\b|"
                      r"unittest\.(?:skip|skipIf|skipUnless|expectedFailure)\b|\bskipTest\s*\(|"
                      r"\braise\s+(?:unittest\.)?SkipTest\b|\bos\._exit\s*\(|\bsys\.exit\s*\(|"
                      r"@(?:skip|skipIf|skipUnless|expectedFailure)\b)")
_CONFIG_NAMES = frozenset({"conftest.py", "pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml",
                           ".coveragerc", "noxfile.py", "sitecustomize.py", "usercustomize.py",
                           ".gitattributes", ".gitmodules", "setup.py"})


class VerifierError(RuntimeError):
    """The verifier itself could not run (environment), not a verdict on the patch."""


# ---------------------------------------------------------------- processes
def _git_env(home: Path) -> dict:
    keep = ("PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "TMPDIR", "LANG")
    env = {k: os.environ[k] for k in keep if os.environ.get(k)}
    home.mkdir(parents=True, exist_ok=True)
    env.update(HOME=str(home), USERPROFILE=str(home), XDG_CONFIG_HOME=str(home),
               GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    return env


def git(cwd: Path, *args: str, home: Path, timeout: float = GIT_TIMEOUT, input_text: str | None = None,
        check: bool = True, extra_env: dict | None = None) -> tuple[int, str, str]:
    """One bounded git call; the whole process tree dies on timeout."""
    env = _git_env(home)
    env.update(extra_env or {})
    argv = ["git", "-c", "core.autocrlf=false", "-c", "core.fsmonitor=false",
            "-c", "user.name=Bossman Evolution", "-c", "user.email=bossman-evolution@localhost", *args]
    kw = {} if input_text is not None else {"stdin": subprocess.DEVNULL}
    res = run_tree(argv, cwd=str(cwd), env=env, input=input_text, text=True, encoding="utf-8",
                   errors="replace", stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, **kw)
    if res.timed_out:
        raise VerifierError(f"git {args[0]} timed out after {timeout}s")
    if check and res.returncode:
        raise VerifierError(f"git {args[0]} failed: " + redact_text((res.stderr or res.stdout or "")[-1500:]))
    return res.returncode or 0, res.stdout or "", res.stderr or ""


def clean_checkout(source: Path, sha: str, dest: Path, *, home: Path) -> str:
    """A fresh checkout of ``sha`` without any remote; returns HEAD (must equal sha)."""
    if dest.exists():
        rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # --shared: objects are read through alternates (the source is never written);
    # the checkout itself is a new, separate working tree and index.
    git(dest.parent, "clone", "--quiet", "--shared", "--no-checkout", str(source), str(dest), home=home,
        timeout=600)
    git(dest, "checkout", "--quiet", "--detach", sha, home=home, timeout=600)
    git(dest, "remote", "remove", "origin", home=home, check=False)
    head = git(dest, "rev-parse", "HEAD", home=home)[1].strip()
    remotes = git(dest, "remote", home=home)[1].strip()
    status = git(dest, "status", "--porcelain", "--untracked-files=all", home=home)[1].strip()
    if head != sha or remotes or status:
        raise VerifierError("baseline checkout is not clean, not at the baseline or has a remote")
    return head


def _on_rm_error(func, path, _exc):
    try:
        os.chmod(path, 0o700)
        func(path)
    except OSError:
        pass


def rmtree(path: Path) -> bool:
    """Remove a checkout; on Windows read-only pack files need a chmod first."""
    if not path.exists():
        return True
    for _ in range(3):
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=_on_rm_error)
        else:  # pragma: no cover — 3.11
            shutil.rmtree(path, onerror=_on_rm_error)
        if not path.exists():
            return True
        time.sleep(0.5)
    return not path.exists()


# ---------------------------------------------------------------- test runners
# The Windows archive runs an EMBEDDABLE Python: its ._pth file makes the
# interpreter ignore PYTHONPATH (and every PYTHON* variable) and keeps the cwd
# off sys.path. So a test process never relies on them: the command line itself
# puts the checkout (and any extra source roots) on sys.path, then runs the
# runner module. With ``guard`` it is the coding sidecar's own bootstrap (loads
# the audit-hook guard, inserts the workspace) — one implementation, reused.
_PATHS_PRELUDE = r'''
import os as _bo, sys as _bs
for _bp in reversed([p for p in _bo.environ.get("BOSSMAN_EVOLUTION_PATHS", "").split(_bo.pathsep) if p]):
    if _bp not in _bs.path:
        _bs.path.insert(0, _bp)
'''
_PLAIN_BOOT = r'''
import runpy, sys
_mod = sys.argv[1]
sys.argv = [_mod] + sys.argv[2:]
runpy.run_module(_mod, run_name="__main__", alter_sys=True)
'''


def module_command(module: str, *args: str, guard: bool, interpreter: list[str] | None = None) -> list[str]:
    """``python -m module args`` that works under the embeddable runtime too.

    ``BOSSMAN_EVOLUTION_PATHS`` (read by our own bootstrap, not by the
    interpreter) lists the source roots to prepend. ``interpreter`` defaults to
    the sidecar's hook ``local_sidecar._interpreter()``."""
    from bossman.apprentice import local_sidecar as ls  # noqa: PLC0415
    interp = list(interpreter) if interpreter else ls._interpreter()  # noqa: SLF001
    boot = _PATHS_PRELUDE + (ls._BOOT if guard else _PLAIN_BOOT)  # noqa: SLF001
    return [*interp, "-B", "-s", "-X", "utf8", "-c", boot, module, *args]


def parse_junit(path: Path) -> dict[str, str] | None:
    """{test id: PASS|FAIL|ERROR|SKIP}, or None when no usable report exists."""
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 5_000_000:
        return None
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return None
    out: dict[str, str] = {}
    for case in root.iter("testcase"):
        # A collection error has no class: its name is the module ("tests.test_x").
        classname, name = case.get("classname", ""), case.get("name", "")
        key = f"{classname}::{name}" if classname else name
        if case.find("skipped") is not None:
            status = "SKIP"
        elif case.find("error") is not None:
            status = "ERROR"
        elif case.find("failure") is not None:
            status = "FAIL"
        else:
            status = "PASS"
        if key in out:          # a duplicate id cannot be compared honestly
            out[key] = "ERROR"
        else:
            out[key] = status
    return out


_UNITTEST_LINE = re.compile(r"^([\w.]+) \(([\w.]+)\)(?:\n[^\n]*?)? \.\.\. (ok|FAIL|ERROR|skipped[^\n]*|"
                            r"expected failure|unexpected success)$", re.M)


def parse_unittest(text: str) -> dict[str, str]:
    """``python -m unittest -v`` lines -> {dotted test id: status}. An import failure
    (``unittest.loader._FailedTest``) is keyed by the module it could not load."""
    out: dict[str, str] = {}
    for name, where, result in _UNITTEST_LINE.findall(text):
        if "unittest.loader._FailedTest" in where:
            key = name
        else:
            key = where if where.endswith("." + name) else f"{where}.{name}"
        status = {"ok": "PASS", "FAIL": "FAIL", "ERROR": "ERROR"}.get(result)
        out[key] = status or ("SKIP" if result.startswith("skipped") else "FAIL")
    return out


def belongs(test_id: str, test_file: str) -> bool:
    """Does a junit/unittest id come from ``test_file`` (``tests/test_x.py``)?"""
    stem = str(PurePosixPath(test_file).with_suffix(""))
    head = test_id.split("::", 1)[0]
    dotted = head.replace(".", "/")
    return head == test_file or dotted == stem or dotted.startswith(stem + "/")


class GuardedHostRunner:
    """The coding path's test isolation: the local sidecar's guard env (minimal
    environment, private HOME/TEMP, audit hook) around pytest or unittest."""

    kind = "host-guarded"

    def __init__(self, *, python: str | None = None, python_paths: tuple[str, ...] = (".",),
                 runner: str = "auto", timeout: float = DEFAULT_TEST_TIMEOUT, on_process=None):
        self.python = python          # None = the sidecar's interpreter hook (the archive runtime)
        self.python_paths = tuple(python_paths) or (".",)
        self.runner = runner
        self.timeout = timeout
        self.on_process = on_process
        self._has_pytest: bool | None = None

    def interpreter(self) -> list[str]:
        from bossman.apprentice import local_sidecar as ls  # noqa: PLC0415
        return [self.python] if self.python else ls._interpreter()  # noqa: SLF001

    def has_pytest(self) -> bool:
        if self._has_pytest is None:
            res = run_tree([*self.interpreter(), "-s", "-c", "import pytest"], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
            self._has_pytest = (not res.timed_out) and res.returncode == 0
        return self._has_pytest

    def identity(self) -> dict:
        return {"kind": self.kind, "python": " ".join(self.interpreter()), "runner": self.effective_runner()}

    def effective_runner(self) -> str:
        if self.runner == "auto":
            return "pytest" if self.has_pytest() else "unittest"
        return self.runner

    def run(self, checkout: Path, tests: list[str], out: Path, *, deadline: float | None = None) -> dict:
        from bossman.apprentice import local_sidecar as ls  # noqa: PLC0415 — the one guard implementation
        out.mkdir(parents=True, exist_ok=True)
        scratch = out / "scratch"
        env = ls._scratch_env(scratch, checkout)  # noqa: SLF001 — the sidecar's own guard, reused
        roots = [str((checkout / p).resolve()) for p in self.python_paths]
        env["BOSSMAN_EVOLUTION_PATHS"] = os.pathsep.join(roots)
        # A regular (non-embedded) child interpreter still honours PYTHONPATH.
        env["PYTHONPATH"] = os.pathsep.join([str(scratch / "guard"), *roots])
        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        env["LOCAL_ONLY"] = "1"
        runner = self.effective_runner()
        junit = scratch / "junit.xml"
        if runner == "pytest":
            argv = module_command("pytest", "-q", "--tb=short", "-p", "no:cacheprovider", "-o", "addopts=",
                                  "--rootdir", str(checkout), "--junitxml", str(junit), *tests,
                                  guard=True, interpreter=self.interpreter())
        else:
            argv = module_command("unittest", "-v", *[t[:-3].replace("/", ".") if t.endswith(".py") else t
                                                      for t in tests], guard=True, interpreter=self.interpreter())
        timeout = self.timeout if deadline is None else max(5.0, min(self.timeout, deadline - time.monotonic()))
        started = time.monotonic()
        res = run_tree(argv, cwd=str(checkout), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, timeout=timeout, on_start=self.on_process)
        text = (res.stdout or b"").decode("utf-8", "replace")
        (out / "output.txt").write_text(redact_text(text[-20000:]), encoding="utf-8")
        record = {"runner": runner, "exit_code": res.returncode, "timed_out": res.timed_out,
                  "seconds": round(time.monotonic() - started, 2), "tests": {}, "status": "BLOCKED"}
        if res.timed_out:
            record["reason"] = "TIMEOUT"
            return record
        results = parse_junit(junit) if runner == "pytest" else parse_unittest(text)
        if runner == "pytest" and junit.is_file():
            shutil.copyfile(junit, out / "junit.xml")
        # pytest: 0 ok, 1 failures, 2 interrupted/collection errors; anything else is not a result.
        if not results or (runner == "pytest" and res.returncode not in (0, 1, 2)):
            record["reason"] = "NO_TEST_RESULTS"
            return record
        record["tests"] = results
        record["status"] = "PASS" if all(v == "PASS" for v in results.values()) else "FAIL"
        return record


class DockerRunner:
    """Aster's executor for model-written edits: offline, read-only, pinned image."""

    kind = "docker"

    def __init__(self, image: str, *, timeout: float = DEFAULT_TEST_TIMEOUT, on_process=None):
        self.image, self.timeout, self.on_process = image, timeout, on_process

    def identity(self) -> dict:
        return {"kind": self.kind, "image": self.image, "runner": "pytest"}

    def run(self, checkout: Path, tests: list[str], out: Path, *, deadline: float | None = None) -> dict:
        from . import runner as r  # noqa: PLC0415
        out.mkdir(parents=True, exist_ok=True)
        with_out = out / "container"
        with_out.mkdir(exist_ok=True)
        with_out.chmod(0o777)  # only this empty evidence directory is writable by the container uid
        name = "bossman-evo-" + uuid.uuid4().hex[:16]
        argv = r.docker_test_command(checkout, with_out, tests, self.image, name)
        timeout = self.timeout if deadline is None else max(5.0, min(self.timeout, deadline - time.monotonic()))
        try:
            res = run_tree(argv, cwd=str(checkout), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=timeout, on_start=self.on_process)
        finally:
            run_tree(["docker", "rm", "-f", name], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, timeout=30)
        text = (res.stdout or b"").decode("utf-8", "replace")
        (out / "output.txt").write_text(redact_text(text[-20000:]), encoding="utf-8")
        record = {"runner": "pytest", "exit_code": res.returncode, "timed_out": res.timed_out,
                  "tests": {}, "status": "BLOCKED"}
        results = None if res.timed_out else parse_junit(with_out / "junit.xml")
        if not results or res.returncode not in (0, 1, 2):
            record["reason"] = "TIMEOUT" if res.timed_out else "NO_TEST_RESULTS"
            return record
        record["tests"] = results
        record["status"] = "PASS" if all(v == "PASS" for v in results.values()) else "FAIL"
        return record


# ---------------------------------------------------------------- classification
def is_test_path(path: str) -> bool:
    p = PurePosixPath(path)
    return (p.name.startswith("test_") or p.name.endswith("_test.py")
            or any(part in {"tests", "test"} for part in p.parts[:-1]))


def protected_reason(path: str, holdout: tuple[str, ...] = ()) -> str:
    """Why ``path`` is outside what any candidate may touch ('' = not protected)."""
    p = PurePosixPath(path)
    name = p.name
    low = path.lower()
    if any(path == h or path.startswith(h.rstrip("/") + "/") for h in holdout):
        return "holdout / evaluator file"
    if name in _CONFIG_NAMES:
        return f"test/evaluator configuration ({name})"
    if low.startswith(".github/") or low.startswith(".git/"):
        return "CI / repository configuration"
    if "config/evolution/" in low or "/self_improvement/" in f"/{low}" or "learning_guard" in low:
        return "evaluator / learning authority"
    if low.startswith("learning/") or low.startswith("schemas/"):
        return "memory authority / shared schema"
    return ""


def _in_prefix(path: str, prefixes) -> bool:
    return any(path == p.rstrip("/") or path.startswith(p.rstrip("/") + "/") for p in prefixes if p)


def new_test_prefixes(task: dict) -> tuple[str, ...]:
    explicit = task.get("new_tests")
    if explicit:
        return tuple(str(p).rstrip("/") for p in explicit)
    parents = {str(PurePosixPath(t).parent) for t in task.get("tests") or []}
    return tuple(sorted(p for p in parents if p and p != "."))


def _hunks(diff_text: str) -> tuple[list[str], list[str]]:
    removed, added = [], []
    for line in diff_text.splitlines():
        if line.startswith(("+++", "---")):
            continue
        if line.startswith("-"):
            removed.append(line[1:])
        elif line.startswith("+"):
            added.append(line[1:])
    return removed, added


def _describe_removal(removed: list[str]) -> str:
    joined = "\n".join(removed)
    if re.search(r"\bassert\w*\b|self\.assert|\bfail\(", joined):
        return "assert removed or changed"
    if re.search(r"==|!=|\breturn\b|\d", joined):
        return "expected value changed"
    return "existing test lines removed or modified"


def strip_explanation(evidence: dict | None) -> dict:
    """Evidence without the executor's own narrative (never a verifier input)."""
    out = {}
    for key, value in dict(evidence or {}).items():
        if key in EXPLANATION_KEYS:
            continue
        out[key] = strip_explanation(value) if isinstance(value, dict) else value
    return out


def model_kind_of(evidence: dict) -> str:
    """MOCK_MODEL whenever any evidence says so; REAL_MODEL only when stated and unmarked."""
    blob = json.dumps(evidence, ensure_ascii=False, default=str)
    if MOCK_MARKER in blob or '"deterministic_test_model": true' in blob or '"MOCK_MODEL"' in blob:
        return "MOCK_MODEL"
    if str(evidence.get("model_kind") or "") == "REAL_MODEL":
        return "REAL_MODEL"
    return "UNKNOWN"


# ---------------------------------------------------------------- the verdict
def verify(*, task: dict, source: Path, base_sha: str, diff: str, tests: list[str], evidence: dict | None,
           out: Path, runner=None, holdout: tuple[str, ...] = (), require_new_regression: bool = True,
           deadline: float | None = None, keep_checkouts: bool = False) -> dict:
    """Return the verdict record (also written to ``out/verdict.json``).

    ``task``: {id, goal, tests (the task's failing checks), editable, new_tests?};
    ``tests``: every fixed test to rerun (task tests + regression suite);
    ``evidence``: attempt record (model id / model_kind / backend); explanation keys dropped.
    """
    out.mkdir(parents=True, exist_ok=True)
    home = out / "git-home"
    runner = runner or GuardedHostRunner()
    evidence = strip_explanation(evidence)
    kind = model_kind_of(evidence)
    rec: dict = {"schema": "bossman.evolution.verdict/1", "verifier": PRINCIPAL, "task_id": task.get("id"),
                 "base_sha": base_sha, "patch_sha256": hashlib.sha256((diff or "").encode("utf-8")).hexdigest(),
                 "model_kind": kind, "model": evidence.get("model"), "runner": runner.identity(),
                 "inputs": sorted(["task", "base_sha", "diff", "tests", "evidence"]),
                 "explanation_used": False, "verdict": "FAIL", "reasons": [], "checks": {},
                 "counts_as_student_success": False, "started_at": time.time()}
    checkouts: list[Path] = []

    def finish(verdict: str, *reasons: str) -> dict:
        rec["verdict"] = verdict
        rec["reasons"] = list(dict.fromkeys(rec["reasons"] + [r for r in reasons if r]))
        teacher = str(evidence.get("assistance_level") or "") in {"teacher_patch", "reference_solution"}
        rec["counts_as_student_success"] = verdict == "PASS" and kind == "REAL_MODEL" and not teacher
        if kind == "MOCK_MODEL":
            rec["student_success_note"] = "MOCK_MODEL: plumbing evidence only, never a student success"
        rec["finished_at"] = time.time()
        if not keep_checkouts:
            rec["checkouts_removed"] = all(rmtree(c) for c in checkouts)
        _write(out / "verdict.json", rec)
        return rec

    if not (diff or "").strip():
        return finish("FAIL", "NO_DIFF: the attempt produced no change")
    if len(diff.encode("utf-8")) > MAX_DIFF_BYTES:
        return finish("UNSAFE", "DIFF_TOO_LARGE")
    (out / "candidate.diff").write_text(diff, encoding="utf-8", newline="\n")
    if not diff.endswith("\n"):
        diff += "\n"
    try:
        cand = out / "checkouts" / "candidate"
        checkouts.append(cand)
        clean_checkout(source, base_sha, cand, home=home)
        code, _o, err = git(cand, "apply", "--index", "--whitespace=nowarn", "-", home=home, input_text=diff,
                            check=False)
        if code:
            rec["checks"]["apply"] = redact_text(err[-1500:])
            return finish("FAIL", "DIFF_DOES_NOT_APPLY to a clean baseline checkout")
        raw = git(cand, "diff", "--cached", "--raw", "--no-renames", "-z", home=home)[1]
        changes = _parse_raw(raw)
        rec["checks"]["changed"] = changes
        verdict = _static_review(cand, changes, task, holdout, home, rec)
        if verdict:
            return finish(verdict)
        new_tests = sorted(c["path"] for c in changes if c["status"] == "A" and is_test_path(c["path"])
                           and c["path"].endswith(".py"))
        rec["checks"]["new_regression_tests"] = new_tests
        tree = git(cand, "write-tree", home=home)[1].strip()
        rec["candidate_tree"] = tree

        base = out / "checkouts" / "baseline"
        checkouts.append(base)
        clean_checkout(source, base_sha, base, home=home)
        fixed = list(dict.fromkeys(tests))
        missing = [t for t in fixed if not (base / t).is_file()]
        if missing:
            return finish("FAIL", "FIXED_TESTS_MISSING_AT_BASELINE: " + ", ".join(missing))
        before = runner.run(base, fixed, out / "runs" / "baseline", deadline=deadline)
        after = runner.run(cand, fixed + new_tests, out / "runs" / "candidate", deadline=deadline)
        rec["checks"]["baseline"] = _slim(before)
        rec["checks"]["candidate"] = _slim(after)
        if before["status"] == "BLOCKED":
            return finish("FAIL", f"BASELINE_NOT_MEASURABLE ({before.get('reason')})")
        if after["status"] == "BLOCKED":
            return finish("FAIL", f"CANDIDATE_NOT_MEASURABLE ({after.get('reason')})")
        verdict, reasons = _compare(task, before["tests"], after["tests"], new_tests, rec)
        if verdict:
            return finish(verdict, *reasons)

        # Negative control: only the test part of the patch, the fix reverted.
        if new_tests:
            neg = out / "checkouts" / "negative-control"
            checkouts.append(neg)
            clean_checkout(source, base_sha, neg, home=home)
            includes = [f"--include={p}" for p in new_tests]
            code, _o, err = git(neg, "apply", "--index", "--whitespace=nowarn", *includes, "-", home=home,
                                input_text=diff, check=False)
            if code:
                return finish("FAIL", "NEGATIVE_CONTROL_NOT_BUILDABLE: " + redact_text(err[-300:]))
            control = runner.run(neg, new_tests, out / "runs" / "negative-control", deadline=deadline)
            rec["checks"]["negative_control"] = _slim(control)
            failing = [k for k, v in control["tests"].items() if v in ("FAIL", "ERROR")]
            if control["status"] == "BLOCKED" and control.get("reason") == "TIMEOUT":
                return finish("FAIL", "NEGATIVE_CONTROL_TIMEOUT")
            if not failing:
                return finish("INVALID_TEST", "NEW_REGRESSION_PASSES_WITHOUT_THE_FIX: it does not test the change")
            rec["checks"]["negative_control_failing"] = failing
        elif require_new_regression:
            return finish("PARTIAL", "NO_NEW_REGRESSION_TEST: the fixed tests pass, but no new regression "
                                     "test proves the defect stays fixed")
        else:
            rec["checks"]["negative_control"] = "task tests fail at the baseline (measured above)"
        return finish("PASS", "task tests pass, nothing regressed, the regression fails without the fix")
    except VerifierError as exc:
        return finish("FAIL", f"VERIFIER_ENVIRONMENT: {exc}")


def _write(path: Path, value: dict) -> None:
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    tmp.write_text(json.dumps(redact_obj(value), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _slim(run: dict) -> dict:
    return {k: run.get(k) for k in ("runner", "status", "exit_code", "timed_out", "reason", "seconds", "tests")}


def _parse_raw(raw: str) -> list[dict]:
    """``git diff --raw -z`` -> [{path, status, old_mode, new_mode}]."""
    parts = raw.split("\0")
    out, i = [], 0
    while i < len(parts) - 1:
        meta = parts[i]
        if not meta.startswith(":"):
            i += 1
            continue
        fields = meta[1:].split()
        old_mode, new_mode, status = fields[0], fields[1], fields[4][:1]
        out.append({"path": parts[i + 1], "status": status, "old_mode": old_mode, "new_mode": new_mode})
        i += 2
    return out


def _static_review(cand: Path, changes: list[dict], task: dict, holdout: tuple[str, ...], home: Path,
                   rec: dict) -> str:
    editable = set(task.get("editable") or [])
    task_tests = set(task.get("tests") or [])
    prefixes = new_test_prefixes(task)
    unsafe, invalid = [], []
    for c in changes:
        path, status = c["path"], c["status"]
        if (path.startswith("/") or ".." in PurePosixPath(path).parts or "\\" in path
                or PurePosixPath(path).parts[:1] == (".git",)):
            unsafe.append(f"{path}: path escape")
            continue
        why = protected_reason(path, holdout)
        if why:
            unsafe.append(f"{path}: {why} must not change")
            continue
        if "120000" in (c["old_mode"], c["new_mode"]) or "160000" in (c["old_mode"], c["new_mode"]):
            unsafe.append(f"{path}: symlink/submodule change")
            continue
        test = is_test_path(path)
        if test and status in ("D", "R", "C"):
            invalid.append(f"{path}: existing test deleted/renamed")
            continue
        if path in task_tests:
            removed, _added = _hunks(git(cand, "diff", "--cached", "-U0", "--", path, home=home)[1])
            invalid.append(f"{path}: the task's own test was modified"
                           + (f" ({_describe_removal(removed)})" if removed else ""))
            continue
        if test and status == "A":
            if not _in_prefix(path, prefixes):
                unsafe.append(f"{path}: new test outside the allowed regression locations {list(prefixes)}")
            continue
        if test:
            text = git(cand, "diff", "--cached", "-U0", "--", path, home=home)[1]
            removed, _added = _hunks(text)
            if removed:
                invalid.append(f"{path}: {_describe_removal(removed)}")
                continue
            before = _def_counts(git(cand, "show", "HEAD:" + path, home=home)[1]) or {}
            after = _def_counts((cand / path).read_text(encoding="utf-8", errors="replace"))
            if after is None:
                invalid.append(f"{path}: the modified test file does not parse")
                continue
            shadowed = sorted(n for n, k in after.items() if k > 1 and k > before.get(n, 0))
            if shadowed:
                invalid.append(f"{path}: an existing test is redefined (shadowed): {', '.join(shadowed[:5])}")
            continue
        if path not in editable:
            unsafe.append(f"{path}: outside the task's editable scope")
    # Content checks over every added line (secrets anywhere, skip markers in tests).
    for c in changes:
        path = c["path"]
        if c["status"] == "D" or any(path in u for u in unsafe):
            continue
        text = git(cand, "diff", "--cached", "--", path, home=home)[1]
        if "Binary files" in text or "GIT binary patch" in text:
            unsafe.append(f"{path}: binary change cannot be reviewed")
            continue
        _removed, added = _hunks(text)
        blob = "\n".join(added)
        if has_secret(blob):
            unsafe.append(f"{path}: secret-like content added")
        if is_test_path(path) and _SKIP_RX.search(blob):
            invalid.append(f"{path}: skip/xfail/exit added to a test")
    rec["checks"]["static"] = {"unsafe": unsafe, "invalid_test": invalid}
    if unsafe:
        rec["reasons"] += ["UNSAFE: " + u for u in unsafe]
        return "UNSAFE"
    if invalid:
        rec["reasons"] += ["INVALID_TEST: " + i for i in invalid]
        return "INVALID_TEST"
    return ""


def _def_counts(source: str) -> dict[str, int] | None:
    """How often each qualified name is defined in its scope. A second definition
    replaces the first, so an earlier test silently stops running (shadowing).
    None = the file does not parse."""
    import ast  # noqa: PLC0415
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    seen: dict[str, int] = {}

    def walk(body, prefix: str) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = prefix + node.name
                seen[name] = seen.get(name, 0) + 1
                if isinstance(node, ast.ClassDef):
                    walk(node.body, name + ".")
    walk(tree.body, "")
    return seen


def _compare(task: dict, before: dict, after: dict, new_tests: list[str], rec: dict) -> tuple[str, list[str]]:
    reasons: list[str] = []
    # Inventory: every test that ran at the baseline still runs and is not skipped.
    ran = {k: v for k, v in before.items() if v in ("PASS", "FAIL")}
    vanished = sorted(k for k in ran if k not in after)
    skipped = sorted(k for k in ran if after.get(k) == "SKIP")
    if vanished or skipped:
        rec["checks"]["inventory"] = {"vanished": vanished[:50], "skipped": skipped[:50]}
        return "INVALID_TEST", [f"TEST_INVENTORY_SHRANK: {len(vanished)} vanished, {len(skipped)} skipped"]
    regressions = sorted(k for k, v in before.items() if v == "PASS" and after.get(k) != "PASS")
    if regressions:
        rec["checks"]["regressions"] = regressions[:50]
        return "FAIL", ["REGRESSION: " + ", ".join(regressions[:5])]
    task_files = list(task.get("tests") or [])
    target_before = {k: v for k, v in before.items() if any(belongs(k, f) for f in task_files)}
    target_after = {k: v for k, v in after.items() if any(belongs(k, f) for f in task_files)}
    rec["checks"]["task_tests_before"] = target_before
    rec["checks"]["task_tests_after"] = target_after
    if not target_after:
        return "FAIL", ["TASK_TESTS_NOT_FOUND in the results"]
    failing_before = [k for k, v in target_before.items() if v != "PASS"]
    if not failing_before:
        return "FAIL", ["NO_DEFECT_AT_BASELINE: the task's tests already pass; nothing was measured"]
    # A module that failed to import at the baseline is one "test"; after the fix it
    # is several real tests, so "still failing" is judged on the candidate side.
    still = sorted(k for k, v in target_after.items() if v != "PASS")
    new_ids = [k for k in after if any(belongs(k, t) for t in new_tests)]
    new_bad = sorted(k for k in new_ids if after[k] != "PASS")
    if new_tests and not new_ids:
        return "INVALID_TEST", ["NEW_REGRESSION_COLLECTED_NO_TESTS"]
    if new_bad:
        return "FAIL", ["NEW_REGRESSION_FAILS_WITH_THE_FIX: " + ", ".join(new_bad[:5])]
    if still:
        fixed = [k for k in failing_before if target_after.get(k) == "PASS"]
        if fixed:
            return "PARTIAL", [f"TASK_PARTLY_FIXED: {len(fixed)} fixed, still failing: " + ", ".join(still[:5])]
        return "FAIL", ["TASK_TESTS_STILL_FAIL: " + ", ".join(still[:5])]
    reasons.append(f"task tests fixed: {len(failing_before)}")
    rec["checks"]["new_regression_ids"] = new_ids
    return "", reasons
