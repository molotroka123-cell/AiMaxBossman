# Owner Hardware Acceptance Pack

Run only against the exact installed release candidate. Do not run from an editable checkout.

The machine-readable checklist is `manifest.json`. The local model-fleet acceptance target is `MODEL_STACK_2026-09-20.md`. The cloud augmentation and routing acceptance target is `CLOUD_STACK_2026-09-20.md`. Predicted real-machine failure points and the immediate engineering response are in `HOTSPOTS_AND_HOTFIX_PLAYBOOK.md`. The existing MVČR scenario remains authoritative at `tests/owner_scenarios/OWNER_HARDWARE_FIRST_RUN.md`.

## One owner command

From the installed package environment:

```powershell
python -m bcc.owner_acceptance --data-dir "$env:BCC_DATA_DIR" --output OWNER_HARDWARE_CERTIFICATION.json
```

Then follow the interactive HW-01…HW-13 checklist. Every case is one of PASS, FAIL, OWNER_ACTION_REQUIRED or NOT_TESTED. Missing credentials or a human-only login checkpoint is OWNER_ACTION_REQUIRED; missing product code is FAIL.

## Exact-SHA evidence rule

A GitHub workflow record is evidence only when jobs actually executed against the candidate SHA. `action_required`, skipped, cancelled, queued-only, or completed runs with zero jobs are not PASS and must never be inherited from another SHA. The Windows artifact and final owner certificate must name the same TESTED_SHA that produced the executed mandatory release jobs.

## Local + cloud acceptance lanes

The owner-hardware run must explicitly cover:

1. LLM / coding / reasoning
2. agents / computer-use / vision
3. tool-calling + structured output
4. image generation/editing
5. video generation/editing
6. cloud routing / privacy / budget / fallback

Do not merge these into one generic “local model works” check.

The local target fleet is documented in `MODEL_STACK_2026-09-20.md`. Cloud free/budget/premium/media routing and privacy/cost rules are documented in `CLOUD_STACK_2026-09-20.md`.

Internet or vendor benchmarks are discovery evidence only. A model becomes a Bossman default only after the same-task owner-hardware comparison on the exact Ryzen AI Max+ 395 / Radeon 8060S / 128 GB machine.

Strict JSON / JSON Schema acceptance must verify both model behavior and runtime schema enforcement. Malformed structured output must fail closed rather than silently entering a tool call.

No real payment, government submission, destructive external action or account mutation is permitted by this pack without the product's normal explicit approval gate.
