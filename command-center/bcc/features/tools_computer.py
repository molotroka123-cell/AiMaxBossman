"""Computer Use в Command Center: observe → target → act → re-observe → verify.

Owner audit 2026-09-21 (glm53 CP-07/08, Aster): установленный продукт не давал
модели ни одного инструмента рабочего стола. Оператор компьютера жил только в
bossman-core (`bossman.computer_operator`) и подключался к его собственному
стеку (Postgres-approvals, Gateway), которого в установке владельца нет. Это
wiring-дефект продукта, а не ограничение среды.

Здесь нет второй реализации управления мышью: используются те же адаптеры,
что и в bossman-core и что уже едут в архиве —
  * `WindowsDesktop` (pywinauto UIA + pyautogui, кириллица через буфер обмена,
    прерывание набора владельцем, проверка координат по виртуальному экрану);
  * `LocalScreenshotProvider` (кадры с окном хранения);
  * `AppLaunchAdapter` + allowlist приложений (никакого exec из вывода модели);
  * `ComputerPolicy` (лексикон последствий по УЛИКАМ экрана, запрет голых
    координат без названной цели, защищённые окна Bossman/UAC).
Подтверждения, «Стоп», журнал — канонические механизмы Command Center
(tool-loop ASK/AUTO, bus → Flight Recorder).

Контракт для модели:
  1. computer.observe → поколение `generation`, окно, элементы UIA с центрами.
  2. computer.act(generation=…) → действие ТОЛЬКО по свежему наблюдению;
     устаревшее поколение или наблюдение старше MAX_OBS_AGE_S — отказ.
  3. Цель — по имени элемента (UIA). Две одинаковые подписи — отказ, пока
     модель не укажет `index` элемента из наблюдения. Координаты — лишь
     запасной путь: нужны `coordinate_fallback=true` и имя цели; перед кликом
     экран перечитывается, и точка обязана лежать внутри названного элемента.
  4. После действия экран перечитывается сам; `expect` проверяется по новому
     наблюдению → verified true/false. Без `expect` — verified=null, а не «ок».
     Неизвестное или неверно типизированное ожидание — invalid, не «проверено».
  5. «Стоп» владельца (POST /api/computer/stop) обрывает набор между порциями,
     блокирует новые действия до «Продолжить», переживает перезапуск backend
     (файл STOP в data_dir/computer) и после «Продолжить» обесценивает все
     прежние наблюдения — старая очередь по устаревшему экрану не исполняется.
     «Стоп» выигрывает гонки (R6): каждое действие запоминает «эпоху стопа» при
     входе и перепроверяет её под замком и перед КАЖДЫМ обращением к рабочему
     столу. Действие, ждавшее замок, пока владелец жал «Стоп» (даже если он тут
     же нажал «Продолжить»), не исполняется — это касается и launch/wait, у
     которых нет generation.
  6. Разрешение на последствийное действие приходит ТОЛЬКО из доверенного
     контекста вызова (ToolContext.approval_id, строка approvals), привязано к
     ВИДУ последствия и перепроверяется по свежему экрану перед эффектом.
     Заявление модели (`semantic`, любой служебный аргумент) разрешением
     не является.
"""
from __future__ import annotations

import asyncio
import platform
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..tools import REGISTRY, ToolResult, ToolSpec
from . import Feature

router = APIRouter()

MAX_ELEMENTS = 150
MAX_VALUE_CHARS = 4000
SETTLE_S = 0.6
# Наблюдение старше этого — не основание для действия, даже с тем же generation:
# за это время владелец мог переключить окно, а экран — измениться без нас.
MAX_OBS_AGE_S = 45.0
# Зависший адаптер (COM-вызов UIA без ответа, залипший драйвер ввода) не должен
# держать замок рабочего стола вечно: исход шага объявляется НЕИЗВЕСТНЫМ, замок
# освобождается, следующий шаг обязан перечитать экран.
ACT_TIMEOUT_S = 60.0
LAUNCH_WAIT_S = 5.0
KINDS = ("focus", "click", "double_click", "type", "hotkey", "scroll", "invoke", "launch", "wait",
         "focus_window")
# Действия, которые шлют ввод в ТЕКУЩЕЕ окно переднего плана. Перед ними окно
# обязано быть тем же, что в наблюдении: живой прогон 2026-09-21 показал, что
# Windows не отдаёт фокус только что запущенному Блокноту, и набор ушёл в поиск
# «Параметров». Ввод «куда-то» — хуже отказа.
INPUT_KINDS = frozenset({"focus", "click", "double_click", "type", "hotkey", "scroll", "invoke"})
# Постусловия, которые умеет проверять verify(). Всё остальное — invalid.
EXPECT_KEYS = frozenset({"window_title_contains", "contains_text", "absent_text",
                         "file_exists", "file_contains"})
MIN_EXPECT_CHARS = 2
# Служебные аргументы, которые модель писать не может: приходят только из кода.
RESERVED_ARGS = frozenset({"_approved_consequence", "_approval_id", "_approved_kind"})
STOP_FILE = "STOP"
# «Исход неизвестен» переживает перезапуск ровно так же, как «Стоп»: backend чаще
# всего и погибает именно на зависшем действии, и вернуться с чистой памятью —
# значит поверить, что рабочий стол в известном состоянии, ничего не проверив.
UNKNOWN_FILE = "OUTCOME_UNKNOWN"


