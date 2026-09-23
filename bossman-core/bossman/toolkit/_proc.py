"""Ограниченный по времени запуск хостового процесса для инструментов агента.

Таймаут — это исход (код 124 и причина), а не исключение из инструмента и не
процесс-сирота. `asyncio.wait_for(proc.communicate())` отменяет только чтение:
сам процесс продолжает работать. Поэтому по таймауту дерево процесса
добивается и дожидается. Дерево, а не pid: на Windows `git.exe` из `Git\\cmd`
— лаунчер настоящего `mingw64\\bin\\git.exe`, и убийство одного лаунчера
оставляет работающего потомка.

Ребёнок, запущенный с `tree_spawn_kwargs()`, ведёт свою группу процессов
(POSIX: новая сессия; Windows: CREATE_NEW_PROCESS_GROUP). Тогда
`kill_tree(..., own_group=True)` снимает на POSIX всю группу через killpg —
и внука, чей родитель уже вышел и которого по дереву родителей не найти.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess

TIMEOUT_CODE = 124


def tree_spawn_kwargs() -> dict:
    """Аргументы для `asyncio.create_subprocess_*`: ребёнок — лидер своей группы."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _descendants(pid: int) -> list:
    try:
        import psutil  # noqa: PLC0415 — опциональная зависимость bossman-core
    except ImportError:
        return []
    try:
        return psutil.Process(pid).children(recursive=True)
    except psutil.Error:
        return []


async def kill_tree(proc: asyncio.subprocess.Process, *, wait_s: float = 10.0,
                    own_group: bool = False) -> None:
    """Убить процесс и его потомков и дождаться, пока процесс действительно выйдет.

    own_group=True — только для ребёнка, запущенного с `tree_spawn_kwargs()`:
    на POSIX его pid — это id его группы, и killpg снимает группу целиком."""
    if own_group and os.name != "nt":
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGKILL)
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


async def communicate_within(proc: asyncio.subprocess.Process, timeout: float, *,
                             own_group: bool = False) -> tuple[bytes | None, bytes | None] | None:
    """communicate() с таймаутом: (stdout, stderr) или None — таймаут, дерево уже
    добито и дождано. Отмена (STOP владельца) тоже добивает дерево."""
    try:
        return await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        await kill_tree(proc, own_group=own_group)
        return None
    except asyncio.CancelledError:
        # STOP владельца отменяет задачу: процесс не должен пережить отмену.
        await kill_tree(proc, own_group=own_group)
        raise


async def communicate_bounded(proc: asyncio.subprocess.Process, timeout: float,
                              what: str) -> tuple[int, bytes] | None:
    """communicate() с таймаутом. None — таймаут (процесс уже добит)."""
    done = await communicate_within(proc, timeout)
    if done is None:
        return None
    out, _ = done
    return proc.returncode or 0, out or b""


def timeout_message(what: str, timeout: float) -> str:
    return f"таймаут {timeout:g} с: {what} остановлен"
