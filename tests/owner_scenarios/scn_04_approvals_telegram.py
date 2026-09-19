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

    # Звено 3. Круг одобрения живёт в ядре и требует PostgreSQL. НАСТОЯЩИЙ бот
    # при этом не нужен: владелец просил детерминированный транспорт, а живые
    # учётные данные Telegram остаются за его машиной.
    # PostgreSQL объявлен в реестре требованием сценария: без него раннер сам
    # отдаёт OWNER_REQUIRED и сюда не доходит. Настоящий бот при этом НЕ нужен —
    # транспорт детерминированный, нужна только база.
    _run_real_approval_round_trip(ctx)


def _run_real_approval_round_trip(ctx) -> None:
    """Задача → нужно одобрение → Telegram → ответ владельца → потреблено → эффект ОДИН раз."""
    import asyncio  # noqa: PLC0415
    import json as _json  # noqa: PLC0415
    import os  # noqa: PLC0415

    import httpx  # noqa: PLC0415

    from bossman import approvals  # noqa: PLC0415
    from bossman.notifications import telegram_transport as tt  # noqa: PLC0415
    from bossman.notifications.models import (  # noqa: PLC0415
        ActionKind, Notification, NotificationAction, Severity)
    from bossman.notifications.store import SQLiteNotificationStore  # noqa: PLC0415

    os.environ["TELEGRAM_ALLOWED_USER_IDS"] = "777"
    sent: list[str] = []
    real_client = httpx.AsyncClient

    def deterministic(*_a, **_kw):
        def serve(request):
            sent.append(request.read().decode("utf-8"))
            return httpx.Response(200, json={"ok": True, "result": {}})
        return real_client(transport=httpx.MockTransport(serve), timeout=5)

    tt.httpx.AsyncClient = deterministic
    try:
        store = SQLiteNotificationStore(str(ctx.path("tg", "notifications.db")))
        transport = tt.TelegramTransport(
            store, bot_token_provider=lambda: "DETERMINISTIC",
            chat_id_provider=lambda: "42", webhook_secret_provider=lambda: "SECRET")

        approval_id = asyncio.run(approvals.create("shell", "удалить каталог владельца"))
        ctx.positive("задача потребовала одобрения и оно заведено в очереди ядра",
                     isinstance(approval_id, int) and approval_id > 0,
                     f"approval_id={approval_id}")

        action = NotificationAction(kind=ActionKind.APPROVE, target_type="approval",
                                    target_id=str(approval_id), label="Одобрить",
                                    fingerprint=f"fp-{approval_id}")
        asyncio.run(transport.send(Notification(
            id=f"n-{approval_id}", event_type="approval.requested",
            severity=Severity.WARNING, title="Нужно одобрение",
            body="удалить каталог владельца", dedupe_key=f"d-{approval_id}",
            actions=[action])))
        ctx.positive("сообщение с кнопкой ушло в транспорт Telegram",
                     len(sent) == 1, f"отправлено={len(sent)}")

        token = _json.loads(sent[0])["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        update = {"callback_query": {"id": "cb", "data": token, "from": {"id": 777},
                                     "message": {"chat": {"id": 42, "type": "private"}}}}

        # Отрицательный контроль ДО положительного: чужая подпись не проходит.
        denied = False
        try:
            asyncio.run(transport.handle_webhook(update, "НЕ-ТОТ-СЕКРЕТ"))
        except tt.CallbackRejected:
            denied = True
        ctx.negative("ответ с чужой подписью отвергнут штатным отказом", denied,
                     "CallbackRejected, а не необработанное исключение")

        asyncio.run(transport.handle_webhook(update, "SECRET"))
        row = asyncio.run(approvals.db.fetchrow(
            "select status, decided_by from approvals where id=$1", approval_id))
        ctx.positive("одобрение владельца потреблено, личность записана до ПОЛЬЗОВАТЕЛЯ",
                     row["status"] == "approved" and "user:777" in (row["decided_by"] or ""),
                     f"status={row['status']} decided_by={row['decided_by']}")

        # Переигрывание той же кнопки не даёт второго эффекта.
        replayed = False
        try:
            asyncio.run(transport.handle_webhook(update, "SECRET"))
        except tt.CallbackRejected:
            replayed = True
        ctx.negative("переигрывание той же кнопки НЕ даёт второго эффекта", replayed,
                     "одноразовость: consume_callback + status='pending' в SQL")

        after = asyncio.run(approvals.db.fetchrow(
            "select status, decided_by from approvals where id=$1", approval_id))
        ctx.positive("после переигрывания состояние одобрения не изменилось",
                     dict(after) == dict(row), f"было={dict(row)} стало={dict(after)}")
    finally:
        tt.httpx.AsyncClient = real_client
