# V4 Continuity — reviewed implementation transport

This contains a real 82,074-byte source patch, not generated placeholder modules.
Base: `9d3f82ac0d26c5b88b408f0c470a3acfae700f98`.
Branch: `sol/v4-continuity-hardening-20260906`.
The binary parts are split gzip transport; they do not execute and are not runtime files.
The materializer checks each Git blob hash, the joined gzip/patch SHA-256,
all ten edited baseline hashes, all twenty output hashes and patch paths.
It aborts on drift. No force-push, feature activation or paid calls.

## Contents of the implementation

- Canonical OrganizationStore journal head witness and TaskJournal schema 4 opt-in.
- Old-valid-signed-journal rollback detection, propagated through organization/Fleet/skills.
- Full action/arguments/expected-effect/run/implementation-bound approval identity.
- Current BCC agent permission and approval revocation checks before effects.
- Semantic visual-state revalidation inside the host execution guard.
- Mandatory-context duplicate retention and finite promotion-metric validation.
- Six new test files, detailed threat model and Fable handoff.

## Local evidence and its limit

Linux/Python 3.13 extracted integration tree, NOT a full current-Fable checkout.
Ten edited runtime baselines matched remote blob identities. Completed runs:
271 portable Core tests; 26 BCC/golden tests (10 deselected); 5 child-process/crash
and bridge tests. These selections overlap; do not sum them into a unique count.
Interrupted earlier attempts do not count. No supported-platform/full exact-SHA,
real LLM retention, 3x performance or whole-V4 certification is claimed.

## Materialization

The branch-only publication workflow applies the verified bytes, commits only
allowlisted files, and fast-forward pushes ONLY this isolated source branch.
It cannot overwrite Fable or primary. Subsequent tests record the materialized SHA.
A publication workflow run's original head SHA is not that new source commit;
full exact-SHA CI is still required on the latter.

For manual work in a clean disposable checkout:

```bash
python handoffs/v4_continuity_patch/materialize.py
python handoffs/v4_continuity_patch/materialize.py --apply
```

Run only if source files have not already been materialized. After materialization,
read `docs/v4/FABLE_CONTINUITY_HARDENING_20260906.md` and
`handoffs/V4_FABLE_IMPLEMENTED_20260906.md`. Review the plaintext runtime diff.
A pack, applied patch or green targeted test is NOT V4_COMPLETE.

Keep the new anchor flag OFF by default. Do not auto-migrate old journals.
Windows input ownership, real model measurements, Generation B/C, 3x targets,
long-horizon/platform/migration acceptance and the whole M0–M11 plan remain open.
