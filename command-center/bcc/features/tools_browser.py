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

import json
import threading
import time
import uuid
from pathlib import Path

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request, Response

from ..db import settings_kv, utcnow
from ..tools import REGISTRY, ToolResult, ToolSpec
from ..v2.browser_control import (AmbiguousSelector, BrowserApprovalRequired,
                                  BrowserDownloadApprovalRequired, BrowserDownloadFailed, BrowserPolicy,
                                  BrowserPolicyDenied, BrowserTakeoverActive, BrowserUnavailable,
                                  CaptchaBlocked, StaleElementReference, redact_secrets)
from ..v2.tables import browser_sessions as bs_t
from . import Feature

# V2.2+ (browser-use, этап 1): хранилище учётных данных браузера.
# Модель называет ИМЯ учётки, пароль подставляет рантайм. В аргументах
# инструмента, в `tool_calls.args` и в контексте модели пароля нет никогда.
CREDENTIALS_KEY = "browser.credentials"
LOGIN_RECEIPTS_REL = Path("browser") / "login-receipts.json"
_LOGIN_RECEIPTS_LOCK = threading.Lock()

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
    except BrowserDownloadApprovalRequired as exc:
        return ToolResult(content=f"{exc}. Файл НЕ сохранён. Если он нужен — вызовите "
                                  f"browser.download с url={exc.url!r} (требует подтверждения).",
                          one_line="browser: загрузка ждёт подтверждения", error=True,
                          data={"download_url": exc.url, "filename": exc.filename,
                                "needs_approval": True})
    except BrowserDownloadFailed as exc:
        await ctx.svc.bus.emit("browser.download", session_id=sid, status="failed",
                               error=str(exc)[:300])
        return ToolResult(content=f"файл не скачан: {exc}", one_line="browser.download: не удалось",
                          error=True, data={"download": exc.record})
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

    if isinstance(result, dict) and result.get("download"):
        dl = result["download"]
        async with ctx.svc.db.session() as s:
            await s.execute(sa.update(bs_t).where(bs_t.c.id == sid).values(
                last_action="download", updated_at=utcnow()))
            await s.commit()
        await ctx.svc.bus.emit("browser.download", session_id=sid, status=dl.get("status"),
                               filename=dl.get("filename"), path=dl.get("path"),
                               bytes=dl.get("bytes"), sha256=dl.get("sha256"),
                               mime=dl.get("mime"), quarantined=dl.get("quarantined"))
        return ToolResult(content=f"{result.get('message')}\nsha256: {dl.get('sha256')}\n"
                                  f"mime: {dl.get('mime')}",
                          one_line=f"browser.download: {dl.get('filename')}",
                          data={"session_id": sid, "download": dl, "path": dl.get("path")})
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
    # allow_download=False: адрес-файл не сохраняется молча — только через
    # ASK-инструмент browser.download (B4).
    return await _act(ctx, args, "navigate",
                      lambda m, sid: m.navigate(sid, url, actor="agent", approved=True,
                                                allow_download=False))


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
                      lambda m, sid: m.click(sid, sel, ref=ref, actor="agent", approved=True,
                                             allow_download=False))


async def _download(args, ctx):
    """Скачать файл: по url или кликом по ref/selector. ASK — решение уже принято."""
    url = str(args.get("url") or "")
    sel = str(args.get("selector") or "")
    ref = str(args.get("ref") or "")
    if not (url or sel or ref):
        return ToolResult(content="нужен url файла или ref/selector ссылки на него",
                          one_line="browser.download: нет цели", error=True)
    return await _act(ctx, args, "download",
                      lambda m, sid: m.download(sid, url=url, selector=sel, ref=ref,
                                                actor="agent", approved=True))


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




def _login_receipts_path(svc) -> Path:
    path = Path(svc.settings.data_dir) / LOGIN_RECEIPTS_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _login_receipts_read(svc) -> dict:
    path = _login_receipts_path(svc)
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
        return body if isinstance(body, dict) else {"receipts": {}}
    except (OSError, ValueError):
        return {"receipts": {}}


def _login_receipts_write(svc, data: dict) -> None:
    path = _login_receipts_path(svc)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _login_receipt_public(row: dict) -> dict:
    return {k: v for k, v in row.items() if k not in {"screenshot_path"}}


def _login_receipt_put(svc, row: dict) -> dict:
    with _LOGIN_RECEIPTS_LOCK:
        data = _login_receipts_read(svc)
        data.setdefault("receipts", {})[row["id"]] = row
        _login_receipts_write(svc, data)
    return _login_receipt_public(row)


