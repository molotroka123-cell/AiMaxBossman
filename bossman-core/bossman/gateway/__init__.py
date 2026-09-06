"""BOSSMAN Stage 3 — local AI Gateway and model router.

`app` тянет FastAPI, а `config`/`router` — нет. Безусловный импорт приложения
здесь означал, что прочитать настройку (`from bossman.gateway.config import
normalize_ollama_host`) нельзя без веб-фреймворка; корневой набор тестов,
который ставит только общие контракты, падал на сборе.

`create_gateway_app` отдаётся лениво (PEP 562) — вызывающий код не меняется.
"""
from .config import GatewayConfig, load_gateway_config
from .router import ModelRouter

__all__ = ["create_gateway_app", "GatewayConfig", "load_gateway_config", "ModelRouter"]

_LAZY = {"create_gateway_app": (".app", "create_gateway_app")}


def __getattr__(name: str):
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module
    return getattr(import_module(target[0], __name__), target[1])


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY})
