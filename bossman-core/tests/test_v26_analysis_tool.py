"""V2.6 раздел 19 — analysis.run: python3 -c в том же sandbox-пути, что shell.run.

Инварианты: не произвольный шелл (только python3 -c, код — один argv-элемент);
docker = AUTO, host/local = ALWAYS ASK (тот же предикат, что у shell);
неизвестный SANDBOX_MODE = PolicyDenied (fail closed). Реальный docker в тестах
не зовём — конструкцию argv проверяем через shell._build_command.
"""
from __future__ import annotations

import asyncio
import shlex

import pytest

from bossman import errors
from bossman.config import settings
from bossman.toolkit import REGISTRY, ToolContext, analysis, shell


def _ctx(tmp_path) -> ToolContext:
    return ToolContext(agent="t", workdir=tmp_path)


# ---------------- регистрация ----------------

def test_tool_registered_in_registry():
    tool = REGISTRY.get("analysis.run")
    assert tool is not None, "analysis.run должен регистрироваться при импорте модуля"
    assert tool.rights == "exec"
    assert tool.required == ["code"]
    assert "code" in tool.params and "timeout_s" in tool.params
    # тот же предикат подтверждения, что у shell.run — один инвариант, не копия
    assert tool.mandatory_confirm is shell._host_exec_needs_approval


# ---------------- mandatory_confirm: docker=AUTO, host=ALWAYS ASK ----------------

def test_mandatory_confirm_docker_is_auto(monkeypatch):
    monkeypatch.setattr(settings, "sandbox_mode", "docker", raising=False)
    assert REGISTRY["analysis.run"].mandatory_confirm() is False


def test_mandatory_confirm_local_always_asks(monkeypatch):
    monkeypatch.setattr(settings, "sandbox_mode", "local", raising=False)
    assert REGISTRY["analysis.run"].mandatory_confirm() is True


def test_mandatory_confirm_unknown_mode_fail_closed(monkeypatch):
    monkeypatch.setattr(settings, "sandbox_mode", "что-то-новое", raising=False)
    assert REGISTRY["analysis.run"].mandatory_confirm() is True


# ---------------- PolicyDenied: как у shell.run — исключением из handler ----------------

def test_unknown_sandbox_mode_raises_policy_denied(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "sandbox_mode", "martian", raising=False)
    with pytest.raises(errors.PolicyDenied) as exc:
        asyncio.run(analysis.run({"code": "print(1)"}, _ctx(tmp_path)))
    assert "неизвестный SANDBOX_MODE" in str(exc.value)


def test_local_without_unsafe_flag_raises_policy_denied(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "sandbox_mode", "local", raising=False)
    monkeypatch.setattr(settings, "allow_unsafe_local_exec", False, raising=False)
    with pytest.raises(errors.PolicyDenied) as exc:
        asyncio.run(analysis.run({"code": "print(1)"}, _ctx(tmp_path)))
    assert "BOSSMAN_UNSAFE_LOCAL_EXEC" in str(exc.value)


# ---------------- конструкция argv: docker без сети, код — один аргумент ----------------

def test_docker_argv_network_none_and_single_code_arg(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "sandbox_mode", "docker", raising=False)
    code = 'import statistics; print(statistics.mean([1, 2, 3]))'
    argv = analysis.build_python_argv(code, _ctx(tmp_path))
    assert argv[:5] == ["docker", "run", "--rm", "--network", "none"]
    assert argv[-2] == "-lc" and argv[-3] == "sh"
    # весь python3 -c <code> — ЕДИНСТВЕННЫЙ аргумент sh -lc, шелл хоста его не видит
    payload = argv[-1]
    assert payload == "python3 -c " + shlex.quote(code)
    assert shlex.split(payload) == ["python3", "-c", code]


def test_argv_built_by_shells_build_command(tmp_path, monkeypatch):
    """Никакого второго sandbox-пути: argv тождественен shell._build_command."""
    monkeypatch.setattr(settings, "sandbox_mode", "docker", raising=False)
    ctx = _ctx(tmp_path)
    code = "print('x')"
    assert analysis.build_python_argv(code, ctx) == \
        shell._build_command("python3 -c " + shlex.quote(code), ctx)


# ---------------- timeout: потолок 120 ----------------

def test_timeout_clamped_to_120():
    assert analysis._clamp_timeout(3600) == 120
    assert analysis._clamp_timeout(15) == 15
    assert analysis._clamp_timeout(0) == 1
    assert analysis._clamp_timeout("мусор") == 60
    assert analysis._clamp_timeout(None) == 60


# ---------------- честный локальный прогон (без docker) ----------------

def test_local_execution_runs_python_and_logs(tmp_path, monkeypatch):
    """local + осознанный флаг: настоящий python3 -c, вывод и лог как у shell.run."""
    monkeypatch.setattr(settings, "sandbox_mode", "local", raising=False)
    monkeypatch.setattr(settings, "allow_unsafe_local_exec", True, raising=False)
    result = asyncio.run(analysis.run(
        {"code": "print('hello-analysis', 2 + 2)"}, _ctx(tmp_path)))
    assert not result.error
    assert "код выхода: 0" in result.content
    assert "hello-analysis 4" in result.content
    logs = list((tmp_path / "assets" / "logs").glob("analysis-*.txt"))
    assert len(logs) == 1 and "hello-analysis 4" in logs[0].read_text()
