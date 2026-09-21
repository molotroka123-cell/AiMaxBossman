# FINAL SALVAGE LEDGER

Canonical target: `release/bossman-owner`.

This ledger records the release disposition of historically important work without globally merging old branches. `CLOSED_FOR_CURRENT_RELEASE` means the useful release-critical capability is present/replaced/rejected or explicitly moved outside the current owner-test promise; it does **not** mean every historical screenshot or experimental adapter was copied.

| PR | Historical value | Canonical release disposition |
|---|---|---|
| #54 | local bundle; exposed missing installed UI | **CLOSED_FOR_CURRENT_RELEASE — PRESENT_BETTER.** Canonical packaging builds the installed product, embeds UI/build identity and has local/Windows bundle workflows. Preserve PR as provenance. |
| #55 | repository hygiene without history rewrite | **CLOSED_FOR_CURRENT_RELEASE — PRESENT_IN_CANON.** Legacy archives/runtime state are removed from the current tree while history remains reachable. Preserve the intentional spec fixture. |
| #57 | full visual page sweep; dock/tokens/chat/tile fixes plus F1–F6 handoff | **CLOSED_FOR_CURRENT_RELEASE — RECHECKED/SUPERSEDED, NO BULK PORT.** Current Command Center has later browser/visual/installed-product acceptance. The material truth defect F3 (invented 125 GB RAM) is explicitly fixed in `bcc/features/resources.py`: missing metrics become unmeasured and resource admission fails closed. Historical presentation deltas that are not reproduced as current P0/P1 stay provenance/backlog rather than being merged across 559 files. |
| #58 | V7 convergence: deadlocks, approval storm, budgets, streaming, model health, QA relay, OpenHands, reality layer | **CLOSED_FOR_CURRENT_RELEASE — FOUNDATIONAL/PRESENT_IN_CANONICAL.** PR #67 is converged on the V7 line and canonical contains the release-critical approval, budget, retry/loop, streaming/model-health, evidence and worker protections. Advisory/reality experiments not required by the owner-test contract are PRE-EVO material, not a partial release merge. Never bulk-merge the 1,000+ file historical diff. |
| #59 | Social Farm/Higgsfield account-browser generation | **CLOSED_FOR_CURRENT_RELEASE_SCOPE — OPTIONAL LIVE ADAPTER NOT CERTIFIED.** Canonical Social Farm carries the durable job/state, restart, evidence and safety primitives used by the current product. PR #59 itself states its Higgsfield selector pack is unverified without a real account and cannot be promoted by fixture tests. Live account/selector validation is owner-hardware/credential evidence and is not a reason to merge the experimental branch into this freeze. |
| #60 | File Intelligence / provenance convergence | **CLOSED_FOR_CURRENT_RELEASE — PRESENT_BETTER_IN_OWNER.** File Intelligence and run provenance are in canonical code/package paths; historical PR remains provenance. |
| #65 | VIP Demo synthetic fallback | **CLOSED_FOR_CURRENT_RELEASE — REJECTED.** Synthetic success fallback may not enter the owner-ready canonical line because it violates no-fake-green. UI ideas may be reconsidered later only with independent justification. |
| #69 | image generation + Dashboard Next contract | **DEFERRED_TO_POST-FREEZE / PRE-EVO.** It is not a second Bossman and is not required to close the current owner-hardware candidate. Do not merge its contract merely to increase scope before the owner test. |

## Current release decisions

`docs/final/PRE_OWNER_FREEZE_DECISIONS.md` is the authoritative closure note for CONTROL-001, SECURITY-001, OS-64, Image Studio product-path proof, Telegram internal round-trip and intentionally fail-closed non-release evidence axes.

`docs/final/PRE_EVO_1_0_PROPOSAL.md` records the next-generation proposal without granting self-modification authority to the stable build.

## Rule

A historical PR is not a hidden release dependency. If a future audit finds a unique capability that is actually part of the current owner promise and absent from canonical, it must be reproduced as a concrete gap and ported capability-by-capability with tests. Closing or deferring a PR never deletes its history and never turns an untested live integration into PASS.
