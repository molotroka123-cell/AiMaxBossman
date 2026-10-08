"""Request-scoped privacy carried through threads/async tasks and provider fallbacks."""
from contextlib import contextmanager
from contextvars import ContextVar
import ipaddress
from urllib.parse import urlsplit

_privacy = ContextVar("bossman_execution_privacy", default="public")


@contextmanager
def execution_privacy(level):
    if level not in ("private", "local_only", "internal", "public"):
        raise ValueError("invalid execution privacy")
    current = _privacy.get()
    effective = current if current in ("private", "local_only") else level
    token = _privacy.set(effective)
    try:
        yield
    finally:
        _privacy.reset(token)


def assert_provider_egress(kind: str, url: str):
    if _privacy.get() not in ("private", "local_only"):
        return
    if kind in ("openrouter", "anthropic", "openai", "gemini"):
        raise PermissionError("privacy requires a local provider")
    host = urlsplit(url).hostname or ""
    if host in ("localhost", "ollama", "llamacpp", "host.docker.internal"):
        return
    try:
        addr = ipaddress.ip_address(host)
        # Judge [::ffff:a.b.c.d] by its IPv4 address: CPython <= 3.12.3 called the mapped
        # metadata endpoint [::ffff:169.254.169.254] private-and-not-link-local.
        addr = getattr(addr, "ipv4_mapped", None) or addr
        if (addr.is_loopback or addr.is_private) and not (addr.is_link_local or addr.is_unspecified):
            return
    except ValueError:
        pass
    raise PermissionError("private context egress blocked at provider boundary")
