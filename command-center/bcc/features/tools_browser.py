"""V2.1 фаза C — Browser как настоящий инструмент агента.

Тот же Playwright-рантайм, что и на странице «Браузер» (bcc/v2/browser_control),
только теперь доступен МОДЕЛИ через канонический tool-loop. DOM-first: модель
получает текст и список интерактивных элементов, а не картинку.

Политика (мастер-промпт §4):
  AUTO — open, read_dom, screenshot, обычная навигация, click, type, select
  ASK  — login, upload, download, submit, отправка сообщений, изменение
         внешнего аккаунта
  DENY — payment, wallet, bank transfer (никогда, даже с подтверждением)

Human Take Over: пока человек за рулём, действия агента отклоняются; после
Resume модель обязана перечитать DOM — старое состояние страницы недействительно.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import sqlalchemy as sa

from ..db import utcnow
from ..tools import REGISTRY, ToolResult, ToolSpec
from ..v2.browser_control import (BrowserApprovalRequired, BrowserPolicy, BrowserPolicyDenied,
                                  BrowserTakeoverActive, BrowserUnavailable)
from ..v2.tables import browser_sessions as bs_t
from . import Feature

# Действия, которые нельзя одобрить в принципе (совпадает с HARD_DENY_ACTIONS
# рантайма — дублируем осознанно: инструмент не должен зависеть от того,
# что кто-то ослабит политику сессии).
NEVER = {"purchase", "payment", "wallet", "bank_transfer"}
ASK_ACTIONS = {"login", "upload", "download", "submit"}
TEXT_LIMIT = 6000
INTERACTIVE_LIMIT = 60


def _mgr(svc):
    from .browser import _mgr as base_mgr      # переиспользуем патч Chromium
    return base_mgr(svc)


async def _session_for(ctx, args: dict) -> int:
    """Сессия браузера этого run'а: переиспользуем, пока не попросили новую."""
    explicit = args.get("session_id")
    if explicit:
        return int(explicit)
    async with ctx.svc.db.session() as s:
        row = (await s.execute(sa.select(bs_t.c.id).where(sa.and_(
            bs_t.c.task_id == ctx.task["id"], bs_t.c.status == "running"))
            .order_by(bs_t.c.id.desc()).limit(1))).first()
    if row:
        return int(row[0])
    async with ctx.svc.db.session() as s:
        res = await s.execute(sa.insert(bs_t).values(
            task_id=ctx.task["id"], agent_id=ctx.agent.get("id"), status="created",
            created_at=utcnow(), updated_at=utcnow()))
        sid = int(res.inserted_primary_key[0])
        await s.commit()
    policy = BrowserPolicy.from_dict((ctx.agent.get("permissions") or {}).get("browser")
                                     if isinstance(ctx.agent.get("permissions"), dict) else None)
    await _mgr(ctx.svc).start(sid, policy, headless=True)
    async with ctx.svc.db.session() as s:
        await s.execute(sa.update(bs_t).where(bs_t.c.id == sid).values(
            status="running", updated_at=utcnow()))
        await s.commit()
    await ctx.svc.bus.emit("agent.tool_call", tool="browser", session_id=sid, action="start")
    return sid


def _render(snapshot: dict) -> ToolResult:
    """DOM-снимок → компактный текст для модели (обрезка здесь, не по просьбе модели)."""
    text = str(snapshot.get("text") or "")
    truncated = len(text) > TEXT_LIMIT
    items = (snapshot.get("interactive") or [])[:INTERACTIVE_LIMIT]
    lines = [f"URL: {snapshot.get('url')}", f"Заголовок: {snapshot.get('title')}", "",
             "Текст страницы:", text[:TEXT_LIMIT], "", "Интерактивные элементы:"]
    for el in items:
        label = el.get("text") or el.get("aria") or el.get("placeholder") or el.get("name") or ""
        lines.append(f"[{el.get('i')}] <{el.get('tag')}"
                     + (f" type={el.get('type')}" if el.get("type") else "")
                     + (f" name={el.get('name')}" if el.get("name") else "")
                     + f"> {label}".rstrip())
    if snapshot.get("takeover"):
        lines.append("\nВНИМАНИЕ: за браузером сейчас человек (Take Over) — "
                     "действия агента отклоняются.")
    return ToolResult(content="\n".join(lines),
                      one_line=f"browser: {snapshot.get('url')}",
                      truncated=truncated,
                      more="browser.read_dom с уточняющим запросом" if truncated else "",
                      data={"session_id": snapshot.get("session_id"), "url": snapshot.get("url")},
                      external=True)


