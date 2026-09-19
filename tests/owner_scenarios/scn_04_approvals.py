"""Владельческие сценарии 12–15: круг одобрения владельца.

Цепочка, а не модульная проверка: рискованное действие встаёт в очередь
подтверждений ПРОДУКТА, отказ предотвращает эффект, верное одобрение
возобновляет задачу ровно один раз, а Telegram проносит круг целиком.

Модульный уровень «одобрение потребляется ровно один раз» уже закрыт
`bossman-core/tests/test_approval_consumed_exactly_once.py` (BL-090) и здесь
НЕ дублируется: сценарий 14 идёт сквозной цепочкой и смотрит на ЭФФЕКТ.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from scenario_runner import INSTALLED_PRODUCT, scenario  # noqa: E402

RISKY = "shell.run"
PREVIEW = "rm -rf /данные/проект  (cwd=/данные)"


class _Effect:
    """Внешний эффект под охраной одобрения: считает КАЖДОЕ применение."""

    def __init__(self, target: Path) -> None:
        self.target = target
        self.applications = 0

    def apply(self) -> None:
        self.applications += 1
        self.target.write_text(f"эффект применён {self.applications}\n", encoding="utf-8")


async def _approvals(path: Path):
    """Очередь подтверждений ПРОДУКТА на файловой SQLite."""
    from bcc.db import Database  # noqa: PLC0415
    from bcc.events import EventBus  # noqa: PLC0415
    from bcc.approvals import Approvals  # noqa: PLC0415

    db = Database(f"sqlite+aiosqlite:///{path}")
    await db.create_all()
    return db, Approvals(db, EventBus())


@scenario(id="OS-12", depth=INSTALLED_PRODUCT)
def os12_risky_action_waits_for_approval(ctx) -> None:
    """Рискованное действие не исполняется, а встаёт в очередь со статусом pending."""
    async def run():
        db, approvals = await _approvals(ctx.path("cc", "bcc.sqlite3"))
        effect = _Effect(ctx.path("работа", "эффект.txt"))
        row = await approvals.create(RISKY, PREVIEW)
        pending = await approvals.list("pending")
        consumed = await approvals.consume(row["id"], kind=RISKY, preview=PREVIEW)
        return db, effect, row, pending, consumed

    db, effect, row, pending, consumed = asyncio.run(run())
    ctx.reached_installed_product("bcc.approvals установленного command-center")
    ctx.positive("рискованное действие встало в очередь подтверждений продукта",
                 row.get("status") == "pending" and row.get("kind") == RISKY,
                 f"status={row.get('status')}")
    ctx.positive("владелец видит ожидающее решение в очереди",
                 any(r["id"] == row["id"] for r in pending), f"ожидает={len(pending)}")
    ctx.negative("пока решения нет, эффект не произошёл",
                 effect.applications == 0 and not effect.target.exists())
    ctx.negative("нерешённое подтверждение нельзя потребить",
                 consumed is False, "consume на pending обязан отказать")
    _ = db


@scenario(id="OS-13", depth=INSTALLED_PRODUCT)
def os13_rejection_prevents_the_effect(ctx) -> None:
    """ОТКАЗ владельца обязан предотвратить эффект, а не просто «отметиться»."""
    async def run():
        db, approvals = await _approvals(ctx.path("cc", "bcc.sqlite3"))
        effect = _Effect(ctx.path("работа", "эффект.txt"))
        row = await approvals.create(RISKY, PREVIEW)
        decided = await approvals.decide(row["id"], False, by="owner")
        allowed = await approvals.consume(row["id"], kind=RISKY, preview=PREVIEW)
        if allowed:
            effect.apply()
        second = await approvals.decide(row["id"], True, by="owner")
        allowed_again = await approvals.consume(row["id"], kind=RISKY, preview=PREVIEW)
        if allowed_again:
            effect.apply()
        return db, effect, decided, allowed, second, allowed_again

    db, effect, decided, allowed, second, allowed_again = asyncio.run(run())
    ctx.reached_installed_product("bcc.approvals установленного command-center")
    ctx.positive("отказ владельца записан продуктом",
                 decided and decided.get("status") == "rejected",
                 f"status={decided.get('status')}")
    ctx.negative("после отказа эффект НЕ произошёл",
                 allowed is False and effect.applications == 0 and not effect.target.exists())
    ctx.negative("отказ нельзя переиграть поздним «одобряю»",
                 second.get("status") == "rejected" and allowed_again is False,
                 f"после повторного решения status={second.get('status')}")
    _ = db


@scenario(id="OS-14", depth=INSTALLED_PRODUCT)
def os14_approval_resumes_the_task_exactly_once(ctx) -> None:
    """Сквозная цепочка: pending → одобрение → возобновление → эффект РОВНО ОДИН раз."""
    async def run():
        db, approvals = await _approvals(ctx.path("cc", "bcc.sqlite3"))
        effect = _Effect(ctx.path("работа", "эффект.txt"))
        row = await approvals.create(RISKY, PREVIEW)
        await approvals.decide(row["id"], True, by="owner")

        # Возобновление задачи: эффект применяется только под потреблённое одобрение.
        first = await approvals.consume(row["id"], kind=RISKY, preview=PREVIEW)
        if first:
            effect.apply()
        # Переигрывание того же намерения (перезапуск, дубль очереди, повтор шага).
        replay = await approvals.consume(row["id"], kind=RISKY, preview=PREVIEW)
        if replay:
            effect.apply()
        # Чужое намерение под тем же одобрением.
        other = await approvals.consume(row["id"], kind=RISKY, preview="rm -rf /другое")
        if other:
            effect.apply()
        final = await approvals.list(None)
        return db, effect, first, replay, other, final

    db, effect, first, replay, other, final = asyncio.run(run())
    ctx.reached_installed_product("bcc.approvals установленного command-center")
    ctx.positive("верное одобрение возобновило задачу",
                 first is True and effect.applications == 1,
                 f"применений={effect.applications}")
    ctx.positive("после потребления запись помечена продуктом как использованная",
                 any(r["status"] == "consumed" for r in final),
                 f"статусы={[r['status'] for r in final]}")
    ctx.negative("переигрывание того же одобрения НЕ дало второго эффекта",
                 replay is False and effect.applications == 1)
    ctx.negative("чужое намерение под тем же одобрением отвергнуто",
                 other is False and effect.applications == 1)
    _ = db