def _core():
    """Адаптеры bossman-core. ImportError — честная причина, а не падение сервиса."""
    from bossman.computer_operator.adapters.app_launch import AppLaunchAdapter
    from bossman.computer_operator.adapters.screenshot import LocalScreenshotProvider
    from bossman.computer_operator.adapters.windows import WindowsDesktop
    from bossman.computer_operator.models import (ActionKind, ComputerAction, ExpectedState,
                                                  Observation, TaskMode)
    from bossman.computer_operator.policy import ComputerPolicy
    return {"AppLaunchAdapter": AppLaunchAdapter, "LocalScreenshotProvider": LocalScreenshotProvider,
            "WindowsDesktop": WindowsDesktop, "ActionKind": ActionKind,
            "ComputerAction": ComputerAction, "ExpectedState": ExpectedState,
            "Observation": Observation, "TaskMode": TaskMode, "ComputerPolicy": ComputerPolicy}


def availability() -> tuple[bool, str]:
    if platform.system().lower() != "windows":
        return False, "управление рабочим столом доступно только на Windows"
    try:
        core = _core()
    except Exception as exc:  # noqa: BLE001
        return False, f"модуль оператора компьютера недоступен: {type(exc).__name__}: {exc}"
    missing = core["WindowsDesktop"].preflight()
    return (False, missing) if missing else (True, "")


