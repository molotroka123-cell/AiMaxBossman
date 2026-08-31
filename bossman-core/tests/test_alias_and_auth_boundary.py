"""Аудит 2026-08-29: консистентность alias'ов и граница аутентификации ядра.

Часть 1 — alias-мисматч (находка про 'bossman-code' vs 'bossman-coder'):
  - все агентные модели разрешаются в алиасы прилагаемого конфига Gateway;
  - агент с неизвестным алиасом падает НА СТАРТЕ (ValueError с именем агента),
    а не 404 RouteNotFound на первом вызове модели в Gateway-режиме.

Часть 2 — граница auth (находка «zero authentication»):
  - дефолтный bind ядра — loopback (127.0.0.1), не 0.0.0.0;
  - Gateway: unauthenticated loopback-проход выключен по умолчанию и не
    действует для внешних адресов даже при явном включении;
  - /telegram/webhook: без/с неверным X-Telegram-Bot-Api-Secret-Token — отказ,
    решающих вызовов approvals.decide нет; с верным — работает.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException

from bossman import approvals
from bossman.agents import auto_gateway_config, load_all, validate_agent_models
from bossman.config import ROOT, Settings, settings
from bossman.gateway.auth import AuthManager
from bossman.gateway.config import load_gateway_config

AGENTS_DIR = Path(__file__).parent.parent / "agents"
GATEWAY_EXAMPLE = ROOT / "config" / "gateway.example.yaml"


def _example_gateway_config():
    return load_gateway_config(GATEWAY_EXAMPLE)


def test_gateway_default_ollama_url_honors_ollama_host(monkeypatch, tmp_path):
    """A moved local Ollama daemon must not leave Gateway on a stale port."""
    cfg_path = tmp_path / "gateway.yaml"
    cfg_path.write_text("backends:\n  ollama:\n    base_url: http://127.0.0.1:11434\n",
                        encoding="utf-8")
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:11435")
    assert load_gateway_config(cfg_path).backends["ollama"].base_url == "http://127.0.0.1:11435"

    cfg_path.write_text("backends:\n  ollama:\n    base_url: http://127.0.0.1:19000/v1\n",
                        encoding="utf-8")
    assert load_gateway_config(cfg_path).backends["ollama"].base_url == "http://127.0.0.1:19000/v1"


# ---------- Часть 1: alias-консистентность ----------

def test_all_bundled_agents_resolve_against_gateway_example():
    """Startup-валидация зелёная: каждая модель агентов есть среди алиасов
    конфига Gateway (bossman-coder, а не bossman-code — каноничное имя)."""
    cfg = _example_gateway_config()
    agents = load_all(AGENTS_DIR, gateway_config=cfg)   # не должно бросить
    assert agents, "агенты должны загрузиться"
    for spec in agents.values():
        assert spec.model in cfg.aliases, f"{spec.name} -> {spec.model}"


def test_canonical_alias_is_bossman_coder_in_example_config():
    text = GATEWAY_EXAMPLE.read_text(encoding="utf-8")
    assert "bossman-code:" not in text          # старое не-каноничное имя исчезло
    assert "bossman-coder:" in text             # алиас Gateway совпадает с агентом
    assert "bossman-code," not in text and "bossman-code]" not in text  # allowlist клиента


def test_unknown_alias_fails_validation_with_agent_name(tmp_path):
    """Агент с несуществующим алиасом -> ValueError С ИМЕНЕМ агента и модели,
    а не молчаливый 404 при первом вызове модели."""
    bad = tmp_path / "badagent"
    bad.mkdir()
    (bad / "agent.yaml").write_text(
        "name: badagent\n"
        "title: Bad\n"
        "model: bossman-nope\n"
        "cloud_policy: never\n"
        "tools: [fs.read]\n",
        encoding="utf-8")
    cfg = _example_gateway_config()
    with pytest.raises(ValueError) as exc:
        load_all(tmp_path, gateway_config=cfg)
    msg = str(exc.value)
    assert "badagent" in msg and "bossman-nope" in msg
    # имена известных алиасов подсказаны — мисматч чинится за один взгляд
    assert "bossman-coder" in msg


def test_validate_agent_models_noop_without_gateway_config():
    """Без Gateway-конфига валидация не применяется (не-Gateway режимы)."""
    agents = load_all(AGENTS_DIR, gateway_config=None)
    assert set(agents) >= {"analyst", "coder"}


def test_auto_gateway_config_off_without_gateway_url(monkeypatch):
    monkeypatch.setattr(settings, "gateway_url", "")
    assert auto_gateway_config() is None


async def test_llm_gateway_client_hook_validates_before_first_call(monkeypatch, tmp_path):
    """Хук в llm._gateway_client: при первом построении Gateway-клиента агенты
    валидируются против BOSSMAN_GATEWAY_CONFIG; мисматч -> ValueError, и клиент
    НЕ кэшируется (повторный вызов снова падает, а не уходит в сеть)."""
    import bossman.llm as llm

    gw_conf = tmp_path / "gw.yaml"
    gw_conf.write_text(
        "backends:\n"
        "  ollama: {base_url: 'http://127.0.0.1:11434'}\n"
        "aliases:\n"
        "  bossman-fast: {targets: [{backend: ollama, model: m, priority: 10}]}\n",
        encoding="utf-8")
    monkeypatch.setattr(llm, "_gateway", None)
    monkeypatch.setattr(settings, "gateway_url", "http://gw/v1")
    monkeypatch.setenv("BOSSMAN_GATEWAY_CONFIG", str(gw_conf))

    with pytest.raises(ValueError) as exc:
        llm._gateway_client()
    assert "coder" in str(exc.value)
    assert llm._gateway is None          # не закэширован — не «переждёшь»
    with pytest.raises(ValueError):
        llm._gateway_client()            # и повторно падает так же

    # консистентный конфиг -> клиент строится и кэшируется
    gw_conf.write_text(
        "backends:\n"
        "  ollama: {base_url: 'http://127.0.0.1:11434'}\n"
        "aliases:\n"
        "  bossman-fast: {targets: [{backend: ollama, model: m, priority: 10}]}\n"
        "  bossman-smart: {targets: [{backend: ollama, model: m, priority: 10}]}\n"
        "  bossman-coder: {targets: [{backend: ollama, model: m, priority: 10}]}\n"
        "  bossman-vision: {targets: [{backend: ollama, model: m, priority: 10}]}\n",
        encoding="utf-8")
    try:
        client = llm._gateway_client()
        assert client.base_url == "http://gw/v1"
        assert llm._gateway is client
    finally:
        await llm.aclose_gateway()


# ---------- Часть 2: граница auth ядра ----------

def test_core_api_default_bind_is_loopback(monkeypatch):
    """Граница «loopback-only» (аудит: bind 0.0.0.0 делал API доступным любому
    сетевому пиру). Дефолт обязан оставаться 127.0.0.1: вся мутация задач/
    approvals/cloud_policy наружу не слушается; удалённые каналы — через
    аутентифицированные /remote/* и секретный /telegram/webhook."""
    monkeypatch.delenv("CORE_HOST", raising=False)
    assert Settings().host == "127.0.0.1"


class _FakeRequest:
    def __init__(self, host: str, authorization: str = ""):
        self.headers = {"authorization": authorization} if authorization else {}
        self.client = type("C", (), {"host": host})()


def test_gateway_unauthenticated_loopback_off_by_default():
    """Loopback-проход без токена выключен в прилагаемом конфиге: даже 127.0.0.1
    обязан предъявить bearer-ключ."""
    cfg = load_gateway_config(GATEWAY_EXAMPLE)
    assert cfg.allow_unauthenticated_loopback is False
    am = AuthManager(cfg)
    with pytest.raises(HTTPException) as exc:
        am.authenticate(_FakeRequest("127.0.0.1"))
    assert exc.value.status_code == 401


def test_gateway_loopback_opt_in_never_applies_to_remote_hosts():
    """Даже при явном allow_unauthenticated_loopback=true псевдо-клиент даётся
    ТОЛЬКО loopback-адресу; внешний хост всё равно требует ключ."""
    cfg = load_gateway_config(GATEWAY_EXAMPLE)
    cfg.allow_unauthenticated_loopback = True
    am = AuthManager(cfg)
    with pytest.raises(HTTPException) as exc:
        am.authenticate(_FakeRequest("192.0.2.55"))
    assert exc.value.status_code == 401
    pseudo = am.authenticate(_FakeRequest("127.0.0.1"))
    assert pseudo.name == "loopback"


# ---------- Часть 2: /telegram/webhook — секрет-токен ----------

def _api_app():
    from bossman.api import app
    return app


def _update(decision: str, sid: int = 7) -> dict:
    return {"callback_query": {"data": f"{decision}:{sid}", "from": {"username": "tester"}}}


@pytest.fixture
def webhook_env(monkeypatch):
    """Секрет задан, approvals.decide подменён (без БД), приложение — реальное."""
    monkeypatch.setattr(settings, "telegram_webhook_secret", "s3cret-token")
    seen: dict = {}

    async def fake_decide(approval_id, approve, decided_by):
        seen["decision"] = (approval_id, approve, decided_by)
        return {"id": approval_id, "status": "approved" if approve else "rejected"}

    monkeypatch.setattr(approvals, "decide", fake_decide)
    return seen


async def test_webhook_rejects_missing_and_wrong_secret(webhook_env):
    seen = webhook_env
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_api_app()),
                                 base_url="http://t") as c:
        r_missing = await c.post("/telegram/webhook", json=_update("approve"))
        r_wrong = await c.post("/telegram/webhook", json=_update("approve"),
                               headers={"X-Telegram-Bot-Api-Secret-Token": "evil"})
    assert r_missing.status_code in (401, 403)
    assert r_wrong.status_code in (401, 403)
    assert "decision" not in seen       # approvals.decide НЕ вызван ни разу


async def test_webhook_secret_is_not_timing_leaky_placeholder(webhook_env):
    """Сверка секрета идёт в постоянном времени (hmac.compare_digest).

    После подключения cost-governor+notifications pack сам разбор вебхука
    (секрет, чат, TTL, single-use) живёт в notifications.telegram_transport —
    api.telegram_webhook лишь передаёт заголовок туда (integration/API_TELEGRAM_HOOK.md).
    Проверяем компонент, где сравнение реально происходит."""
    import hmac as _hmac
    import inspect
    from bossman.notifications.telegram_transport import TelegramTransport
    src = inspect.getsource(TelegramTransport.handle_webhook)
    assert "compare_digest" in src
    assert _hmac.compare_digest("a", "a") is True   # sanity: модуль hmac жив


async def test_webhook_accepts_correct_secret(webhook_env, monkeypatch):
    """Опаковый b:<token>-callback (см. TELEGRAM_SECURITY.md пакета): старый
    формат reject:<id> хардкодом отклоняется — это осознанное ужесточение,
    покрытое test_telegram_webhook_security.py (legacy callback rejected)."""
    from bossman.notifications.models import ActionKind, NotificationAction
    from bossman.notifications.runtime import STORE as notif_store

    seen = webhook_env
    monkeypatch.setattr(settings, "telegram_chat_id", "555")
    action = NotificationAction(ActionKind.DENY, "approval", "9", "❌", "approval:9")
    token = notif_store.create_callback(action, "555")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_api_app()),
                                 base_url="http://t") as c:
        r = await c.post("/telegram/webhook",
                         json={"callback_query": {"id": "cb1", "data": f"b:{token}",
                                                   "message": {"chat": {"id": 555}}}},
                         headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret-token"})
    assert r.status_code == 200
    assert seen["decision"] == (9, False, "tg:chat:555")


async def test_webhook_fails_closed_without_configured_secret(monkeypatch):
    """Пустой секрет в настройках => вебхук запрещён целиком (fail-closed)."""
    monkeypatch.setattr(settings, "telegram_webhook_secret", "")
    seen: dict = {}

    async def fake_decide(approval_id, approve, decided_by):
        seen["hit"] = True
        return {"id": approval_id}

    monkeypatch.setattr(approvals, "decide", fake_decide)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_api_app()),
                                 base_url="http://t") as c:
        r = await c.post("/telegram/webhook", json=_update("approve"),
                         headers={"X-Telegram-Bot-Api-Secret-Token": "anything"})
    assert r.status_code in (401, 403)
    assert "hit" not in seen
