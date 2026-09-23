# MODEL FLEET GREEN — Owner Hardware

Target: Ryzen AI Max+ 395 / Radeon 8060S / 128 GB unified memory.

## Goal

Bossman exposes one authoritative Models/Fleet surface shared by UX, CLI and agent router.

A model is not GREEN because files exist. GREEN requires:
1. exact model/revision/quant recorded;
2. runtime/backend starts;
3. health query returns a real generation;
4. assigned capability task succeeds;
5. tool/schema contract succeeds where relevant;
6. unload/reload or restart is verified;
7. measured memory/latency is recorded.

## Required candidate inventory

### LLM / coding / agents
- Qwen3.8-27B — MAIN baseline.
- Xing4.0-29B-A4B — FAST/AGENT first-class challenger.
- Qwen3.6-35B-A3B — current fast challenger.
- Occamy-1.0 — long-horizon agent candidate.
- Qwen3.8-Flash-Next — heavy experimental route.
- gpt-oss-120B — on-demand independent verifier.

### GUI / vision
- Nex-N2.5-mini or the best verified local GUI/vision route already present in Bossman.
- If a named model is not actually available from a trustworthy source/runtime, mark DISCOVERY_BLOCKED rather than inventing it.

### Image
- Qwen-Image-2512.
- FLUX.2 Klein 4B candidate.
- Z-Image Turbo candidate.
- Heavy image candidates only after memory/runtime preflight.

### Video
- LTX-2.5.
- Wan 2.2 A14B.
- HunyuanVideo 1.5.

### OCR
- baidu/Unlimited-OCR as a specialist candidate if its code/runtime is reviewed and runnable on the target machine.

## UX states

Each row has exactly one state:

`NOT_INSTALLED | DOWNLOADING | VERIFYING | INSTALLED | LOADING | READY | TESTING | GREEN | DEGRADED | BLOCKED_RUNTIME | BLOCKED_HARDWARE | BLOCKED_LICENSE | ERROR`

Do not use one generic red/green boolean.

Show:
- display name;
- exact model ID;
- revision/hash;
- quant;
- role;
- backend;
- context;
- disk size;
- current RAM/VRAM/unified memory;
- load time;
- generation speed;
- last real test;
- last failure reason;
- unload button;
- test button;
- default/fallback role.

## Runtime policy

Do not keep all heavy models resident.

Recommended initial residency:
- MAIN or FAST;
- lightweight GUI/vision worker if measured useful;
- everything else on demand.

Heavy verifier/image/video workers get leases and unload after bounded idle time. During video rendering or future Unreal builds, release unnecessary LLM allocations.

## Tests per LLM

1. short chat;
2. repo navigation;
3. scoped code patch;
4. strict JSON;
5. exact tool selection and arguments;
6. two-tool chain;
7. forced tool failure and recovery;
8. restart/resume;
9. final-state verification.

Xing4 vs Qwen3.6 must use identical prompts, tools, context and limits.

Record:
`verified_success, tool_accuracy, schema_accuracy, time_to_verified_result, owner_interventions, peak_memory, tok_s`.

## Media tests

A media model is GREEN only from a **newly generated artifact**:
- record model/revision/runtime/prompt/seed when available;
- output hash;
- decode/open result;
- actual dimensions/duration;
- visual sanity;
- cancellation/restart behavior where supported.

Imported files, FFmpeg test patterns or old outputs do not count as AI-generation PASS.

## Final report

`MODEL | REVISION | QUANT | BACKEND | LOAD | REAL_TASK | TOOLS | RESTART | RAM | SPEED | RESULT`

Expected result is not "all GREEN at any cost". The expected result is:
- all actually compatible models GREEN;
- every incompatible/unavailable one with a specific reproducible blocker;
- no fake model identity and no silent cloud fallback.
