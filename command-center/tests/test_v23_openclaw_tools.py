"""Инструменты OpenClaw: политика отправки и сверка получателя.

Мост уже проверен в `test_v23_openclaw_bridge.py` против поддельного Gateway,
говорящего на реальном протоколе. Здесь проверяется то, что находится ВЫШЕ
моста и что решает, отправится ли сообщение живому человеку.

Уровень доказательности: **REAL IMPLEMENTED** по решению политики и по сверке
получателя (работает боевой код), **MOCK TESTED** по транспорту.
"""
from __future__ import annotations

import pytest

from bcc.features import tools_openclaw as oc
from bcc.tools import ToolContext, decide_effect


# ------------------------------------------------------------------ политика

def _spec(name: str):
    return next(s for s in oc.SPECS if s.name == name)


def _agent(*perms: str) -> dict:
    return {"id": 1, "permissions": {p: True for p in perms}}


@pytest.fixture(autouse=True)
def _clean_allow():
    saved = list(oc._ALLOW)
    oc._ALLOW.clear()
    yield
    oc._ALLOW[:] = saved


ARGS = {"key": "s1", "channel": "telegram", "contact": "@owner", "message": "привет"}


def test_send_is_ask_even_when_the_agent_has_the_permission():
    """Право `channel.send` снимает ASK у опасного инструмента — и это дефолт
    для всех прочих. Для отправки живому человеку хук возвращает ASK обратно."""
    effect, reason = decide_effect(_spec("openclaw.send"), ARGS, _agent("channel.send"))
    assert effect == "ask", reason
    assert "не в списке разрешённых" in reason


def test_send_is_ask_without_the_permission_too():
    effect, _ = decide_effect(_spec("openclaw.send"), ARGS, _agent())
    assert effect == "ask"


def test_owner_may_preapprove_exactly_one_pair():
    oc._ALLOW.append({"channel": "telegram", "contact": "@owner"})
    effect, reason = decide_effect(_spec("openclaw.send"), ARGS, _agent("channel.send"))
    assert effect == "auto", reason

    # тот же канал, другой человек — по-прежнему ASK
    other = {**ARGS, "contact": "@client"}
    assert decide_effect(_spec("openclaw.send"), other, _agent("channel.send"))[0] == "ask"
    # тот же человек, другой канал — тоже ASK
    elsewhere = {**ARGS, "channel": "whatsapp"}
    assert decide_effect(_spec("openclaw.send"), elsewhere, _agent("channel.send"))[0] == "ask"


def test_preapproval_does_not_survive_a_missing_recipient():
    """Без явного получателя разрешение неприменимо — сверять нечего."""
    oc._ALLOW.append({"channel": "telegram", "contact": "@owner"})
    effect, reason = decide_effect(_spec("openclaw.send"), {"key": "s1", "message": "x"},
                                   _agent("channel.send"))
    assert effect == "ask" and "получателя" in reason


def test_reads_do_not_require_confirmation():
    for name in ("openclaw.health", "openclaw.channels", "openclaw.sessions"):
        effect, _ = decide_effect(_spec(name), {}, _agent("channel.read"))
        assert effect == "auto", name


def test_send_is_not_replayed_automatically():
    """Движок не должен переигрывать отправку сам — за это отвечает флаг."""
    assert _spec("openclaw.send").idempotent is False
    assert _spec("openclaw.send").category == "send"


# ------------------------------------------------------------------ сверка получателя

class FakeBridge:
    """Мост-заглушка: решение политики уже принято, проверяем обработчик."""

    def __init__(self, describe, send=None, fail=None):
        self.describe = describe
        self.send_payload = send or {"delivered": True}
        self.fail = fail
        self.sent: list[dict] = []

    async def call(self, name, params=None, *, idem="", run_id=None, timeout=None):
        if name == "session_describe":
            if isinstance(self.describe, Exception):
                raise self.describe
            return self.describe
        if name == "send":
            if self.fail is not None:
                raise self.fail
            self.sent.append({"params": dict(params or {}), "idem": idem})
            return self.send_payload
        raise AssertionError(f"инструмент позвал неожиданный метод {name}")


