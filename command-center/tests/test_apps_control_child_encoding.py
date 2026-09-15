"""H-CLUSTER: дочернее приложение писало журнал в кодировке хоста и умирало.

Живой прогон владельца (Windows, 2026-09-06): приложение с кириллическим
выводом получало exit 1 и статус 'exited' вместо 'not_ready'. Дефект не в
приложении: `_child_env` фильтрует окружение, дочерний python не наследует
ничего про кодировку и берёт локаль хоста (cp1252), а журнал читается как
UTF-8. Кодировка дочернего процесса — часть контракта запуска.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from bcc.features.apps_control import _child_env


def test_the_child_is_told_which_encoding_to_write_in(tmp_path):
    env = _child_env(tmp_path, 8123)
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["PYTHONUTF8"] == "1"
    assert env["PYTHONUNBUFFERED"] == "1"          # прежний контракт не тронут
    assert env["APP_PORT"] == "8123" and env["PORT"] == "8123"


def test_a_child_printing_cyrillic_survives_a_hostile_locale(tmp_path):
    """Воспроизведение под ASCII-локалью: без контракта дочерний процесс падает
    с UnicodeEncodeError, с ним — печатает кириллицу и выходит своим кодом."""
    script = tmp_path / "app.py"
    script.write_text("import sys\nprint('приложение не поднялось: порт занят')\n"
                      "sys.exit(3)\n", encoding="utf-8")

    hostile = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"}
    bare = subprocess.run([sys.executable, str(script)], capture_output=True, env=hostile)
    with_contract = subprocess.run(
        [sys.executable, str(script)], capture_output=True,
        env={**hostile, **{k: v for k, v in _child_env(tmp_path, None).items()
                           if k.startswith("PYTHON")}})

    # Настоящий код возврата приложения виден только при заданной кодировке.
    assert with_contract.returncode == 3
    assert "приложение не поднялось".encode() in with_contract.stdout
    if bare.returncode != 3:                        # хост с ASCII-локалью
        assert b"UnicodeEncodeError" in bare.stderr
