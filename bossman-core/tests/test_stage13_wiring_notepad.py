"""Stage 13: реальный сквозной путь Computer Operator и запуск Блокнота.

Покрывает то, ради чего этап существует: production-сборка собрана на реальных
компонентах (без заглушек), APP_LAUNCH проходит deny-by-default allowlist,
запуск идёт argv-массивом без оболочки, действие выполняется на СВЕЖЕМ
наблюдении, а на настоящей Windows-машине Блокнот действительно открывается.
"""
from __future__ import annotations

import asyncio
import inspect
import platform

import pytest

from bossman.computer_operator import subsystem as subsystem_mod
from bossman.computer_operator.adapters.app_launch import AppLaunchAdapter, spawn_detached
from bossman.computer_operator.adapters.router import ActionRouter
from bossman.computer_operator.applist import canonical_app, resolve_executable
from bossman.computer_operator.manager import ComputerOperatorManager
from bossman.computer_operator.models import (ActionKind, ComputerAction, ExpectedState, TaskMode,
                                              TaskState)
from bossman.computer_operator.observer import Observer
from bossman.computer_operator.planner import Planner
from bossman.computer_operator.policy import ComputerPolicy
from bossman.computer_operator.store import JsonTaskStore
from bossman.computer_operator.wiring import FakeObserver, FakePlanner


def launch(target="notepad"):
    return ComputerAction.make(ActionKind.APP_LAUNCH, target=target,
                               expected=ExpectedState(foreground_app_contains="notepad"))


def complete():
    return ComputerAction.make(ActionKind.COMPLETE)


async def _auto_create(kind, preview, tool=None, payload=None):
    return 1


async def _auto_wait(approval_id, timeout_s=None):
    return {"status": "approved", "id": approval_id}


def _fake_resolver(app):
    """Подмена ТОЛЬКО платформенного поиска файла: notepad.exe существует лишь
    на Windows, а проверить сквозной путь нужно и на Linux-CI. Allowlist при
    этом остаётся настоящим — подменяется поиск, а не разрешение."""
    from pathlib import Path
    return Path(r"C:\Windows\System32") / f"{app}.exe"


# ---------- A: production wiring действительно подключён ----------

def test_production_manager_has_no_stub_components():
    """Главный критерий этапа: в проде больше нет Unwired-заглушек."""
    m = subsystem_mod.MANAGER
    assert isinstance(m.planner, Planner)
    assert isinstance(m.observer, Observer)
    assert isinstance(m.action_router, ActionRouter)
    assert isinstance(m.policy, ComputerPolicy)
    assert not hasattr(subsystem_mod, "Unwired")
    src = inspect.getsource(subsystem_mod)
    assert "Unwired" not in src
    assert "wire Stage3" not in src and "wire action router" not in src


def test_action_router_carries_real_backends():
    names = [b.name for b in subsystem_mod.MANAGER.action_router.backends]
    assert "app-launch" in names          # APP_LAUNCH исполним
    assert "windows" in names             # клавиатура/мышь/UIA
    assert "vision-input" in names


def test_task_store_lives_in_workspace_not_cwd():
    """Состояние задач не должно писаться в текущий каталог процесса."""
    p = str(subsystem_mod.default_store_path())
    assert p.endswith("computer_operator/tasks.json") or p.endswith("computer_operator\\tasks.json")
    assert not p.startswith("./") and not p.startswith("computer_operator")


async def test_planner_reaches_model_only_through_llm_chat(monkeypatch):
    """Планировщик ходит в модель через существующий llm.chat (Stage 3 Gateway),
    а не собственным HTTP-клиентом — второго выхода к провайдеру нет."""
    import bossman.llm as llm
    seen = {}

    async def fake_chat(agent, messages, *, alias=None, max_tokens=None, **kw):
        seen["agent"] = agent
        seen["alias"] = alias
        seen["messages"] = messages
        return {"content": '{"kind":"COMPLETE"}'}

    monkeypatch.setattr(llm, "chat", fake_chat)
    out = await subsystem_mod.planner_chat(model="bossman-fast",
                                           messages=[{"role": "user", "content": "hi"}],
                                           max_tokens=100)
    assert out["content"]
    assert seen["alias"] == "bossman-fast"
    # управление рабочим столом не уезжает в облако молча
    assert seen["agent"].cloud_policy == "never"


# ---------- B: allowlist запуска приложений (deny-by-default) ----------

@pytest.mark.parametrize("name,expected", [
    ("notepad", "notepad"), ("Notepad", "notepad"), ("  NOTEPAD  ", "notepad"),
    ("notepad.exe", "notepad"), ("блокнот", "notepad"),
    ("calc", "calculator"), ("calculator", "calculator"),
])
def test_allowlist_accepts_known_apps(name, expected):
    assert canonical_app(name) == expected


