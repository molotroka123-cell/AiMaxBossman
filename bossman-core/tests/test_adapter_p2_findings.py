"""P2/P3-находки аудита адаптеров (audit-03-adapters.md), строки A3-05..A3-12.

A3-05: FailSafeException рвал длинный ввод посреди строки не-типизированной
       ошибкой, и replan повторял ту же координату.
A3-06: «границы» координат были произвольными константами (клик мимо всех
       мониторов проходил), а лишние клавиши хоткея молча срезались.
A3-07: любой отказ UIA превращался в «unsupported input» без причины.
A3-08: кадры прошлых запусков не удалял никто; OSError на записи PNG роняла
       наблюдение целиком.
A3-09: ожидание процесса в потоке не отменялось (утечка потока на каждый cancel);
       resolver ходил по диску в событийном цикле.
A3-10: исключение из supports() одного бэкенда отменяло перебор остальных.
A3-11: значения полей с паролем попадали в наблюдение и в журнал.
A3-12: цель «Save» кликала «Save and exit».

Windows-адаптер проверяется без Windows: pyautogui и геометрия экрана
подменяются на границе импорта — проверяется НАША логика, а не чужая раскладка.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest

from bossman.apprentice.models import AppIdentity, PlanStep, SemanticTarget
from bossman.computer_operator.adapters.app_launch import AppLaunchAdapter, _SyncProcess, _popen
from bossman.computer_operator.adapters.playwright_browser import (PlaywrightBrowserActuator,
                                                                   PlaywrightBrowserObserver)
from bossman.computer_operator.adapters.router import ActionRouter
from bossman.computer_operator.adapters.screenshot import LocalScreenshotProvider
from bossman.computer_operator.adapters.windows import (FailSafeAbort, UiaTargetError, WindowsDesktop)
from bossman.computer_operator.models import ActionKind, ComputerAction


# ------------------------------------------------------------------ pyautogui

class FakeFailSafe(Exception):
    pass


class FakePyAutoGui(types.ModuleType):
    """Раскладка покрывает ASCII; запись и хоткеи записываются, а не жмутся."""

    def __init__(self, *, fail_write_from=None):
        super().__init__("pyautogui")
        self.FAILSAFE = False
        self.FailSafeException = FakeFailSafe
        self.written: list[str] = []
        self.hotkeys: list[tuple] = []
        self.clicks: list[tuple] = []
        self._pyautogui_win = types.SimpleNamespace(
            keyboardMapping={chr(c): c for c in range(32, 127)})
        self._fail_from = fail_write_from

    def write(self, text, interval=0):
        if self._fail_from is not None and len("".join(self.written)) >= self._fail_from:
            raise FakeFailSafe("mouse in corner")
        self.written.append(text)

    def hotkey(self, *keys):
        self.hotkeys.append(keys)

    def click(self, x, y):
        self.clicks.append((x, y))


@pytest.fixture
def pyautogui(monkeypatch):
    fake = FakePyAutoGui()
    monkeypatch.setitem(sys.modules, "pyautogui", fake)
    return fake


def _action(kind, **kw):
    return ComputerAction.make(kind, **kw)


# ------------------------------------------------------------------ A3-05

def test_failsafe_during_a_long_type_is_a_typed_abort_naming_the_progress(monkeypatch):
    """Обрыв защитой — не «действие не удалось, попробуй ещё раз той же
    координатой»: это отдельный тип ошибки, и в ней сказано, сколько ушло."""
    fake = FakePyAutoGui(fail_write_from=16)
    monkeypatch.setitem(sys.modules, "pyautogui", fake)
    with pytest.raises(FailSafeAbort) as e:
        asyncio.run(WindowsDesktop()._input(_action(ActionKind.TYPE, text="x" * 64)))
    assert "after 16 of 64 characters" in str(e.value)


def test_typing_without_a_failsafe_still_goes_through(pyautogui):
    """Положительный контроль: обычный ввод не превратился в отказ."""
    asyncio.run(WindowsDesktop()._input(_action(ActionKind.TYPE, text="hello world")))
    assert "".join(pyautogui.written) == "hello world"


# ------------------------------------------------------------------ A3-06

def _click(x, y):
    return _action(ActionKind.CLICK, args={"x": x, "y": y})


def test_coordinate_outside_every_monitor_is_refused(monkeypatch):
    """x=90000 проходил «границы» и уводил клик в непредсказуемое окно."""
    monkeypatch.setattr(WindowsDesktop, "_virtual_screen", staticmethod(lambda: (0, 0, 1919, 1079)))
    with pytest.raises(ValueError, match="outside the virtual screen"):
        WindowsDesktop._xy(_click(90000, 0))


def test_a_second_monitor_on_the_left_keeps_working(monkeypatch):
    """Отрицательный контроль: отрицательный X на мульти-мониторе легитимен и
    отвергаться не должен."""
    monkeypatch.setattr(WindowsDesktop, "_virtual_screen", staticmethod(lambda: (-1920, 0, 1919, 1079)))
    assert WindowsDesktop._xy(_click(-1200, 500)) == (-1200, 500)
    assert WindowsDesktop._xy(_click(10, 10)) == (10, 10)


def test_without_screen_geometry_the_old_sanity_bounds_remain(monkeypatch):
    """Не-Windows/нет метрик: проверка не должна ни падать, ни пропускать мусор."""
    monkeypatch.setattr(WindowsDesktop, "_virtual_screen", staticmethod(lambda: None))
    assert WindowsDesktop._xy(_click(10, 10)) == (10, 10)
    with pytest.raises(ValueError, match="coordinate bounds"):
        WindowsDesktop._xy(_click(10 ** 9, 0))


def test_nine_hotkey_keys_are_refused_instead_of_silently_truncated(pyautogui):
    """Срез до 8 выполнял ДРУГУЮ комбинацию, чем просили."""
    keys = [f"k{i}" for i in range(9)]
    with pytest.raises(ValueError, match="at most 8 keys"):
        asyncio.run(WindowsDesktop()._input(_action(ActionKind.HOTKEY, args={"keys": keys})))
    assert pyautogui.hotkeys == []


def test_an_ordinary_hotkey_still_fires(pyautogui):
    """Положительный контроль."""
    asyncio.run(WindowsDesktop()._input(_action(ActionKind.HOTKEY, args={"keys": ["ctrl", "s"]})))
    assert pyautogui.hotkeys == [("ctrl", "s")]


# ------------------------------------------------------------------ A3-07

def _forced_windows():
    d = WindowsDesktop()
    d.is_windows = True
    return d


def test_uia_failure_names_the_reason_instead_of_unsupported_input(monkeypatch):
    """COM-ошибка/нет pywinauto больше не выглядит как «действие не поддержано»."""
    def boom():
        raise OSError("com is angry")
    monkeypatch.setattr(WindowsDesktop, "_active_window", staticmethod(boom))
    a = _action(ActionKind.FOCUS, target="Save")
    with pytest.raises(UiaTargetError, match="OSError: com is angry"):
        asyncio.run(_forced_windows().execute(a, None))


def test_uia_miss_says_element_not_found(monkeypatch):
    class Window:
        def descendants(self, title=None): return []
    monkeypatch.setattr(WindowsDesktop, "_active_window", staticmethod(Window))
    with pytest.raises(UiaTargetError, match="element not found"):
        asyncio.run(_forced_windows().execute(_action(ActionKind.UI_INVOKE, target="Save"), None))


def test_a_reachable_uia_target_is_invoked(monkeypatch):
    """Положительный контроль: успешный путь не превратился в отказ."""
    invoked = []

    class Node:
        def invoke(self): invoked.append("invoke")

    class Window:
        def descendants(self, title=None): return [Node()]

    monkeypatch.setattr(WindowsDesktop, "_active_window", staticmethod(Window))
    asyncio.run(_forced_windows().execute(_action(ActionKind.UI_INVOKE, target="Save"), None))
    assert invoked == ["invoke"]


# ------------------------------------------------------------------ A3-08

def test_frames_of_previous_runs_are_swept_on_start(tmp_path):
    """Окно хранения жило в памяти процесса: PNG прошлого запуска оставались навсегда."""
    old = tmp_path / "screen-1.png"; old.write_bytes(b"png")
    fresh = tmp_path / "screen-2.png"; fresh.write_bytes(b"png")
    import os
    stale = time.time() - 24 * 3600
    os.utime(old, (stale, stale))
    LocalScreenshotProvider(tmp_path)
    assert not old.exists()
    assert fresh.exists(), "свежий кадр соседнего процесса удалять нельзя"


def test_a_full_disk_degrades_the_frame_instead_of_killing_the_observation(tmp_path, monkeypatch):
    """OSError на save роняла ВСЁ наблюдение, хотя без кадра оно деградирует."""
    shot = types.SimpleNamespace(save=lambda p: (_ for _ in ()).throw(OSError("no space left")))
    fake = types.ModuleType("pyautogui")
    fake.screenshot = lambda: shot
    monkeypatch.setitem(sys.modules, "pyautogui", fake)
    provider = LocalScreenshotProvider(tmp_path)
    assert asyncio.run(provider.capture()) == (None, False)


def test_a_writable_disk_still_produces_a_frame(tmp_path, monkeypatch):
    """Положительный контроль: скриншот по-прежнему пишется."""
    class Shot:
        def save(self, p): Path(p).write_bytes(b"png")
    fake = types.ModuleType("pyautogui")
    fake.screenshot = Shot
    monkeypatch.setitem(sys.modules, "pyautogui", fake)
    ref, sensitive = asyncio.run(LocalScreenshotProvider(tmp_path).capture())
    assert ref and Path(ref).exists() and sensitive is False


# ------------------------------------------------------------------ A3-09

class FakePopen:
    def __init__(self): self.pid = 4242; self._code = None; self.blocking_wait_entered = False
    def poll(self): return self._code
    def wait(self):
        self.blocking_wait_entered = True
        time.sleep(30); return 0                    # как настоящий Popen.wait: неотменяем
    def exit(self, code=0): self._code = code


def test_cancelling_a_wait_leaks_no_thread():
    """`to_thread(Popen.wait)` не отменялся: поток жил до выхода GUI-приложения."""
    async def scenario():
        p = FakePopen()
        proc = _SyncProcess(p)
        task = asyncio.create_task(proc.wait())
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return p

    before = threading.active_count()
    p = asyncio.run(scenario())
    # Блокирующий Popen.wait не вызывался вовсе: именно он переживал отмену.
    assert p.blocking_wait_entered is False
    assert threading.active_count() <= before


def test_wait_still_returns_the_exit_code():
    """Положительный контроль: ожидание всё ещё дожидается процесса."""
    async def scenario():
        p = FakePopen()
        proc = _SyncProcess(p)
        task = asyncio.create_task(proc.wait())
        await asyncio.sleep(0.1)
        p.exit(3)
        return await asyncio.wait_for(task, 5)

    assert asyncio.run(scenario()) == 3


def test_launch_gets_its_own_process_group_on_windows(monkeypatch):
    """terminate/kill без своей группы не убивал дерево потомков."""
    seen = {}
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: seen.update(kw) or object())
    _popen(["notepad.exe"])
    assert seen.get("creationflags") == 0x200


def test_resolver_does_not_block_the_event_loop():
    """resolver ходит по диску: в цикле это останавливает и другие задачи."""
    where = {}

    def resolver(app):
        where["thread"] = threading.current_thread().name
        return "C:/Windows/System32/notepad.exe"

    launched = []

    async def launcher(exe): launched.append(exe)

    adapter = AppLaunchAdapter(launcher=launcher, resolver=resolver)
    asyncio.run(adapter.execute(_action(ActionKind.APP_LAUNCH, target="notepad"), None))
    assert launched == ["C:/Windows/System32/notepad.exe"]
    assert where["thread"] != threading.main_thread().name


# ------------------------------------------------------------------ A3-10

class _Backend:
    def __init__(self, name, *, ok=True, raises=None):
        self.name = name; self.ok = ok; self.raises = raises; self.executed = False
    async def supports(self, a, o):
        if self.raises: raise self.raises
        return self.ok
    async def execute(self, a, o): self.executed = True


def test_a_backend_whose_probe_raises_does_not_block_the_others():
    """Раньше исключение из supports() уходило наружу, и действие не пробовал НИ ОДИН
    из следующих бэкендов."""
    broken = _Backend("broken", raises=RuntimeError("probe blew up"))
    good = _Backend("good")
    name = asyncio.run(ActionRouter([broken, good]).execute(_click(1, 2), None))
    assert name == "good" and good.executed


def test_when_nothing_supports_the_action_the_probe_failures_are_named():
    broken = _Backend("broken", raises=RuntimeError("probe blew up"))
    with pytest.raises(RuntimeError, match="probe failures: broken: RuntimeError: probe blew up"):
        asyncio.run(ActionRouter([broken, _Backend("no", ok=False)]).execute(_click(1, 2), None))


def test_ordinary_routing_is_unchanged():
    """Положительный контроль: первый подходящий бэкенд по-прежнему выигрывает."""
    first, second = _Backend("first"), _Backend("second")
    assert asyncio.run(ActionRouter([first, second]).execute(_click(1, 2), None)) == "first"
    assert first.executed and not second.executed


# ------------------------------------------------------------------ playwright

class FakeElement:
    def __init__(self, name, *, value="", attrs=None, enabled=True):
        self.name = name; self.value = value; self.attrs = attrs or {}; self.enabled = enabled
    def accessible_name(self): return self.name
    def get_attribute(self, k): return self.attrs.get(k)
    def text_content(self): return self.name
    def is_enabled(self): return self.enabled
    def input_value(self): return self.value


class FakeLocator:
    def __init__(self, items): self.items = items
    def count(self): return len(self.items)
    def nth(self, i): return self.items[i]
    @property
    def first(self): return self.items[0]


class FakePage:
    def __init__(self, by_role, title="Login", url="https://example.test/login"):
        self.by_role = by_role; self._title = title; self.url = url
    def title(self): return self._title
    def get_by_role(self, role, name=None, exact=False):
        items = list(self.by_role.get(role, []))
        if name is not None:
            if exact: items = [e for e in items if e.name.strip().lower() == name.strip().lower()]
            else: items = [e for e in items if name.lower() in e.name.lower()]
        return FakeLocator(items)


# ------------------------------------------------------------------ A3-11

def test_password_value_never_enters_the_observation():
    """Значение поля с паролем уходило в контекст модели и в журнал задачи."""
    page = FakePage({"textbox": [FakeElement("Password", value="hunter2",
                                             attrs={"type": "password"})]})
    obs = PlaywrightBrowserObserver(page).observe()
    entry = obs.ui_tree["elements"][0]
    assert "value" not in entry and "hunter2" not in entry["text"]


def test_a_field_named_like_a_secret_is_not_read_either():
    page = FakePage({"textbox": [FakeElement("API token", value="sk-live-123")]})
    obs = PlaywrightBrowserObserver(page).observe()
    assert "sk-live-123" not in str(obs.ui_tree)


def test_ordinary_field_values_are_still_observed_and_marked_sensitive():
    """Отрицательный контроль: наблюдение не оскопили — значение обычного поля
    по-прежнему видно, но наблюдение помечено для redaction-конвейера."""
    page = FakePage({"textbox": [FakeElement("Full name", value="Ivan")]})
    obs = PlaywrightBrowserObserver(page).observe()
    assert obs.ui_tree["elements"][0]["value"] == "Ivan"
    assert obs.sensitive is True


def test_a_page_without_values_is_not_marked_sensitive():
    page = FakePage({"button": [FakeElement("Save")]})
    assert PlaywrightBrowserObserver(page).observe().sensitive is False


# ------------------------------------------------------------------ A3-12

def _step(role, name, kind=ActionKind.CLICK):
    return PlanStep("s1", kind, AppIdentity(app="chromium"), SemanticTarget(role, name))


def test_save_does_not_click_save_and_exit():
    """Подстрочный матч Playwright брал первую кнопку: «Save» жало «Save and exit»."""
    page = FakePage({"button": [FakeElement("Save and exit"), FakeElement("Save")]})
    act = PlaywrightBrowserActuator(page)
    loc = act._locate(_step("button", "Save"))
    assert loc.name == "Save"


def test_an_ambiguous_target_is_refused_with_the_candidates():
    page = FakePage({"button": [FakeElement("Save draft"), FakeElement("Save and exit")]})
    with pytest.raises(RuntimeError, match="ambiguous"):
        PlaywrightBrowserActuator(page)._locate(_step("button", "Save"))


def test_a_unique_substring_target_still_resolves():
    """Отрицательный контроль: единственный кандидат по подстроке — не двусмысленность."""
    page = FakePage({"button": [FakeElement("Save and exit")]})
    assert PlaywrightBrowserActuator(page)._locate(_step("button", "Save")).name == "Save and exit"


def test_a_missing_target_is_still_a_miss():
    page = FakePage({"button": [FakeElement("Cancel")]})
    with pytest.raises(RuntimeError, match="not found"):
        PlaywrightBrowserActuator(page)._locate(_step("button", "Save"))
