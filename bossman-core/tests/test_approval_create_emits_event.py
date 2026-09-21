"""`approvals.create()` падала TypeError при КАЖДОМ вызове.

Найдено прогоном владельческого сценария №15 против НАСТОЯЩЕГО PostgreSQL, а не
чтением. Строка:

    events.emit("approval.created", id=approval_id, kind=kind, tool=tool, ...)

`events.emit` объявлена как `def emit(kind: str, **data)` — её ПЕРВЫЙ параметр
называется `kind`. Имя события уходило в него позиционно, и одновременно
передавался `kind=` ключом:

    TypeError: emit() got multiple values for argument 'kind'

То есть одобрение нельзя было создать вовсе: ни одно рискованное действие не
могло дойти до владельца этим путём.

Почему никто не заметил: `create()` достижима только с живым PostgreSQL, а все
существующие наборы подменяют `approvals.create`/`decide` двойниками и настоящий
код не исполняют — это же было причиной BL-090.

Столкновение здесь не только техническое: `emit` САМА кладёт в полезную нагрузку
`payload["kind"] = <имя события>`. Поэтому «род одобрения» под тем же именем не
поместился бы, даже если бы вызов проходил, и поле переименовано в
`approval_kind`.
"""
from __future__ import annotations

import inspect

import pytest

from bossman import approvals, events


def test_emit_really_owns_the_name_kind():
    """Основание дефекта, закреплённое, а не пересказанное."""
    first = list(inspect.signature(events.emit).parameters)[0]
    assert first == "kind", f"первый параметр emit теперь {first!r} — запись ниже устарела"


def test_create_no_longer_passes_kind_as_a_keyword():
    import re
    source = inspect.getsource(approvals.create)
    # Граница слова обязательна: наивное `"kind=kind" not in source` ловило бы и
    # правильную форму `approval_kind=kind`. Поймал это собственный прогон.
    assert not re.search(r"(?<![\w])kind=kind", source), (
        "снова передаётся kind= ключом — emit() упадёт на КАЖДОМ создании одобрения")
    assert "approval_kind=kind" in source, "род одобрения потерян из события"


@pytest.mark.parametrize("bad", [
    'events.emit("approval.created", kind="shell")',
])
def test_the_collision_is_real_and_not_theoretical(bad):
    """Пара: столкновение воспроизводится, а исправленная форма проходит."""
    seen: list[dict] = []
    original = events._SINKS if hasattr(events, "_SINKS") else None
    with pytest.raises(TypeError, match="kind"):
        events.emit("approval.created", kind="shell")
    # Исправленная форма не падает.
    events.emit("approval.created", approval_kind="shell")
    assert original is None or events._SINKS is original


# --- подпись вебхука: отказ обязан быть ОТКАЗОМ, а не исключением мимо кода ---

def test_a_non_ascii_secret_header_is_denied_cleanly():
    """Заголовок приходит из сети; его содержимое нам не подконтрольно.

    `hmac.compare_digest` на строках с не-ASCII поднимает TypeError, и отказ
    уходил бы мимо `CallbackRejected` — вызывающий получил бы необработанное
    исключение вместо честного «webhook denied». Найдено прогоном сценария №15.
    """
    import asyncio

    from bossman.notifications.telegram_transport import CallbackRejected, TelegramTransport

    transport = TelegramTransport(
        store=None, bot_token_provider=lambda: "T", chat_id_provider=lambda: "42",
        webhook_secret_provider=lambda: "SECRET")

    async def call(header):
        return await transport.handle_webhook({}, header)

    for header in ("НЕ-ТОТ-СЕКРЕТ", "WRONG-ASCII", "", None):
        with pytest.raises(CallbackRejected):
            asyncio.run(call(header))


def test_the_right_secret_still_gets_past_the_signature_check():
    """Положительная половина: иначе «всё отвергается» тоже давало бы зелёное.

    Дальше проверка упирается в форму обновления, и это уже ДРУГОЙ отказ —
    важно, что не отказ подписи.
    """
    import asyncio

    from bossman.notifications.telegram_transport import CallbackRejected, TelegramTransport

    transport = TelegramTransport(
        store=None, bot_token_provider=lambda: "T", chat_id_provider=lambda: "42",
        webhook_secret_provider=lambda: "SECRET")
    with pytest.raises(CallbackRejected) as caught:
        asyncio.run(transport.handle_webhook({}, "SECRET"))
    assert "webhook denied" not in str(caught.value)
