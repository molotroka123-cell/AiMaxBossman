# Jev 1.13 Decision Engine — staged integration

Status: **STAGED / DISABLED BY DEFAULT**

Target branch: `release/bossman-owner`

Purpose: prepare Bossman to optionally use TypeSafe Jev 1.13 as a cheap, typed System-1 decision layer. Do not make Jev a hard dependency and do not break existing routing.

## Intended architecture

```
task
  -> JevDecisionProvider (optional)
      -> model route
      -> tool route
      -> complexity/risk score
      -> owner-approval decision
      -> retry/escalation decision
  -> existing Bossman executor/router
  -> evaluator/verifier
  -> result
```

Jev should assist routing, not replace Bossman's current router. Existing behavior remains the fallback.

## API target

Documentation reviewed:
- https://defapi.org/api/model/en/typesafe/jev-1.13
- model: `typesafe/jev-1.13`
- endpoint family: DefAPI decisions API

Jev exposes typed decision primitives:
- **Choice** — select one candidate, useful for model/tool routing.
- **Score** — bounded evaluation, useful for complexity/risk/quality.
- **Noul** — boolean/probability-style decision, useful for approval/escalation/retry gates.

Validate the exact request/response schema against current provider docs during implementation.

## Proposed Bossman decisions

A single decision pass may determine:
1. `model_route`: local_fast | local_reasoner | cloud_reasoner
2. `tool_route`: none | terminal | browser | github | other_registered_tool
3. `complexity_score`
4. `risk_score`
5. `needs_owner_approval`
6. `needs_strong_verifier`
7. `retry_or_escalate`

Never treat type-safe output as proof that the underlying decision is correct.

## Safety / reliability gates

- Feature flag: `BOSSMAN_JEV_ENABLED=false` by default.
- No API key committed to Git.
- Secret comes from environment/secret store only.
- Hard timeout and bounded retries.
- Circuit breaker after repeated provider failures.
- On timeout/error/invalid response -> existing Bossman router.
- High-risk actions continue through existing approval policy.
- Jev must not bypass `never/ask/allowed` policies.
- Log decision, confidence/score where available, fallback reason, latency and cost metadata without logging secrets.
- Low-confidence or high-risk decisions escalate to the stronger reasoning/verifier path.
- Keep a kill switch so Jev can be disabled without rebuilding Bossman.

## Self-improvement use

Candidate loop:

```
baseline
 -> generate candidate change
 -> Jev cheap routing/gating
 -> execute isolated evaluation
 -> strong evaluator/verifier
 -> compare candidate vs baseline
 -> regression/security gates
 -> owner approval when required
 -> promote only measurable winner
 -> retain rollback artifact
```

Jev is not the final judge for self-modification. Promotion requires deterministic tests and/or the stronger evaluator appropriate to the task.

Track at minimum:
- task class
- Jev decision
- chosen provider/tool
- execution success
- verifier result
- latency
- estimated cost
- fallback/escalation
- regression outcome

This creates data for later calibration of Bossman's routing policy.

## Implementation checklist for owner setup

When the owner activates/pays for the provider:

- [ ] Confirm current DefAPI/Jev API contract from official docs.
- [ ] Add secret locally (never commit it).
- [ ] Implement `JevDecisionProvider` behind Bossman's provider/router interface.
- [ ] Add config + feature flag.
- [ ] Add timeout/retry/circuit-breaker/fallback.
- [ ] Add typed schema validation.
- [ ] Add unit tests with mocked responses.
- [ ] Add failure tests: timeout, 4xx/5xx, malformed response, rate limit.
- [ ] Add shadow mode: record Jev decisions while existing router remains authoritative.
- [ ] Run representative Bossman scenarios and compare decisions.
- [ ] Calibrate thresholds.
- [ ] Enable only after shadow evaluation passes.
- [ ] Preserve existing router as fallback/kill-switch.

## Recommended rollout

**Phase 0 — disabled:** code/config present, no network calls.

**Phase 1 — shadow:** Jev decides but cannot alter execution. Compare it with Bossman's current routing.

**Phase 2 — low-risk routing:** allow model/tool routing only for reversible low-risk work.

**Phase 3 — optimization:** use accumulated outcomes to tune routing thresholds and reduce unnecessary cloud-model calls.

Do not automatically promote Jev to authority over destructive actions, security controls, spending, credentials, or irreversible external actions.

## Acceptance criteria

Integration is ready when:
- Bossman starts normally with Jev disabled and without a Jev key.
- Jev failure never prevents task completion when the existing router is available.
- Secrets never appear in repository/logs.
- Existing approvals remain authoritative.
- Shadow-mode metrics are observable.
- Tests cover success + failure/fallback paths.
- Provider can be disabled immediately with one configuration switch.
