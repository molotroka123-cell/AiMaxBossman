"""Облачные провайдеры моделей: ключ дан — список моделей есть; не дан — сказано, почему.

Жалоба владельца после живого прогона 20260906: ключ OpenRouter вставлен, а
списка облачных моделей нет и причина не названа. Здесь зафиксирован обратный
контракт — на каждый исход (нет ключа, ключ отклонён, облако запрещено
политикой) владелец получает ПРИЧИНУ, а не пустоту.

Сети в тестах нет: транспорт подменяется httpx.MockTransport. Ни один ключ в
этом файле не настоящий.
"""
from __future__ import annotations

import httpx
import pytest

from bossman.gateway.app import create_gateway_app
from bossman.gateway.backends import (OpenRouterBackend, ZaiBackend, build_backend,
                                      OpenAIBackend)
from bossman.gateway.config import (GLM_MODEL_ENV, OPENROUTER_BASE_URL_ENV,
                                    OPENROUTER_KEY_ENV, ZAI_KEY_ENV, AliasConfig,
                                    BackendConfig, ClientConfig, GatewayConfig, ModelTarget,
                                    glm_model_id, load_env_file, load_gateway_config,
                                    openrouter_backend_config, parse_env_file,
                                    zai_backend_config)
from bossman.gateway.router import CloudPolicyDenied, ModelRouter, RouteNotFound

FAKE_KEY = "sk-or-v1-not-a-real-key"   # ci-secret-scan: allow — заведомо нерабочее
GLM = "z-ai/glm-5.3-flash"

CATALOG = {"data": [
    {"id": GLM, "name": "Z.AI: GLM 5.3 Flash", "context_length": 128000},
    {"id": "anthropic/claude-opus-5", "name": "Anthropic: Claude Opus 5"},
]}


def _transport(handler):
    return httpx.MockTransport(handler)


def _catalog_handler(seen: list[httpx.Request] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json=CATALOG)
        return httpx.Response(404, json={"error": "not found"})
    return handler


# --------------------------------------------------------------- ключ → список моделей

async def test_key_from_env_yields_the_cloud_model_list(monkeypatch):
    """Главный сценарий владельца: дал ключ — получил каталог, и GLM 5.3 в нём."""
    monkeypatch.setenv(OPENROUTER_KEY_ENV, FAKE_KEY)
    seen: list[httpx.Request] = []
    backend = OpenRouterBackend.from_env(_transport(_catalog_handler(seen)))
    try:
        listing = await backend.list_models()
    finally:
        await backend.close()

    assert listing.status == "ok" and listing.reason is None
    assert GLM in listing.models
    request = seen[0]
    # Ключ уходит заголовком авторизации и только им; версия API не удваивается.
    assert request.headers["authorization"] == f"Bearer {FAKE_KEY}"
    assert str(request.url) == "https://openrouter.ai/api/v1/models"


async def test_configured_base_url_with_version_is_not_doubled(monkeypatch):
    """Владелец копирует адрес из документации вместе с /v1 — это рабочий ввод."""
    monkeypatch.setenv(OPENROUTER_KEY_ENV, FAKE_KEY)
    monkeypatch.setenv(OPENROUTER_BASE_URL_ENV, "https://openrouter.ai/api/v1/")
    seen: list[httpx.Request] = []
    backend = OpenRouterBackend.from_env(_transport(_catalog_handler(seen)))
    try:
        assert (await backend.list_models()).status == "ok"
    finally:
        await backend.close()
    assert str(seen[0].url) == "https://openrouter.ai/api/v1/models"


# --------------------------------------------------------------- отрицательные контроли

async def test_missing_key_is_unavailable_not_empty_list(monkeypatch):
    """Без ключа провайдер недоступен — с названием переменной и без обращения в сеть."""
    monkeypatch.delenv(OPENROUTER_KEY_ENV, raising=False)
    seen: list[httpx.Request] = []
    backend = OpenRouterBackend.from_env(_transport(_catalog_handler(seen)))
    try:
        listing = await backend.list_models()
    finally:
        await backend.close()

    assert listing.status == "unavailable" and listing.models == []
    assert OPENROUTER_KEY_ENV in listing.reason
    assert seen == [], "без ключа в сеть ходить не за чем"


