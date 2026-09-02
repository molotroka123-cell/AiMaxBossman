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
from fastapi import APIRouter, HTTPException, Request

from ..db import settings_kv, utcnow
from ..tools import REGISTRY, ToolResult, ToolSpec
from ..v2.browser_control import (AmbiguousSelector, BrowserApprovalRequired, BrowserPolicy,
                                  BrowserPolicyDenied, BrowserTakeoverActive, BrowserUnavailable,
                                  CaptchaBlocked, StaleElementReference, redact_secrets)
from ..v2.tables import browser_sessions as bs_t
from . import Feature

# V2.2+ (browser-use, этап 1): хранилище учётных данных браузера.
# Модель называет ИМЯ учётки, пароль подставляет рантайм. В аргументах
# инструмента, в `tool_calls.args` и в контексте модели пароля нет никогда.
CREDENTIALS_KEY = "browser.credentials"

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
        # F-011: явный session_id принимается ТОЛЬКО если сессия принадлежит этой
        # задаче — чужой номер (другой задачи/миссии) не даёт управлять её браузером.
        async with ctx.svc.db.session() as s:
            row = (await s.execute(sa.select(bs_t.c.id).where(sa.and_(
                bs_t.c.id == int(explicit), bs_t.c.task_id == ctx.task["id"])))).first()
        if row is None:
            raise PermissionError(f"browser session {explicit} не принадлежит задаче {ctx.task['id']}")
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
             "Текст страницы:", text[:TEXT_LIMIT], "",
             "Интерактивные элементы (ref действителен только до следующего снимка):"]
    for el in items:
        label = el.get("text") or el.get("aria") or el.get("placeholder") or el.get("name") or ""
        mark = " [ЗАПОЛНЕНО]" if el.get("filled") else ""
        if el.get("secret"):
            # Не только пароль: скрытые поля, коды из SMS, токены. Значение
            # модели не нужно ни для одного сценария — достаточно факта.
            label = "(секретное поле — значение недоступно)" + mark
        lines.append(f"[ref={el.get('ref') or el.get('i')}] <{el.get('tag')}"
                     + (f" type={el.get('type')}" if el.get("type") else "")
                     + (f" name={el.get('name')}" if el.get("name") else "")
                     + f"> {label}".rstrip())
    captcha = snapshot.get("captcha") or {}
    if captcha.get("present"):
        lines.append(f"\nНА СТРАНИЦЕ КАПЧА ({captcha.get('provider')}). "
                     f"Решать её нельзя — это контроль доступа владельца сайта, "
                     f"и обходить его запрещено. Сессия передана человеку "
                     f"(Take Over): попросите владельца пройти проверку и нажать "
                     f"Resume, после чего перечитайте DOM. Другого пути нет — "
                     f"перезагрузка страницы и повторные попытки не помогут.")
    elif snapshot.get("takeover"):
        lines.append("\nВНИМАНИЕ: за браузером сейчас человек (Take Over) — "
                     "действия агента отклоняются.")
    return ToolResult(content="\n".join(lines),
                      one_line=f"browser: {snapshot.get('url')}",
                      truncated=truncated,
                      more="browser.read_dom с уточняющим запросом" if truncated else "",
                      data={"session_id": snapshot.get("session_id"),
                            "url": snapshot.get("url"),
                            "captcha": captcha,
                            "needs_human": bool(captcha.get("present"))},
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
    except CaptchaBlocked as exc:
        return ToolResult(
            content=f"{exc}. Действие не выполнено. Капчу решать нельзя — это "
                    f"контроль доступа владельца сайта. Сообщите владельцу, что "
                    f"нужна ручная проверка: он пройдёт её сам, после чего "
                    f"перечитайте страницу через browser.read_dom. Повторные "
                    f"попытки, перезагрузка и обход не помогут и не разрешены.",
            one_line=f"browser.{action}: капча, нужен человек", error=True,
            data={"captcha_provider": exc.provider, "needs_human": True})
    except StaleElementReference as exc:
        # Ключевое: НИЧЕГО не нажато. Соседний элемент не трогаем.
        return ToolResult(content=f"{exc}. Действие не выполнено — ни один элемент не нажат. "
                                  f"Перечитайте страницу через browser.read_dom и возьмите "
                                  f"новую ссылку ref из свежего снимка.",
                          one_line=f"browser.{action}: устаревшая ссылка", error=True,
                          data={"stale_ref": exc.ref, "needs_fresh_snapshot": True})
    except AmbiguousSelector as exc:
        return ToolResult(content=f"{exc}. Действие не выполнено: выбирать наугад нельзя. "
                                  f"Возьмите ref нужного элемента из browser.read_dom "
                                  f"или уточните селектор.",
                          one_line=f"browser.{action}: неоднозначный селектор", error=True,
                          data={"ambiguous_selector": exc.selector, "matches": exc.count})
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
    ref = str(args.get("ref") or "")
    if not sel and not ref:
        return ToolResult(content="нужен ref из свежего DOM-снимка (надёжнее) или selector",
                          one_line="browser.click: нет цели", error=True)
    return await _act(ctx, args, "click",
                      lambda m, sid: m.click(sid, sel, ref=ref, actor="agent", approved=True))


async def _type(args, ctx):
    sel = str(args.get("selector") or "")
    ref = str(args.get("ref") or "")
    if not sel and not ref:
        return ToolResult(content="нужен ref из свежего DOM-снимка (надёжнее) или selector",
                          one_line="browser.type: нет цели", error=True)
    return await _act(ctx, args, "type",
                      lambda m, sid: m.type_text(sid, sel, str(args.get("text") or ""),
                                                 ref=ref, actor="agent", approved=True))


async def _select(args, ctx):
    return await _act(ctx, args, "select",
                      lambda m, sid: m.select(sid, str(args.get("selector") or ""),
                                              str(args.get("value") or ""),
                                              ref=str(args.get("ref") or ""),
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
                      lambda m, sid: m.click(sid, sel, ref=str(args.get("ref") or ""),
                                             actor="agent", approved=True))


async def credentials_map(svc) -> dict:
    """Все учётки. Расшифровываются только внутри рантайма."""
    import json
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(settings_kv.c.value_enc)
                               .where(settings_kv.c.key == CREDENTIALS_KEY))).first()
    if row and row[0]:
        try:
            data = json.loads(svc.vault.decrypt(row[0]))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


