"""AF-03: ключ OpenRouter обязан попадать в OpenRouter, а не в первого попавшегося.

Дефект, найденный аудитом ASTRA_SKILLS_FREEZE_20260907 и подтверждённый здесь
воспроизведением:

  * `ui/pages/openrouter.js` показывал панель ключа только при `!providers.length`.
    Если в базе уже лежал НЕ-OpenRouter поставщик (например, одна Ollama),
    выбор падал на `providers[0]`, и панель писала ключ в ЭТУ строку;
  * `bcc/features/openrouter.py::_openrouter_provider` проверял, что строка
    существует, а не что она принадлежит OpenRouter, и `PATCH /key` обновлял
    ключ именно этой строки.

«Пустая база» и «база с чужим поставщиком» — разные пути. Второй здесь закрыт
с двух сторон: сервер отказывается писать ключ, пока каноническая identity не
установлена или указывает на другого, а страница вообще не выбирает поставщика
сама — она спрашивает сервер и связывается ровно с тем id, который он назвал.

Главный инвариант всех отрицательных случаев: ключ и адрес ПОСТОРОННЕГО
поставщика остаются неизменными ПОБАЙТНО. Сравнивается не только расшифрованное
значение, но и сам шифротекст `api_key_enc`: перезапись тем же значением дала бы
другой шифротекст Fernet и была бы поймана.

Сети нет: транспорт — httpx.MockTransport с семантикой openrouter.ai. Все ключи
в файле выдуманные, ни одного живого вызова.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa

from bcc.db import providers as providers_t
from bcc.v2 import openrouter_ext, openrouter_identity as identity
from bcc.v2.tables import provider_catalog_models as catalog_t
from bossman_shared.privacy import execution_privacy

# ---------------------------------------------------------------- выдуманные ключи
OR_KEY = "sk-or-v1-isolation-not-a-real-key"        # ci-secret-scan: allow
OR_KEY_2 = "sk-or-v1-isolation-second-not-real"     # ci-secret-scan: allow
OLLAMA_KEY = "ollama-local-not-a-real-key"          # ci-secret-scan: allow
CLOUD_KEY = "tg-cloud-not-a-real-key"               # ci-secret-scan: allow
IMPOSTOR_KEY = "proxy-not-a-real-key"               # ci-secret-scan: allow

GLM = "z-ai/glm-5.3-flash"                          # живёт в самом хвосте алфавита

# Настоящий конструктор, снятый до любых подмен.
_ORIGINAL_INIT = openrouter_ext.OpenRouterClient.__init__

UI_PAGE = Path(__file__).resolve().parents[1] / "ui" / "pages" / "openrouter.js"

# Каталог заведомо больше страницы: 262 модели, GLM в конце — как у настоящего
# OpenRouter (430+ моделей, `z-ai/*` последние).
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
    """Семантика openrouter.ai: API под /api/v1, ключ обязателен, остальное 404."""
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
    """Подменяется ТОЛЬКО транспорт: адрес поставщика остаётся тем, что в строке."""

    def record(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(request.url))
        return handler(request)

    def new_init(self, api_key, base_url=openrouter_ext.DEFAULT_BASE, transport=None):
        _ORIGINAL_INIT(self, api_key, base_url=base_url, transport=httpx.MockTransport(record))
    monkeypatch.setattr(openrouter_ext.OpenRouterClient, "__init__", new_init)


# ---------------------------------------------------------------- посторонние поставщики

FOREIGN = {
    "ollama": {"name": "Ollama (локальная)", "kind": "openai_compat",
               "base_url": "http://127.0.0.1:11434/v1", "api_key": OLLAMA_KEY},
    "cloud": {"name": "Together", "kind": "openai_compat",
              "base_url": "https://api.together.xyz/v1", "api_key": CLOUD_KEY},
    "lmstudio": {"name": "LM Studio", "kind": "openai_compat",
                 "base_url": "http://127.0.0.1:1234/v1"},
    # Ровно тот случай, ради которого identity не ищется подстрокой: в адресе
    # есть слово openrouter, но это чужой прокси.
    "impostor": {"name": "openrouter-proxy", "kind": "openai_compat",
                 "base_url": "https://openrouter.ai.evil.example/api/v1",
                 "api_key": IMPOSTOR_KEY},
}


async def seed(env, *names: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for name in names:
        r = await env.client.post("/api/providers", json=FOREIGN[name])
        assert r.status_code < 400, r.text
        out[name] = int(r.json()["id"])
    return out


async def fingerprint(env, provider_id: int) -> dict:
    """Побайтный снимок строки: шифротекст ключа, расшифровка, адрес, имя, вид.

    Шифротекст здесь важнее расшифровки: перезапись тем же ключом поменяла бы
    его (Fernet недетерминирован), и «ничего не изменилось» перестало бы врать.
    """
    async with env.svc.db.session() as s:
        row = (await s.execute(sa.select(providers_t).where(
            providers_t.c.id == provider_id))).first()
    assert row is not None, f"провайдер {provider_id} исчез"
    m = row._mapping
    return {"api_key_enc": m["api_key_enc"], "base_url": m["base_url"],
            "name": m["name"], "kind": m["kind"],
            "plain": env.svc.vault.decrypt(m["api_key_enc"])}


async def fingerprints(env, ids: dict[str, int]) -> dict[str, dict]:
    return {k: await fingerprint(env, v) for k, v in ids.items()}


async def assert_untouched(env, ids: dict[str, int], before: dict[str, dict]) -> None:
    after = await fingerprints(env, ids)
    for key in before:
        assert after[key] == before[key], (
            f"посторонний поставщик «{key}» изменился: "
            f"{before[key]} -> {after[key]}")


async def provider_ids(env) -> set[int]:
    return {int(p["id"]) for p in (await env.client.get("/api/providers")).json()}


async def ident(env) -> dict:
    r = await env.client.get("/api/openrouter/provider")
    assert r.status_code == 200, r.text
    return r.json()


async def assert_no_key_leak(env, *keys: str) -> None:
    """Ключ не возвращается наружу и не попадает в события."""
    body = (await env.client.get("/api/providers")).text
    ident_body = (await env.client.get("/api/openrouter/provider")).text
    events = str(await env.svc.bus.recent(200))
    for key in keys:
        assert key not in body, "ключ утёк в /api/providers"
        assert key not in ident_body, "ключ утёк в /api/openrouter/provider"
        assert key not in events, "ключ утёк в события"


# ================================================================== 1. пустая база

async def test_empty_database_connect_creates_the_openrouter_provider(env, monkeypatch):
    """Контроль исходного (уже работавшего) пути: на пустой базе Connect создаёт своего."""
    patch_transport(monkeypatch)
    assert await provider_ids(env) == set()
    before = await ident(env)
    assert before == {"connected": False, "provider_id": None, "name": None,
                      "base_url": None, "has_key": False, "providers_total": 0}

    r = await env.client.post("/api/openrouter/connect", json={"api_key": OR_KEY})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] is True and body["models"] == 262

    now = await ident(env)
    assert now["connected"] is True and now["provider_id"] == body["provider_id"]
    assert now["has_key"] is True and now["providers_total"] == 1
    assert "api_key" not in now and OR_KEY not in json.dumps(now)
    await assert_no_key_leak(env, OR_KEY)


# ================================================================== 2. только Ollama

async def test_key_write_into_an_ollama_only_database_is_refused(env, monkeypatch):
    """ВОСПРОИЗВЕДЕНИЕ AF-03. Один поставщик, и он не наш: ключ туда не пишется.

    На старом коде `PATCH /api/openrouter/{ollama}/key` отвечал 200 и клал ключ
    OpenRouter в строку Ollama — ровно то, что описал аудит.
    """
    patch_transport(monkeypatch)
    ids = await seed(env, "ollama")
    before = await fingerprints(env, ids)

    # Сначала — сам дефект, и НИ ОДНОЙ новой ручки до него: на старом коде эта
    # запись отвечала 200 и подменяла ключ Ollama. Строка проверяется раньше
    # кода ответа, чтобы падение показывало именно испорченную строку.
    r = await env.client.patch(f"/api/openrouter/{ids['ollama']}/key",
                               json={"api_key": OR_KEY})
    after = await fingerprint(env, ids["ollama"])
    assert after["plain"] == OLLAMA_KEY, (
        f"ключ OpenRouter записан в строку Ollama: {after['plain']!r}")
    assert after["api_key_enc"] == before["ollama"]["api_key_enc"], "шифротекст чужого ключа переписан"
    assert after["base_url"] == before["ollama"]["base_url"], "адрес чужого поставщика изменён"
    assert r.status_code == 409, r.text
    assert OR_KEY not in r.text, "ключ не эхом в отказе"

    await assert_untouched(env, ids, before)
    assert (await ident(env))["connected"] is False, "Ollama не должна считаться OpenRouter"
    await assert_no_key_leak(env, OR_KEY)


async def test_connect_with_only_ollama_makes_its_own_provider(env, monkeypatch):
    """Тот же вход, но законной ручкой: заводится ОТДЕЛЬНЫЙ поставщик."""
    patch_transport(monkeypatch)
    ids = await seed(env, "ollama")
    before = await fingerprints(env, ids)

    r = await env.client.post("/api/openrouter/connect", json={"api_key": OR_KEY})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] is True
    assert body["provider_id"] != ids["ollama"], "ключ уехал в чужого поставщика"
    assert body["models"] == 262

    await assert_untouched(env, ids, before)
    assert len(await provider_ids(env)) == 2
    # каталог тоже привязан к своему поставщику, а не к чужому
    ours = (await env.client.get(f"/api/openrouter/{body['provider_id']}/catalog")).json()
    assert ours["total"] == 262
    async with env.svc.db.session() as s:
        foreign_rows = int((await s.execute(
            sa.select(sa.func.count()).select_from(catalog_t)
            .where(catalog_t.c.provider_id == ids["ollama"]))).scalar_one() or 0)
    assert foreign_rows == 0, "каталог OpenRouter залился в чужого поставщика"
    await assert_no_key_leak(env, OR_KEY)


# ================================================================== 3. другой облачный

async def test_key_write_into_another_cloud_provider_is_refused(env, monkeypatch):
    patch_transport(monkeypatch)
    ids = await seed(env, "cloud")
    before = await fingerprints(env, ids)

    r = await env.client.patch(f"/api/openrouter/{ids['cloud']}/key",
                               json={"api_key": OR_KEY})
    assert r.status_code == 409, r.text
    await assert_untouched(env, ids, before)

    # и законный путь не трогает чужое облако
    made = (await env.client.post("/api/openrouter/connect",
                                  json={"api_key": OR_KEY})).json()
    assert made["provider_id"] != ids["cloud"]
    await assert_untouched(env, ids, before)
    await assert_no_key_leak(env, OR_KEY, CLOUD_KEY)


# ================================================================== 4. смешанная база

async def test_mixed_database_without_openrouter_offers_connect_and_touches_nobody(env, monkeypatch):
    """Четыре чужих поставщика, включая «openrouter-proxy» в адресе. Наш — ни один."""
    patch_transport(monkeypatch)
    ids = await seed(env, "ollama", "cloud", "lmstudio", "impostor")
    before = await fingerprints(env, ids)

    # Сперва запись в каждого чужого — и только потом взгляд страницы: падать
    # этот тест на старом коде обязан на испорченной строке, а не на 404 новой ручки.
    for name, pid in ids.items():
        r = await env.client.patch(f"/api/openrouter/{pid}/key", json={"api_key": OR_KEY})
        await assert_untouched(env, ids, before)
        assert r.status_code == 409, f"{name}: {r.text}"

    state = await ident(env)
    assert state["connected"] is False and state["providers_total"] == 4, state

    made = (await env.client.post("/api/openrouter/connect",
                                  json={"api_key": OR_KEY})).json()
    assert made["created"] is True and made["provider_id"] not in set(ids.values())
    await assert_untouched(env, ids, before)

    after = await ident(env)
    assert after["connected"] is True and after["providers_total"] == 5
    assert after["provider_id"] == made["provider_id"]
    await assert_no_key_leak(env, OR_KEY, OLLAMA_KEY, CLOUD_KEY, IMPOSTOR_KEY)


async def test_foreign_provider_cannot_be_operated_through_the_openrouter_routes(env, monkeypatch):
    """Когда наш поставщик известен, чужой id получает отказ на всех ручках."""
    patch_transport(monkeypatch)
    ids = await seed(env, "ollama", "impostor")
    before = await fingerprints(env, ids)
    made = (await env.client.post("/api/openrouter/connect",
                                  json={"api_key": OR_KEY})).json()
    mine = made["provider_id"]

    for name, pid in ids.items():
        assert (await env.client.patch(f"/api/openrouter/{pid}/key",
                                       json={"api_key": OR_KEY})).status_code == 409, name
        assert (await env.client.post(f"/api/openrouter/{pid}/connect")).status_code == 409, name
        assert (await env.client.post(f"/api/openrouter/{pid}/sync?force=true")).status_code == 409, name
        assert (await env.client.get(f"/api/openrouter/{pid}/status")).status_code == 409, name
        assert (await env.client.post(f"/api/openrouter/{pid}/pin",
                                      json={"remote_id": GLM})).status_code == 409, name
    await assert_untouched(env, ids, before)

    # отказ называет, кто здесь настоящий, и не выдаёт ключей
    refusal = await env.client.get(f"/api/openrouter/{ids['ollama']}/status")
    err = refusal.json()["error"]
    assert err["openrouter_provider_id"] == mine
    assert OLLAMA_KEY not in refusal.text and OR_KEY not in refusal.text


# ================================================================== 5. двойной Connect

async def test_repeated_connect_is_idempotent_and_never_spills(env, monkeypatch):
    """Повторный клик — смена ключа у ТОГО ЖЕ поставщика, чужие не тронуты."""
    patch_transport(monkeypatch)
    ids = await seed(env, "ollama", "cloud")
    before = await fingerprints(env, ids)

    first = (await env.client.post("/api/openrouter/connect",
                                   json={"api_key": OR_KEY})).json()
    second = (await env.client.post("/api/openrouter/connect",
                                    json={"api_key": OR_KEY_2})).json()
    third = (await env.client.post("/api/openrouter/connect",
                                   json={"api_key": OR_KEY_2})).json()
    assert first["created"] is True
    assert second["created"] is False and third["created"] is False
    assert {second["provider_id"], third["provider_id"]} == {first["provider_id"]}
    assert len(await provider_ids(env)) == 3, "повторный Connect завёл лишнего поставщика"

    mine = await fingerprint(env, first["provider_id"])
    assert mine["plain"] == OR_KEY_2, "последний ключ не сохранился у своего"
    await assert_untouched(env, ids, before)
    await assert_no_key_leak(env, OR_KEY, OR_KEY_2)


# ================================================================== 6. гонка

async def test_concurrent_connect_clicks_make_exactly_one_provider(env, monkeypatch):
    """Четыре одновременных Connect при живой Ollama: один новый поставщик, не пять."""
    patch_transport(monkeypatch)
    ids = await seed(env, "ollama")
    before = await fingerprints(env, ids)

    results = await asyncio.gather(*[
        env.client.post("/api/openrouter/connect", json={"api_key": OR_KEY})
        for _ in range(4)])
    assert [r.status_code for r in results] == [200] * 4, [r.text for r in results]
    made = {r.json()["provider_id"] for r in results}
    assert len(made) == 1, f"гонка развела поставщиков: {made}"
    assert sum(1 for r in results if r.json()["created"]) == 1
    assert made.isdisjoint(set(ids.values()))
    assert len(await provider_ids(env)) == 2
    await assert_untouched(env, ids, before)


async def test_concurrent_key_writes_land_in_one_row(env, monkeypatch):
    """Гонка на записи ключа: чужая строка не участвует ни в одном исходе."""
    patch_transport(monkeypatch)
    ids = await seed(env, "ollama")
    before = await fingerprints(env, ids)
    mine = (await env.client.post("/api/openrouter/connect",
                                  json={"api_key": OR_KEY})).json()["provider_id"]

    results = await asyncio.gather(
        env.client.patch(f"/api/openrouter/{mine}/key", json={"api_key": OR_KEY_2}),
        env.client.patch(f"/api/openrouter/{ids['ollama']}/key", json={"api_key": OR_KEY_2}),
        env.client.patch(f"/api/openrouter/{mine}/key", json={"api_key": OR_KEY_2}),
        env.client.patch(f"/api/openrouter/{ids['ollama']}/key", json={"api_key": OR_KEY_2}))
    codes = [r.status_code for r in results]
    assert codes == [200, 409, 200, 409], codes
    assert (await fingerprint(env, mine))["plain"] == OR_KEY_2
    await assert_untouched(env, ids, before)


# ================================================================== 7. неверный ключ

async def test_invalid_key_is_refused_and_no_foreign_row_changes(env, monkeypatch):
    def unauthorized(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/key"):
            return httpx.Response(401, json={"error": {"message": "No auth credentials found"}})
        return openrouter_like(request)

    patch_transport(monkeypatch, unauthorized)
    ids = await seed(env, "ollama", "cloud")
    before = await fingerprints(env, ids)

    r = await env.client.post("/api/openrouter/connect", json={"api_key": OR_KEY})
    assert r.status_code == 400, r.text
    assert "401" in r.json()["error"]["message"]
    assert OR_KEY not in r.text
    await assert_untouched(env, ids, before)

    # и тем более неверный ключ не приписывается чужому по id
    bad = await env.client.patch(f"/api/openrouter/{ids['ollama']}/key",
                                 json={"api_key": OR_KEY})
    assert bad.status_code == 409
    await assert_untouched(env, ids, before)
    await assert_no_key_leak(env, OR_KEY)


async def test_empty_key_is_refused_before_anything_happens(env, monkeypatch):
    seen: list[str] = []
    patch_transport(monkeypatch, seen=seen)
    ids = await seed(env, "ollama")
    before = await fingerprints(env, ids)

    assert (await env.client.post("/api/openrouter/connect",
                                  json={"api_key": "   "})).status_code == 422
    assert (await env.client.patch(f"/api/openrouter/{ids['ollama']}/key",
                                   json={"api_key": "  "})).status_code == 422
    assert seen == [], "пустой ключ не должен трогать сеть"
    assert len(await provider_ids(env)) == 1
    await assert_untouched(env, ids, before)


# ================================================================== 8. отказ политики

async def test_policy_refusal_touches_neither_network_nor_foreign_rows(env, monkeypatch):
    seen: list[str] = []
    patch_transport(monkeypatch, seen=seen)
    ids = await seed(env, "ollama", "cloud")
    before = await fingerprints(env, ids)

    with execution_privacy("private"):
        r = await env.client.post("/api/openrouter/connect", json={"api_key": OR_KEY})
    assert r.status_code == 403, r.text
    assert "политика" in r.json()["error"]["message"]
    assert seen == [], "в приватном контексте наружу не ушло ни одного запроса"
    await assert_untouched(env, ids, before)

    with execution_privacy("private"):
        denied = await env.client.patch(f"/api/openrouter/{ids['cloud']}/key",
                                        json={"api_key": OR_KEY})
    assert denied.status_code == 409, "identity проверяется раньше политики — и всё равно отказ"
    await assert_untouched(env, ids, before)
    await assert_no_key_leak(env, OR_KEY)


# ================================================================== 9. отказ сети

async def test_network_failure_is_reported_and_changes_nothing_foreign(env, monkeypatch):
    def dead(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    patch_transport(monkeypatch, dead)
    ids = await seed(env, "ollama", "cloud", "impostor")
    before = await fingerprints(env, ids)

    r = await env.client.post("/api/openrouter/connect", json={"api_key": OR_KEY})
    assert r.status_code == 502, r.text
    assert "OpenRouter" in r.json()["error"]["message"]
    await assert_untouched(env, ids, before)

    # ручки чужого поставщика по-прежнему отказывают, а не «пробуют сеть»
    assert (await env.client.patch(f"/api/openrouter/{ids['ollama']}/key",
                                   json={"api_key": OR_KEY})).status_code == 409
    await assert_untouched(env, ids, before)
    await assert_no_key_leak(env, OR_KEY)


# ================================================================== 10. поиск за первой страницей

async def test_catalog_paging_and_search_reach_the_tail_of_the_alphabet(env, monkeypatch):
    """GLM живёт за первой страницей: и листанием, и поиском — с честными счётчиками."""
    patch_transport(monkeypatch)
    ids = await seed(env, "ollama")
    before = await fingerprints(env, ids)
    pid = (await env.client.post("/api/openrouter/connect",
                                 json={"api_key": OR_KEY})).json()["provider_id"]

    first = (await env.client.get(f"/api/openrouter/{pid}/catalog?limit=100")).json()
    assert first["total"] == 262 and first["returned"] == 100 and first["has_more"] is True
    assert GLM not in {c["remote_id"] for c in first["items"]}, "предусловие: GLM в хвосте"

    seen, offset, pages = [], 0, 0
    while True:
        page = (await env.client.get(
            f"/api/openrouter/{pid}/catalog?limit=100&offset={offset}")).json()
        assert page["returned"] == len(page["items"])
        assert page["offset"] + page["returned"] <= page["total"]
        seen += [c["remote_id"] for c in page["items"]]
        offset += page["returned"]
        pages += 1
        if not page["has_more"]:
            break
        assert page["returned"] > 0, "страница без строк с has_more — бесконечный цикл"
        assert pages < 10, "листание не сходится"
    assert pages == 3 and len(seen) == 262 and len(set(seen)) == 262
    assert GLM in seen

    found = (await env.client.get(f"/api/openrouter/{pid}/catalog?q=glm&limit=100")).json()
    assert found["total"] == 2 and found["has_more"] is False and found["query"] == "glm"
    assert GLM in {c["remote_id"] for c in found["items"]}

    # третья страница закрепляется — то, ради чего листание и нужно
    pinned = (await env.client.post(f"/api/openrouter/{pid}/pin",
                                    json={"remote_id": GLM, "alias": "or-glm-5.3"})).json()
    assert pinned["alias"] == "or-glm-5.3"
    await assert_untouched(env, ids, before)


# ================================================================== 11. честные числа

async def test_counts_reported_back_are_the_real_ones(env, monkeypatch):
    """Числа на экране обязаны совпадать с базой на каждом шаге, а не «примерно»."""
    patch_transport(monkeypatch)
    assert (await ident(env))["providers_total"] == 0

    ids = await seed(env, "ollama", "cloud", "lmstudio")
    state = await ident(env)
    assert state["providers_total"] == len(await provider_ids(env)) == 3
    assert state["connected"] is False and state["provider_id"] is None

    made = (await env.client.post("/api/openrouter/connect",
                                  json={"api_key": OR_KEY})).json()
    assert made["models"] == 262 == len(BIG_CATALOG["data"])

    state = await ident(env)
    assert state["providers_total"] == len(await provider_ids(env)) == 4
    assert state["connected"] is True and state["has_key"] is True
    assert state["base_url"] == openrouter_ext.DEFAULT_BASE

    st = (await env.client.get(f"/api/openrouter/{made['provider_id']}/status")).json()
    async with env.svc.db.session() as s:
        real = int((await s.execute(
            sa.select(sa.func.count()).select_from(catalog_t)
            .where(catalog_t.c.provider_id == made["provider_id"]))).scalar_one() or 0)
    assert st["catalog_models"] == real == 262
    # у чужого поставщика каталога нет, и это ноль, а не «неизвестно»
    assert (await env.client.get(
        f"/api/openrouter/{ids['ollama']}/catalog")).json()["total"] == 0


# ================================================================== fail-closed

async def test_dangling_identity_pointer_fails_closed(env, monkeypatch):
    """Указатель есть, строки нет: ключ не «переезжает» в оставшегося поставщика."""
    patch_transport(monkeypatch)
    ids = await seed(env, "ollama")
    before = await fingerprints(env, ids)
    mine = (await env.client.post("/api/openrouter/connect",
                                  json={"api_key": OR_KEY})).json()["provider_id"]

    assert (await env.client.delete(f"/api/providers/{mine}")).status_code == 200
    assert await provider_ids(env) == {ids["ollama"]}
    assert (await ident(env))["connected"] is False, "удалённый поставщик не может быть нашим"

    r = await env.client.patch(f"/api/openrouter/{ids['ollama']}/key",
                               json={"api_key": OR_KEY_2})
    assert r.status_code == 409, r.text
    await assert_untouched(env, ids, before)

    # и восстановление идёт своим путём, а не поверх чужой строки
    again = (await env.client.post("/api/openrouter/connect",
                                   json={"api_key": OR_KEY_2})).json()
    assert again["provider_id"] != ids["ollama"]
    await assert_untouched(env, ids, before)


async def test_unknown_provider_id_is_not_an_identity(env, monkeypatch):
    patch_transport(monkeypatch)
    ids = await seed(env, "ollama")
    before = await fingerprints(env, ids)
    assert (await env.client.patch("/api/openrouter/999999/key",
                                   json={"api_key": OR_KEY})).status_code == 409
    await assert_untouched(env, ids, before)


# ================================================================== положительный контроль

async def test_with_openrouter_present_the_normal_flow_still_works(env, monkeypatch):
    """OpenRouter действительно есть: ключ, каталог, поиск, pin, смена ключа — всё как было."""
    patch_transport(monkeypatch)
    ids = await seed(env, "ollama")
    before = await fingerprints(env, ids)

    connected = (await env.client.post("/api/openrouter/connect",
                                       json={"api_key": OR_KEY})).json()
    pid = connected["provider_id"]

    assert (await env.client.get(f"/api/openrouter/{pid}/status")).json()["has_key"] is True
    assert (await env.client.post(f"/api/openrouter/{pid}/connect")).status_code == 200
    assert (await env.client.post(f"/api/openrouter/{pid}/sync?force=true")).json()["synced"] == 262

    # смена ключа у СВОЕГО поставщика по-прежнему разрешена
    patched = await env.client.patch(f"/api/openrouter/{pid}/key", json={"api_key": OR_KEY_2})
    assert patched.status_code == 200 and patched.json()["provider_id"] == pid
    assert (await fingerprint(env, pid))["plain"] == OR_KEY_2

    pinned = (await env.client.post(f"/api/openrouter/{pid}/pin",
                                    json={"remote_id": GLM, "alias": "or-glm"})).json()
    models = (await env.client.get("/api/models")).json()
    row = next(m for m in models if m["id"] == pinned["model_id"])
    assert row["name"] == GLM and row["kind"] == "cloud" and row["pricing_known"] is True

    await assert_untouched(env, ids, before)
    await assert_no_key_leak(env, OR_KEY, OR_KEY_2, OLLAMA_KEY)


async def test_no_key_ever_travels_in_a_url(env, monkeypatch):
    """Ключ живёт в теле запроса и в vault. Ни в адресе ручки, ни в исходящем URL."""
    seen: list[str] = []
    patch_transport(monkeypatch, seen=seen)
    ids = await seed(env, "ollama")
    made = (await env.client.post("/api/openrouter/connect",
                                  json={"api_key": OR_KEY})).json()
    await env.client.patch(f"/api/openrouter/{made['provider_id']}/key",
                           json={"api_key": OR_KEY_2})
    await env.client.get(f"/api/openrouter/{made['provider_id']}/catalog?q=glm")
    assert seen, "предусловие: исходящие запросы были"
    for url in seen:
        assert OR_KEY not in url and OR_KEY_2 not in url and OLLAMA_KEY not in url
    await assert_no_key_leak(env, OR_KEY, OR_KEY_2, OLLAMA_KEY)
    _ = ids


# ================================================================== сторона интерфейса

NODE_SUITE = r"""
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const PAGE = process.env.OPENROUTER_PAGE;
const mod = await import(PAGE);
const { providerBinding, connectRequest, refreshRequest, KEY_ENDPOINT } = mod;
// Забор читает КОД, а не комментарии: в шапке файла дефект описан словами,
// и запрет на его текст превратил бы документацию в ошибку сборки.
const source = readFileSync(PAGE, 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, ' ')
  .replace(/^\s*\/\/.*$/gm, ' ');

