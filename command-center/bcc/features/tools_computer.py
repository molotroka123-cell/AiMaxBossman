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
  7. rc19: КАЖДОЕ действие, кроме `wait`, — отдельное одобрение владельца
     (строка approvals, принятая движком к исполнению, моложе APPROVAL_TTL_S,
     не использованная раньше; журнал переживает перезапуск). Право агента,
     правило политики и аренда его не заменяют. Одобренное действие
     исполняется по наблюдению, о котором спрашивали, если оно ещё
     действительно, окно то же и цель находится на свежем экране.
  8. rc19: ввод и focus_window — только в окна процессов из allowlist
     (Блокнот, Калькулятор); Win-клавиши, платежи и учётные данные (включая
     поле пароля по UIA IsPassword) не исполняются ни с каким одобрением.
  9. 2026-10-10 (разбор задачи 83): приложения ЗАДАЧИ. Владелец (только через
     панель/CLI: PUT /api/computer/tasks/{id}/apps) разрешает задаче набор
     приложений из каталога applist.APP_CATALOG; с таким разрешением ввод,
     focus_window и адресное наблюдение доходят ТОЛЬКО до окон, которые
     запустила эта же задача (и их диалогов), а чужие окна того же процесса
     (Win11 Блокнот держит все окна в одном процессе) не читаются и не
     трогаются. Без разрешения действует прежний набор (Блокнот/Калькулятор).
 10. Привязка к окну: каждое наблюдение называет окно точными hwnd и pid.
     Одобряемое действие ввода и focus_window обязано нести `window`+`pid`
     (они попадают в digest одобрения и в вопрос владельцу) и совпасть с
     окном наблюдения. Перед эффектом окно перечитывается адресно: другой
     hwnd/pid, изменившиеся заголовок/вкладки/содержимое или окно не впереди
     — отказ с требованием нового наблюдения и нового одобрения. Фокус
     сервер сам не возвращает: focus_window — отдельное одобренное действие,
     и отказ Windows отдать фокус — отказ, без обходных приёмов.
 11. Секреты в тексте (ключи, токены, номера карт), платежи и необратимые
     внешние действия (send/upload/deploy/push/merge/release/uninstall) не
     исполняются ни с каким одобрением.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
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
                         "file_exists", "file_contains", "file_sha256"})
MIN_EXPECT_CHARS = 2
# Служебные аргументы, которые модель писать не может: приходят только из кода.
RESERVED_ARGS = frozenset({"_approved_consequence", "_approval_id", "_approved_kind"})
STOP_FILE = "STOP"
# «Исход неизвестен» переживает перезапуск ровно так же, как «Стоп»: backend чаще
# всего и погибает именно на зависшем действии, и вернуться с чистой памятью —
# значит поверить, что рабочий стол в известном состоянии, ничего не проверив.
UNKNOWN_FILE = "OUTCOME_UNKNOWN"

# ------------------------------------------------------------------ rc19 hardening
# CU-ONESHOT: каждое действие на рабочем столе, кроме пассивного `wait`, требует
# СВОЕГО одобрения владельца — строки approvals, которую движок принял к
# исполнению (approved → consumed). Право агента `computer.control`, правило
# `tool_rules`, аренда (lease) одобрением не являются.
MUTATION_FREE_KINDS = frozenset({"wait"})
APPROVAL_ROW_KINDS = frozenset({"tool", "effect_reconciliation"})
# Одобрение живёт ограниченно: от создания вопроса до эффекта. Переменная
# окружения может только СОКРАТИТЬ срок (приёмочный прогон), но не продлить.
APPROVAL_TTL_S = 300.0
APPROVAL_TTL_ENV = "BCC_COMPUTER_APPROVAL_TTL_S"
MIN_APPROVAL_TTL_S = 5.0
# Однажды использованное одобрение не предъявляется второй раз даже в обход
# движка; журнал переживает перезапуск backend.
USED_APPROVALS_FILE = "USED_APPROVALS.json"
USED_APPROVALS_MAX = 5000
# Наблюдения, по которым владелец мог одобрить действие. «Продолжить»,
# неизвестный исход и перезапуск их обнуляют — одобренное по старому экрану
# после этого не исполняется.
OBS_HISTORY = 16
# CU-ALLOWLIST: ввод идёт только в окна процессов из allowlist приложений
# (bossman.computer_operator.applist) — Блокнот и проверенный Калькулятор.
# Окно оболочки (cmd/PowerShell/Terminal), браузер, мессенджер — не цель.
HOSTED_FRAME_PROCESS = "applicationframehost.exe"
# Клавиши, которые открывают оболочку Windows поверх любого окна (Win+R, Win+X,
# меню «Пуск», диспетчер задач): ни одна из них не нужна для работы в окне.
REFUSED_HOTKEY_KEYS = frozenset({"win", "winleft", "winright", "lwin", "rwin", "super",
                                 "cmd", "command", "meta", "apps"})
REFUSED_HOTKEY_COMBOS = (frozenset({"ctrl", "esc"}), frozenset({"ctrl", "escape"}),
                         frozenset({"ctrl", "shift", "esc"}), frozenset({"ctrl", "shift", "escape"}),
                         frozenset({"ctrl", "alt", "delete"}), frozenset({"ctrl", "alt", "del"}))
# Деньги и учётные данные Computer Use не трогает вовсе — ни с одобрением, ни без:
# для них есть отдельные пути с собственными стенами (browser.confirmed_*,
# secret_executor), а не набор с клавиатуры по выбору модели.
HARD_REFUSED_CONSEQUENCES = frozenset({"pay", "purchase", "transfer", "secret_entry",
                                       "account_change", "security_change",
                                       # необратимые внешние действия (owner 2026-10-10)
                                       "send", "external_upload", "deploy", "git_push", "merge",
                                       "release", "uninstall"})
HARD_REFUSAL_TEXT = ("платежи и учётные данные (а также необратимые внешние действия) "
                     "Computer Use не выполняет ни с одобрением, ни без")
