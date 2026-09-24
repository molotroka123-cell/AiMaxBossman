# Bossman 1.6 — Coding Limit Saver / Cheap-First Policy

Status: foundation contract + routing code.

Implementation:
`command-center/bcc/features/coding_limit_saver_v16.py`

Tests:
`command-center/tests/test_coding_limit_saver_v16.py`

## Owner rule

For normal coding work:

```
verified local/free coder
  -> another bounded cheap attempt when useful
  -> GLM-5.3-Flash for hard blocker/final coding escalation
  -> verifier
```

**Aster does not write product code.**

Aster spends its quota only on high-value audit/improvement packets:
- false-PASS risk;
- architecture defect;
- waste/context bloat;
- missing regression;
- cheaper/better route;
- generalized lesson.

Aster output is advisory until verified.

## Why

The purpose is not to minimize tokens at any price.

Optimize:

`verified quality / incremental cost / elapsed time / context consumed`.

A free model that produces three broken patches can be more expensive than one
bounded GLM-5.3-Flash call. The router therefore records verified success and
escalates when cheap attempts stop making progress.

## Code-author allowlist

Allowed classes:
- `LOCAL`;
- `FREE`;
- `GLM53_FLASH`.

Not allowed:
- `ASTER`;
- arbitrary premium model silently promoted to coder;
- auditor/verifier writing the fix it later certifies.

Claude/frontier may remain a teacher in workflows where already authorized, but
BossBlocks-001 should prefer code authored by local/free workers or
GLM-5.3-Flash. Teacher advice is not a replacement for independent verification.

## Limit-saving mechanisms

### 1. Request fingerprint cache

Identical task fingerprint + capabilities + privacy + complexity produces a
stable cache key.

Caller should reuse a previously verified result/skill instead of paying a
second inference for the same deterministic work.

Never reuse when source/evidence/policy/model versions changed.

### 2. Progressive context

A coder receives only:
- objective;
- acceptance/test failure;
- scoped files;
- relevant evidence;
- tool schema.

Default active input cap is ~30% of the physical model context and never more
than 50% without a separate deep-task reason.

Large context window is capacity, not a target.

### 3. Free attempts are bounded

Default:
- two cheap attempts per blocker;
- then a GLM-5.3-Flash escalation may be eligible.

No endless free-agent loop.

### 4. GLM is bounded

Default:
- max 2 GLM coding calls per task;
- no silent budget increase;
- no use when privacy policy forbids external egress;
- after cap: BLOCKED/replan, not hidden premium fallback.

The exact paid/free status of a GLM route is determined by the connected
provider/account at runtime. Do not hard-code "free" merely from model name.

### 5. Aster receives evidence, not the whole repo

At its scheduled checkpoint Aster receives a compact packet:
- task DAG delta;
- changed files/diff stats;
- tests and failures;
- known blockers;
- model/cost/context telemetry;
- verifier result;
- current top risks.

Do not send full source tree/log history unless Aster requests a specific deeper
artifact and the context budget permits it.

### 6. Audit cadence

BossBlocks default:
- Aster every 30 minutes;
- immediate Aster only for P0/P1, false-PASS risk, repeated blocker or final gate.

Do not spend Aster capacity after every successful trivial edit.

### 7. Compile successful work into cheaper paths

A verified repeated trajectory should become:
- regression;
- lesson;
- typed skill;
- deterministic tool/workflow when possible.

The ideal mature workflow makes fewer LLM calls than the first run.

## GLM-5.3-Flash note

As of the 2026-09-24/25 Z.ai documentation, GLM-5.3-Flash is positioned as an
efficient multimodal/agentic member of the GLM-5.3 family, with 320B total and
18B active parameters reported by the publisher. ZCode supports GLM-5.3-Flash
through connected GLM plans/API credentials.

Bossman must discover the actual model ID, endpoint, quota and billing treatment
from the configured provider instead of assuming today's plan terms forever.

## BossBlocks authoring policy

For BossBlocks-001:

1. Jev breaks work into scoped coding packets.
2. Coding Limit Saver selects a legal writer.
3. LOCAL/FREE workers write most code.
4. After bounded failures, GLM-5.3-Flash may write the hard fix.
5. Aster audits only.
6. Independent QA/verifier accepts/rejects.
7. Verified learning is broadcast to future agents.

Target metric:
`ASTER_CODE_WRITES = 0`.

Also record:
- free/local code-writing calls;
- GLM coding calls;
- cache hits;
- context tokens avoided;
- Aster checkpoint calls;
- cost per verified feature.

## GREEN invariants

- Aster never appears as code writer.
- LOCAL_ONLY/SECRET never routes to external FREE/GLM.
- GLM cap is enforced.
- free quota exhaustion is explicit.
- no duplicate inference for a valid cache hit.
- no audit model self-certifies its own authored patch.
