"""Adversarial re-verification of findings that were reported as landed.

Lane rule: a commit that MENTIONS a finding is not evidence that the defect
cannot happen. Every test here is either

  * a REPRODUCTION — it FAILS today, on this working tree, and each such test
    names the file:line and the concrete input that produces the wrong output;
    or
  * a NEGATIVE CONTROL — it passes today AND was shown to fail when the fix
    was locally reverted (recorded in the test's docstring).

Scope of this file (audit-03-adapters.md and audit-09-web-designer.md):

  * A3-05 … A3-12 — все ВОСПРОИЗВОДЯТСЯ. Закрыты были только A3-01…A3-04
    (см. bossman-core/tests/test_adapter_p1_findings.py и
    test_operator_step_timeout.py); строки P2/P3 того же аудита кода не
    получили вовсе. Тесты ниже падают — это и есть отчёт.
  * A9-06 — закрытие БЕЗ теста: единственное место, где отказ 409 больше не
    запирает редактор навсегда, проверялось только чтением кода. Здесь оно
    проверяется в настоящем Chromium.

Файл ничего не чинит: правки лежат в чужих лентах, и вердикт должен остаться
независимым от них.
"""
from __future__ import annotations

import asyncio
import sys
import threading
import types

import pytest

from bossman.computer_operator.adapters.app_launch import AppLaunchAdapter
from bossman.computer_operator.adapters.playwright_browser import (
    PlaywrightBrowserActuator, PlaywrightBrowserObserver)
from bossman.computer_operator.adapters.router import ActionRouter
from bossman.computer_operator.adapters.screenshot import LocalScreenshotProvider
from bossman.computer_operator.adapters.windows import WindowsDesktop
from bossman.computer_operator.models import ActionKind, ComputerAction, ExpectedState

from .browser_support import chromium_available, reason as browser_reason
from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401


# ================================================================ A3 harness

class _Keyboard:
    """Раскладка pyautogui: ASCII покрыт, остальное — None (как на Windows)."""

    def __init__(self):
        self.keyboardMapping = {chr(c): c for c in range(32, 127)}


class _FailSafe(Exception):
    """Стенд-двойник pyautogui.FailSafeException."""


class FakePyAutoGui(types.ModuleType):
    def __init__(self, *, fail_after: int | None = None):
        super().__init__("pyautogui")
        self.FAILSAFE = False
        self.PAUSE = 0.1
        self.FailSafeException = _FailSafe
        self.written: list[str] = []
        self.hotkeys: list[tuple] = []
        self.clicks: list[tuple] = []
        self._pyautogui_win = _Keyboard()
        self._fail_after = fail_after

    def write(self, text, interval=0):
        if self._fail_after is not None and len("".join(self.written)) >= self._fail_after:
            raise _FailSafe("PyAutoGUI fail-safe triggered from mouse moving to a corner")
        self.written.append(text)

    def hotkey(self, *keys):
        self.hotkeys.append(keys)

    def click(self, *xy):
        self.clicks.append(xy)


@pytest.fixture
def pyautogui(monkeypatch):
    fake = FakePyAutoGui()
    monkeypatch.setitem(sys.modules, "pyautogui", fake)
    return fake


def _type(text, **args):
    return ComputerAction.make(ActionKind.TYPE, text=text,
                               expected=ExpectedState(contains_text="ok"), args=args)


# ---------------------------------------------------------------- A3-05