// Список поставщиков, на котором старая страница ломалась: OpenRouter в нём нет,
// но есть чужие, включая тех, чьё имя и адрес содержат слово openrouter.
const HOSTILE = [
  { id: 7, name: 'Ollama (локальная)', base_url: 'http://127.0.0.1:11434/v1' },
  { id: 8, name: 'openrouter-proxy', base_url: 'https://openrouter.ai.evil.example/api/v1' },
  { id: 9, name: 'Together', base_url: 'https://api.together.xyz/v1' },
];

test('нет OpenRouter — страница просит ключ, а не связывается с чужим', () => {
  for (const total of [0, 1, 3]) {
    const b = providerBinding({ connected: false, provider_id: null, providers_total: total });
    assert.equal(b.mode, 'connect');
    assert.equal(b.providerId, null);
    assert.equal(b.others, total);
  }
});

test('identity недоступна — отказ, а не догадка', () => {
  const cases = [
    providerBinding(null, { error: new Error('нет связи') }),
    providerBinding({ connected: true, provider_id: 5 }, { error: new Error('нет связи') }),
    providerBinding(null),
    providerBinding(undefined),
    providerBinding('OpenRouter'),
    providerBinding([{ id: 7 }]),
  ];
  for (const b of cases) {
    assert.equal(b.mode, 'connect');
    assert.equal(b.providerId, null);
  }
});