def _login_receipt_update(svc, rid: str, **changes) -> dict | None:
    with _LOGIN_RECEIPTS_LOCK:
        data = _login_receipts_read(svc)
        row = (data.get("receipts") or {}).get(rid)
        if not isinstance(row, dict):
            return None
        row.update(changes)
        _login_receipts_write(svc, data)
        return _login_receipt_public(row)


def _login_receipts_pending(svc) -> list[dict]:
    with _LOGIN_RECEIPTS_LOCK:
        rows = list((_login_receipts_read(svc).get("receipts") or {}).values())
    out = [_login_receipt_public(dict(r)) for r in rows if isinstance(r, dict)
           and r.get("phase") in {"PRE_LOGIN", "SUCCESS", "FAILED", "UNVERIFIED_POST_SUBMIT"}]
    return sorted(out, key=lambda r: float(r.get("created_at") or 0.0))

async def save_credentials(svc, data: dict) -> None:
    import json
    enc = svc.vault.encrypt(json.dumps(data, ensure_ascii=False))
    async with svc.db.session() as s:
        await s.execute(sa.delete(settings_kv).where(settings_kv.c.key == CREDENTIALS_KEY))
        await s.execute(sa.insert(settings_kv).values(key=CREDENTIALS_KEY, value_enc=enc))
        await s.commit()


async def _require_local_secret_agent(ctx) -> None:
    """Secret/login page reasoning must use only a local model route.

    The secret itself is never model-visible, but the login page can contain
    account metadata. Main and fallback models must both resolve to local
    providers; otherwise the secret flow is refused before page reasoning.
    """
    task = ctx.task if isinstance(ctx.task, dict) else {}
    agent_id = task.get("agent_id")
    if type(agent_id) is not int:
        raise PermissionError("secret/login flow requires an explicitly bound local agent")
    from ..db import agents as agents_t, models as models_t, providers as providers_t
    from ..v2.model_router import derive_local
    async with ctx.svc.db.session() as s:
        agent = (await s.execute(sa.select(agents_t.c.model_id, agents_t.c.fallback_model_id)
                                 .where(agents_t.c.id == agent_id))).first()
        if agent is None:
            raise PermissionError("secret/login agent no longer exists")
        mids = [agent._mapping.get("model_id"), agent._mapping.get("fallback_model_id")]
        for mid in [m for m in mids if type(m) is int]:
            row = (await s.execute(
                sa.select(models_t.c.kind, providers_t.c.kind.label("provider_kind"),
                          providers_t.c.base_url)
                .select_from(models_t.join(providers_t, models_t.c.provider_id == providers_t.c.id))
                .where(models_t.c.id == mid))).first()
            if row is None:
                raise PermissionError("secret/login model route is unresolved")
            m = row._mapping
            local, _why = derive_local(str(m["kind"] or ""), str(m["provider_kind"] or ""),
                                       str(m["base_url"] or ""))
            if not local:
                raise PermissionError("secret/login flow refuses cloud or non-local model routes")


def public_credential(cid: str, cred: dict) -> dict:
    """То, что можно показывать модели и в UI: без пароля, всегда."""
    return {"id": cid, "login": str(cred.get("login") or ""),
            "domain": str(cred.get("domain") or ""),
            "note": str(cred.get("note") or "")[:200],
            "has_password": bool(cred.get("password"))}


