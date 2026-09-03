"""CR-3: войти в Command Center можно и без установленных entry points.

Аудит запуска с компьютера владельца: команды `bcc` в PATH не оказалось
(пакет не был установлен через `pip install -e`), а `python -m bcc` падал
с «No module named bcc.__main__». Плюс `bcc --help` поднимал сервер вместо
того, чтобы показать справку. Здесь закрыты обе дыры.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from bcc import __version__
from bcc.app import build_parser, main

REPO = Path(__file__).resolve().parents[1]


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "bcc", *args], cwd=REPO,
                          capture_output=True, text=True, timeout=60)


def test_python_dash_m_bcc_exists():
    """Модуль запускается как пакет — без этого владельцу нечем войти."""
    assert (REPO / "bcc" / "__main__.py").is_file()


def test_help_does_not_start_a_server():
    proc = _run("--help")
    assert proc.returncode == 0, proc.stderr
    assert "--host" in proc.stdout and "--port" in proc.stdout
    # Сервер не поднимался: иначе процесс не вышел бы сам и не молчал бы об адресе.
    assert "Command Center: http" not in proc.stdout


def test_version_prints_and_exits():
    proc = _run("--version")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == f"bcc {__version__}"


def test_version_path_never_touches_uvicorn(monkeypatch, capsys):
    """Быстрая проверка внутри процесса: --version возвращается до запуска сервера."""
    import bcc.app as app_mod

    def _explode(*_a, **_k):  # pragma: no cover — срабатывание = провал теста
        raise AssertionError("uvicorn.run не должен вызываться на --version")

    monkeypatch.setattr(app_mod.uvicorn, "run", _explode)
    main(["--version"])
    assert capsys.readouterr().out.strip() == f"bcc {__version__}"


@pytest.mark.parametrize("argv,host,port", [
    ([], None, None),
    (["--host", "0.0.0.0", "--port", "9001"], "0.0.0.0", 9001),
])
def test_parser_accepts_host_and_port(argv, host, port):
    args = build_parser().parse_args(argv)
    assert args.host == host and args.port == port


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "[::1]", "127.1",
                                  "::ffff:127.0.0.1", "2130706433", ""])
def test_loopback_addresses_are_recognised(host):
    from bcc.app import is_loopback
    assert is_loopback(host) is True


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "10.0.0.1", "::", "example.com",
                                  "not-an-address"])
def test_external_addresses_are_not_mistaken_for_loopback(host):
    from bcc.app import is_loopback
    assert is_loopback(host) is False


def test_binding_outside_loopback_warns_the_owner(monkeypatch, capsys):
    """Command Center задуман локальным; открыться на весь мир молча он не должен."""
    import bcc.app as app_mod

    monkeypatch.setattr(app_mod, "create", lambda: object())
    monkeypatch.setattr(app_mod.settings, "ensure_dirs", lambda: None)
    monkeypatch.setattr(app_mod.uvicorn, "run", lambda *a, **k: None)

    main(["--host", "0.0.0.0"])
    assert "ВНИМАНИЕ" in capsys.readouterr().out

    main(["--host", "127.0.0.1"])
    assert "ВНИМАНИЕ" not in capsys.readouterr().out
