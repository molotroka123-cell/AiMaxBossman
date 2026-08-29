from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(slots=True)
class BackendConfig:
    name: str
    base_url: str
    api_key_env: str | None = None
    api_key: str | None = None
    timeout_seconds: float = 120.0
    max_concurrency: int = 2
    enabled: bool = True
    kind: str = "openai"
    health_path: str = "/v1/models"
    extra_headers: dict[str, str] = field(default_factory=dict)
    # Облачность объявляется явно, а не угадывается по имени: это источник
    # истины для облачной политики. Backend без флага считается локальным —
    # ошибиться в сторону «локальный» безопасно, потому что забытый флаг у
    # настоящего облака поймает второй барьер (ключ агента / сеть без интернета).
    cloud: bool = False
    # Health-probe живёт на коротком собственном таймауте, а не на таймауте
    # инференса: чёрная дыра в сети не должна вешать /health на 120 секунд.
    health_timeout_seconds: float = 4.0
    # Circuit breaker: N неудач подряд (транспорт/таймаут/5xx) размыкают
    # автомат на cooldown секунд. Клиентские 4xx автомат не двигают.
    circuit_failure_threshold: int = 3
    circuit_cooldown_seconds: float = 30.0

    def resolved_api_key(self) -> str | None:
        if self.api_key_env:
            return os.getenv(self.api_key_env) or self.api_key
        return self.api_key


@dataclass(slots=True)
class ModelTarget:
    backend: str
    model: str
    priority: int = 100
    capabilities: set[str] = field(default_factory=set)
    context_window: int | None = None
    max_output_tokens: int | None = None


@dataclass(slots=True)
class AliasConfig:
    name: str
    targets: list[ModelTarget] = field(default_factory=list)
    required_capabilities: set[str] = field(default_factory=set)


@dataclass(slots=True)
class ClientConfig:
    name: str
    key_env: str | None = None
    key: str | None = None
    requests_per_minute: int = 60
    burst: int = 10
    allowed_aliases: set[str] = field(default_factory=lambda: {"*"})

    def resolved_key(self) -> str | None:
        if self.key_env:
            return os.getenv(self.key_env) or self.key
        return self.key


@dataclass(slots=True)
class GatewayConfig:
    backends: dict[str, BackendConfig] = field(default_factory=dict)
    aliases: dict[str, AliasConfig] = field(default_factory=dict)
    clients: dict[str, ClientConfig] = field(default_factory=dict)
    bind_host: str = "127.0.0.1"
    bind_port: int = 8765
    allow_unauthenticated_loopback: bool = False
    queue_timeout_seconds: float = 300.0
    health_ttl_seconds: float = 15.0
    metrics_enabled: bool = True
    request_body_limit_bytes: int = 8 * 1024 * 1024


def _set(value: Any) -> set[str]:
    if not value:
        return set()
    return {str(x) for x in value}


def load_gateway_config(path: str | Path | None = None) -> GatewayConfig:
    path = Path(path or os.getenv("BOSSMAN_GATEWAY_CONFIG", "config/gateway.yaml"))
    raw: dict[str, Any] = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    backends: dict[str, BackendConfig] = {}
    for name, cfg in (raw.get("backends") or {}).items():
        backends[name] = BackendConfig(name=name, **(cfg or {}))

    aliases: dict[str, AliasConfig] = {}
    for name, cfg in (raw.get("aliases") or {}).items():
        cfg = cfg or {}
        targets = []
        for t in cfg.get("targets", []):
            targets.append(ModelTarget(
                backend=str(t["backend"]),
                model=str(t["model"]),
                priority=int(t.get("priority", 100)),
                capabilities=_set(t.get("capabilities")),
                context_window=t.get("context_window"),
                max_output_tokens=t.get("max_output_tokens"),
            ))
        aliases[name] = AliasConfig(
            name=name,
            targets=targets,
            required_capabilities=_set(cfg.get("required_capabilities")),
        )

    clients: dict[str, ClientConfig] = {}
    for name, cfg in (raw.get("clients") or {}).items():
        cfg = cfg or {}
        clients[name] = ClientConfig(
            name=name,
            key_env=cfg.get("key_env"),
            key=cfg.get("key"),
            requests_per_minute=int(cfg.get("requests_per_minute", 60)),
            burst=int(cfg.get("burst", 10)),
            allowed_aliases=_set(cfg.get("allowed_aliases")) or {"*"},
        )

    server = raw.get("server") or {}
    return GatewayConfig(
        backends=backends,
        aliases=aliases,
        clients=clients,
        bind_host=str(server.get("host", "127.0.0.1")),
        bind_port=int(server.get("port", 8765)),
        allow_unauthenticated_loopback=bool(server.get("allow_unauthenticated_loopback", False)),
        queue_timeout_seconds=float(server.get("queue_timeout_seconds", 300)),
        health_ttl_seconds=float(server.get("health_ttl_seconds", 15)),
        metrics_enabled=bool(server.get("metrics_enabled", True)),
        request_body_limit_bytes=int(server.get("request_body_limit_bytes", 8 * 1024 * 1024)),
    )