async def _login(args, ctx):
    await _require_local_secret_agent(ctx)
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
    next_fields = [str(x)[:120] for x in (args.get("next_fields") or [])
                   if isinstance(x, (str, int, float))][:16]
    receipt_id = uuid.uuid4().hex[:12]
    receipt = {
        "id": receipt_id, "phase": "PRE_LOGIN", "created_at": time.time(),
        "task_id": ctx.task.get("id"), "credential_id": cid, "login": login_value[:320],
        "domain": str(cred.get("domain") or "")[:240], "next_fields": next_fields,
        "session_id": None, "verified_by": None, "post_login_url": None,
        "screenshot_path": None,
    }
    _login_receipt_put(ctx.svc, receipt)
    observed: dict = {}

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
        receipt["session_id"] = sid
        if args.get("submit_selector") or args.get("submit_ref"):
            before = await m.status(sid)
            pre_url = str(before.get("url") or "")
            clicked = await m.click(sid, str(args.get("submit_selector") or ""),
                                    ref=str(args.get("submit_ref") or ""),
                                    actor="agent", approved=True)
            expected = str(args.get("success_url_contains") or "").strip()
            status = await m.status(sid)
            post_url = str(status.get("url") or "")
            observed["pre_url"] = pre_url
            observed["post_url"] = post_url
            # A model-supplied "/" or the unchanged login URL is not proof.
            # Controlled owner tests can provide a concrete dashboard/account fragment.
            verified = (len(expected) >= 4 and expected in post_url and
                        bool(pre_url) and bool(post_url) and pre_url != post_url)
            observed["verified"] = verified
            if verified:
                observed["png"] = await m.screenshot(sid, actor="agent", approved=True)
            return clicked
        return await m.snapshot(sid, actor="agent", approved=True)

    try:
        result = await _act(ctx, args, "login", run)
    except Exception:
        _login_receipt_update(ctx.svc, receipt_id, phase="FAILED", finished_at=time.time())
        raise
    # Последняя страховка: что бы ни попало в результат, секрета там не будет.
    # `data` чистим наравне с текстом: туда кладётся `url`, а форма входа с
    # `method=GET` уносит пароль именно в адрес.
    if secret:
        result.content = redact_secrets(result.content, {secret})
        result.one_line = redact_secrets(result.one_line, {secret})
        result.data = redact_secrets(result.data, {secret})
    if args.get("submit_selector") or args.get("submit_ref"):
        if observed.get("verified"):
            shots = Path(ctx.svc.settings.data_dir) / "browser" / "login-receipts"
            shots.mkdir(parents=True, exist_ok=True)
            shot = shots / f"{receipt_id}.png"
            shot.write_bytes(observed["png"])
            _login_receipt_update(
                ctx.svc, receipt_id, phase="SUCCESS", finished_at=time.time(),
                verified_by="success_url_contains", post_login_url=observed.get("post_url"),
                screenshot_path=str(shot))
            if isinstance(result.data, dict):
                result.data["login_receipt_id"] = receipt_id
                result.data["login_verified"] = True
        else:
            _login_receipt_update(
                ctx.svc, receipt_id, phase="UNVERIFIED_POST_SUBMIT",
                finished_at=time.time(), post_login_url=observed.get("post_url"))
    return result


async def _request_owner_fields(args, ctx):
    await _require_local_secret_agent(ctx)
    """Ask the owner for missing form values without exposing the answer to the model."""
    store = getattr(ctx.svc, "owner_input", None)
    if store is None:
        return ToolResult(content="owner-input store is unavailable",
                          one_line="browser.owner_input: unavailable", error=True)
    try:
        sid = await _session_for(ctx, args)
        current = await _mgr(ctx.svc).jev_current(sid, actor="agent", approved=True)
        refs = set(current.get("refs") or [])
        fields = args.get("fields")
        if not isinstance(fields, list):
            raise ValueError("fields must be a list")
        for field in fields:
            if not isinstance(field, dict):
                raise ValueError("every field must be an object")
            ref = str(field.get("ref") or "")
            selector = str(field.get("selector") or "")
            if ref and ref not in refs:
                raise ValueError(f"stale/unknown ref: {ref}")
            if not ref and not selector:
                raise ValueError("field needs ref or selector")
        row = store.create(
            task_id=ctx.task.get("id"), session_id=sid, fields=fields,
            context=str(args.get("context") or "missing form data"), source="browser",
            initial_url=str(current.get("url") or ""), success=args.get("success"))
        await ctx.svc.bus.emit("owner.input_requested", request_id=row["id"],
                               task_id=ctx.task.get("id"), session_id=sid,
                               fields=[f["key"] for f in row["fields"]])
        return ToolResult(
            content=(f"нужны данные владельца: request_id={row['id']}. "
                     f"Значения придут через owner-input/Telegram и модели не показываются. "
                     f"После ответа вызовите browser.fill_owner_fields с этим request_id."),
            one_line=f"owner input {row['id']}: ожидает владельца",
            data={"request_id": row["id"], "needs_owner_input": True,
                  "field_labels": [f["label"] for f in row["fields"]]},
            external=True)
    except Exception as exc:
        return ToolResult(content=f"не удалось создать запрос владельцу: {type(exc).__name__}: {exc}",
                          one_line="browser.owner_input: ошибка", error=True)


