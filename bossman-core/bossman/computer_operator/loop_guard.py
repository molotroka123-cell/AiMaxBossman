"""Loop / no-progress protection для Computer Operator.

Проблема: цикл менеджера повторял НЕИЗМЕННОЕ действие против НЕИЗМЕННОГО
состояния, пока не исчерпается replan-бюджет (до 20 раз) — «слепой кликер».
Здесь — детерминированный дешёвый детектор, который останавливает это раньше.

Никаких моделей и сети: только подписи действия и состояния (sha1 по
канонизированному кортежу) и ограниченная история. O(1) на шаг.

Детектируем:
* repeat        — то же действие против того же состояния подряд;
* no_progress   — действие выполняется, но состояние не меняется (before==after);
* verify_loop   — одно и то же действие подряд не проходит верификацию;
* oscillation   — состояние скачет между двумя значениями (A,B,A,B).
"""
from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass
from typing import Any


def _sha(*parts: Any) -> str:
    blob = json.dumps(parts, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def action_signature(action: Any) -> str:
    """Подпись действия: вид + цель + текст + аргументы (без изменчивых id)."""
    kind = getattr(getattr(action, "kind", None), "value", getattr(action, "kind", ""))
    return _sha(kind, getattr(action, "target", None), getattr(action, "text", None),
                getattr(action, "args", None))


def state_signature(observation: Any) -> str:
    """Подпись наблюдаемого состояния: приоритет — СТРУКТУРНЫЕ признаки.

    Скриншот в подпись НЕ входит: пиксельный шум (курсор, часы, анти-алиасинг)
    делал бы каждое состояние «новым» и глушил детектор.
    """
    if observation is None:
        return "none"
    fg = getattr(observation, "foreground", None) or {}
    tree = getattr(observation, "ui_tree", None)
    return _sha(fg.get("app"), fg.get("title"), fg.get("url"),
                _tree_fingerprint(tree), (getattr(observation, "summary", "") or "")[:2000])


def _tree_fingerprint(tree: Any) -> Any:
    """Компактный отпечаток UI/DOM-дерева (структура важнее полного содержимого)."""
    if tree is None:
        return None
    if isinstance(tree, dict):
        els = tree.get("elements")
        if isinstance(els, list):
            return [(str(e.get("control_type", ""))[:40], str(e.get("name", ""))[:80])
                    for e in els[:200] if isinstance(e, dict)]
    if isinstance(tree, list):
        return [str(x)[:80] for x in tree[:200]]
    return str(tree)[:2000]


@dataclass(frozen=True)
class GuardVerdict:
    tripped: bool
    kind: str = ""      # repeat | no_progress | verify_loop | oscillation
    reason: str = ""


@dataclass(frozen=True)
class _Record:
    action_sig: str
    before_sig: str
    after_sig: str
    verified: bool | None


class LoopGuard:
    """Ограниченная история шагов + дешёвые проверки застревания."""

    def __init__(self, *, max_identical: int = 3, max_no_progress: int = 3,
                 max_verify_fail: int = 3, history: int = 16) -> None:
        self.max_identical = max_identical
        self.max_no_progress = max_no_progress
        self.max_verify_fail = max_verify_fail
        self._hist: deque[_Record] = deque(maxlen=max(4, history))

    def record(self, action: Any, before: Any, after: Any, verified: bool | None) -> None:
        self._hist.append(_Record(action_signature(action), state_signature(before),
                                  state_signature(after), verified))

    def reset(self) -> None:
        """Состояние сменилось внешне (takeover/recover) — история неактуальна."""
        self._hist.clear()

    def check(self, action: Any, before: Any) -> GuardVerdict:
        """Вызывается ПЕРЕД исполнением. Решает, не застряли ли мы."""
        if not self._hist:
            return GuardVerdict(False)
        a_sig, s_sig = action_signature(action), state_signature(before)

        tail = list(self._hist)[-self.max_identical:]
        if len(tail) >= self.max_identical and all(
                r.action_sig == a_sig and r.before_sig == s_sig for r in tail):
            return GuardVerdict(True, "repeat",
                                f"одно и то же действие против того же состояния "
                                f"{len(tail)}× подряд — исполнение остановлено")

        prog = list(self._hist)[-self.max_no_progress:]
        if len(prog) >= self.max_no_progress and all(
                r.action_sig == a_sig and r.before_sig == r.after_sig for r in prog):
            return GuardVerdict(True, "no_progress",
                                f"действие выполнялось {len(prog)}× и не меняло состояние")

        vf = [r for r in list(self._hist)[-self.max_verify_fail:] if r.action_sig == a_sig]
        if len(vf) >= self.max_verify_fail and all(r.verified is False for r in vf):
            return GuardVerdict(True, "verify_loop",
                                f"действие {len(vf)}× подряд не прошло верификацию")

        if self._oscillating():
            return GuardVerdict(True, "oscillation",
                                "состояние колеблется между двумя значениями — прогресса нет")
        return GuardVerdict(False)

    def _oscillating(self) -> bool:
        sigs = [r.after_sig for r in self._hist][-4:]
        if len(sigs) < 4:
            return False
        a, b = sigs[0], sigs[1]
        return a != b and sigs == [a, b, a, b]
