"""Stage 8 — SAFE rootless runtime: реальное исполнение с минимальной изоляцией.

Даёт tier ROOTLESS (и только его — честно, чтобы политика отвергла HOSTILE).
Свойства:
- одноразовая рабочая КОПИЯ источника (прод-ФС не монтируется как writable);
- запуск только через `create_subprocess_exec` (аргументы массивом, НИКОГДА shell);
- rlimits из ResourceRequest (адресное пространство, число процессов, файлы, CPU);
- wall-time таймаут → RuntimeTimeout;
- OFFLINE: если доступен `unshare -rn`, процесс уходит в сетевой namespace без
  интерфейсов — реальный блок egress, а не только решение control plane;
- destroy сносит рабочую копию.

Ограничение (осознанное): без user namespaces/bwrap это НЕ сильная изоляция —
поэтому tier ровно ROOTLESS, и HOSTILE/DEVELOPER через него не пройдут.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

try:
    import resource               # POSIX rlimits
except ImportError:               # Windows: модуля нет — рантайм там fail-closed
    resource = None               # (safe_runtime_available() -> False)

from ... import obs
from ..models import (
    IsolationTier,
    NetworkMode,
    RuntimeCapabilities,
    SandboxSession,
    SandboxState,
)
from ..netguard import EgressLockdown, available as lockdown_available, sandbox_uid
from ..runtime import DestroyFailure, RuntimeCrash, RuntimeTimeout

log = obs.get_logger("bossman.sandbox.safe")

# Команда по умолчанию, если заявка не задала свою (безвредный no-op).
DEFAULT_ARGV = ("/bin/echo", "bossman-sandbox-ready")


def safe_runtime_available() -> bool:
    """SAFE-рантайм работоспособен, если есть чем исполнять процессы."""
    return os.name == "posix"


def _unshare_available() -> bool:
    return shutil.which("unshare") is not None


class SafeRuntime:
    """Лёгкий rootless-рантайм. Один процесс на песочницу."""

    name = "safe"

    def __init__(self, *, workspace_root: str | Path | None = None) -> None:
        self.workspace_root = Path(workspace_root) if workspace_root else None
        # sandbox_id -> (process, workdir, stdout_path)
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        self._workdirs: dict[str, Path] = {}
        self._results: dict[str, int | None] = {}
        # Активные блокировки egress по песочницам (nftables + выделенный uid).
        self._locks: dict[str, EgressLockdown] = {}

    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            name=self.name,
            tiers=frozenset({IsolationTier.ROOTLESS}),  # честно: только слабый tier
            supports_offline=_unshare_available(),
            # ALLOWLIST честен только когда есть ЧЕМ его заставить: nftables +
            # root. Иначе процесс мог бы открыть сокет мимо прокси, и режим был
            # бы фикцией — политика такой рантайм отвергнет.
            supports_allowlist=lockdown_available(),
            supports_readonly_root=False,
            supports_seccomp=False,
            supports_pid_limit=True,
            supports_mem_limit=True,
        )

    # ---- пути ----

    def _root_for(self, session: SandboxSession) -> Path:
        base = self.workspace_root or Path(os.getcwd()) / "_sandbox"
        return Path(base) / session.id

    # ---- жизненный цикл ----

    async def prepare(self, session: SandboxSession) -> None:
        root = self._root_for(session)
        work = root / "work"
        out = root / "out"
        work.mkdir(parents=True, exist_ok=True)
        out.mkdir(parents=True, exist_ok=True)

        src = session.spec.workspace_source
        if src:
            src_p = Path(src).resolve()
            if not src_p.exists():
                raise RuntimeCrash(f"workspace source does not exist: {src_p}")
            # ОДНОРАЗОВАЯ КОПИЯ, а не монтирование оригинала (non-negotiable #6).
            # symlinks=False: ссылки наружу не переносятся как ссылки.
            if src_p.is_dir():
                shutil.copytree(src_p, work, dirs_exist_ok=True, symlinks=False,
                                ignore=shutil.ignore_patterns(".git", ".env", "*.pem", "id_rsa*", ".ssh"))
            else:
                shutil.copy2(src_p, work / src_p.name)
        self._workdirs[session.id] = work
        # Процессу под выделенным uid нужны права на свою рабочую область и
        # каталог вывода (и только на них).
        uid = self._drop_uid_for(session)
        if uid is not None:
            for d in (root, work, out):
                try:
                    os.chown(d, uid, uid)
                except (OSError, AttributeError):
                    pass

    @staticmethod
    def _needs_lockdown(session: SandboxSession) -> bool:
        """Нужен ли сетевой барьер: есть прокси (значит режим не OFFLINE)."""
        return bool(session.spec.labels.get("egress_proxy"))

    @staticmethod
    def _drop_uid_for(session: SandboxSession) -> int | None:
        """Под каким uid исполнять код песочницы.

        ВСЕГДА сбрасываем привилегии, а не только когда есть egress-прокси.
        Red-team: раньше OFFLINE-песочница (режим по умолчанию) шла под uid ядра,
        то есть под root — а из-под root не действует protected_hardlinks, и
        вредоносный код делал хардлинк на любой файл хоста (/etc/shadow) прямо в
        свою рабочую область. Изоляция кода не должна зависеть от режима сети."""
        if os.name != "posix" or not hasattr(os, "setuid"):
            return None
        try:
            if os.geteuid() != 0:
                return None      # мы и так непривилегированы — setuid невозможен
        except AttributeError:
            return None
        return sandbox_uid(session.id)

    def _preexec(self, session: SandboxSession):
        """rlimits применяются в дочернем процессе до exec."""
        r = session.spec.resources
        drop_to_uid = self._drop_uid_for(session)

        def _apply() -> None:
            if resource is None:      # без POSIX-rlimits дочерний не запускаем
                raise RuntimeError("rlimits unavailable on this platform")
            # Память (адресное пространство).
            if r.ram_bytes > 0:
                resource.setrlimit(resource.RLIMIT_AS, (r.ram_bytes, r.ram_bytes))
            # Число процессов/потоков — защита от fork-бомбы.
            if r.max_pids > 0:
                resource.setrlimit(resource.RLIMIT_NPROC, (r.max_pids, r.max_pids))
            # Файловые дескрипторы.
            if r.max_open_files > 0:
                resource.setrlimit(resource.RLIMIT_NOFILE, (r.max_open_files, r.max_open_files))
            # Размер создаваемых файлов — защита от disk-fill.
            if r.disk_bytes > 0:
                resource.setrlimit(resource.RLIMIT_FSIZE, (r.disk_bytes, r.disk_bytes))
            # CPU-время (страховка поверх wall-time).
            if r.wall_time_seconds > 0:
                cpu = max(1, int(r.wall_time_seconds))
                resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
            # Новая сессия: сигнал уходит всей группе, а не только лидеру.
            os.setsid()
            # Сброс привилегий в выделенный uid ПОСЛЕ лимитов: именно по этому
            # uid nftables режет весь трафик мимо прокси. Порядок setgid→setuid
            # обязателен — обратный оставил бы группу root.
            if drop_to_uid is not None:
                try:
                    os.setgid(drop_to_uid)
                    os.setgroups([])
                    os.setuid(drop_to_uid)
                except (OSError, AttributeError, PermissionError):
                    # Не смогли сбросить права — процесс НЕ запускаем под root
                    # с открытой сетью: это тихий обход барьера.
                    os._exit(97)

        return _apply

    def _argv(self, session: SandboxSession) -> list[str]:
        """Команда берётся ТОЛЬКО как массив аргументов. Строка/shell запрещены."""
        argv = session.spec.labels.get("argv")
        if isinstance(argv, (list, tuple)):
            cmd = [str(a) for a in argv]
        elif isinstance(argv, str):
            # Явно не режем строку шеллом: одиночная строка трактуется как один
            # аргумент-исполняемый файл, чтобы исключить инъекцию.
            cmd = [argv]
        else:
            cmd = list(DEFAULT_ARGV)
        if not cmd:
            cmd = list(DEFAULT_ARGV)

        # OFFLINE: реальный сетевой namespace без интерфейсов, если можем.
        policy = session.policy
        offline = policy is None or policy.network_mode == NetworkMode.OFFLINE
        if offline and _unshare_available():
            # -r: map current user to root inside userns (rootless);
            # -n: новый network namespace (без интерфейсов = нет egress).
            cmd = ["unshare", "-r", "-n", "--"] + cmd
        return cmd

    def _env(self, session: SandboxSession) -> dict[str, str]:
        """Минимальное окружение: НИКАКИХ host-секретов (non-negotiable #4).
        Наследование os.environ запрещено — там живут ключи и токены."""
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(self._workdirs.get(session.id, Path("/tmp"))),
            "LANG": "C.UTF-8",
            "BOSSMAN_SANDBOX_ID": session.id,
        }
        # Если менеджер поднял egress-прокси (ALLOWLIST/INTERNET), процесс обязан
        # ходить через него: адрес отдаётся стандартными переменными, которые
        # понимают curl/pip/git/requests. NO_PROXY намеренно пуст — исключений,
        # ходящих мимо барьера, быть не должно.
        proxy = session.spec.labels.get("egress_proxy")
        if proxy:
            url = f"http://{proxy}"
            env.update({
                "http_proxy": url, "https_proxy": url,
                "HTTP_PROXY": url, "HTTPS_PROXY": url,
                "all_proxy": url, "ALL_PROXY": url,
                "no_proxy": "", "NO_PROXY": "",
            })
        return env

    async def start(self, session: SandboxSession) -> None:
        work = self._workdirs.get(session.id)
        if work is None:
            raise RuntimeCrash("start before prepare")
        argv = self._argv(session)
        # Барьер ставим ДО запуска процесса: иначе между exec и правилами
        # существует окно, в которое можно успеть открыть сокет.
        self._apply_lockdown(session)
        out_path = self._root_for(session) / "out" / "stdout.log"
        try:
            proc = await asyncio.create_subprocess_exec(   # НИКОГДА не shell
                *argv,
                cwd=str(work),
                env=self._env(session),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                preexec_fn=self._preexec(session),
            )
        except (OSError, ValueError) as exc:
            raise RuntimeCrash(f"failed to spawn sandbox process: {exc}") from exc
        self._procs[session.id] = proc
        session.runtime_handle = proc.pid
        self._out_paths = getattr(self, "_out_paths", {})
        self._out_paths[session.id] = out_path

    async def poll(self, session: SandboxSession) -> SandboxState:
        proc = self._procs.get(session.id)
        if proc is None:
            return SandboxState.FAILED
        timeout = max(1, int(session.spec.resources.wall_time_seconds or 60))
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            await self._kill(session)
            raise RuntimeTimeout(f"wall-time {timeout}s exceeded") from exc
        # Вывод пишем в out/ — оттуда его заберёт Artifact Gate.
        out_path = getattr(self, "_out_paths", {}).get(session.id)
        if out_path is not None:
            try:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(stdout or b"")
            except OSError:
                pass
        self._results[session.id] = proc.returncode
        return SandboxState.COMPLETED if proc.returncode == 0 else SandboxState.FAILED

    def _apply_lockdown(self, session: SandboxSession) -> None:
        proxy = session.spec.labels.get("egress_proxy")
        if not proxy:
            return
        if not lockdown_available():
            # Прокси есть, а заставить ходить через него нечем → не запускаем.
            raise RuntimeCrash(
                "egress lockdown unavailable (need root + nftables); refusing to run "
                "with unenforced network policy")
        host, _, port = str(proxy).rpartition(":")
        lock = EgressLockdown(session.id)
        lock.apply(host or "127.0.0.1", int(port))
        self._locks[session.id] = lock

    def _release_lockdown(self, session: SandboxSession) -> None:
        lock = self._locks.pop(session.id, None)
        if lock is not None:
            lock.remove()

    async def freeze(self, session: SandboxSession) -> None:
        """SAFE-рантайм не умеет снапшотить память: «заморозка» = остановка
        процесса с сохранением рабочей копии для расследования."""
        proc = self._procs.get(session.id)
        if proc and proc.returncode is None:
            try:
                proc.send_signal(19)  # SIGSTOP
            except (ProcessLookupError, OSError):
                pass

    async def cancel(self, session: SandboxSession) -> None:
        await self._kill(session)

    async def _kill(self, session: SandboxSession) -> None:
        proc = self._procs.get(session.id)
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.kill()
            await asyncio.wait_for(proc.wait(), timeout=5)
        except (ProcessLookupError, OSError, asyncio.TimeoutError):
            pass

    async def destroy(self, session: SandboxSession) -> None:
        await self._kill(session)
        # Правила firewall сносятся вместе с песочницей — в хосте не остаётся следов.
        self._release_lockdown(session)
        self._procs.pop(session.id, None)
        self._workdirs.pop(session.id, None)
        root = self._root_for(session)
        keep = session.spec.labels.get("keep_workspace") in ("1", "true", True)
        if keep:
            return
        try:
            if root.exists():
                shutil.rmtree(root, ignore_errors=False)
        except OSError as exc:
            raise DestroyFailure(f"failed to remove sandbox workspace: {exc}") from exc

    # ---- вспомогательное для тестов/инспекции ----

    def exit_code(self, session: SandboxSession) -> int | None:
        return self._results.get(session.id)

    def workdir(self, session: SandboxSession) -> Path | None:
        return self._workdirs.get(session.id)
