"""gmail.*, crm.*, docs.read — декларации для v0.4 (Fresh Vibes).

Права и подтверждения работают уже сейчас (gmail.send / crm.write — confirm по
умолчанию); сами обработчики подключаются на этапе v0.4. Лимиты — из 10.4:
gmail.read: отправитель, тема, дата, первые 600 символов, ≤10 писем;
crm.read: только запрошенные поля.
"""
from __future__ import annotations

from . import ToolContext, ToolDef, ToolResult, register


def _stub(name: str):
    async def handler(args: dict, ctx: ToolContext) -> ToolResult:
        return ToolResult(f"{name}: коннектор ещё не настроен (этап v0.4)",
                          one_line=f"{name}: не настроен", error=True)
    return handler


register(ToolDef("gmail.read", "Письма: отправитель, тема, дата, первые 600 символов (≤10 писем).",
                 "read", _stub("gmail.read"),
                 params={"query": {"type": "string"}, "id": {"type": "string"}}, token_limit=1000))
register(ToolDef("gmail.draft", "Создать черновик письма (без отправки).",
                 "write", _stub("gmail.draft"),
                 params={"to": {"type": "string"}, "subject": {"type": "string"},
                         "body": {"type": "string"}}, required=["to", "body"]))
register(ToolDef("gmail.send", "Отправить письмо — необратимо.",
                 "send", _stub("gmail.send"),
                 params={"draft_id": {"type": "string"}}, confirm_default=True))
register(ToolDef("crm.read", "Запись CRM: только запрошенные поля.",
                 "read", _stub("crm.read"),
                 params={"id": {"type": "string"}, "fields": {"type": "array", "items": {"type": "string"}}},
                 token_limit=2000))
register(ToolDef("crm.write", "Изменить запись CRM — с подтверждением.",
                 "write", _stub("crm.write"),
                 params={"id": {"type": "string"}, "fields": {"type": "object"}}, confirm_default=True))
register(ToolDef("docs.read", "Документы клиники: выдержка по запросу.",
                 "read", _stub("docs.read"), params={"query": {"type": "string"}}, token_limit=2000))
