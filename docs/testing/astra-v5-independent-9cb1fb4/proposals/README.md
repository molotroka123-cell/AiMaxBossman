# Proposals — NOT APPLIED, NOT EXECUTED

These are review inputs for Opus, the sole integrator. They are data files, not
changes to the protected implementation. Only syntax was checked. No patched
source was run under the pinned tested SHA.

`01` addresses failure monotonicity and exact health types. It does not implement
attestation, revision binding, a production caller or durable canary activation.
`02` addresses unconditional task-ID uniqueness, split-lane uniqueness,
disjointness/coverage, baseline binding and finite scores. It preserves existing
15 measured / 5 holdout / 20 total floors. A huge integer outside float range
also needs a deliberate fail-closed error policy before integration; the
proposal is not a complete numeric-input hardening audit.

The evidence boundary is NOT fixed by checking a reference prefix or adding
another signer. Resolve canonical evidence, validate the verifier identity,
objective/revision/run/attempt/source binding and freshness, and atomically
recheck the consumed verdict at the actual activation boundary. Keep failed
revisions latched across restart. Reuse the existing policy/Treasury/ledger.

For promotion, the syntactically valid retention reference used by tests is a
fixture, not real retention proof. A production caller must resolve the actual
paired report and bind its baseline, candidate, task set and scope. Do not use
these helper-level eligibility results as an authorization to activate.

Diffs use zero context so tracked patch documents pass whitespace hygiene.
Review against the declared source blob, then validate with
`git apply --check --unidiff-zero <proposal.patch>` before any authorized integration.
Do not apply a zero-context patch to a drifting file without reviewing the hunk.
