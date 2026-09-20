# FINAL SALVAGE LEDGER

Canonical target: `release/bossman-owner`.

This ledger records disposition of historically important work without globally merging old branches.

| PR | Historical value | Canonical disposition |
|---|---|---|
| #54 | local bundle; exposed missing installed UI | SUPERSEDED/PRESENT_BETTER: canonical packaging now builds wheels, embeds UI/build identity and has Windows/local-bundle workflows. Preserve PR as provenance. |
| #55 | repository hygiene without history rewrite | PRESENT_IN_CANON: legacy archives/runtime state are removed from current tree while history remains reachable. Preserve the intentional spec fixture. |
| #57 | full visual page sweep; dock/tokens/chat/tile fixes | PARTIALLY SUPERSEDED: canonical UI contains later visual/UX work and browser acceptance. Any unique visual findings remain provenance until rechecked by final UX crawler. Do not bulk-merge 559 files. |
| #58 | V7 convergence: deadlock, approval storm, budgets, streaming, model health, QA relay, OpenHands | PRESENT_BETTER/PARTIALLY PRESENT: these capabilities are represented in canonical command-center/core and current convergence decisions. Any unique code must be ported capability-by-capability, never merged wholesale. |
| #59 | Social Farm/Higgsfield line | PARTIALLY PORTED: canonical Social Farm uses stronger DB/job state primitives; remaining provider/session integration items are tracked in CONVERGENCE_DECISIONS.md. Hardware/account-specific selector validation stays OWNER_HARDWARE_REQUIRED. |
| #60 | File Intelligence / provenance convergence | PRESENT_BETTER_IN_OWNER: File Intelligence and run provenance are in canonical code/package paths; historical PR remains provenance. |
| #65 | VIP Demo synthetic fallback | REJECTED FOR RELEASE: synthetic response fallback may not enter owner-ready canonical because it violates no-fake-green. UI cleanup ideas may be ported separately only if independently justified. |

## Rule

A historical PR may be closed only when each unique useful capability is either present with evidence, explicitly ported, or explicitly rejected with reason. Closing a PR never deletes its history.