@pytest.mark.parametrize("bad", [
    None, "", "   ", "cmd", "powershell", "regedit", "unknown-app",
    r"C:\Windows\System32\notepad.exe",          # путь, а не имя
    "../../windows/system32/notepad.exe",
    "notepad.exe & calc.exe",                    # цепочка команд
    "notepad.exe;calc.exe", "notepad | calc", "notepad$(whoami)",
    "notepad`whoami`", "notepad\ncalc", "notepad\0", "notepad %USERPROFILE%",
    "n" * 65,                                    # переполнение имени
])
def test_allowlist_denies_everything_else(bad):
    assert canonical_app(bad) is None


def test_policy_denies_non_allowlisted_app_launch():
    p = ComputerPolicy()
    d = p.classify(launch("powershell"), mode=TaskMode.CONTROL)
    assert not d.allow and "allowlist" in d.reason


def test_policy_allows_allowlisted_app_launch():
    d = ComputerPolicy().classify(launch("notepad"), mode=TaskMode.CONTROL)
    assert d.allow and not d.requires_approval


def test_policy_denies_app_launch_in_observe_only_mode():
    d = ComputerPolicy().classify(launch("notepad"), mode=TaskMode.OBSERVE_ONLY)
    assert not d.allow


def test_resolve_executable_rejects_unknown_and_non_windows():
    assert resolve_executable("unknown-app") is None
    assert resolve_executable("notepad", system="Linux") is None


def test_resolve_executable_ignores_path_hijack(tmp_path, monkeypatch):
    """Подложенный в PATH notepad.exe не подменяет системный."""
    fake = tmp_path / "notepad.exe"
    fake.write_text("evil", encoding="utf-8")
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("SystemRoot", str(tmp_path / "nowhere"))
    assert resolve_executable("notepad", system="Windows") is None


# ---------- C: исполнение запуска — argv, без оболочки ----------

async def test_app_launch_adapter_uses_argv_never_shell():
    calls = []

    async def rec(exe):
        calls.append(exe)

    a = AppLaunchAdapter(launcher=rec, resolver=_fake_resolver)
    act = launch("notepad")
    assert await a.supports(act, None)
    await a.execute(act, None)
    assert len(calls) == 1
    # запускается путь, выбранный allowlist'ом, а не строка от модели
    assert "notepad" in str(calls[0]).lower()


async def test_app_launch_adapter_refuses_non_allowlisted_even_if_policy_bypassed():
    """Второй рубеж: даже при обходе policy роутер не запустит произвольное."""
    a = AppLaunchAdapter(launcher=lambda exe: None)
    act = launch("powershell")
    assert not await a.supports(act, None)
    with pytest.raises(RuntimeError, match="allowlist"):
        await a.execute(act, None)


def test_launch_path_contains_no_shell_execution():
    """Ни shell=True, ни os.system/popen, ни create_subprocess_shell в КОДЕ.

    Проверяем AST, а не текст файла: комментарий, объясняющий запрет, не должен
    ни проходить за нарушение, ни маскировать настоящее.
    """
    import ast
    from bossman.computer_operator.adapters import app_launch
    from bossman.computer_operator import applist

    banned = {"os.system", "os.popen", "os.spawnl", "os.spawnv", "subprocess.call",
              "subprocess.run", "asyncio.create_subprocess_shell"}

    def dotted(node):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            return f"{node.value.id}.{node.attr}"
        return None

    for mod in (app_launch, applist):
        tree = ast.parse(inspect.getsource(mod))
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "shell":
                assert not (isinstance(node.value, ast.Constant) and node.value.value)
            if isinstance(node, ast.Call):
                name = dotted(node.func)
                assert name not in banned, f"{mod.__name__}: {name}"


async def test_spawn_falls_back_when_loop_cannot_do_subprocesses(monkeypatch, tmp_path):
    """Windows SelectorEventLoop не умеет подпроцессы — запуск не должен падать.

    create_subprocess_exec там бросает NotImplementedError; запуск обязан
    пережить это, оставшись argv-массивом без оболочки.
    """
    from bossman.computer_operator.adapters import app_launch

    async def boom(*a, **kw):
        raise NotImplementedError("SelectorEventLoop has no subprocess support")

    captured = {}

    def fake_popen(argv):
        captured["argv"] = argv
        return _DummyPopen()

    monkeypatch.setattr(app_launch.asyncio, "create_subprocess_exec", boom)
    monkeypatch.setattr(app_launch, "_popen", fake_popen)
    proc = await app_launch.spawn_detached(r"C:\Windows\System32\notepad.exe")
    # список аргументов, а не строка команды: оболочки на этом пути нет
    assert captured["argv"] == [r"C:\Windows\System32\notepad.exe"]
    assert proc.returncode is None
    assert await proc.wait() == 0


class _DummyPopen:
    pid = 4242
    def poll(self): return None
    def wait(self): return 0
    def terminate(self): pass
    def kill(self): pass


def test_sync_process_wrapper_exposes_async_process_interface():
    from bossman.computer_operator.adapters.app_launch import _SyncProcess
    p = _SyncProcess(_DummyPopen())
    assert p.pid == 4242 and p.returncode is None
    p.terminate(); p.kill()


# ---------- D: сквозной путь до executor'а ----------

