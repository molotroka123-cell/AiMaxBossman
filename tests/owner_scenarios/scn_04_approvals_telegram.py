"""Владельческий сценарий 15: круг одобрения через Telegram — целиком.

Цепочка владельца: задача → нужно одобрение → транспорт Telegram →
смоделированный аутентифицированный ответ владельца → одобрение потреблено →
задача возобновилась → эффект произошёл ОДИН раз → завершение дошло до владельца.

Сотни контрактных тестов транспорта НЕ равны кругу одобрения, поэтому сценарий
ищет именно круг и честно называет, где он рвётся на этой ветке.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bossman-core"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "command-center"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from scenario_runner import INSTALLED_PRODUCT, scenario  # noqa: E402


@scenario(id="OS-15", depth=INSTALLED_PRODUCT)
def os15_telegram_carries_the_whole_approval_loop(ctx) -> None:
    """Круг одобрения через Telegram: где он есть, а где его нет."""
    import bcc.telegram_companion.adapters as companion_adapters  # noqa: PLC0415

    ctx.reached_installed_product(
        "bcc.telegram_companion этой ветки + bossman.telegram (фасад одобрений)")

    # Звено 1. Компаньон существует и умеет транспорт — но НЕ одобрения.
    doc = (companion_adapters.__doc__ or "")
    ctx.positive("Telegram Companion присутствует на ветке и несёт транспорт",
                 hasattr(companion_adapters, "Telegram") and hasattr(companion_adapters, "Core"),
                 f"адаптеры={[n for n in ('Telegram', 'Core', 'Models') if hasattr(companion_adapters, n)]}")
    ctx.negative("Companion НАМЕРЕННО не несёт API одобрений — круг им не замкнуть",
                 "approval api" in doc.lower() and "no shell" in doc.lower(),
                 f"его собственная граница: {doc.strip()[:120]}")

    # Звено 2. Круг одобрения живёт в другом месте — фасаде bossman.telegram.
    import bossman.telegram as approvals_transport  # noqa: PLC0415

    ctx.positive("транспорт одобрений продукта найден и предъявляет нужные ручки",
                 all(hasattr(approvals_transport, name)
                     for name in ("enabled", "ask_approval", "handle_webhook")),
                 "bossman.telegram: enabled/ask_approval/handle_webhook")
    ctx.negative("без настроенного бота транспорт одобрений закрыт, а не «как-нибудь»",
                 approvals_transport.enabled() is False,
                 "enabled() == False при отсутствии настройки владельца")

    # Звено 3. Дальше круг не замкнуть: очередь одобрений ядра живёт в PostgreSQL.
    ctx.owner_required(
        "круг одобрения через Telegram на этой ветке НЕ замкнут сценарием: "
        "bcc.telegram_companion по своему же контракту не имеет API одобрений "
        "(«No shell, desktop, arbitrary file-read, or approval API»), а круг, "
        "который умеет одобрения (bossman.telegram → bossman.approvals → "
        "notifications.telegram_transport), требует живого PostgreSQL и "
        "настроенного бота владельца: BOSSMAN_DATABASE_URL и токен не заданы. "
        "Нужны секреты владельца либо отдельное звено в "
        "tools/installed_product_chain.py, проносящее одобрение через Telegram.")