class FakeBus:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    async def emit(self, name, **payload):
        self.events.append((name, payload))


def _ctx(monkeypatch, bridge: FakeBridge, *, svc=None, mission_id=7, run_id=42,
         call_id="c1"):
    """Контекст вызова.

    `svc` теперь обязателен для отправки: путь идёт через долговечный outbox в
    базе, и подделка без БД его не пройдёт. Это и есть смысл правки — защита от
    дубля больше не живёт в памяти процесса.
    """
    bus = FakeBus()
    if svc is None:
        svc = type("Svc", (), {"bus": bus})()
    else:
        svc.bus = bus                     # шину подменяем, базу оставляем настоящую
    monkeypatch.setattr(oc, "bridge", lambda _svc: _ready(bridge))
    return ToolContext(svc=svc, task={"mission_id": mission_id}, run_id=run_id,
                       agent=_agent("channel.send"), call_id=call_id), bus


async def _ready(value):
    return value


async def test_send_refuses_when_the_session_belongs_to_someone_else(monkeypatch):
    """Главная защита: разрешение выдано на одного человека, а сессия чужая.

    Решение AUTO/ASK принимается по тому, что НАЗВАЛА модель. Если не сверить
    это с настоящим владельцем сессии, разрешение для @owner сработало бы на
    @client. Поэтому сверка живёт в обработчике, уже после решения политики.
    """
    br = FakeBridge(describe={"channel": "telegram", "contact": "@client"})
    ctx, bus = _ctx(monkeypatch, br)
    result = await oc._tool_send(dict(ARGS), ctx)

    assert result.error is True
    assert "@client" in result.content and "@owner" in result.content
    assert br.sent == [], "сообщение ушло получателю, который не совпал с заявленным"
    assert any(name == "agent.warning" for name, _ in bus.events)


async def test_send_refuses_when_the_gateway_will_not_say_who_it_is(monkeypatch):
    """Пустой ответ — это «не подтверждено», а не «подтверждено пустым»."""
    br = FakeBridge(describe={})
    ctx, _ = _ctx(monkeypatch, br)
    result = await oc._tool_send(dict(ARGS), ctx)
    assert result.error is True and "не подтверждён" in result.one_line
    assert br.sent == []


async def test_send_is_not_delivered_twice_even_when_the_step_is_replayed(monkeypatch, env):
    """Повтор шага НЕ доходит до Gateway второй раз.

    Раньше здесь проверялось только совпадение ключей — то есть надежда, что
    дедупликацию сделает чужая сторона. Теперь повтор останавливается у нас,
    записью в базе, и до сети не доходит вовсе.
    """
    br = FakeBridge(describe={"channel": "telegram", "contact": "@owner"})
    ctx, _ = _ctx(monkeypatch, br, svc=env.svc)
    first = await oc._tool_send(dict(ARGS), ctx)
    assert first.error is False
    assert len(br.sent) == 1

    # тот же шаг после «перезапуска»: провайдер выдал НОВЫЙ call_id
    ctx2, _ = _ctx(monkeypatch, br, svc=env.svc, call_id="провайдер-выдал-другой")
    second = await oc._tool_send(dict(ARGS), ctx2)
    assert second.error is False
    assert second.data.get("duplicate") is True
    assert len(br.sent) == 1, "повтор дошёл до Gateway — человек получил второе сообщение"

    # другой текст тому же человеку — это другое действие, оно уходит
    other = {**ARGS, "message": "другое сообщение"}
    ctx3, _ = _ctx(monkeypatch, br, svc=env.svc)
    await oc._tool_send(other, ctx3)
    assert len(br.sent) == 2
    assert br.sent[0]["idem"] != br.sent[1]["idem"]
    assert br.sent[0]["idem"].startswith("bossman-")