async def save_credentials(svc, data: dict) -> None:
    import json
    enc = svc.vault.encrypt(json.dumps(data, ensure_ascii=False))
    async with svc.db.session() as s:
        await s.execute(sa.delete(settings_kv).where(settings_kv.c.key == CREDENTIALS_KEY))
        await s.execute(sa.insert(settings_kv).values(key=CREDENTIALS_KEY, value_enc=enc))
        await s.commit()


def public_credential(cid: str, cred: dict) -> dict:
    """То, что можно показывать модели и в UI: без пароля, всегда."""
    return {"id": cid, "login": str(cred.get("login") or ""),
            "domain": str(cred.get("domain") or ""),
            "note": str(cred.get("note") or "")[:200],
            "has_password": bool(cred.get("password"))}


async def _login(args, ctx):
    """Вход по ССЫЛКЕ на учётку. Пароль модель не видит и не передаёт.

    Раньше `password` был обычным строковым аргументом инструмента: модель
    обязана была его сгенерировать, значит он лежал в её контексте и в
    `tool_calls.args` в БД. Теперь модель выбирает, КАКОЙ учёткой войти, а
    значение достаёт рантайм — вне видимости модели.
    """
    cid = str(args.get("credential_id") or "").strip()
    if not cid:
        known = await credentials_map(ctx.svc)
        names = ", ".join(sorted(known)) or "ни одной не заведено"
        return ToolResult(
            content=f"нужен credential_id — имя сохранённой учётной записи. "
                    f"Доступны: {names}. Пароль в аргументах не передаётся: "
                    f"его подставит рантайм.",
            one_line="browser.login: нет credential_id", error=True)

    creds = await credentials_map(ctx.svc)
    cred = creds.get(cid)
    if not isinstance(cred, dict):
        return ToolResult(content=f"учётная запись «{cid}» не найдена. "
                                  f"Доступны: {', '.join(sorted(creds)) or 'нет'}",
                          one_line="browser.login: учётка не найдена", error=True)

    secret = str(cred.get("password") or "")
    login_value = str(cred.get("login") or "")

    async def run(m, sid):
        # Проверка домена: учётка, привязанная к домену, не подставляется на
        # чужой странице — иначе редирект уводил бы пароль не туда.
        domain = str(cred.get("domain") or "").strip().lower()
        if domain:
            from urllib.parse import urlparse
            host = (urlparse((await m.status(sid)).get("url") or "").hostname or "").lower()
            if host and not (host == domain or host.endswith("." + domain)):
                raise PermissionError(
                    f"учётка «{cid}» привязана к домену {domain}, а страница на {host}")
        await m.type_text(sid, str(args.get("login_selector") or ""), login_value,
                          ref=str(args.get("login_ref") or ""), actor="agent", approved=True)
        await m.fill_secret(sid, str(args.get("password_selector") or ""), secret=secret,
                            ref=str(args.get("password_ref") or ""),
                            actor="agent", approved=True)
        if args.get("submit_selector") or args.get("submit_ref"):
            return await m.click(sid, str(args.get("submit_selector") or ""),
                                 ref=str(args.get("submit_ref") or ""),
                                 actor="agent", approved=True)
        return await m.snapshot(sid, actor="agent", approved=True)

    result = await _act(ctx, args, "login", run)
    # Последняя страховка: что бы ни попало в результат, секрета там не будет.
    # `data` чистим наравне с текстом: туда кладётся `url`, а форма входа с
    # `method=GET` уносит пароль именно в адрес.
    if secret:
        result.content = redact_secrets(result.content, {secret})
        result.one_line = redact_secrets(result.one_line, {secret})
        result.data = redact_secrets(result.data, {secret})
    return result


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
             description="Кликнуть по элементу: ref из последнего DOM-снимка (надёжно) "
                         "или CSS-селектор. Устаревший ref и неоднозначный селектор "
                         "отклоняются, а не нажимаются наугад.",
             handler=_click,
             input_schema={"ref": {"type": "string",
                                   "description": "ссылка из последнего DOM-снимка (надёжнее селектора)"},
                           "selector": {"type": "string"}}, required=[],
             category="write", permission="browser.control", source="browser",
             default_effect="auto", timeout_seconds=60.0, idempotent=False,
             external_output=True),
    ToolSpec(name="browser.type",
             description="Ввести текст в поле по ref из DOM-снимка или CSS-селектору. "
                         "Для паролей используйте browser.login с credential_id.",
             handler=_type,
             input_schema={"ref": {"type": "string"}, "selector": {"type": "string"},
                           "text": {"type": "string"}},
             required=["text"], category="write", permission="browser.control",
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
    ToolSpec(name="browser.login",
             description="Войти по СОХРАНЁННОЙ учётной записи (credential_id). "
                         "Пароль в аргументах не передаётся и модели не показывается. "
                         "Всегда через подтверждение человека.",
             handler=_login,
             input_schema={"credential_id": {"type": "string",
                                            "description": "имя сохранённой учётной записи; "
                                                           "пароль подставит рантайм"},
                           "login_selector": {"type": "string"},
                           "login_ref": {"type": "string"},
                           "password_selector": {"type": "string"},
                           "password_ref": {"type": "string"},
                           "submit_selector": {"type": "string"},
                           "submit_ref": {"type": "string"}},
             required=["credential_id"], category="send",
             permission="browser.control", source="browser", default_effect="ask",
             idempotent=False, external_output=True,
             effect_hook=lambda a: ("ask", "вход в аккаунт")),
]