test('негодный id не становится связкой', () => {
  for (const bad of [null, undefined, 0, -1, 1.5, 'ollama', '', {}, [], NaN, Infinity, true]) {
    const b = providerBinding({ connected: true, provider_id: bad, providers_total: 3 });
    assert.equal(b.mode, 'connect', `id ${String(bad)} не должен связывать`);
    assert.equal(b.providerId, null);
  }
});

test('связка возможна только с id, который назвал сервер', () => {
  const b = providerBinding({ connected: true, provider_id: 42, providers_total: 4,
                              name: 'OpenRouter', base_url: 'https://openrouter.ai/api/v1',
                              has_key: true });
  assert.equal(b.mode, 'bound');
  assert.equal(b.providerId, '42');
  assert.equal(b.hasKey, true);
  // ни один id из чужого списка не может оказаться связкой
  for (const p of HOSTILE) assert.notEqual(b.providerId, String(p.id));
});

test('введённый ключ уходит на ручку без provider_id', () => {
  const req = connectRequest('sk-or-v1-ui-not-a-real-key');
  assert.equal(req.ok, true);
  assert.equal(req.method, 'POST');
  assert.equal(req.path, KEY_ENDPOINT);
  assert.equal(req.path, '/api/openrouter/connect');
  assert.equal(req.body.api_key, 'sk-or-v1-ui-not-a-real-key');
  // ключа нет в адресе, и id поставщика в адрес не подставляется
  assert.ok(!req.path.includes('sk-or'));
  assert.ok(!/\d/.test(req.path));
  assert.ok(!req.path.endsWith('/key'));
  for (const p of HOSTILE) assert.ok(!req.path.includes(String(p.id)));
});

