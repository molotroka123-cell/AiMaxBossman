# CLAUDE NEXT ACTION — OWNER PRODUCT FIRST

Canonical branch: `release/bossman-owner`

This file is a direct execution correction for the current convergence run. Do not create another final/convergence branch. Continue on the canonical owner branch.

## 1. OWNER SCENARIOS are the primary readiness signal

Existing Core / Command Center / Astra / Fable / PostgreSQL / contract suites are regression evidence. They are not the definition of product readiness.

Maintain two separate scoreboards:

- `REGRESSION CI`
- `OWNER SCENARIOS`

Never combine their counts. Report progress primarily as:

`OWNER SCENARIOS: X / 20` → `X / 50` → `X / 100+`

Do not use commit count, branch count, unit-test count, or document count as the main readiness metric.

## 2. Start the first 20 integrated owner scenarios now

Create and use:

- `tests/owner_scenarios/`
- `owner_scenarios.json`

The first 20 scenarios must exercise real integrated Bossman behavior:

1. Normal owner AI request through Bossman.
2. Multi-turn context survives.
3. Project context is recalled correctly.
4. Project A context does not leak into project B.
5. Bossman selects an available model.
6. Provider failure triggers valid fallback.
7. File tool reads a real file.
8. File mutation verifies the actual resulting bytes/state.
9. Browser workflow completes and verifies its outcome.
10. Coding agent edits a small project and runs its tests.
11. Planner → worker → verifier multi-agent chain works.
12. Risky action enters `WAIT_APPROVAL`.
13. Denied approval prevents the effect.
14. Valid approval resumes the task exactly once.
15. Telegram approval round-trip completes end-to-end.
16. Image Studio produces and persists a real artifact.
17. Video Studio produces a real artifact and validates it with FFmpeg/ffprobe.
18. Bossman is terminated mid-task and resumes correctly after restart.
19. Replayed/duplicate operation does not duplicate the external effect.
20. Owner receives one consolidated human-readable completion report.

Implement and execute these 20 before expanding the suite.

## 3. Installed-product evidence over source-only evidence

Where technically possible, run owner scenarios against the packaged/installed Bossman path.

Do not treat:

`source import → direct function call → PASS`

as equivalent to:

`installed owner product → real capability → verified outcome`.

Record evidence level per scenario.

## 4. CI AI provider

Use / implement `ci_ai_provider` so the engineering AI can temporarily serve as the inference backend through the same provider contract Bossman expects.

Required coverage:

- free text
- structured output
- tool selection
- argument/schema correctness
- multi-turn context
- planning
- verifier call

Label this evidence `AI_BACKED_CI`.

Never call it `LOCAL_MODEL_CERTIFIED`.

Local-model certification requires the real owner hardware/model path.

## 5. Windows installed-product evidence is a mandatory gate

For the exact canonical candidate SHA, all mandatory Windows owner workflows must actually exist and complete successfully.

Rules:

- missing mandatory workflow = FAIL
- queued = UNKNOWN
- cancelled = UNKNOWN/FAIL depending on cause
- completed successful run = PASS

Add an automated release check that verifies the mandatory workflows exist for the exact candidate SHA. This must prevent another silent branch-filter failure like BL-089.

## 6. Salvage PR59 / PR60 / PR61 / PR62 subsystem-by-subsystem

Do not globally merge historical PRs just because they are large or called final.

Use `CONVERGENCE_DECISIONS.md` and evaluate each unique capability as one of:

- `PRESENT_BETTER_IN_OWNER`
- `PORT_REQUIRED`
- `OBSOLETE`
- `OWNER_HARDWARE_ONLY`

Port every `PORT_REQUIRED` P0/P1 capability into the canonical branch, adapted to the current architecture.

For each material decision record:

- candidates
- selected implementation
- evidence
- lost-capability check
- rollback SHA

Apply this specifically to PR59/60/61/62, Astra candidates, File Intelligence, Browser, Computer Control, Packaging, Video Studio, approvals/recovery, and other overlapping implementations.

## 7. Telegram must be proven as a real round-trip

Do not count contract tests alone as acceptance.

Required acceptance chain:

`Bossman task → approval needed → Telegram transport → authenticated owner response → approval consumed → task resumes → effect happens once → completion reaches owner`

This is owner scenario #15.

## 8. Image and Video should be proven in CI where possible

Image:

`fixture/provider adapter → Bossman/Image Studio operation → persisted artifact → reopen/retrieve → verify output`

Video:

`fixture → Bossman/Video Studio operation → FFmpeg render → output file → ffprobe validation`

Only GPU/provider-specific generation remains owner-hardware/provider evidence.

## 9. No new product wave until the first 20 scenarios pass

Do not start V9, another final branch, or unrelated large feature work.

Focus on:

- integration
- salvage
- packaging
- Windows
- real owner scenarios
- break/fix/retest

## 10. Required execution loop

Continue automatically using:

`BUILD → RUN → BREAK → FIX → VERIFY → CONTINUE`

Do not restart planning unless a genuine technical blocker requires it.

## 11. Next checkpoint format

At the next meaningful checkpoint report only:

- `Canonical SHA:`
- `Windows installed-product:`
- `Regression CI:`
- `Owner scenarios:`
- `P0:`
- `P1:`
- `Salvage remaining:`
- `Owner-hardware-only:`
- `Next action:`

The next product milestone is not “more tests” or “more commits”. It is:

**20 integrated owner scenarios implemented and executed on the canonical owner release path.**
