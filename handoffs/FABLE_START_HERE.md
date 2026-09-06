# Fable: independent V4/V5 system hardening

## Read this before the archive

Repository: `molotroka123-cell/AiMaxBossman`.
Integration branch: `claude/bossman-control-v03-43igbk`.
Work on your own `fable/system-spine-hardening` branch/worktree from the latest
integration HEAD. Preserve any existing uncommitted work before changing branches.

The archive is a planning handoff, NOT a current audit, implementation attestation,
or complete mirror of repository documentation. Its historical snapshot is
`6464d523929c199bcefaab8311d2b1a39245101b`. Re-read current code, branch/PR provenance,
epoch plans and exact-SHA checks before deciding what remains. User-reported V4/V5
commits must be resolved and checked for ancestry, not assumed merged.

## Archive integrity

File: `handoffs/Fable_Handoff_Pack_2026-09-06.zip`
Size: 8359 bytes.
SHA-256: `58df52913663690b624b3715daa156a1805a2a8d0ece2e8d4d01040924527d17`
Git blob: `01832e221efaa5f56f78ada9417faaed9b340d70`

There are 11 UTF-8 entries: nine Markdown documents, one mini prompt and a JSON
manifest. `MANIFEST.json` lists the other ten entries. Validate before reading:

```sh
python -m zipfile -t handoffs/Fable_Handoff_Pack_2026-09-06.zip
```

The earlier committed copy was 8356 bytes with broken ZIP offsets. This replacement
is the intact local artifact, verified by CRC and full entry reads. The exceptional
owner-authorized archive placement does not weaken the general ZIP quarantine.

## Independent engineering lane

Do not wait for Astra. Do not duplicate its feature implementation or rename epochs
already planned on another branch. Briefly map existing canonical components, then
implement testable fixes. "System Spine" means shared contracts/adapters around
existing engines, not eight new parallel kernels or an alternative source of truth.

Your priority is confirmed correctness/security defects, followed by cross-system
Execution Truth, scoped context/capability selection, recovery, intelligence
preservation and integration acceptance. Prove effects on at least three independent
subsystems before claiming a system-wide improvement. Safety rules still apply to all.

For each finding: reproduce -> root cause -> smallest fix -> narrow regression ->
cross-system regression -> hostile retest -> coherent commit -> push your branch.
Fetch before integration. Use a PR against the latest integration branch; never
force-push, overwrite concurrent work, auto-merge untested changes or call an
unmerged branch's result a primary-branch result. If paths overlap, work elsewhere
or make a minimal, clearly identified integration patch after the other change lands.

Use specialist subagents only if actually available, with separate worktrees and
one integrator. Do not invent agent execution or independent review. Prefer two or
three bounded workstreams over duplicate whole-repository audits.

## Intelligence-preservation corrections

The existing metric gate is not itself a model experiment or proof of preserved
intelligence. Build/reuse a genuine paired runner: same model/version/quantization,
same items, decoding settings and resource budgets. Pin the evaluated SHA and record
actual executed routes. Use fair tool availability in tool tasks; a tool-free RAW
lane is not an equivalent baseline for tool execution.

98% retention is a relative ratio, NOT a two-percentage-point accuracy margin.
Twenty observations per metric is a smoke-test floor, not statistical proof of a
2% non-inferiority claim. Report paired uncertainty/confidence intervals, category
results and INSUFFICIENT_EVIDENCE when precision is insufficient. Never hide a
reasoning regression inside an average or count repeated identical fixtures as
independent evidence. Keep holdout tasks out of training/skill promotion.

Context pruning must not remove required facts, and a data label alone is not a
prompt-injection security boundary: enforce authorization/privacy/budget in code.
Missing samples, runner credentials or hardware never become fabricated PASS.
Do not spend on providers or external effects without an explicit configured budget
and authorization.

## Delivery

Return exact branch/commit/PR, changed paths, before/after failures, executed test
counts and environments, known open items, and exact-SHA CI status. Keep runtime
proof separate from documentation and deterministic fixtures. No blanket claim that
all holes are closed. Save useful tested progress before execution limits run out.
