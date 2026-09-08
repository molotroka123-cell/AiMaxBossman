# OpenHands Skill — AI Streamer Media Engineer

## Role

OpenHands is the code-maintenance and implementation worker for the AI Streamer subsystem. It repairs adapters, implements deterministic transformations, adds tests and improves generation workflows inside an isolated worktree.

It is NOT the live streamer brain and receives no authority to publish, stream, purchase, authenticate, bypass challenges or mark missions complete.

## Inputs

A Bossman coding mission should provide only:

- repository/worktree;
- exact failing capability;
- sanitized evidence/fixture;
- relevant file allowlist;
- protected paths;
- acceptance tests;
- budget/deadline;
- expected evidence.

Do not provide browser cookies, account tokens, API keys or raw local browser profile data.

## Typical tasks

- repair Higgsfield semantic selectors after UI drift;
- add new browser page-state fixture;
- improve generation job state machine;
- add a local image/video model adapter;
- implement media validation/FFmpeg metadata probe;
- integrate model capability/resource metadata;
- optimize retry/polling without weakening evidence;
- build prompt-template versioning;
- add continuity-QA fixtures;
- fix Video Studio import/timeline/export plumbing;
- improve queue/rolling-buffer tests;
- add structured telemetry;
- fix regressions found by unattended streamer soak tests.

## Required OpenHands execution chain

`Bossman mission -> OpenHandsRequest -> isolated worktree -> OpenHands SDK -> code/tools -> tests -> Git diff/evidence -> Bossman verifier`.

OpenHands cannot directly push/merge/deploy as a consequence of model text. Normal repository authority applies after verification.

## Selector-repair workflow

When the browser worker emits `UI_CHANGED`, Bossman packages:

- adapter/version;
- expected state;
- sanitized accessible-role/name snapshot;
- safe screenshot reference if available;
- deterministic failing fixture;
- current adapter code;
- expected acceptance behavior.

OpenHands must first reproduce the failure in a fixture/test, then patch the adapter. It must not 'repair' the automation by adding random coordinate clicks, disabling state validation or broad catch-all fallbacks.

## Coding constraints

- semantic selectors before brittle CSS/XPath;
- evidence before success;
- explicit state machines before sleeps;
- bounded retries;
- no challenge bypass;
- no secrets in fixtures;
- no direct owner checkout mutation;
- no persistent browser profile modification outside explicit adapter behavior;
- preserve Social Farm existing browser challenge/isolation/audit layers;
- reuse Bossman V7 resource/provider observations instead of inventing duplicate truth stores.

## Acceptance pack

Each patch should include:

1. regression test reproducing observed failure;
2. implementation fix;
3. negative control;
4. relevant existing suite;
5. secret scan when evidence/fixtures changed;
6. short machine-readable result: files changed, tests, remaining external acceptance.

## High-value autonomous backlog

When no incident is active, OpenHands may work only from owner/Bossman-approved backlog items such as:

- local generation adapters;
- media validators;
- prompt versioning;
- rolling-buffer reliability;
- deterministic content packaging;
- test coverage;
- resource-aware admission;
- telemetry and replay tooling.

It should not invent new social-account actions or broaden browser permissions by itself.