async def test_expired_key_surfaces_401_as_the_reason(monkeypatch):
    """Истёкший ключ (KEY-EXPIRY живого прогона) — 401 словами, а не «пусто»."""
    monkeypatch.setenv(OPENROUTER_KEY_ENV, FAKE_KEY)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "No auth credentials found"}})

    backend = OpenRouterBackend.from_env(_transport(handler))
    try:
        listing = await backend.list_models()
    finally:
        await backend.close()

    assert listing.status == "error" and listing.models == []
    assert "401" in listing.reason
    assert FAKE_KEY not in listing.reason        # секрет не эхом


async def test_network_failure_is_named_not_swallowed(monkeypatch):
    monkeypatch.setenv(OPENROUTER_KEY_ENV, FAKE_KEY)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network is down")

    backend = OpenRouterBackend.from_env(_transport(handler))
    try:
        listing = await backend.list_models()
    finally:
        await backend.close()
    assert listing.status == "error" and "ConnectError" in listing.reason


# --------------------------------------------------------------- маршрутизация по id модели

def _router(monkeypatch, *, cloud=True) -> ModelRouter:
    monkeypatch.setenv(OPENROUTER_KEY_ENV, FAKE_KEY)
    monkeypatch.setenv(ZAI_KEY_ENV, "zai-not-a-real-key")
    cfg = GatewayConfig(
        backends={"openrouter": openrouter_backend_config(),
                  "zai": zai_backend_config(),
                  "ollama": BackendConfig("ollama", "http://local", cloud=False)},
        aliases={"bossman-fast": AliasConfig("bossman-fast",
                                             [ModelTarget("ollama", "qwen", 10, {"text"})])},
    )
    t = _transport(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}))
    return ModelRouter(cfg, {n: build_backend(c, t) for n, c in cfg.backends.items()})


def test_openrouter_prefix_routes_to_openrouter_backend(monkeypatch):
    """`openrouter/<id>` — это и есть выбор провайдера; префикс до бэкенда не доезжает."""
    routes = _router(monkeypatch).resolve(f"openrouter/{GLM}", cloud_allowed=True)
    assert len(routes) == 1
    assert routes[0].backend_name == "openrouter" and routes[0].model == GLM
    assert routes[0].is_cloud is True
    assert isinstance(routes[0].backend, OpenRouterBackend)


def test_glm_model_id_routes_to_zai_backend(monkeypatch):
    """Второй маршрут к той же модели: `glm-5.3` напрямую в Z.ai."""
    routes = _router(monkeypatch).resolve(glm_model_id(), cloud_allowed=True)
    assert len(routes) == 1
    assert routes[0].backend_name == "zai" and routes[0].model == glm_model_id()
    assert isinstance(routes[0].backend, ZaiBackend)


def test_zai_paths_do_not_duplicate_the_api_version(monkeypatch):
    """У Z.ai версия входит в base_url: путь инференса — /chat/completions."""
    monkeypatch.setenv(ZAI_KEY_ENV, "zai-not-a-real-key")
    backend = ZaiBackend.from_env()
    assert backend.config.base_url == "https://api.z.ai/api/coding/paas/v4"
    assert backend.resolve_path("/v1/chat/completions") == "/chat/completions"


def test_generic_backend_paths_are_untouched():
    """Обычный OpenAI-совместимый бэкенд остаётся байт-в-байт прежним."""
    b = OpenAIBackend(BackendConfig("ollama", "http://local"))
    assert b.resolve_path("/v1/chat/completions") == "/v1/chat/completions"


def test_direct_cloud_route_still_obeys_fail_closed_policy(monkeypatch):
    """Прямая адресация — не обход облачной политики: без разрешения это отказ."""
    with pytest.raises(CloudPolicyDenied):
        _router(monkeypatch).resolve(f"openrouter/{GLM}", cloud_allowed=False)


def test_unknown_model_is_still_route_not_found(monkeypatch):
    with pytest.raises(RouteNotFound):
        _router(monkeypatch).resolve("no-such-model", cloud_allowed=True)


def test_alias_wins_over_direct_addressing(monkeypatch):
    """Правила оператора главнее: алиас из yaml не перехватывается провайдером."""
    routes = _router(monkeypatch).resolve("bossman-fast", cloud_allowed=False)
    assert [r.backend_name for r in routes] == ["ollama"]


