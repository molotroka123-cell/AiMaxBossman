# Epoch 6 — Rollback, Risk & Safety

## Safety boundary

Epoch 6 optimizes latency and resource use, not truth semantics.

Never move these guarantees to lossy/background-only execution:

- durable intent before irreversible effect;
- signed/verified evidence;
- effect receipts;
- owner approval/revocation;
- budget settlement where authoritative;
- fencing/lease ownership;
- completion postconditions;
- recovery state needed to avoid duplicate effects.

## Common dangerous optimizations

### Caching stale observations

Allowed only when applicability/freshness contract says observation remains valid. Owner approval followed by changed foreground/UI state must invalidate dispatch where current policy requires it.

### Async persistence everywhere

Only non-authoritative telemetry may batch/loss-tolerate. Mission truth must survive crash.

### Bigger concurrency

Can reduce queue time and worsen total throughput through paging, disk contention and model thrash. Admission must use measured envelopes.

### Long-lived caches

Must be scoped by identity/revision/config and invalidated deterministically. No evidence or authorization cache across incompatible mission boundaries.

### Timeouts/retries

Increasing timeout is not a root-cause fix. Retry after ambiguous external mutation must reconcile first.

## Rollback contract

Each perf patch needs:

- baseline/candidate metric delta;
- feature/config switch if rollback cannot be a simple revert;
- data/schema compatibility note;
- no irreversible migration solely for performance without migration/rollback proof;
- exact commit that restores prior behavior.

## Kill criteria

Revert/reject immediately if candidate:

- reduces verified success;
- increases unsafe/duplicate effects;
- delays Stop/Pause/revoke beyond agreed budget;
- introduces new memory leaks/OOM/paging;
- creates p95/p99 stalls even if average improves;
- breaks restart/recovery;
- loses user data/edit state;
- depends on machine-specific tuning without safe fallback.

## Evidence language

Use `MEASURED`, `CODE_EVIDENCED`, `HYPOTHESIS`, `NOT_RUN`, `INSUFFICIENT_EVIDENCE`. Avoid marketing multipliers until the corresponding benchmark artifact exists.