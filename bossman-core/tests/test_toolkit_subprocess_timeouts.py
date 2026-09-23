"""Хостовые инструменты агента: таймаут — это исход, а не сирота и не вечное ожидание.

* `toolkit.media._run` (ffmpeg/ffprobe): `asyncio.wait_for(proc.communicate())`
  по таймауту отменяет ЧТЕНИЕ, но не процесс. Исключение уходило из инструмента,
  а ffmpeg продолжал работать и писать выход — после «таймаута» на диске
  появлялся файл, которого исход не объявлял.
* `toolkit.gitops._exec`: таймаута не было вовсе. git, повисший на хуке или на
  запросе подписи, держал шаг агента бесконечно.

Процессы здесь настоящие (интерпретатор вместо ffmpeg/git), свидетельство
осиротения — файл-маркер, который процесс пишет ПОСЛЕ окна таймаута.
"""
from __future__ import annotations

import asyncio
import os
import stat
import sys
import time
from pathlib import Path

import pytest

from bossman.toolkit import gitops, media

LATE_WRITER = ("import sys, time; time.sleep(float(sys.argv[2])); "
               "open(sys.argv[1], 'w').write('alive')")


async def test_media_run_timeout_is_an_outcome_and_kills_the_process(tmp_path: Path):
    marker = tmp_path / "late.txt"
    started = time.monotonic()
    code, out = await asyncio.wait_for(
        media._run([sys.executable, "-c", LATE_WRITER, str(marker), "2.5"], timeout=1),
        timeout=20)
    assert code == 124 and "тайм" in out, (code, out)
    assert time.monotonic() - started < 2.5
    await asyncio.sleep(3.0)
    assert not marker.exists(), "процесс пережил таймаут и дописал выход"


async def test_media_run_negative_control_a_fast_command_is_untouched(tmp_path: Path):
    marker = tmp_path / "fast.txt"
    code, out = await media._run([sys.executable, "-c", LATE_WRITER, str(marker), "0"],
                                 timeout=20)
    assert code == 0 and marker.read_text() == "alive"


@pytest.mark.skipif(os.name == "nt", reason="NOT RUN: подставной git — POSIX-скрипт")
async def test_gitops_exec_has_a_timeout_and_kills_a_hung_git(tmp_path: Path, monkeypatch):
    marker = tmp_path / "late.txt"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "git"
    fake.write_text(f"#!{sys.executable}\n"
                    "import sys, time\n"
                    "time.sleep(3)\n"
                    f"open({str(marker)!r}, 'w').write('alive')\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(gitops, "GIT_TIMEOUT_S", 1.0, raising=False)

    code, out = await asyncio.wait_for(gitops._exec("git", "status", cwd=tmp_path), timeout=20)

    assert code == 124 and "тайм" in out, (code, out)
    await asyncio.sleep(3.0)
    assert not marker.exists(), "git пережил таймаут"


async def test_gitops_exec_negative_control_real_git_still_answers(tmp_path: Path):
    code, out = await gitops._exec("git", "--version", cwd=tmp_path)
    assert code == 0 and out.startswith("git version")