async def _fill_owner_fields(args, ctx):
    await _require_local_secret_agent(ctx)
    """Fill an answered request; plaintext values never enter tool/model output."""
    store = getattr(ctx.svc, "owner_input", None)
    request_id = str(args.get("request_id") or "").strip()
    if store is None or not request_id:
        return ToolResult(content="нужен answered owner-input request_id",
                          one_line="browser.fill_owner_fields: нет request", error=True)
    try:
        row, values = store.values_for_fill(request_id, task_id=ctx.task.get("id"))
        sid = await _session_for(ctx, {"session_id": row["session_id"]})
        mgr = _mgr(ctx.svc)
        filled = []
        for field in row.get("fields") or []:
            key = str(field["key"])
            if key not in values:
                raise ValueError(f"answer missing field {key}")
            selector, ref = str(field.get("selector") or ""), str(field.get("ref") or "")
            if field.get("secret"):
                await mgr.fill_secret(sid, selector, secret=values[key], ref=ref,
                                      actor="agent", approved=True)
            else:
                await mgr.type_text(sid, selector, values[key], ref=ref,
                                    actor="agent", approved=True)
            filled.append(key)
        store.mark_filled(request_id)
        await ctx.svc.bus.emit("owner.input_filled", request_id=request_id,
                               task_id=ctx.task.get("id"), session_id=sid, fields=filled)
        return ToolResult(
            content=(f"заполнены поля из owner-input {request_id}: {', '.join(filled)}. "
                     "Значения не раскрыты модели. Перечитайте DOM перед следующим действием. "
                     "Отправка формы/регистрация остаётся отдельным ASK-действием."),
            one_line=f"browser.fill_owner_fields: {len(filled)} полей",
            data={"request_id": request_id, "session_id": sid, "filled": filled,
                  "needs_fresh_snapshot": True})
    except Exception as exc:
        return ToolResult(content=f"поля не заполнены: {type(exc).__name__}: {exc}",
                          one_line="browser.fill_owner_fields: ошибка", error=True)


async def _verify_owner_input_success(args, ctx):
    """Fresh deterministic post-login/post-registration verification.

    The local model chooses the request only. Success conditions were frozen
    when the request was created; this tool cannot weaken them afterwards.
    """
    await _require_local_secret_agent(ctx)
    store = getattr(ctx.svc, "owner_input", None)
    request_id = str(args.get("request_id") or "").strip()
    if store is None or not request_id:
        return ToolResult(content="нужен owner-input request_id",
                          one_line="browser.verify_owner_input_success: нет request", error=True)
    try:
        row = store.get(request_id)
        if not isinstance(row, dict) or row.get("status") != "FILLED":
            raise ValueError("owner-input must be FILLED before success verification")
        sid = await _session_for(ctx, {"session_id": row["session_id"]})
        snap = await _mgr(ctx.svc).snapshot(sid, actor="agent", approved=True)
        url = str(snap.get("url") or "")
        text = str(snap.get("text") or "")
        rules = row.get("success") if isinstance(row.get("success"), dict) else {}
        checks, ok = [], True
        if rules.get("url_changed"):
            hit = bool(row.get("initial_url")) and url != str(row.get("initial_url"))
            checks.append(f"url_changed={hit}"); ok = ok and hit
        if rules.get("url_contains"):
            hit = str(rules["url_contains"]) in url
            checks.append(f"url_contains={hit}"); ok = ok and hit
        if rules.get("contains_text"):
            hit = str(rules["contains_text"]).casefold() in text.casefold()
            checks.append(f"contains_text={hit}"); ok = ok and hit
        if rules.get("absent_text"):
            hit = str(rules["absent_text"]).casefold() not in text.casefold()
            checks.append(f"absent_text={hit}"); ok = ok and hit
        if not checks:
            raise ValueError("request has no deterministic success criteria")
        if not ok:
            return ToolResult(content="успех входа/регистрации НЕ подтверждён: " + ", ".join(checks),
                              one_line="browser.verify_owner_input_success: не подтверждено",
                              error=True, data={"request_id": request_id, "verified": False,
                                                "checks": checks, "session_id": sid})
        verified = store.mark_verified(request_id, evidence={"verified": True, "url": url, "checks": checks})
        await ctx.svc.bus.emit("owner.input_verified", request_id=request_id,
                               task_id=ctx.task.get("id"), session_id=sid, checks=checks)
        return ToolResult(content="успех входа/регистрации подтверждён свежей страницей; "
                                  "секретная Telegram-сессия может показать финальный кадр и очиститься.",
                          one_line="browser.verify_owner_input_success: VERIFIED",
                          data={"request_id": request_id, "verified": True,
                                "checks": checks, "session_id": sid,
                                "status": verified.get("status")})
    except Exception as exc:
        return ToolResult(content=f"успех не подтверждён: {type(exc).__name__}: {exc}",
                          one_line="browser.verify_owner_input_success: ошибка", error=True)


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
    ToolSpec(name="browser.download",
             description="Скачать файл в папку загрузок сессии: url файла или ref/selector "
                         "ссылки. Файл проверяется на диске (размер, sha256, тип); "
                         "исполняемые — в карантин. Требует подтверждения.",
             handler=_download,
             input_schema={"url": {"type": "string"}, "ref": {"type": "string"},
                           "selector": {"type": "string"}}, required=[],
             category="write", permission="browser.control", source="browser",
             default_effect="ask", timeout_seconds=180.0, idempotent=False,
             effect_hook=lambda a: ("ask", "скачивание файла на диск владельца")),
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
    ToolSpec(
        name="browser.request_owner_fields",
        description=("Если форме не хватает данных владельца: создать запрос с названиями полей "
                     "и ref/selector. Bossman пришлёт запрос владельцу в Telegram; ответ хранится "
                     "зашифрованно и не показывается модели."),
        handler=_request_owner_fields,
        input_schema={
            "session_id": {"type": "integer"},
            "context": {"type": "string"},
            "fields": {"type": "array", "items": {"type": "object"}},
            "success": {"type": "object"},
        },
        required=["fields"], category="read", permission="browser.control",
        source="browser", default_effect="auto", timeout_seconds=30.0,
        external_output=True),
    ToolSpec(
        name="browser.verify_owner_input_success",
        description=("После заполнения и submit/login перечитать страницу и проверить "
                     "ЗАРАНЕЕ зафиксированные success-критерии owner-input. "
                     "Только VERIFIED запускает финальный Telegram-скрин и очистку сессии."),
        handler=_verify_owner_input_success,
        input_schema={"request_id": {"type": "string"}},
        required=["request_id"], category="read", permission="browser.read",
        source="browser", default_effect="auto", timeout_seconds=45.0,
        external_output=True),
    ToolSpec(
        name="browser.fill_owner_fields",
        description=("Заполнить ранее запрошенные поля ответом владельца по request_id. "
                     "Значения получает рантайм напрямую; модель их не видит. "
                     "Не отправляет форму и не создаёт аккаунт."),
        handler=_fill_owner_fields,
        input_schema={"request_id": {"type": "string"}},
        required=["request_id"], category="write", permission="browser.control",
        source="browser", default_effect="auto", timeout_seconds=90.0,
        idempotent=False, external_output=True),
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
                           "submit_ref": {"type": "string"},
                           "success_url_contains": {"type": "string",
                                                    "description": "конкретный ожидаемый фрагмент НОВОГО post-login URL (минимум 4 символа); нужен для verified Telegram receipt"},
                           "next_fields": {"type": "array", "items": {"type": "string"},
                                           "description": "только названия несекретных полей, которые владелец должен подготовить после входа"}},
             required=["credential_id"], category="send",
             permission="browser.control", source="browser", default_effect="ask",
             idempotent=False, external_output=True,
             effect_hook=lambda a: ("ask", "вход в аккаунт")),
]


