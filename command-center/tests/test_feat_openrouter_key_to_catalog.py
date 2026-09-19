"""Ключ дан → список облачных моделей загрузился. И наоборот: не загрузился → сказано, почему.

Жалоба владельца: ключ OpenRouter вставлен, а списка моделей (нужен был
z-ai/glm-5.3-flash) нет. Репродукция вскрыла две причины, и обе зафиксированы
здесь:

  * адрес провайдера без `/v1` («https://openrouter.ai/api» — ровно так он
    записан в config/gateway.example.yaml) уводил проверку ключа на
    несуществующий путь: 404 → «нет связи, повторите позже» при рабочем ключе;
  * сбой синхронизации каталога проглатывался в connect: наружу уходило
    `{"ok": true, "models": 0}` без единого слова о причине.

Транспорт подменён httpx.MockTransport и повторяет семантику openrouter.ai:
всё живёт под `/api/v1`, остальное — 404. Настоящих ключей в файле нет.
"""
import httpx
import pytest
import sqlalchemy as sa

from bcc.db import models as models_t
from bcc.v2 import openrouter_ext
from bossman_shared.privacy import execution_privacy

FAKE_KEY = "sk-or-v1-not-a-real-key"   # ci-secret-scan: allow
# Настоящий конструктор, снятый до любых подмен: повторный патч поверх
# патча иначе замыкался бы на первый транспорт и терял второй.
_ORIGINAL_INIT = openrouter_ext.OpenRouterClient.__init__

GLM = "z-ai/glm-5.3-flash"

CATALOG = {"data": [
    {"id": GLM, "name": "Z.AI: GLM 5.3 Flash", "context_length": 128000,
     "pricing": {"prompt": "0.0000002", "completion": "0.0000008"},
     "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
     "supported_parameters": ["tools", "tool_choice", "response_format"]},
    {"id": "anthropic/claude-opus-5", "name": "Anthropic: Claude Opus 5",
     "context_length": 1000000,
     "pricing": {"prompt": "0.000005", "completion": "0.000025"},
     "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]},
     "supported_parameters": ["tools"]},
]}


def openrouter_like(request: httpx.Request) -> httpx.Response:
    """Поведение настоящего openrouter.ai: API живёт под /api/v1, остальное 404."""
    path = request.url.path
    if not path.startswith("/api/v1/"):
        return httpx.Response(404, json={"error": {"message": "Not Found"}})
    if not request.headers.get("authorization", "").startswith("Bearer sk-or-"):
        return httpx.Response(401, json={"error": {"message": "No auth credentials found"}})
    if path.endswith("/key"):
        return httpx.Response(200, json={"data": {"label": "owner", "usage": 0}})
    if path.endswith("/models"):
        return httpx.Response(200, json=CATALOG)
    return httpx.Response(404, json={"error": {"message": "Not Found"}})


def _patch_transport(monkeypatch, handler=openrouter_like, seen=None):
    """Подменяется ТОЛЬКО транспорт: адрес провайдера остаётся тем, что ввёл владелец."""

    def record(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(request.url))
        return handler(request)

    def new_init(self, api_key, base_url=openrouter_ext.DEFAULT_BASE, transport=None):
        _ORIGINAL_INIT(self, api_key, base_url=base_url, transport=httpx.MockTransport(record))
    monkeypatch.setattr(openrouter_ext.OpenRouterClient, "__init__", new_init)


async def _provider(env, base_url: str, *, key: str | None = FAKE_KEY, name="openrouter"):
    body = {"name": name, "kind": "openai_compat", "base_url": base_url}
    if key is not None:
        body["api_key"] = key
    return (await env.client.post("/api/providers", json=body)).json()


# ------------------------------------------------------------------ положительный путь

async def test_key_supplied_loads_catalog_with_glm(env, monkeypatch):
    """Владелец вставил ключ — каталог загрузился, GLM 5.3 в нём есть и закрепляется."""
    seen: list[str] = []
    _patch_transport(monkeypatch, seen=seen)
    prov = await _provider(env, "https://openrouter.ai/api")     # адрес без версии

    r = await env.client.post(f"/api/openrouter/{prov['id']}/connect")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["models"] >= 2
    assert "catalog_error" not in body

    catalog = (await env.client.get(f"/api/openrouter/{prov['id']}/catalog")).json()["items"]
    card = next(c for c in catalog if c["remote_id"] == GLM)
    assert card["context_window"] == 128000 and "tools" in card["supported_parameters"]

    # запросы ушли на рабочий путь провайдера, а не на «/api/key»
    assert all("/api/v1/" in url for url in seen), seen

    # модель доезжает до активного реестра — ради этого всё и делалось
    pinned = (await env.client.post(f"/api/openrouter/{prov['id']}/pin",
                                    json={"remote_id": GLM, "alias": "glm-5.3"})).json()
    async with env.svc.db.session() as s:
        row = (await s.execute(sa.select(models_t).where(models_t.c.id == pinned["model_id"]))).first()
    assert row is not None and row._mapping["kind"] == "cloud" and row._mapping["name"] == GLM


