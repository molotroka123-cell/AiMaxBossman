from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from .backends import CircuitOpenError, OpenAIBackend, build_backend
from .config import GatewayConfig, ModelTarget, glm_model_id


class RouteNotFound(RuntimeError):
    pass


class CloudPolicyDenied(RuntimeError):
    """Алиас обслуживается только облаком, а облако запрещено политикой.

    Это НЕ «маршрут не найден»: маршрут есть, но он ведёт наружу, а владелец
    объявил, что данные наружу не уходят. Отдаётся как отдельный исход, чтобы
    ядро отличило «нечем обслужить» от «политика запретила отправку»."""
    pass


# Прямая адресация провайдера идентификатором модели, без алиаса в yaml.
# Владелец называет модель так, как она называется у провайдера, и это и есть
# маршрут: «openrouter/<id>» — через OpenRouter, «glm-5.3» (GLM_MODEL_ID) —
# напрямую в Z.ai. Алиасы остаются главнее: их правила писал оператор.
DIRECT_BACKEND_PREFIXES = {"openrouter/": "openrouter"}


def direct_target(alias: str) -> tuple[str, str] | None:
    """(бэкенд, id модели у провайдера) для прямой адресации, иначе None."""
    for prefix, backend in DIRECT_BACKEND_PREFIXES.items():
        if alias.startswith(prefix) and len(alias) > len(prefix):
            return backend, alias[len(prefix):]
    if alias == glm_model_id():
        return "zai", alias
    return None


@dataclass(slots=True)
class Route:
    alias: str
    backend_name: str
    model: str
    target: ModelTarget
    backend: OpenAIBackend
    is_cloud: bool = False


class ModelRouter:
    def __init__(self, config: GatewayConfig, backends: dict[str, OpenAIBackend] | None = None):
        self.config = config
        self.backends = backends or {name: build_backend(cfg) for name, cfg in config.backends.items() if cfg.enabled}

    async def close(self) -> None:
        await asyncio.gather(*(b.close() for b in self.backends.values()), return_exceptions=True)

    async def refresh_health(self, force: bool = False) -> dict[str, dict]:
        now = time.time()
        async def one(name: str, backend: OpenAIBackend):
            if force or now - backend.health.checked_at >= self.config.health_ttl_seconds:
                await backend.probe()
            return name, {
                "healthy": backend.health.healthy,
                "checked_at": backend.health.checked_at,
                "error": backend.health.error,
                "latency_ms": backend.health.latency_ms,
            }
        pairs = await asyncio.gather(*(one(n,b) for n,b in self.backends.items()))
        return dict(pairs)

    def resolve(self, alias: str, required_capabilities: set[str] | None = None,
                cloud_allowed: bool = True) -> list[Route]:
        """Маршруты под алиас.

        cloud_allowed=False (облачная политика never, либо ask без подтверждения)
        ВЫРЕЗАЕТ облачные цели ещё до сети: политику держит сам Gateway, а не
        надежда, что ядро правильно угадает облачность по имени алиаса. Если
        после этого не осталось ни одной цели — CloudPolicyDenied: данные не
        уходят никуда.
        """
        cfg = self.config.aliases.get(alias)
        if not cfg:
            direct = self._direct_route(alias, cloud_allowed)
            if direct is not None:
                return direct
            raise RouteNotFound(f"Unknown model alias: {alias}")
        required = set(required_capabilities or ()) | cfg.required_capabilities
        candidates = []
        skipped_open: list[str] = []
        dropped_cloud = False
        for t in sorted(cfg.targets, key=lambda x: x.priority):
            backend = self.backends.get(t.backend)
            if not backend:
                continue
            if required and not required.issubset(t.capabilities):
                continue
            is_cloud = bool(getattr(backend.config, "cloud", False))
            if is_cloud and not cloud_allowed:
                dropped_cloud = True
                continue        # облачная цель при запрете облака — мимо, не в сеть
            if not backend.breaker.allow_attempt():
                # Разомкнутый автомат: цель ПРОПУСКАЕТСЯ, а не деприоритизируется
                # (иначе каждый запрос снова платит её полный таймаут). В
                # HALF_OPEN это одна пробная попытка; провал переоткроет автомат.
                skipped_open.append(f"{t.backend}/{t.model}")
                continue
            candidates.append(Route(alias, t.backend, t.model, t, backend, is_cloud))
        if not candidates:
            if dropped_cloud:
                raise CloudPolicyDenied(
                    f"алиас '{alias}' обслуживается только облаком, а облачная "
                    f"политика это запрещает — данные не отправлены")
            if skipped_open:
                raise CircuitOpenError(
                    f"все цели алиаса '{alias}' разомкнуты автоматом "
                    f"({', '.join(skipped_open)}) — отказ сразу, бэкенды не дёргаем")
            raise RouteNotFound(f"No configured target for alias '{alias}' with capabilities {sorted(required)}")
        # healthy targets first; unchecked targets are optimistically usable
        return sorted(candidates, key=lambda r: (r.backend.health.checked_at > 0 and not r.backend.health.healthy, r.target.priority))

    def _direct_route(self, alias: str, cloud_allowed: bool) -> list[Route] | None:
        """Маршрут по идентификатору модели провайдера, если такой провайдер поднят.

        Возможности цели не проверяются: владелец назвал КОНКРЕТНУЮ модель, и
        подменять её на другую по несовпадению объявленных capability было бы
        решением за него. Облачная политика, наоборот, действует как обычно —
        прямая адресация не обходной путь для запрета облака.
        """
        target = direct_target(alias)
        if target is None:
            return None
        backend_name, model = target
        backend = self.backends.get(backend_name)
        if backend is None:
            return None
        is_cloud = bool(getattr(backend.config, "cloud", False))
        if is_cloud and not cloud_allowed:
            raise CloudPolicyDenied(
                f"модель '{alias}' обслуживается только облаком ({backend_name}), "
                f"а облачная политика это запрещает — данные не отправлены")
        if not backend.breaker.allow_attempt():
            raise CircuitOpenError(
                f"бэкенд '{backend_name}' разомкнут автоматом — отказ сразу")
        t = ModelTarget(backend_name, model, 100, set())
        return [Route(alias, backend_name, model, t, backend, is_cloud)]

    def list_models(self) -> list[dict]:
        out = []
        for alias, cfg in self.config.aliases.items():
            out.append({
                "id": alias,
                "object": "model",
                "owned_by": "bossman-gateway",
                "capabilities": sorted(cfg.required_capabilities | set().union(*(t.capabilities for t in cfg.targets))),
            })
        return out
