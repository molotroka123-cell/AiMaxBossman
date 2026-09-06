# Audit — unified development-quality database

One offline catalogue for cross-branch audit reports, PR discussions, review
comments and CI artifacts. NOT a second Bossman runtime database, policy,
learning store or permission source. Report text is not executed as instructions.

`AUDIT_REGISTRY.json` is the reviewed versioned seed ledger in Git.
`tools/audit_intake.py` builds/updates `Audit.sqlite` and portable `Audit.json`.
Tables: sources, sightings, finding_versions, scans. Existing databases update
idempotently; previous finding versions remain. root_cause_key groups duplicate
causes without deleting per-source claims or hiding conflicting reports.

Containing commit != audited SHA. CI head_sha != actual PR merge checkout.
Ingested sources default to UNREVIEWED. Index completeness does not mean every
report was read or every bug fixed. No automatic closure follows a green
workflow, report prose, an old scorecard or a skipped test.

Raw logs, prompts and comment bodies are not copied into the database. Retain
locators, content hashes and whitelisted metadata; review originals through their
permissions. Binary archives are indexed, not executed. Symlinks are not followed.

## Collection

```
python tools/audit_intake.py --repo . --output ../bossman-audit --seed docs/audits/AUDIT_REGISTRY.json --since 2026-09-06T00:00:00Z --github
```

Use an existing read-only token in the local environment for API rate limits;
never paste or commit credentials. The repository API is fixed to AiMaxBossman;
redirects cannot forward credentials. No model API is called.

All fetched remote tips and intermediate commits in the declared window are
indexed, so an audit added/deleted before the tip remains discoverable. Every
commit's changed-path metadata is retained even when report naming is unusual.
PR bodies/comments/submitted and inline reviews, runs and artifact metadata are
paginated. API/page/commit caps, changing pagination, unavailable objects and
network errors appear as scan gaps. Exit 2 = PARTIAL, never complete.
Unpublished reports and deleted GitHub comments cannot be recovered; no zero-miss
or full semantic-review guarantee is claimed.

The hosted job produces one database snapshot per run. Repeated local runs into
the same output directory accumulate history. Fresh hosted snapshots reconstruct
reachable Git/API data. The Git seed is durable; artifacts expire after 30 days.
Retain important reviewed records in Git and back up Audit.json/Audit.sqlite.

## Automation and isolation

Push on the new audit branch triggers initial collection. Scheduled/workflow_run
triggers become operational only after merging the workflow onto the default
branch; until then they are prepared, not continuous monitoring. This is not a
promise that an interactive ChatGPT session runs in the background indefinitely.

The collector executes only its checked-out code/tests, never code from scanned
branches. It never writes remote refs, merges PRs, modifies the running UI or
activates N0. The full UI run stays pinned to its existing SHA.

## Reviewed seed

13 records: SQLite/admission fixes; five Windows findings; remaining repo_map
byte-hash concern; UI/recovery evidence gaps; unintegrated-V4 tracking. These are
not 13 independent production bugs: three Windows setup failures share missing
symlink privileges.

New static finding at 776bc42: a symlink-availability decorator skips the whole
mixed fingerprint test, including normal file-state cases. Split those from the
symlink-only cases rather than hide coverage. Reported in PR #25.

Collector regressions: 18 passed locally, exit 0. The initial pagination test
fixture confused per_page=100 with page=1; fixed and rerun. Production gates and
application behavior are unchanged. Remote full collection is a separate run.