async def test_status_reports_a_loaded_catalog(env, monkeypatch):
    _patch_transport(monkeypatch)
    prov = await _provider(env, "https://openrouter.ai/api")
    await env.client.post(f"/api/openrouter/{prov['id']}/connect")
    st = (await env.client.get(f"/api/openrouter/{prov['id']}/status")).json()
    assert st["has_key"] is True and st["catalog_models"] >= 2 and st["last_synced_at"]


def test_base_url_without_version_is_normalized():
    """Нормализация адреса — там же, где строятся пути, а не в каждом вызывающем."""
    assert openrouter_ext.normalize_base_url("https://openrouter.ai/api") == openrouter_ext.DEFAULT_BASE
    assert openrouter_ext.normalize_base_url("https://openrouter.ai/api/v1/") == openrouter_ext.DEFAULT_BASE
    assert openrouter_ext.normalize_base_url("") == openrouter_ext.DEFAULT_BASE
    # чужая версия — решение владельца, его не трогают
    assert openrouter_ext.normalize_base_url("https://proxy.local/openai/v2") == \
        "https://proxy.local/openai/v2"


# ------------------------------------------------------------------ отрицательные контроли

async def test_expired_key_is_named_401_not_an_outage(env, monkeypatch):
    """Истёкший ключ (живой прогон 20260906) обязан читаться как 401, а не как «нет связи»."""
    _patch_transport(monkeypatch)
    prov = await _provider(env, "https://openrouter.ai/api", key="expired-key")
    r = await env.client.post(f"/api/openrouter/{prov['id']}/connect")
    assert r.status_code == 400
    err = r.json()["error"]
    assert "401" in err["message"] and "openrouter.ai/keys" in err["hint"]
    assert "expired-key" not in r.text                    # ключ не эхом


async def test_key_expiring_between_check_and_sync_is_named_401(env, monkeypatch):
    """Ключ принят на /key, но отвергнут на /models: 503 обязан назвать 401."""
    def expired_on_models(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(401, json={"error": {"message": "expired"}})
        return openrouter_like(request)

    _patch_transport(monkeypatch, expired_on_models)
    prov = await _provider(env, "https://openrouter.ai/api")
    r = await env.client.post(f"/api/openrouter/{prov['id']}/sync?force=true")
    assert r.status_code == 503
    err = r.json()["error"]
    assert "401" in err["message"] and err["status_code"] == 401
    assert "openrouter.ai/keys" in err["hint"]


async def test_connect_says_why_the_catalog_is_empty(env, monkeypatch):
    """Ключ подтверждён, каталог не приехал — причина едет вместе с ответом."""
    _patch_transport(monkeypatch)
    prov = await _provider(env, "https://openrouter.ai/api")

    async def dead(self):
        raise httpx.ConnectError("network is down")

    monkeypatch.setattr(openrouter_ext.OpenRouterClient, "list_models", dead)
    r = await env.client.post(f"/api/openrouter/{prov['id']}/connect")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["models"] == 0
    assert "OpenRouter" in body["catalog_error"] and body["catalog_hint"]


async def test_wrong_address_is_reported_as_an_address_problem(env, monkeypatch):
    """404 от чужого адреса — не «повторите позже»: чинить надо base_url."""
    _patch_transport(monkeypatch)
    prov = await _provider(env, "https://openrouter.ai/wrong")
    r = await env.client.post(f"/api/openrouter/{prov['id']}/connect")
    assert r.status_code == 400
    err = r.json()["error"]
    assert "404" in err["message"] and "https://openrouter.ai/wrong/v1" in err["hint"]


async def test_privacy_refusal_is_a_policy_refusal_not_an_empty_list(env, monkeypatch):
    """Приватный контекст: каталог не запрашивается, и это сказано как отказ политики."""
    seen: list[str] = []
    _patch_transport(monkeypatch, seen=seen)
    prov = await _provider(env, "https://openrouter.ai/api")
    with execution_privacy("private"):
        r = await env.client.post(f"/api/openrouter/{prov['id']}/connect")
    assert r.status_code == 403, r.text
    assert "политика" in r.json()["error"]["message"]
    assert seen == [], "в приватном контексте наружу не ушло ни одного запроса"


async def test_catalog_stays_empty_but_explained_after_refusal(env, monkeypatch):
    """После отказа список остаётся пустым — но /sync объясняет причину, а не молчит."""
    _patch_transport(monkeypatch)
    prov = await _provider(env, "https://openrouter.ai/api")
    with execution_privacy("private"):
        r = await env.client.post(f"/api/openrouter/{prov['id']}/sync?force=true")
    assert r.status_code == 503
    assert "каталог не запрошен" in r.json()["error"]["message"]
    assert (await env.client.get(f"/api/openrouter/{prov['id']}/catalog")).json()["items"] == []


@pytest.mark.parametrize("status,expected", [(401, "401"), (402, "402"), (429, "429"),
                                             (503, "503")])
def test_every_refusal_has_its_own_words(status, expected):
    detail, hint = openrouter_ext.explain_status(status)
    assert expected in detail and hint
