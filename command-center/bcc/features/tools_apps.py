"""Feature — Apps как настоящий инструмент агента (MODULE 3 of
BCC-V2-UNIVERSAL-ACTION-EXECUTION-P1-001, продолжение MODULE 1/browser).

До этого файла bcc/features/apps_control.py умело реально запускать/
останавливать процессы (start_app/stop_app/process_info — настоящий
subprocess, не заглушка), но было доступно ТОЛЬКО через HTTP-кнопку
дашборда: ни одного ToolSpec для него не было зарегистрировано, поэтому
агент внутри обычной задачи (bcc.engine, `tasks.prompt`) не мог вызвать это
даже теоретически — точно тот же структурный разрыв, что был у браузера до
MODULE 1 (bcc/features/action_router.py): запрос «Открой приложение X»
структурно НЕ МОГ обернуться реальным действием, только текстом.

Здесь — тонкая обёртка вокруг УЖЕ ГОТОВЫХ start_app/stop_app/process_info:
вторая реализация не создаётся, повторяются все существующие ограничения
apps_control.py (флаг BOSSMAN_APPS_CONTROL_ENABLED, только известные
манифесты, свой процесс — свой pid). Инструмент, который порождает процесс
(`apps.start`) — ASK по умолчанию: цена ошибки здесь не «некрасивая
карточка», а чужой процесс в системе владельца (тот же принцип, что уже
описан в apps_control.py).
"""
from __future__ import annotations

from ..tools import REGISTRY, ToolResult, ToolSpec
from . import Feature
from . import apps_control as _apps


async def _start(args, ctx):
    app_id = str(args.get("app_id") or "").strip()
    if not app_id:
        return ToolResult(content="нужен app_id", one_line="apps.start: нет app_id", error=True)
    if not _apps.enabled():
        return ToolResult(
            content=(f"управление приложениями выключено (нужен "
                     f"{_apps.FLAG}=1 и перезапуск Command Center) — действие не выполнено"),
            one_line="apps.start: выключено", error=True)
    try:
        res = await _apps.start_app(app_id, ctx.svc.settings.data_dir)
    except Exception as exc:  # noqa: BLE001 — HTTPException (не найдено, порт занят, и т.п.)
        detail = getattr(exc, "detail", None)
        msg = detail.get("message") if isinstance(detail, dict) else str(exc)
        return ToolResult(content=str(msg), one_line=f"apps.start: {app_id}: ошибка", error=True)
    await ctx.svc.bus.emit("agent.tool_call", tool="apps", app_id=app_id, action="start")
    return ToolResult(content=res.get("message", ""), one_line=f"apps.start: {app_id}",
                      error=not res.get("ok", True), data=res)


async def _stop(args, ctx):
    app_id = str(args.get("app_id") or "").strip()
    if not app_id:
        return ToolResult(content="нужен app_id", one_line="apps.stop: нет app_id", error=True)
    if not _apps.enabled():
        return ToolResult(
            content=(f"управление приложениями выключено (нужен "
                     f"{_apps.FLAG}=1 и перезапуск Command Center) — действие не выполнено"),
            one_line="apps.stop: выключено", error=True)
    try:
        res = await _apps.stop_app(app_id)
    except Exception as exc:  # noqa: BLE001 — HTTPException (не найдено), и т.п.
        detail = getattr(exc, "detail", None)
        msg = detail.get("message") if isinstance(detail, dict) else str(exc)
        return ToolResult(content=str(msg), one_line=f"apps.stop: {app_id}: ошибка", error=True)
    await ctx.svc.bus.emit("agent.tool_call", tool="apps", app_id=app_id, action="stop")
    return ToolResult(content=res.get("message", ""), one_line=f"apps.stop: {app_id}",
                      error=not res.get("ok", True), data=res)


async def _status(args, ctx):
    app_id = str(args.get("app_id") or "").strip()
    if not app_id:
        return ToolResult(content="нужен app_id", one_line="apps.status: нет app_id", error=True)
    try:
        res = _apps.process_info(app_id, ctx.svc.settings.data_dir)
    except Exception as exc:  # noqa: BLE001 — HTTPException (не найдено)
        detail = getattr(exc, "detail", None)
        msg = detail.get("message") if isinstance(detail, dict) else str(exc)
        return ToolResult(content=str(msg), one_line=f"apps.status: {app_id}: ошибка", error=True)
    running = "запущено" if res.get("running") else "не запущено"
    return ToolResult(content=f"{app_id}: {running} (pid={res.get('pid')}, port={res.get('port')})",
                      one_line=f"apps.status: {app_id} {running}", data=res)


SPECS = [
    ToolSpec(name="apps.start",
             description="Запустить известное приложение (по app_id из каталога apps) как "
                         "процесс. Требует BOSSMAN_APPS_CONTROL_ENABLED=1 у владельца.",
             handler=_start, input_schema={"app_id": {"type": "string"}}, required=["app_id"],
             category="exec", source="apps", default_effect="ask", idempotent=False,
             timeout_seconds=60.0, external_output=True,
             effect_hook=lambda a: ("ask", "запуск процесса на машине владельца")),
    ToolSpec(name="apps.stop",
             description="Остановить приложение, которое запустил BOSSMAN (по app_id).",
             handler=_stop, input_schema={"app_id": {"type": "string"}}, required=["app_id"],
             category="exec", source="apps", default_effect="ask", idempotent=False,
             timeout_seconds=30.0, external_output=True,
             effect_hook=lambda a: ("ask", "остановка процесса на машине владельца")),
    ToolSpec(name="apps.status", description="Состояние приложения: запущено ли, pid, порт.",
             handler=_status, input_schema={"app_id": {"type": "string"}}, required=["app_id"],
             category="read", source="apps", default_effect="auto", timeout_seconds=15.0),
]


async def _setup(svc) -> None:
    for spec in SPECS:
        REGISTRY.register(spec)


FEATURE = Feature(name="tools_apps", setup=_setup)
