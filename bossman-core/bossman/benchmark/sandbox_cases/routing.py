"""REAL_SANDBOX cases for the production routing boundary: ModelRouter alias->target
selection, the FastAPI gateway failover/policy path, the deterministic fast/heavy
compute tier, and the cost_control budget gate that guards every cloud route."""
from __future__ import annotations

import asyncio
import gc
import os
import tempfile
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import httpx

from ..sandbox_row import CaseProbe


def _cfg(aliases: dict, *, served: bool = False):
    """Real GatewayConfig: one declared-local and one declared-cloud backend."""
    from bossman.gateway.config import BackendConfig, ClientConfig, GatewayConfig
    kw = ({"clients": {"core": ClientConfig("core", key=None)}, "allow_unauthenticated_loopback": True}
          if served else {})
    return GatewayConfig(
        backends={"ollama": BackendConfig(name="ollama", base_url="http://local", cloud=False),
                  "openrouter": BackendConfig(name="openrouter", base_url="http://cloud",
                                              kind="openrouter", cloud=True)},
        aliases=aliases, **kw)


def _router(cfg, handler):
    """Real ModelRouter over real OpenAIBackends bound to a MockTransport (no socket)."""
    from bossman.gateway.backends import OpenAIBackend
    from bossman.gateway.router import ModelRouter
    transport = httpx.MockTransport(handler)
    return ModelRouter(cfg, {n: OpenAIBackend(c, transport) for n, c in cfg.backends.items()})


