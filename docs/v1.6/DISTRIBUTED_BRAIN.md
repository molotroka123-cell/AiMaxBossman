# Pillar 1 — Distributed Brain

Goal: one Bossman control plane can elastically use AI Max, approved rented GPUs,
mobile/edge devices and API providers without moving authority away from owner.

## Node contract
Each node advertises:
- node_id and attested owner binding;
- CPU/RAM/GPU/VRAM;
- supported runtimes/models/tools;
- locality/privacy class;
- current load;
- estimated marginal cost;
- heartbeat;
- capability allowlist.

## Scheduler
Task -> capability requirements -> eligible nodes -> cost/latency/quality score ->
lease -> execution -> evidence -> verifier -> release.

Leases expire. A dead node cannot keep authority.

## iPhone/edge role
Mobile is primarily sensor/control/approval:
camera, microphone, notifications, owner confirmation and small local inference.
It is not a loophole for provider quotas.

## Failure design
- heartbeat loss -> revoke lease;
- duplicate worker result -> idempotency key;
- network partition -> no new destructive authority;
- task retry -> new attempt id, same logical task id;
- STOP -> fan out to every active lease;
- remote node never owns canonical memory directly.

## Acceptance
Kill one worker mid-workflow and prove another worker resumes from durable
checkpoint without duplicate side effect.