def _element_with_rect(c) -> dict[str, Any]:
    """Элемент UIA + прямоугольник и значение (для проверки результата)."""
    e = c.element_info
    item: dict[str, Any] = {
        "name": str(getattr(e, "name", "") or "")[:300],
        "control_type": str(getattr(e, "control_type", "") or "")[:80],
        "automation_id": str(getattr(e, "automation_id", "") or "")[:200],
    }
    try:
        r = c.rectangle()
        if r.width() > 0 and r.height() > 0:
            item.update(left=r.left, top=r.top, right=r.right, bottom=r.bottom,
                        x=(r.left + r.right) // 2, y=(r.top + r.bottom) // 2)
    except Exception:  # noqa: BLE001
        pass
    if item["control_type"] in ("Edit", "Document"):
        secret = False
        try:
            secret = bool(e.element.CurrentIsPassword)
        except Exception:  # noqa: BLE001
            pass
        if secret:
            item["value"] = "(секретное поле — значение скрыто)"
        else:
            try:
                item["value"] = str(c.iface_value.CurrentValue or "")[:MAX_VALUE_CHARS]
            except Exception:  # noqa: BLE001
                try:
                    item["value"] = str(c.window_text() or "")[:MAX_VALUE_CHARS]
                except Exception:  # noqa: BLE001
                    pass
    return item


@dataclass
class ComputerState:
    # Поколение уникально для ПРОЦЕССА: после перезапуска backend числа не
    # повторяются, и generation из прошлой жизни никогда не совпадёт с текущим.
    generation: int = field(default_factory=lambda: int(time.time()) * 1000)
    session: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    last: dict[str, Any] = field(default_factory=dict)
    stop: threading.Event = field(default_factory=threading.Event)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    desktop: Any = None
    shots: Any = None
    launcher: Any = None
    launched_pid: int | None = None
    stop_path: Path | None = None
    # Исход последнего действия неизвестен (таймаут адаптера): до свежего
    # наблюдения действия запрещены.
    outcome_unknown: str = ""
    # R6: растёт на каждом «Стоп» и «Продолжить». Действие, начатое в одной
    # эпохе, не делает ни шага по рабочему столу в другой.
    stop_epoch: int = 0

    @property
    def unknown_path(self) -> Path | None:
        """Lives next to STOP: one owner directory, one lifetime."""
        return None if self.stop_path is None else self.stop_path.with_name(UNKNOWN_FILE)

    def stopped(self) -> bool:
        return self.stop.is_set()

    def mark_outcome_unknown(self, text: str) -> None:
        self.outcome_unknown = text
        self.last = {}
        path = self.unknown_path
        if path is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            except OSError:
                pass

    def clear_outcome_unknown(self) -> None:
        """Only a fresh observation may do this: the screen has been re-read."""
        self.outcome_unknown = ""
        path = self.unknown_path
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def restore(self) -> None:
        """Adopt what the previous life left on disk. «Стоп» and «исход неизвестен» both
        stand until the owner resumes / the screen is re-read — never because we restarted."""
        if self.stop_path is not None and self.stop_path.is_file():
            self.stop.set()
        path = self.unknown_path
        if path is not None:
            try:
                if path.is_file():
                    self.outcome_unknown = (path.read_text(encoding="utf-8").strip()[:500]
                                            or "исход прошлого действия неизвестен")
            except OSError:
                pass

    def set_stop(self, by: str) -> None:
        self.stop.set()
        self.stop_epoch += 1
        if self.stop_path is not None:
            try:
                self.stop_path.parent.mkdir(parents=True, exist_ok=True)
                self.stop_path.write_text(f"{by}\n{time.time()}\n", encoding="utf-8")
            except OSError:
                pass

    def clear_stop(self) -> None:
        self.stop_epoch += 1
        self.stop.clear()
        if self.stop_path is not None:
            try:
                self.stop_path.unlink(missing_ok=True)
            except OSError:
                pass
        # «Продолжить» ≠ «доиграть очередь»: всё, что планировалось по экрану
        # до «Стоп», обесценивается — модель обязана перечитать экран.
        self.generation += 1
        self.last = {}


def _state(svc) -> ComputerState:
    st = getattr(svc, "_computer_state", None)
    if st is None:
        st = ComputerState()
        st.stop_path = Path(svc.settings.data_dir) / "computer" / STOP_FILE
        # Безопасное поведение после перезапуска: «Стоп», нажатый до падения
        # backend, остаётся в силе, пока владелец сам не нажмёт «Продолжить»;
        # неизвестный исход — пока экран не перечитают.
        st.restore()
        svc._computer_state = st
    if st.desktop is None:
        core = _core()

        class _Desktop(core["WindowsDesktop"]):
            _element = staticmethod(_element_with_rect)

        st.desktop = _Desktop()
        st.desktop.set_interrupt(st.stop)
        shots_dir = Path(svc.settings.data_dir) / "computer" / "screens"
        st.shots = core["LocalScreenshotProvider"](root=shots_dir, retention=32)
        from bossman.computer_operator.adapters.app_launch import spawn_detached

        async def launcher(exe):
            proc = await spawn_detached(exe)
            st.launched_pid = int(getattr(proc, "pid", 0) or 0) or None
            return proc

        st.launcher = core["AppLaunchAdapter"](launcher=launcher)
    return st


async def observe(svc, *, screenshot: bool = True) -> dict[str, Any]:
    ok, why = availability()
    if not ok:
        raise RuntimeError(why)
    st = _state(svc)
    fg, tree = await st.desktop.snapshot()
    shot = None
    if screenshot:
        shot, _ = await st.shots.capture()
    st.generation += 1
    st.clear_outcome_unknown()
    elements = list((tree or {}).get("elements") or [])[:MAX_ELEMENTS]
    for i, el in enumerate(elements):
        el["i"] = i
    obs = {"generation": st.generation, "session": st.session, "observed_at": time.time(),
           "window": {k: fg.get(k) for k in ("title", "app", "handle", "pid", "error") if k in fg},
           "elements": elements, "screenshot": shot, "stopped": st.stopped()}
    st.last = obs
    await svc.bus.emit("computer.observe", generation=st.generation,
                       window=str(fg.get("title") or "")[:200], elements=len(elements),
                       screenshot=shot)
    return obs


def _haystack(obs: dict) -> str:
    parts = [str((obs.get("window") or {}).get("title") or "")]
    for el in obs.get("elements") or []:
        parts.append(str(el.get("name") or ""))
        if el.get("value"):
            parts.append(str(el["value"]))
    return "\n".join(parts)


def verify(obs: dict, expect: Any, *, started_at: float | None = None) -> tuple[bool | None, list[str]]:
    """Постусловие по СВЕЖЕМУ наблюдению.

    Возвращает (verified, notes):
      * None  — постусловие не задано: результат НЕ проверен (не «ок»);
      * False — хотя бы одна проверка не подтвердилась ИЛИ ожидание невалидно
        (не объект, неизвестное поле, не строка, короче MIN_EXPECT_CHARS);
      * True  — только когда выполнена ХОТЯ БЫ ОДНА известная проверка и все
        выполненные проверки подтвердились.
    Заголовок окна сам по себе не доказывает сохранение файла: для этого есть
    `file_exists`/`file_contains`, которые читают диск, а не экран.
    """
    if expect is None:
        return None, ["постусловие не задано — результат не проверен"]
    if not isinstance(expect, dict):
        return False, [f"expect должен быть объектом, получено {type(expect).__name__} — не проверено"]
    cleaned: dict[str, str] = {}
    invalid: list[str] = []
    for k, v in expect.items():
        key = str(k)
        if key not in EXPECT_KEYS:
            invalid.append(f"неизвестное поле expect «{key}» (допустимы: {', '.join(sorted(EXPECT_KEYS))})")
            continue
        if not isinstance(v, str):
            invalid.append(f"expect.{key}: ожидается строка, получено {type(v).__name__}")
            continue
        if len(v.strip()) < MIN_EXPECT_CHARS:
            invalid.append(f"expect.{key}: слишком короткое условие (минимум {MIN_EXPECT_CHARS} символа)")
            continue
        cleaned[key] = v.strip()
    if invalid:
        return False, invalid + ["ожидание невалидно — результат НЕ подтверждён"]
    if not cleaned:
        return None, ["постусловие пустое — результат не проверен"]
    title = str((obs.get("window") or {}).get("title") or "").lower()
    hay = _haystack(obs).lower()
    notes: list[str] = []
    ok = True
    checks = 0
    if "window_title_contains" in cleaned:
        hit = cleaned["window_title_contains"].lower() in title
        ok &= hit
        checks += 1
        notes.append(f"заголовок {'содержит' if hit else 'НЕ содержит'} "
                     f"«{cleaned['window_title_contains']}»")
    if "contains_text" in cleaned:
        hit = cleaned["contains_text"].lower() in hay
        ok &= hit
        checks += 1
        notes.append(f"на экране {'есть' if hit else 'НЕТ'} «{cleaned['contains_text']}»")
    if "absent_text" in cleaned:
        hit = cleaned["absent_text"].lower() not in hay
        ok &= hit
        checks += 1
        notes.append(f"«{cleaned['absent_text']}» {'отсутствует' if hit else 'ВСЁ ЕЩЁ на экране'}")
    if "file_exists" in cleaned or "file_contains" in cleaned:
        raw = cleaned.get("file_exists") or ""
        target = Path(raw) if raw else None
        if "file_contains" in cleaned and target is None:
            ok = False
            checks += 1
            notes.append("file_contains требует file_exists с путём к файлу")
        elif target is not None:
            checks += 1
            if not target.is_absolute():
                ok = False
                notes.append(f"file_exists: путь «{raw}» не абсолютный — не проверено")
            elif not target.is_file():
                ok = False
                notes.append(f"файл «{raw}» НЕ существует")
            else:
                try:
                    mtime = target.stat().st_mtime
                except OSError as exc:
                    ok = False
                    notes.append(f"файл «{raw}»: {type(exc).__name__}")
                else:
                    fresh = started_at is None or mtime >= started_at - 1.0
                    ok &= fresh
                    notes.append(f"файл «{raw}» {'записан после действия' if fresh else 'СТАРЕЕ действия (не сохранён им)'}")
                    if "file_contains" in cleaned:
                        try:
                            body = target.read_text(encoding="utf-8", errors="replace")
                        except OSError as exc:
                            ok = False
                            notes.append(f"файл «{raw}» не читается: {type(exc).__name__}")
                        else:
                            hit = cleaned["file_contains"] in body
                            ok &= hit
                            notes.append(f"в файле {'есть' if hit else 'НЕТ'} «{cleaned['file_contains']}»")
    if checks == 0:
        return None, ["ни одна проверка не выполнена — результат не проверен"]
    return bool(ok), notes


def _find(obs: dict, target: str, index: Any = None) -> list[dict]:
    """Элементы по имени. `index` (номер из наблюдения) снимает неоднозначность
    и ОБЯЗАН указывать на элемент с этим же именем."""
    t = target.strip().lower()
    elements = list(obs.get("elements") or [])
    if isinstance(index, int) and not isinstance(index, bool):
        el = next((e for e in elements if e.get("i") == index), None)
        if el is None or str(el.get("name") or "").strip().lower() != t:
            return []
        return [el]
    exact = [e for e in elements if str(e.get("name") or "").strip().lower() == t]
    return exact or [e for e in elements if t and t in str(e.get("name") or "").lower()]


def _render_obs(obs: dict) -> str:
    w = obs.get("window") or {}
    lines = [f"generation: {obs['generation']}",
             f"окно: {w.get('title') or '(нет активного окна)'}"
             + (f" [{w.get('error')}]" if w.get("error") else ""),
             f"скриншот: {obs.get('screenshot') or 'не снят'}",
             "элементы (name | тип | центр x,y):"]
    for el in obs.get("elements") or []:
        xy = f"{el['x']},{el['y']}" if "x" in el else "-"
        val = f" = «{str(el['value'])[:200]}»" if el.get("value") else ""
        lines.append(f"[{el['i']}] {el.get('name') or '(без имени)'} | {el.get('control_type')} | {xy}{val}")
    if obs.get("stopped"):
        lines.append("\nВЛАДЕЛЕЦ НАЖАЛ «СТОП» — действия запрещены до «Продолжить».")
    return "\n".join(lines)


class ActRefused(RuntimeError):
    pass


def _top_windows() -> list[tuple[int, str, int]]:
    """(handle, title, pid) видимых окон верхнего уровня."""
    from pywinauto import Desktop
    out = []
    for w in Desktop(backend="uia").windows():
        try:
            if w.is_visible():
                try:
                    pid = int(w.process_id() or 0)
                except Exception:  # noqa: BLE001
                    pid = 0
                out.append((int(w.handle), str(w.window_text() or ""), pid))
        except Exception:  # noqa: BLE001
            continue
    return out


def _process_name(pid: int) -> str:
    try:
        import psutil
        return str(psutil.Process(pid).name() or "").lower()
    except Exception:  # noqa: BLE001
        return ""


def _expected_exes(app: str) -> set[str]:
    try:
        from bossman.computer_operator.applist import APP_ALLOWLIST
        return {n.lower() for n in APP_ALLOWLIST.get(app, {}).get("windows", ())}
    except Exception:  # noqa: BLE001
        return set()


def _is_hosted_calculator_exe(path: str) -> bool:
    """Windows Calculator's real UWP executable, beneath its package directory."""
    exe = PureWindowsPath(path)
    return (exe.name.casefold() == "calculatorapp.exe"
            and exe.parent.name.casefold().startswith("microsoft.windowscalculator_")
            and exe.parent.parent.name.casefold() == "windowsapps")


def _hosted_calculator_window(handle: int) -> bool:
    """Verify the app process inside an ApplicationFrameHost window.

    Windows Calculator's top-level UIA PID belongs to the shared frame host,
    not to CalculatorApp.exe. The newly created window is attributable only
    when its direct CoreWindow child belongs to the Calculator package.
    """
    try:
        import psutil
        from pywinauto import Desktop

        window = Desktop(backend="uia").window(handle=handle)
        if window.class_name() != "ApplicationFrameWindow":
            return False
        for child in window.children():
            if child.class_name() != "Windows.UI.Core.CoreWindow":
                continue
            if _is_hosted_calculator_exe(psutil.Process(int(child.process_id())).exe()):
                return True
    except Exception:  # noqa: BLE001 — uncertain identity is a refusal
        return False
    return False


def attribute_new_window(new: list[tuple[int, str, int]], *, launched_pid: int | None,
                         expected_exes: set[str], process_name=_process_name,
                         hosted_app: str | None = None,
                         hosted_window_matcher=_hosted_calculator_window) -> tuple[int, str] | None:
    """Какое из НОВЫХ окон принадлежит запущенному приложению.

    Первое попавшееся новое окно — не ответ: за 5 с могло всплыть чужое
    (уведомление, чужой установщик, Параметры). Окно принимается, если его
    процесс — тот, что мы запустили, или процесс с ожидаемым именем
    исполняемого файла из allowlist (Win11 Notepad перезапускает себя другим
    процессом). Иначе — None: отказ, а не догадка.
    """
    for h, title, pid in new:
        if launched_pid and pid == launched_pid:
            return h, title
    for h, title, pid in new:
        if pid and expected_exes and process_name(pid) in expected_exes:
            return h, title
    if hosted_app == "calculator":
        for h, title, pid in new:
            if (pid and process_name(pid) == "applicationframehost.exe"
                    and hosted_window_matcher(h)):
                return h, title
    return None


def _set_foreground(handle: int) -> None:
    from pywinauto import Desktop
    w = Desktop(backend="uia").window(handle=handle)
    try:
        if w.is_minimized():
            w.restore()
    except Exception:  # noqa: BLE001
        pass
    w.set_focus()


def _focus_editable(handle: int, name: str) -> bool:
    """Фокус на РЕДАКТИРУЕМОМ элементе с этим именем в окне handle."""
    from pywinauto import Desktop
    w = Desktop(backend="uia").window(handle=handle) if handle else Desktop(backend="uia").top_window()
    for ctype in ("Edit", "Document", None):
        try:
            cands = w.descendants(title=name, control_type=ctype) if ctype else w.descendants(title=name)
        except Exception:  # noqa: BLE001
            cands = []
        for c in cands:
            try:
                if not c.is_enabled() or not c.is_visible():
                    continue
                c.set_focus()
                if c.has_keyboard_focus() or ctype is None:
                    return True
            except Exception:  # noqa: BLE001
                continue
    return False


async def _focus(st: "ComputerState", handle: int) -> bool:
    await asyncio.to_thread(_set_foreground, handle)
    await asyncio.sleep(0.3)
    fg = await st.desktop.foreground()
    return int(fg.get("handle") or 0) == int(handle)


def _stop_check(st: ComputerState, phase: str, epoch: int | None = None) -> None:
    if st.stopped():
        raise ActRefused(f"владелец нажал «Стоп» ({phase}): действия на рабочем столе "
                         f"остановлены до «Продолжить»")
    if epoch is not None and st.stop_epoch != epoch:
        raise ActRefused(f"владелец нажимал «Стоп», пока действие ждало очереди или шло ({phase}) — "
                         f"действие отменено; вызовите computer.observe и решите заново "
                         f"по свежему экрану")


def _policy_observation(core, obs: dict, generation: int):
    fg = dict((obs or {}).get("window") or {})
    return core["Observation"]("obs", time.time(), fg, "", None, None, False, generation)


async def _bounded(st: ComputerState, coro, what: str):
    """Адаптер с таймаутом: зависание = НЕИЗВЕСТНЫЙ исход, не вечный замок."""
    try:
        return await asyncio.wait_for(coro, timeout=ACT_TIMEOUT_S)
    except asyncio.TimeoutError:
        st.mark_outcome_unknown(f"{what} не ответил за {ACT_TIMEOUT_S:.0f} с — исход неизвестен")
        raise ActRefused(f"{st.outcome_unknown}; перечитайте экран (computer.observe) "
                         f"прежде чем действовать дальше")


async def act(svc, args: dict, *, approved_kind: str | None = None,
              approval_ref: int | None = None) -> dict[str, Any]:
    """Одно действие по свежему наблюдению + автоматическая проверка результата.

    `approved_kind` — вид последствия (например "delete"), который владелец
    одобрил ДЛЯ ЭТОГО вызова; приходит из доверенного контекста, не из args.
    """
    ok, why = availability()
    if not ok:
        raise ActRefused(why)
    core = _core()
    AK = core["ActionKind"]
    st = _state(svc)
    args = {k: v for k, v in dict(args or {}).items() if k not in RESERVED_ARGS}
    kind = str(args.get("action") or "").strip().lower()
    if kind not in KINDS:
        raise ActRefused(f"action: одно из {', '.join(KINDS)}")
    # R6: эпоха стопа фиксируется при входе; любой «Стоп» (и «Продолжить») после
    # этого момента отменяет оставшиеся шаги действия.
    epoch = st.stop_epoch
    _stop_check(st, "до очереди", epoch)
    target = str(args.get("target") or "").strip()
    started_at = time.time()
    async with st.lock:                            # один рабочий стол — одно действие за раз
        # «Стоп» мог прийти, пока действие ждало замок: очередь из двух действий,
        # STOP между ними — второе не исполняется.
        _stop_check(st, "после ожидания очереди", epoch)
        if st.outcome_unknown:
            raise ActRefused(f"исход прошлого действия неизвестен ({st.outcome_unknown}) — "
                             f"сначала computer.observe")
        if kind not in ("launch", "wait"):
            gen = args.get("generation")
            if (not st.last or not isinstance(gen, int) or isinstance(gen, bool)
                    or gen != st.generation):
                raise ActRefused(
                    f"наблюдение устарело (generation={gen!r}, текущее {st.generation}): "
                    f"вызовите computer.observe и действуйте по свежему экрану")
            age = time.time() - float(st.last.get("observed_at") or 0)
            if age > MAX_OBS_AGE_S:
                raise ActRefused(f"наблюдение старше {MAX_OBS_AGE_S:.0f} с ({age:.0f} с) — "
                                 f"перечитайте экран (computer.observe)")
        before = st.last
        if kind in INPUT_KINDS:
            _stop_check(st, "проверка окна", epoch)
            fg_now = await _bounded(st, st.desktop.foreground(), "проверка окна")
            want = (before.get("window") or {}).get("handle")
            if want and int(fg_now.get("handle") or 0) != int(want):
                raise ActRefused(
                    f"фокус ушёл: наблюдалось окно «{(before.get('window') or {}).get('title')}», "
                    f"а сейчас впереди «{fg_now.get('title')}». Ввод не отправлен — вызовите "
                    f"computer.observe (или focus_window) и действуйте по свежему экрану")
        x, y = args.get("x"), args.get("y")
        coords = (isinstance(x, int) and isinstance(y, int)
                  and not isinstance(x, bool) and not isinstance(y, bool))
        index = args.get("index")
        if index is not None and (not isinstance(index, int) or isinstance(index, bool)):
            raise ActRefused("index: целое число элемента из наблюдения")
        if target and kind in ("click", "double_click", "invoke", "focus") and not coords:
            hits = _find(before, target, index)
            exact = [e for e in hits if str(e.get("name") or "").strip().lower() == target.lower()]
            if len(exact) > 1:
                raise ActRefused(
                    f"на экране {len(exact)} элемента с именем «{target}» "
                    f"(индексы {', '.join(str(e.get('i')) for e in exact)}) — укажите index")
            if index is not None and not hits:
                raise ActRefused(f"элемент [{index}] не называется «{target}» в наблюдении "
                                 f"{before.get('generation')} — цель не подтверждена")
            if index is not None and hits and "x" in hits[0] and kind in ("click", "double_click"):
                # Однозначная цель по индексу: клик в центр ЭТОГО элемента через
                # координатный путь с повторным чтением экрана (ниже).
                x, y, coords = hits[0]["x"], hits[0]["y"], True
                args["coordinate_fallback"] = True
        mapping = {"focus": AK.FOCUS, "click": AK.UI_INVOKE if (target and not coords) else AK.CLICK,
                   "double_click": AK.DOUBLE_CLICK, "type": AK.TYPE, "hotkey": AK.HOTKEY,
                   "scroll": AK.SCROLL, "invoke": AK.UI_INVOKE, "launch": AK.APP_LAUNCH,
                   "wait": AK.WAIT, "focus_window": AK.WAIT}
        action_args = {k: args[k] for k in ("keys", "clicks", "coordinate_fallback", "semantic",
                                             "interval") if k in args}
        if coords:
            action_args["x"], action_args["y"] = x, y
        # Уверенность НЕ самозаявленная: координатный путь считается обоснованным
        # только после проверки попадания в названный элемент на свежем экране.
        a = core["ComputerAction"].make(
            mapping[kind], target=target or None, text=args.get("text"), args=action_args,
            confidence=1.0, source="planner")
        pol_obs = _policy_observation(core, before, st.generation)
        policy = core["ComputerPolicy"]()
        decision = policy.classify(a, mode=core["TaskMode"].CONTROL, observation=pol_obs)
        if not decision.allow:
            raise ActRefused(f"политика запретила действие: {decision.reason}")
        required = (decision.approval_kind or "").removeprefix("computer_") if decision.requires_approval else None
        if required and approved_kind != required:
            if approved_kind:
                raise ActRefused(
                    f"одобрено последствие «{approved_kind}», а действие ведёт к «{required}» "
                    f"({decision.reason}) — одобрение не переносится, повторите вызов с "
                    f"semantic=\"{required}\"")
            raise ActRefused(
                f"действие последствийное ({decision.reason}) — повторите вызов с "
                f"semantic=\"{required}\": тогда оно пройдёт через подтверждение владельца")

        if kind in ("click", "double_click") and coords:
            # Координатный запасной путь: ПЕРЕД кликом перечитываем экран и
            # требуем, чтобы точка лежала внутри названного элемента.
            _stop_check(st, "перечитывание экрана", epoch)
            fresh = await _bounded(st, observe(svc, screenshot=False), "наблюдение")
            hits = [e for e in _find(fresh, target, index) if "left" in e
                    and e["left"] <= x <= e["right"] and e["top"] <= y <= e["bottom"]]
            if not hits:
                raise ActRefused(f"координаты ({x},{y}) не попадают в элемент «{target}» на "
                                 f"свежем экране (generation {fresh['generation']}) — клик не выполнен")
            if len(hits) > 1:
                raise ActRefused(f"в точке ({x},{y}) на свежем экране {len(hits)} элемента «{target}» "
                                 f"— цель неоднозначна, клик не выполнен")
            before = fresh
        elif kind == "type" and target:
            # Живой прогон 2026-09-21: «Имя файла:» в диалоге сохранения — это и
            # ComboBox, и Edit внутри; фокус на обёртке, и вставка уходила в никуда.
            # Для ввода выбираем РЕДАКТИРУЕМЫЙ элемент и проверяем, что фокус на нём.
            handle = int((before.get("window") or {}).get("handle") or 0)
            _stop_check(st, "фокус поля", epoch)
            if not await asyncio.to_thread(_focus_editable, handle, target):
                raise ActRefused(f"поле «{target}» не найдено или не принимает ввод — ничего не введено")
        if kind in ("type", "hotkey") and not target:
            # Живой прогон 2026-09-21: после фокуса окна Блокнот (Win11) держит
            # клавиатурный фокус на рамке/вкладках, и ввод пропадал без следа.
            # Единственное поле ввода окна — однозначная цель; при нескольких
            # модель обязана назвать target сама.
            editable = [e for e in (before or {}).get("elements") or []
                        if e.get("control_type") in ("Document", "Edit") and e.get("name")]
            if len(editable) == 1:
                _stop_check(st, "фокус поля", epoch)
                await _bounded(st, st.desktop.execute(
                    core["ComputerAction"].make(AK.FOCUS, target=editable[0]["name"]), None), "фокус поля")
            elif kind == "type" and len(editable) > 1:
                raise ActRefused("в окне несколько полей ввода: "
                                 + ", ".join(f"«{e['name']}»" for e in editable[:8])
                                 + " — укажите target")

        if required:
            # Одобрение привязано к экрану: перед эффектом политика
            # пересматривается по СВЕЖЕМУ окну. Если экран сменился так, что
            # последствие уже другое (или окно защищено) — отказ, не эффект.
            _stop_check(st, "перечитывание экрана", epoch)
            fresh = await _bounded(st, observe(svc, screenshot=False), "наблюдение")
            d2 = policy.classify(a, mode=core["TaskMode"].CONTROL,
                                 observation=_policy_observation(core, fresh, st.generation))
            if not d2.allow:
                raise ActRefused(f"перед эффектом политика запретила действие: {d2.reason}")
            now_required = (d2.approval_kind or "").removeprefix("computer_") if d2.requires_approval else None
            if now_required != required:
                raise ActRefused(f"экран изменился: одобрено «{required}», сейчас последствие "
                                 f"«{now_required or 'нет'}» — одобрение не применено")
            fg_now = fresh.get("window") or {}
            if (before.get("window") or {}).get("handle") and int(fg_now.get("handle") or 0) != \
                    int((before.get("window") or {}).get("handle") or 0):
                raise ActRefused("окно сменилось после одобрения — одобрение не применено")
            before = fresh
        # Последняя проверка «Стоп» — непосредственно перед эффектом.
        _stop_check(st, "перед эффектом", epoch)
        if kind == "type" and args.get("replace"):
            await _bounded(st, st.desktop.execute(core["ComputerAction"].make(
                AK.HOTKEY, args={"keys": ["ctrl", "a"]}), None), "выделение")
        t0 = time.perf_counter()
        if kind == "wait":
            deadline = time.monotonic() + min(10.0, max(0.0, float(args.get("seconds") or 1)))
            while True:
                _stop_check(st, "ожидание", epoch)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(0.1, remaining))
        elif kind == "launch":
            from bossman.computer_operator.applist import canonical_app
            app = canonical_app(target)
            known = {h for h, _, _ in await asyncio.to_thread(_top_windows)}
            st.launched_pid = None
            _stop_check(st, "запуск", epoch)
            await _bounded(st, st.launcher.execute(a, None), "запуск")
            # Windows не отдаёт передний план процессу, запущенному из фона:
            # находим окно ЗАПУЩЕННОГО приложения и переводим фокус на него явно.
            chosen = None
            seen_new: list[tuple[int, str, int]] = []
            for _ in range(int(LAUNCH_WAIT_S / 0.25)):
                await asyncio.sleep(0.25)
                seen_new = [w for w in await asyncio.to_thread(_top_windows) if w[0] not in known]
                chosen = attribute_new_window(seen_new, launched_pid=st.launched_pid,
                                              expected_exes=_expected_exes(app or ""),
                                              hosted_app=app)
                if chosen:
                    break
            if not chosen:
                foreign = "; ".join(f"«{t}»" for _, t, _ in seen_new[:4])
                raise ActRefused(
                    f"«{target}» запущен, но окно запущенного приложения не появилось за "
                    f"{LAUNCH_WAIT_S:.0f} с"
                    + (f" (новые чужие окна: {foreign} — не трогаем)" if foreign else ""))
            _stop_check(st, "фокус окна", epoch)
            if not await _focus(st, chosen[0]):
                raise ActRefused(f"окно «{chosen[1]}» появилось, но фокус получить не удалось")
        elif kind == "focus_window":
            if not target:
                raise ActRefused("focus_window: нужен target — часть заголовка окна")
            wins = [(h, t) for h, t, _ in await asyncio.to_thread(_top_windows)
                    if target.lower() in t.lower()]
            if len(wins) != 1:
                raise ActRefused(f"окон с «{target}» в заголовке: {len(wins)} — "
                                 + ("уточните заголовок" if wins else "такого окна нет"))
            _stop_check(st, "фокус окна", epoch)
            if not await _focus(st, wins[0][0]):
                raise ActRefused(f"не удалось вывести «{wins[0][1]}» на передний план")
        else:
            _stop_check(st, "действие", epoch)
            await _bounded(st, st.desktop.execute(a, None), "действие")
        await asyncio.sleep(SETTLE_S)
        after = await _bounded(st, observe(svc), "наблюдение")
        verified, notes = verify(after, args.get("expect"), started_at=started_at)
        result = {"action": kind, "target": target, "coordinates": [x, y] if coords else None,
                  "before_generation": (before or {}).get("generation"),
                  "after_generation": after["generation"], "verified": verified, "checks": notes,
                  "approved_kind": approved_kind if required else None, "approval_ref": approval_ref,
                  "elapsed_ms": int((time.perf_counter() - t0) * 1000), "observation": after}
    await svc.bus.emit("computer.act", action=kind, target=target[:200], verified=verified,
                       before=result["before_generation"], after=result["after_generation"],
                       approved_kind=result["approved_kind"], approval_id=approval_ref,
                       window=str((after.get("window") or {}).get("title") or "")[:200])
    return result