# Действия, привязанные к конкретному окну: ввод и вывод окна на передний план.
BOUND_KINDS = INPUT_KINDS | {"focus_window"}
# Разрешения приложений на задачу (пишет только владелец через HTTP).
TASK_APPS_FILE = "TASK_APPS.json"
WINDOW_SCOPES = ("launched",)
# Сколько ждать, пока Windows действительно отдаст передний план окну.
FOCUS_WAIT_S = 2.0
LAUNCH_FOCUS_WAIT_S = 4.0
# Похоже на секрет — не печатаем ни с каким одобрением (ключи, токены, карты).
SECRET_TEXT_PATTERNS = (
    ("ключ API", re.compile(r"\b(?:sk|rk|pk)-(?:[a-z]+-)?[A-Za-z0-9_\-]{20,}")),
    ("токен GitHub", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{30,}")),
    ("ключ AWS", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("ключ Google", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("токен Slack", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}")),
    ("токен бота Telegram", re.compile(r"\b\d{8,10}:[A-Za-z0-9_\-]{35}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
    ("приватный ключ", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)
_CARD_RX = re.compile(r"(?<!\d)(?:\d[ \-]?){12,18}\d(?!\d)")
CREDENTIAL_TARGET_TOKENS = ("password", "passwd", "пароль", "passcode", "passphrase", "pin-код",
                            "pin code", "пин-код", "cvv", "cvc", "card number", "номер карты",
                            "секретн", "secret", "api key", "api-key", "token", "токен", "otp",
                            "одноразов")


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
            item["is_password"] = True
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
    # generation -> наблюдение, по которому владелец мог одобрить действие.
    history: dict[int, dict[str, Any]] = field(default_factory=dict)
    # id строк approvals, уже потраченных на эффект (переживает перезапуск).
    used_approvals: list[int] = field(default_factory=list)
    # hwnd -> {"task_id", "pid", "app"}: окна, которые запустил Computer Use.
    # Только в памяти: после перезапуска окно надо запустить заново (fail closed).
    launched: dict[int, dict[str, Any]] = field(default_factory=dict)

    @property
    def unknown_path(self) -> Path | None:
        """Lives next to STOP: one owner directory, one lifetime."""
        return None if self.stop_path is None else self.stop_path.with_name(UNKNOWN_FILE)

    @property
    def used_path(self) -> Path | None:
        return None if self.stop_path is None else self.stop_path.with_name(USED_APPROVALS_FILE)

    def remember(self, obs: dict[str, Any]) -> None:
        self.history[int(obs["generation"])] = obs
        while len(self.history) > OBS_HISTORY:
            self.history.pop(next(iter(self.history)))

    def spend_approval(self, approval_id: int) -> bool:
        """Отметить одобрение использованным ДО эффекта. False — уже было."""
        if approval_id in self.used_approvals:
            return False
        self.used_approvals.append(approval_id)
        del self.used_approvals[:-USED_APPROVALS_MAX]
        path = self.used_path
        if path is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(self.used_approvals), encoding="utf-8")
                tmp.replace(path)
            except OSError as exc:
                # Журнал не записан — повтор после перезапуска не исключён: отказ.
                self.used_approvals.remove(approval_id)
                raise ActRefused(f"журнал использованных одобрений не записан ({type(exc).__name__}) "
                                 f"— действие не выполнено") from exc
        return True

    def stopped(self) -> bool:
        return self.stop.is_set()

    def mark_outcome_unknown(self, text: str) -> None:
        self.outcome_unknown = text
        self.last = {}
        self.history.clear()
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
        used = self.used_path
        if used is not None and used.is_file():
            try:
                raw = json.loads(used.read_text(encoding="utf-8"))
                self.used_approvals = [int(x) for x in raw if isinstance(x, int) and not isinstance(x, bool)]
            except (OSError, ValueError, TypeError):
                # Повреждённый журнал нельзя принять за «ничего не использовано»:
                # до ручного разбора владельцем действия запрещены.
                self.outcome_unknown = "журнал использованных одобрений повреждён — проверьте data_dir/computer"

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
        # до «Стоп», обесценивается — модель обязана перечитать экран. Это
        # касается и уже одобренных, но не исполненных действий.
        self.generation += 1
        self.last = {}
        self.history.clear()


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
            # Идентичность окна и тип поля — с настоящей ОС, в момент действия.
            window_process_allowed = staticmethod(window_allowlisted)
            focused_is_password = staticmethod(uia_focused_is_password)
            window_pid = staticmethod(_window_pid)
            root_owner = staticmethod(_root_owner)

            async def snapshot_window(self, handle):
                """Адресное наблюдение: дерево ИМЕННО этого окна, впереди оно или нет."""
                self._req()

                def f():
                    from pywinauto import Desktop
                    try:
                        if not _is_window(handle):
                            return {"title": "", "app": "", "handle": int(handle), "error": "окна нет"}, None
                        w = Desktop(backend="uia").window(handle=int(handle))
                        fg = {"title": w.window_text(), "app": str(getattr(w.element_info, "name", "") or ""),
                              "handle": int(w.handle)}
                    except Exception as e:  # noqa: BLE001
                        return {"title": "", "app": "", "handle": int(handle), "error": type(e).__name__}, None
                    try:
                        tree = {"elements": self._walk(w)}
                    except Exception:  # noqa: BLE001
                        tree = None
                    return fg, tree
                return await asyncio.to_thread(f)

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


async def _pid_of(st: "ComputerState", handle: Any) -> int | None:
    probe = getattr(st.desktop, "window_pid", None)
    if probe is None or not handle:
        return None
    try:
        pid = int(await asyncio.to_thread(probe, int(handle)) or 0)
    except Exception:  # noqa: BLE001
        return None
    return pid or None


async def _root_of(st: "ComputerState", handle: Any) -> int:
    """Окно-владелец верхнего уровня (диалог «Сохранить как» -> окно Блокнота)."""
    probe = getattr(st.desktop, "root_owner", None)
    try:
        h = int(handle or 0)
    except (TypeError, ValueError):
        return 0
    if probe is None or not h:
        return h
    try:
        return int(await asyncio.to_thread(probe, h) or h)
    except Exception:  # noqa: BLE001
        return h


def _fingerprint(window: dict, elements: list[dict]) -> str:
    """Состояние окна, к которому привязано одобрение: заголовок, вкладки,
    содержимое полей. Сменилась вкладка/документ — это уже другое окно для
    владельца, даже если hwnd тот же (Win11 Блокнот с вкладками)."""
    tabs = sorted(str(e.get("name") or "") for e in elements if e.get("control_type") == "TabItem")
    fields = [(str(e.get("name") or ""), str(e.get("value") or "")) for e in elements
              if e.get("control_type") in ("Document", "Edit")]
    blob = json.dumps({"title": str(window.get("title") or ""), "tabs": tabs, "fields": fields},
                      ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


@dataclass
class TaskScope:
    """Что владелец разрешил ЭТОЙ задаче (None-поля — прежний режим по умолчанию)."""
    task_id: int | None = None
    apps: tuple[str, ...] | None = None          # None — DEFAULT_APPS без явного разрешения
    launched_only: bool = False

    @property
    def allowed(self) -> dict | None:
        if self.apps is None:
            return None
        from bossman.computer_operator.applist import grant_specs
        return grant_specs(self.apps)

    @property
    def granted(self) -> bool:
        return self.apps is not None


def _task_owned(st: "ComputerState", scope: TaskScope | None, handle: Any, root: Any = None) -> bool:
    if scope is None or not scope.launched_only:
        return True
    for h in (handle, root):
        try:
            rec = st.launched.get(int(h or 0))
        except (TypeError, ValueError):
            rec = None
        if rec is not None and rec.get("task_id") == scope.task_id:
            return True
    return False


async def _task_windows(st: "ComputerState", scope: TaskScope | None) -> list[dict[str, Any]] | None:
    """Живые окна, запущенные этой задачей (только при разрешении владельца;
    без него — None: список не ведётся)."""
    if scope is None or not scope.launched_only:
        return None
    try:
        alive = {h: (t, p) for h, t, p in await asyncio.to_thread(_top_windows)}
    except Exception:  # noqa: BLE001
        alive = {}
    out = []
    for h, rec in list(st.launched.items()):
        if rec.get("task_id") != scope.task_id:
            continue
        if h not in alive:
            continue
        title, pid = alive[h]
        out.append({"handle": h, "pid": pid or rec.get("pid"), "title": title[:120], "app": rec.get("app")})
    return out


async def observe(svc, *, screenshot: bool = True, window: Any = None,
                  scope: TaskScope | None = None) -> dict[str, Any]:
    """Наблюдение. `window` — адресно: дерево этого hwnd, даже если впереди
    другое окно (вызывающий уже проверил, что окно разрешено задаче).
    `scope` с разрешением владельца: окно вне разрешения задачи показывается
    без элементов и значений — чужие документы (ключи, .env) модель не читает."""
    ok, why = availability()
    if not ok:
        raise RuntimeError(why)
    st = _state(svc)
    targeted = window is not None
    snap_window = getattr(st.desktop, "snapshot_window", None)
    if targeted and snap_window is not None:
        fg, tree = await snap_window(int(window))
        fg_now = await st.desktop.foreground()
        in_front = int(fg_now.get("handle") or 0) == int(fg.get("handle") or 0)
    else:
        fg, tree = await st.desktop.snapshot()
        in_front = True
    shot = None
    if screenshot:
        shot, _ = await st.shots.capture()
    st.generation += 1
    st.clear_outcome_unknown()
    elements = list((tree or {}).get("elements") or [])[:MAX_ELEMENTS]
    for i, el in enumerate(elements):
        el["i"] = i
    win = {k: fg.get(k) for k in ("title", "app", "handle", "pid", "error") if k in fg}
    if not win.get("pid"):
        pid = await _pid_of(st, win.get("handle"))
        if pid:
            win["pid"] = pid
    win["foreground"] = in_front
    root = await _root_of(st, win.get("handle"))
    if root and root != win.get("handle"):
        win["root"] = root
    hidden = False
    if scope is not None and scope.launched_only and not _task_owned(st, scope, win.get("handle"), root):
        # Окно вне разрешения задачи: ни элементов, ни значений, ни скриншота.
        elements, shot, hidden = [], None, True
        win["title"] = "(окно вне разрешения задачи — содержимое скрыто)"
    obs = {"generation": st.generation, "session": st.session, "observed_at": time.time(),
           "window": win, "elements": elements, "screenshot": shot, "stopped": st.stopped(),
           "targeted": targeted, "hidden": hidden,
           "windows": await _task_windows(st, scope),
           "task_id": None if scope is None else scope.task_id}
    obs["fingerprint"] = _fingerprint(win, elements)
    st.last = obs
    st.remember(obs)
    await svc.bus.emit("computer.observe", generation=st.generation,
                       window=str(win.get("title") or "")[:200], elements=len(elements),
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
    if "file_exists" in cleaned or "file_contains" in cleaned or "file_sha256" in cleaned:
        raw = cleaned.get("file_exists") or ""
        target = Path(raw) if raw else None
        want_sha = cleaned.get("file_sha256")
        if want_sha is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", want_sha):
            ok = False
            checks += 1
            notes.append("file_sha256: ожидается 64 шестнадцатеричных символа — не проверено")
            want_sha = None
        if ("file_contains" in cleaned or "file_sha256" in cleaned) and target is None:
            ok = False
            checks += 1
            notes.append("file_contains/file_sha256 требуют file_exists с путём к файлу")
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
                    wants_body = "file_contains" in cleaned or want_sha is not None
                    if wants_body and not fresh:
                        # Содержимое файла, который НЕ писало это действие, не
                        # читается: иначе `expect` — оракул по любому файлу диска
                        # (ключи, токены) через безобидный `wait`.
                        notes.append("содержимое не проверялось: файл записан не этим действием")
                    elif wants_body:
                        try:
                            data = target.read_bytes()
                        except OSError as exc:
                            ok = False
                            notes.append(f"файл «{raw}» не читается: {type(exc).__name__}")
                        else:
                            if "file_contains" in cleaned:
                                body = data.decode("utf-8", errors="replace")
                                hit = cleaned["file_contains"] in body
                                ok &= hit
                                notes.append(f"в файле {'есть' if hit else 'НЕТ'} «{cleaned['file_contains']}»")
                            if want_sha is not None:
                                got = hashlib.sha256(data).hexdigest()
                                hit = got == want_sha.lower()
                                ok &= hit
                                notes.append(f"SHA-256 файла {'совпадает' if hit else 'НЕ совпадает'} "
                                             f"({got[:16]}…)")
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
    ident = (f" [hwnd={w.get('handle')}, pid={w.get('pid') or '?'}"
             + ("" if w.get("foreground", True) else ", НЕ впереди") + "]") if w.get("handle") else ""
    lines = [f"generation: {obs['generation']}",
             f"окно: {w.get('title') or '(нет активного окна)'}{ident}"
             + (f" [{w.get('error')}]" if w.get("error") else ""),
             f"скриншот: {obs.get('screenshot') or 'не снят'}"]
    if obs.get("task_id") is not None and obs.get("windows") is not None:
        wins = obs.get("windows") or []
        lines.append("окна этой задачи (для window/pid в computer.act):" if wins
                     else "окна этой задачи: нет (запустите разрешённое приложение: action=launch)")
        for tw in wins:
            lines.append(f"  - hwnd={tw['handle']}, pid={tw.get('pid')}: «{tw.get('title')}»"
                         + (" (впереди)" if tw["handle"] == w.get("handle") and w.get("foreground", True)
                            else ""))
    lines.append("элементы (name | тип | центр x,y):")
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
        from bossman.computer_operator.applist import APP_CATALOG
        spec = APP_CATALOG.get(app, {})
        return {n.lower() for n in (*spec.get("windows", ()), *spec.get("process", ()))}
    except Exception:  # noqa: BLE001
        return set()


def _process_exe(pid: int) -> str:
    try:
        import psutil
        return str(psutil.Process(pid).exe() or "")
    except Exception:  # noqa: BLE001
        return ""


def _is_window(handle: Any) -> bool:
    try:
        import ctypes
        from ctypes import wintypes
        return bool(ctypes.windll.user32.IsWindow(wintypes.HWND(int(handle))))
    except Exception:  # noqa: BLE001
        return False


def _root_owner(handle: int) -> int:
    """GetAncestor(GA_ROOTOWNER): окно верхнего уровня, которому принадлежит диалог."""
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        u.GetAncestor.restype = wintypes.HWND
        root = u.GetAncestor(wintypes.HWND(int(handle)), 3)
        return int(root or 0) or int(handle)
    except Exception:  # noqa: BLE001
        return int(handle)


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


async def _wait_foreground(st: "ComputerState", handle: int, seconds: float) -> bool:
    """Опрос: окно действительно впереди? Только чтение — ничего не активирует."""
    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        fg = await st.desktop.foreground()
        if int(fg.get("handle") or 0) == int(handle):
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(0.1)


async def _focus(st: "ComputerState", handle: int) -> bool:
    """Одна штатная просьба к ОС (UIA SetFocus -> SetForegroundWindow) и опрос.

    Отказ Windows (foreground lock) — это отказ: ни нажатия Alt, ни
    AttachThreadInput, ни клика ради фокуса здесь нет и быть не должно."""
    await asyncio.to_thread(_set_foreground, handle)
    return await _wait_foreground(st, handle, FOCUS_WAIT_S)


async def _window_allowed(st: "ComputerState", handle: Any,
                          allowed: dict | None = None) -> tuple[bool, str]:
    probe = getattr(st.desktop, "window_process_allowed", None)
    if probe is None:            # бэкенд не умеет назвать процесс окна — не угадываем
        return False, "бэкенд не подтверждает приложение окна"
    try:
        if allowed is None:
            ok, who = await asyncio.to_thread(probe, handle)
        else:
            ok, who = await asyncio.to_thread(lambda: probe(handle, allowed=allowed))
    except Exception as exc:  # noqa: BLE001
        return False, f"проверка окна упала: {type(exc).__name__}"
    return bool(ok), str(who)


def _apps_label(scope: "TaskScope | None") -> str:
    from bossman.computer_operator.applist import DEFAULT_APPS
    apps = DEFAULT_APPS if scope is None or scope.apps is None else scope.apps
    return ", ".join(apps)


async def _require_allowlisted_window(st: "ComputerState", obs: dict | None,
                                      scope: "TaskScope | None" = None) -> None:
    window = (obs or {}).get("window") or {}
    allowed, who = await _window_allowed(st, window.get("handle"), None if scope is None else scope.allowed)
    if not allowed:
        raise ActRefused(f"окно «{window.get('title') or '?'}» (процесс {who}) вне allowlist Computer Use "
                         f"(разрешено: {_apps_label(scope)}) — ввод не отправлен")
    if not _task_owned(st, scope, window.get("handle"), window.get("root")):
        raise ActRefused(f"окно hwnd={window.get('handle')} не запускалось этой задачей — по разрешению "
                         f"владельца ввод идёт только в окна, которые задача открыла сама; ввод не отправлен")


def _stop_check(st: ComputerState, phase: str, epoch: int | None = None) -> None:
    if st.stopped():
        raise ActRefused(f"владелец нажал «Стоп» ({phase}): действия на рабочем столе "
                         f"остановлены до «Продолжить»")
    if epoch is not None and st.stop_epoch != epoch:
        raise ActRefused(f"владелец нажимал «Стоп», пока действие ждало очереди или шло ({phase}) — "
                         f"действие отменено; вызовите computer.observe и решите заново "
                         f"по свежему экрану")


def _window_pid(handle: int) -> int:
    import ctypes
    from ctypes import wintypes
    pid = wintypes.DWORD(0)
    ctypes.windll.user32.GetWindowThreadProcessId(wintypes.HWND(int(handle)), ctypes.byref(pid))
    return int(pid.value)


def _allowed_process_names(allowed: dict | None = None) -> set[str]:
    from bossman.computer_operator.applist import APP_ALLOWLIST
    table = APP_ALLOWLIST if allowed is None else allowed
    return {str(n).lower() for spec in table.values()
            for n in (*spec.get("windows", ()), *spec.get("process", ()))}


def window_allowlisted(handle: Any, *, pid_of=None, name_of=None,
                       hosted_matcher=None, allowed: dict | None = None,
                       exe_of=None) -> tuple[bool, str]:
    """(можно ли слать ввод в это окно, имя процесса). Любое сомнение — отказ.

    Окно принадлежит процессу из разрешённого набора приложений (по умолчанию
    Блокнот и Калькулятор; с разрешением владельца — набор задачи из каталога;
    диалоги «Сохранить как» — тот же процесс), либо это окно
    ApplicationFrameHost, в котором проверенно живёт пакет Калькулятора.
    Упакованное приложение (Paint) — только если exe процесса лежит в каталоге
    его пакета. Оболочки, браузер, мессенджеры, UAC/учётные данные и сам
    Bossman не проходят НИКОГДА (applist.HARD_DENIED_PROCESSES).
    """
    from bossman.computer_operator.applist import APP_ALLOWLIST, hard_denied_process, packaged_exe_ok
    table = APP_ALLOWLIST if allowed is None else allowed
    try:
        h = int(handle or 0)
    except (TypeError, ValueError):
        h = 0
    if not h:
        return False, "окно не определено"
    try:
        pid = (pid_of or _window_pid)(h)
    except Exception:  # noqa: BLE001
        return False, "процесс окна не определён"
    name = str((name_of or _process_name)(pid) or "").lower() if pid else ""
    if hard_denied_process(name):
        return False, name
    for spec in table.values():
        names = {str(n).lower() for n in (*spec.get("windows", ()), *spec.get("process", ()))}
        if name and name in names:
            package = spec.get("package")
            if package and not packaged_exe_ok((exe_of or _process_exe)(pid), package):
                return False, f"{name} (не из пакета {package})"
            return True, name
    if (name == HOSTED_FRAME_PROCESS and "calculator" in table
            and (hosted_matcher or _hosted_calculator_window)(h)):
        return True, "calculator"
    return False, name or "процесс не определён"


def secret_like_text(text: Any) -> str | None:
    """Похоже ли вводимое на секрет. Возвращает вид секрета или None."""
    t = str(text or "")
    if not t:
        return None
    for label, rx in SECRET_TEXT_PATTERNS:
        if rx.search(t):
            return label
    for m in _CARD_RX.finditer(t):
        digits = [int(c) for c in m.group(0) if c.isdigit()]
        if 13 <= len(digits) <= 19:
            total = 0
            for i, d in enumerate(reversed(digits)):
                if i % 2:
                    d *= 2
                    if d > 9:
                        d -= 9
                total += d
            if total % 10 == 0:
                return "номер карты"
    return None


def uia_focused_is_password() -> bool | None:
    """Поле с клавиатурным фокусом — секретное? None — не удалось узнать."""
    try:
        from pywinauto.uia_defines import IUIA
        element = IUIA().iuia.GetFocusedElement()
        return bool(element.CurrentIsPassword)
    except Exception:  # noqa: BLE001
        return None


def _hotkey_keys(keys: Any) -> set[str]:
    if isinstance(keys, str):
        keys = re.split(r"[+\s,]+", keys)
    if not isinstance(keys, (list, tuple)):
        return set()
    return {str(k).strip().lower() for k in keys if str(k).strip()}


def hotkey_refusal(keys: Any) -> str | None:
    ks = _hotkey_keys(keys)
    if ks & REFUSED_HOTKEY_KEYS or any(combo <= ks for combo in REFUSED_HOTKEY_COMBOS):
        return (f"комбинация {'+'.join(sorted(ks))} открывает оболочку Windows (Win/Пуск/Выполнить/"
                f"диспетчер задач) — Computer Use её не нажимает")
    return None


def credential_target(name: str | None) -> bool:
    n = str(name or "").lower()
    return bool(n) and any(tok in n for tok in CREDENTIAL_TARGET_TOKENS)


def hard_refusal(args: dict) -> str | None:
    """Отказ, который не снимает никакое одобрение (виден уже по аргументам)."""
    from bossman.computer_operator.applist import APP_CATALOG, canonical_app
    from bossman.computer_operator.policy import ComputerPolicy
    args = dict(args or {})
    kind = str(args.get("action") or "").strip().lower()
    target = str(args.get("target") or "").strip()
    if kind == "launch" and canonical_app(target, APP_CATALOG) is None:
        return (f"«{target[:80]}» вне allowlist запуска (каталог: {', '.join(sorted(APP_CATALOG))}; "
                f"задаче — только приложения, разрешённые ей владельцем)")
    if kind == "hotkey":
        refusal = hotkey_refusal(args.get("keys"))
        if refusal:
            return refusal
    for claim in (ComputerPolicy.declared_consequence(args),
                  ComputerPolicy.declared_consequence({"semantic": target}) if target else None):
        if claim in HARD_REFUSED_CONSEQUENCES:
            return f"последствие «{claim}»: {HARD_REFUSAL_TEXT}"
    if kind == "type" and credential_target(target):
        return f"ввод в поле «{target[:80]}» — это учётные данные; Computer Use их не вводит"
    if kind == "type":
        secret = secret_like_text(args.get("text"))
        if secret:
            return f"вводимый текст похож на секрет ({secret}) — Computer Use секреты не вводит"
    return None


def _approval_ttl_s() -> float:
    raw = os.environ.get(APPROVAL_TTL_ENV, "").strip()
    try:
        value = float(raw) if raw else APPROVAL_TTL_S
    except ValueError:
        value = APPROVAL_TTL_S
    if value != value:                                  # NaN
        value = APPROVAL_TTL_S
    return max(MIN_APPROVAL_TTL_S, min(APPROVAL_TTL_S, value))


async def claim_approval(svc, approval_id: Any, task: dict | None = None) -> dict:
    """Одноразовое одобрение владельца для ЭТОГО эффекта, иначе ActRefused.

    Действительно только: строка approvals существует, её вид — вопрос движка
    о вызове инструмента, движок уже принял её к исполнению (consumed — CAS
    approved→consumed в `_authorization_at_effect_time`), она относится к этой
    задаче, моложе APPROVAL_TTL_S и НЕ была использована раньше (журнал в
    data_dir/computer, пишется до эффекта).
    """
    if approval_id is None or isinstance(approval_id, bool):
        raise ActRefused("каждое действие на рабочем столе требует отдельного одобрения владельца; "
                         "право агента, правило политики или аренда (lease) его не заменяют")
    try:
        aid = int(approval_id)
    except (TypeError, ValueError):
        raise ActRefused("ссылка на одобрение некорректна") from None
    import sqlalchemy as sa
    from ..db import approvals as approvals_t, utcnow
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(approvals_t).where(approvals_t.c.id == aid))).first()
    row = dict(row._mapping) if row is not None else None
    if row is None:
        raise ActRefused(f"одобрение #{aid} не найдено — действие не выполнено")
    if row.get("kind") not in APPROVAL_ROW_KINDS:
        raise ActRefused(f"одобрение #{aid} другого вида ({row.get('kind')}) — не относится к этому вызову")
    if row.get("status") != "consumed":
        raise ActRefused(f"одобрение #{aid} не принято движком к исполнению "
                         f"(статус {row.get('status')}) — действие не выполнено")
    task_id = (task or {}).get("id")
    if row.get("task_id") is not None and task_id is not None and int(row["task_id"]) != int(task_id):
        raise ActRefused(f"одобрение #{aid} выдано другой задаче — не переносится")
    created = row.get("created_at")
    if created is None:
        raise ActRefused(f"у одобрения #{aid} нет времени создания — срок не проверить")
    if getattr(created, "tzinfo", None) is not None:
        created = created.replace(tzinfo=None) - (created.utcoffset() or timedelta(0))
    age = (utcnow() - created).total_seconds()
    ttl = _approval_ttl_s()
    if age > ttl:
        raise ActRefused(f"одобрение #{aid} истекло: {age:.0f} с с момента вопроса при сроке "
                         f"{ttl:.0f} с — нужен новый вопрос владельцу по свежему экрану")
    st = _owner_state(svc)
    if not st.spend_approval(aid):
        raise ActRefused(f"одобрение #{aid} уже использовано — повтор действия по нему запрещён")
    return row


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


def _int_arg(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _wdesc(w: dict | None) -> str:
    w = w or {}
    return f"«{w.get('title') or '?'}» (hwnd={w.get('handle')}, pid={w.get('pid') or '?'})"


async def act(svc, args: dict, *, approved_kind: str | None = None,
              approval_ref: int | None = None, scope: TaskScope | None = None) -> dict[str, Any]:
    """Одно действие по свежему наблюдению + автоматическая проверка результата.

    `approved_kind` — вид последствия (например "delete"), который владелец
    одобрил ДЛЯ ЭТОГО вызова; приходит из доверенного контекста, не из args.
    `scope` — разрешение владельца для задачи (приложения, только свои окна).
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
    if kind == "hotkey":
        refusal = hotkey_refusal(args.get("keys"))
        if refusal:
            raise ActRefused(refusal)
    if kind == "type":
        secret = secret_like_text(args.get("text"))
        if secret:
            raise ActRefused(f"вводимый текст похож на секрет ({secret}) — Computer Use секреты не вводит")
    allowed = None if scope is None else scope.allowed
    if kind == "launch":
        from bossman.computer_operator.applist import canonical_app
        if canonical_app(str(args.get("target") or ""), allowed) is None:
            raise ActRefused(f"«{str(args.get('target') or '')[:80]}» вне allowlist запуска этой задачи "
                             f"(разрешено: {_apps_label(scope)}) — запуск не выполнен")
    # R6: эпоха стопа фиксируется при входе; любой «Стоп» (и «Продолжить») после
    # этого момента отменяет оставшиеся шаги действия.
    epoch = st.stop_epoch
    _stop_check(st, "до очереди", epoch)
    target = str(args.get("target") or "").strip()
    started_at = time.time()
    raw_window, raw_pid = args.get("window"), args.get("pid")
    want_h, want_pid = _int_arg(raw_window), _int_arg(raw_pid)
    if (raw_window is not None and want_h is None) or (raw_pid is not None and want_pid is None):
        raise ActRefused("window и pid — целые числа (hwnd и pid окна из computer.observe)")
    async with st.lock:                            # один рабочий стол — одно действие за раз
        # «Стоп» мог прийти, пока действие ждало замок: очередь из двух действий,
        # STOP между ними — второе не исполняется.
        _stop_check(st, "после ожидания очереди", epoch)
        if st.outcome_unknown:
            raise ActRefused(f"исход прошлого действия неизвестен ({st.outcome_unknown}) — "
                             f"сначала computer.observe")
        seen: dict = {}
        gen = args.get("generation")
        if kind not in ("launch", "wait"):
            valid_gen = _int_arg(gen) is not None
            if approval_ref is not None:
                # Одобренное действие: владелец отвечал не мгновенно, и за это время
                # экран могли перечитать (кнопка «Разрешить» в Пульте сама делает
                # свежее наблюдение). Поэтому не «generation == текущему», а:
                # наблюдение, по которому спрашивали, ещё действительно (не было
                # «Продолжить»/перезапуска/неизвестного исхода), окно — ТО ЖЕ
                # (hwnd, pid, заголовок, вкладки, содержимое) на адресно
                # перечитанном экране и оно впереди. Срок одобрения — в claim_approval.
                if not valid_gen:
                    raise ActRefused(
                        f"generation обязателен (получено {gen!r}): одобряемое действие привязывается "
                        f"к наблюдению — вызовите computer.observe и передайте его generation, window и pid")
                seen = st.history.get(gen) or {}
                if not seen:
                    raise ActRefused(
                        f"наблюдение generation={gen!r}, по которому одобрено действие, больше "
                        f"недействительно (перезапуск, «Стоп»/«Продолжить» или неизвестный исход) — "
                        f"одобрение не применено; нужен новый computer.observe и новый вопрос владельцу")
            else:
                if not st.last or not valid_gen or gen != st.generation:
                    raise ActRefused(
                        f"наблюдение устарело (generation={gen!r}, текущее {st.generation}): "
                        f"вызовите computer.observe и действуйте по свежему экрану")
                age = time.time() - float(st.last.get("observed_at") or 0)
                if age > MAX_OBS_AGE_S:
                    raise ActRefused(f"наблюдение старше {MAX_OBS_AGE_S:.0f} с ({age:.0f} с) — "
                                     f"перечитайте экран (computer.observe)")
                seen = st.last
        bound = dict(seen.get("window") or {})
        if kind in INPUT_KINDS:
            # Привязка к окну наблюдения: точные hwnd и pid. Для одобряемого
            # действия они обязательны — владелец видит их в вопросе.
            if approval_ref is not None and (want_h is None or want_pid is None):
                raise ActRefused(
                    f"действие ввода привязывается к окну: передайте window={bound.get('handle')} и "
                    f"pid={bound.get('pid')} из наблюдения generation={gen} — без них одобрение не применяется")
            if want_h is not None and want_h != int(bound.get("handle") or 0):
                raise ActRefused(f"window={want_h} — не окно наблюдения generation={gen} "
                                 f"({_wdesc(bound)}); нужен computer.observe этого окна")
            if want_pid is not None and want_pid != int(bound.get("pid") or 0):
                raise ActRefused(f"pid={want_pid} не совпадает с процессом окна {_wdesc(bound)} — "
                                 f"окно не то, ввод не отправлен")
            if seen.get("hidden"):
                raise ActRefused("окно наблюдения вне разрешения задачи — ввод не отправлен; запустите "
                                 "разрешённое приложение (launch) или наблюдайте окно задачи (window=hwnd)")
            if approval_ref is not None:
                _stop_check(st, "перечитывание экрана после одобрения", epoch)
                fresh = await _bounded(st, observe(svc, screenshot=False, window=bound.get("handle") or None,
                                                   scope=scope), "наблюдение")
                now = fresh.get("window") or {}
                if not bound.get("handle") or int(now.get("handle") or 0) != int(bound.get("handle") or 0):
                    raise ActRefused(
                        f"окно сменилось с момента вопроса владельцу: одобрено для «{bound.get('title')}», "
                        f"сейчас впереди «{now.get('title')}» — одобрение не применено")
                if bound.get("pid") and int(now.get("pid") or 0) != int(bound["pid"]):
                    raise ActRefused(f"окно сменилось: hwnd={bound.get('handle')} теперь принадлежит "
                                     f"процессу pid={now.get('pid')}, а одобрено для pid={bound.get('pid')} — "
                                     f"одобрение не применено")
                if not now.get("foreground", True):
                    fgw = await _bounded(st, st.desktop.foreground(), "проверка окна")
                    raise ActRefused(
                        f"окно сменилось с момента вопроса владельцу: одобрено для {_wdesc(bound)}, "
                        f"сейчас впереди «{fgw.get('title')}» — одобрение не применено. Фокус сервер сам "
                        f"не возвращает: нужен новый computer.observe, при необходимости focus_window "
                        f"отдельным одобрением, и новый вопрос владельцу")
                if seen.get("fingerprint") and fresh.get("fingerprint") != seen.get("fingerprint"):
                    raise ActRefused(
                        f"окно {_wdesc(bound)} изменилось с момента вопроса владельцу (заголовок, вкладка "
                        f"или содержимое) — одобрение не применено; нужен новый computer.observe и новый "
                        f"вопрос владельцу")
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
        decision = policy.classify(a, mode=core["TaskMode"].CONTROL, observation=pol_obs,
                                   allowed_apps=allowed)
        if not decision.allow:
            raise ActRefused(f"политика запретила действие: {decision.reason}")
        required = (decision.approval_kind or "").removeprefix("computer_") if decision.requires_approval else None
        if required in HARD_REFUSED_CONSEQUENCES:
            raise ActRefused(f"последствие «{required}» ({decision.reason}): {HARD_REFUSAL_TEXT}")
        if kind in INPUT_KINDS:
            # CU-ALLOWLIST: окно из наблюдения (оно же сейчас впереди — проверено
            # выше) обязано принадлежать разрешённому приложению, а с
            # разрешением владельца — ещё и быть запущенным этой задачей.
            await _require_allowlisted_window(st, before, scope)
        if kind == "type":
            if credential_target(target):
                raise ActRefused(f"ввод в поле «{target}» — это учётные данные; Computer Use их не вводит")
            if target and any(e.get("is_password") for e in _find(before, target, index)):
                raise ActRefused(f"поле «{target}» секретное (пароль) — Computer Use учётные данные не вводит")
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
            fresh = await _bounded(st, observe(svc, screenshot=False, scope=scope), "наблюдение")
            if int((fresh.get("window") or {}).get("handle") or 0) != int((before.get("window") or {}).get("handle") or 0):
                raise ActRefused(f"перед кликом впереди другое окно «{(fresh.get('window') or {}).get('title')}» "
                                 f"— клик не выполнен")
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
            if kind == "type" and len(editable) == 1 and (editable[0].get("is_password")
                                                          or credential_target(editable[0].get("name"))):
                raise ActRefused(f"единственное поле окна «{editable[0].get('name')}» секретное — "
                                 f"Computer Use учётные данные не вводит")
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
            fresh = await _bounded(st, observe(svc, screenshot=False, scope=scope), "наблюдение")
            d2 = policy.classify(a, mode=core["TaskMode"].CONTROL,
                                 observation=_policy_observation(core, fresh, st.generation),
                                 allowed_apps=allowed)
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
        if kind == "type":
            # Истина о поле — у ОС в момент ввода, а не у подписи в наблюдении:
            # фокус на поле пароля (или невозможность это проверить) — отказ.
            probe = getattr(st.desktop, "focused_is_password", None)
            secret = await asyncio.to_thread(probe) if probe is not None else None
            if secret is not False:
                raise ActRefused("поле с фокусом секретное (пароль) или его тип не удалось проверить — "
                                 "ничего не введено")
        if kind in INPUT_KINDS:
            # Последний взгляд на передний план непосредственно перед вводом:
            # окно наблюдения, и никакое другое (фокус могли увести за время
            # проверок выше).
            fg_last = await _bounded(st, st.desktop.foreground(), "проверка окна")
            want = int((before.get("window") or {}).get("handle") or 0)
            if want and int(fg_last.get("handle") or 0) != want:
                raise ActRefused(f"фокус ушёл перед самым вводом: впереди «{fg_last.get('title')}», "
                                 f"а не {_wdesc(before.get('window'))} — ввод не отправлен")
        # Последняя проверка «Стоп» — непосредственно перед эффектом.
        _stop_check(st, "перед эффектом", epoch)
        if kind == "type" and args.get("replace"):
            await _bounded(st, st.desktop.execute(core["ComputerAction"].make(
                AK.HOTKEY, args={"keys": ["ctrl", "a"]}), None), "выделение")
        t0 = time.perf_counter()
        after_window: int | None = None
        acted_window: dict[str, Any] | None = None
        if kind == "wait":
            deadline = time.monotonic() + min(10.0, max(0.0, float(args.get("seconds") or 1)))
            while True:
                _stop_check(st, "ожидание", epoch)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(0.1, remaining))
        elif kind == "launch":
            from bossman.computer_operator.applist import APP_CATALOG, canonical_app
            app = canonical_app(target, allowed)
            if app is None:
                raise ActRefused(f"«{target}» не разрешён этой задаче (разрешено: {_apps_label(scope)})")
            known = {h for h, _, _ in await asyncio.to_thread(_top_windows)}
            st.launched_pid = None
            _stop_check(st, "запуск", epoch)
            await _bounded(st, st.launcher.execute(a, None, allowed=allowed), "запуск")
            # Окно ЗАПУЩЕННОГО приложения: новое, нужного процесса (для пакета —
            # из каталога пакета). Чужие новые окна не берём.
            chosen = None
            seen_new: list[tuple[int, str, int]] = []
            for _ in range(int(LAUNCH_WAIT_S / 0.25)):
                await asyncio.sleep(0.25)
                seen_new = [w for w in await asyncio.to_thread(_top_windows) if w[0] not in known]
                # Сначала окно самого запущенного процесса, затем остальные новые;
                # принимается только то, чей процесс подтверждён как ЭТО приложение
                # (имя образа, пакет, хост Калькулятора) — та же проверка, что и для ввода.
                ordered = sorted(seen_new, key=lambda w: 0 if st.launched_pid and w[2] == st.launched_pid else 1)
                for h_new, title_new, _pid in ordered:
                    if (await _window_allowed(st, h_new, {app: APP_CATALOG[app]}))[0]:
                        chosen = (h_new, title_new)
                        break
                if chosen:
                    break
            if not chosen:
                foreign = "; ".join(f"«{t}»" for _, t, _ in seen_new[:4])
                raise ActRefused(
                    f"«{target}» запущен, но окно запущенного приложения не появилось за "
                    f"{LAUNCH_WAIT_S:.0f} с"
                    + (f" (новые чужие окна: {foreign} — не трогаем)" if foreign else ""))
            pid = await _pid_of(st, chosen[0])
            st.launched[int(chosen[0])] = {"task_id": None if scope is None else scope.task_id,
                                           "pid": pid, "app": app, "at": time.time()}
            # Новое окно обычно само выходит вперёд; ждём это, затем ОДНА штатная
            # просьба к ОС. Не вышло — запуск всё равно состоялся: это факт, а не
            # ошибка; для ввода модель вызовет focus_window отдельным одобрением.
            _stop_check(st, "фокус окна", epoch)
            in_front = await _wait_foreground(st, chosen[0], LAUNCH_FOCUS_WAIT_S)
            if not in_front:
                _stop_check(st, "фокус окна", epoch)
                in_front = await _focus(st, chosen[0])
            after_window = int(chosen[0])
            acted_window = {"handle": int(chosen[0]), "pid": pid, "title": chosen[1], "foreground": in_front}
        elif kind == "focus_window":
            wins: list[tuple[int, str]] = []
            if want_h is not None:
                if approval_ref is not None and want_pid is None:
                    raise ActRefused("focus_window: нужен pid окна из наблюдения вместе с window")
                listed = {int(w.get("handle") or 0) for w in (seen.get("windows") or [])}
                listed.add(int(bound.get("handle") or 0))
                if want_h not in listed:
                    raise ActRefused(f"окна hwnd={want_h} нет в наблюдении generation={gen} — "
                                     f"вызовите computer.observe и возьмите hwnd/pid из него")
                pid_now = await _pid_of(st, want_h)
                if want_pid is not None and pid_now != want_pid:
                    raise ActRefused(f"окно hwnd={want_h}: процесс сейчас pid={pid_now}, ожидался pid={want_pid} "
                                     f"— окно закрыто или подменено, фокус не переводится")
                ok_, who = await _window_allowed(st, want_h, allowed)
                if not ok_:
                    raise ActRefused(f"окно hwnd={want_h} (процесс {who}) вне allowlist Computer Use и "
                                     f"не трогается (разрешено: {_apps_label(scope)})")
                if not _task_owned(st, scope, want_h):
                    raise ActRefused(f"окно hwnd={want_h} не запускалось этой задачей — не трогается")
                title = next((t for h, t, _ in await asyncio.to_thread(_top_windows) if h == want_h), "")
                wins = [(want_h, title)]
            else:
                if not target:
                    raise ActRefused("focus_window: нужен window (hwnd) и pid из наблюдения "
                                     "или target — часть заголовка окна")
                if scope is not None and scope.launched_only:
                    raise ActRefused("focus_window по заголовку недоступен при разрешении владельца на задачу: "
                                     "укажите window и pid окна задачи из наблюдения")
                matching = [(h, t) for h, t, _ in await asyncio.to_thread(_top_windows)
                            if target.lower() in t.lower()]
                foreign = []
                for h, t in matching:
                    allowed_, who = await _window_allowed(st, h, allowed)
                    (wins if allowed_ else foreign).append((h, t) if allowed_ else f"«{t[:60]}» ({who})")
                if len(wins) != 1:
                    raise ActRefused(f"разрешённых окон с «{target}» в заголовке: {len(wins)} — "
                                     + ("уточните заголовок (лучше window/pid из наблюдения)" if wins
                                        else "такого окна нет")
                                     + (f"; вне allowlist Computer Use и не трогаются: {', '.join(foreign[:4])}"
                                        if foreign else ""))
            _stop_check(st, "фокус окна", epoch)
            if not await _focus(st, wins[0][0]):
                raise ActRefused(f"Windows не отдал передний план окну «{wins[0][1]}» (hwnd={wins[0][0]}) — "
                                 f"фокус не обходится; повторите позже или выведите окно вручную")
            after_window = int(wins[0][0])
        else:
            _stop_check(st, "действие", epoch)
            await _bounded(st, st.desktop.execute(a, None), "действие")
        if kind in INPUT_KINDS:
            # Проверка — по окну, в которое шёл ввод. Если впереди его же диалог
            # (тот же владелец) — наблюдаем передний план, иначе — окно адресно.
            bh = int((before.get("window") or {}).get("handle") or 0)
            if bh:
                fgh = int((await st.desktop.foreground()).get("handle") or 0)
                if fgh != bh and await _root_of(st, fgh) != bh:
                    after_window = bh
        await asyncio.sleep(SETTLE_S)
        after = await _bounded(st, observe(svc, window=after_window, scope=scope), "наблюдение")
        verified, notes = verify(after, args.get("expect"), started_at=started_at)
        result = {"action": kind, "target": target, "coordinates": [x, y] if coords else None,
                  "before_generation": (before or {}).get("generation"),
                  "after_generation": after["generation"], "verified": verified, "checks": notes,
                  "approved_kind": approved_kind if required else None, "approval_ref": approval_ref,
                  "window": acted_window or {k: (after.get("window") or {}).get(k)
                                             for k in ("handle", "pid", "title", "foreground")},
                  "elapsed_ms": int((time.perf_counter() - t0) * 1000), "observation": after}
    await svc.bus.emit("computer.act", action=kind, target=target[:200], verified=verified,
                       before=result["before_generation"], after=result["after_generation"],
                       approved_kind=result["approved_kind"], approval_id=approval_ref,
                       window=str((after.get("window") or {}).get("title") or "")[:200],
                       hwnd=(result["window"] or {}).get("handle"), pid=(result["window"] or {}).get("pid"))
    return result


# ------------------------------------------------------------------ tools

# ------------------------------------------------------------------ разрешения задачи

def _grants_path(svc) -> Path:
    return Path(svc.settings.data_dir) / "computer" / TASK_APPS_FILE


def _read_grants(svc) -> dict[str, Any]:
    """Журнал разрешений владельца. Читается заново на КАЖДОМ решении: снятое
    разрешение действует сразу. Повреждённый журнал — отказ, а не «ничего»."""
    path = _grants_path(svc)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ActRefused(f"журнал разрешений приложений повреждён ({type(exc).__name__}) — "
                         f"Computer Use остановлен до разбора владельцем (data_dir/computer/{TASK_APPS_FILE})") from exc
    if not isinstance(raw, dict):
        raise ActRefused("журнал разрешений приложений повреждён — Computer Use остановлен до разбора владельцем")
    return raw


def task_grant(svc, task_id: Any) -> dict | None:
    if task_id is None or isinstance(task_id, bool):
        return None
    rec = _read_grants(svc).get(str(int(task_id)))
    return rec if isinstance(rec, dict) else None


def task_scope(svc, task: dict | None) -> TaskScope:
    """Разрешение владельца для задачи вызова (контекст движка, не аргументы модели)."""
    from bossman.computer_operator.applist import normalize_grant
    task_id = (task or {}).get("id")
    rec = task_grant(svc, task_id)
    if rec is None:
        return TaskScope(task_id=task_id)
    try:
        apps = normalize_grant(rec.get("apps"))
    except ValueError as exc:
        raise ActRefused(f"разрешение задачи #{task_id} некорректно: {exc}") from exc
    return TaskScope(task_id=int(task_id), apps=apps,
                     launched_only=str(rec.get("windows") or "launched") == "launched")


async def _t_observe(args, ctx):
    try:
        scope = task_scope(ctx.svc, getattr(ctx, "task", None))
        raw = (args or {}).get("window")
        window = _int_arg(raw)
        if raw is not None and window is None:
            raise ActRefused("window: целое число (hwnd окна задачи из прошлого наблюдения)")
        if window is not None:
            st = _state(ctx.svc)
            ok_, who = await _window_allowed(st, window, scope.allowed)
            if not ok_:
                raise ActRefused(f"окно hwnd={window} (процесс {who}) вне allowlist Computer Use "
                                 f"(разрешено: {_apps_label(scope)}) — не наблюдается")
            if not _task_owned(st, scope, window, await _root_of(st, window)):
                raise ActRefused(f"окно hwnd={window} не запускалось этой задачей — не наблюдается")
        obs = await observe(ctx.svc, window=window, scope=scope)
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
    kind = str(args.get("action") or "").strip().lower()
    try:
        refusal = hard_refusal(args)
        if refusal:
            raise ActRefused(refusal)
        if kind not in MUTATION_FREE_KINDS:
            # CU-ONESHOT: одно одобрение владельца — один эффект. Проверяется
            # здесь, на входе в обработчик, для ЛЮБОГО пути сюда (AUTO по праву
            # агента, правило политики, аренда, прямой вызов).
            await claim_approval(ctx.svc, getattr(ctx, "approval_id", None), getattr(ctx, "task", None))
    except ActRefused as exc:
        await ctx.svc.bus.emit("computer.refused", action=kind[:40], reason=str(exc)[:300],
                               approval_id=getattr(ctx, "approval_id", None))
        return ToolResult(content=f"действие не выполнено: {exc}", one_line="computer.act: отказ",
                          error=True)
    approved_kind, approval_ref = approved_consequence(args, ctx)
    if kind in MUTATION_FREE_KINDS:
        approval_ref = None            # `wait` ничего не меняет: одобрение не привязывает экран
    try:
        scope = task_scope(ctx.svc, getattr(ctx, "task", None))
        res = await act(ctx.svc, args, approved_kind=approved_kind, approval_ref=approval_ref,
                        scope=scope)
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


async def _act_context_deny(args: dict, ctx) -> str | None:
    """Отказ ДО вопроса владельцу по разрешению задачи: не будить владельца ради
    того, что обработчик всё равно не исполнит. Только запрещает."""
    from bossman.computer_operator.applist import canonical_app
    try:
        scope = task_scope(ctx.svc, getattr(ctx, "task", None))
    except ActRefused as exc:
        return str(exc)
    kind = str((args or {}).get("action") or "").strip().lower()
    if kind == "launch" and canonical_app(str((args or {}).get("target") or ""), scope.allowed) is None:
        return (f"приложение «{str((args or {}).get('target') or '')[:60]}» не разрешено этой задаче "
                f"(разрешено: {_apps_label(scope)}); разрешение даёт только владелец")
    if kind == "type":
        secret = secret_like_text((args or {}).get("text"))
        if secret:
            return f"вводимый текст похож на секрет ({secret}) — Computer Use секреты не вводит"
    return None


def _act_effect(args: dict):
    from bossman.computer_operator.policy import ComputerPolicy
    # Пол политики (hook_is_floor): ни право агента, ни правило владельца не
    # опускают его до AUTO. DENY — то, что не исполняется ни с каким одобрением.
    refusal = hard_refusal(args or {})
    if refusal:
        return ("deny", refusal)
    # semantic ЛИБО подпись цели/текста: «click» на «Delete account» тоже ASK.
    declared = ComputerPolicy.ask_consequence(args or {})
    if declared:
        return ("ask", f"последствийное действие на рабочем столе: {declared}")
    kind = str((args or {}).get("action") or "").strip().lower()
    if kind in MUTATION_FREE_KINDS:
        return None
    # CU-ONESHOT: любое изменение на рабочем столе владельца — отдельный вопрос.
    return ("ask", "действие на рабочем столе владельца: одно одобрение — одно действие")


SPECS = [
    ToolSpec(name="computer.observe",
             description="Посмотреть на рабочий стол: окно (hwnd, pid), элементы (имя, тип, центр x,y, "
                         "значение полей) и скриншот. Возвращает generation для computer.act. "
                         "window=<hwnd> — адресно наблюдать окно ЭТОЙ задачи, даже если впереди другое; "
                         "в ответе перечислены окна задачи (hwnd, pid).",
             handler=_t_observe,
             input_schema={"window": {"type": "integer",
                                      "description": "hwnd окна задачи из прошлого наблюдения"}},
             category="read", permission="computer.observe",
             source="computer", default_effect="ask", timeout_seconds=60.0, external_output=True),
    ToolSpec(name="computer.act",
             description="Одно действие на рабочем столе по свежему наблюдению (generation). "
                         "action: focus|click|double_click|type|hotkey|scroll|invoke|launch|wait|"
                         "focus_window. Действие ввода и focus_window привязываются к окну: передайте "
                         "window (hwnd) и pid из наблюдения; ввод идёт только в это окно и только "
                         "когда оно впереди — иначе отказ (нужны новое наблюдение и одобрение; фокус "
                         "сервер сам не возвращает). focus_window: window+pid окна задачи. "
                         "Цель — имя элемента (target); при двух одинаковых именах укажите index "
                         "из наблюдения. Координаты x,y только с coordinate_fallback=true и target. "
                         "expect: {window_title_contains, contains_text, absent_text, file_exists "
                         "(абсолютный путь), file_contains, file_sha256} проверяется по новому "
                         "экрану/диску; неизвестные поля делают результат НЕ подтверждённым. "
                         "launch: только приложения, разрешённые владельцем этой задаче (по "
                         "умолчанию notepad/calculator); ввод — только в их окна (с разрешением "
                         "владельца — только в окна, открытые этой задачей). Каждое действие, кроме "
                         "wait, — отдельное одобрение владельца. Удаление — semantic=delete; "
                         "платежи, учётные данные, секреты и необратимые внешние действия "
                         "(отправка, публикация, push, деинсталляция) не выполняются вовсе; само "
                         "слово semantic разрешением не является.",
             handler=_t_act,
             input_schema={"action": {"type": "string", "enum": list(KINDS)},
                           "generation": {"type": "integer"},
                           "window": {"type": "integer", "description": "hwnd окна из наблюдения"},
                           "pid": {"type": "integer", "description": "pid окна из наблюдения"},
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
             external_output=True, effect_hook=_act_effect, context_deny=_act_context_deny),
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


@router.get("/computer/catalog")
async def http_catalog():
    """Что владелец может разрешить задаче (каталог) и что не разрешается никогда."""
    from bossman.computer_operator.applist import APP_CATALOG, DEFAULT_APPS, HARD_DENIED_PROCESSES
    return {"apps": {k: {"label": v.get("label"), "process": list(v.get("process", ()))}
                     for k, v in APP_CATALOG.items()},
            "default": list(DEFAULT_APPS), "window_scopes": list(WINDOW_SCOPES),
            "never": sorted(HARD_DENIED_PROCESSES)}


async def _task_exists(svc, task_id: int) -> bool:
    import sqlalchemy as sa
    from ..db import tasks as tasks_t
    async with svc.db.session() as s:
        return (await s.execute(sa.select(tasks_t.c.id).where(tasks_t.c.id == task_id))).first() is not None


def _write_grants(svc, grants: dict) -> None:
    path = _grants_path(svc)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(grants, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


@router.get("/computer/tasks/{task_id}/apps")
async def http_task_apps(task_id: int, request: Request):
    from bossman.computer_operator.applist import DEFAULT_APPS
    svc = request.app.state.svc
    try:
        rec = task_grant(svc, task_id)
    except ActRefused as exc:
        raise HTTPException(409, {"message": str(exc)})
    if rec is None:
        return {"task_id": task_id, "granted": False, "apps": list(DEFAULT_APPS), "windows": "any"}
    return {"task_id": task_id, "granted": True, **rec}


@router.put("/computer/tasks/{task_id}/apps")
async def http_grant_task_apps(task_id: int, request: Request):
    """Разрешение владельца: какие приложения из каталога эта задача может
    запускать и в окна каких (только своих) слать ввод. Только панель/CLI
    владельца (сессия + CSRF); у модели нет инструмента, который сюда доходит."""
    from bossman.computer_operator.applist import normalize_grant
    svc = request.app.state.svc
    hint = 'тело: JSON {"apps": ["notepad", ...], "windows": "launched"}'
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(422, {"message": hint})
    if not isinstance(body, dict):
        raise HTTPException(422, {"message": hint})
    try:
        apps = normalize_grant(body.get("apps"))
    except ValueError as exc:
        raise HTTPException(422, {"message": str(exc)})
    windows = str(body.get("windows") or "launched")
    if windows not in WINDOW_SCOPES:
        raise HTTPException(422, {"message": f"windows: одно из {', '.join(WINDOW_SCOPES)}"})
    if not await _task_exists(svc, task_id):
        raise HTTPException(404, {"message": f"задача #{task_id} не найдена"})
    try:
        grants = _read_grants(svc)
    except ActRefused as exc:
        raise HTTPException(409, {"message": str(exc)})
    rec = {"apps": list(apps), "windows": windows, "granted_by": "owner", "granted_at": time.time()}
    grants[str(task_id)] = rec
    _write_grants(svc, grants)
    await svc.bus.emit("computer.grant", task_id=task_id, apps=list(apps), windows=windows, by="owner")
    return {"task_id": task_id, "granted": True, **rec}


@router.delete("/computer/tasks/{task_id}/apps")
async def http_revoke_task_apps(task_id: int, request: Request):
    svc = request.app.state.svc
    try:
        grants = _read_grants(svc)
    except ActRefused as exc:
        raise HTTPException(409, {"message": str(exc)})
    existed = grants.pop(str(task_id), None) is not None
    if existed:
        _write_grants(svc, grants)
        await svc.bus.emit("computer.grant", task_id=task_id, apps=[], windows=None, by="owner")
    return {"task_id": task_id, "granted": False, "revoked": existed}


async def _setup(svc) -> None:
    for spec in SPECS:
        REGISTRY.register(spec)


FEATURE = Feature(name="tools_computer", router=router, setup=_setup)
