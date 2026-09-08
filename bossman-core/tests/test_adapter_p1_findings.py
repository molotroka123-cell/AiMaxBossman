"""P1-находки аудита адаптеров (audit-03-adapters.md) на машине владельца.

A3-01 (security): планировщик мог выполнить browser.confirmed_* БЕЗ согласия.
A3-02: ввод продолжался после «Стоп»/«Пауза»/«Перехвата» и после освобождения
       аренды рабочего стола.
A3-03: TYPE кириллицей молча вводил только ASCII-обрывки.

Windows-адаптер тестируется без Windows: pyautogui и буфер обмена подменяются
на границе импорта, потому что проверяется НАША логика выбора пути ввода и
реакции на команду владельца, а не чужая раскладка.
"""
from __future__ import annotations

import asyncio
import sys
import threading
import types

import pytest

from bossman.computer_operator.adapters.router import ActionRouter
from bossman.computer_operator.adapters.windows import WindowsDesktop
from bossman.computer_operator.models import ActionKind, ComputerAction, ExpectedState


# ------------------------------------------------------------------ A3-01

def test_a_browser_op_needing_approval_cannot_be_dispatched_directly():
    """Обход стены подтверждения закрыт: инструменты с confirm_default
    отклоняются ДО вызова обработчика."""
    from bossman.toolkit import REGISTRY
    import bossman.toolkit.browser  # noqa: F401 — регистрация инструментов
    from bossman.computer_operator.subsystem import _browser_toolkit_dispatch

    guarded = [name for name, tool in REGISTRY.items()
               if name.startswith("browser.") and getattr(tool, "confirm_default", False)]
    assert set(guarded) == {"browser.confirmed_click", "browser.confirmed_press",
                            "browser.confirmed_select"}, guarded

    for name in guarded:
        action = ComputerAction.make(ActionKind.BROWSER,
                                     args={"op": name.split(".", 1)[1], "key": "Enter"})
        with pytest.raises(RuntimeError, match="requires owner approval"):
            asyncio.run(_browser_toolkit_dispatch(action, types.SimpleNamespace(workspace_dir=".")))


def test_an_ordinary_browser_op_still_dispatches():
    """Положительный контроль: стена стоит только там, где нужно подтверждение,
    а не на всём браузере."""
    from bossman.toolkit import REGISTRY
    import bossman.toolkit.browser  # noqa: F401
    assert not getattr(REGISTRY["browser.observe"], "confirm_default", False)


# ------------------------------------------------------------------ A3-03

class FakeKeyboard:
    """Раскладка pyautogui: карта покрывает ASCII, всё остальное — None.

    Ровно так ведёт себя `_pyautogui_win._keyDown`: символ, которого нет в карте,
    ПРОПУСКАЕТСЯ МОЛЧА.
    """

    def __init__(self):
        self.keyboardMapping = {chr(c): c for c in range(32, 127)}


class FakePyAutoGui(types.ModuleType):
    def __init__(self, *, clipboard_ok=True):
        super().__init__("pyautogui")
        self.FAILSAFE = False
        self.written: list[str] = []
        self.hotkeys: list[tuple] = []
        self._pyautogui_win = FakeKeyboard()
        self.clipboard_ok = clipboard_ok

    def write(self, text, interval=0):
        # Как настоящий бэкенд: непокрытые символы не нажимаются вовсе.
        self.written.append("".join(c for c in text if ord(c) < 127))

    def hotkey(self, *keys):
        self.hotkeys.append(keys)


@pytest.fixture
def pyautogui(monkeypatch):
    fake = FakePyAutoGui()
    monkeypatch.setitem(sys.modules, "pyautogui", fake)
    return fake


def _type(text, **args):
    return ComputerAction.make(ActionKind.TYPE, text=text,
                               expected=ExpectedState(contains_text="ok"), args=args)


def test_ascii_text_is_typed_directly(pyautogui):
    desktop = WindowsDesktop()
    asyncio.run(desktop._input(_type("hello world")))
    assert "".join(pyautogui.written) == "hello world"
    assert pyautogui.hotkeys == []            # буфер обмена не трогали


def test_cyrillic_text_goes_through_the_clipboard_instead_of_being_mangled(pyautogui,
                                                                          monkeypatch):
    """Главная находка: «Привет, мир» доезжал как «, ». Теперь текст либо
    попадает целиком, либо шаг честно падает."""
    pasted: list[str] = []
    restored: list = []
    clip = types.ModuleType("win32clipboard")
    con = types.ModuleType("win32con")
    con.CF_UNICODETEXT = 13
    state = {"value": "owner's own clipboard"}

    clip.OpenClipboard = lambda: None
    clip.CloseClipboard = lambda: None
    clip.EmptyClipboard = lambda: None
    clip.IsClipboardFormatAvailable = lambda fmt: state["value"] is not None
    clip.GetClipboardData = lambda fmt: state["value"]

    def set_data(fmt, value):
        state["value"] = value
        (pasted if len(pasted) == 0 else restored).append(value)

    clip.SetClipboardData = set_data
    monkeypatch.setitem(sys.modules, "win32clipboard", clip)
    monkeypatch.setitem(sys.modules, "win32con", con)

    desktop = WindowsDesktop()
    asyncio.run(desktop._input(_type("Привет, мир")))

    assert pasted == ["Привет, мир"]          # текст ушёл целиком
    assert pyautogui.hotkeys == [("ctrl", "v")]
    assert pyautogui.written == []            # ни одного искалеченного фрагмента
    # Буфер владельца — его вещь: он возвращён на место.
    assert restored == ["owner's own clipboard"]
    assert state["value"] == "owner's own clipboard"


