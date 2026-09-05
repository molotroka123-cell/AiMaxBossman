# Threat model and limitations

Protected against at the module boundary: model-text done, non-verified knowledge
labels used as completion proof, changed plan resume, receipt replay to different
mission/run/expectation, corrupted signatures, stale evidence, stale local owner/fence,
blind crash retries, concurrent single-host claims, per-mission budget overspend,
private dependency exports, silently truncated dependency context and changed benchmark
criteria in candidate comparisons.

Trusted computing base: host runtime, policy loader, verifier keys and effective identity
registry, observer adapters, action catalog, clocks, local filesystems/DB and the calling
host's reconciliation decisions. An agent with arbitrary Python execution in this same
process or write access to state/keys can subvert this package. Keep these outside the
agent sandbox. HMAC is NOT a proof that the world observation itself was honest.

Dispatch binding includes mission digest, effect ID and monotonic local fence. It prevents
using an earlier ordinary receipt for a later dispatch. Only a trusted observer may sign
this binding; a malicious verifier can still forge observations. Targets must be resolved
canonically at IO time by adapters to avoid DNS/path races. This package is not their fix.

SQLite WAL + FULL sync provides single-host transactional durability subject to the OS,
filesystem and storage guarantees. There is no multi-host Fleet lock service here, no
cross-database atomic completion and no exactly-once guarantee for arbitrary external
APIs. A DB fence does not cancel an already transmitted HTTP request. If the provider
cannot authoritatively distinguish absent from in-flight, manual review is necessary.
The kernel never auto-retries unknown effects, including a crash before actual dispatch.

Mission obligations describe a finite declared scope. The compiler cannot discover every
hidden side effect, prove unrestricted natural-language intent, or prove absence of all
regressions. The host must bind test evidence to code/artifact SHA, not to generic true.
Each effect needs a dedicated post-state obligation; scheduler dependency order remains
owned by existing Bossman. Reused provider idempotency keys must be stable across attempts.

Counterfactual comparison works only over a declared snapshot. Prediction quality and
uncertainty estimation are supplied by domain adapters; no predictive simulator is shipped.
Autonomy and utility formulas are heuristics, not calibrated mathematics of intelligence.
The learning ledger records correlation/differences; cause text remains INFERRED. Fact
supersession, route outcome and benchmark truth must come from trusted admission/verifier
logic. They are not cryptographically attested by the support module itself.

Clinical facts may exist in the local state. SQLite is not encrypted by this package.
Use protected local storage/encryption and retention policy. Even delta keys and lesson
text can be sensitive: pass only redacted descriptions or digest references to learning.
No logs or examples in this archive contain user clinical data or credentials.
