"""Мост OpenClaw, контракт V1 — три условия §13 проверяются кодом.

Тесты идут против ПОДДЕЛЬНОГО Gateway: настоящий OpenClaw в этой среде не
поднят, и притворяться иначе нельзя. Но подделка говорит на реальном протоколе,
снятом с живого Gateway (`docs/research/openclaw.md` §3): кадры `req`/`res`,
`connect.challenge` до рукопожатия, `hello-ok` с `auth.scopes` и
`features.methods`, ошибки вида `{code, message, details}`.

Уровень доказательности: **MOCK TESTED** по транспорту, **REAL IMPLEMENTED** по
разбору кадров — разбирает их тот же код, что пойдёт в бой.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from bcc.v2.openclaw_bridge import (CLIENT_ID, FORBIDDEN_SCOPES, NEVER_PROXY, REQUIRED_SCOPES,
                                    OpenClawBridge, OpenClawConfig, OpenClawForbidden,
                                    OpenClawMemoryConflict, OpenClawScopeError,
                                    OpenClawUnavailable, idempotency_key, memory_conflict,
                                    traceparent)

websockets = pytest.importorskip("websockets", reason="нет пакета websockets")


class FakeGateway:
    """Минимальный Gateway на реальном протоколе OpenClaw."""

    def __init__(self, *, scopes=REQUIRED_SCOPES, token="", config=None,
                 send_challenge=True, fail_connect="", config_error=""):
        self.scopes = list(scopes)
        self.token = token
        self.config = config or {}
        self.send_challenge = send_challenge
        self.fail_connect = fail_connect
        # Gateway, который не отдаёт конфигурацию: нет права, старая версия,
        # метод выключен. Ровно тот случай, в котором мост шёл вслепую.
        self.config_error = config_error
        self.seen: list[dict] = []
        self.server = None
        self.url = ""

    async def start(self):
        self.server = await websockets.serve(self._handle, "127.0.0.1", 0)
        port = list(self.server.sockets)[0].getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}"
        return self.url

    async def stop(self):
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()

    async def _handle(self, ws):
        if self.send_challenge:
            await ws.send(json.dumps({"type": "event", "event": "connect.challenge",
                                      "payload": {"nonce": "n", "ts": 1}}))
        async for raw in ws:
            msg = json.loads(raw)
            self.seen.append(msg)
            mid, method = msg.get("id"), msg.get("method")
            params = msg.get("params") or {}

            if method == "connect":
                if self.fail_connect:
                    await self._err(ws, mid, self.fail_connect, "отказано")
                    continue
                if self.token and (params.get("auth") or {}).get("token") != self.token:
                    await self._err(ws, mid, "INVALID_REQUEST",
                                    "unauthorized: gateway token mismatch")
                    continue
                await self._ok(ws, mid, {
                    "type": "hello-ok", "protocol": 4,
                    "server": {"version": "2026.6.34", "connId": "c1"},
                    "features": {"methods": ["health", "agents.list", "sessions.send"],
                                 "events": []},
                    "snapshot": {}, "auth": {"role": "operator", "scopes": self.scopes},
                    "policy": {"maxPayload": 26214400}})
            elif method == "health":
                await self._ok(ws, mid, {"ok": True, "plugins": {"loaded": ["memory-core"]}})
            elif method == "config.get":
                if self.config_error:
                    await self._err(ws, mid, self.config_error, "нет доступа к конфигурации")
                    continue
                await self._ok(ws, mid, self.config)
            elif method == "agents.list":
                await self._ok(ws, mid, {"defaultId": "main", "agents": [{"id": "main"}]})
            elif method == "sessions.send":
                await self._ok(ws, mid, {"delivered": True,
                                         "idempotencyKey": params.get("idempotencyKey")})
            else:
                await self._err(ws, mid, "UNKNOWN_METHOD", method or "")

    async def _ok(self, ws, mid, payload):
        await ws.send(json.dumps({"type": "res", "id": mid, "ok": True,
                                  "payload": payload}, ensure_ascii=False))

    async def _err(self, ws, mid, code, message, details=None):
        await ws.send(json.dumps({"type": "res", "id": mid, "ok": False,
                                  "error": {"code": code, "message": message,
                                            "details": details or {}}},
                                 ensure_ascii=False))


@pytest.fixture
async def gateway():
    made: list[FakeGateway] = []

    async def make(**kw):
        gw = FakeGateway(**kw)
        await gw.start()
        made.append(gw)
        return gw

    yield make
    for gw in made:
        await gw.stop()


def _bridge(url: str, **kw) -> OpenClawBridge:
    return OpenClawBridge(config=OpenClawConfig(url=url, **kw))


# ------------------------------------------------------------------ без сети

def test_idempotency_key_is_derived_not_random():
    """Случайный ключ не защищает ни от чего: при повторе он был бы другим."""
    a = idempotency_key(mission_id=7, run_id=42, call_id="c1")
    b = idempotency_key(mission_id=7, run_id=42, call_id="c1")
    assert a == b, "повтор того же действия обязан дать тот же ключ"
    assert a != idempotency_key(mission_id=7, run_id=42, call_id="c2")
    assert a != idempotency_key(mission_id=8, run_id=42, call_id="c1")
    assert a.startswith("bossman-")


def test_traceparent_is_valid_w3c():
    tp = traceparent(run_id=1)
    parts = tp.split("-")
    assert len(parts) == 4 and parts[0] == "00"
    assert len(parts[1]) == 32 and len(parts[2]) == 16


def test_auto_send_requires_channel_and_contact_together():
    """«Весь Telegram» разрешить нельзя — в этом и смысл условия 1."""
    cfg = OpenClawConfig(url="ws://x", auto_send_allow=[
        {"channel": "telegram", "contact": "@owner"}])
    assert cfg.send_is_preapproved("telegram", "@owner") is True
    assert cfg.send_is_preapproved("telegram", "@client") is False
    assert cfg.send_is_preapproved("whatsapp", "@owner") is False
    # пустой список = ASK на всё; это дефолт и он намеренно неудобный
    assert OpenClawConfig(url="ws://x").send_is_preapproved("telegram", "@owner") is False


def test_auto_send_ignores_wildcards_in_config():
    cfg = OpenClawConfig.from_dict({"url": "ws://x", "auto_send_allow": [
        {"channel": "telegram", "contact": "*"}, {"channel": "telegram"}]})
    # «*» — это просто контакт с таким именем, а не подстановка
    assert cfg.send_is_preapproved("telegram", "@кто-угодно") is False
    assert len(cfg.auto_send_allow) == 1        # запись без contact отброшена


def test_memory_conflict_detects_shared_vault():
    """Условие 3: их вики не должна смотреть в наше хранилище."""
    ours = "/home/user/Obsidian/BOSSMAN"
    assert memory_conflict({"memory": {"wiki": {"path": ours}}}, ours)
    assert memory_conflict({"plugins": {"memory-wiki": {"vault": ours + "/sub"}}}, ours)
    # их собственный каталог конфликтом не является
    assert memory_conflict({"memory": {"wiki": {"path": "/root/.openclaw/wiki"}}},
                           ours) == ""
    # похожее имя, но другой каталог — не конфликт
    assert memory_conflict({"memory": {"path": "/home/user/Obsidian/BOSSMAN-OLD"}},
                           ours) == ""


def test_never_proxy_list_covers_the_dangerous_surface():
    """node.invoke даёт камеру, экран, геолокацию и SMS — его в V1 нет."""
    assert "node.invoke" in NEVER_PROXY
    assert "config.set" in NEVER_PROXY
    assert "skills.proposals.apply" in NEVER_PROXY


# ------------------------------------------------------------------ против Gateway

async def test_handshake_reads_scopes_and_version(gateway):
    gw = await gateway()
    br = _bridge(gw.url)
    payload = await br.health()
    assert payload["ok"] is True
    assert br.server_version == "2026.6.34" and br.protocol == 4
    assert list(br.scopes) == list(REQUIRED_SCOPES)

    connect = next(m for m in gw.seen if m.get("method") == "connect")
    assert connect["params"]["client"]["id"] == CLIENT_ID
    assert connect["params"]["scopes"] == list(REQUIRED_SCOPES)
    await br.close()


async def test_bridge_refuses_when_gateway_grants_admin(gateway):
    """Лишние права — не удобство, а расширение поверхности отказа."""
    gw = await gateway(scopes=[*REQUIRED_SCOPES, *FORBIDDEN_SCOPES])
    br = _bridge(gw.url)
    with pytest.raises(OpenClawScopeError, match="не просил"):
        await br.health()
    await br.close()


async def test_send_carries_derived_idempotency_key(gateway):
    """Наш ретрай не должен стать вторым сообщением человеку."""
    gw = await gateway()
    br = _bridge(gw.url)
    key = idempotency_key(mission_id=1, run_id=2, call_id="abc")
    first = await br.call("send", {"key": "s1", "message": "привет"},
                          idem=key, run_id=2)
    assert first["idempotencyKey"] == key

    second = await br.call("send", {"key": "s1", "message": "привет"},
                           idem=key, run_id=2)
    assert second["idempotencyKey"] == key      # тот же ключ на повторе

    sends = [m for m in gw.seen if m.get("method") == "sessions.send"]
    assert len(sends) == 2
    assert sends[0]["params"]["idempotencyKey"] == sends[1]["params"]["idempotencyKey"]
    assert all(m.get("traceparent") for m in sends)   # трассировка на каждом
    await br.close()


async def test_methods_outside_v1_are_refused_before_the_wire(gateway):
    """node.invoke не уходит в сеть вообще — отвергается у нас."""
    gw = await gateway()
    br = _bridge(gw.url)
    await br.health()
    before = len(gw.seen)
    for method in ("node.invoke", "config.set", "skills.proposals.apply", "exec.approvals.set"):
        with pytest.raises(OpenClawForbidden):
            await br.call(method, {})
    assert len(gw.seen) == before, "запрещённый метод дошёл до Gateway"
    await br.close()


async def test_memory_conflict_blocks_the_bridge(gateway):
    """Соединение не должно устанавливаться вообще, а не «проверяться потом».

    Раньше тест звал `br._check_memory(ws)` руками — и был зелёным при том,
    что боевой путь проверку не вызывал. Теперь проверка сидит в `_open`, и
    сюда мы приходим тем же путём, что и бой.
    """
    ours = "/home/user/Obsidian/BOSSMAN"
    gw = await gateway(config={"memory": {"wiki": {"path": ours, "render": "obsidian"}}})
    br = _bridge(gw.url, vault_root=ours)
    with pytest.raises(OpenClawMemoryConflict, match="потеря данных"):
        await br._open()
    assert br._ws is None, "соединение с конфликтующей памятью осталось открытым"
    await br.close()


async def test_memory_check_passes_when_vaults_are_separate(gateway):
    gw = await gateway(config={"memory": {"wiki": {"path": "/root/.openclaw/wiki"}}})
    br = _bridge(gw.url, vault_root="/home/user/Obsidian/BOSSMAN")
    assert await br._open() is not None         # не бросает
    await br.close()


async def test_bad_token_is_reported_not_swallowed(gateway):
    gw = await gateway(token="правильный")
    br = _bridge(gw.url, token="неправильный")
    with pytest.raises(OpenClawUnavailable, match="token mismatch"):
        await br.health()
    await br.close()


async def test_startup_sidecars_is_reported_as_retryable(gateway):
    """Холодный старт Gateway — не отказ, и путать их нельзя."""
    gw = await gateway(fail_connect="UNAVAILABLE")

    async def handle(ws):
        await ws.send(json.dumps({"type": "event", "event": "connect.challenge",
                                  "payload": {"nonce": "n", "ts": 1}}))
        async for raw in ws:
            msg = json.loads(raw)
            await ws.send(json.dumps({
                "type": "res", "id": msg.get("id"), "ok": False,
                "error": {"code": "UNAVAILABLE", "message": "starting",
                          "details": {"reason": "startup-sidecars"},
                          "retryable": True}}))

    gw.server.close()
    await gw.server.wait_closed()
    server = await websockets.serve(handle, "127.0.0.1", 0)
    port = list(server.sockets)[0].getsockname()[1]
    br = _bridge(f"ws://127.0.0.1:{port}")
    with pytest.raises(OpenClawUnavailable, match="ещё запускается"):
        await br.health()
    await br.close()
    server.close()
    await server.wait_closed()


async def test_unconfigured_bridge_says_so_instead_of_crashing():
    br = OpenClawBridge(config=OpenClawConfig())
    assert br.available is False
    with pytest.raises(OpenClawUnavailable, match="не настроен"):
        await br.health()


# ------------------------------------- враждебная перепроверка (V2.3, §1.2 и §2)
# Условие 3 §13 считалось закрытым. Проверка была написана, тестами покрыта — и
# НИКОГДА не вызывалась из боевого пути: `_open` звал только `_handshake`.
# Тесты выше дёргали `br._check_memory(ws)` руками, поэтому были зелёными.

async def test_memory_check_actually_runs_on_connect(gateway):
    """Проверка памяти обязана срабатывать сама, без ручного вызова.

    Именно этот тест ловит «проверка есть, но её никто не зовёт»: он ходит
    ровно тем путём, которым пойдёт бой, — обычным вызовом моста.
    """
    ours = "/home/user/Obsidian/BOSSMAN"
    gw = await gateway(config={"memory": {"wiki": {"path": ours, "render": "obsidian"}}})
    br = _bridge(gw.url, vault_root=ours)
    with pytest.raises(OpenClawMemoryConflict, match="потеря данных"):
        await br.health()
    await br.close()


async def test_connect_succeeds_when_vaults_are_separate(gateway):
    """Обратная сторона: честно разведённые каталоги не должны мешать работе."""
    gw = await gateway(config={"memory": {"wiki": {"path": "/root/.openclaw/wiki"}}})
    br = _bridge(gw.url, vault_root="/home/user/Obsidian/BOSSMAN")
    assert (await br.health())["ok"] is True
    await br.close()


async def test_unreadable_config_is_refused_not_ignored(gateway):
    """Нет ответа на `config.get` — значит гарантии нет, а не «всё хорошо».

    Раньше исключение глоталось и мост шёл дальше вслепую. Цена ошибки
    несимметрична: отказ подключиться чинится за минуту, перезатёртые заметки
    владельца не чинятся ничем.
    """
    from bcc.v2.openclaw_bridge import OpenClawMemoryUnverified

    gw = await gateway(config_error="FORBIDDEN")
    br = _bridge(gw.url, vault_root="/home/user/Obsidian/BOSSMAN")
    with pytest.raises(OpenClawMemoryUnverified, match="ГАРАНТИИ НЕТ"):
        await br.health()
    # это подвид конфликта памяти — прежние обработчики продолжают работать
    assert issubclass(OpenClawMemoryUnverified, OpenClawMemoryConflict)
    await br.close()


async def test_no_vault_configured_means_nothing_to_protect(gateway):
    """Без `vault_root` защищать нечего — конфигурацию даже не спрашиваем."""
    gw = await gateway(config_error="FORBIDDEN")
    br = _bridge(gw.url)
    assert (await br.health())["ok"] is True
    assert not [m for m in gw.seen if m.get("method") == "config.get"]
    await br.close()


# ------------------------------------------------- нормализация путей хранилища

def test_memory_conflict_sees_through_symlink(tmp_path):
    """Символическая ссылка на наш каталог — тот же каталог.

    Сравнение строк этого не видит: `/tmp/их-vault` и `/home/.../BOSSMAN` —
    разные строки и один и тот же inode.
    """
    ours = tmp_path / "Obsidian" / "BOSSMAN"
    ours.mkdir(parents=True)
    link = tmp_path / "openclaw-vault"
    link.symlink_to(ours, target_is_directory=True)
    assert memory_conflict({"memory": {"wiki": {"path": str(link)}}}, str(ours))


def test_memory_conflict_normalises_tilde_relative_and_noise(tmp_path, monkeypatch):
    """`~`, относительный путь, лишние слэши и `..` — это тот же каталог."""
    home = tmp_path / "home"
    (home / "Obsidian" / "BOSSMAN").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(home)
    ours = str(home / "Obsidian" / "BOSSMAN")

    for written in ("~/Obsidian/BOSSMAN",
                    "Obsidian/BOSSMAN",
                    "./Obsidian//BOSSMAN/",
                    "Obsidian/BOSSMAN/../BOSSMAN"):
        assert memory_conflict({"memory": {"path": written}}, ours), written


def test_memory_conflict_ignores_letter_case(tmp_path):
    """На регистронезависимой ФС `/BOSSMAN` и `/bossman` — один каталог."""
    ours = tmp_path / "Obsidian" / "BOSSMAN"
    ours.mkdir(parents=True)
    theirs = str(tmp_path / "Obsidian" / "bossman")
    assert memory_conflict({"memory": {"path": theirs}}, str(ours))


def test_memory_conflict_catches_nesting_in_both_directions(tmp_path):
    """Вложенность в любую сторону — тоже два писателя в одни заметки."""
    ours = tmp_path / "vault"
    ours.mkdir()
    inner = str(ours / "OpenClaw" / "wiki")           # их каталог внутри нашего
    outer = str(tmp_path)                             # наш каталог внутри их
    assert memory_conflict({"memory": {"path": inner}}, str(ours))
    assert memory_conflict({"memory": {"path": outer}}, str(ours))
    # сосед по родителю конфликтом не является
    assert memory_conflict({"memory": {"path": str(tmp_path / "vault-other")}},
                           str(ours)) == ""


def test_memory_conflict_looks_at_the_whole_config(tmp_path):
    """Путь может лежать где угодно в их конфигурации, не только под `memory`."""
    ours = tmp_path / "vault"
    ours.mkdir()
    assert memory_conflict({"agents": {"main": {"workspace": str(ours)}}}, str(ours))


# ----------------------------------------------- ключ идемпотентности отправки

def test_idempotency_key_from_call_id_alone_is_not_retry_safe():
    """`call_id` выдаёт ПРОВАЙДЕР модели, и при повторе он другой.

    Сценарий, из-за которого человек получает сообщение дважды: процесс умер
    после `sessions.send`, но до `_save_checkpoint` (bcc/engine.py:413).
    `recover()` вернул run в очередь с прошлым checkpoint'ом, модель повторила
    вызов — и провайдер выдал НОВЫЙ `tool_calls[].id`. Ключ другой, OpenClaw
    видит новую операцию, клиенту уходит второе сообщение.
    """
    first = idempotency_key(mission_id=7, run_id=42, call_id="call_abc")
    after_crash = idempotency_key(mission_id=7, run_id=42, call_id="call_zzz")
    assert first != after_crash          # это и есть дыра, зафиксируем её честно


def test_send_key_survives_a_new_provider_call_id():
    """Ключ отправки выводится из СОДЕРЖИМОГО, поэтому переживает повтор."""
    payload = {"channel": "telegram", "contact": "@owner", "text": "смета готова"}
    first = idempotency_key(mission_id=7, run_id=42, call_id="call_abc", payload=payload)
    after_crash = idempotency_key(mission_id=7, run_id=42, call_id="call_zzz",
                                  payload=payload)
    assert first == after_crash, "ретрай движка стал бы вторым сообщением человеку"
    # порядок ключей в словаре роли не играет — сериализация каноническая
    assert first == idempotency_key(
        mission_id=7, run_id=42,
        payload={"text": "смета готова", "contact": "@owner", "channel": "telegram"})
    # другое сообщение, другой получатель, другой run — другой ключ
    assert first != idempotency_key(mission_id=7, run_id=42,
                                    payload={**payload, "text": "смета не готова"})
    assert first != idempotency_key(mission_id=7, run_id=42,
                                    payload={**payload, "contact": "@client"})
    assert first != idempotency_key(mission_id=7, run_id=43, payload=payload)
    assert first.startswith("bossman-")
