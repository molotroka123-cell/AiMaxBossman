"""APP_LAUNCH: запуск разрешённого приложения argv-массивом, без оболочки.

Граница безопасности: строка из вывода модели НИКОГДА не попадает в командную
строку. Модель называет логическое имя, allowlist отдаёт путь, процесс
стартует через create_subprocess_exec — ни shell=True, ни os.system, ни
конкатенации аргументов здесь нет и быть не должно.
"""
from __future__ import annotations

import asyncio
import subprocess

# Шаг опроса завершения процесса. Ожидание в потоке (`to_thread(Popen.wait)`)
# отменить нельзя: отмена роняла только await, а поток жил до выхода GUI-
# приложения — по потоку на каждую отмену (A3-09).
_WAIT_POLL_S = 0.05

from ..applist import canonical_app, resolve_executable
from ..models import ActionKind


class _SyncProcess:
    """Интерфейс asyncio-процесса поверх subprocess.Popen.

    Нужен там, где событийный цикл не умеет подпроцессы: на Windows это
    SelectorEventLoop, где create_subprocess_exec бросает NotImplementedError
    (подпроцессы там умеет только ProactorEventLoop). Запуск остаётся тем же
    argv-массивом без оболочки.
    """

    def __init__(self, proc: subprocess.Popen):
        self._proc = proc

    @property
    def pid(self) -> int:
        return self._proc.pid

    @property
    def returncode(self):
        return self._proc.poll()

    def terminate(self) -> None:
        self._proc.terminate()

    def kill(self) -> None:
        self._proc.kill()

    async def wait(self):
        while True:
            code = self._proc.poll()
            if code is not None:
                return code
            await asyncio.sleep(_WAIT_POLL_S)


def _popen(argv: list[str]) -> subprocess.Popen:
    # Список аргументов, shell не передаётся вовсе.
    kwargs = {}
    # Своя группа процессов: без неё terminate/kill бьёт только сам процесс, а
    # порождённое им дерево остаётся жить (A3-09). Флаг существует лишь на
    # Windows, на остальных системах его нет и передавать нечего.
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    if flags:
        kwargs["creationflags"] = flags
    return subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)


async def spawn_detached(exe):
    """argv-массив из одного элемента: аргументов от планировщика нет вовсе."""
    argv = [str(exe)]
    try:
        proc = await asyncio.create_subprocess_exec(
            argv[0],
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except NotImplementedError:
        proc = _SyncProcess(await asyncio.to_thread(_popen, argv))
    # GUI-приложение живёт дальше само: ждать его завершения нельзя. Процесс
    # возвращается, чтобы вызывающий (например live-smoke) мог его закрыть.
    return proc


class AppLaunchAdapter:
    name = "app-launch"

    def __init__(self, *, launcher=None, resolver=None):
        self.launcher = launcher or spawn_detached
        # resolver подменяем только в тестах: allowlist-проверка ниже остаётся
        # настоящей в любом случае.
        self.resolver = resolver or resolve_executable

    async def supports(self, a, o) -> bool:
        return a.kind is ActionKind.APP_LAUNCH and canonical_app(a.target) is not None

    async def execute(self, a, o) -> None:
        app = canonical_app(a.target)
        if app is None:                       # второй рубеж после policy
            raise RuntimeError("app is not allowlisted for launch")
        # resolver ходит по диску (System32, shutil.which): в событийном цикле
        # это блокирующий I/O, который останавливает и другие задачи.
        exe = await asyncio.to_thread(self.resolver, app)
        if exe is None:
            raise RuntimeError(f"no executable for allowlisted app {app!r} on this system")
        await self.launcher(exe)