def test_a_failsafe_abort_is_typed_and_says_how_much_was_typed(monkeypatch):
    """РЕПРОДУКЦИЯ A3-05 — adapters/windows.py:181-212 (`_input`, ветка TYPE).

    Вход: мышь оставлена в углу экрана легальным кликом; шаг TYPE на 64 символа.
    pyautogui проверяет failSafeCheck() МЕЖДУ символами, поэтому write бросает
    FailSafeException посреди строки.

    Что происходит сейчас: исключение уходит из `_input` как есть и попадает в
    manager.py:409 `except Exception` -> replan -> планировщик повторяет ТУ ЖЕ
    акцию -> следующая write снова падает на том же углу, пока не сгорит
    replan-бюджет (manager.py:412 'action replan budget'). Частичный ввод при
    этом уже в поле, а шаг об этом не сообщает.

    Чего требует контракт: типизированный аварийный останов (не replan) с
    указанием, сколько символов успело уйти, — иначе исход шага неизвестен.
    """
    fake = FakePyAutoGui(fail_after=16)
    monkeypatch.setitem(sys.modules, "pyautogui", fake)
    desktop = WindowsDesktop()

    with pytest.raises(Exception) as caught:  # noqa: PT011 — тип и есть предмет проверки
        asyncio.run(desktop._input(_type("x" * 64)))

    assert not isinstance(caught.value, _FailSafe), (
        "fail-safe уехал наверх сырым исключением pyautogui: manager.py:409 "
        "трактует его как обычную ошибку шага и повторяет ту же координату")
    assert "16" in str(caught.value), (
        "останов не называет, сколько символов уже введено — исход шага неизвестен")


def test_the_typing_pause_is_not_left_at_the_pyautogui_default(pyautogui):
    """РЕПРОДУКЦИЯ A3-05 (вторая половина) — adapters/windows.py:181.

    `pyautogui.PAUSE` по умолчанию 0.1 c и добавляется К КАЖДОМУ вызову.
    Интервал ввода задаётся явно (`a.args['interval']`), поэтому скрытая
    добавка ломает и скорость шага, и human-speed гейты.
    """
    desktop = WindowsDesktop()
    asyncio.run(desktop._input(_type("hello", interval=0)))
    assert pyautogui.PAUSE == 0, "скрытая пауза 0.1 c на каждый вызов не снята"


# ---------------------------------------------------------------- A3-06

def test_a_hotkey_longer_than_the_limit_is_refused_not_silently_trimmed(pyautogui):
    """РЕПРОДУКЦИЯ A3-06 — adapters/windows.py:214 (`keys=[...][:8]`).

    Вход: HOTKEY с девятью клавишами.
    Сейчас: выполняются первые восемь — НЕ ТОТ хоткей, о чём никто не узнаёт.
    Контракт: отказ, потому что «почти тот» хоткей — это чужое действие.
    """
    nine = ["ctrl", "shift", "alt", "win", "a", "b", "c", "d", "e"]
    action = ComputerAction.make(ActionKind.HOTKEY, args={"keys": nine},
                                 expected=ExpectedState(contains_text="ok"))
    with pytest.raises(ValueError):
        asyncio.run(desktop_input(action))
    assert pyautogui.hotkeys == []


def desktop_input(action):
    return WindowsDesktop()._input(action)


def test_a_click_outside_every_monitor_is_refused(pyautogui):
    """РЕПРОДУКЦИЯ A3-06 — adapters/windows.py:221-226 (`_xy`).

    Вход: CLICK x=90000, y=0. Проверка `-10000<=x<=100000` — произвольная
    константа, а не геометрия: она пропускает координату, которой нет ни на
    одном мониторе. pyautogui уводит курсор к краю виртуального экрана и
    кликает в непредсказуемое окно владельца.
    """
    action = ComputerAction.make(ActionKind.CLICK, args={"x": 90000, "y": 0},
                                 expected=ExpectedState(contains_text="ok"))
    with pytest.raises(ValueError):
        asyncio.run(desktop_input(action))
    assert pyautogui.clicks == [], "клик ушёл за пределы всех мониторов"


# ---------------------------------------------------------------- A3-07