def _manager(tmp_path, planner, observer, launcher):
    return ComputerOperatorManager(
        store=JsonTaskStore(tmp_path / "tasks.json"),
        planner=planner, observer=observer,
        action_router=ActionRouter([AppLaunchAdapter(launcher=launcher, resolver=_fake_resolver)]),
        approval_create=_auto_create, approval_wait=_auto_wait,
        event_emit=lambda *a, **k: None)


async def test_open_notepad_runs_through_policy_router_and_executor(tmp_path):
    """command -> plan -> observe -> policy -> router -> executor -> observe.

    Планировщик подменён (это модель), ВСЁ остальное — продовые классы.
    """
    launched = []

    async def rec(exe):
        launched.append(str(exe))

    planner = FakePlanner([launch("notepad"), complete()])
    observer = FakeObserver(
        observations=[{"foreground": {"app": "explorer.exe", "title": "Desktop"},
                       "summary": "desktop"}],
        foreground={"app": "notepad.exe", "title": "Untitled - Notepad"},
        summary="notepad is open")
    m = _manager(tmp_path, planner, observer, rec)
    t = m.create_task("Открой Блокнот")
    assert await m.run(t.id) is TaskState.COMPLETED
    assert len(launched) == 1 and "notepad" in launched[0].lower()
    done = m.store.get(t.id)
    assert done.history[0].verified is True     # постусловие подтверждено
    assert done.steps_used == 1


async def test_non_allowlisted_app_never_reaches_executor(tmp_path):
    """Политика останавливает запуск ДО роутера: процесс не стартует вовсе."""
    launched = []

    async def rec(exe):
        launched.append(str(exe))

    planner = FakePlanner([launch("powershell"), complete()])
    observer = FakeObserver(foreground={"app": "explorer.exe"}, summary="desktop")
    m = _manager(tmp_path, planner, observer, rec)
    t = m.create_task("Запусти powershell")
    assert await m.run(t.id) is TaskState.COMPLETED   # планировщик перепланировал
    assert launched == []                              # но запуска не было


async def test_app_launch_executes_on_fresh_observation(tmp_path):
    """Инвариант свежести: перед действием — наблюдение, после — НОВОЕ наблюдение."""
    order = []

    async def rec(exe):
        order.append("launch")

    class Tracking(FakeObserver):
        async def observe(self, *, generation):
            order.append("observe")
            return await super().observe(generation=generation)

    planner = FakePlanner([launch("notepad"), complete()])
    observer = Tracking(foreground={"app": "notepad.exe"}, summary="notepad")
    m = _manager(tmp_path, planner, observer, rec)
    t = m.create_task("Открой Блокнот")
    assert await m.run(t.id) is TaskState.COMPLETED
    i = order.index("launch")
    assert "observe" in order[:i]      # действие спланировано на наблюдении
    assert "observe" in order[i + 1:]  # результат проверен НОВЫМ наблюдением


async def test_stale_generation_blocks_launch(tmp_path):
    """Если состояние сменилось после планирования — запуск не выполняется."""
    launched = []

    async def rec(exe):
        launched.append(str(exe))

    m = _manager(tmp_path, None, None, rec)
    t = m.create_task("Открой Блокнот")

    class Bumping:
        async def next_action(self, **kw):
            m.pause(t.id)          # пользователь вмешался: generation += 1
            return launch("notepad")

    m.planner = Bumping()
    m.observer = FakeObserver(foreground={"app": "explorer.exe"}, summary="desktop")
    await m.run(t.id)
    assert launched == []


# ---------- E: живой Windows-смоук ----------

def _windows_gui() -> bool:
    return platform.system().lower() == "windows" and resolve_executable("notepad") is not None


requires_windows = pytest.mark.skipif(
    not _windows_gui(), reason="SKIP_NO_WINDOWS_GUI: нет Windows с доступным notepad.exe")


@requires_windows
async def test_live_notepad_actually_launches(tmp_path):
    """Живой смоук: Блокнот РЕАЛЬНО запускается через продовый путь."""
    from bossman.computer_operator.adapters.screenshot import LocalScreenshotProvider
    from bossman.computer_operator.adapters.windows import WindowsDesktop

    procs = []

    async def rec(exe):
        p = await spawn_detached(exe)     # настоящий запуск, argv-массивом
        procs.append(p)
        return p

    desktop = WindowsDesktop()
    planner = FakePlanner([launch("notepad"), complete()])
    m = ComputerOperatorManager(
        store=JsonTaskStore(tmp_path / "tasks.json"),
        planner=planner,
        observer=Observer(desktop, LocalScreenshotProvider(root=tmp_path / "shots")),
        action_router=ActionRouter([AppLaunchAdapter(launcher=rec), desktop]),
        approval_create=_auto_create, approval_wait=_auto_wait,
        event_emit=lambda *a, **k: None)
    t = m.create_task("Открой Блокнот")
    try:
        state = await m.run(t.id)
        assert procs and procs[0].returncode is None       # процесс живёт
        assert state in {TaskState.COMPLETED, TaskState.FAILED}
        assert m.store.get(t.id).steps_used >= 1           # действие выполнено
    finally:
        for p in procs:
            try:
                p.terminate()
                await asyncio.wait_for(p.wait(), timeout=10)
            except Exception:
                pass
