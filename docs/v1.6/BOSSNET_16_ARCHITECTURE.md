# Bossman 1.6 — BOSSNET Cognitive Infrastructure

1.5 builds the autonomous local workflow runtime. 1.6 changes scale.

Five pillars:
1. Distributed Brain / elastic compute fabric.
2. Specialist Model Foundry.
3. Temporal Knowledge Fabric.
4. Simulation World / digital twin.
5. Provider Fleet + economic scheduler (specified separately).

## Non-goals
- no uncontrolled self-replication;
- no automatic account creation/payment;
- no live trading authority;
- no self-certified model promotion;
- no hidden external side effects.

## System shape

```
OWNER
  |
BOSSNET CONTROL PLANE
  +-- Node Registry / Scheduler
  +-- Provider Fleet
  +-- Knowledge Fabric
  +-- Simulation World
  +-- Model Foundry
  +-- Evidence / Verifier
        |
        +-- AI Max local node
        +-- rented GPU nodes
        +-- mobile/edge nodes
        +-- approved cloud APIs
```

Every remote action has task identity, budget, capability allowlist, evidence
receipt and revocation/STOP propagation.