async def _act(ctx, args: dict, action: str, run) -> ToolResult:
    """Общая обвязка: сессия → действие → снимок DOM → запись в БД."""
    if action in NEVER:
        return ToolResult(content=f"действие {action} запрещено без исключений",
                          one_line=f"browser.{action}: запрещено", error=True)
    try:
        sid = await _session_for(ctx, args)
    except BrowserUnavailable as exc:
        return ToolResult(content=f"браузер недоступен: {exc}",
                          one_line="browser: рантайм недоступен", error=True)
    except Exception as exc:
        return ToolResult(content=f"не удалось открыть сессию браузера: "
                                  f"{type(exc).__name__}: {exc}",
                          one_line="browser: сессия не открылась", error=True)

    mgr = _mgr(ctx.svc)
    try:
        # approved=True: решение AUTO/ASK/DENY уже принял канонический слой
        result = await run(mgr, sid)
    except BrowserTakeoverActive:
        return ToolResult(content="за браузером сейчас человек (Take Over) — действие "
                                  "отклонено; дождитесь Resume и перечитайте DOM",
                          one_line="browser: takeover", error=True)
    except BrowserPolicyDenied as exc:
        return ToolResult(content=f"действие запрещено политикой браузера: {exc}",
                          one_line=f"browser.{action}: deny", error=True)
    except BrowserApprovalRequired:
        return ToolResult(content="политика сессии требует подтверждения человека",
                          one_line=f"browser.{action}: ask", error=True)
    except BrowserUnavailable as exc:
        return ToolResult(content=f"браузер недоступен: {exc}",
                          one_line="browser: рантайм недоступен", error=True)
    except Exception as exc:
        return ToolResult(content=f"ошибка браузера: {type(exc).__name__}: {exc}",
                          one_line=f"browser.{action}: ошибка", error=True)

    if isinstance(result, dict):
        async with ctx.svc.db.session() as s:
            await s.execute(sa.update(bs_t).where(bs_t.c.id == sid).values(
                current_url=str(result.get("url") or ""), last_action=action,
                updated_at=utcnow()))
            await s.commit()
        await ctx.svc.bus.emit("agent.tool_call", tool="browser", session_id=sid,
                               action=action, url=str(result.get("url") or "")[:200])
        result.setdefault("session_id", sid)
        return _render(result)
    return result


# ------------------------------------------------------------------ tools

async def _open(args, ctx):
    url = str(args.get("url") or "")
    if not url:
        return ToolResult(content="нужен аргумент url", one_line="browser.open: нет url", error=True)
    return await _act(ctx, args, "navigate",
                      lambda m, sid: m.navigate(sid, url, actor="agent", approved=True))


async def _read_dom(args, ctx):
    return await _act(ctx, args, "snapshot",
                      lambda m, sid: m.snapshot(sid, actor="agent", approved=True))


async def _click(args, ctx):
    sel = str(args.get("selector") or "")
    if not sel:
        return ToolResult(content="нужен аргумент selector", one_line="browser.click: нет selector",
                          error=True)
    return await _act(ctx, args, "click",
                      lambda m, sid: m.click(sid, sel, actor="agent", approved=True))


async def _type(args, ctx):
    sel = str(args.get("selector") or "")
    if not sel:
        return ToolResult(content="нужен аргумент selector", one_line="browser.type: нет selector",
                          error=True)
    return await _act(ctx, args, "type",
                      lambda m, sid: m.type_text(sid, sel, str(args.get("text") or ""),
                                                 actor="agent", approved=True))


async def _select(args, ctx):
    return await _act(ctx, args, "select",
                      lambda m, sid: m.select(sid, str(args.get("selector") or ""),
                                              str(args.get("value") or ""),
                                              actor="agent", approved=True))


async def _back(args, ctx):
    return await _act(ctx, args, "back", lambda m, sid: m.back(sid, actor="agent"))


async def _reload(args, ctx):
    return await _act(ctx, args, "reload", lambda m, sid: m.reload(sid, actor="agent"))


async def _submit(args, ctx):
    """Отправка формы = клик по submit-элементу, но через ASK-инструмент."""
    sel = str(args.get("selector") or "")
    if not sel:
        return ToolResult(content="нужен аргумент selector кнопки отправки",
                          one_line="browser.submit: нет selector", error=True)
    return await _act(ctx, args, "submit",
                      lambda m, sid: m.click(sid, sel, actor="agent", approved=True))


async def _login(args, ctx):
    """Ввод логина/пароля — всегда через подтверждение человека."""
    async def run(m, sid):
        await m.type_text(sid, str(args.get("login_selector") or ""),
                          str(args.get("login") or ""), actor="agent", approved=True)
        await m.type_text(sid, str(args.get("password_selector") or ""),
                          str(args.get("password") or ""), actor="agent", approved=True)
        if args.get("submit_selector"):
            return await m.click(sid, str(args["submit_selector"]), actor="agent", approved=True)
        return await m.snapshot(sid, actor="agent", approved=True)
    return await _act(ctx, args, "login", run)