def test_glm_model_id_is_configuration(monkeypatch):
    monkeypatch.setenv(GLM_MODEL_ENV, "glm-5.3-air")
    monkeypatch.setenv(ZAI_KEY_ENV, "zai-not-a-real-key")
    assert glm_model_id() == "glm-5.3-air"


# --------------------------------------------------------------- health-эндпоинт

def _app(cfg, backends):
    app = create_gateway_app(cfg, router=ModelRouter(cfg, backends))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw")


def _health_cfg() -> GatewayConfig:
    return GatewayConfig(
        backends={"openrouter": openrouter_backend_config()},
        aliases={}, clients={"core": ClientConfig("core", key=None)},
        allow_unauthenticated_loopback=True)


async def test_health_openrouter_returns_the_model_list(monkeypatch):
    monkeypatch.setenv(OPENROUTER_KEY_ENV, FAKE_KEY)
    cfg = _health_cfg()
    backends = {"openrouter": build_backend(cfg.backends["openrouter"],
                                            _transport(_catalog_handler()))}
    async with _app(cfg, backends) as c:
        r = await c.get("/health/openrouter")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and GLM in body["models"]


async def test_health_openrouter_without_key_says_why(monkeypatch):
    """Ключа нет — эндпоинт отвечает, а не падает, и называет переменную."""
    monkeypatch.delenv(OPENROUTER_KEY_ENV, raising=False)
    cfg = _health_cfg()
    backends = {"openrouter": build_backend(cfg.backends["openrouter"],
                                            _transport(_catalog_handler()))}
    async with _app(cfg, backends) as c:
        r = await c.get("/health/openrouter")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "unavailable" and body["models"] == []
    assert OPENROUTER_KEY_ENV in body["reason"]


# --------------------------------------------------------------- секреты из .env

def test_env_file_fills_gaps_but_never_overrides_real_environment(tmp_path, monkeypatch):
    """Окружение процесса сильнее файла: .env разработчика не перебивает боевой ключ."""
    env_file = tmp_path / ".env"
    env_file.write_text(f'{OPENROUTER_KEY_ENV}=from-file\n# комментарий\n'
                        f'export {ZAI_KEY_ENV}="from-file-too"\n', encoding="utf-8")
    monkeypatch.setenv(OPENROUTER_KEY_ENV, "from-real-environment")
    monkeypatch.delenv(ZAI_KEY_ENV, raising=False)

    load_env_file(env_file)

    import os
    assert os.environ[OPENROUTER_KEY_ENV] == "from-real-environment"
    assert os.environ[ZAI_KEY_ENV] == "from-file-too"


def test_env_file_parser_ignores_everything_that_is_not_an_assignment():
    parsed = parse_env_file("# c\n\nA=1\nexport B='two'\nnot an assignment\nC=\n")
    assert parsed == {"A": "1", "B": "two", "C": ""}


def test_missing_env_file_is_not_an_error(tmp_path):
    assert load_env_file(tmp_path / "нет-такого.env") == {}


def test_key_alone_brings_the_provider_up(tmp_path, monkeypatch):
    """Без ключа облачного бэкенда нет; с ключом он появляется без правки yaml."""
    monkeypatch.setenv("BOSSMAN_GATEWAY_CONFIG", str(tmp_path / "нет-конфига.yaml"))
    monkeypatch.setenv("BOSSMAN_ENV_FILE", str(tmp_path / "нет-такого.env"))
    monkeypatch.delenv(OPENROUTER_KEY_ENV, raising=False)
    monkeypatch.delenv(ZAI_KEY_ENV, raising=False)
    assert "openrouter" not in load_gateway_config().backends

    monkeypatch.setenv(OPENROUTER_KEY_ENV, FAKE_KEY)
    backend = load_gateway_config().backends["openrouter"]
    assert backend.cloud is True and backend.api_key_env == OPENROUTER_KEY_ENV
    assert backend.api_key is None, "значение ключа в конфигурации не хранится"


