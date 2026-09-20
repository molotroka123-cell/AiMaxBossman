# ARCHITECTURE

Bossman is organized around a single owner control plane.

- Command Center: owner UI, API, approvals, tasks, provider/model registry, media studios and persistent owner state.
- Bossman Core: agent/runtime capabilities, computer operator, gateway, context/retrieval, browser/tool contracts and safety boundaries.
- Apps: separately scoped product surfaces that use canonical capability, security and persistence contracts.
- Release evidence: exact-SHA workflows, owner scenarios, installed-product checks and owner-hardware acceptance.

## Trust model

Every side effect crosses policy/approval boundaries. Owner approval is scoped to the actual action and must not be replayed into a different task, revision or effect. Observations are re-read before and after consequential computer actions. Unknown external outcomes are reconciled, not blindly repeated.

## Release model

`source -> candidate SHA -> exact-SHA CI -> Windows artifact -> clean install -> owner acceptance -> freeze`

EVO 1.0 is a future isolated-candidate promotion system and is not enabled in the pre-EVO release.
