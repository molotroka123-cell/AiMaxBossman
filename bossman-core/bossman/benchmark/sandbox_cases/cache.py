"""Real prompt-cache shaping/usage boundary (bossman.gateway) and the real local
cognitive-reuse gate (bossman.exec_cache + learning_guard.runtime_bridge)."""
from __future__ import annotations

import asyncio
import gc
import itertools
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from ..._shared import cache_observation as _co  # bootstraps repo-root ``bossman_shared``
from ..sandbox_row import CaseProbe

_MODEL = "anthropic/claude-opus-5"
_ALIAS = "smart"
_LONG = "POLICY-BLOCK " * 260          # stable system prefix, > minimum_cacheable_tokens
# ExecutionCache is a process singleton; a per-invocation nonce keeps the measured
# miss->hit delta identical no matter how often the case runs in one process.
_RUN = itertools.count()


@contextmanager
def _isolated_ledger():
    """Private cost_control ledger so this case cannot be denied — or helped —
    by whatever budget state the ambient process happens to carry."""
    import bossman.cost_control.runtime as rt
    from bossman.cost_control.governor import CostGovernor
    from bossman.cost_control.store import SQLiteBudgetStore
    with tempfile.TemporaryDirectory(prefix="bench-cache-ledger-") as tmp:
        store = SQLiteBudgetStore(Path(tmp) / "cost.db")
        saved = (rt.STORE, rt.GOVERNOR)
        rt.STORE, rt.GOVERNOR = store, CostGovernor(store, lambda kind, **d: None)
        try:
            yield store
        finally:
            rt.STORE, rt.GOVERNOR = saved
            gc.collect()


def _body(**usage) -> dict:
    body = {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}
    if usage:
        body["usage"] = usage
    return body


def _build_gateway(script: dict[int, tuple]):
    """Real Gateway app; the only test double is the upstream socket (MockTransport)."""
    import httpx
    from bossman.gateway.app import create_gateway_app
    from bossman.gateway.backends import OpenAIBackend
    from bossman.gateway.config import AliasConfig, BackendConfig, ClientConfig, GatewayConfig, ModelTarget
    from bossman.gateway.router import ModelRouter

    sent: list[dict] = []

    def handler(request: "httpx.Request") -> "httpx.Response":
        sent.append(json.loads(request.content))
        status, payload = script[min(len(sent), max(script))]
        return httpx.Response(status, json=payload)

    cfg = GatewayConfig(
        backends={"openrouter": BackendConfig("openrouter", "http://upstream.invalid",
                                              cloud=True, kind="openrouter")},
        aliases={_ALIAS: AliasConfig(_ALIAS, [ModelTarget("openrouter", _MODEL, 100, {"text"})])},
        clients={"core": ClientConfig("core", key=None)},
        allow_unauthenticated_loopback=True, metrics_enabled=True)
    backends = {n: OpenAIBackend(c, httpx.MockTransport(handler)) for n, c in cfg.backends.items()}
    return create_gateway_app(cfg, router=ModelRouter(cfg, backends)), sent


