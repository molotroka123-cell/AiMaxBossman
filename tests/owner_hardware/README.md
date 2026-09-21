# Owner Hardware Acceptance Pack

Canonical candidate: `release/bossman-owner`. Run the exact selected installed Windows artifact, not an editable checkout.

Start with the [owner guide](../../OWNER_ACCEPTANCE.md) and [installation instructions](../../INSTALL.md). The authoritative checklist is [manifest.json](manifest.json): **HW-01…HW-13**.

## First command

From PowerShell in the extracted full Windows application folder:

```powershell
.\Evening-Test.cmd
```

This is the basic installed-product check, **not automatic execution or certification of all 13 hardware cases**. Follow the owner guide afterward. For the narrower configured-model harness, use the archive's Python, not an unrelated global interpreter:

```powershell
.\runtime\python.exe -I -m bcc.owner_acceptance --data-dir "<configured-test-data-dir>" --output "owner-acceptance.json"
```

Supply the actual isolated configured data directory. Only fresh output from the current invocation and the correct artifact can be accepted; a leftover JSON file after process failure is not new evidence. The repository-root PowerShell wrapper is not a substitute for verifying the shipped command path.

## Evidence and safety

Record PASS / FAIL / OWNER_ACTION_REQUIRED / NOT_TESTED per case. A missing credential/human-only checkpoint is OWNER_ACTION_REQUIRED; missing code is FAIL. The certificate must identify TESTED_SHA and the SHA-256 of the exact Windows ZIP.

Queued, skipped, cancelled, action_required, zero-job and wrong-SHA runs are not PASS. Verify actual checkout/build/test identity and executed jobs across relevant push, PR and dispatch events. A new documentation commit cannot inherit an older certificate.

Cover LLM/coding, agents/computer-use, tool/schema reliability, image and video separately, plus cloud privacy/budget/fallback. Test postconditions, persistence, cancellation and recovery through product paths. Runtime validation must reject malformed tool arguments before any effect.

## Supporting specifications

- [Local candidate fleet](MODEL_STACK_2026-09-20.md) — verify official IDs, license, runtime and same-task results on Ryzen AI Max+ 395 / Radeon 8060S / 128 GB before promotion.
- [Cloud candidates and policy](CLOUD_STACK_2026-09-20.md) — revalidate provider terms/prices; no silent paid escalation or private-data dispatch.
- [Predicted failures and hotfix process](HOTSPOTS_AND_HOTFIX_PLAYBOOK.md).
- [MVČR first-run scenario](../owner_scenarios/OWNER_HARDWARE_FIRST_RUN.md) — prepare the reviewable package, stop at WAIT_APPROVAL.

No real payment, government submission, live trading or destructive external action is part of the safe first run. No candidate model becomes default merely because it appears in these documents. EVO remains recommendation-only.
