# Universal Computer Apprentice + audit P0 closure — final report

START_SHA (remote, before this work): `3b15d466dcc8747db502940896884a8774dcdb25`
FINAL_LOCAL_SHA = FINAL_REMOTE_SHA: see the last line of this file (`git rev-parse HEAD` == `origin/claude/bossman-control-v03-43igbk`).
Branch: `claude/bossman-control-v03-43igbk`. No force-push. Two foreign commits landed mid-work
(0898696 video log, 42da6c1 jsonschema dev-dep + Kimi K3 prompt) and were fast-forwarded, not rewritten.

## 1. Verdict

**NO-GO for live use, READY for owner audit of the code path.** Everything that can be proven on this
host is proven (deterministic suites, wheels, adversarial reproductions). Everything that needs an
external system is honestly `LIVE_NOT_PROVEN (MOCK)` and stays behind OFF flags:
Higgsfield, Claude Code teacher, Google Maps outreach, paid Anthropic cache hit, Docker daemon.

## 2. Architecture (one universal agent, not three bots)

`bossman-core/bossman/apprentice/` — `UniversalComputerApprentice` state machine
RECEIVE_TASK → PLAN → OBSERVE → ACT → VERIFY → CONTINUE | RECOVER | FALLBACK | WAIT_APPROVAL | SUCCEED | FAIL.
Every action is a typed `ActionRecord` (task/run/session, app identity, semantic target, typed action,
precondition, fresh pre/post observation refs bound to task/run/session/action/effect, expected
transition, verification method, result, risk class, side_effect_id, timestamp, evidence source;
schema `schemas/apprentice_action_record.schema.json`). Memory: Task Episode / Verified Skill /
Negative Lesson through `learning/trace.py` primitives and `learning_guard` promotion; Claude Code is
an `UNTRUSTED_TEACHER_OUTPUT` source with technical sanctions; outreach is approval-gated with
key-scoped side-effect identity. Reuse table and threat model: `docs/intelligence/UNIVERSAL_COMPUTER_APPRENTICE.md`
(28 threat rows T01–T28, each with prevention/detection/recovery and a named regression test;
5 own proposals implemented behind their own flags, 3 DEFERRED).

## 3. Audit P0 → fix → files → tests → status

| P0 | Fix | Files | Tests | Status |
|---|---|---|---|---|
| Failed verification must block the UCA result | VERIFY=false → RECOVER/FAIL, never SUCCEED; episode `verified=False`, no skill | apprentice/engine.py, recording.py | test_apprentice_p0 (P0-1) | FIXED |
| Writes need idempotency key + verified effect receipt | key required before ACT; typed `EffectReceipt` verified by the engine, ledger claim/complete/abandon | engine.py, guards.py, models.py | test_apprentice_p0 (P0-2), test_lead_uca_adversarial | FIXED |
| receipt.action_type must match the action | mismatch → `receipt_invalid`, ledger not completed | engine.py | test_apprentice_p0 (P0-3), lead tests (foreign action/effect id, stale receipt) | FIXED |
| Observation not from the future, bound to task/run/session/action/effect | skew 120 s, binding checks, records carry full binding | engine.py, guards.py | test_apprentice_p0 (P0-4), lead frozen-observation test | FIXED |
| Autonomy Trainer SHADOW without baseline / successes | SHADOW needs measured `baseline_success` and ≥need successful verified episodes | learning_guard/autonomy_trainer.py | test_pass3_autonomy_trainer::test_shadow_requires_baseline_and_verified_successes | FIXED |
| Verifier identity structured; alias not independent | `canonical_principal_id`, same model_id under any class not independent | deep_fix.py, learning/trace.py | test_audit_p0_identity_evidence, tests/test_audit_p0_trace_identity | FIXED |
| Evidence must carry principal_id/head_sha/environment/collected_at + future skew | required fields; MAX_CLOCK_SKEW_S=120 on observed/collected | deep_fix.py, learning/trace.py | same files | FIXED |
| AI Company verifier/approver trusted typed principals; model:* cannot approve | `untrusted_approver_reason`, `verifier_dependency_reason` (Principal.independent_of) | company/runtime.py | test_audit_p0_company_principals | FIXED |
| Learning Store transactional single journal | `journal.jsonl` authoritative; snapshots derived and rebuilt on divergence; legacy bootstrap | learning/trace.py, data/learning/journal.jsonl | tests/test_audit_p0_learning_journal | FIXED |
| Critical gate typed PASS/FAIL/NOT_APPLICABLE; silent None forbidden | GATE_VERDICTS typed; None/dict-without-verdict = critical failure; gates return NOT_APPLICABLE | bcc/engine.py, features/deep_fix.py, features/review_gate.py | test_pass3_hooks_fail_closed (12) | FIXED |
| bossman_shared in wheels and Docker | root `pyproject.toml` (bossman-shared: bossman_shared, learning, bossman_schemas), import-first bootstraps, root-context Dockerfile | pyproject.toml, Dockerfile, _shared.py ×2, apprentice/_bootstrap.py, learning/trace.py | tests/test_packaging_installed (venv install outside checkout), root-ci `docker-smoke` | FIXED (container: CI) |
| Provider cache evidence outranks applied flag | `classify()` evidence-first | bossman_shared/cache_observation.py | tests/test_audit_p0_cache_evidence | FIXED |
| Trainer + local reuse wired to runtime under OFF flags | `learning_guard/runtime_bridge.py`: Deep Fix record → sanitized Episode → measured-baseline evaluation; `ExecutionCache.get` reuse gate | runtime_bridge.py, exec_cache.py, deep_fix.py | test_audit_p0_runtime_wiring | FIXED (flags OFF) |
| V2 Auto-Repair CI | pgvector/pgvector:pg16, DSN env, dev extras, timeouts | .github/workflows/bossman-v2-repair.yml | CI run on final HEAD | see §7 |

