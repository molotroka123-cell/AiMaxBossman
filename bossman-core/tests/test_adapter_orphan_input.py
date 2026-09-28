"""Аудит rc19 (P1-A): поток ввода, брошенный вызывающим, печатал дальше.

`asyncio.wait_for` по таймауту (или отмена задачи) отменяет только ожидание:
поток `asyncio.to_thread` продолжал печатать, «исход неизвестен» снимался
первым же наблюдением, и следующее действие шло ОДНОВРЕМЕННО с сиротой —
причём в любое окно, оказавшееся впереди.

Адаптер теперь: видит свои живые потоки эффекта (`busy`), обрывает брошенные
(`abort_inflight`) на ближайшей порции и перед каждой порцией сверяет окно
переднего плана с тем, что проверил вызывающий (`foreground_handle`).
pyautogui подменён на границе импорта — реальный ввод не трогается.
"""
from __future__ import annotations

import asyncio
import sys
import threading
import time
import types

import pytest

from bossman.computer_operator.adapters.windows import WindowsDesktop
from bossman.computer_operator.models import ActionKind, ComputerAction, ExpectedState


class SlowPyAutoGui(types.ModuleType):
    """pyautogui без __file__ (тестовый): каждая порция «печатается» заметное время."""

    def __init__(self, per_char_s=0.01):
        super().__init__("pyautogui")
        self.FAILSAFE = False
        self.per_char_s = per_char_s
        self.written: list[str] = []
        self.on_write = None

    def write(self, text, interval=0):
        time.sleep(self.per_char_s * len(text))
        self.written.append(text)
        if self.on_write is not None:
            self.on_write()

    def hotkey(self, *keys):
        pass


@pytest.fixture
def pg(monkeypatch):
    fake = SlowPyAutoGui()
    monkeypatch.setitem(sys.modules, "pyautogui", fake)
    return fake


def _type(text, **args):
    return ComputerAction.make(ActionKind.TYPE, text=text,
                               expected=ExpectedState(contains_text="ok"), args=args)


def _typed(pg):
    return sum(len(x) for x in pg.written)


async def test_a_caller_timeout_leaves_a_visible_busy_thread_that_abort_stops(pg):
    pg.per_char_s = 0.02                      # 16 символов ≈ 0.3 с на порцию
    desktop = WindowsDesktop()
    desktop.set_interrupt(threading.Event())
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(desktop._input(_type("z" * 160)), timeout=0.1)
    # ожидание отменено, а поток жив — и это видно
    assert desktop.busy()
    desktop.abort_inflight()
    deadline = time.monotonic() + 5
    while desktop.busy() and time.monotonic() < deadline:
        await asyncio.sleep(0.02)
    assert not desktop.busy()
    # оборвался на ближайшей порции, а не допечатал 160 символов
    assert 0 < _typed(pg) <= 32, _typed(pg)


async def test_without_abort_the_orphan_would_finish_the_text(pg):
    """Отрицательный контроль: именно abort_inflight останавливает сироту."""
    pg.per_char_s = 0.002
    desktop = WindowsDesktop()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(desktop._input(_type("q" * 64)), timeout=0.01)
    deadline = time.monotonic() + 5
    while desktop.busy() and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert _typed(pg) == 64


async def test_an_input_aborted_before_its_thread_started_does_nothing(pg):
    desktop = WindowsDesktop()
    token = desktop._effect_token()               # как будто вызов уже поставлен в очередь
    desktop.abort_inflight()
    assert token.is_set()
    # следующий вызов получает СВОЙ, не оборванный токен
    await desktop._input(_type("ok"))
    assert "".join(pg.written) == "ok"
    assert not desktop.busy()


def test_typing_stops_when_the_foreground_window_changes(pg, monkeypatch):
    desktop = WindowsDesktop()
    fg = {"now": 501}
    monkeypatch.setattr(desktop, "_foreground_hwnd", lambda: fg["now"])
    pg.per_char_s = 0

    def popup_steals_focus():
        fg["now"] = 999                           # всплывающее окно / клик владельца

    pg.on_write = popup_steals_focus
    with pytest.raises(RuntimeError, match="foreground window changed after 16 of 64"):
        asyncio.run(desktop._input(_type("w" * 64, foreground_handle=501)))
    assert _typed(pg) == 16                       # хвост в чужое окно не ушёл


def test_the_clipboard_path_checks_the_foreground_too(pg, monkeypatch):
    desktop = WindowsDesktop()
    monkeypatch.setattr(desktop, "_typeable", lambda text, p: False)
    pasted = []
    monkeypatch.setattr(desktop, "_type_via_clipboard", lambda text, p: pasted.append(text))
    monkeypatch.setattr(desktop, "_foreground_hwnd", lambda: 0)   # не удалось прочитать
    with pytest.raises(RuntimeError, match="foreground window changed after 0"):
        asyncio.run(desktop._input(_type("Привет", foreground_handle=7)))
    assert pasted == []


def test_same_foreground_types_everything(pg, monkeypatch):
    """Положительный контроль: окно не менялось — текст целиком."""
    desktop = WindowsDesktop()
    monkeypatch.setattr(desktop, "_foreground_hwnd", lambda: 501)
    pg.per_char_s = 0
    asyncio.run(desktop._input(_type("v" * 40, foreground_handle=501)))
    assert "".join(pg.written) == "v" * 40
