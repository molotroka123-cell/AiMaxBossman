from social_farm.generation.higgsfield_browser_contracts import MediaKind
from social_farm.generation.media_router import (
    GenerationRoute,
    RouteCandidate,
    RoutingContext,
    choose_generation_route,
)


def candidate(route: GenerationRoute, **kwargs):
    defaults = dict(
        route=route,
        supported_kinds=frozenset({MediaKind.IMAGE, MediaKind.VIDEO}),
        healthy=True,
        available=True,
        estimated_cost_usd=0.01,
        estimated_latency_s=5.0,
        quality_score=0.7,
        risk_score=0.1,
        required_memory_gb=0.0,
        browser_owner_auth_ready=True,
    )
    defaults.update(kwargs)
    return RouteCandidate(**defaults)


def test_router_rejects_local_model_when_memory_is_insufficient() -> None:
    decision = choose_generation_route(
        RoutingContext(MediaKind.VIDEO, safe_available_memory_gb=20, max_cost_usd=1.0),
        [
            candidate(GenerationRoute.LOCAL, required_memory_gb=48, quality_score=0.95),
            candidate(GenerationRoute.HIGGSFIELD_BROWSER, quality_score=0.85),
        ],
    )
    assert decision.selected is GenerationRoute.HIGGSFIELD_BROWSER
    assert "local:memory" in decision.rejected


def test_router_rejects_browser_when_owner_auth_is_not_ready() -> None:
    decision = choose_generation_route(
        RoutingContext(MediaKind.IMAGE, safe_available_memory_gb=64, max_cost_usd=1.0),
        [candidate(GenerationRoute.HIGGSFIELD_BROWSER, browser_owner_auth_ready=False)],
    )
    assert decision.selected is GenerationRoute.DEFER
    assert "higgsfield_browser:owner_auth" in decision.rejected


def test_router_prefers_higher_quality_when_within_budget() -> None:
    decision = choose_generation_route(
        RoutingContext(MediaKind.IMAGE, safe_available_memory_gb=64, max_cost_usd=0.2),
        [
            candidate(GenerationRoute.LOCAL, quality_score=0.55, estimated_cost_usd=0.0),
            candidate(GenerationRoute.CHEAP_CLOUD, quality_score=0.8, estimated_cost_usd=0.02),
        ],
    )
    assert decision.selected is GenerationRoute.CHEAP_CLOUD


def test_router_defers_when_all_routes_violate_policy_or_budget() -> None:
    decision = choose_generation_route(
        RoutingContext(
            MediaKind.VIDEO,
            safe_available_memory_gb=4,
            max_cost_usd=0.001,
            allow_browser=False,
            allow_cloud=False,
            allow_local=False,
        ),
        [
            candidate(GenerationRoute.LOCAL),
            candidate(GenerationRoute.CHEAP_CLOUD),
            candidate(GenerationRoute.HIGGSFIELD_BROWSER),
        ],
    )
    assert decision.selected is GenerationRoute.DEFER
    assert len(decision.rejected) == 3
