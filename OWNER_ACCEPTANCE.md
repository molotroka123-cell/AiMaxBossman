# BOSSMAN OWNER ACCEPTANCE

Canonical line: `release/bossman-owner`.

The release is owner-ready only when one exact SHA has:
- clean Windows install outside the repository checkout;
- mandatory exact-SHA CI complete;
- installed-product owner scenarios complete;
- zero software-fixable P0/P1;
- no unexplained dead UI control or UI→API mismatch;
- an owner-hardware pack for the remaining physical/account-bound checks.

## Owner flow

1. Install the exact Windows artifact.
2. Complete first boot until the UI says READY or lists concrete items requiring attention.
3. Run the installed `bcc.owner_acceptance` harness.
4. Execute HW-01…HW-13 from `tests/owner_hardware/manifest.json`.
5. Use `tests/owner_hardware/MODEL_STACK_2026-09-20.md` as the current local model-fleet acceptance target.
6. Use `tests/owner_hardware/CLOUD_STACK_2026-09-20.md` for cloud free/budget/premium/media routing, privacy and budget acceptance.
7. Keep real Telegram, local-model, real desktop-control and MVČR submission checks as OWNER_ACTION_REQUIRED until performed on the owner's machine/account.
8. Do not treat mocks, source imports, old-SHA runs, skipped jobs or clicks without verified effects as PASS.

## Model-stack acceptance

The local-AI acceptance is split into five operational lanes:

- LLM / coding / reasoning;
- agents / computer-use / vision;
- tool-calling + structured output;
- image;
- video.

A model is not promoted to the default route because it is newer, larger or stronger on an external leaderboard. It must win the relevant same-task comparison on the owner's exact hardware with evidence for stability, memory use, latency and completion quality.

Strict structured output must be backed by runtime schema enforcement where supported. Model-produced malformed JSON must not silently become a tool invocation.

The owner-hardware certificate should record the selected model per task, fallback behavior, owner interventions, memory/latency where relevant, and whether evidence came from a real local model, a real cloud model, a mock, or no model at all.

The final certificate must name TESTED_SHA and the Windows artifact SHA-256.

## Cloud acceptance

Cloud is auxiliary, not primary. Private/LOCAL_ONLY data must not leave the machine. Free→paid escalation must not happen silently. Premium routes require explicit approval, and provider auto-recharge remains disabled unless the owner explicitly enables it. Cloud acceptance is tracked separately from local-model quality.