def test_when_the_clipboard_path_fails_the_step_fails_loudly(pyautogui, monkeypatch):
    """Отрицательный контроль: половина введённого текста хуже ненажатой
    клавиши, потому что шаг при этом выглядит выполненным."""
    broken = types.ModuleType("win32clipboard")
    broken.OpenClipboard = lambda: (_ for _ in ()).throw(OSError("clipboard busy"))
    monkeypatch.setitem(sys.modules, "win32clipboard", broken)
    monkeypatch.setitem(sys.modules, "win32con", types.ModuleType("win32con"))

    desktop = WindowsDesktop()
    with pytest.raises(RuntimeError, match="cannot type"):
        asyncio.run(desktop._input(_type("Привет")))
    assert pyautogui.written == []


# ------------------------------------------------------------------ A3-02

def test_typing_stops_when_the_owner_interrupts(pyautogui):
    """Владелец нажал «Стоп» посреди длинной печати: ввод прекращается на
    следующей же порции, а не через десятки минут."""
    desktop = WindowsDesktop()
    event = threading.Event()
    desktop.set_interrupt(event)

    original = pyautogui.write
    def write(text, interval=0):
        original(text, interval)
        if sum(len(x) for x in pyautogui.written) >= 32:
            event.set()                        # владелец вмешался
    pyautogui.write = write

    with pytest.raises(RuntimeError, match="owner interrupted typing"):
        asyncio.run(desktop._input(_type("x" * 4000)))
    typed = sum(len(x) for x in pyautogui.written)
    assert 0 < typed < 4000                    # остановились, а не допечатали
    assert typed <= 48                         # и почти сразу


def test_an_already_set_interrupt_types_nothing_at_all(pyautogui):
    """Команда владельца, пришедшая до шага, не должна пропустить ни символа."""
    desktop = WindowsDesktop()
    event = threading.Event()
    event.set()
    desktop.set_interrupt(event)
    with pytest.raises(RuntimeError, match="owner interrupted typing after 0"):
        asyncio.run(desktop._input(_type("hello")))
    assert pyautogui.written == []


def test_without_an_interrupt_the_whole_text_is_typed(pyautogui):
    """Положительный контроль: без вмешательства порционный ввод даёт тот же
    текст, что и один вызов."""
    desktop = WindowsDesktop()
    desktop.set_interrupt(threading.Event())
    asyncio.run(desktop._input(_type("y" * 100)))
    assert "".join(pyautogui.written) == "y" * 100


def test_the_router_hands_the_interrupt_to_backends_that_take_one():
    class Taker:
        name = "taker"
        got = None
        def set_interrupt(self, event): self.got = event
    class Plain:
        name = "plain"

    event = threading.Event()
    taker, plain = Taker(), Plain()
    ActionRouter([taker, plain]).set_interrupt(event)
    assert taker.got is event                  # а бэкенд без метода просто не получил


def test_the_manager_signals_before_it_releases_the_desktop(tmp_path):
    """Порядок обязателен: сначала «остановись», потом отдать аренду. Наоборот —
    окно, в котором владелец уже получил рабочий стол, а поток в него печатает.
    """
    from bossman.computer_operator.wiring import FakeAdapter, FakeObserver, FakePlanner, make_manager

    order: list[str] = []

    class Lease:
        def __init__(self): self._holder = None
        def acquire(self, task_id, ttl_s=None): self._holder = task_id; return True
        def heartbeat(self, task_id): return True
        def release(self, task_id): order.append("release"); self._holder = None; return True
        def revoke(self): self._holder = None
        def holder(self): return self._holder

    mgr = make_manager(tmp_path / "t.json", FakePlanner([]), FakeObserver(summary="ok"),
                       adapter=FakeAdapter(), control_lease=Lease())
    task = mgr.create_task("describe the screen")
    original = mgr._signal_interrupt
    mgr._signal_interrupt = lambda i: (order.append("interrupt"), original(i))[1]

    asyncio.run(mgr.run(task.id))
    assert order[:2] == ["interrupt", "release"], order


@pytest.mark.parametrize("command", ["pause", "take_control", "stop"])
def test_every_owner_command_raises_the_interrupt(tmp_path, command):
    from bossman.computer_operator.wiring import FakeObserver, FakePlanner, make_manager

    mgr = make_manager(tmp_path / "t.json", FakePlanner([]), FakeObserver(summary="ok"))
    task = mgr.create_task("owner command fixture")
    assert not mgr.interrupted(task.id)
    getattr(mgr, command)(task.id)
    assert mgr.interrupted(task.id), command
