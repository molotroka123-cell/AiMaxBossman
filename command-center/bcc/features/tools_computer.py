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
    координат без названной цели).
Подтверждения, «Стоп», журнал — канонические механизмы Command Center
(tool-loop ASK/AUTO, bus → Flight Recorder).

Контракт для модели:
  1. computer.observe → поколение `generation`, окно, элементы UIA с центрами.
  2. computer.act(generation=…) → действие ТОЛЬКО по свежему наблюдению;
     устаревшее поколение — отказ «перечитайте экран».
  3. Цель — по имени элемента (UIA). Координаты — лишь запасной путь:
     нужны `coordinate_fallback=true` и имя цели; перед кликом экран
     перечитывается, и точка обязана лежать внутри названного элемента.
  4. После действия экран перечитывается сам; `expect` проверяется по новому
     наблюдению → verified true/false. Без `expect` — verified=null, а не «ок».
  5. «Стоп» владельца (POST /api/computer/stop) обрывает набор между порциями
     и блокирует новые действия до «Продолжить».
"""
from __future__ import annotations

import asyncio
import platform
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..tools import REGISTRY, ToolResult, ToolSpec
from . import Feature

router = APIRouter()

MAX_ELEMENTS = 150
MAX_VALUE_CHARS = 4000
SETTLE_S = 0.6
KINDS = ("focus", "click", "double_click", "type", "hotkey", "scroll", "invoke", "launch", "wait",
         "focus_window")
# Действия, которые шлют ввод в ТЕКУЩЕЕ окно переднего плана. Перед ними окно
# обязано быть тем же, что в наблюдении: живой прогон 2026-09-21 показал, что
# Windows не отдаёт фокус только что запущенному Блокноту, и набор ушёл в поиск
# «Параметров». Ввод «куда-то» — хуже отказа.
INPUT_KINDS = frozenset({"focus", "click", "double_click", "type", "hotkey", "scroll", "invoke"})


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
    generation: int = 0
    last: dict[str, Any] = field(default_factory=dict)
    stop: threading.Event = field(default_factory=threading.Event)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    desktop: Any = None
    shots: Any = None
    launcher: Any = None


def _state(svc) -> ComputerState:
    st = getattr(svc, "_computer_state", None)
    if st is None:
        st = ComputerState()
        svc._computer_state = st
    if st.desktop is None:
        core = _core()

        class _Desktop(core["WindowsDesktop"]):
            _element = staticmethod(_element_with_rect)

        st.desktop = _Desktop()
        st.desktop.set_interrupt(st.stop)
        shots_dir = Path(svc.settings.data_dir) / "computer" / "screens"
        st.shots = core["LocalScreenshotProvider"](root=shots_dir, retention=32)
        st.launcher = core["AppLaunchAdapter"]()
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
    elements = list((tree or {}).get("elements") or [])[:MAX_ELEMENTS]
    for i, el in enumerate(elements):
        el["i"] = i
    obs = {"generation": st.generation, "observed_at": time.time(),
           "window": {k: fg.get(k) for k in ("title", "app", "handle", "error") if k in fg},
           "elements": elements, "screenshot": shot, "stopped": st.stop.is_set()}
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


def verify(obs: dict, expect: dict | None) -> tuple[bool | None, list[str]]:
    """Постусловие по СВЕЖЕМУ наблюдению.

    Пустое expect → None (не проверяли). Непустое, но с неизвестным или
    неверно типизированным ключом → False (проверить не смогли — считаем НЕ
    подтверждённым, fail closed). verified=True возможно ТОЛЬКО когда хотя бы
    одно ИЗВЕСТНОЕ условие реально проверено и все известные сошлись."""
    KNOWN = ("window_title_contains", "contains_text", "absent_text")
    if not isinstance(expect, dict) or not expect:
        return None, ["постусловие не задано — результат не проверен"]
    clean, bad = {}, []
    for k, v in expect.items():
        if isinstance(v, (str, int, float, bool)) and str(v).strip():
            clean[k] = str(v)
        elif str(v or "").strip():
            bad.append(k)                       # непустое, но не скаляр — неверный тип
    unknown = sorted(set([k for k in clean if k not in KNOWN] + bad))
    if unknown:
        return False, [f"ожидание {', '.join(unknown)} не поддержано — "
                       f"проверить не смогли, считаю НЕ подтверждённым"]
    if not clean:
        return None, ["постусловие не задано — результат не проверен"]
    title = str((obs.get("window") or {}).get("title") or "").lower()
    hay = _haystack(obs).lower()
    notes, ok, checked = [], True, 0
    if "window_title_contains" in clean:
        hit = clean["window_title_contains"].lower() in title; ok &= hit; checked += 1
        notes.append(f"заголовок {'содержит' if hit else 'НЕ содержит'} "
                     f"«{clean['window_title_contains']}»")
    if "contains_text" in clean:
        hit = clean["contains_text"].lower() in hay; ok &= hit; checked += 1
        notes.append(f"на экране {'есть' if hit else 'НЕТ'} «{clean['contains_text']}»")
    if "absent_text" in clean:
        hit = clean["absent_text"].lower() not in hay; ok &= hit; checked += 1
        notes.append(f"«{clean['absent_text']}» {'отсутствует' if hit else 'ВСЁ ЕЩЁ на экране'}")
    if not checked:
        return None, ["постусловие не задано — результат не проверен"]
    return ok, notes


def _find(obs: dict, target: str) -> list[dict]:
    t = target.strip().lower()
    exact = [e for e in obs.get("elements") or [] if str(e.get("name") or "").strip().lower() == t]
    return exact or [e for e in obs.get("elements") or []
                     if t and t in str(e.get("name") or "").lower()]


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


def _top_windows() -> list[tuple[int, str]]:
    from pywinauto import Desktop
    out = []
    for w in Desktop(backend="uia").windows():
        try:
            if w.is_visible():
                out.append((int(w.handle), str(w.window_text() or "")))
        except Exception:  # noqa: BLE001
            continue
    return out


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


async def act(svc, args: dict, *, approved: bool = False) -> dict[str, Any]:
    """Одно действие по свежему наблюдению + автоматическая проверка результата."""
    ok, why = availability()
    if not ok:
        raise ActRefused(why)
    core = _core()
    AK = core["ActionKind"]
    st = _state(svc)
    kind = str(args.get("action") or "").strip().lower()
    if kind not in KINDS:
        raise ActRefused(f"action: одно из {', '.join(KINDS)}")
    if st.stop.is_set():
        raise ActRefused("владелец нажал «Стоп»: действия на рабочем столе остановлены до «Продолжить»")
    target = str(args.get("target") or "").strip()
    async with st.lock:                            # один рабочий стол — одно действие за раз
        if kind not in ("launch", "wait"):
            gen = args.get("generation")
            if not st.last or not isinstance(gen, int) or gen != st.generation:
                raise ActRefused(
                    f"наблюдение устарело (generation={gen!r}, текущее {st.generation}): "
                    f"вызовите computer.observe и действуйте по свежему экрану")
        before = st.last
        if kind in INPUT_KINDS:
            fg_now = await st.desktop.foreground()
            want = (before.get("window") or {}).get("handle")
            if want and int(fg_now.get("handle") or 0) != int(want):
                raise ActRefused(
                    f"фокус ушёл: наблюдалось окно «{(before.get('window') or {}).get('title')}», "
                    f"а сейчас впереди «{fg_now.get('title')}». Ввод не отправлен — вызовите "
                    f"computer.observe (или focus_window) и действуйте по свежему экрану")
        x, y = args.get("x"), args.get("y")
        coords = isinstance(x, int) and isinstance(y, int)
        mapping = {"focus": AK.FOCUS, "click": AK.UI_INVOKE if (target and not coords) else AK.CLICK,
                   "double_click": AK.DOUBLE_CLICK, "type": AK.TYPE, "hotkey": AK.HOTKEY,
                   "scroll": AK.SCROLL, "invoke": AK.UI_INVOKE, "launch": AK.APP_LAUNCH,
                   "wait": AK.WAIT, "focus_window": AK.WAIT}
        a = core["ComputerAction"].make(
            mapping[kind], target=target or None, text=args.get("text"),
            args={k: args[k] for k in ("x", "y", "keys", "clicks", "coordinate_fallback", "semantic",
                                       "interval") if k in args},
            confidence=float(args.get("confidence") or 1.0), source="planner")
        fg = dict((before or {}).get("window") or {})
        pol_obs = core["Observation"]("obs", time.time(), fg, "", None, None, False, st.generation)
        decision = core["ComputerPolicy"]().classify(a, mode=core["TaskMode"].CONTROL,
                                                     observation=pol_obs)
        if not decision.allow:
            raise ActRefused(f"политика запретила действие: {decision.reason}")
        if decision.requires_approval:
            # Одобрение засчитывается ТОЛЬКО когда (1) до нас дошёл доверенный путь
            # после подтверждения владельца (approved задаёт обёртка _t_act, не
            # модель) и (2) effect-hook действительно МОГ вынести это последствие
            # на подтверждение (semantic или подпись цели/текста). Последствие,
            # известное лишь по переднему окну, владельцу не показывали — отказ,
            # чтобы модель назвала его semantic и владелец увидел ASK.
            asked = core["ComputerPolicy"].ask_consequence(args) is not None
            if not (approved and asked):
                raise ActRefused(
                    f"действие последствийное ({decision.reason}) — повторите вызов с "
                    f"semantic=\"{(decision.approval_kind or '').removeprefix('computer_')}\": "
                    f"тогда оно пройдёт через подтверждение владельца")

        if kind in ("click", "double_click") and coords:
            # Координатный запасной путь: ПЕРЕД кликом перечитываем экран и
            # требуем, чтобы точка лежала внутри названного элемента.
            fresh = await observe(svc, screenshot=False)
            hits = [e for e in _find(fresh, target) if "left" in e
                    and e["left"] <= x <= e["right"] and e["top"] <= y <= e["bottom"]]
            if not hits:
                raise ActRefused(f"координаты ({x},{y}) не попадают в элемент «{target}» на "
                                 f"свежем экране (generation {fresh['generation']}) — клик не выполнен")
        elif kind == "type" and target:
            # Живой прогон 2026-09-21: «Имя файла:» в диалоге сохранения — это и
            # ComboBox, и Edit внутри; фокус на обёртке, и вставка уходила в никуда.
            # Для ввода выбираем РЕДАКТИРУЕМЫЙ элемент и проверяем, что фокус на нём.
            handle = int((before.get("window") or {}).get("handle") or 0)
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
                await st.desktop.execute(
                    core["ComputerAction"].make(AK.FOCUS, target=editable[0]["name"]), None)
            elif kind == "type" and len(editable) > 1:
                raise ActRefused("в окне несколько полей ввода: "
                                 + ", ".join(f"«{e['name']}»" for e in editable[:8])
                                 + " — укажите target")

        if kind == "type" and args.get("replace"):
            await st.desktop.execute(core["ComputerAction"].make(
                AK.HOTKEY, args={"keys": ["ctrl", "a"]}), None)
        t0 = time.perf_counter()
        if kind == "wait":
            await asyncio.sleep(min(10.0, max(0.0, float(args.get("seconds") or 1))))
        elif kind == "launch":
            known = {h for h, _ in await asyncio.to_thread(_top_windows)}
            await st.launcher.execute(a, None)
            # Windows не отдаёт передний план процессу, запущенному из фона:
            # находим НОВОЕ окно и переводим фокус на него явно.
            new = []
            for _ in range(20):
                await asyncio.sleep(0.25)
                new = [(h, t) for h, t in await asyncio.to_thread(_top_windows) if h not in known]
                if new:
                    break
            if not new:
                raise ActRefused(f"«{target}» запущен, но новое окно не появилось за 5 с")
            if not await _focus(st, new[0][0]):
                raise ActRefused(f"окно «{new[0][1]}» появилось, но фокус получить не удалось")
        elif kind == "focus_window":
            if not target:
                raise ActRefused("focus_window: нужен target — часть заголовка окна")
            wins = [(h, t) for h, t in await asyncio.to_thread(_top_windows)
                    if target.lower() in t.lower()]
            if len(wins) != 1:
                raise ActRefused(f"окон с «{target}» в заголовке: {len(wins)} — "
                                 + ("уточните заголовок" if wins else "такого окна нет"))
            if not await _focus(st, wins[0][0]):
                raise ActRefused(f"не удалось вывести «{wins[0][1]}» на передний план")
        else:
            await st.desktop.execute(a, None)
        await asyncio.sleep(SETTLE_S)
        after = await observe(svc)
        verified, notes = verify(after, args.get("expect"))
        result = {"action": kind, "target": target, "coordinates": [x, y] if coords else None,
                  "before_generation": (before or {}).get("generation"),
                  "after_generation": after["generation"], "verified": verified, "checks": notes,
                  "elapsed_ms": int((time.perf_counter() - t0) * 1000), "observation": after}
    await svc.bus.emit("computer.act", action=kind, target=target[:200], verified=verified,
                       before=result["before_generation"], after=result["after_generation"],
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


async def _t_act(args, ctx):
    args = dict(args)
    # effect_hook уже поднял ASK для последствия (semantic ЛИБО подпись цели/текста);
    # сюда управление доходит только после подтверждения владельца. approved НЕ
    # выводится из полей модели — его ставит эта доверенная обёртка.
    try:
        res = await act(ctx.svc, args, approved=True)
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
                         "Цель — имя элемента (target); координаты x,y только с "
                         "coordinate_fallback=true и target. expect: {window_title_contains, "
                         "contains_text, absent_text} проверяется по новому экрану. launch: только "
                         "notepad/calculator. Последствийное (удалить/оплатить/отправить) — "
                         "semantic=<вид>, пойдёт через подтверждение.",
             handler=_t_act,
             input_schema={"action": {"type": "string", "enum": list(KINDS)},
                           "generation": {"type": "integer"},
                           "target": {"type": "string"}, "text": {"type": "string"},
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
    st = getattr(request.app.state.svc, "_computer_state", None)
    return {"available": ok, "detail": why or "Windows UIA + pyautogui готовы",
            "stopped": bool(st and st.stop.is_set()), "generation": st.generation if st else 0,
            "tools": [s.name for s in SPECS]}


@router.post("/computer/observe")
async def http_observe(request: Request):
    try:
        return await observe(request.app.state.svc)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, {"message": f"наблюдение недоступно: {exc}"})


@router.post("/computer/stop")
async def http_stop(request: Request):
    """«Стоп» владельца: обрывает набор текста и блокирует новые действия."""
    svc = request.app.state.svc
    st = getattr(svc, "_computer_state", None) or ComputerState()
    svc._computer_state = st
    st.stop.set()
    await svc.bus.emit("computer.stop", by="owner")
    return {"stopped": True}


@router.post("/computer/resume")
async def http_resume(request: Request):
    svc = request.app.state.svc
    st = getattr(svc, "_computer_state", None) or ComputerState()
    svc._computer_state = st
    st.stop.clear()
    await svc.bus.emit("computer.resume", by="owner")
    return {"stopped": False}


async def _setup(svc) -> None:
    for spec in SPECS:
        REGISTRY.register(spec)


FEATURE = Feature(name="tools_computer", router=router, setup=_setup)
