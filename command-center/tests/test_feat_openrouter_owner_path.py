"""Путь владельца целиком: пустая установка → ключ → каталог → GLM 5.3 закреплена.

Аудит-11 показал три обрыва на этом пути, и здесь каждый закрыт тестом:

  OR-001 — провайдера OpenRouter нельзя было создать ничем, кроме переменной
           окружения при старте; страница с полем для ключа его не создавала.
  OR-002 — каталог отдавался куском в 200 строк по алфавиту без offset и без
           счётчиков, поэтому `z-ai/*` (конец алфавита) не существовал для UI.
  OR-003 — одна переменная под тремя именами: ключ «по документации» включал
           провайдера, но не плагин, и наоборот.

Сети нет: транспорт — httpx.MockTransport с семантикой openrouter.ai.
Настоящих ключей в файле нет.
"""
import asyncio

import httpx
import pytest
import sqlalchemy as sa

from bcc.db import providers as providers_t
from bcc.v2 import openrouter_ext, openrouter_identity as identity

from .conftest import make_settings, start_app

FAKE_KEY = "sk-or-v1-not-a-real-key"          # ci-secret-scan: allow
OTHER_KEY = "sk-or-v1-second-not-a-real-key"  # ci-secret-scan: allow
# Настоящий конструктор, снятый до любых подмен: повторный патч поверх
# патча иначе замыкался бы на первый транспорт и терял второй.
_ORIGINAL_INIT = openrouter_ext.OpenRouterClient.__init__

GLM = "z-ai/glm-5.3-flash"

# Каталог заведомо больше страницы: 262 модели, GLM — в самом конце алфавита,
# ровно как у настоящего OpenRouter (430+ моделей, z-ai/* последние).
BIG_CATALOG = {"data": (
    [{"id": f"aa-vendor/model-{i:03d}", "name": f"Model {i}", "context_length": 8192,
      "pricing": {"prompt": "0.000001", "completion": "0.000002"},
      "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
      "supported_parameters": ["tools"]} for i in range(260)]
    + [{"id": "z-ai/glm-4.6", "name": "Z.AI: GLM 4.6", "context_length": 200000,
        "pricing": {"prompt": "0.0000004", "completion": "0.0000016"},
        "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
        "supported_parameters": ["tools"]},
       {"id": GLM, "name": "Z.AI: GLM 5.3 Flash", "context_length": 1310720,
        "pricing": {"prompt": "0.000000075", "completion": "0.0000003"},
        "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]},
        "supported_parameters": ["tools", "tool_choice", "response_format"]}]
)}


