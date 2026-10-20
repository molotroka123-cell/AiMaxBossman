"""Branch 6 «Computer»: the Windows adapter's last-moment guards (2026-10-20).

pyautogui / pywinauto are replaced at the import boundary: no real input is sent,
the real desktop and screen are never touched.
"""
from __future__ import annotations

import sys
import threading
import types

import pytest

from bossman.computer_operator.adapters.windows import WindowsDesktop
from bossman.computer_operator.models import ActionKind, ComputerAction


class FakePyAutoGui(types.ModuleType):
    def __init__(self):
        super().__init__("pyautogui")
        self.FAILSAFE = False
        self.sent: list[tuple] = []

    def click(self, *xy):
        self.sent.append(("click", xy))

    def doubleClick(self, *xy):
        self.sent.append(("double_click", xy))

    def hotkey(self, *keys):
        self.sent.append(("hotkey", keys))

    def scroll(self, clicks):
        self.sent.append(("scroll", clicks))

    def dragTo(self, *xy, duration=0):
        self.sent.append(("drag", xy))

    def write(self, text, interval=0):
        self.sent.append(("write", text))


@pytest.fixture
def pg(monkeypatch):
    fake = FakePyAutoGui()
    monkeypatch.setitem(sys.modules, "pyautogui", fake)
    monkeypatch.setattr(WindowsDesktop, "_virtual_screen", classmethod(lambda cls: None))
    return fake


SINGLE_SHOT = [
    (ActionKind.CLICK, {"x": 10, "y": 10}),
    (ActionKind.DOUBLE_CLICK, {"x": 10, "y": 10}),
    (ActionKind.HOTKEY, {"keys": ["ctrl", "s"]}),
    (ActionKind.SCROLL, {"clicks": -3}),
    (ActionKind.DRAG, {"x": 10, "y": 10}),
]


# ------------------------------------------------------------------ STOP right before a single-shot effect

@pytest.mark.parametrize("kind,args", SINGLE_SHOT)
async def test_stop_set_before_the_thread_starts_sends_nothing(pg, kind, args):
    desktop = WindowsDesktop()
    stop = threading.Event()
    desktop.set_interrupt(stop)
    stop.set()                               # STOP after the caller's last check
    with pytest.raises(RuntimeError, match="owner interrupted"):
        await desktop._input(ComputerAction.make(kind, args=dict(args)))
    assert pg.sent == []


@pytest.mark.parametrize("kind,args", SINGLE_SHOT)
async def test_without_stop_the_single_shot_effect_is_sent(pg, kind, args):
    """Positive control: the guard does not block ordinary input."""
    desktop = WindowsDesktop()
    desktop.set_interrupt(threading.Event())
    await desktop._input(ComputerAction.make(kind, args=dict(args)))
    assert len(pg.sent) == 1


class _Elem:
    def __init__(self, log):
        self.log = log

    def set_focus(self):
        self.log.append("set_focus")

    def invoke(self):
        self.log.append("invoke")


class _Win:
    def __init__(self, log, handle=7):
        self.log = log
        self.handle = handle

    def descendants(self, title=None):
        return [_Elem(self.log)]


@pytest.mark.parametrize("kind", [ActionKind.FOCUS, ActionKind.UI_INVOKE])
async def test_stop_before_the_uia_effect_is_honoured(monkeypatch, kind):
    log: list[str] = []
    monkeypatch.setattr(WindowsDesktop, "_active_window", staticmethod(lambda: _Win(log)))
    desktop = WindowsDesktop()
    stop = threading.Event()
    desktop.set_interrupt(stop)
    stop.set()
    ok, reason = await desktop._uia(ComputerAction.make(kind, target="Сохранить"))
    assert ok is False and "interrupted" in reason
    assert log == []
    stop.clear()
    ok, _ = await desktop._uia(ComputerAction.make(kind, target="Сохранить"))
    assert ok is True and len(log) == 1


# ------------------------------------------------------------------ window identity right before the effect

@pytest.mark.parametrize("kind,args", SINGLE_SHOT)
async def test_single_shot_input_refused_when_foreground_is_another_window(pg, kind, args):
    desktop = WindowsDesktop()
    desktop.set_interrupt(threading.Event())
    desktop._foreground_hwnd = lambda: 99           # a popup took the foreground
    with pytest.raises(RuntimeError, match="foreground window changed"):
        await desktop._input(ComputerAction.make(kind, args={**args, "foreground_handle": 7}))
    assert pg.sent == []
    desktop._foreground_hwnd = lambda: 7            # positive control: the checked window
    await desktop._input(ComputerAction.make(kind, args={**args, "foreground_handle": 7}))
    assert len(pg.sent) == 1


@pytest.mark.parametrize("kind", [ActionKind.FOCUS, ActionKind.UI_INVOKE])
async def test_uia_target_is_not_searched_in_a_foreign_foreground_window(monkeypatch, kind):
    log: list[str] = []
    monkeypatch.setattr(WindowsDesktop, "_active_window", staticmethod(lambda: _Win(log, handle=99)))
    desktop = WindowsDesktop()
    desktop.set_interrupt(threading.Event())
    ok, reason = await desktop._uia(ComputerAction.make(kind, target="Удалить",
                                                        args={"foreground_handle": 7}))
    assert ok is False and "foreground window changed" in reason
    assert log == []
    ok, _ = await desktop._uia(ComputerAction.make(kind, target="Удалить", args={"foreground_handle": 99}))
    assert ok is True and len(log) == 1