def _asgi(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw")


async def _close_all(*routers) -> None:
    for r in routers:
        await r.close()


@contextmanager
def _isolated_ledger(tmp: Path):
    """Private cost_control ledger for one case.

    ``gateway/app.py`` reads ``cost_control.runtime.STORE/GOVERNOR`` lazily on every
    cloud attempt, so rebinding them puts the real budget hook in front of the real
    router while never touching this machine's own ledger.
    """
    # NB: BOSSMAN_COST_DB is deliberately NOT touched.  Setting it before the
    # first import binds the module-level STORE to this temp directory for the
    # whole process; once the directory is removed every later user of the
    # global store fails with "unable to open database file".  Rebinding
    # rt.STORE/rt.GOVERNOR is sufficient because app.py reads them lazily.
    import bossman.cost_control.runtime as rt
    from bossman.cost_control.governor import CostGovernor
    from bossman.cost_control.store import SQLiteBudgetStore
    events: list[str] = []
    store = SQLiteBudgetStore(tmp / "cost.db")
    saved = (rt.STORE, rt.GOVERNOR)
    rt.STORE = store
    rt.GOVERNOR = CostGovernor(store, lambda kind, **d: events.append(kind))
    try:
        yield store, rt.GOVERNOR, events
    finally:
        rt.STORE, rt.GOVERNOR = saved
        gc.collect()   # sqlite connections are per-call; let Windows unlink the temp db


# --------------------------------------------------------------- model_selection
def model_selection(seed: int) -> dict:
    probe = CaseProbe("sandbox.model_selection", "model_selection", seed)
    from bossman.gateway.backends import CircuitOpenError
    from bossman.gateway.config import AliasConfig, ModelTarget
    from bossman.gateway.router import CloudPolicyDenied, RouteNotFound

    cfg = _cfg({
        "bossman-smart": AliasConfig("bossman-smart", [
            ModelTarget("ollama", "qwen2.5:14b", 10, {"text", "tools"}),
            ModelTarget("openrouter", "anthropic/claude-opus-5", 100, {"text", "tools"})]),
        "bossman-vision": AliasConfig("bossman-vision", [
            ModelTarget("ollama", "llava", 10, {"text", "vision"})], {"vision"}),
        "cloud-only": AliasConfig("cloud-only", [ModelTarget("openrouter", "gpt-4o", 10, {"text"})])})
    router = _router(cfg, lambda r: httpx.Response(200, json={}))
    try:
        routes = router.resolve("bossman-smart", cloud_allowed=True)
        probe.positive("priority_order", [r.backend_name for r in routes], ["ollama", "openrouter"])
        probe.positive("declared_cloud_flags", [r.is_cloud for r in routes], [False, True])
        probe.positive("cloud_denied_keeps_local_targets",
                       [(r.backend_name, r.model) for r in router.resolve("bossman-smart", cloud_allowed=False)],
                       [("ollama", "qwen2.5:14b")])
        probe.positive("capability_filtered_target",
                       [r.model for r in router.resolve("bossman-vision", {"vision"})], ["llava"])
        probe.positive("model_catalog", [(m["id"], m["capabilities"]) for m in router.list_models()],
                       [("bossman-smart", ["text", "tools"]), ("bossman-vision", ["text", "vision"]),
                        ("cloud-only", ["text"])])
        # a backend really probed unhealthy sinks below a lower-priority healthy one
        router.backends["ollama"].health.healthy = False
        router.backends["ollama"].health.checked_at = 1.0
        probe.positive("probed_unhealthy_backend_demoted",
                       [r.backend_name for r in router.resolve("bossman-smart")], ["openrouter", "ollama"])
        router.backends["ollama"].health.healthy = True

        probe.refused("unknown_alias_refused", lambda: router.resolve("no-such-alias"),
                      RouteNotFound, contains="Unknown model alias")
        probe.refused("unsatisfiable_capability_refused",
                      lambda: router.resolve("bossman-smart", {"embeddings"}),
                      RouteNotFound, contains="embeddings")
        probe.refused("alias_required_capability_not_widened",
                      lambda: router.resolve("bossman-vision", {"tools"}), RouteNotFound, contains="tools")
        probe.refused("cloud_only_alias_policy_denied",
                      lambda: router.resolve("cloud-only", cloud_allowed=False),
                      CloudPolicyDenied, contains="'cloud-only'")
        for backend in router.backends.values():          # trip every real circuit breaker
            for _ in range(backend.config.circuit_failure_threshold):
                backend.breaker.record_failure("HTTP 500")
        probe.refused("all_breakers_open_refused", lambda: router.resolve("bossman-smart"),
                      CircuitOpenError, contains="ollama/qwen2.5:14b")
        router.backends["ollama"].breaker.record_success()          # real breaker recovery
        probe.positive("breaker_recovery_restores_target",
                       [r.backend_name for r in router.resolve("bossman-smart")], ["ollama"])
        probe.count(recoveries=1)
    finally:
        asyncio.run(router.close())
    probe.tag("GATEWAY-MODEL-ROUTER", "NO-NETWORK")
    return probe.finish()


# ----------------------------------------------------------------------- router
def router(seed: int) -> dict:
    probe = CaseProbe("sandbox.router", "router", seed)
    from bossman.gateway.app import create_gateway_app
    from bossman.gateway.config import AliasConfig, ModelTarget

    hits = {"local": 0, "cloud": 0}
    rejected = {"local": 0, "cloud": 0}

    def down(request: httpx.Request) -> httpx.Response:
        where = "local" if "local" in str(request.url) else "cloud"
        hits[where] += 1
        if where == "local":
            return httpx.Response(503, json={"error": "local down"})    # 5xx -> failover allowed
        return httpx.Response(200, json={"model": "upstream-name", "choices": [{"message": {"content": "ok"}}],
                                         "usage": {"prompt_tokens": 10, "completion_tokens": 5}})

    def bad_request(request: httpx.Request) -> httpx.Response:
        rejected["local" if "local" in str(request.url) else "cloud"] += 1
        return httpx.Response(400, json={"error": "bad request"})       # 4xx -> must NOT escalate

    cfg = _cfg({"bossman-smart": AliasConfig("bossman-smart", [
                    ModelTarget("ollama", "qwen-fast", 10, {"text"}),
                    ModelTarget("openrouter", "anthropic/claude-opus-5", 100, {"text"})]),
                "cloud-only": AliasConfig("cloud-only", [ModelTarget("openrouter", "gpt-4o", 10, {"text"})])},
               served=True)
    r1, r2 = _router(cfg, down), _router(cfg, bad_request)
    app, app4xx = create_gateway_app(cfg, router=r1), create_gateway_app(cfg, router=r2)
    body = {"model": "bossman-smart", "max_tokens": 32, "messages": [{"role": "user", "content": "hi"}]}

    async def drive() -> None:
        async with _asgi(app) as c, _asgi(app4xx) as c4:
            ok = await c.post("/v1/chat/completions", json=body,
                              headers={"X-Bossman-Cloud-Allowed": "1", "X-Request-Id": f"req-{seed}"})
            probe.positive("failover_served_by_next_target",
                           (ok.status_code, ok.headers["x-bossman-backend"], ok.headers["x-bossman-route-model"]),
                           (200, "openrouter", "anthropic/claude-opus-5"))
            probe.positive("cloud_audit_and_alias_rewrite",
                           (ok.headers["x-bossman-cloud"], ok.json()["model"], ok.headers["x-request-id"]),
                           ("1", "bossman-smart", f"req-{seed}"))
            probe.positive("both_tiers_attempted_once", dict(hits), {"local": 1, "cloud": 1})
            closed = await c.post("/v1/chat/completions", json=body)    # header missing = fail closed
            probe.negative("missing_cloud_header_is_fail_closed", (closed.status_code, hits["cloud"]), (502, 1))
            denied = await c.post("/v1/chat/completions", headers={"X-Bossman-Cloud-Allowed": "0"},
                                  json={**body, "model": "cloud-only"})
            probe.negative("cloud_only_alias_policy_denied",
                           (denied.status_code, denied.json()["error"]["code"], hits["cloud"]),
                           (403, "POLICY_DENIED", 1))
            missing = await c.post("/v1/chat/completions", headers={"X-Bossman-Cloud-Allowed": "1"},
                                   json={**body, "model": "ghost-alias"})
            probe.negative("unknown_alias_not_routed",
                           (missing.status_code, "Unknown model alias" in missing.text), (404, True))
            four = await c4.post("/v1/chat/completions", headers={"X-Bossman-Cloud-Allowed": "1"}, json=body)
            probe.negative("request_error_never_escalates_to_cloud",
                           (four.status_code, dict(rejected)), (400, {"local": 1, "cloud": 0}))
            for backend in r1.backends.values():
                for _ in range(backend.config.circuit_failure_threshold):
                    backend.breaker.record_failure("HTTP 503")
            dead = await c.post("/v1/chat/completions", headers={"X-Bossman-Cloud-Allowed": "1"}, json=body)
            probe.negative("open_breakers_refuse_without_backend_call",
                           (dead.status_code, dead.json()["error"]["code"], hits["cloud"]),
                           (503, "NO_BACKENDS_AVAILABLE", 1))

    # the private ledger holds no policy, so the cost hook is a pass-through and this
    # case measures routing alone (see sandbox.budget_router for the budget gate)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, \
            _isolated_ledger(Path(tmp)) as (store, _gov, _events):
        probe.positive("no_invented_budget_limit", store.has_enabled_policies(), False)
        try:
            asyncio.run(drive())
        finally:
            asyncio.run(_close_all(r1, r2))
    probe.count(effects=1, recoveries=1)   # one upstream answer, reached by real failover
    probe.tag("GATEWAY-APP", "FAILOVER", "FAIL-CLOSED")
    return probe.finish()


# ------------------------------------------------------------ fast_heavy_policy
def fast_heavy_policy(seed: int) -> dict:
    probe = CaseProbe("sandbox.fast_heavy_policy", "fast_heavy_policy", seed)
    from bossman.compute_budget import (MANDATORY_ACTIONS, ComputeLevel, evr, may_skip,
                                        select_level, should_continue_reasoning, voi)
    from bossman.signals import DecisionSignals, derive_signals

    cheap = derive_signals("add two numbers")
    probe.positive("cheap_signals_derived", (cheap.task_complexity, cheap.risk), (0.0, 0.0))
    probe.positive("cheap_task_picks_fast_tier", select_level(cheap)[0].name, "C0_FAST")
    heavy = derive_signals("delete prod database and send email to owner",
                           previous_failures=2, tool_count=4)
    probe.positive("irreversible_markers_raise_risk",
                   (round(heavy.risk, 6), heavy.task_complexity), (0.9, 0.4))
    heavy_level, heavy_reasons = select_level(heavy)
    probe.positive("high_consequence_escalates_to_max_verification",
                   (heavy_level.name, len(heavy_reasons) >= 1), ("C4_MAX_VERIFICATION", True))
    probe.positive("escalation_ladder",
                   [select_level(s)[0].name for s in (DecisionSignals(uncertainty=0.8),
                                                      DecisionSignals(task_complexity=0.7),
                                                      DecisionSignals(task_complexity=0.4))],
                   ["C3_MULTI_CANDIDATE", "C2_DEEP", "C1_NORMAL"])
    probe.positive("starved_budget_capped_at_normal",
                   select_level(DecisionSignals(uncertainty=0.9, resource_budget=0.05))[0].name, "C1_NORMAL")
    probe.positive("high_risk_beats_budget_starvation",
                   select_level(DecisionSignals(uncertainty=0.9, resource_budget=0.05, risk=0.9))[0].name,
                   "C4_MAX_VERIFICATION")
    probe.positive("frozen_signals_copied_not_mutated",
                   (heavy.with_(uncertainty=0.3).uncertainty, heavy.uncertainty), (0.3, 0.0))
    probe.positive("optional_work_skippable_when_voi_negative",
                   (round(voi(0.9, 0.2, 0.05), 6), may_skip("retrieval", -0.48)), (-0.75, True))

    negative_evr = round(evr(0.1, delta_quality=0.2, token_cost=0.5), 6)
    probe.negative("no_extra_compute_without_positive_expected_value",
                   (negative_evr, should_continue_reasoning(negative_evr)), (-0.48, False))
    probe.negative("fast_tier_refused_for_high_consequence",
                   heavy_level is ComputeLevel.C0_FAST or heavy_level <= ComputeLevel.C1_NORMAL, False)
    probe.negative("mandatory_verification_never_skipped_by_economics",
                   ([may_skip(a, -1.0) for a in sorted(MANDATORY_ACTIONS)], sorted(MANDATORY_ACTIONS)),
                   ([False] * 5, ["approval", "egress_guard", "ingest_guard",
                                  "safety_verification", "security_verification"]))
    probe.refused("non_signal_input_refused", lambda: select_level("not a DecisionSignals"),
                  AttributeError, contains="risk")
    probe.tag("COMPUTE-BUDGET", "DETERMINISTIC-TIERS")
    return probe.finish()


# ---------------------------------------------------------------- budget_router
def budget_router(seed: int) -> dict:
    probe = CaseProbe("sandbox.budget_router", "budget_router", seed)
    from bossman.cost_control.enforcer import BudgetEnforcer, BudgetHardStop
    from bossman.cost_control.models import BudgetContext, BudgetPolicy, BudgetScope, HardLimitAction
    from bossman.cost_control.pricing import UnknownPricing, estimate_usd, normalize_per_token_price
    from bossman.cost_control.store import BudgetError, BudgetExtensionRequired
    from bossman.gateway.app import create_gateway_app
    from bossman.gateway.config import AliasConfig, ModelTarget

    def bucket(store, scope: str) -> dict:
        return next(b for b in store.snapshots() if b["scope"] == scope)

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, \
            _isolated_ledger(Path(tmp)) as (store, gov, events):
        store.set_policy(BudgetPolicy(BudgetScope.DAILY_GLOBAL, Decimal("3.00"),
                                      hard_action=HardLimitAction.STOP))
        ctx = BudgetContext(run_id=f"run-{seed}", owner_device_id="core")
        est = estimate_usd(prompt_tokens_upper=1000, completion_tokens_upper=500,
                           prompt_price_per_token=Decimal("0.000001"),
                           completion_price_per_token=Decimal("0.000005"))
        probe.positive("upper_bound_estimate", (str(est), store.has_enabled_policies()), ("0.003500", True))
        key = f"req-{seed}:0:openrouter:gpt-4o"
        allow = gov.reserve_cloud_call(ctx, est, idempotency_key=key, cloud_allowed=True)
        probe.positive("reservation_granted_inside_limit",
                       (allow.kind.value, allow.reservation.status.value, str(allow.reservation.estimated_usd)),
                       ("allow", "active", "0.003500"))
        committed = gov.commit(allow.reservation.id, est)
        probe.positive("commit_moves_hold_to_spend",
                       (committed.status.value, bucket(store, "daily_global")["spent_usd"],
                        bucket(store, "daily_global")["reserved_usd"]), ("committed", "0.003500", "0.000000"))
        held = gov.reserve_cloud_call(ctx, Decimal("0.50"), idempotency_key=f"req-{seed}:1", cloud_allowed=True)
        probe.positive("hold_is_visible_in_ledger", bucket(store, "daily_global")["reserved_usd"], "0.500000")
        released = gov.release(held.reservation.id)
        probe.positive("release_returns_hold_to_pool",   # release writes the bare zero
                       (released.status.value, bucket(store, "daily_global")["reserved_usd"]), ("released", "0"))

        no_cloud = gov.reserve_cloud_call(ctx, Decimal("0.10"), idempotency_key=f"req-{seed}:2",
                                          cloud_allowed=False)
        probe.negative("cloud_forbidden_denied_before_ledger_write",
                       (no_cloud.kind.value, no_cloud.reason), ("deny", "cloud policy forbids external call"))
        dup = gov.reserve_cloud_call(ctx, est, idempotency_key=key, cloud_allowed=True)
        probe.negative("replayed_billable_key_denied", (dup.kind.value, dup.reason),
                       ("deny", "duplicate billable attempt (committed)"))
        over = gov.reserve_cloud_call(ctx, Decimal("100"), idempotency_key=f"req-{seed}:3", cloud_allowed=True)
        probe.negative("past_hard_limit_denied",
                       (over.kind.value, over.reason, over.required_extra_usd > 0, events[-1]),
                       ("deny", "hard budget exceeded", True, "budget.exceeded"))
        probe.refused("negative_money_refused",
                      lambda: gov.reserve_cloud_call(ctx, Decimal("-1"), idempotency_key="neg",
                                                     cloud_allowed=True),
                      ValueError, contains="finite and non-negative")
        probe.refused("unbounded_idempotency_key_refused",
                      lambda: store.reserve(ctx, Decimal("0.10"), idempotency_key=""),
                      ValueError, contains="bounded idempotency_key")
        probe.refused("unknown_reservation_commit_refused", lambda: gov.commit("br_missing", Decimal("0.01")),
                      BudgetError, contains="unknown reservation")
        small = gov.reserve_cloud_call(ctx, Decimal("0.01"), idempotency_key=f"req-{seed}:4", cloud_allowed=True)
        probe.refused("overspend_commit_requires_extension",
                      lambda: gov.commit(small.reservation.id, Decimal("2.5")),
                      BudgetExtensionRequired, contains="exceeds reservation")
        gov.release(small.reservation.id)
        probe.refused("unknown_pricing_refused", lambda: normalize_per_token_price("cheap-ish"),
                      UnknownPricing, contains="invalid provider price")
        enforcer = BudgetEnforcer(gov, None, None)   # approvals are unreachable on the STOP path
        probe.refused("enforcer_hard_stop_on_deny",
                      lambda: asyncio.run(enforcer.reserve(ctx, Decimal("50"), idempotency_key=f"req-{seed}:5",
                                                           cloud_allowed=True)),
                      BudgetHardStop, contains="hard budget exceeded")

        # ---- the wired path: the gateway's budget hook in front of a real cloud route
        upstream = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            upstream["n"] += 1
            return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}],
                                             "usage": {"prompt_tokens": 100, "completion_tokens": 50}})

        cfg = _cfg({"cloud-priced": AliasConfig("cloud-priced", [
                        ModelTarget("openrouter", "gpt-4o", 10, {"text"},
                                    price_usd_per_million_input_tokens="1",
                                    price_usd_per_million_output_tokens="2")]),
                    "cloud-unpriced": AliasConfig("cloud-unpriced", [
                        ModelTarget("openrouter", "gpt-4o-mystery", 10, {"text"})])}, served=True)
        gw_router = _router(cfg, handler)
        app = create_gateway_app(cfg, router=gw_router)
        store.set_policy(BudgetPolicy(BudgetScope.RUN, Decimal("0.000001"), hard_action=HardLimitAction.STOP))
        before = Decimal(bucket(store, "daily_global")["spent_usd"])
        payload = {"model": "cloud-priced", "max_tokens": 100,
                   "messages": [{"role": "user", "content": "hello"}]}

        async def drive() -> None:
            async with _asgi(app) as c:
                c.headers["x-bossman-cloud-allowed"] = "1"
                ok = await c.post("/v1/chat/completions", json=payload)   # no run id -> daily scope only
                probe.positive("priced_cloud_route_settled_from_real_usage",
                               (ok.status_code, ok.headers["x-bossman-cloud"], upstream["n"],
                                str(Decimal(bucket(store, "daily_global")["spent_usd"]) - before),
                                bucket(store, "daily_global")["reserved_usd"]),
                               (200, "1", 1, "0.000200", "0.000000"))
                stopped = await c.post("/v1/chat/completions", json=payload,
                                       headers={"x-run-id": f"poor-{seed}"})   # run bucket is 1e-6 USD
                probe.negative("hard_stop_blocks_cloud_call_before_the_network",
                               (stopped.status_code, "BudgetHardStop" in stopped.text, upstream["n"]),
                               (502, True, 1))
                unpriced = await c.post("/v1/chat/completions", json={**payload, "model": "cloud-unpriced"})
                probe.negative("unknown_price_fails_closed",
                               (unpriced.status_code, "BudgetPricingUnknown" in unpriced.text, upstream["n"]),
                               (502, True, 1))

        try:
            asyncio.run(drive())
        finally:
            asyncio.run(gw_router.close())
        probe.count(effects=2, recoveries=1)   # committed spend + settled cloud call; released hold
    probe.tag("COST-GOVERNOR", "GATEWAY-BUDGET-HOOK", "FAIL-CLOSED")
    return probe.finish()


CASES = {
    "sandbox.model_selection": model_selection,
    "sandbox.router": router,
    "sandbox.fast_heavy_policy": fast_heavy_policy,
    "sandbox.budget_router": budget_router,
}