def openrouter_like(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if not path.startswith("/api/v1/"):
        return httpx.Response(404, json={"error": {"message": "Not Found"}})
    if not request.headers.get("authorization", "").startswith("Bearer sk-or-"):
        return httpx.Response(401, json={"error": {"message": "No auth credentials found"}})
    if path.endswith("/key"):
        return httpx.Response(200, json={"data": {"label": "owner", "usage": 0}})
    if path.endswith("/models"):
        return httpx.Response(200, json=BIG_CATALOG)
    return httpx.Response(404, json={"error": {"message": "Not Found"}})


def patch_transport(monkeypatch, handler=openrouter_like, seen=None):

    def record(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(f"{request.method} {request.url.path}")
        return handler(request)

    def new_init(self, api_key, base_url=openrouter_ext.DEFAULT_BASE, transport=None):
        _ORIGINAL_INIT(self, api_key, base_url=base_url, transport=httpx.MockTransport(record))
    monkeypatch.setattr(openrouter_ext.OpenRouterClient, "__init__", new_init)


async def _providers(env) -> list[dict]:
    return (await env.client.get("/api/providers")).json()


# ------------------------------------------------------------------ OR-001: путь с нуля

async def test_owner_pastes_a_key_on_an_empty_install(env, monkeypatch):
    """Чистая БД без переменных окружения: один вызов — и каталог на месте."""
    seen: list[str] = []
    patch_transport(monkeypatch, seen=seen)
    assert await _providers(env) == [], "предусловие: провайдеров нет"

    r = await env.client.post("/api/openrouter/connect", json={"api_key": FAKE_KEY})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["created"] is True and body["models"] == 262

    provs = await _providers(env)
    assert len(provs) == 1 and provs[0]["id"] == body["provider_id"]
    # ключ наружу не отдаётся ни в ответе connect, ни в списке провайдеров
    assert FAKE_KEY not in r.text
    assert FAKE_KEY not in (provs[0].get("api_key_masked") or "")
    # подключение не запускает платных вызовов: только /key и /models
    assert all(not p.endswith("/chat/completions") for p in seen), seen


async def test_repeated_connect_updates_the_same_provider(env, monkeypatch):
    """Повторный Connect — это смена ключа, а не второй поставщик."""
    patch_transport(monkeypatch)
    first = (await env.client.post("/api/openrouter/connect", json={"api_key": FAKE_KEY})).json()
    second = (await env.client.post("/api/openrouter/connect", json={"api_key": OTHER_KEY})).json()
    assert second["provider_id"] == first["provider_id"] and second["created"] is False
    assert len(await _providers(env)) == 1
    async with env.svc.db.session() as s:
        row = (await s.execute(sa.select(providers_t).where(
            providers_t.c.id == first["provider_id"]))).first()
    assert env.svc.vault.decrypt(row._mapping["api_key_enc"]) == OTHER_KEY


async def test_double_click_creates_exactly_one_provider(env, monkeypatch):
    """Два одновременных Connect (двойной клик) — один поставщик, не два."""
    patch_transport(monkeypatch)
    both = await asyncio.gather(
        env.client.post("/api/openrouter/connect", json={"api_key": FAKE_KEY}),
        env.client.post("/api/openrouter/connect", json={"api_key": FAKE_KEY}))
    assert [r.status_code for r in both] == [200, 200]
    ids = {r.json()["provider_id"] for r in both}
    assert len(ids) == 1 and len(await _providers(env)) == 1


async def test_empty_key_is_refused_before_anything_is_created(env, monkeypatch):
    seen: list[str] = []
    patch_transport(monkeypatch, seen=seen)
    r = await env.client.post("/api/openrouter/connect", json={"api_key": "   "})
    assert r.status_code == 422
    assert await _providers(env) == [] and seen == []


@pytest.mark.parametrize("status,expect_code,expect_text", [
    (401, 400, "401"), (403, 400, "403"), (503, 502, "503")])
async def test_provider_refusals_are_readable_and_recoverable(env, monkeypatch, status,
                                                              expect_code, expect_text):
    """401/403/недоступность — разные сообщения; после ошибки Connect чинится ключом."""
    def refusing(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/key"):
            return httpx.Response(status, json={"error": {"message": "nope"}})
        return openrouter_like(request)

    patch_transport(monkeypatch, refusing)
    bad = await env.client.post("/api/openrouter/connect", json={"api_key": FAKE_KEY})
    assert bad.status_code == expect_code
    assert expect_text in bad.json()["error"]["message"]
    assert FAKE_KEY not in bad.text

    patch_transport(monkeypatch)                   # владелец исправил ключ
    good = await env.client.post("/api/openrouter/connect", json={"api_key": OTHER_KEY})
    assert good.status_code == 200 and good.json()["models"] == 262
    assert len(await _providers(env)) == 1, "неудачная попытка не оставила мусорного поставщика"


async def test_timeout_is_reported_as_unavailable_not_as_a_bad_key(env, monkeypatch):
    def hanging(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    patch_transport(monkeypatch, hanging)
    r = await env.client.post("/api/openrouter/connect", json={"api_key": FAKE_KEY})
    assert r.status_code == 502 and "OpenRouter" in r.json()["error"]["message"]


async def test_catalog_failure_after_a_valid_key_is_explained(env, monkeypatch):
    """Ключ принят, каталог не приехал: подключение состоялось, причина названа."""
    def key_ok_models_down(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            raise httpx.ConnectError("network is down")
        return openrouter_like(request)

    patch_transport(monkeypatch, key_ok_models_down)
    r = await env.client.post("/api/openrouter/connect", json={"api_key": FAKE_KEY})
    assert r.status_code == 200
    body = r.json()
    assert body["models"] == 0 and body["catalog_error"] and body["catalog_hint"]


# ------------------------------------------------------------------ OR-002: каталог целиком

async def _connected(env, monkeypatch) -> int:
    patch_transport(monkeypatch)
    body = (await env.client.post("/api/openrouter/connect", json={"api_key": FAKE_KEY})).json()
    return int(body["provider_id"])


async def test_catalog_page_tells_the_truth_about_the_rest(env, monkeypatch):
    pid = await _connected(env, monkeypatch)
    page = (await env.client.get(f"/api/openrouter/{pid}/catalog?limit=100")).json()
    assert page["total"] == 262 and page["returned"] == 100 and page["has_more"] is True
    assert len(page["items"]) == 100
    assert GLM not in {c["remote_id"] for c in page["items"]}, "предусловие: GLM в хвосте"


async def test_paging_reaches_the_end_of_the_alphabet(env, monkeypatch):
    """Ради этого всё и делалось: z-ai/* достижимы листанием, а не только поиском."""
    pid = await _connected(env, monkeypatch)
    seen, offset = set(), 0
    while True:
        page = (await env.client.get(
            f"/api/openrouter/{pid}/catalog?limit=100&offset={offset}")).json()
        seen |= {c["remote_id"] for c in page["items"]}
        offset += page["returned"]
        if not page["has_more"]:
            break
        assert page["returned"] > 0, "страница без строк с has_more — бесконечный цикл"
    assert GLM in seen and len(seen) == 262


async def test_search_covers_the_whole_catalog_and_counts_honestly(env, monkeypatch):
    pid = await _connected(env, monkeypatch)
    found = (await env.client.get(f"/api/openrouter/{pid}/catalog?q=glm&limit=100")).json()
    assert found["total"] == 2 and found["has_more"] is False
    assert GLM in {c["remote_id"] for c in found["items"]}
    assert found["query"] == "glm"


async def test_empty_search_result_is_not_an_error(env, monkeypatch):
    """«Ничего не нашлось» и «ручка упала» — разные ответы, а не одинаковая пустота."""
    pid = await _connected(env, monkeypatch)
    r = await env.client.get(f"/api/openrouter/{pid}/catalog?q=такого-нет")
    assert r.status_code == 200
    assert r.json() == {"items": [], "total": 0, "returned": 0, "offset": 0,
                        "limit": 50, "has_more": False, "query": "такого-нет"}
    missing = await env.client.get("/api/openrouter/9999/catalog")
    assert missing.status_code == 200 and missing.json()["total"] == 0


async def test_page_size_is_capped_but_offset_still_moves(env, monkeypatch):
    """Огромный limit не «чинит» проблему и не молчит: страница остаётся страницей."""
    pid = await _connected(env, monkeypatch)
    page = (await env.client.get(f"/api/openrouter/{pid}/catalog?limit=5000")).json()
    assert page["returned"] == 200 and page["limit"] == 200 and page["has_more"] is True
    tail = (await env.client.get(f"/api/openrouter/{pid}/catalog?limit=200&offset=200")).json()
    assert tail["returned"] == 62 and tail["has_more"] is False
    assert GLM in {c["remote_id"] for c in tail["items"]}


async def test_found_model_is_pinnable_and_priced(env, monkeypatch):
    """Найденная GLM закрепляется в реестре с известной ценой — иначе облако fail-closed."""
    pid = await _connected(env, monkeypatch)
    pinned = (await env.client.post(f"/api/openrouter/{pid}/pin",
                                    json={"remote_id": GLM, "alias": "or-z-ai-glm-5.3-flash"})).json()
    models = (await env.client.get("/api/models")).json()
    row = next(m for m in models if m["id"] == pinned["model_id"])
    assert row["name"] == GLM and row["kind"] == "cloud" and row["pricing_known"] is True


# ------------------------------------------------------------------ OR-003: один кред

@pytest.mark.parametrize("env_name", [identity.ENV_API_KEY, *identity.LEGACY_ENV_API_KEYS])
async def test_both_env_names_bootstrap_the_provider(tmp_path, monkeypatch, env_name):
    """Старое имя из README и каноническое имя — оба поднимают провайдера."""
    for name in (identity.ENV_API_KEY, *identity.LEGACY_ENV_API_KEYS):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(env_name, FAKE_KEY)
    settings = make_settings(tmp_path)
    app, svc = await start_app(settings, start_workers=False)
    try:
        async with svc.db.session() as s:
            rows = (await s.execute(sa.select(providers_t))).fetchall()
        assert len(rows) == 1, f"{env_name} не создал провайдера"
        assert svc.vault.decrypt(dict(rows[0]._mapping)["api_key_enc"]) == FAKE_KEY
        # указатель проставлен: следующий Connect найдёт того же провайдера
        row = await identity.provider_row(svc.db, svc.vault)
        assert row is not None and int(row["id"]) == int(dict(rows[0]._mapping)["id"])
    finally:
        await svc.stop()


def test_conflicting_env_names_are_reported_not_guessed(monkeypatch):
    monkeypatch.setenv(identity.ENV_API_KEY, FAKE_KEY)
    monkeypatch.setenv(identity.LEGACY_ENV_API_KEYS[0], OTHER_KEY)
    cred = identity.env_credential()
    assert cred.key == FAKE_KEY                       # каноническое имя сильнее
    assert cred.conflicts and identity.ENV_API_KEY in cred.conflict_message
    assert identity.LEGACY_ENV_API_KEYS[0] in cred.conflict_message


def test_matching_values_under_two_names_are_not_a_conflict(monkeypatch):
    monkeypatch.setenv(identity.ENV_API_KEY, FAKE_KEY)
    monkeypatch.setenv(identity.LEGACY_ENV_API_KEYS[0], FAKE_KEY)
    assert identity.env_credential().conflicts == []


async def test_plugin_credential_comes_from_the_vault(env, monkeypatch):
    """Ключ, введённый в интерфейсе, включает и плагин: он больше не смотрит только в env."""
    from bcc.features import plugins as P
    monkeypatch.delenv(identity.ENV_API_KEY, raising=False)
    for legacy in identity.LEGACY_ENV_API_KEYS:
        monkeypatch.delenv(legacy, raising=False)
    assert await P.resolve_cred("OPENROUTER_API_KEY", env.svc) is None

    patch_transport(monkeypatch)
    await env.client.post("/api/openrouter/connect", json={"api_key": FAKE_KEY})
    assert await P.resolve_cred("OPENROUTER_API_KEY", env.svc) == FAKE_KEY
    rows = (await env.client.get("/api/plugins")).json()
    openrouter = next(p for p in rows["plugins"] if p["plugin"] == "openrouter")
    assert openrouter["credential"] == "configured"
    assert FAKE_KEY not in (await env.client.get("/api/plugins")).text


async def test_provider_is_not_chosen_by_a_url_substring(env, monkeypatch):
    """Чужой адрес со словом «openrouter» внутри не становится нашим провайдером."""
    impostor = (await env.client.post("/api/providers", json={
        "name": "прокси", "kind": "openai_compat",
        "base_url": "https://openrouter.ai.evil.example/api/v1",
        "api_key": OTHER_KEY})).json()
    row = await identity.provider_row(env.svc.db, env.svc.vault)
    assert row is None

    patch_transport(monkeypatch)
    created = (await env.client.post("/api/openrouter/connect", json={"api_key": FAKE_KEY})).json()
    assert created["provider_id"] != impostor["id"]
    mine = await identity.provider_row(env.svc.db, env.svc.vault)
    assert mine is not None and int(mine["id"]) == created["provider_id"]


async def test_existing_provider_is_adopted_once_by_exact_address(env, monkeypatch):
    """Установка, где провайдер уже создан старым bootstrap'ом, не получает второго."""
    existing = (await env.client.post("/api/providers", json={
        "name": "OpenRouter (env)", "kind": "openai_compat",
        "base_url": openrouter_ext.DEFAULT_BASE, "api_key": OTHER_KEY})).json()
    patch_transport(monkeypatch)
    r = (await env.client.post("/api/openrouter/connect", json={"api_key": FAKE_KEY})).json()
    assert r["provider_id"] == existing["id"] and r["created"] is False
    assert len(await _providers(env)) == 1
