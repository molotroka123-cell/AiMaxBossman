"""V2.2 §9 — изолированный рабочий каталог агента.

Проблема, которую это закрывает. Разрешённые корни (`terminal.roots`, code
roots) — общие: любой агент, получивший `terminal.run`, мог писать в любой файл
внутри корня, в том числе в промежуточные файлы соседнего агента той же миссии.
Одна миссия из пяти агентов — это пять писателей в один каталог без всякой
границы между ними. Ошибка одного тихо портит работу другого, и в логах это
выглядит как «второй агент сам сломался».

Решение — не новый слой прав, а одно правило поверх уже существующей проверки
корней:

    каталог `<data_dir>/scratch/<mission>/<agent>/` принадлежит РОВНО одному
    агенту одной миссии; всё внутри `<data_dir>/scratch/`, что лежит вне
    собственного каталога вызывающего, запрещено — и на чтение тоже.

Почему и на чтение. Черновики соседа — это его незаконченные выводы; агент,
подсмотревший их, начинает опираться на данные, которые через минуту будут
переписаны. Изоляция без чтения была бы половинчатой.

Что правило НЕ делает: не трогает обычные корни проектов. Файл вне
`<data_dir>/scratch/` этот модуль не касается — им занимается прежняя проверка
корней, и она не ослаблена ни на шаг.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

SCRATCH_DIRNAME = "scratch"
_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def base_dir(settings: Any) -> Path:
    return Path(settings.data_dir) / SCRATCH_DIRNAME


def _slug(prefix: str, value: Any) -> str:
    raw = str(value if value not in (None, "") else "none")
    return f"{prefix}-{_SAFE.sub('_', raw)[:64]}"


def owner_dir(settings: Any, *, mission_id: Any = None, agent_id: Any = None,
              task_id: Any = None) -> Path:
    """`<base>/<mission>/<agent>`.

    Задача без миссии — тоже владелец: одиночные задачи разных агентов не
    должны сливаться в один каталог только потому, что миссии у них нет.
    """
    scope = _slug("mission", mission_id) if mission_id not in (None, "") \
        else _slug("task", task_id)
    return base_dir(settings) / scope / _slug("agent", agent_id)


def for_context(ctx: Any) -> Path:
    task = ctx.task if isinstance(getattr(ctx, "task", None), dict) else {}
    agent = ctx.agent if isinstance(getattr(ctx, "agent", None), dict) else {}
    return owner_dir(ctx.svc.settings, mission_id=task.get("mission_id"),
                     agent_id=agent.get("id"), task_id=task.get("id"))


def ensure(path: Path) -> Path:
    """Создать каталог владельца. 0700 — черновики агента не публичны."""
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass                                   # на некоторых ФС chmod не работает
    return path


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def violation(settings: Any, own: Path | None, target: Path) -> str:
    """Пустая строка — доступ разрешён. Иначе — причина отказа для модели.

    Ответ намеренно не раскрывает, чей это каталог: агенту незачем знать имена
    соседей, ему нужно знать, что сюда нельзя.
    """
    base = base_dir(settings)
    if not _under(target, base):
        return ""                              # вне scratch — не наша граница
    if own is not None and _under(target, own):
        return ""
    return (f"каталог {target} — рабочая область другого агента; "
            f"пишите и читайте только в своей: "
            f"{own if own is not None else base}")


def check(ctx: Any, target: Path) -> str:
    """Проверка для вызова инструмента. Свой каталог создаётся по требованию."""
    own = for_context(ctx)
    if _under(target, own):
        ensure(own)
    return violation(ctx.svc.settings, own, target)


__all__ = ["SCRATCH_DIRNAME", "base_dir", "owner_dir", "for_context", "ensure",
           "violation", "check"]