def prompt_cache(seed: int) -> dict:
    probe = CaseProbe("sandbox.prompt_cache", "prompt_cache", seed)
    import httpx
    from bossman.gateway.prompt_cache import (extract_cache_usage, is_trusted_session_id, normalize_ttl,
                                              prepare_provider_payload, stable_session_id)
    hot = _body(prompt_tokens=1500, completion_tokens=10, prompt_tokens_details={"cached_tokens": 1400})
    cold = _body(prompt_tokens=1500, completion_tokens=10, prompt_tokens_details={"cache_write_tokens": 1400})
    plain = _body(prompt_tokens=100, completion_tokens=5)
    # Scripted upstream: call 6 rejects the OPTIONAL cache metadata (400) -> the
    # Gateway must retry once WITHOUT it (fail-open recovery) and still answer 200.
    script = {1: (200, cold), 2: (200, hot), 3: (200, _body()), 4: (200, plain),
              5: (200, plain), 6: (400, {"error": "unknown parameter: cache_control"}), 7: (200, plain)}
    app, sent = _build_gateway(script)
    s1, s2 = stable_session_id("bench", seed), stable_session_id("bench", seed, "short")
    snaps: list[dict] = []

    async def drive() -> int:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw") as cl:
            async def post(msgs, **hdr):
                r = await cl.post("/v1/chat/completions", json={"model": _ALIAS, "messages": msgs},
                                  headers={"x-bossman-cloud-allowed": "1", **hdr})
                snaps.append((await cl.get("/metrics")).json())
                return r.status_code
            big = [{"role": "system", "content": _LONG}, {"role": "user", "content": "hi"}]
            small = [{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}]
            await post(big, **{"x-bossman-session-id": s1, "x-bossman-cache-ttl": "1h"})
            await post(big, **{"x-bossman-session-id": s1, "x-bossman-cache-ttl": "1h"})
            await post(small, **{"x-bossman-session-id": s2, "x-bossman-cache-ttl": "1h"})
            await post(big, **{"x-bossman-session-id": s1, "x-bossman-cache-ttl": "99h"})
            await post(big, **{"x-bossman-session-id": "../../etc/passwd"})
            return await post(big, **{"x-bossman-session-id": s1, "x-bossman-cache-ttl": "1h"})

    with _isolated_ledger():
        retry_status = asyncio.run(drive())
    pc = [s["prompt_cache"] for s in snaps]

    # Guard first: without it an upstream that was never reached surfaces as an
    # IndexError instead of a readable failed check.
    probe.positive("gateway_reached_upstream_for_every_call", len(sent), 7)
    if len(sent) < 7:
        return probe.finish(upstream_calls=len(sent))

    # -- POSITIVE: the real payload transformation actually reached the provider.
    probe.positive("stable_prefix_got_cache_breakpoint",
                   sent[0]["messages"][0]["content"][0]["cache_control"], {"type": "ephemeral", "ttl": "1h"})
    probe.positive("trusted_session_forwarded_as_affinity_key", sent[0].get("session_id"), s1)
    probe.positive("cold_write_from_provider_counters",
                   (pc[0]["state"], pc[0]["cache_write_tokens"], pc[0]["hits"]), ("COLD", 1400, 0))
    probe.positive("hot_read_from_provider_counters",
                   (pc[1]["state"], pc[1]["cached_tokens"], pc[1]["fresh_input_tokens"], pc[1]["hits"]),
                   ("HOT", 1400, 200, 1))
    probe.positive("prefix_stability_measured_across_session", pc[1]["prefix_stability_percent"], 100.0)
    probe.positive("usage_extraction_is_source_of_truth", extract_cache_usage(hot),
                   {"prompt_tokens": 1500, "completion_tokens": 10, "cached_tokens": 1400,
                    "cache_read_tokens": 1400, "cache_write_tokens": 0, "fresh_input_tokens": 100,
                    "provider_cost": None, "cache_discount": None})
    probe.positive("normalized_observations_recorded", snaps[-1]["cache_observations"]["counts"],
                   {"HIT": 1, "WRITE": 1, "MISS": 2, "BYPASS": 0, "UNKNOWN": 1, "DEGRADED": 1})
    probe.positive("cache_metadata_rejection_recovered", retry_status, 200)
    probe.positive("retry_dropped_cache_metadata", sent[6]["messages"][0]["content"], _LONG)

    # -- NEGATIVE: no fabricated hit, hostile metadata refused, unsupported paths.
    probe.negative("no_usage_evidence_no_hit_claimed",
                   (pc[2]["state"], pc[2]["miss_reason"], pc[2]["hits"]), ("MISS", "too short", 1))
    probe.negative("cache_control_without_usage_flagged",
                   snaps[2]["cache_observations"]["cache_control_without_usage"], 1)
    probe.negative("bogus_ttl_clamped_and_degraded",
                   (pc[3]["ttl"], pc[3]["state"], pc[3]["miss_reason"]), ("5m", "DEGRADED", "invalid metadata"))
    probe.negative("bogus_ttl_not_sent_upstream", sent[3]["messages"][0]["content"][0]["cache_control"],
                   {"type": "ephemeral"})
    probe.negative("forged_session_id_not_forwarded", "session_id" in sent[4], False)
    probe.negative("forged_session_id_degrades_affinity",
                   (pc[4]["session_affinity"], pc[4]["state"]), (False, "DEGRADED"))
    probe.negative("untrusted_session_id_rejected", is_trusted_session_id("../../etc/passwd"), False)
    probe.negative("invalid_ttl_flagged_by_normalizer", normalize_ttl("99h", "5m"), ("5m", True))
    unsupported = prepare_provider_payload({"messages": [{"role": "system", "content": "P"}]},
                                           provider_kind="openai", provider_model="gpt-4o")[1]
    probe.negative("non_openrouter_payload_untouched",
                   (unsupported["state"], unsupported["miss_reason"], unsupported["cache_control_applied"]),
                   ("UNSUPPORTED", "unsupported provider", False))
    disabled = prepare_provider_payload({"messages": [{"role": "system", "content": "P"}]},
                                        provider_kind="openrouter", provider_model=_MODEL, enabled=False)[1]
    probe.negative("disabled_cache_refuses_shaping",
                   (disabled["state"], disabled["miss_reason"]), ("UNSUPPORTED", "caching disabled"))
    probe.negative("empty_provider_body_yields_zero_counters", extract_cache_usage(None)["cached_tokens"], 0)

    # The observation schema refuses a self-reported hit and any prompt content.
    log = _co.ObservationLog(capacity=8)
    probe.refused("fabricated_hit_refused_by_schema",
                  lambda: log.record(_co.CacheObservation(provider="openrouter", model=_MODEL,
                                                          route="gateway", state="HIT")),
                  ValueError, contains="HIT without cache_read_tokens")
    real = _co.build_observation(provider="openrouter", model=_MODEL, route="gateway", eligible=True,
                                 buckets=_co.normalize_openai_style_usage(hot["usage"]), ttl="1h").as_dict()
    probe.refused("observation_carrying_prompt_text_refused",
                  lambda: log.record({**real, "prompt": "SECRET"}),
                  ValueError, contains="forbidden content field prompt")

    probe.tag("CACHE-PROVIDER-EVIDENCE", "CACHE-NO-FABRICATED-HIT", "CACHE-HOSTILE-METADATA",
              "CACHE-FAILOPEN-RETRY")
    probe.count(effects=6, recoveries=1)
    return probe.finish(upstream_calls=len(sent))