# ------------------------------------------------- API учётных данных браузера

router = APIRouter()


@router.get("/browser/credentials")
async def http_list_credentials(request: Request):
    """Список учёток. Пароля здесь нет и быть не может."""
    creds = await credentials_map(request.app.state.svc)
    return {"credentials": [public_credential(cid, c) for cid, c in sorted(creds.items())
                            if isinstance(c, dict)]}


@router.post("/browser/credentials")
async def http_save_credential(request: Request):
    svc = request.app.state.svc
    body = await request.json()
    cid = str(body.get("id") or "").strip()
    if not cid:
        raise HTTPException(422, {"message": "нужен id учётной записи"})
    if not str(body.get("password") or ""):
        raise HTTPException(422, {"message": "нужен password"})
    creds = await credentials_map(svc)
    creds[cid] = {"login": str(body.get("login") or ""),
                  "password": str(body.get("password")),
                  "domain": str(body.get("domain") or "").strip().lower(),
                  "note": str(body.get("note") or "")[:200]}
    await save_credentials(svc, creds)
    await svc.bus.emit("browser.credential.saved", credential_id=cid)
    return {"credential": public_credential(cid, creds[cid])}


@router.delete("/browser/credentials/{credential_id}")
async def http_delete_credential(credential_id: str, request: Request):
    svc = request.app.state.svc
    creds = await credentials_map(svc)
    if credential_id not in creds:
        raise HTTPException(404, {"message": "учётная запись не найдена"})
    creds.pop(credential_id)
    await save_credentials(svc, creds)
    return {"ok": True, "id": credential_id}


async def _setup(svc) -> None:
    for spec in SPECS:
        REGISTRY.register(spec)


FEATURE = Feature(name="tools_browser", router=router, setup=_setup)
