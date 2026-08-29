"""Дисциплина argv-only на хостовых инструментах + точный allowlist планировщика.

Нарушение Stage 8: инструменты агента собирали командную строку и звали
`create_subprocess_shell`. Даже с shlex.quote это второй интерпретатор, которого
быть не должно. Теперь — `create_subprocess_exec` (argv), а планировщик
сверяет ИМЯ исполняемого точно, без startswith.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from bossman import errors
from bossman.config import settings
from bossman.dev_factory.planner import ALLOWED_TEST_BINARIES, LLMPlanner
from bossman.dev_factory.models import StepKind
from bossman.toolkit import gitops, media, shell


# ---------- 1. никакого shell в хостовых инструментах ----------

@pytest.mark.parametrize("mod", [gitops, media, shell])
def test_no_create_subprocess_shell_in_toolkit(mod):
    src = Path(inspect.getfile(mod)).read_text(encoding="utf-8")
    tree = ast.parse(src)
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "create_subprocess_shell":
            bad.append(node.lineno)
    assert not bad, f"{mod.__name__}: create_subprocess_shell на строках {bad}"


@pytest.mark.parametrize("mod", [gitops, media, shell])
def test_toolkit_uses_exec(mod):
    src = Path(inspect.getfile(mod)).read_text(encoding="utf-8")
    assert "create_subprocess_exec" in src, f"{mod.__name__} не перешёл на exec"


# ---------- 2. shell._build_command возвращает argv, а не строку ----------

class _Ctx:
    def __init__(self, wd): self.workdir = Path(wd)


def test_docker_build_is_argv_list(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "sandbox_mode", "docker", raising=False)
    argv = shell._build_command("pytest -q; rm -rf /", _Ctx(tmp_path))
    assert isinstance(argv, list)
    # опасная строка едет ОДНИМ аргументом sh -lc контейнера, а не разбирается
    assert argv[-1] == "pytest -q; rm -rf /"
    assert argv[-2] == "-lc" and argv[0] == "docker"


def test_local_requires_optin(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "sandbox_mode", "local", raising=False)
    monkeypatch.setattr(settings, "allow_unsafe_local_exec", False, raising=False)
    with pytest.raises(errors.PolicyDenied):
        shell._build_command("id", _Ctx(tmp_path))


def test_unknown_mode_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "sandbox_mode", "docekr", raising=False)
    monkeypatch.setattr(settings, "allow_unsafe_local_exec", True, raising=False)
    with pytest.raises(errors.PolicyDenied):
        shell._build_command("id", _Ctx(tmp_path))


# ---------- 3. gitops не выполняет запрещённые операции ----------

@pytest.mark.anyio
async def test_gitops_rejects_forbidden_op(tmp_path):
    ctx = _Ctx(tmp_path)
    res = await gitops.git({"op": "push"}, ctx)
    assert res.error


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------- 4. точный allowlist планировщика (никакого startswith) ----------

@pytest.mark.parametrize("evil", [
    "python-malicious",
    "nodeevil",
    "pytest-wrapper-evil",
    "npmx",
    "make-believe",
    "gopher",
])
def test_lookalike_binary_rejected(evil):
    p = LLMPlanner(agent=None, test_argv=("python3", "-m", "pytest", "-q"))
    argv = p._safe_argv([evil, "--do-bad"], StepKind.TEST)
    # незнакомое имя → безопасный дефолт, а НЕ команда злоумышленника
    assert argv == ("python3", "-m", "pytest", "-q")
    assert evil not in argv


@pytest.mark.parametrize("good", ["python", "python3", "pytest", "npm", "node", "cargo"])
def test_exact_allowed_binary_passes(good):
    p = LLMPlanner(agent=None)
    argv = p._safe_argv([good, "run"], StepKind.TEST)
    assert argv[0] == good


def test_string_argv_rejected_wholesale():
    p = LLMPlanner(agent=None, test_argv=("python3", "-m", "pytest"))
    # строка вместо массива — целиком отвергается (иначе shell-инъекция)
    assert p._safe_argv("pytest; rm -rf /", StepKind.TEST) == ("python3", "-m", "pytest")


def test_allowlist_is_not_claimed_as_sandbox():
    # python/node сами исполняют код — allowlist не изоляция. Оставляем как
    # регрессионную заметку: список существует, но реальную границу держит Этап 8.
    assert "python" in {x.rsplit("/", 1)[-1] for x in ALLOWED_TEST_BINARIES}