def local_cognitive_reuse(seed: int) -> dict:
    probe = CaseProbe("sandbox.local_cognitive_reuse", "local_cognitive_reuse", seed)
    from bossman.exec_cache import ExecutionCache, get_cache
    from bossman.file_intel import parse_file
    from bossman.learning_guard import runtime_bridge as rb
    from bossman.learning_guard.autonomy_trainer import AutonomyCandidate
    from bossman_shared.cache_intelligence import ReuseOutcome, allow_local_cognitive_reuse, fresh_observation_wins
    prior_flag, prior_gate = os.environ.get(rb.REUSE_FLAG), rb._GATE
    key = ExecutionCache.key("fix", f"task-{seed}")
    plan = {"plan": f"patch-{seed}"}
    try:
        os.environ.pop(rb.REUSE_FLAG, None)          # production default: experiment OFF
        # -- POSITIVE: a real production consumer reuses through the real ExecutionCache.
        with tempfile.TemporaryDirectory(prefix="bench-reuse-") as tmp:
            csv = Path(tmp) / "artifact.csv"
            csv.write_text(f"col_a,col_b\n{seed},{next(_RUN)}\n", encoding="utf-8")
            shared = get_cache()
            before = shared.stats()
            first, second = parse_file(csv), parse_file(csv)
            after = shared.stats()
        probe.positive("production_parse_served_from_exec_cache", first is second, True)
        probe.positive("reused_artifact_content_hash", second.content_hash, first.content_hash)
        probe.positive("exec_cache_hit_delta", after["hits"] - before["hits"], 1)
        probe.positive("exec_cache_miss_delta", after["misses"] - before["misses"], 1)

        os.environ[rb.REUSE_FLAG] = "1"              # arm the audited reuse experiment
        rb._GATE = None
        # -- NEGATIVE: with the gate armed and no recorded A/B, fresh work wins.
        blind = ExecutionCache()
        blind.put(key, plan, verified=True, evidence="verified by pytest")
        probe.negative("reuse_refused_without_recorded_ab", blind.get(key, task_class="fix"), None)
        probe.negative("reuse_refusal_reason_no_ab", blind.last_reuse_refusal,
                       "no same-model A/B recorded for this task class")
        probe.negative("reuse_block_counted", (blind.blocked_by_reuse_gate, blind.hits), (1, 0))
        good = ReuseOutcome(verified_success_on=0.91, verified_success_off=0.90, continuity_delta=0.2,
                            compute_delta=-0.3, samples_on=25, samples_off=25)
        rb.default_reuse_gate().record_ab("fix", good)
        served = ExecutionCache()
        served.put(key, plan, verified=True, evidence="verified by pytest", env_fingerprint="env-a")
        rec = served.get(key, env_fingerprint="env-a", require_verified=True, task_class="fix")
        probe.positive("verified_entry_reused_after_passing_ab", rec.result, plan)
        probe.positive("reuse_carries_provenance", (rec.evidence, rec.verified), ("verified by pytest", True))
        probe.positive("reuse_hit_counted", served.stats()["hits"], 1)
        probe.positive("contract_allows_noninferior_beneficial_reuse",
                       (allow_local_cognitive_reuse(good)[0], allow_local_cognitive_reuse(good)[1].split(" (")[0]),
                       (True, "non-inferior and beneficial"))
        # -- NEGATIVE: the recorded A/B says reuse degrades quality -> refuse.
        degrading = ReuseOutcome(verified_success_on=0.5, verified_success_off=0.9,
                                 continuity_delta=0.2, samples_on=25, samples_off=25)
        rb.default_reuse_gate().record_ab("fix", degrading)
        worse = ExecutionCache()
        worse.put(key, plan, verified=True)
        probe.negative("reuse_refused_when_ab_degrades", worse.get(key, task_class="fix"), None)
        probe.negative("refusal_reason_from_reuse_contract", worse.last_reuse_refusal,
                       "VerifiedSuccess inferior with reuse ON")
        probe.negative("contract_refuses_degrading_ab", allow_local_cognitive_reuse(degrading),
                       (False, "VerifiedSuccess inferior with reuse ON"))
        probe.negative("contract_refuses_thin_evidence",
                       allow_local_cognitive_reuse(ReuseOutcome(0.9, 0.9, continuity_delta=0.2,
                                                                samples_on=2, samples_off=2)),
                       (False, "INSUFFICIENT_EVIDENCE: 2/2 < 20"))
    finally:
        rb._GATE = prior_gate
        os.environ.pop(rb.REUSE_FLAG, None)
        if prior_flag is not None:
            os.environ[rb.REUSE_FLAG] = prior_flag

    # -- NEGATIVE (flag off): fail-closed kinds, stale entries, unverified entries.
    guard = ExecutionCache()
    secret = ExecutionCache.key("credentials", f"vault-{seed}")
    probe.negative("credentials_never_cached", guard.put(secret, "sk-live-xxx", verified=True), False)
    probe.negative("live_balance_never_cached",
                   guard.put(ExecutionCache.key("live_balance", seed), 42, verified=True), False)
    probe.negative("refused_kinds_left_no_trace",
                   (guard.stats()["rejected_kinds"], guard.stats()["entries"], guard.get(secret)), (2, 0, None))
    stale = ExecutionCache()
    reg = ExecutionCache.key("parsed_registry", f"registry-{seed}.yaml", 1.0)
    stale.put(reg, {"model_windows": {"bossman-smart": 131072}}, verified=True, env_fingerprint="env-a")
    probe.negative("stale_entry_refused_after_env_drift", stale.get(reg, env_fingerprint="env-b"), None)
    probe.negative("stale_entry_evicted", stale.stats()["entries"], 0)
    unverified = ExecutionCache()
    guess = ExecutionCache.key("plan", seed)
    unverified.put(guess, {"guess": 1}, verified=False)
    probe.negative("unverified_entry_refused", unverified.get(guess, require_verified=True), None)
    probe.positive("refusal_caused_by_verification_requirement", unverified.get(guess).result, {"guess": 1})
    probe.negative("fresh_observation_beats_reused",
                   fresh_observation_wins({"cached": 1}, {"fresh": 2}), {"fresh": 2})
    probe.refused("reuse_candidate_without_rollback_ref_refused",
                  lambda: AutonomyCandidate(candidate_id=f"class:fix-{seed}", kind="context",
                                            scope={"task_class": "fix", "risky": False},
                                            hypothesis="reuse verified fix plans", rollback_ref=""),
                  ValueError, contains="rollback_ref required")
    # RECOVERY: after the env-drift refusal the caller redoes the work under the new env.
    stale.put(reg, {"model_windows": {"bossman-smart": 131072}}, verified=True, env_fingerprint="env-b")
    probe.positive("recomputed_entry_served_under_new_env",
                   stale.get(reg, env_fingerprint="env-b").result, {"model_windows": {"bossman-smart": 131072}})
    probe.tag("REUSE-GATE-AB", "REUSE-FAIL-CLOSED", "REUSE-STALE-EVICTION", "REUSE-FRESH-WINS")
    probe.count(effects=1, recoveries=1)
    return probe.finish()


CASES = {"sandbox.prompt_cache": prompt_cache,
         "sandbox.local_cognitive_reuse": local_cognitive_reuse}