# ------------------------------------------------------------------ tools

async def _t_observe(args, ctx):
    try:
        obs = await observe(ctx.svc)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(content=f"наблюдение недоступно: {exc}", one_line="computer.observe: нет",
                          error=True)
    return ToolResult(content=_render_obs(obs), one_line=f"computer: {obs['window'].get('title')}",
                      data={"generation": obs["generation"], "screenshot": obs["screenshot"]},
                      external=True)


def approved_consequence(args: dict, ctx) -> tuple[str | None, int | None]:
    """Что владелец одобрил для ЭТОГО вызова — из доверенного контекста.

    Движок исполняет вызов с `ctx.approval_id`, когда строка approvals решена
    владельцем; `effect_hook` ниже поднимает ASK ровно для заявленного вида
    последствия, и одобрение привязано к digest'у аргументов. Поэтому вид
    одобренного последствия = заявленный вид, и только при наличии approval_id.
    Без approval_id (AUTO, вызов вне движка) одобрения НЕТ, что бы ни писала
    модель в semantic или служебных полях.
    """
    from bossman.computer_operator.policy import ComputerPolicy
    approval_id = getattr(ctx, "approval_id", None)
    if approval_id is None:
        return None, None
    # CU-APPROVAL (canonical line): одобренное последствие = то, что effect-hook
    # МОГ показать владельцу — заявленный semantic ЛИБО подпись цели/текста.
    declared = ComputerPolicy.ask_consequence(args or {})
    return declared, int(approval_id)


