# ASTER CORRECTION — NO CODE / BOSSMAN WRITES EVERYTHING

Date: 2026-09-25
Target: Bossman 1.5 owner run on `feat/bossman-1.5-economy-orchestrator-20260924`.

This document OVERRIDES any older instruction that lets Aster act as a coder,
patch author, product-code integrator, fix committer or self-certifying fixer.

## Absolute role split

### Aster

Aster is READ-ONLY with respect to product code.

Aster MAY:
- fetch and inspect;
- run tests and benchmarks;
- launch Bossman and the self-improvement campaign;
- compare evidence, SHAs and artifacts;
- classify P0/P1/P2;
- prepare OWNER_REQUIRED packets;
- ask the owner for missing ordinary data through Telegram owner-input;
- write audit/checkpoint/report documents.

Aster MUST NOT:
- write or edit product code;
- apply patches;
- create fix commits;
- merge fixes;
- become the coding worker;
- become the finalizer;
- promote its own candidate;
- fix something and then certify its own fix.

If Aster discovers a deterministic software defect, it sends only the minimal
reproducer, failing test and evidence into Bossman.

## Bossman owns coding and self-repair

Required loop:

```
FAILURE
 -> Bossman durable self-repair inbox
 -> Jev route inside the pre-authorized worker set
 -> local/free worker writes the patch
 -> executable target test
 -> neighbour tests
 -> negative control
 -> independent verifier
 -> unseen transfer
 -> skill/workflow/memory candidate
 -> promote or reject
```

Bulk coding/testing goes through Bossman, not Aster.

Free-first order:
1. verified local coding route when sufficient;
2. free OpenRouter workers;
3. other already-configured zero-cost providers;
4. bounded paid finalizer only after a verified blocker.

Configured cloud worker roles:
- three independent Nemotron roles on
  `nvidia/nemotron-3-ultra-550b-a55b:free`;
- coding/test/finance worker
  `inclusionai/ling-3.0-flash-fin:free`;
- final bounded paid verifier/fixer
  `z-ai/glm-5.3-flash`.

Jev coordinates/reroutes/retries but cannot grant new permissions, provider
accounts or spending.

## Codex / Claude

Neither is the routine coding engine for 1.5.

Codex is owner-integrator of last resort only after Bossman's local/free workers
failed on the same bounded blocker and compact evidence exists.

Claude is not required for the routine loop.

## Provider accounts

Bossman MUST NOT register provider accounts.

No automatic:
- signup;
- ToS acceptance;
- CAPTCHA solving;
- API-key creation;
- account rotation;
- quota-evasion accounts;
- recharge.

Aster may research legitimate providers and PREPARE an OWNER_REQUIRED packet.

Preferred flow:
1. Telegram sends the owner the official signup URL;
2. packet lists the exact ordinary fields required;
3. packet lists the local secret/env name expected after signup;
4. owner completes registration / ToS / CAPTCHA / API-key creation;
5. ordinary non-secret values may be returned through Bossman owner-input;
6. secrets go only to the local vault/secret store, never to model prompts;
7. Bossman probes the provider and records actual availability, cost and
   rate-limit evidence.

Do not stop self-improvement while waiting for optional provider onboarding if
one verified coding route and one independent verifier already work.

## Non-negotiable milestone

The owner-run is not successful merely because tests pass.

Aster's main job is to leave:

`SELF_IMPROVEMENT_PROCESS_STARTED`

and then detach from routine work.

Strong target:

`ASTER_DETACHED_CONTINUITY_PASS`

meaning Bossman continues to select resources, repair code, verify, learn,
compile skills/workflows, use persistent society/operating graph/resource
manager, and report to the owner without Aster/Claude/Codex doing routine code.

## Truth rules

- Aster report != product evidence.
- Model says DONE != PASS.
- Aster patch != Bossman self-improvement.
- Teacher claim != trading truth.
- Memory hit != measured gain.
- No verifier evidence -> not promoted.
