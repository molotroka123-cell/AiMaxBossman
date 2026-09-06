# Command Center crash matrix: effect before receipt

Scope: `command-center/bcc/engine.py` tool loop (V2 task engine). This note
documents behaviour proven by `command-center/tests/test_fable_crash_after_effect.py`;
it is not a runtime attestation and does not extend to the V3 `TaskJournal`
path, which has its own authenticated write-ahead semantics
(`AUTHENTICATED_JOURNAL.md`).

## The cell

| Crash point | Before | Now |
|---|---|---|
| after the non-idempotent handler returned, before the `tool_calls` receipt | no trace of the dispatch; the next attempt's replay guard found nothing and ran the handler again (duplicate irreversible effect) | a `started` row was written before dispatch; the next attempt sees it and does not execute |
| during an approved resume (`_resume_pending_tool`), same window | same duplicate, under an approval that authorized ONE execution | the approved call is `interrupted`; a fresh `effect_reconciliation` decision is required |
| takeover with no further request for the tool | orphaned in-flight state invisible | the sweep at run start marks orphans `interrupted`; finalize treats them as unobserved, never as success |

## States

`tool_calls.status` gains three explicit values:

- `started`: write-ahead. The action is dispatched; no outcome yet. Only
  non-idempotent tools (`ToolSpec.idempotent=False`) are journaled this way.
- `interrupted`: a previous attempt died between dispatch and receipt. The
  effect is unobserved. Set by the takeover sweep or on detection.
- `reconciled`: the owner decided the fate of an `interrupted` dispatch.

## Owner decision

The engine never guesses. When the same action (tool + canonical args) is
requested again and an `interrupted` or `started` dispatch exists in an earlier
attempt, the run parks behind an approval of kind `effect_reconciliation`
whose preview says the effect MAY ALREADY HAVE HAPPENED:

- approve: the owner asserts the effect did not happen. The action runs exactly
  once, and the prior row becomes `reconciled`.
- reject: the action does not run. The prior row becomes `reconciled`, the
  request is `rejected`.

For an ASK-policy tool the reconciliation question replaces the plain
"may it run" approval, so the owner makes one decision, not two.

## Finalization

`interrupted` and `reconciled` rows are handled like `denied`/`rejected` in
`bcc/finalize.py`: with a declared post-state contract, only a fresh
observation of the world can settle the obligation; without a contract the
task fails honestly. An unobserved irreversible effect is never a completion.

Invariants preserved: no duplicate irreversible effect after crash/retry/restart;
ambiguous post-crash state stays explicit; APPROVAL != POST_STATE.
