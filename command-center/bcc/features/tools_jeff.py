"""Bossman -> Jeff bridge: ``jeff.broadcast`` sends one announcement from Jeff to the people who already talk to him.

Owner request 2026-10-01 in the Bossman chat: «Разошли через Jeff всем приветики» - the executor answered «Я не знаю, кто такой Jeff»,
because Bossman had no tool that reaches Jeff. The tool is always an ASK (permission ``channel.send``, a hard floor): the approval
preview carries the exact text and the number of recipients, and nothing is sent without the owner's decision. Only people who
already wrote to Jeff can be reached; the owner's private block rule applies; ids are never shown.
"""
from __future__ import annotations

from ..tools import REGISTRY, ToolResult, ToolSpec
from . import Feature


def _broadcast_effect(args: dict):
    from ..pit import broadcast
    text = str((args or {}).get("text") or "").strip()
    if not text:
        return ("deny", "пустой текст рассылки")
    if len(text) > broadcast.MAX_TEXT:
        return ("deny", f"текст длиннее {broadcast.MAX_TEXT} знаков")
    return ("ask", "рассылка от имени Jeff людям, которые ему уже писали: одно одобрение - одна рассылка")


async def _t_broadcast(args, ctx):
    from ..pit import broadcast
    text = str((args or {}).get("text") or "")
    try:
        result = await broadcast.send(ctx.svc.settings.data_dir, text)
    except ValueError as exc:
        return ToolResult(content=f"рассылка не выполнена: {exc}", one_line="jeff.broadcast: отказ", error=True)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(content=f"рассылка не выполнена: {type(exc).__name__}", one_line="jeff.broadcast: ошибка",
                          error=True)
    line = f"Jeff разослал: доставлено {result['sent']}, не доставлено {result['failed']}, заблокировано правилом владельца {result['blocked']}"
    return ToolResult(content=line, one_line="jeff.broadcast: " + line.split(":", 1)[1].strip(), data=result)


SPECS = [
    ToolSpec(name="jeff.broadcast",
             description="Разослать одно сообщение от имени Jeff всем, кто ему уже писал в Telegram (привет, анонс). "
                         "text - готовый текст по-русски, коротко и по-человечески. Каждое обращение - отдельное одобрение владельца.",
             handler=_t_broadcast,
             input_schema={"text": {"type": "string", "description": "текст рассылки"}},
             required=["text"], category="send", permission="channel.send",
             source="jeff", default_effect="ask", timeout_seconds=300.0, idempotent=False,
             effect_hook=_broadcast_effect),
]


async def _setup(svc) -> None:
    for spec in SPECS:
        REGISTRY.register(spec)


FEATURE = Feature(name="tools_jeff", router=None, setup=_setup)