async def _t_act(args, ctx):
    args = {k: v for k, v in dict(args or {}).items() if k not in RESERVED_ARGS}
    approved_kind, approval_ref = approved_consequence(args, ctx)
    try:
        res = await act(ctx.svc, args, approved_kind=approved_kind, approval_ref=approval_ref)
    except ActRefused as exc:
        return ToolResult(content=f"действие не выполнено: {exc}", one_line="computer.act: отказ",
                          error=True)
    except Exception as exc:  # noqa: BLE001 — отказ адаптера: исход назван, не «успех»
        return ToolResult(content=f"действие сорвалось: {type(exc).__name__}: {exc}. Перечитайте "
                                  f"экран (computer.observe) перед следующим шагом.",
                          one_line="computer.act: ошибка", error=True)
    head = {True: "ПРОВЕРЕНО", False: "НЕ ПОДТВЕРДИЛОСЬ", None: "НЕ ПРОВЕРЕНО"}[res["verified"]]
    text = (f"{res['action']} «{res['target']}» выполнено. Результат: {head}. "
            + "; ".join(res["checks"]) + "\n\nНовый экран:\n" + _render_obs(res["observation"]))
    return ToolResult(content=text, one_line=f"computer.{res['action']}: {head}",
                      error=res["verified"] is False,
                      data={k: res[k] for k in ("verified", "after_generation", "checks")},
                      external=True)