def test_a_failed_uia_target_says_why_instead_of_unsupported_input(pyautogui, monkeypatch):
    """РЕПРОДУКЦИЯ A3-07 — adapters/windows.py:97-112 (`execute` / `_uia`).

    Вход: FOCUS по target, которого на экране нет (или COM-ошибка при обходе).
    `_uia` возвращает False на ЛЮБОЙ исключительной ситуации, причина теряется,
    и `execute` идёт в `_input`, где для FOCUS ветки нет: владелец видит в
    истории шага «RuntimeError: unsupported input» — сообщение, которое
    указывает на несуществующую проблему.
    """
    desktop = WindowsDesktop()
    monkeypatch.setattr(desktop, "is_windows", True)

    async def never_finds(a):
        return False

    monkeypatch.setattr(desktop, "_uia", never_finds)
    action = ComputerAction.make(ActionKind.FOCUS, target="Кнопка, которой нет",
                                 expected=ExpectedState(contains_text="ok"))

    with pytest.raises(RuntimeError) as caught:
        asyncio.run(desktop.execute(action, None))
    message = str(caught.value)
    assert "unsupported input" not in message, message
    assert "Кнопка, которой нет" in message, (
        "ошибка не называет ни цель, ни причину — диагноз подменён")


# ---------------------------------------------------------------- A3-08

def test_frames_left_by_a_previous_process_are_cleaned_up(tmp_path, monkeypatch):
    """РЕПРОДУКЦИЯ A3-08(a) — adapters/screenshot.py:16 (`self._written`).

    Окно хранения ведётся В ПАМЯТИ процесса. Кадры прошлого запуска лежат в том
    же общем каталоге (tempdir/bossman-computer) и не удаляются НИКОГДА: две
    сессии подряд — и PNG экрана владельца остаются на диске навсегда.
    """
    root = tmp_path / "bossman-computer"
    root.mkdir()
    for i in range(200):
        (root / f"screen-{i}.png").write_bytes(b"old frame")

    fake = types.ModuleType("pyautogui")

    class _Shot:
        def save(self, path):
            open(path, "wb").write(b"new frame")

    fake.screenshot = lambda: _Shot()
    monkeypatch.setitem(sys.modules, "pyautogui", fake)

    provider = LocalScreenshotProvider(root=root, retention=8)
    asyncio.run(provider.capture())

    left = sorted(root.glob("screen-*.png"))
    assert len(left) <= 8, f"кадры прошлого процесса остались навсегда: {len(left)}"


def test_a_disk_error_degrades_the_observation_instead_of_breaking_it(tmp_path, monkeypatch):
    """РЕПРОДУКЦИЯ A3-08(c) — adapters/screenshot.py:24-30.

    `pyautogui.screenshot().save(p)` стоит вне try (пойман только ImportError).
    Диск полон / файл держит антивирус -> OSError -> падает ВСЁ наблюдение,
    хотя скриншот — лишь одна его часть.
    """
    fake = types.ModuleType("pyautogui")

    class _Shot:
        def save(self, path):
            raise OSError(28, "No space left on device")

    fake.screenshot = lambda: _Shot()
    monkeypatch.setitem(sys.modules, "pyautogui", fake)

    provider = LocalScreenshotProvider(root=tmp_path / "shots")
    ref, sensitive = asyncio.run(provider.capture())
    assert ref is None and sensitive is False


# ---------------------------------------------------------------- A3-09

def test_resolving_an_executable_does_not_block_the_event_loop():
    """РЕПРОДУКЦИЯ A3-09 — adapters/app_launch.py:88 (`self.resolver(app)`).

    `resolve_executable` — синхронный дисковый обход (System32/SysWOW64 +
    shutil.which). Он вызывается ПРЯМО в корутине `execute`, то есть держит
    событийный цикл всего оператора: пока диск отвечает, ни heartbeat аренды,
    ни опрос команды владельца не идут.
    """
    loop_thread = threading.current_thread().ident
    seen: dict[str, int | None] = {}

    def resolver(app):
        seen["thread"] = threading.current_thread().ident
        return "C:/Windows/System32/notepad.exe"

    async def launcher(exe):
        return None

    adapter = AppLaunchAdapter(launcher=launcher, resolver=resolver)
    action = ComputerAction.make(ActionKind.APP_LAUNCH, target="notepad",
                                 expected=ExpectedState(contains_text="ok"))

    async def go():
        nonlocal loop_thread
        loop_thread = threading.current_thread().ident
        await adapter.execute(action, None)

    asyncio.run(go())
    assert seen["thread"] != loop_thread, (
        "дисковый resolve выполнен в потоке событийного цикла — цикл оператора "
        "стоит вместе с ним")


