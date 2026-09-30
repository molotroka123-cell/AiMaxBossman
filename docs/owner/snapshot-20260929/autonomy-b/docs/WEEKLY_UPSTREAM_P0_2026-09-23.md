# Bossman — Weekly Upstream P0 Candidates — 2026-09-23

Status: **STAGED FOR REVIEW / DO NOT BLINDLY MERGE**

Target branch: `release/bossman-owner`

This file freezes the highest-value upstream candidates identified for the next owner integration pass. Goal: reuse proven ideas/code where appropriate, while preserving Bossman's working architecture, approval system, rollback, local-first goals, and security boundaries.

## P0-A — Cloudflare Security Audit Skill

Upstream:
https://github.com/cloudflare/security-audit-skill

### Why Bossman wants it

Use the architecture/patterns for adversarial auditing of Bossman's own changes:
- architecture/trust-boundary mapping
- parallel security hunting
- independent verification/refutation of findings
- coverage tracking
- machine-readable findings
- repeatable audit passes

### Bossman target loop

```
candidate change
 -> deterministic tests
 -> security hunter swarm
 -> independent adversarial verifier
 -> regression/security gate
 -> accept/reject
 -> evidence + rollback artifact
```

**Critical rule:** the agent that produced a change must not be the sole authority deciding that its own change is safe.

### Integration requirements

- Pin reviewed upstream revision.
- Review license/dependencies.
- Adapt to Bossman's existing agent orchestration rather than replacing it.
- Findings must include evidence/reproduction where possible.
- Separate hunter and verifier roles.
- No automatic promotion solely because the audit produced zero findings.
- Preserve existing Bossman approval policy.

---

## P0-B — Alibaba Open Code Review

Upstream:
https://github.com/alibaba/open-code-review

### Why Bossman wants it

Candidate deterministic/LLM hybrid review layer after coding agents modify Bossman.

Target:

```
Claude/Bossman coder
 -> tests
 -> structured code review
 -> security audit
 -> independent verifier
 -> benchmark candidate vs baseline
 -> promotion gate
```

We want to study/reuse:
- deterministic handling of code/change structure
- efficient context construction
- precise line/change anchoring
- separation between deterministic pipeline work and semantic LLM judgment
- evidence-backed findings

### Important

Upstream benchmark claims are upstream evidence, not proof that it will outperform Bossman's current review stack. Run our own benchmark on representative Bossman PRs before making it authoritative.

### Acceptance

Compare:
- true useful findings
- false positives
- missed regressions
- token usage
- latency
- cost
- line/location accuracy

Keep existing review path as fallback until measured improvement is established.

---

## P0-C — Hermes Agent Architecture Mining

Upstream:
https://github.com/NousResearch/hermes-agent

**Do not transplant Hermes wholesale.** Mine only clearly superior, compatible patterns.

Priority research areas:
1. persistent scheduled/cron agents
2. memory across recurring executions
3. live steering/control of subagents
4. MCP/tool management
5. gateway/provider patterns
6. isolated profiles/environments
7. search/tool caching
8. context compression
9. secret/keychain handling
10. model routing
11. image/video provider patterns

### Bossman desired outcome

Nightly improvement mode:

```
schedule
 -> read previous experiment memory
 -> generate bounded candidates
 -> isolated tests
 -> reviewers/verifiers
 -> benchmark vs baseline
 -> retain evidence
 -> queue winners for owner/policy-approved promotion
```

No uncontrolled recursive self-modification.

---

## P1 — Paper/Repo -> Skill Factory

Research concept: convert high-quality documentation, papers and repositories into **candidate executable Bossman skills**, then test them rather than trusting generated integration.

Pipeline:

```
trusted source
 -> source extractor
 -> candidate skill specification
 -> generated adapter/skill
 -> sandbox
 -> deterministic tests
 -> security review
 -> evaluator
 -> skill registry candidate
 -> approval/promotion
```

Every generated skill must retain provenance:
- source URL/repository
- source revision/version/date
- generated files
- tests
- permissions/tools requested
- benchmark evidence
- security findings
- rollback/removal procedure

Never ingest arbitrary repositories directly into an execution environment.

---

## P1 — Unified local model/runtime watcher

Track improvements around GGUF / llama.cpp / Transformers integration, especially AMD/ROCm/Strix Halo support.

Bossman hardware target:
- Ryzen AI Max+ 395
- Radeon 8060S
- 128 GB unified memory

Do not migrate runtime merely because a new integration exists. Benchmark:
- model quality
- tokens/sec
- prompt processing
- memory usage
- tool calling
- structured outputs
- long context
- stability
- startup/load time

---

# Target Bossman 1.1 control plane

```
                         OWNER POLICY
                              |
                        Bossman Planner
                              |
                    General Decision Router
                         /           \
                cheap/reflex       deep work
                    |                 |
                  Jev        local/cloud reasoners
                    |                 |
              Browser task?           |
                 /    \               |
          Jev Ultrafast  full browser |
                 \      /             |
                   execution/result
                          |
                  deterministic tests
                          |
                 Open Code Review
                          |
                security hunter swarm
                          |
              independent verifier(s)
                          |
                candidate benchmark
                          |
             policy + promotion gate
                          |
                  memory/evidence
                          |
                  next experiment
```

## Non-negotiable self-improvement invariants

1. Baseline is immutable during an experiment.
2. Candidate changes execute in isolation first.
3. Every candidate has a rollback path.
4. No candidate self-approves.
5. Security/approval policies cannot be weakened by the candidate being evaluated.
6. Secrets are never committed or passed into untrusted generated code unnecessarily.
7. External repositories are pinned/reviewed before integration.
8. Speed/cost improvement cannot compensate for a material reliability/security regression.
9. Promotion requires evidence.
10. Owner approval remains required wherever Bossman's policy says `ask`.
11. Keep an auditable experiment ledger.
12. Stop loops on repeated no-progress, regressions, budget cap, or abnormal behavior.

## Tomorrow — Claude Code order

1. Fetch latest `release/bossman-owner`.
2. Preserve all new commits and current working subsystems.
3. Read:
   - `docs/JEV_DECISION_ENGINE.md`
   - `docs/JEV_ULTRAFAST_BROWSER_CRITICAL.md`
   - this document
4. Do not start by rewriting architecture.
5. Inspect only the interfaces required for integration.
6. Review/pin upstream dependencies before reuse.
7. Implement behind feature flags where practical.
8. Build offline/mocked tests before using paid APIs.
9. Establish baseline measurements.
10. Run candidates in shadow/isolation.
11. Produce evidence for each claimed improvement.
12. Promote only changes that pass existing tests + new regression/security gates.
13. Leave owner branch runnable when optional providers/keys are absent.

## Deliverable evidence

At the end of the integration pass create/update a concise evidence report containing:
- exact HEAD SHA
- upstream revisions used
- files changed
- tests/commands run
- pass/fail counts
- benchmark before/after
- known limitations
- security review status
- feature flags
- rollback instructions
- remaining blockers

The goal is **measurable compounding improvement**, not uncontrolled code churn.
