---
name: boost-autopilot
description: Run and manage Bossman Boost Autopilot (/api/autopilot/*, autopilot.run_pipeline, bossblock audit). Combines CUA-Recovery pre-step state guards, AgentProofBench independent post-state verification, TimlMemBench scoped temporal memory, secret redaction, and 5-layer BossBlock 3D world readiness auditing.
---

# Bossman Boost Autopilot (`boost-autopilot`)

Use this skill when the owner or an agent needs closed-loop automated execution and verification in Bossman:
- Running automated health & hygiene checks (`system_health_and_hygiene`)
- Auditing 5-layer 3D game world captures (`bossblock_world_audit` / `python -m bossblock audit <folder>`)
- Generating sanitized daily audit digests with cryptographic proof receipts (`daily_digest_and_proof`)
- Verifying any artifact via AgentProofBench (`POST /api/autopilot/verify-artifact`)

## Architecture & Guarantees

1. **Pre-Step State Barrier (CUA-Recovery, `bcc/v2/recovery.py`)**:
   - Every autopilot run executes `StepStateGuard.verify_before_next_action()` before committing artifacts or memory facts.
   - Detects mid-step faults (`wrong_form_field`, `closed_dialog`, `failed_save`) and either repairs from checkpoint (`rollback_and_retry`) or halts before irreversible execution (`halt_before_irreversible`).
2. **Independent Post-State Verification (AgentProofBench, `bcc/v2/verification.py`)**:
   - Captures `initial_snapshot` before writing, re-reads the artifact from disk (`file:reopen`), and produces a `CriterionProofReceipt` (`rule_version="apb-2026.10.31-boost-v1"`, `proof_sha256`, `false_claim_caught`).
3. **Scoped Temporal Memory Lifecycle (TimlMemBench, `bcc/v2/memory/facts.py`)**:
   - Verified runs are recorded in `FactStore` under `subject="autopilot:<pipeline>"`, `predicate="last_verified_run"`, with `replace_current=True` and strict `scope` isolation (`work` / `personal` / `public`).
4. **Secret Scrubbing (`bcc/features/pult_report.py`)**:
   - Notes and digests are automatically scrubbed via `redact()` / `contains_secret()` before saving or reporting.

## CLI & API Usage

```powershell
# Run full Boost Autopilot verification & generate audit receipt:
python tools/boost_autopilot_cli.py --output docs/audits/2026-10-31-boost-autopilot-pipeline/receipt.json

# Audit a 5-layer BossBlock 3D world folder (e.g. Dnipro city center):
python -m bossblock audit apps/bossman-mobile/bossmobile/ui/dnipro_assets --no-write --json
```

### REST Endpoints (`:8801`)

- `GET /api/autopilot/status` — current configuration, counters, and recent run receipts.
- `POST /api/autopilot/run` — run `system_health_and_hygiene`, `bossblock_world_audit`, `daily_digest_and_proof`, or `all`.
- `POST /api/autopilot/config` — update `enabled`, `interval_minutes`, `default_pipeline`, `auto_repair`, `scope`.
- `POST /api/autopilot/verify-artifact` — standalone AgentProofBench verification of any file artifact.