async def test_definite_refusal_is_retryable_but_ambiguity_is_not(monkeypatch, env):
    """Отказ и неоднозначность — разные исходы, и путать их нельзя.

    На отказе внешнего эффекта не было: попытка возможна. На обрыве после
    отправки запроса эффект мог случиться, и повтор стал бы вторым сообщением.
    """
    from bcc.v2.openclaw_bridge import OpenClawUnavailable

    # отказ: Gateway ответил — значит принял решение, эффекта не было
    refuse = FakeBridge(describe={"channel": "telegram", "contact": "@owner"},
                        fail=OpenClawUnavailable("INVALID_REQUEST: отказано"))
    ctx, _ = _ctx(monkeypatch, refuse, svc=env.svc)
    denied = await oc._tool_send(dict(ARGS), ctx)
    assert denied.error is True

    # тот же шаг после отказа снова доходит до Gateway — это не дубль
    ok = FakeBridge(describe={"channel": "telegram", "contact": "@owner"})
    ctx2, _ = _ctx(monkeypatch, ok, svc=env.svc)
    assert (await oc._tool_send(dict(ARGS), ctx2)).error is False
    assert len(ok.sent) == 1

    # неоднозначность: связь оборвалась после отправки
    args = {**ARGS, "message": "сообщение с неизвестной судьбой"}
    lost = FakeBridge(describe={"channel": "telegram", "contact": "@owner"},
                      fail=OpenClawUnavailable("нет ответа за 30 с", ambiguous=True))
    ctx3, bus = _ctx(monkeypatch, lost, svc=env.svc)
    unknown = await oc._tool_send(args, ctx3)
    assert unknown.error is True and "неизвестно" in unknown.content
    assert any(name == "agent.warning" for name, _ in bus.events)

    # повтор после неоднозначности НЕ уходит в сеть — требует человека
    again = FakeBridge(describe={"channel": "telegram", "contact": "@owner"})
    ctx4, _ = _ctx(monkeypatch, again, svc=env.svc)
    blocked = await oc._tool_send(args, ctx4)
    assert blocked.error is True and "сверка" in blocked.content
    assert again.sent == [], "слепой повтор после неоднозначности дошёл до Gateway"


async def test_send_needs_an_explicit_recipient(monkeypatch):
    br = FakeBridge(describe={"channel": "telegram", "contact": "@owner"})
    ctx, _ = _ctx(monkeypatch, br)
    result = await oc._tool_send({"key": "s1", "message": "привет"}, ctx)
    assert result.error is True and br.sent == []


async def test_forbidden_method_is_explained_as_final(monkeypatch):
    """Отказ по контракту V1 — не временный сбой, и модель не должна его обходить."""
    from bcc.v2.openclaw_bridge import OpenClawForbidden

    br = FakeBridge(describe=OpenClawForbidden("метод node.invoke вне контракта V1"))
    ctx, _ = _ctx(monkeypatch, br)
    result = await oc._tool_send(dict(ARGS), ctx)
    assert result.error is True
    assert "не временный отказ" in result.content


# ------------------------------------------------------------------ разбор ответа

def test_session_identity_reads_the_shapes_a_gateway_may_use():
    assert oc._session_identity({"channel": "telegram", "contact": "@a"}) == ("telegram", "@a")
    assert oc._session_identity({"session": {"channelId": "WhatsApp",
                                             "peer": "+420"}}) == ("whatsapp", "+420")
    assert oc._session_identity({"transport": "telegram",
                                 "contact": {"id": "@b"}}) == ("telegram", "@b")
    assert oc._session_identity({"channel": "telegram"}) == ("telegram", "")
    assert oc._session_identity(None) == ("", "")
    assert oc._session_identity("строка вместо объекта") == ("", "")


def test_tool_catalogue_does_not_offer_the_dangerous_surface():
    """node.invoke даёт камеру, экран, геолокацию и SMS — инструмента для него нет."""
    names = {s.name for s in oc.SPECS}
    assert not any("node" in n or "exec" in n or "config" in n for n in names)
    assert names == {"openclaw.health", "openclaw.channels", "openclaw.agents",
                     "openclaw.sessions", "openclaw.session_describe", "openclaw.send"}
