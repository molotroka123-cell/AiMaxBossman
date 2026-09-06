# Fable — implemented Continuity boundary patch

Read `docs/v4/FABLE_CONTINUITY_HARDENING_20260906.md` first. This is real code in
canonical components, not another source-pack runtime to copy blindly.

1. Fetch your current branch and this PR; review only the touched boundary files.
2. Preserve your later media/Fleet/recovery/context fixes. Reconcile overlaps;
   never replace your whole tree with an older archive.
3. Review anchor threat model, schema-4 reader requirement and default-OFF flag.
   Do NOT silently migrate/enroll old journals or enable production autonomy.
4. Run `V4 Continuity boundaries` on the actual integrated SHA, then full Core,
   Command Center and the existing media/Fleet/security gates. Old green SHAs,
   local fixture results and a merged PR are not current release evidence.
5. Attack full valid journal rollback, stale/changed approvals, disabled agent,
   visual drift and mandatory-context dedup before accepting the change.
6. Continue the canonical V4 plan. This closes selected implementation gaps,
   NOT all M0–M11 or Generation B/C. Keep V5 activation behind N0/V4 acceptance.

Minimum targeted run, from Core with shared/CC packages installed:

```bash
python -m pytest tests/test_v4_journal_anchor.py tests/test_v4_effect_boundary.py tests/test_v4_context_retention.py tests/test_v4_bcc_authorization.py tests/test_v4_anchor_bridges.py -q
```

Then from Command Center:

```bash
python -m pytest tests/test_v4_organization_anchor.py tests/test_feat_organization.py -q
```

Real model measurements are still required for Intelligence Preservation; these
new tests validate mechanisms, not model reasoning quality or any 3x speedup.
