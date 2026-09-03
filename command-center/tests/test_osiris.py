"""OSIRIS: слой происхождения и реестр источников — доказательства отказов.

Каждый запрет из раздела 2 ТЗ проверяется тестом, который показывает ОТКАЗ, а
не намерение отказать. Сети здесь нет и быть не может: транспорт подменяется
типизированным адаптером со свойством `live = False`, и один из тестов
доказывает, что такой адаптер не имеет права объявить источник рабочим.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from bcc.features import osiris
from bcc.plugin_security import PluginSecurityError

WIKI_API = "https://en.wikipedia.org/api/rest_v1/page/summary/"
WIKI_PAGE = "https://en.wikipedia.org/wiki/Anthropic"
WIKI_PAYLOAD = {
    "title": "Anthropic",
    "description": "American artificial intelligence company",
    "extract": "Anthropic PBC — американская компания, занимающаяся ИИ.",
    "lang": "en",
    "wikibase_item": "Q117818153",
    "timestamp": "2026-08-30T11:22:33Z",
    "content_urls": {"desktop": {"page": WIKI_PAGE}},
    "coordinates": {"lat": 37.77, "lon": -122.41},
}


class StubAdapter:
    """Транспорт без сети. `live = False` — стенд не выдаёт себя за источник.

    Считает каждый вызов: тест выключенного флага доказывает ноль обращений.
    """

    live = False

    def __init__(self, routes: dict[str, tuple[int, str]] | None = None, *,
                 error: Exception | None = None):
        self.routes = dict(routes or {})
        self.error = error
        self.calls: list[str] = []

    async def fetch(self, url: str, *, headers=None, timeout: float = 15.0):
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        for prefix, (status, body) in self.routes.items():
            if url.startswith(prefix):
                return osiris.FetchResult(status=status, body=body, url=url)
        return osiris.FetchResult(status=404, body="{}", url=url)


def wiki_adapter(**extra) -> StubAdapter:
    return StubAdapter({WIKI_API: (200, json.dumps(WIKI_PAYLOAD)), **extra})


def install(env, adapter: StubAdapter) -> osiris.OsirisStore:
    store = osiris.store(env.svc)
    store.adapter = adapter
    return store


def osiris_dir(env) -> Path:
    return Path(env.settings.data_dir) / osiris.DIRNAME


def err(resp) -> dict:
    body = resp.json()
    return body.get("error") or body.get("detail") or body


def passport(**over) -> dict:
    """Полный паспорт; тест убирает из него ровно одно поле."""
    base = {"value": 42, "subject": "Anthropic", "source_id": "wikipedia-rest-en",
            "source_url": WIKI_PAGE, "method": "api", "license": "CC BY-SA 4.0",
            "observed_at": datetime(2026, 8, 30, 11, 22, 33), "raw_ref": "raw:deadbeef"}
    base.update(over)
    return base


C_SOURCE = {"id": "news-open", "category": "C", "base_url": "https://news.example.org",
            "auth_mode": "none", "rate_limit_per_min": 5, "license": "публикация без лицензии",
            "provides": ["headline"], "not_provides": ["контакты авторов"],
            "tos_checked_at": "2026-09-01", "path_template": "/story/{subject}",
            "parser": "generic_json", "observed_at_field": "published_at"}


# ------------------------------------------------------- паспорт обязателен


def test_observation_without_source_id_rejected():
    """Без source_id наблюдение не создаётся: факт, источник которого нельзя
    назвать строкой, нечем проверить и не за что отвечать."""
    with pytest.raises(osiris.PassportError) as exc:
        osiris.Observation(**passport(source_id=""))
    assert "source_id" in str(exc.value)


def test_observation_without_observed_at_rejected():
    """Без observed_at наблюдение не создаётся, и строка вместо времени не
    считается временем: свежий факт нельзя путать с прошлогодним."""
    with pytest.raises(osiris.PassportError):
        osiris.Observation(**passport(observed_at=None))
    with pytest.raises(osiris.PassportError):
        osiris.Observation(**passport(observed_at="2026-08-30T11:22:33"))


def test_observation_without_method_rejected():
    """Без method (и с методом вне закрытого списка) наблюдение не создаётся:
    способ получения — часть разрешения, а не примечание."""
    with pytest.raises(osiris.PassportError):
        osiris.Observation(**passport(method=""))
    with pytest.raises(osiris.PassportError):
        osiris.Observation(**passport(method="как-нибудь"))


def test_observation_with_full_passport_is_created():
    """Полный паспорт — наблюдение создаётся и отдаёт все поля раздела 3 ТЗ."""
    obs = osiris.Observation(**passport(confidence=0.9, attribute="title"))
    data = obs.as_dict()
    assert set(data) >= {"value", "subject", "source_id", "source_url", "method", "license",
                         "observed_at", "collected_at", "confidence", "raw_ref"}
    assert data["observed_at"] == "2026-08-30T11:22:33" and data["confidence"] == 0.9
    assert data["id"] and data["raw_ref"] == "raw:deadbeef"


def test_observation_without_raw_ref_rejected_for_network_methods():
    """Сетевое наблюдение без ссылки на сырьё не создаётся: вывод, который
    нечем перепроверить без повторного обращения к источнику, бесполезен."""
    with pytest.raises(osiris.PassportError):
        osiris.Observation(**passport(raw_ref=None))
    # Данные, принесённые владельцем, сырья в кэше не имеют — и это законно.
    assert osiris.Observation(**passport(method="user_upload", raw_ref=None,
                                         source_url="upload://contacts.csv")).id


def test_observation_passport_is_immutable():
    """Паспорт нельзя переписать после создания: иначе «откуда взято» станет
    мнением того, кто трогал запись последним."""
    obs = osiris.Observation(**passport())
    with pytest.raises(Exception):
        obs.source_id = "другой источник"


# ------------------------------------------------ реестр и его декларации


async def test_registry_answers_where_from(env):
    """Реестр показывается владельцу целиком: по нему отвечают на вопрос
    «откуда у тебя это» — категория, адрес, лицензия, что и чего НЕ отдаёт."""
    resp = await env.client.get("/api/osiris/sources")
    assert resp.status_code == 200
    source = {s["id"]: s for s in resp.json()["sources"]}["wikipedia-rest-en"]
    assert source["category"] == "A" and source["method"] == "api"
    assert source["auth_mode"] == "none" and source["rate_limit_per_min"] > 0
    assert source["license"] == "CC BY-SA 4.0"
    assert source["provides"] and source["not_provides"]
    assert source["tos_checked_at"] and source["base_url"] == "https://en.wikipedia.org"


async def test_register_source_requires_flag(env, monkeypatch):
    """Регистрация — действие, меняющее состояние: при выключенном флаге 409."""
    monkeypatch.delenv(osiris.FLAG, raising=False)
    resp = await env.client.post("/api/osiris/sources", json=dict(C_SOURCE))
    assert resp.status_code == 409


async def test_register_requires_tos_check_date(env, monkeypatch):
    """Без даты проверки условий использования источник не регистрируется:
    добавление источника — осознанное действие, а не побочный эффект."""
    monkeypatch.setenv(osiris.FLAG, "1")
    decl = dict(C_SOURCE)
    decl.pop("tos_checked_at")
    resp = await env.client.post("/api/osiris/sources", json=decl)
    assert resp.status_code == 400 and "tos_checked_at" in err(resp)["message"]


# --------------------------------------------- запреты как код (раздел 2)


FORBIDDEN_CASES = [
    ("foreign_session", {"headers": {"Cookie": "sessionid=aaaa.bbbb"}}),
    ("captcha_bypass", {"captcha_solver": "2captcha"}),
    ("leaked_database", {"base_url": "https://combolist-dumps.example.net"}),
    ("biometrics", {"provides": ["face_match", "headline"]}),
    ("private_scope", {"scope": "private_group"}),
    ("robots_override", {"ignore_robots": True}),
    ("paywall_bypass", {"strategy": "paywall_bypass"}),
    ("person_tracking", {"purpose": "geo_track"}),
]


@pytest.mark.parametrize("code,marker", FORBIDDEN_CASES, ids=[c for c, _ in FORBIDDEN_CASES])
async def test_forbidden_source_declaration_rejected(env, monkeypatch, code, marker):
    """Каждый пункт списка «Запрещено by design» отклоняет источник на
    регистрации с машинно-читаемой причиной, и файла источника не появляется."""
    monkeypatch.setenv(osiris.FLAG, "1")
    decl = {**C_SOURCE, "id": f"bad-{code.replace('_', '-')}", **marker}
    resp = await env.client.post("/api/osiris/sources", json=decl)
    assert resp.status_code == 400, resp.text
    assert err(resp)["code"] == code, err(resp)
    assert not (osiris_dir(env) / "sources" / f"{decl['id']}.json").exists()


async def test_forbidden_checks_are_listed_for_owner(env):
    """Список проверяемых запретов виден владельцу: запрет, которого нет в
    ответе системы, — это текст, а не код."""
    codes = {c["code"] for c in (await env.client.get("/api/osiris")).json()["forbidden_checks"]}
    assert codes == {code for code, _ in FORBIDDEN_CASES}


async def test_register_rejects_private_address(env, monkeypatch):
    """Приватный адрес отклоняется существующей проверкой egress."""
    monkeypatch.setenv(osiris.FLAG, "1")
    decl = {**C_SOURCE, "id": "loopback", "base_url": "http://127.0.0.1:9000"}
    resp = await env.client.post("/api/osiris/sources", json=decl)
    assert resp.status_code == 400 and err(resp)["code"] == "egress_blocked"


async def test_register_rejects_cloud_metadata_address(env, monkeypatch):
    """Адрес метаданных облака отклоняется — и по IP, и по имени."""
    monkeypatch.setenv(osiris.FLAG, "1")
    for base in ("http://169.254.169.254/latest/meta-data",
                 "http://metadata.google.internal/computeMetadata/v1"):
        resp = await env.client.post("/api/osiris/sources",
                                     json={**C_SOURCE, "id": "meta-src", "base_url": base})
        assert resp.status_code == 400 and err(resp)["code"] == "egress_blocked", base


async def test_egress_check_is_reused_not_duplicated(env, monkeypatch):
    """Исходящий адрес идёт именно через plugin_security.validate_url: подменив
    её, мы видим вызовы и меняем вердикт — значит второго слоя проверки нет."""
    monkeypatch.setenv(osiris.FLAG, "1")
    seen: list[str] = []
    original = osiris.psec.validate_url

    def spy(url, **kw):
        seen.append(url)
        return original(url, **kw)

    monkeypatch.setattr(osiris.psec, "validate_url", spy)
    assert (await env.client.post("/api/osiris/sources", json=dict(C_SOURCE))).status_code == 200
    assert C_SOURCE["base_url"] in seen

    monkeypatch.setattr(osiris.psec, "validate_url",
                        lambda url, **kw: (_ for _ in ()).throw(PluginSecurityError("нет")))
    resp = await env.client.post("/api/osiris/sources", json={**C_SOURCE, "id": "news-two"})
    assert resp.status_code == 400 and err(resp)["code"] == "egress_blocked"


def test_checked_url_blocks_private_and_metadata_directly():
    """Та же дверь наружу для любого адреса слоя, не только для реестра."""
    for url in ("http://127.0.0.1/x", "http://169.254.169.254/x", "http://2130706433/x",
                "http://metadata.google.internal/x"):
        with pytest.raises(PluginSecurityError):
            osiris.checked_url(url)


# ---------------------------------------------- robots.txt для категории C


async def test_category_c_without_allowing_robots_is_not_collected(env, monkeypatch):
    """Источник категории C, чей robots.txt запрещает обход, не собирается:
    ни одного наблюдения и ни одного файла сырья."""
    monkeypatch.setenv(osiris.FLAG, "1")
    adapter = StubAdapter({"https://news.example.org/robots.txt":
                           (200, "User-agent: *\nDisallow: /\n"),
                           "https://news.example.org/story/":
                           (200, json.dumps({"headline": "текст"}))})
    install(env, adapter)
    assert (await env.client.post("/api/osiris/sources", json=dict(C_SOURCE))).status_code == 200

    resp = await env.client.post("/api/osiris/collect",
                                 json={"source_id": "news-open", "subject": "Some-Story"})
    assert resp.status_code == 403 and err(resp)["code"] == "robots_disallow"
    assert not (osiris_dir(env) / "raw").exists()
    assert (await env.client.get("/api/osiris/observations",
                                 params={"subject": "Some-Story"})).json()["count"] == 0
    # До самой страницы дело не дошло — запрашивался только robots.txt.
    assert adapter.calls == ["https://news.example.org/robots.txt"]


async def test_category_c_unreachable_robots_is_refusal_not_permission(env, monkeypatch):
    """Недоступный robots.txt — отказ (fail-closed), а не «раз не запрещено,
    значит можно»."""
    monkeypatch.setenv(osiris.FLAG, "1")
    install(env, StubAdapter({"https://news.example.org/robots.txt": (503, "")}))
    await env.client.post("/api/osiris/sources", json=dict(C_SOURCE))
    resp = await env.client.post("/api/osiris/collect",
                                 json={"source_id": "news-open", "subject": "Some-Story"})
    assert resp.status_code == 403 and err(resp)["code"] == "robots_disallow"


async def test_category_c_with_allowing_robots_is_collected(env, monkeypatch):
    """Контроль к двум предыдущим: при разрешающем robots.txt тот же источник
    собирается — отказ был именно про robots, а не про категорию C вообще."""
    monkeypatch.setenv(osiris.FLAG, "1")
    install(env, StubAdapter({
        "https://news.example.org/robots.txt": (200, "User-agent: *\nDisallow: /private\n"),
        "https://news.example.org/story/": (200, json.dumps(
            {"headline": "открытая публикация", "published_at": "2026-08-01T09:00:00Z"}))}))
    await env.client.post("/api/osiris/sources", json=dict(C_SOURCE))
    resp = await env.client.post("/api/osiris/collect",
                                 json={"source_id": "news-open", "subject": "Some-Story"})
    assert resp.status_code == 200, resp.text
    obs = resp.json()["observations"]
    assert [o["attribute"] for o in obs] == ["headline"]
    assert obs[0]["method"] == "fetch" and obs[0]["observed_at"] == "2026-08-01T09:00:00"


async def test_category_d_is_not_collected_over_network(env, monkeypatch):
    """Категорию D владелец приносит сам — сетью она не собирается."""
    monkeypatch.setenv(osiris.FLAG, "1")
    adapter = wiki_adapter()
    install(env, adapter)
    decl = {"id": "owner-export", "category": "D", "base_url": "upload://contacts",
            "auth_mode": "user_upload", "rate_limit_per_min": 1, "license": "личные данные",
            "provides": ["contact"], "not_provides": [], "tos_checked_at": "2026-09-01"}
    assert (await env.client.post("/api/osiris/sources", json=decl)).status_code == 200
    resp = await env.client.post("/api/osiris/collect",
                                 json={"source_id": "owner-export", "subject": "Anthropic"})
    assert resp.status_code == 400 and adapter.calls == []


# ------------------------------------------- источник категории A целиком


async def test_collect_produces_full_passport(env, monkeypatch):
    """Сбор с источника A даёт наблюдения с полным паспортом: настоящий адрес
    страницы, лицензия источника, время правки как observed_at и ссылка на сырьё."""
    monkeypatch.setenv(osiris.FLAG, "1")
    install(env, wiki_adapter())
    resp = await env.client.post("/api/osiris/collect",
                                 json={"source_id": "wikipedia-rest-en", "subject": "Anthropic"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    by_attr = {o["attribute"]: o for o in body["observations"]}
    assert {"title", "description", "extract", "lang", "wikibase_item",
            "coordinates"} == set(by_attr)
    title = by_attr["title"]
    assert title["value"] == "Anthropic" and title["subject"] == "Anthropic"
    assert title["source_id"] == "wikipedia-rest-en" and title["source_url"] == WIKI_PAGE
    assert title["method"] == "api" and title["license"] == "CC BY-SA 4.0"
    assert title["observed_at"] == "2026-08-30T11:22:33"       # время правки из источника
    assert title["raw_ref"].startswith("raw:") and 0.0 <= title["confidence"] <= 1.0

    listed = await env.client.get("/api/osiris/observations", params={"subject": "Anthropic"})
    assert listed.json()["count"] == len(by_attr)


async def test_raw_kept_apart_so_recheck_needs_no_source(env, monkeypatch):
    """Сырьё лежит отдельно от выводов, с хешем и TTL: перепроверить наблюдение
    можно по файлу кэша, не обращаясь к источнику повторно."""
    monkeypatch.setenv(osiris.FLAG, "1")
    install(env, wiki_adapter())
    body = (await env.client.post("/api/osiris/collect",
                                  json={"source_id": "wikipedia-rest-en",
                                        "subject": "Anthropic"})).json()
    digest = body["raw_ref"].split(":", 1)[1]
    raw = json.loads((osiris_dir(env) / "raw" / f"{digest}.json").read_text(encoding="utf-8"))
    assert raw["ttl_seconds"] > 0 and raw["expires_at"] > raw["fetched_at"]
    assert raw["body_sha256"] and json.loads(raw["body"])["title"] == "Anthropic"
    assert raw["url"].startswith(WIKI_API) and raw["transport"] == "stub"


async def test_repeat_collect_appends_observation_and_does_not_overwrite(env, monkeypatch):
    """Повторный сбор того же факта — НОВОЕ наблюдение с новым collected_at, а
    не перезапись прежнего: история наблюдений и есть ценность."""
    monkeypatch.setenv(osiris.FLAG, "1")
    install(env, wiki_adapter())
    payload = {"source_id": "wikipedia-rest-en", "subject": "Anthropic"}
    first = (await env.client.post("/api/osiris/collect", json=payload)).json()
    second = (await env.client.post("/api/osiris/collect", json=payload)).json()

    titles = [o for o in (await env.client.get(
        "/api/osiris/observations", params={"subject": "Anthropic"})).json()["observations"]
        if o["attribute"] == "title"]
    assert len(titles) == 2, titles
    assert titles[0]["collected_at"] != titles[1]["collected_at"]
    assert titles[0]["value"] == titles[1]["value"] == "Anthropic"
    assert {t["id"] for t in titles} == {o["id"] for o in
                                         first["observations"] + second["observations"]
                                         if o["attribute"] == "title"}
    # Второй сбор попал в свежий кэш — источник не дёргали дважды.
    assert second["from_cache"] is True and first["from_cache"] is False


async def test_rate_limit_refuses_when_budget_spent(env, monkeypatch):
    """Объявленный в реестре лимит запросов исполняется, а не декларируется."""
    monkeypatch.setenv(osiris.FLAG, "1")
    adapter = StubAdapter({"https://api.example.org/v1/": (200, json.dumps({"field": "v"}))})
    install(env, adapter)
    decl = {"id": "tight-api", "category": "A", "base_url": "https://api.example.org",
            "auth_mode": "none", "rate_limit_per_min": 1, "license": "CC0",
            "provides": ["field"], "not_provides": [], "tos_checked_at": "2026-09-01",
            "path_template": "/v1/{subject}", "cache_ttl_seconds": 0}
    assert (await env.client.post("/api/osiris/sources", json=decl)).status_code == 200
    body = {"source_id": "tight-api", "subject": "Thing"}
    assert (await env.client.post("/api/osiris/collect", json=body)).status_code == 200
    resp = await env.client.post("/api/osiris/collect", json=body)
    assert resp.status_code == 429 and err(resp)["code"] == "rate_limited"
    assert len(adapter.calls) == 1                    # второй запрос наружу не ушёл


async def test_unknown_source_is_404(env, monkeypatch):
    """Собирать с источника, которого нет в реестре, нельзя."""
    monkeypatch.setenv(osiris.FLAG, "1")
    adapter = wiki_adapter()
    install(env, adapter)
    resp = await env.client.post("/api/osiris/collect",
                                 json={"source_id": "нет-такого", "subject": "Anthropic"})
    assert resp.status_code == 404 and adapter.calls == []


async def test_source_error_yields_no_observations(env, monkeypatch):
    """Ошибка источника не превращается в данные: 502, ни сырья, ни выводов."""
    monkeypatch.setenv(osiris.FLAG, "1")
    install(env, StubAdapter({WIKI_API: (500, "boom")}))
    resp = await env.client.post("/api/osiris/collect",
                                 json={"source_id": "wikipedia-rest-en", "subject": "Anthropic"})
    assert resp.status_code == 502
    assert not (osiris_dir(env) / "raw").exists()
    assert not (osiris_dir(env) / "observations").exists()


async def test_source_never_checked_live_is_marked_honestly(env, monkeypatch):
    """Источник, которого живьём не дёргали, помечен «не проверялся живьём»:
    подменённый адаптер не имеет права выдать зелёный статус за работу."""
    monkeypatch.setenv(osiris.FLAG, "1")
    install(env, wiki_adapter())
    before = {s["id"]: s for s in (await env.client.get("/api/osiris")).json()["sources"]}
    assert before["wikipedia-rest-en"]["live_status"] == "not_verified_live"

    body = (await env.client.post("/api/osiris/collect",
                                  json={"source_id": "wikipedia-rest-en",
                                        "subject": "Anthropic"})).json()
    assert body["transport"] == "stub"
    after = {s["id"]: s for s in (await env.client.get("/api/osiris")).json()["sources"]}
    assert after["wikipedia-rest-en"]["live_status"] == "not_verified_live"
    assert after["wikipedia-rest-en"]["live_checked_at"] is None
    # Право пометить источник проверенным есть только у настоящего транспорта.
    assert osiris.HttpFetchAdapter.live is True and StubAdapter.live is False


# --------------------------------------------------- право на удаление


async def test_delete_subject_leaves_no_raw_no_derived_no_index(env, monkeypatch):
    """Удаление субъекта одной операцией убирает сырьё, производные и след в
    индексе — проверяется и по файлам, и по ручке."""
    monkeypatch.setenv(osiris.FLAG, "1")
    install(env, wiki_adapter())
    await env.client.post("/api/osiris/collect",
                          json={"source_id": "wikipedia-rest-en", "subject": "Anthropic"})
    root = osiris_dir(env)
    key = osiris.subject_key("Anthropic")
    assert list((root / "observations" / key).glob("*.json"))
    assert list((root / "raw").glob("*.json"))
    assert key in json.loads((root / "index.json").read_text(encoding="utf-8"))["subjects"]

    resp = await env.client.delete("/api/osiris/subjects/Anthropic")
    assert resp.status_code == 200 and resp.json()["observations_deleted"] > 0
    assert resp.json()["raw_deleted"] > 0 and resp.json()["index_entry_removed"] is True

    assert not (root / "observations" / key).exists()
    assert list((root / "raw").glob("*.json")) == []
    assert json.loads((root / "index.json").read_text(encoding="utf-8"))["subjects"] == {}
    listed = await env.client.get("/api/osiris/observations", params={"subject": "Anthropic"})
    assert listed.json()["count"] == 0
    state = (await env.client.get("/api/osiris")).json()
    assert state["observations"] == 0 and state["subjects"] == 0 and state["raw_records"] == 0


async def test_delete_subject_requires_flag(env, monkeypatch):
    """Удаление меняет состояние: при выключенном флаге 409."""
    monkeypatch.delenv(osiris.FLAG, raising=False)
    assert (await env.client.delete("/api/osiris/subjects/Anthropic")).status_code == 409


# ------------------------------------------------------- выключенный флаг


async def test_flag_off_calls_no_adapter_and_creates_no_files(env, monkeypatch):
    """При выключенном флаге ни один сетевой адаптер не вызывается и ни один
    файл не создаётся; читающие ручки честно отвечают enabled: false."""
    monkeypatch.delenv(osiris.FLAG, raising=False)
    adapter = wiki_adapter()
    install(env, adapter)

    state = await env.client.get("/api/osiris")
    assert state.status_code == 200 and state.json()["enabled"] is False
    assert state.json()["observations"] == 0 and state.json()["sources"]
    sources = await env.client.get("/api/osiris/sources")
    assert sources.status_code == 200 and sources.json()["enabled"] is False
    listed = await env.client.get("/api/osiris/observations", params={"subject": "Anthropic"})
    assert listed.status_code == 200 and listed.json()["count"] == 0

    for resp in (await env.client.post("/api/osiris/sources", json=dict(C_SOURCE)),
                 await env.client.post("/api/osiris/collect",
                                       json={"source_id": "wikipedia-rest-en",
                                             "subject": "Anthropic"}),
                 await env.client.delete("/api/osiris/subjects/Anthropic")):
        assert resp.status_code == 409, resp.text

    assert adapter.calls == []
    assert not osiris_dir(env).exists(), sorted(p.name for p in osiris_dir(env).iterdir())
