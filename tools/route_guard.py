"""Owner rule for product runtime: local models or OpenRouter ':free' models only.

Tool scripts (YouTube/K1m6a ingest, Motion Studio spec generation) take their
model endpoint from env or CLI. Without this guard a mistaken or hostile value
would send frames, transcripts and the local API key to a paid provider. The check
runs before any network I/O.
"""
from __future__ import annotations

from urllib.parse import urlparse

LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
OPENROUTER_HOSTS = frozenset({"openrouter.ai"})


class RouteViolation(RuntimeError):
    """Endpoint/model is neither local nor an OpenRouter ':free' model."""


def assert_free_or_local(url: str, model: str = "") -> None:
    host = (urlparse(str(url or "")).hostname or "").lower()
    if host in LOCAL_HOSTS:
        return
    if host in OPENROUTER_HOSTS and str(model or "").endswith(":free"):
        return
    raise RouteViolation(
        f"refused: {host or url!r} model {model!r} is not local and not an OpenRouter ':free' model")
