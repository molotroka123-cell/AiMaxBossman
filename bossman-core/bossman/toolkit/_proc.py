"""Ограниченный по времени запуск хостового процесса для инструментов агента.

Таймаут — это исход (код 124 и причина), а не исключение из инструмента и не
процесс-сирота. `asyncio.wait_for(proc.communicate())` отменяет только чтение:
сам процесс продолжает работать. Поэтому по таймауту дерево процесса
добивается и дожидается. Дерево, а не pid: на Windows `git.exe` из `Git\\cmd`
— лаунчер настоящего `mingw64\\bin\\git.exe`, и убийство одного лаунчера
оставляет работающего потомка.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess

TIMEOUT_CODE = 124


def _descendants(pid: int) -> list:
    try:
        import psutil  # noqa: PLC0415 — опциональная зависимость bossman-core
    except ImportError:
        return []
    try:
        return psutil.Process(pid).children(recursive=True)
    except psutil.Error:
        return []


async def kill_tree(proc: asyncio.subprocess.Process, *, wait_s: float = 10.0) -> None:
    """Убить процесс и его потомков и дождаться, пока процесс действительно выйдет."""
    if proc.returncode is not None:
        return
    children = _descendants(proc.pid)
    if os.name == "nt" and not children:
        # Без psutil дерево на Windows снимает taskkill /T.
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],  # noqa: S603, S607
                           capture_output=True, timeout=15, check=False)
    with contextlib.suppress(ProcessLookupError):
        proc.kill()
    for child in children:
        with contextlib.suppress(Exception):
            child.kill()
    with contextlib.suppress(asyncio.TimeoutError, ProcessLookupError):
        await asyncio.wait_for(proc.wait(), timeout=wait_s)


async def communicate_bounded(proc: asyncio.subprocess.Process, timeout: float,
                              what: str) -> tuple[int, bytes] | None:
    """communicate() с таймаутом. None — таймаут (процесс уже добит)."""
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        await kill_tree(proc)
        return None
    except asyncio.CancelledError:
        # STOP владельца отменяет задачу: процесс не должен пережить отмену.
        await kill_tree(proc)
        raise
    return proc.returncode or 0, out or b""


def timeout_message(what: str, timeout: float) -> str:
    return f"таймаут {timeout:g} с: {what} остановлен"
