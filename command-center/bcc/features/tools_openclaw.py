"""OpenClaw как инструменты модели — вторая половина моста (первая: v2/openclaw_bridge).

Мост умеет говорить с Gateway. Здесь эта возможность выдаётся МОДЕЛИ, и вся
разница между «интеграция» и «инцидент» находится в этом файле.

Три вещи, которые здесь сделаны намеренно и которые легко сделать неправильно:

1. **Отправка идёт через канонический ASK, а не мимо него.** `openclaw.send`
   объявлен с `default_effect="ask"` и правом `channel.send`. Право снимает ASK
   у опасного инструмента — поэтому хук политики возвращает ASK обратно для
   любой пары «канал+контакт», которую владелец не разрешил явно. Разрешение
   выдаётся на канал И контакт вместе: «весь Telegram» разрешить невозможно.

2. **Разрешение проверяется по СЕССИИ, а не по словам модели.** Решение
   AUTO/ASK принимается до вызова обработчика, по аргументам, которые назвала
   модель. Если бы этим всё и заканчивалось, модель могла бы объявить
   разрешённую пару и отправить в другую: получилось бы разрешение, выданное
   на одного человека, а сработавшее на другом. Поэтому перед отправкой
   обработчик спрашивает у Gateway, кому на самом деле принадлежит сессия, и
   при расхождении не отправляет ничего.

3. **Повтор не превращается во второе сообщение.** `idempotencyKey` выводится
   из наших `mission_id + run_id + call_id`, поэтому ретрай того же шага несёт
   тот же ключ. `idempotent=False` — движок не переигрывает такой вызов сам.

Чего здесь нет: `node.invoke` (камера, экран, геолокация, SMS), `exec.*`,
`config.set`, установка скиллов. Мост отвергает их раньше сети, а реестр
инструментов их просто не предлагает.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from ..db import settings_kv, utcnow
from ..tools import REGISTRY, ToolResult, ToolSpec
from ..v2.tables import channel_outbox
from ..v2.openclaw_bridge import (OpenClawBridge, OpenClawConfig, OpenClawForbidden,
                                  OpenClawMemoryConflict, OpenClawScopeError,
                                  OpenClawUnavailable, idempotency_key)
from . import Feature

CONFIG_KEY = "openclaw.config"
MEMORY_KEY = "memory.vault"       # тот же ключ, что у tools_memory — путь задаёт человек
OUTPUT_LIMIT = 6000
MESSAGE_LIMIT = 4000

# Список разрешённых пар нужен ХУКУ ПОЛИТИКИ, а он синхронный и в БД сходить не
# может. Держим копию в памяти; она обновляется при чтении и при записи
# конфигурации. Пустой список = ASK на всё, и это же значение получается при
# любой ошибке загрузки — то есть сбой конфигурации делает систему строже, а не
# свободнее.
_ALLOW: list[dict[str, str]] = []


# ------------------------------------------------------------------ долговечный outbox

class OutboxCollision(RuntimeError):
    """Тот же ключ на другое содержимое или другого получателя.

    Одобрение выдаётся на конкретное сообщение конкретному человеку. Пропустить
    под тем же ключом другой текст значит воспользоваться чужим разрешением.
    """


@dataclass(frozen=True, slots=True)
class OutboxSlot:
    """Что нам известно об этой отправке ДО того, как что-то делать."""

    key: str
    state: str
    fresh: bool                    # True — мы застолбили её первыми
    result: dict[str, Any] = field(default_factory=dict)
    detail: str = ""

    @property
    def needs_human(self) -> bool:
        """UNKNOWN не разрешается ни повтором, ни ожиданием — только человеком."""
        return self.state == "UNKNOWN"


def _body_hash(channel: str, contact: str, body: str) -> str:
    """Отпечаток отправки. Само тело не хранится: журнал живёт долго, а
    переписка с людьми не то, что стоит держать вечно."""
    raw = f"{channel}\x00{contact}\x00{body}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def outbox_reserve(svc, *, key: str, channel: str, contact: str, body: str,
                         run_id: Any = None, mission_id: Any = None) -> OutboxSlot:
    """Застолбить отправку. Ровно один вызов получает `fresh=True`.

    Гонку решает не код, а уникальность первичного ключа в базе: два воркера,
    вставляющие одну строку, дают ровно одну удачную вставку. Проверка «а нет
    ли уже такой» перед вставкой не годится — между проверкой и вставкой
    помещается второй воркер.
    """
    digest = _body_hash(channel, contact, body)
    async with svc.db.session() as s:
        try:
            await s.execute(sa.insert(channel_outbox).values(
                key=key, channel=channel, contact=contact, body_hash=digest,
                state="PENDING", result_json={}, detail="",
                mission_id=_int_or_none(mission_id), run_id=_int_or_none(run_id),
                attempts=0, created_at=utcnow(), updated_at=utcnow()))
            await s.commit()
            return OutboxSlot(key=key, state="PENDING", fresh=True)
        except IntegrityError:
            await s.rollback()

    async with svc.db.session() as s:
        row = (await s.execute(sa.select(channel_outbox)
                               .where(channel_outbox.c.key == key))).mappings().first()
    if row is None:                      # строку удалили между вставкой и чтением
        raise OutboxCollision(f"запись {key} исчезла во время резервирования")
    if row["body_hash"] != digest:
        raise OutboxCollision(
            f"ключ {key} уже занят другой отправкой ({row['channel']} → "
            f"{row['contact']}). Одобрение выдано не на это сообщение.")

    state = str(row["state"])
    if state == "FAILED":
        # Провайдер ответил отказом — внешнего эффекта не было, попытка возможна.
        async with svc.db.session() as s:
            await s.execute(sa.update(channel_outbox)
                            .where(channel_outbox.c.key == key)
                            .values(state="PENDING", detail="", updated_at=utcnow()))
            await s.commit()
        return OutboxSlot(key=key, state="PENDING", fresh=True)
    return OutboxSlot(key=key, state=state, fresh=False,
                      result=dict(row["result_json"] or {}), detail=str(row["detail"] or ""))


async def _outbox_set(svc, key: str, **values: Any) -> None:
    async with svc.db.session() as s:
        await s.execute(sa.update(channel_outbox)
                        .where(channel_outbox.c.key == key)
                        .values(updated_at=utcnow(), **values))
        await s.commit()


async def outbox_mark_sending(svc, key: str) -> None:
    async with svc.db.session() as s:
        await s.execute(sa.update(channel_outbox)
                        .where(channel_outbox.c.key == key)
                        .values(state="SENDING", updated_at=utcnow(),
                                attempts=channel_outbox.c.attempts + 1))
        await s.commit()


async def outbox_mark_sent(svc, key: str, result: dict | None = None) -> None:
    await _outbox_set(svc, key, state="SENT", result_json=dict(result or {}))


async def outbox_mark_unknown(svc, key: str, detail: str = "") -> None:
    """Запрос мог дойти. Это не отказ, и повторять его нельзя."""
    await _outbox_set(svc, key, state="UNKNOWN", detail=detail[:500])


async def outbox_mark_failed(svc, key: str, detail: str = "") -> None:
    """Провайдер ответил отказом — внешнего эффекта не было."""
    await _outbox_set(svc, key, state="FAILED", detail=detail[:500])


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ конфигурация

async def _our_vault(svc) -> str:
    """Каталог нашей памяти — из `settings_kv["memory.vault"]`, где его задал человек."""
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(settings_kv.c.value_enc)
                               .where(settings_kv.c.key == MEMORY_KEY))).first()
    if not (row and row[0]):
        return ""
    try:
        return str((json.loads(svc.vault.decrypt(row[0])) or {}).get("root") or "")
    except Exception:
        return ""


async def _load(svc) -> OpenClawConfig:
    global _ALLOW
    raw: dict[str, Any] = {}
    async with svc.db.session() as s:
        row = (await s.execute(sa.select(settings_kv.c.value_enc)
                               .where(settings_kv.c.key == CONFIG_KEY))).first()
    if row and row[0]:
        try:
            raw = json.loads(svc.vault.decrypt(row[0])) or {}
        except Exception:
            raw = {}
    if not raw.get("vault_root"):
        # Своё хранилище подставляем сами, из настройки памяти. Проверка
        # конфликта (условие 3) не должна зависеть от того, вспомнил ли владелец
        # продублировать путь ещё и здесь: он уже назвал его один раз.
        raw["vault_root"] = await _our_vault(svc)
    cfg = OpenClawConfig.from_dict(raw)
    _ALLOW = [dict(p) for p in cfg.auto_send_allow]
    return cfg


async def _save(svc, raw: dict[str, Any]) -> OpenClawConfig:
    async with svc.db.session() as s:
        await s.execute(sa.delete(settings_kv).where(settings_kv.c.key == CONFIG_KEY))
        await s.execute(sa.insert(settings_kv).values(
            key=CONFIG_KEY,
            value_enc=svc.vault.encrypt(json.dumps(raw, ensure_ascii=False))))
        await s.commit()
    svc.openclaw = None            # соединение поднимется заново с новой конфигурацией
    return await _load(svc)


async def bridge(svc) -> OpenClawBridge:
    """Мост на сервисе. Пересоздаётся, когда конфигурацию поменяли."""
    existing = getattr(svc, "openclaw", None)
    if existing is not None:
        return existing
    br = OpenClawBridge(config=await _load(svc))
    svc.openclaw = br
    return br


# ------------------------------------------------------------------ общее

def _fail(exc: Exception, tool: str) -> ToolResult:
    """Ошибку моста переводим модели честно: причина у каждой своя."""
    if isinstance(exc, OpenClawForbidden):
        text = (f"{exc}\nЭто не временный отказ: метод вне контракта V1. "
                f"Обходить его не нужно — расширение поверхности решает владелец.")
    elif isinstance(exc, OpenClawMemoryConflict):
        text = f"{exc}"
    elif isinstance(exc, OpenClawScopeError):
        text = f"{exc}"
    elif isinstance(exc, OpenClawUnavailable):
        text = (f"OpenClaw недоступен: {exc}\nПовторять вслепую не нужно — "
                f"сначала убедитесь, что Gateway запущен.")
    else:
        text = f"{type(exc).__name__}: {exc}"
    return ToolResult(content=text, one_line=f"{tool}: ошибка", error=True)


def _short(payload: Any) -> str:
    try:
        text = json.dumps(payload, ensure_ascii=False, indent=2)
    except Exception:
        text = str(payload)
    return text[:OUTPUT_LIMIT]


async def _read_call(name: str, params: dict, ctx, tool: str) -> ToolResult:
    try:
        payload = await (await bridge(ctx.svc)).call(name, params, run_id=ctx.run_id)
    except Exception as exc:
        return _fail(exc, tool)
    text = _short(payload)
    return ToolResult(content=text, one_line=f"{tool}: ок",
                      truncated=len(text) >= OUTPUT_LIMIT,
                      more="уточните запрос — ответ обрезан",
                      data={"payload": payload} if isinstance(payload, dict) else {},
                      external=True)


# ------------------------------------------------------------------ чтение

async def _tool_health(args: dict, ctx) -> ToolResult:
    return await _read_call("health", {}, ctx, "openclaw.health")


async def _tool_channels(args: dict, ctx) -> ToolResult:
    return await _read_call("channels", {}, ctx, "openclaw.channels")


async def _tool_agents(args: dict, ctx) -> ToolResult:
    return await _read_call("agents", {}, ctx, "openclaw.agents")


async def _tool_sessions(args: dict, ctx) -> ToolResult:
    params: dict[str, Any] = {}
    if args.get("channel"):
        params["channel"] = str(args["channel"])
    return await _read_call("sessions", params, ctx, "openclaw.sessions")


async def _tool_session_describe(args: dict, ctx) -> ToolResult:
    key = str(args.get("key") or "").strip()
    if not key:
        return ToolResult(content="нужен аргумент key", one_line="openclaw.session: нет ключа",
                          error=True)
    return await _read_call("session_describe", {"key": key}, ctx, "openclaw.session_describe")


# ------------------------------------------------------------------ отправка

def _session_identity(payload: Any) -> tuple[str, str]:
    """Кому на самом деле принадлежит сессия: (канал, контакт).

    Форма ответа у Gateway не зафиксирована жёстко, поэтому читаем защитно и
    возвращаем пустое, когда не нашли, — пустое означает «не подтверждено», и
    отправка на нём остановится.
    """
    node: Any = payload
    if isinstance(node, dict) and isinstance(node.get("session"), dict):
        node = node["session"]
    if not isinstance(node, dict):
        return "", ""
    channel = node.get("channel") or node.get("channelId") or node.get("transport") or ""
    contact = (node.get("contact") or node.get("peer") or node.get("to")
               or node.get("chatId") or node.get("address") or "")
    if isinstance(contact, dict):
        contact = contact.get("id") or contact.get("handle") or contact.get("address") or ""
    return str(channel or "").lower(), str(contact or "")


async def _tool_send(args: dict, ctx) -> ToolResult:
    key = str(args.get("key") or "").strip()
    message = str(args.get("message") or "").strip()
    channel = str(args.get("channel") or "").strip().lower()
    contact = str(args.get("contact") or "").strip()

    if not key or not message:
        return ToolResult(content="нужны аргументы key и message",
                          one_line="openclaw.send: неполный вызов", error=True)
    if not channel or not contact:
        return ToolResult(
            content="нужно явно назвать channel и contact — кому именно уходит сообщение. "
                    "Без этого политика отправки не может быть проверена.",
            one_line="openclaw.send: не назван получатель", error=True)
    if len(message) > MESSAGE_LIMIT:
        return ToolResult(content=f"сообщение длиннее {MESSAGE_LIMIT} символов — сократите",
                          one_line="openclaw.send: слишком длинно", error=True)

    br = await bridge(ctx.svc)

    # Сверка получателя с Gateway. Решение AUTO/ASK принято ДО обработчика по
    # тому, что назвала модель; если не проверить, разрешение, выданное на
    # одного человека, сработало бы на другом.
    try:
        described = await br.call("session_describe", {"key": key}, run_id=ctx.run_id)
    except Exception as exc:
        return _fail(exc, "openclaw.send")

    real_channel, real_contact = _session_identity(described)
    if not real_channel or not real_contact:
        return ToolResult(
            content=f"Gateway не сообщил, кому принадлежит сессия {key}. "
                    f"Отправка не выполнена: получателя нужно подтвердить, а не предположить.",
            one_line="openclaw.send: получатель не подтверждён", error=True)
    if real_channel != channel or real_contact != contact:
        await ctx.svc.bus.emit("agent.warning", tool="openclaw.send",
                               reason="получатель не совпал с заявленным",
                               declared=f"{channel}:{contact}",
                               actual=f"{real_channel}:{real_contact}")
        return ToolResult(
            content=f"Отправка отменена: заявлен получатель {channel}:{contact}, "
                    f"а сессия {key} принадлежит {real_channel}:{real_contact}. "
                    f"Разрешение выдано не на этого адресата.",
            one_line="openclaw.send: получатель не совпал", error=True)

    mission_id = (ctx.task or {}).get("mission_id")
    # Ключ выводится из СОДЕРЖИМОГО отправки, а не из call_id: последний выдаёт
    # провайдер модели, и после нашего собственного повтора он другой.
    idem = idempotency_key(mission_id=mission_id, run_id=ctx.run_id,
                           payload={"channel": real_channel, "contact": real_contact,
                                    "message": message})

    # Столбим отправку в БАЗЕ, а не в памяти. Память очищается перезапуском —
    # ровно тем событием, от которого защита и нужна.
    try:
        slot = await outbox_reserve(ctx.svc, key=idem, channel=real_channel,
                                    contact=real_contact, body=message,
                                    run_id=ctx.run_id, mission_id=mission_id)
    except OutboxCollision as exc:
        return ToolResult(content=f"Отправка отменена: {exc}",
                          one_line="openclaw.send: конфликт ключа", error=True)

    if not slot.fresh:
        if slot.state == "SENT":
            # Это уже отправлено. Возвращаем прежний результат, не отправляя снова.
            return ToolResult(
                content=f"сообщение уже было отправлено ранее: {real_channel} → "
                        f"{real_contact}\n{_short(slot.result)}",
                one_line=f"openclaw.send: повтор, уже отправлено",
                data={"idempotency_key": idem, "duplicate": True}, external=True)
        if slot.needs_human:
            return ToolResult(
                content=f"Предыдущая попытка оборвалась ПОСЛЕ отправки запроса, и "
                        f"неизвестно, дошло ли сообщение до {real_contact}. "
                        f"{slot.detail}\nПовторять нельзя: это может стать вторым "
                        f"сообщением человеку. Нужна сверка человеком.",
                one_line="openclaw.send: состояние неизвестно, нужна сверка",
                error=True)
        return ToolResult(
            content=f"эта отправка уже выполняется другим воркером "
                    f"(состояние {slot.state}). Второй раз запускать не нужно.",
            one_line="openclaw.send: уже выполняется", error=True)

    await outbox_mark_sending(ctx.svc, idem)
    try:
        payload = await br.call("send", {"key": key, "message": message},
                                idem=idem, run_id=ctx.run_id, timeout=30.0)
    except Exception as exc:
        # Неоднозначность и отказ — разные исходы. На отказе внешнего эффекта не
        # было, на неоднозначности он мог быть, и повторять её нельзя.
        if isinstance(exc, OpenClawUnavailable) and getattr(exc, "ambiguous", False):
            await outbox_mark_unknown(ctx.svc, idem, str(exc))
            await ctx.svc.bus.emit("agent.warning", tool="openclaw.send",
                                   reason="неизвестное внешнее состояние",
                                   idempotency_key=idem, channel=real_channel)
            return ToolResult(
                content=f"Связь оборвалась после отправки запроса: {exc}\n"
                        f"Дошло ли сообщение — неизвестно. Слепой повтор запрещён: "
                        f"сначала сверка.",
                one_line="openclaw.send: состояние неизвестно", error=True)
        await outbox_mark_failed(ctx.svc, idem, str(exc))
        return _fail(exc, "openclaw.send")

    await outbox_mark_sent(ctx.svc, idem, payload if isinstance(payload, dict) else {})
    await ctx.svc.bus.emit("agent.action", tool="openclaw.send", channel=real_channel,
                           contact=real_contact, idempotency_key=idem)
    return ToolResult(content=f"сообщение отправлено: {real_channel} → {real_contact}\n"
                              f"{_short(payload)}",
                      one_line=f"openclaw.send: {real_channel} → {real_contact}",
                      data={"idempotency_key": idem, "channel": real_channel,
                            "contact": real_contact},
                      external=True)


def _send_effect(args: dict) -> tuple[str, str] | None:
    """Хук политики. Может только ужесточить — и он ужесточает почти всегда.

    Выданное агенту право `channel.send` делает инструмент AUTO. Здесь ASK
    возвращается обратно для любой пары, которую владелец не разрешил явно.
    Пустой список разрешений (дефолт) означает ASK на всё.
    """
    channel = str(args.get("channel") or "").strip().lower()
    contact = str(args.get("contact") or "").strip()
    if not channel or not contact:
        return "ask", "отправка без явно названного получателя"
    for pair in _ALLOW:
        if pair.get("channel") == channel and pair.get("contact") == contact:
            return None                     # владелец разрешил именно эту пару
    return "ask", f"отправка живому человеку: {channel} → {contact} не в списке разрешённых"


# ------------------------------------------------------------------ реестр

SPECS = [
    ToolSpec(name="openclaw.health",
             description="Состояние Gateway OpenClaw: жив ли он и какие плагины загружены.",
             handler=_tool_health, category="read", permission="channel.read",
             source="openclaw", default_effect="auto", external_output=True),
    ToolSpec(name="openclaw.channels",
             description="Статус каналов связи OpenClaw (какие подключены и работают).",
             handler=_tool_channels, category="read", permission="channel.read",
             source="openclaw", default_effect="auto", external_output=True),
    ToolSpec(name="openclaw.agents",
             description="Список агентов, известных Gateway OpenClaw.",
             handler=_tool_agents, category="read", permission="channel.read",
             source="openclaw", default_effect="auto", external_output=True),
    ToolSpec(name="openclaw.sessions",
             description="Список диалогов (сессий) в каналах связи.",
             handler=_tool_sessions,
             input_schema={"channel": {"type": "string",
                                       "description": "ограничить одним каналом"}},
             category="read", permission="channel.read", source="openclaw",
             default_effect="auto", external_output=True),
    ToolSpec(name="openclaw.session_describe",
             description="Подробности диалога по ключу: канал, собеседник, состояние.",
             handler=_tool_session_describe,
             input_schema={"key": {"type": "string", "description": "ключ сессии"}},
             required=["key"], category="read", permission="channel.read",
             source="openclaw", default_effect="auto", external_output=True),
    ToolSpec(
        name="openclaw.send",
        description=("Отправить сообщение живому человеку в канал связи. Требует "
                     "подтверждения владельца, кроме пар «канал+контакт», которые он "
                     "разрешил заранее. Нужно явно назвать channel и contact — они "
                     "сверяются с настоящим получателем сессии, и при расхождении "
                     "отправки не будет."),
        handler=_tool_send,
        input_schema={
            "key": {"type": "string", "description": "ключ сессии из openclaw.sessions"},
            "channel": {"type": "string", "description": "канал: telegram, whatsapp, …"},
            "contact": {"type": "string", "description": "получатель, как его называет канал"},
            "message": {"type": "string", "description": "текст сообщения"},
        },
        required=["key", "channel", "contact", "message"],
        category="send", permission="channel.send", source="openclaw",
        default_effect="ask", idempotent=False, external_output=True,
        timeout_seconds=60.0, effect_hook=_send_effect),
]


# ------------------------------------------------------------------ HTTP

router = APIRouter(prefix="/openclaw", tags=["openclaw"])


class AllowPair(BaseModel):
    channel: str
    contact: str


class ConfigIn(BaseModel):
    url: str = ""
    token: str = ""
    vault_root: str = ""
    auto_send_allow: list[AllowPair] = []


def _public(cfg: OpenClawConfig) -> dict:
    """Токен наружу не отдаём никогда — только признак, что он задан."""
    return {"url": cfg.url, "has_token": bool(cfg.token), "vault_root": cfg.vault_root,
            "auto_send_allow": cfg.auto_send_allow, "configured": cfg.configured}


@router.get("/config")
async def get_config(request: Request) -> dict:
    return _public(await _load(request.app.state.svc))


@router.post("/config")
async def set_config(body: ConfigIn, request: Request) -> dict:
    svc = request.app.state.svc
    current = await _load(svc)
    raw = {
        "url": body.url.strip(),
        # Пустой токен в запросе означает «не менять», а не «стереть»: иначе
        # правка списка контактов молча выбрасывала бы учётные данные.
        "token": body.token.strip() or current.token,
        "vault_root": body.vault_root.strip() or current.vault_root,
        "auto_send_allow": [{"channel": p.channel.lower().strip(),
                             "contact": p.contact.strip()}
                            for p in body.auto_send_allow
                            if p.channel.strip() and p.contact.strip()],
    }
    return _public(await _save(svc, raw))


@router.post("/health")
async def check(request: Request) -> dict:
    br = await bridge(request.app.state.svc)
    if not br.available:
        return {"ok": False, "detail": "OpenClaw не настроен или нет пакета websockets"}
    try:
        payload = await br.health()
    except Exception as exc:
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "payload": payload, "server_version": br.server_version,
            "protocol": br.protocol, "scopes": list(br.scopes)}


async def _setup(svc) -> None:
    for spec in SPECS:
        REGISTRY.register(spec)
    await _load(svc)              # прогреваем список разрешённых пар для хука


FEATURE = Feature(name="tools_openclaw", router=router, setup=_setup)