def _act_effect(args: dict):
    from bossman.computer_operator.policy import ComputerPolicy
    # semantic ЛИБО подпись цели/текста: «click» на «Delete account» тоже ASK.
    declared = ComputerPolicy.ask_consequence(args or {})
    if declared:
        return ("ask", f"последствийное действие на рабочем столе: {declared}")
    return None


SPECS = [
    ToolSpec(name="computer.observe",
             description="Посмотреть на рабочий стол: активное окно, элементы (имя, тип, центр x,y, "
                         "значение полей) и скриншот. Возвращает generation для computer.act.",
             handler=_t_observe, input_schema={}, category="read", permission="computer.observe",
             source="computer", default_effect="ask", timeout_seconds=60.0, external_output=True),
    ToolSpec(name="computer.act",
             description="Одно действие на рабочем столе по свежему наблюдению (generation). "
                         "action: focus|click|double_click|type|hotkey|scroll|invoke|launch|wait|"
                         "focus_window (target = часть заголовка окна). Ввод идёт только в "
                         "окно из наблюдения — если фокус ушёл, отказ. "
                         "Цель — имя элемента (target); при двух одинаковых именах укажите index "
                         "из наблюдения. Координаты x,y только с coordinate_fallback=true и target. "
                         "expect: {window_title_contains, contains_text, absent_text, file_exists "
                         "(абсолютный путь), file_contains} проверяется по новому экрану/диску; "
                         "неизвестные поля делают результат НЕ подтверждённым. launch: только "
                         "notepad/calculator. Последствийное (удалить/оплатить/отправить) — "
                         "semantic=<вид>, пойдёт через подтверждение владельца; само слово "
                         "semantic разрешением не является.",
             handler=_t_act,
             input_schema={"action": {"type": "string", "enum": list(KINDS)},
                           "generation": {"type": "integer"},
                           "target": {"type": "string"}, "text": {"type": "string"},
                           "index": {"type": "integer",
                                     "description": "номер элемента из наблюдения при одинаковых именах"},
                           "keys": {"type": "array", "items": {"type": "string"}},
                           "x": {"type": "integer"}, "y": {"type": "integer"},
                           "coordinate_fallback": {"type": "boolean"},
                           "clicks": {"type": "integer"}, "seconds": {"type": "number"},
                           "replace": {"type": "boolean",
                                       "description": "type: сначала выделить всё (Ctrl+A)"},
                           "semantic": {"type": "string"},
                           "expect": {"type": "object"}},
             required=["action"], category="write", permission="computer.control",
             source="computer", default_effect="ask", timeout_seconds=180.0, idempotent=False,
             external_output=True, effect_hook=_act_effect),
]


