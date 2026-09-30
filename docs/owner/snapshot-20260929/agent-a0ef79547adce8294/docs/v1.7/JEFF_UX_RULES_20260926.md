# Jeff UX rules + Bossman limit-saving improvements

Status: feature-branch contract. These rules are intended for the Jeff UX integration agent and should not weaken the current 1.5–1.7 release gates.

## 1. Keep Jeff a presentation/participant surface

Jeff may:
- chat;
- use participant memory;
- search/read the web through the participant-safe path;
- understand participant-owned images;
- create/edit media through the existing Studio broker when policy allows;
- speak and accept dictation;
- expose participant presentation settings.

Jeff may not:
- create owner tasks directly;
- call Computer Use;
- call shell/terminal;
- approve owner actions;
- inspect owner-private memory;
- change model/provider secrets;
- spend money;
- trade;
- write GitHub.

A richer UX does not grant richer authority.

## 2. One Bossman, one backend

Do not create:
- a second FastAPI backend;
- a second task engine;
- a second memory authority;
- a second Telegram transport;
- a second Studio;
- a second Jev;
- a second model registry.

Jeff Desktop attaches to the exact-build existing Bossman backend.

## 3. Cheap-first agent workflow

Before asking a strong coding model to inspect Jeff integration:

1. run `tools/jeff-ux-fast-check.ps1`;
2. run `tools/jeff_ux_packet.py`;
3. read only the reported changed files;
4. reproduce one failing targeted test;
5. provide the strong model only:
   - failure;
   - invariant;
   - relevant files;
   - minimal diff;
   - exact target test.

Do not send the full repository or full passing logs.

## 4. Strong-model escalation gate

Default coding order:

```text
deterministic/static checks
→ existing tests
→ local coder
→ verified free coder
→ bounded GLM-5.3-Flash
→ strong frontier only for architecture-sensitive blocker
```

Use the existing Coding Limit Saver policy. Do not invent a parallel quota router.

A strong model should be reserved for:
- a confirmed P0/P1;
- cross-surface architecture bug;
- repeated blocker after cheap attempts;
- final integration review.

## 5. Context budget

For an ordinary Jeff change, the coding packet should contain at most:
- objective;
- one failing test/error;
- authority invariant;
- 1–4 directly relevant files;
- neighbour API contract only when needed.

Never paste:
- all PIT source;
- all Command Center source;
- full CI logs;
- historical planning docs

unless the targeted evidence proves they are needed.

Goal:
`VERIFIED_FIX / CONTEXT_TOKEN`.

## 6. Test order to save limits and time

Use:

```text
syntax/compile
→ static authority scan
→ Jeff isolation tests
→ Jeff browser smoke
→ affected PIT/desktop neighbours
→ real Windows smoke
→ Telegram parity
→ only then broader regression
```

Do not run a 50-minute Command Center suite after every CSS/UX edit.

## 7. Deterministic authority scan before LLM review

`tools/jeff_ux_packet.py` blocks executable references to:
- owner task creation;
- generic mutation escape hatch;
- control-plane API;
- terminal API;
- browser control API;
- owner coding/OpenHands API;
- shell authority;
- computer authority.

If the packet is BLOCKED, fix that first.

## 8. Browser chat rule

The current web composer is intentionally local UX preview.

Do not connect it to `/api/tasks`.

When live web chat is added, it must use the existing participant runtime semantics:

```text
participant identity
→ public_guard
→ participant memory
→ participant-safe route
→ render_jeff_reply
```

The browser transport is only a new ingress/egress adapter, not a new brain.

## 9. Voice architecture

Keep voice as a brokered presentation capability.

Stage A:
- browser/system TTS;
- browser/system STT;
- zero additional model spend.

Stage B:
- verified local TTS engine;
- same `voice_id` profile;
- audio artifact verification.

Stage C:
- Telegram voice through existing transport.

Any remote TTS:
- explicit owner authorization;
- known price;
- no silent fallback.

Voice never changes tool permissions.

## 10. Avatar architecture

Avatar is presentation metadata only.

Initial IDs:
- `aurora`;
- `ember`;
- `mono`;
- `cobalt`.

Later participant profile:
- `avatar_id`;
- optional approved image asset reference.

Avatar changes must:
- remain per-participant;
- survive restart;
- be deletable with participant profile;
- never affect another participant.

## 11. Telegram parity

Desktop and Telegram must eventually share:
- participant identity;
- own memory;
- privacy settings;
- avatar/voice presentation settings;
- same allowed/denied policy.

They must not share:
- owner-global memory;
- owner approvals;
- owner control authority.

## 12. Bossman improvements learned from this branch

### A. Feature branch packets
Every isolated feature branch should be able to emit:
- source SHA;
- canonical SHA;
- merge base;
- changed files;
- unexpected files;
- boundary violations;
- targeted tests.

This avoids expensive repo-wide rediscovery.

### B. Fast-check scripts
Every large subsystem should have a cheap deterministic preflight before model escalation.

### C. Exact-build shared-service rule
A satellite UX may reuse a Bossman backend only when build identity matches exactly.

### D. Shared backend lifetime
A satellite window must never terminate a shared backend used by the owner application.

### E. Presentation profiles
Keep human-facing avatar/voice/style metadata separate from:
- authority;
- secrets;
- model routing;
- security telemetry.

### F. Capability honesty
Unavailable functionality should be visibly locked/labelled rather than rendered as a working button.

## 13. Merge gate

The Jeff integration candidate is mergeable only when:

```text
STATIC_AUTHORITY_SCAN=PASS
JEFF_ISOLATION=PASS
JEFF_BROWSER=PASS
WINDOWS_OPEN=PASS
JEFF_SHORTCUT=PASS
BOSSMAN_STILL_OPEN=PASS
TELEGRAM_POLICY_PARITY=PASS
CROSS_USER_LEAKS=0
OWNER_MEMORY_LEAKS=0
JEFF_PC_CONTROL=DENIED
BOSSMAN_TARGETED_REGRESSION=PASS
```

Until then it remains an isolated feature candidate.