# ---------------------------------------------------------------- A3-10

def test_one_backend_failing_its_probe_does_not_disable_the_others():
    """РЕПРОДУКЦИЯ A3-10 — adapters/router.py:16-18.

    `if await b.supports(...)` стоит без try: бэкенд, чей зонд БРОСАЕТ (на
    не-Windows, при отсутствующем COM, при недоступном реестре возможностей),
    роняет ВЕСЬ выбор маршрута — подходящий бэкенд, стоящий следом, даже не
    опрашивается, а владелец видит ошибку зонда вместо действия.
    """
    class Broken:
        name = "broken"

        async def supports(self, a, o):
            raise RuntimeError("capability probe exploded")

        async def execute(self, a, o):
            raise AssertionError("не должен вызываться")

    class Good:
        name = "good"

        def __init__(self):
            self.ran = False

        async def supports(self, a, o):
            return True

        async def execute(self, a, o):
            self.ran = True

    good = Good()
    router = ActionRouter([Broken(), good])
    action = ComputerAction.make(ActionKind.CLICK, args={"x": 1, "y": 1},
                                 expected=ExpectedState(contains_text="ok"))

    assert asyncio.run(router.execute(action, None)) == "good"
    assert good.ran


# ---------------------------------------------------------------- A3-11 / A3-12

class _Locator:
    def __init__(self, entries):
        self.entries = entries

    def count(self):
        return len(self.entries)

    def nth(self, i):
        return self.entries[i]

    @property
    def first(self):
        return self.entries[0]


class _El:
    def __init__(self, name, value=None, input_type="text"):
        self.name = name
        self.value = value
        self.input_type = input_type
        self.clicked = False

    def accessible_name(self):
        return self.name

    def get_attribute(self, attr):
        return {"aria-label": self.name, "type": self.input_type}.get(attr)

    def text_content(self):
        return self.name

    def is_enabled(self):
        return True

    def input_value(self):
        return self.value if self.value is not None else ""

    def click(self, **kw):
        self.clicked = True


class _Page:
    """Двойник страницы Playwright: get_by_role(name=..., exact=...) как в жизни —
    без exact имя матчится ПОДСТРОКОЙ и регистронезависимо."""

    def __init__(self, by_role):
        self.by_role = by_role
        self.url = "https://example.test/login"

    def title(self):
        return "Вход"

    def get_by_role(self, role, name=None, exact=False):
        entries = list(self.by_role.get(role, []))
        if name is not None:
            if exact:
                entries = [e for e in entries if e.name == name]
            else:
                entries = [e for e in entries if str(name).lower() in e.name.lower()]
        return _Locator(entries)


def test_a_password_value_never_reaches_the_observation():
    """РЕПРОДУКЦИЯ A3-11 — adapters/playwright_browser.py:52-58, 66.

    Вход: страница логина, textbox `type=password` со значением 'S3cret!'.
    Chromium отдаёт пароль как обычный textbox, `entry['value']` пишется без
    исключений, а `sensitive=False` захардкожен, поэтому redaction-конвейер
    (bossman.obs) к этому наблюдению НЕ применяется: пароль уходит в контекст
    модели и в журнал задачи открытым текстом.
    """
    page = _Page({"textbox": [_El("Логин", "owner"),
                              _El("Пароль", "S3cret!", input_type="password")]})
    obs = PlaywrightBrowserObserver(page).observe()

    flat = repr(obs.ui_tree)
    assert "S3cret!" not in flat, "пароль попал в наблюдение и уедет в журнал"
    assert obs.sensitive is True, (
        "наблюдение со значениями полей не помечено sensitive — redaction "
        "downstream не включится")