def test_explicit_yaml_backend_is_not_overridden_by_env(tmp_path, monkeypatch):
    """Решение оператора в yaml (в т.ч. enabled: false) сильнее наличия ключа."""
    cfg_file = tmp_path / "gateway.yaml"
    cfg_file.write_text("backends:\n  openrouter:\n    base_url: https://mirror.example/api\n"
                        "    enabled: false\n", encoding="utf-8")
    monkeypatch.setenv("BOSSMAN_GATEWAY_CONFIG", str(cfg_file))
    monkeypatch.setenv("BOSSMAN_ENV_FILE", str(tmp_path / "нет-такого.env"))
    monkeypatch.setenv(OPENROUTER_KEY_ENV, FAKE_KEY)
    backend = load_gateway_config().backends["openrouter"]
    assert backend.base_url == "https://mirror.example/api" and backend.enabled is False


# --------------------------------------------------------------- сквозной путь до модели

async def test_glm_through_openrouter_reaches_the_model_when_cloud_is_allowed(monkeypatch):
    """Ради этого всё и делалось: ядро зовёт GLM 5.3 по её собственному id."""
    monkeypatch.setenv(OPENROUTER_KEY_ENV, FAKE_KEY)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "42"}}],
                                         "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    cfg = GatewayConfig(backends={"openrouter": openrouter_backend_config()}, aliases={},
                        clients={"core": ClientConfig("core", key=None)},
                        allow_unauthenticated_loopback=True)
    backends = {"openrouter": build_backend(cfg.backends["openrouter"], _transport(handler))}
    body = {"model": f"openrouter/{GLM}", "messages": [{"role": "user", "content": "сколько?"}]}
    async with _app(cfg, backends) as c:
        r = await c.post("/v1/chat/completions", json=body,
                         headers={"x-bossman-cloud-allowed": "1"})
    assert r.status_code == 200, r.text
    assert r.headers["x-bossman-cloud"] == "1"
    # провайдеру ушёл его собственный id модели, без служебного префикса
    assert seen and seen[0].read().decode().find(f'"{GLM}"') > 0
    assert str(seen[0].url).endswith("/api/v1/chat/completions")


async def test_same_call_without_the_cloud_header_is_denied_by_policy(monkeypatch):
    """Fail-closed остаётся fail-closed: без явного разрешения данные не уходят."""
    monkeypatch.setenv(OPENROUTER_KEY_ENV, FAKE_KEY)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "42"}}]})

    cfg = GatewayConfig(backends={"openrouter": openrouter_backend_config()}, aliases={},
                        clients={"core": ClientConfig("core", key=None)},
                        allow_unauthenticated_loopback=True)
    backends = {"openrouter": build_backend(cfg.backends["openrouter"], _transport(handler))}
    body = {"model": f"openrouter/{GLM}", "messages": [{"role": "user", "content": "секрет"}]}
    async with _app(cfg, backends) as c:
        r = await c.post("/v1/chat/completions", json=body)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "POLICY_DENIED"
    assert seen == [], "запрос не должен был уйти наружу"


# --------------------------------------------------------------- CLI владельца

async def _cli_models(monkeypatch, provider: str, transport=None) -> int:
    from bossman import cli
    from bossman.gateway import backends as backends_mod

    if transport is not None:
        real = backends_mod.build_backend
        monkeypatch.setattr(backends_mod, "build_backend",
                            lambda cfg, t=None: real(cfg, transport))

    class Args:
        pass
    args = Args()
    args.provider = provider
    return await cli._models(args)


async def test_cli_lists_models_when_the_key_is_set(monkeypatch, capsys):
    monkeypatch.setenv(OPENROUTER_KEY_ENV, FAKE_KEY)
    code = await _cli_models(monkeypatch, "openrouter", _transport(_catalog_handler()))
    assert code == 0
    assert GLM in capsys.readouterr().out.splitlines()


async def test_cli_without_key_explains_instead_of_crashing(monkeypatch, capsys):
    monkeypatch.delenv(OPENROUTER_KEY_ENV, raising=False)
    monkeypatch.setenv("BOSSMAN_ENV_FILE", "/нет/такого/.env")
    code = await _cli_models(monkeypatch, "openrouter")
    assert code == 1
    err = capsys.readouterr().err
    assert OPENROUTER_KEY_ENV in err and "не задан" in err