test('пустой ключ никуда не уходит', () => {
  for (const empty of ['', '   ', null, undefined]) {
    const req = connectRequest(empty);
    assert.equal(req.ok, false);
    assert.equal(req.path, undefined);
  }
});

test('переподключение без ключа невозможно без связки', () => {
  assert.equal(refreshRequest(null), null);
  assert.equal(refreshRequest({ mode: 'connect', providerId: null }), null);
  assert.equal(refreshRequest({ mode: 'connect', providerId: '7' }), null);
  assert.equal(refreshRequest({ mode: 'bound', providerId: null }), null);
  const ok = refreshRequest({ mode: 'bound', providerId: '42' });
  assert.deepEqual(ok, { method: 'POST', path: '/api/openrouter/42/connect' });
});

test('в исходнике страницы не осталось выбора наугад (регрессионный забор)', () => {
  assert.ok(!source.includes('providers[0]'), 'вернулся выбор первого поставщика');
  assert.ok(!/includes\(\s*['"]openrouter['"]\s*\)/.test(source),
            'вернулось опознание поставщика по подстроке');
  assert.ok(!/\/key['"`]/.test(source), 'страница снова пишет ключ по provider_id');
  assert.ok(!source.includes('localStorage'), 'ключ рядом с localStorage');
  assert.ok(source.includes('/api/openrouter/provider'), 'страница не спрашивает identity');
});
"""


def test_ui_binds_only_to_the_identity_the_server_named(tmp_path):
    """Сторона интерфейса: production-модуль страницы исполняется, а не читается.

    Проверяются экспортированные чистые функции самой страницы — те, что решают,
    с каким поставщиком связаться и куда отправить ключ.
    """
    node = os.environ.get("CODEX_PRIMARY_RUNTIME_NODE") or shutil.which("node")
    if not node:
        pytest.skip("Node недоступен: контракты страницы не исполнялись")
    suite = tmp_path / "openrouter_identity.test.mjs"
    suite.write_text(NODE_SUITE, encoding="utf-8")
    result = subprocess.run([node, "--test", str(suite)], capture_output=True, text=True,
                            timeout=60, check=False,
                            env={**os.environ, "OPENROUTER_PAGE": str(UI_PAGE)})
    assert result.returncode == 0, result.stdout + result.stderr