# ------------------------------------------------- API учётных данных браузера

router = APIRouter()


@router.get("/browser/login-receipts")
async def http_login_receipts(request: Request):
    """Safe metadata only: account/login and field labels, never password."""
    return {"receipts": _login_receipts_pending(request.app.state.svc)}


@router.get("/browser/login-receipts/{receipt_id}/screenshot")
async def http_login_receipt_screenshot(receipt_id: str, request: Request):
    if not __import__("re").fullmatch(r"[0-9a-f]{12}", receipt_id):
        raise HTTPException(404, {"message": "login receipt not found"})
    data = _login_receipts_read(request.app.state.svc)
    row = (data.get("receipts") or {}).get(receipt_id)
    path = Path(str((row or {}).get("screenshot_path") or ""))
    if not isinstance(row, dict) or row.get("phase") != "SUCCESS" or not path.is_file():
        raise HTTPException(404, {"message": "verified login screenshot unavailable"})
    raw = path.read_bytes()
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise HTTPException(409, {"message": "login receipt screenshot invalid"})
    return Response(content=raw, media_type="image/png")


@router.post("/browser/login-receipts/{receipt_id}/consumed")
async def http_consume_login_receipt(receipt_id: str, request: Request):
    row = _login_receipt_update(request.app.state.svc, receipt_id, phase="CONSUMED", consumed_at=time.time())
    if row is None:
        raise HTTPException(404, {"message": "login receipt not found"})
    data = _login_receipts_read(request.app.state.svc)
    stored = (data.get("receipts") or {}).get(receipt_id) or {}
    path = Path(str(stored.get("screenshot_path") or ""))
    if path.is_file():
        try:
            path.unlink()
        except OSError:
            pass
    return {"id": receipt_id, "phase": "CONSUMED"}


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