async def _screenshot(args, ctx):
    try:
        sid = await _session_for(ctx, args)
        png = await _mgr(ctx.svc).screenshot(sid, actor="agent", approved=True)
    except BrowserTakeoverActive:
        return ToolResult(content="за браузером человек — скриншот агенту недоступен",
                          one_line="browser.screenshot: takeover", error=True)
    except Exception as exc:
        return ToolResult(content=f"не удалось снять скриншот: {type(exc).__name__}: {exc}",
                          one_line="browser.screenshot: ошибка", error=True)
    shots = Path(ctx.svc.settings.data_dir) / "browser"
    shots.mkdir(parents=True, exist_ok=True)
    path = shots / f"shot-{sid}-{uuid.uuid4().hex[:6]}.png"
    path.write_bytes(png)
    return ToolResult(content=f"скриншот сохранён: {path} ({len(png)} байт). "
                              f"Содержимое страницы читайте через browser.read_dom.",
                      one_line="browser.screenshot: ок",
                      data={"path": str(path), "session_id": sid})


def _no_loosen(_args: dict) -> tuple[str, str] | None:
    return None


SPECS = [
    ToolSpec(name="browser.open", description="Открыть URL в браузере и вернуть DOM-снимок "
                                              "(текст страницы + интерактивные элементы).",
             handler=_open, input_schema={"url": {"type": "string"}}, required=["url"],
             category="read", permission="browser.read", source="browser",
             default_effect="auto", timeout_seconds=90.0, external_output=True),
    ToolSpec(name="browser.read_dom", description="Перечитать текущую страницу (DOM-снимок).",
             handler=_read_dom, input_schema={}, category="read", permission="browser.read",
             source="browser", default_effect="auto", timeout_seconds=60.0, external_output=True),
    ToolSpec(name="browser.screenshot", description="Скриншот текущей страницы в файл.",
             handler=_screenshot, input_schema={}, category="read", permission="browser.read",
             source="browser", default_effect="auto", timeout_seconds=60.0),
    ToolSpec(name="browser.click",
             description="Кликнуть по элементу (CSS-селектор из DOM-снимка).",
             handler=_click, input_schema={"selector": {"type": "string"}}, required=["selector"],
             category="write", permission="browser.control", source="browser",
             default_effect="auto", timeout_seconds=60.0, idempotent=False,
             external_output=True),
    ToolSpec(name="browser.type", description="Ввести текст в поле по CSS-селектору.",
             handler=_type,
             input_schema={"selector": {"type": "string"}, "text": {"type": "string"}},
             required=["selector", "text"], category="write", permission="browser.control",
             source="browser", default_effect="auto", timeout_seconds=60.0, idempotent=False,
             external_output=True),
    ToolSpec(name="browser.select", description="Выбрать значение в выпадающем списке.",
             handler=_select,
             input_schema={"selector": {"type": "string"}, "value": {"type": "string"}},
             required=["selector", "value"], category="write", permission="browser.control",
             source="browser", default_effect="auto", timeout_seconds=60.0, idempotent=False,
             external_output=True),
    ToolSpec(name="browser.back", description="Назад по истории браузера.", handler=_back,
             input_schema={}, category="read", permission="browser.read", source="browser",
             default_effect="auto", external_output=True),
    ToolSpec(name="browser.reload", description="Перезагрузить страницу.", handler=_reload,
             input_schema={}, category="read", permission="browser.read", source="browser",
             default_effect="auto", external_output=True),
    # Чувствительные действия: ASK по умолчанию и НЕ ослабляются выданным правом
    ToolSpec(name="browser.submit", description="Отправить форму (нужно подтверждение человека).",
             handler=_submit, input_schema={"selector": {"type": "string"}}, required=["selector"],
             category="send", permission="browser.control", source="browser",
             default_effect="ask", idempotent=False, external_output=True,
             effect_hook=lambda a: ("ask", "отправка формы во внешний мир")),
    ToolSpec(name="browser.login", description="Ввести учётные данные и войти "
                                               "(всегда через подтверждение человека).",
             handler=_login,
             input_schema={"login_selector": {"type": "string"}, "login": {"type": "string"},
                           "password_selector": {"type": "string"},
                           "password": {"type": "string"},
                           "submit_selector": {"type": "string"}},
             required=["login_selector", "password_selector"], category="send",
             permission="browser.control", source="browser", default_effect="ask",
             idempotent=False, external_output=True,
             effect_hook=lambda a: ("ask", "вход в аккаунт")),
]


async def _setup(svc) -> None:
    for spec in SPECS:
        REGISTRY.register(spec)


FEATURE = Feature(name="tools_browser", setup=_setup)
