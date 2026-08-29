"""Облачная политика — граница, которую Gateway обязан держать сам.

Red-team нашёл дыру: is_cloud() в llm.py классифицирует по префиксу алиаса, и
capability-алиас (bossman-smart) НИКОГДА не считается облачным. Поэтому агент с
cloud_policy=never в режиме Gateway молча уходил в облако при отказе локальной
модели. Данные, которые владелец объявил «наружу нельзя», утекали.

Эти тесты падали до правки: маршрутизатор не знал слова cloud.
"""
import httpx
import pytest

from bossman.gateway.backends import OpenAIBackend
from bossman.gateway.config import AliasConfig, BackendConfig, GatewayConfig, ModelTarget
from bossman.gateway.router import CloudPolicyDenied, ModelRouter


def _router(**backend_kw):
    """Алиас bossman-smart: локальный ollama (приоритет 10) + облачный openrouter (100)."""
    cfg = GatewayConfig(
        backends={
            "ollama": BackendConfig("ollama", "http://local", cloud=False),
            "openrouter": BackendConfig("openrouter", "http://cloud", cloud=True),
        },
        aliases={"bossman-smart": AliasConfig("bossman-smart", [
            ModelTarget("ollama", "qwen", 10, {"text"}),
            ModelTarget("openrouter", "gpt-4o", 100, {"text"}),
        ])},
    )
    t = httpx.MockTransport(lambda r: httpx.Response(200, json={}))
    return ModelRouter(cfg, {n: OpenAIBackend(c, t) for n, c in cfg.backends.items()})


def test_backend_declares_cloud_explicitly():
    """Облачность объявляется, а не угадывается по имени: явный флаг — источник истины."""
    assert BackendConfig("openrouter", "http://x", cloud=True).cloud is True
    assert BackendConfig("ollama", "http://x").cloud is False


def test_never_policy_removes_cloud_targets_entirely():
    """cloud_allowed=False: облачные цели не попадают в маршрут вовсе."""
    routes = _router().resolve("bossman-smart", cloud_allowed=False)
    assert routes, "локальный маршрут должен остаться"
    assert all(not r.is_cloud for r in routes), "облачная цель просочилась при policy=never"
    assert {r.backend_name for r in routes} == {"ollama"}


def test_never_policy_with_only_cloud_available_denies_not_egresses():
    """Если алиас можно обслужить ТОЛЬКО облаком, а policy=never — отказ, не отправка."""
    cfg = GatewayConfig(
        backends={"openrouter": BackendConfig("openrouter", "http://cloud", cloud=True)},
        aliases={"bossman-smart": AliasConfig("bossman-smart",
                                              [ModelTarget("openrouter", "gpt-4o", 100, {"text"})])},
    )
    t = httpx.MockTransport(lambda r: httpx.Response(200, json={}))
    router = ModelRouter(cfg, {"openrouter": OpenAIBackend(cfg.backends["openrouter"], t)})
    with pytest.raises(CloudPolicyDenied):
        router.resolve("bossman-smart", cloud_allowed=False)


def test_allowed_policy_keeps_cloud_fallback():
    routes = _router().resolve("bossman-smart", cloud_allowed=True)
    names = {r.backend_name for r in routes}
    assert "openrouter" in names and "ollama" in names


def test_route_knows_whether_it_is_cloud():
    routes = _router().resolve("bossman-smart", cloud_allowed=True)
    by_name = {r.backend_name: r for r in routes}
    assert by_name["openrouter"].is_cloud is True
    assert by_name["ollama"].is_cloud is False


# ------------------------------------------------------------------ сквозной путь ядро→Gateway

async def test_core_never_agent_never_reaches_cloud_backend(monkeypatch):
    """Агент cloud_policy=never в режиме Gateway: облачный backend НЕ вызывается.

    Это и есть та утечка, которую нашёл red-team. Поднимаем настоящий Gateway
    (FastAPI) поверх поддельных backend'ов и считаем, сколько раз дёрнули облако.
    """
    import httpx
    from bossman import llm
    from bossman.agents import AgentSpec
    from bossman.config import settings
    from bossman.gateway.app import create_gateway_app
    from bossman.gateway.config import (AliasConfig, BackendConfig, ClientConfig,
                                        GatewayConfig, ModelTarget)

    cloud_hits = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "cloud" in str(request.url):
            cloud_hits["n"] += 1
            return httpx.Response(200, json={"choices": [{"message": {"content": "из облака"}}]})
        # локальный backend «падает», вынуждая обычный роутер к fallback на облако
        return httpx.Response(503, json={"error": "local down"})

    transport = httpx.MockTransport(handler)
    cfg = GatewayConfig(
        backends={"ollama": BackendConfig("ollama", "http://local", cloud=False),
                  "openrouter": BackendConfig("openrouter", "http://cloud", cloud=True)},
        aliases={"bossman-smart": AliasConfig("bossman-smart", [
            ModelTarget("ollama", "qwen", 10, {"text"}),
            ModelTarget("openrouter", "gpt-4o", 100, {"text"})])},
        clients={"core": ClientConfig("core", key=None)},
        allow_unauthenticated_loopback=True,
    )
    from bossman.gateway.backends import OpenAIBackend
    backends = {n: OpenAIBackend(c, transport) for n, c in cfg.backends.items()}
    app = create_gateway_app(cfg, router=__import__("bossman.gateway.router", fromlist=["ModelRouter"]).ModelRouter(cfg, backends))

    gw_transport = httpx.ASGITransport(app=app)
    monkeypatch.setattr(settings, "gateway_url", "http://gw/v1", raising=False)
    monkeypatch.setattr(llm, "_gateway", llm.GatewayClient(base_url="http://gw/v1"))
    llm._gateway._client = httpx.AsyncClient(transport=gw_transport)

    # Локальный backend есть, но падает (503). Прежде это вызывало fallback в
    # облако. Теперь для never-агента облако вырезано: локальный отказ НЕ
    # эскалирует наружу. Запрос падает локальной ошибкой — и это правильно.
    never = AgentSpec(name="analyst", title="a", model="bossman-smart", cloud_policy="never")
    with pytest.raises(Exception):
        await llm.chat(never, [{"role": "user", "content": "секретные данные сделки"}])
    assert cloud_hits["n"] == 0, "данные агента never ушли в облако — граница пробита"

    # А теперь тот же агент, но алиас обслуживается ТОЛЬКО облаком: чистый отказ
    # по политике, не отправка. Ядро разворачивает его в CloudDenied.
    cfg.aliases["cloud-only"] = AliasConfig("cloud-only",
                                            [ModelTarget("openrouter", "gpt-4o", 100, {"text"})])
    with pytest.raises(llm.CloudDenied):
        await llm.chat(AgentSpec(name="analyst", title="a", model="cloud-only",
                                 cloud_policy="never"),
                       [{"role": "user", "content": "секрет"}])
    assert cloud_hits["n"] == 0, "cloud-only + never всё равно ушёл в облако"

    await llm._gateway.close()
    llm._gateway = None