Teacher sanctions (Claude Code): TEACHER_OUTPUT_REJECTED (rollback of the attempt only, evidence kept,
one corrective retry, learning blocked), TEACHER_OUTPUT_QUARANTINED (patch never applied, related traces
barred, owner approval required), ACCEPTANCE_TAMPERING (rejected, hash-bound tests restored, adversarial
register, reliability −0.40), circuit breaker on repeated identical errors, scoped reliability score per
(model, version, task type, repo, 7-day window) — `apprentice/sanctions.py`, `teacher.py`; tests
test_apprentice_sanctions (7), test_apprentice_teacher (19).

## 4. Three E2E scenarios — all `LIVE_NOT_PROVEN (MOCK)`

| Scenario | Simulator | Proven in the mock | Live instruction |
|---|---|---|---|
| Higgsfield video (create → queue/generating/ready/error → Extend → verify → download) | tests/fixtures/apprentice/higgsfield_sim.py | launched exactly once, states distinguished, download substitution refused, skill with semantic anchors, DEGRADED on UI change, no blind replay | UCA doc §9.1 |
| Complex bug → own attempt → Claude Code fallback → independent verification → learn only on accepted | teacher_sim.py | commands/diff/tests recorded, good ACCEPTED / bad REJECTED+retry→breaker / tampering / security quarantine; analogous bug: 0 extra teacher calls | UCA doc §9.2 |
| Google Maps → verify problem → demo → proposal → owner approval → send | maps_sim.py | public data only, injected listing ignored, package shown, digest-bound one-time approval, duplicate/resend/mass/blocked refused | UCA doc §9.3 |

## 5. Test evidence on the final code (this host, Linux container, PG16 @5433)

- command-center: `791 passed, 3 skipped in 197.7s` (no hang bypass, --timeout=300)
- bossman-core: `1580 passed, 5 skipped in 18.4s` (after the exec-cache init repair)
- root: `77 passed in 9.6s` (includes the wheel install test)
- targeted: apprentice + lead + audit (core) `132 passed`; audit (root) `11 passed`; hooks (cc) 12 tests
- compileall PASS, secret scan PASS, `git diff --check` clean, wheels `bossman_shared-0.1.0`, `bossman_core-0.3.0` built and installed into a fresh venv from outside the checkout.

## 6. Flags (all default OFF)

BOSSMAN_UNIVERSAL_COMPUTER_APPRENTICE (master), BOSSMAN_SKILL_RECORDING, BOSSMAN_SKILL_SHADOW_REPLAY,
BOSSMAN_SKILL_PROMOTION, BOSSMAN_CLAUDE_CODE_FALLBACK, BOSSMAN_EXTERNAL_OUTREACH,
BOSSMAN_APPRENTICE_{DRY_RUN_PREVIEW,CHECKPOINT_RESUME,ANCHOR_REDUNDANCY,LESSON_PRECHECK,EVIDENCE_EXPORT},
BOSSMAN_AUTONOMY_TRAINER_SHADOW, BOSSMAN_COGNITIVE_REUSE_EXPERIMENT, BOSSMAN_CONTEXT_WASTE_OBSERVE,
BOSSMAN_CACHE_ADVISOR, AI_COMPANY_MODE_ENABLED, BOSSMAN_DEEP_FIX_ENABLED. Safe numeric telemetry
(BOSSMAN_CACHE_TELEMETRY_V2) stays ON.

## 7. GitHub Actions on the final HEAD

On dd109c4 all four workflows were red for two reasons, both fixed on the next commits: (1) the
secret scanner flagged the synthetic canary in tests/test_audit_p0_runtime_wiring.py (now carries the
`ci-secret-scan: allow` mark), (2) `ExecutionCache.rejected_kinds` displaced by the reuse-gate hook
(38174eb). V2 Auto-Repair on 38174eb reached the full suite for the first time (pgvector fix works:
schema applied, P0 tests 6 passed, 1561 passed) and failed only on the host-specific sandbox
process-runtime tests, which the workflow now leaves to the Core CI groups. The final run ids and
conclusions on the last HEAD are listed in the chat report; `docker-smoke` (root-ci) is the container
proof this host cannot give.

## 8. Not proven on this host

Docker build/run (no daemon → CI docker-smoke job is the proof), live Higgsfield, live Claude Code,
live Google Maps/mail transport, paid Anthropic WRITE→HIT, Windows, GPU/RunPod.

## 9. Remaining risks

Side-effect ledger, approval nonces and reliability scores are process-local (durable store is the next
step); injection defence is regex-based; shadow replay checks anchor resolvability only; the trainer
baseline is the historical success rate of the class (measured, but small samples).

## 10. Rollback

Flags OFF disable every new behaviour without code changes. Code:
`git revert` of the apprentice range `091aaf0 857c0f3 53d73fc ce153ed 2cc3806 8a26d14 0068639 4d975d6 e467a4d 992019a d6bf16b`
and of the audit commits `1d2b553 1a881ff 85a5dbb c1da122 4b634e3 caed9d8 e5ab091 ce914e3 1f09550 dd109c4 38174eb`
(the journal bootstrap `ce914e3` only adds a file; deleting `data/learning/journal.jsonl` returns the
store to legacy snapshots which are re-bootstrapped on next read).
