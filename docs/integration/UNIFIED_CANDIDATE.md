# Unified Bossman candidate — 2026-09-06

Status: **INTEGRATED_CANDIDATE / ACCEPTANCE_PENDING**.
This is an integration checkpoint, not an assertion that all subsystems work.

## One integration branch

`integration/bossman-unified-20260906`

| Input | Exact snapshot | Treatment |
|---|---|---|
| Primary branch | `b85ab17c3ec46c7ba10ca33270cc6d2b95e83a33` | Preserved as an ancestor |
| Published Fable closure | `dc0d149b91bee6617f5d2d207e99cd744eb24905` | Merged through PR #16, source branch untouched |
| Combined code | `2c88d24d9252c264498ac1ba65fa5cb4cd4dc89d` | Both inputs are parents; full tests still required |
| Astra combined checkpoint | `9f11da9a037906bbbf6251f9e028b8ac3e1cfed9` | Already included through primary; do not merge old precursor copies over it |

The candidate includes the existing Continuity/Steward contracts, Web Designer,
unified native Video Studio, intelligence-gate code and Fable handoff. The five
additional published Fable commits touch 32 paths: approval/restart safeguards,
tool handling, Windows contracts, Core isolation, Video Factory, DOM preservation,
regressions and design documentation. A successful Git merge is NOT a passing
behavioral integration test.

Earlier `astra/epoch4-evolution`, `astra/web-designer-properties`,
`astra/video-frame-edit`, `codex/video-studio` and related branches are development
provenance, not separate apps to install. Their features were integrated with
fixes through the Astra combined checkpoint. Some commits were imported rather
than merged verbatim: do not infer ancestry from matching feature names.
See [Astra's integration record](../v4/INTEGRATION_2026-09-06.md).

## Product surface

- Keep one existing Command Center router and one registered-page catalogue.
- Main video workspace is `#/video-studio`; legacy links are compatibility paths,
  not a second editor to promote or a reason to delete owner projects.
- Web Designer stays at `#/web_designer` and retains sandbox/source-preservation
  safeguards. Shared policies, budgets, task queue and finalizer are unchanged
  by cosmetic cleanup.
- Epoch names remain **Bossman Continuity** and **Bossman Steward**. Do not create
  a competing roadmap, kernel, ledger or memory database under a new name.
- CSS polish only bounds/wraps dock labels, enables overflow scrolling from the
  first button and keeps keyboard focus visible. No color grading, preview,
  status, policy or cloud-routing semantics are changed.

## Documentation cleanup

The root README now describes the coherent candidate instead of presenting the
old video-only session as the entire project. It marks Steward contracts as
started, not production-ready. The previous README is retained byte-for-byte as
`README_HISTORY_20260906.md` at the repository root, preserving its relative links.
The generated scorecard region is preserved, collapsed and explicitly historical;
no evidence or numeric scores were upgraded.

## Boundaries and open acceptance

There is no new hardware/browser acceptance or real same-model intelligence
measurement in this cosmetic pass. V4 and V5 are not certified complete.
Previously documented journal rollback-anchor, verify/open race, activation,
persistence, migration and long-horizon limitations must be rechecked on the
combined tree, not silently relabelled fixed by a merge.

Historical unit counts in source reports do not transfer to this candidate.
Run the [daytime plan](DAYTIME_ACCEPTANCE.md). Missing measurements remain
NOT_RUN / INSUFFICIENT_EVIDENCE. No paid provider call is authorized by this doc.

## Fable continues independently

Do not move, rewrite or delete Fable's active branch. New unpublished work cannot
be collected by reading GitHub. For each next published milestone:

1. Fetch primary, candidate and the specific Fable ref; record exact SHAs.
2. Compare since the last integrated Fable snapshot; inspect shared engine,
   finalizer, evidence, DOM and media changes before merging.
3. Open a PR into the candidate; preserve both independent fixes, without force.
4. Run the narrow combined tripwires, then the affected full suites.
5. Keep one exact candidate SHA during acceptance. Any further commit requires
   fresh exact-SHA evidence. Do not auto-merge to primary or change default branch.

Old experimental branches, owner ZIPs, credentials and runtime databases have
not been deleted. Dependency-update PRs are not part of this assembly.