# ------------------------------------------------------------------ HTTP (панель владельца)

@router.get("/computer/status")
async def http_status(request: Request):
    ok, why = availability()
    svc = request.app.state.svc
    st = getattr(svc, "_computer_state", None)
    if st is None:
        # Состояние ещё не создано, но файл STOP с прошлой жизни — уже факт.
        stop_path = Path(svc.settings.data_dir) / "computer" / STOP_FILE
        stopped = stop_path.is_file()
        unknown = stop_path.with_name(UNKNOWN_FILE).is_file()
    else:
        stopped, unknown = st.stopped(), bool(st.outcome_unknown)
    return {"available": ok, "detail": why or "Windows UIA + pyautogui готовы",
            "stopped": stopped, "generation": st.generation if st else 0,
            "session": st.session if st else None,
            # R6: занят ли рабочий стол (действие в полёте) и эпоха стопа — для панели.
            "busy": bool(st and st.lock.locked()), "stop_epoch": st.stop_epoch if st else 0,
            "outcome_unknown": ((st.outcome_unknown if st else "") or None
                                if st is not None else
                                ("исход прошлого действия неизвестен" if unknown else None)),
            "tools": [s.name for s in SPECS]}


def _owner_state(svc) -> ComputerState:
    st = getattr(svc, "_computer_state", None)
    if st is None:
        st = ComputerState()
        st.stop_path = Path(svc.settings.data_dir) / "computer" / STOP_FILE
        st.restore()
        svc._computer_state = st
    return st


@router.post("/computer/observe")
async def http_observe(request: Request):
    try:
        return await observe(request.app.state.svc)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, {"message": f"наблюдение недоступно: {exc}"})


@router.post("/computer/stop")
async def http_stop(request: Request):
    """«Стоп» владельца: обрывает набор текста, блокирует новые действия,
    переживает перезапуск backend (файл STOP)."""
    svc = request.app.state.svc
    st = _owner_state(svc)
    st.set_stop("owner")
    await svc.bus.emit("computer.stop", by="owner")
    return {"stopped": True, "persisted": bool(st.stop_path and st.stop_path.is_file())}


@router.post("/computer/resume")
async def http_resume(request: Request):
    """«Продолжить» владельца. Только через панель (сессия + CSRF); у модели нет
    инструмента, который сюда доходит. Все прежние наблюдения обесцениваются."""
    svc = request.app.state.svc
    st = _owner_state(svc)
    st.clear_stop()
    await svc.bus.emit("computer.resume", by="owner", generation=st.generation)
    return {"stopped": False, "generation": st.generation}


async def _setup(svc) -> None:
    for spec in SPECS:
        REGISTRY.register(spec)


FEATURE = Feature(name="tools_computer", router=router, setup=_setup)
