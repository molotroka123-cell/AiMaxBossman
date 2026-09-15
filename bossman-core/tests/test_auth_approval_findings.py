"""Аудит №2 (auth/approvals/perimeter): воспроизведение и регрессия по A2-*.

Каждая находка закрыта парой: тест на сам дефект и позитивный (для запретов —
негативный) контроль, доказывающий, что законный путь не сломан.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from bossman import approvals as approvals_mod
from bossman import api, obs, runner
from bossman.computer_operator import routes as co_routes
from bossman.remote_client.auth import (SCOPE_ADMIN, SCOPE_APPROVE, SCOPE_CHAT,
                                        SCOPE_EVENTS, Principal)

SECRET = "rcd_" + "A" * 40


# ---------- A2-01: сырые аргументы в approvals.payload и в GET /approvals ----------

class _Grant:
    confirm = True


class _Agent:
    name = "a"
    title = "A"

    def grant(self, name):
        return _Grant()


class _Tool:
    name = "http.post"
    rights = "send"
    confirm_default = True
    mandatory_confirm = None

    async def handler(self, args, ctx):
        from bossman.toolkit import ToolResult
        return ToolResult("ok", one_line="http.post: ок")


async def _call_confirmed_tool(monkeypatch, args):
    """Прогнать _call_tool по ветке подтверждения, вернув payload из approvals.create."""
    from bossman.toolkit import ToolContext

    seen = {}

    async def fake_create(kind, preview, *, task_id=None, run_id=None, tool=None, payload=None):
        seen["payload"] = payload
        seen["preview"] = preview
        return 7

    async def fake_wait(approval_id, timeout_s=24 * 3600):
        return {"status": "approved", "decided_by": "device:dev_1"}

    async def noop(*a, **kw):
        return None

    monkeypatch.setattr(runner.db, "execute", noop)
    monkeypatch.setattr(runner.events, "emit", lambda *a, **kw: None)
    monkeypatch.setattr(runner, "by_api_name", lambda n: _Tool())
    monkeypatch.setattr(runner, "_cybersec_inspect_external", lambda t, **kw: t)
    monkeypatch.setattr(runner.approvals, "create", fake_create)
    monkeypatch.setattr(runner.approvals, "wait", fake_wait)
    await runner._call_tool(_Agent(), 1, 1, "http.post", args,
                            ToolContext(agent="a", workdir="."))
    return seen


@pytest.mark.asyncio
async def test_a2_01_approval_payload_is_redacted(monkeypatch):
    seen = await _call_confirmed_tool(
        monkeypatch, {"headers": {"Authorization": f"Bearer {SECRET}"}, "url": "https://x"})
    assert SECRET not in json.dumps(seen["payload"], ensure_ascii=False)
    assert SECRET not in seen["preview"]


@pytest.mark.asyncio
async def test_a2_01_positive_control_payload_keeps_non_secret_args(monkeypatch):
    """Позитивный контроль: обычные аргументы доезжают до подтверждения целиком."""
    seen = await _call_confirmed_tool(monkeypatch, {"url": "https://x", "retries": 3})
    assert seen["payload"]["url"] == "https://x"
    assert seen["payload"]["retries"] == 3


@pytest.mark.asyncio
async def test_a2_01_list_approvals_redacts_payload(monkeypatch):
    rows = [{"id": 1, "preview": f"Bearer {SECRET}", "status": "pending",
             "payload": {"headers": {"Authorization": f"Bearer {SECRET}"}}}]

    async def fake_fetch(sql, *a):
        return rows

    monkeypatch.setattr(api.db, "fetch", fake_fetch)
    out = await api.list_approvals()
    assert SECRET not in json.dumps(out, ensure_ascii=False, default=str)
    assert out[0]["id"] == 1 and out[0]["status"] == "pending"   # позитивный контроль


# ---------- A2-03: тумблер computer_control в обход resume/take-control ----------

class _FakeTask:
    def __init__(self, tid="t1", owner="dev_1"):
        self.id = tid; self.owner_device_id = owner; self.goal = "g"
        self.mode = None; self.state = None; self.steps_used = 0; self.max_steps = 1
        self.replans_used = 0; self.last_error = None; self.waiting_approval_id = None
        self.last_observation = None


class _FakeStore:
    def __init__(self, task):
        self.task = task

    def get(self, i):
        return self.task

    def list(self):
        return [self.task]


class _FakeManager:
    """Менеджер ровно с тем контрактом, который трогают роуты."""
    def __init__(self, task, access_check=None):
        self.store = _FakeStore(task); self.access_check = access_check
        self.resumed = []; self.taken = []

    def resume(self, i):
        self.resumed.append(i); return self.store.task

    def take_control(self, i):
        self.taken.append(i); return self.store.task

    async def run(self, i):
        return None


def _view_safe(t):
    return {"id": t.id}


@pytest.fixture
def _co_routes_view(monkeypatch):
    # view() читает enum'ы задачи; для этих тестов важен только вызов гейта.
    monkeypatch.setattr(co_routes, "view", _view_safe)


def _principal(scopes=(SCOPE_CHAT,), device_id="dev_1"):
    return Principal(device_id=device_id, scopes=frozenset(scopes), name="phone")


@pytest.mark.asyncio
async def test_a2_03_resume_rechecks_profile_toggle(monkeypatch, _co_routes_view):
    calls = []

    def deny(device_id, source="local"):
        calls.append((device_id, source))
        raise PermissionError("computer_control выключен")

    task = _FakeTask()
    mgr = _FakeManager(task, access_check=deny)
    monkeypatch.setattr(co_routes, "MANAGER", mgr)
    with pytest.raises(PermissionError):
        await co_routes.resume("t1", _principal())
    assert mgr.resumed == []          # задача не возобновлена
    assert calls and calls[0][1] != "local"   # источник не подменён на «локального хозяина»


@pytest.mark.asyncio
async def test_a2_03_take_control_rechecks_profile_toggle(monkeypatch, _co_routes_view):
    def deny(device_id, source="local"):
        raise PermissionError("computer_control выключен")

    mgr = _FakeManager(_FakeTask(), access_check=deny)
    monkeypatch.setattr(co_routes, "MANAGER", mgr)
    with pytest.raises(PermissionError):
        await co_routes.take("t1", _principal())
    assert mgr.taken == []


@pytest.mark.asyncio
async def test_a2_03_negative_control_allowed_profile_still_resumes(monkeypatch, _co_routes_view):
    """Негативный контроль: с включённым тумблером resume/take-control работают."""
    seen = []

    def allow(device_id, source="local"):
        seen.append((device_id, source))

    mgr = _FakeManager(_FakeTask(), access_check=allow)
    monkeypatch.setattr(co_routes, "MANAGER", mgr)
    monkeypatch.setattr(co_routes.asyncio, "create_task", lambda coro: coro.close())
    assert (await co_routes.resume("t1", _principal()))["id"] == "t1"
    assert (await co_routes.take("t1", _principal()))["id"] == "t1"
    assert mgr.resumed == ["t1"] and mgr.taken == ["t1"] and len(seen) == 2


@pytest.mark.asyncio
async def test_a2_03_no_gate_configured_is_noop(monkeypatch, _co_routes_view):
    """access_check=None (локальная сборка без профилей) — поведение как раньше."""
    mgr = _FakeManager(_FakeTask(), access_check=None)
    monkeypatch.setattr(co_routes, "MANAGER", mgr)
    monkeypatch.setattr(co_routes.asyncio, "create_task", lambda coro: coro.close())
    assert (await co_routes.resume("t1", _principal()))["id"] == "t1"


# ---------- A2-06: wait() теряет параллельное решение ----------

@pytest.mark.asyncio
async def test_a2_06_wait_returns_real_decision_on_race(monkeypatch):
    """Гонка: decide() успел записать approved ровно перед истечением ожидания."""
    row = {"id": 5, "status": "approved", "decided_by": "device:dev_1"}

    updated = []

    async def fetchrow(sql, *a):
        return row                 # строка уже не pending: expire её не тронул

    async def execute(sql, *a):
        updated.append(sql)
        return None

    monkeypatch.setattr(approvals_mod.db, "fetchrow", fetchrow)
    monkeypatch.setattr(approvals_mod.db, "execute", execute)
    out = await approvals_mod.wait(5, timeout_s=0)
    assert out["status"] == "approved"
    assert out["decided_by"] == "device:dev_1"


@pytest.mark.asyncio
async def test_a2_06_positive_control_timeout_still_expires(monkeypatch):
    """Позитивный контроль: никто не решил — истечение по-прежнему expired."""
    states = {"status": "pending"}

    async def fetchrow(sql, *a):
        return dict(states, id=6)

    async def execute(sql, *a):
        if "expired" in sql:
            states["status"] = "expired"
        return None

    monkeypatch.setattr(approvals_mod.db, "fetchrow", fetchrow)
    monkeypatch.setattr(approvals_mod.db, "execute", execute)
    out = await approvals_mod.wait(6, timeout_s=0)
    assert out["status"] == "expired"


# ---------- A2-08: egress-guard на telegram fail-open ----------

def test_a2_08_broken_egress_guard_is_fail_closed(monkeypatch):
    from bossman.cybersec import guards
    from bossman.notifications import telegram_transport as tt

    def boom(text, channel=None):
        raise RuntimeError("guard exploded")

    monkeypatch.setattr(guards, "egress_guard", boom)
    out = tt._egress_guard_text(f"токен {SECRET}")
    assert SECRET not in out


def test_a2_08_positive_control_allow_passes_text_through(monkeypatch):
    from bossman.cybersec import guards
    from bossman.notifications import telegram_transport as tt

    class _V:
        decision = guards.EgressDecision.ALLOW

    monkeypatch.setattr(guards, "egress_guard", lambda text, channel=None: _V())
    assert tt._egress_guard_text("обычный текст") == "обычный текст"


# ---------- A2-09: редактор не знает собственные форматы токенов ----------

def test_a2_09_platform_tokens_are_redacted():
    assert SECRET not in obs.redact(f"device token {SECRET} in log")
    session = "rcs_" + "B" * 40
    assert session not in obs.redact(f"session {session}")
    assert session not in obs.redact(f"subprotocol bossman.bearer.{session}")


def test_a2_09_negative_control_ordinary_text_survives():
    """Негативный контроль: обычный текст не превращается в «REDACTED»."""
    text = "record rcs and rcd_ prefixes without a token; recovery done"
    assert obs.redact(text) == text


# ---------- A2-10: bootstrap выдаёт admin по умолчанию ----------

def test_a2_10_bootstrap_default_scopes_have_no_admin():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "bootstrap_remote_device.py"
    spec = importlib.util.spec_from_file_location("_bootstrap_probe", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    default = {x.strip() for x in mod.DEFAULT_SCOPES.split(",") if x.strip()}
    assert SCOPE_ADMIN not in default
    # позитивный контроль: телефон остаётся рабочим — чат, события, подтверждения
    assert default == {SCOPE_CHAT, SCOPE_EVENTS, SCOPE_APPROVE}


# ---------- A2-11: подменяемая личность решившего ----------

@pytest.mark.asyncio
async def test_a2_11_decided_by_comes_from_principal(monkeypatch):
    seen = {}

    async def fake_decide(approval_id, approve, decided_by):
        seen["by"] = decided_by
        return {"id": approval_id, "status": "approved", "decided_by": decided_by}

    monkeypatch.setattr(api.approvals_mod, "decide", fake_decide)
    body = api.Decision(approve=True, by="device:dev_ВРАГ")
    out = await api.decide_approval(1, body, _principal((SCOPE_APPROVE,), "dev_1"))
    assert seen["by"] == "device:dev_1"
    assert out["decided_by"] == "device:dev_1"


# ---------- A2-05: отзыв устройства не рвёт открытый поток событий ----------

@pytest.mark.asyncio
async def test_a2_05_revoked_device_stops_event_stream(monkeypatch):
    from bossman.remote_client import events as rc_events
    from bossman.remote_client.service import DeviceService
    from bossman.remote_client.store import InMemoryDeviceStore

    svc = DeviceService(InMemoryDeviceStore())
    device_id, _raw = await svc.enroll("phone", {SCOPE_CHAT, SCOPE_EVENTS})
    monkeypatch.setattr(rc_events, "get_service", lambda: svc)
    monkeypatch.setattr(rc_events, "REAUTH_INTERVAL_S", 0.01)

    principal = Principal(device_id=device_id, scopes=frozenset({SCOPE_CHAT, SCOPE_EVENTS}),
                          name="phone")
    q: asyncio.Queue = asyncio.Queue()
    q.put_nowait(json.dumps({"kind": "task.updated", "id": 1}))
    stream = rc_events.iter_device_events(principal, q)
    assert json.loads(await asyncio.wait_for(stream.__anext__(), 1))["id"] == 1
    await svc.revoke_device(device_id)
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(stream.__anext__(), 2)


@pytest.mark.asyncio
async def test_a2_05_positive_control_live_device_keeps_streaming(monkeypatch):
    from bossman.remote_client import events as rc_events
    from bossman.remote_client.service import DeviceService
    from bossman.remote_client.store import InMemoryDeviceStore

    svc = DeviceService(InMemoryDeviceStore())
    device_id, _raw = await svc.enroll("phone", {SCOPE_CHAT, SCOPE_EVENTS})
    monkeypatch.setattr(rc_events, "get_service", lambda: svc)
    monkeypatch.setattr(rc_events, "REAUTH_INTERVAL_S", 0.01)

    principal = Principal(device_id=device_id, scopes=frozenset({SCOPE_CHAT, SCOPE_EVENTS}),
                          name="phone")
    q: asyncio.Queue = asyncio.Queue()
    stream = rc_events.iter_device_events(principal, q)
    await asyncio.sleep(0.05)                       # несколько циклов переаутентификации
    q.put_nowait(json.dumps({"kind": "task.updated", "id": 2}))
    assert json.loads(await asyncio.wait_for(stream.__anext__(), 1))["id"] == 2
    await stream.aclose()


# ---------- A2-07: анонимность telegram-решения ----------

def _tg_transport(tmp_path, handler):
    from bossman.notifications.store import SQLiteNotificationStore
    from bossman.notifications.telegram_transport import TelegramTransport
    return TelegramTransport(SQLiteNotificationStore(tmp_path / "n.db"),
                             bot_token_provider=lambda: "TEST_BOT_TOKEN_NOT_REAL",
                             chat_id_provider=lambda: "123",
                             webhook_secret_provider=lambda: "WEBHOOK_TEST_SECRET_NOT_REAL",
                             action_handler=handler)


def _tg_update(opaque, *, uid="777", chat_type="private"):
    return {"callback_query": {"id": "cb1", "data": "b:" + opaque,
                               "from": {"id": uid},
                               "message": {"chat": {"id": 123, "type": chat_type}}}}


def _tg_action():
    from bossman.notifications.models import ActionKind, NotificationAction
    return NotificationAction(kind=ActionKind.APPROVE, target_type="approval",
                              target_id="42", label="Approve", fingerprint="fp")


@pytest.mark.asyncio
async def test_a2_07_decided_by_carries_telegram_user(tmp_path):
    seen = []

    async def handler(action, actor):
        seen.append(actor)

    t = _tg_transport(tmp_path, handler)
    opaque = t.store.create_callback(_tg_action(), "123")
    await t.handle_webhook(_tg_update(opaque), "WEBHOOK_TEST_SECRET_NOT_REAL")
    assert seen == ["tg:user:777@chat:123"]


@pytest.mark.asyncio
async def test_a2_07_group_chat_without_allowlist_is_denied(tmp_path, monkeypatch):
    from bossman.notifications.store import CallbackRejected

    monkeypatch.delenv("TELEGRAM_ALLOWED_USER_IDS", raising=False)
    called = []

    async def handler(action, actor):
        called.append(actor)

    t = _tg_transport(tmp_path, handler)
    opaque = t.store.create_callback(_tg_action(), "123")
    with pytest.raises(CallbackRejected):
        await t.handle_webhook(_tg_update(opaque, chat_type="supergroup"),
                               "WEBHOOK_TEST_SECRET_NOT_REAL")
    assert called == []


@pytest.mark.asyncio
async def test_a2_07_negative_control_group_with_allowlist_works(tmp_path, monkeypatch):
    """Негативный контроль: разрешённый участник группы решает как раньше."""
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "777")
    seen = []

    async def handler(action, actor):
        seen.append(actor)

    t = _tg_transport(tmp_path, handler)
    opaque = t.store.create_callback(_tg_action(), "123")
    await t.handle_webhook(_tg_update(opaque, chat_type="group"),
                           "WEBHOOK_TEST_SECRET_NOT_REAL")
    assert seen == ["tg:user:777@chat:123"]


# ---------- A2-05 (вторая половина): бессрочная сессия ----------

@pytest.mark.asyncio
async def test_a2_05_expired_session_token_is_refused(monkeypatch):
    from bossman.errors import DeviceRevoked
    from bossman.remote_client import service as rc_service
    from bossman.remote_client.store import InMemoryDeviceStore

    svc = rc_service.DeviceService(InMemoryDeviceStore())
    device_id, _raw = await svc.enroll("phone", {SCOPE_CHAT})
    _sid, session_token = await svc.open_session(device_id)
    # свежая сессия работает — позитивный контроль
    assert (await svc.authenticate(f"Bearer {session_token}")).device_id == device_id
    # Возраст сессии — единственное, что меняется: время не подменяем (модуль
    # time общий для процесса), сдвигаем сам предел.
    monkeypatch.setattr(rc_service, "SESSION_MAX_AGE_S", -1.0)
    with pytest.raises(DeviceRevoked):
        await svc.authenticate(f"Bearer {session_token}")


# ---------- A2-04: fail-open downgrade источника в manager.create_task ----------

def test_a2_04_typeerror_inside_gate_must_not_downgrade_source(tmp_path):
    """Внутренний TypeError гейта не должен превращать remote-источник в local.

    `except TypeError: access_check(owner_device_id)` ловил ЛЮБОЙ TypeError,
    включая поднятый ВНУТРИ гейта на боевом пути, и переспрашивал проверку БЕЗ
    источника. Источник по умолчанию — "local": внутренняя ошибка сервиса
    профилей молча повышала удалённый вызов до локального и открывала рабочий
    стол. Отказ обязан быть fail-CLOSED.
    """
    from bossman.computer_operator.wiring import FakeObserver, FakePlanner, make_manager

    sources = []

    def gate(device_id, source="local"):
        sources.append(source)
        if len(sources) == 1:
            raise TypeError("боевой баг внутри profiles-сервиса")
        raise PermissionError("computer_control выключен")

    mgr = make_manager(tmp_path / "tasks.json", FakePlanner(), FakeObserver(),
                       access_check=gate)
    with pytest.raises(PermissionError):
        mgr.create_task("сделай что-нибудь", source="core:rcd_x", owner_device_id="dev_x")
    assert sources == ["core:rcd_x"]


def test_a2_04_a_one_argument_gate_still_works(tmp_path):
    """Положительный контроль: совместимость со старым колбэком не потеряна."""
    from bossman.computer_operator.wiring import FakeObserver, FakePlanner, make_manager

    seen = []

    def old_gate(device_id):
        seen.append(device_id)

    mgr = make_manager(tmp_path / "tasks.json", FakePlanner(), FakeObserver(),
                       access_check=old_gate)
    mgr.create_task("посмотреть экран", source="core:rcd_x", owner_device_id="dev_x")
    assert seen == ["dev_x"]


def test_a2_04_a_one_argument_gate_that_refuses_still_refuses(tmp_path):
    from bossman.computer_operator.wiring import FakeObserver, FakePlanner, make_manager

    def old_gate(device_id):
        raise PermissionError("computer_control выключен")

    mgr = make_manager(tmp_path / "tasks.json", FakePlanner(), FakeObserver(),
                       access_check=old_gate)
    with pytest.raises(PermissionError):
        mgr.create_task("сделай что-нибудь", source="local", owner_device_id="dev_x")


def test_a2_04_the_source_reaches_a_two_argument_gate_verbatim(tmp_path):
    """Гейт обязан получить ИМЕННО тот источник, с которым пришёл вызов."""
    from bossman.computer_operator.wiring import FakeObserver, FakePlanner, make_manager

    seen = []
    mgr = make_manager(tmp_path / "tasks.json", FakePlanner(), FakeObserver(),
                       access_check=lambda d, s: seen.append((d, s)))
    mgr.create_task("посмотреть экран", source="core:rcd_x", owner_device_id="dev_x")
    assert seen == [("dev_x", "core:rcd_x")]