def test_an_ambiguous_semantic_target_is_refused_not_guessed():
    """РЕПРОДУКЦИЯ A3-12 — adapters/playwright_browser.py:86-89 (`_locate`).

    Вход: на странице кнопки «Save» и «Save and exit»; шаг целится в «Save».
    `get_by_role(..., exact=False)` матчит обе, `loc.first` берёт первую по
    порядку DOM — если «Save and exit» стоит раньше, оператор нажимает НЕ ТУ
    кнопку и уходит со страницы, не сохранив.
    """
    from bossman.apprentice.models import PlanStep

    save_and_exit = _El("Save and exit")
    save = _El("Save")
    page = _Page({"button": [save_and_exit, save]})
    actuator = PlaywrightBrowserActuator(page)

    step = PlanStep(kind=ActionKind.CLICK,
                    target=types.SimpleNamespace(role="button", name="Save",
                                                 label=lambda: "button 'Save'"))
    try:
        actuator._perform(step)
    except RuntimeError:
        return                       # отказ при неоднозначности — то, что нужно
    assert not save_and_exit.clicked, "нажата «Save and exit» вместо «Save»"


# ================================================================ A9-06

needs_browser = pytest.mark.skipif(not chromium_available(), reason=browser_reason())

MARKER = "<!-- вторая правка владельца -->"


@pytest.mark.timeout(180)
@needs_browser
def test_one_conflict_does_not_lock_the_code_editor_forever(live):  # noqa: F811
    """НЕГАТИВНЫЙ КОНТРОЛЬ A9-06 — ui/pages/web_designer.js (`flushSave`, catch).

    Закрытие A9-06 не было покрыто ни одним тестом. Сценарий: код проекта
    изменён мимо панели (вторая вкладка) -> набор в редакторе получает 409.
    Раньше `catch` только показывал тост, `state.meta` оставалась устаревшей, и
    КАЖДОЕ следующее нажатие клавиши повторяло тот же `base_version`: владелец
    не мог сохранить ничего до перезагрузки страницы.

    Проверено отрицательным контролем: при локальном возврате `catch` к одному
    только тосту (без `reloadState`) этот тест падает — вторая правка не
    доезжает до сервера.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            _login(page, live)
            page.evaluate("""async () => {
              const csrf = localStorage.getItem('bcc.csrf') || '';
              await fetch('/api/web-designer/projects', {method: 'POST',
                headers: {'Content-Type': 'application/json', 'X-BCC-CSRF': csrf},
                body: JSON.stringify({name: 'Конфликт', prompt: '', template: 'blank'})});
            }""")
            page.goto(live.url + "/#/web_designer")
            page.wait_for_selector("textarea.bd-code", timeout=20000)

            # Чужая вкладка сохранила свою версию: base_version панели устарел.
            page.evaluate("""async () => {
              const csrf = localStorage.getItem('bcc.csrf') || '';
              const cur = await (await fetch('/api/web-designer/projects/1')).json();
              await fetch('/api/web-designer/projects/1/code', {method: 'PUT',
                headers: {'Content-Type': 'application/json', 'X-BCC-CSRF': csrf},
                body: JSON.stringify({html: cur.code + '\\n<!-- чужая вкладка -->',
                                      base_version: cur.meta.version})});
            }""")

            # Первый набор -> гарантированный 409.
            page.fill("textarea.bd-code", "<!doctype html><html><body>1</body></html>")
            page.dispatch_event("textarea.bd-code", "input")
            page.wait_for_timeout(2500)

            # Второй набор — тот, который раньше уже не мог пройти НИКОГДА.
            page.fill("textarea.bd-code",
                      "<!doctype html><html><body>2</body></html>" + MARKER)
            page.dispatch_event("textarea.bd-code", "input")

            page.wait_for_function(
                """async () => {
                  const r = await fetch('/api/web-designer/projects/1');
                  return (await r.json()).code.includes(%r);
                }""" % MARKER,
                timeout=20000)
            assert errors == [], errors
        finally:
            browser.close()
