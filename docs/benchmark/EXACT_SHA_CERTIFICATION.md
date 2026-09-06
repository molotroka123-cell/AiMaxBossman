# Exact-SHA CI certification

`docs/benchmark/current-scorecard.json` carries `exact_sha_ci`. Until now that
field was a hand-typed enum with no verification path: nothing in the repo could
contradict a `PASS`. `tools/exact_sha_certify.py` makes the claim falsifiable.

## What certifies a commit

A SHA is `CERTIFIED` only when every required workflow (the seven gates named
in `DEFAULT_REQUIRED`: root-ci, Bossman Core CI, Command Center CI, Bossman V2
Auto-Repair, ASTRA acceptance, Solana safety gates, and Fable media and Fleet
acceptance) has a **completed, successful** run whose `head_sha` is **exactly**
that SHA. Media and Fleet acceptance is required because it is the only gate
that installs real FFmpeg and actually executes the renderer, the Fleet TLS
RPC and the execution-truth regressions; a release SHA whose media path was
never executed is not certified. The
Intelligence Preservation gate is a separate SHA-bound axis and is not folded
into CI certification; add it with `--required` when a measured payload exists.

| Observation | Result |
|---|---|
| completed, conclusion `success`, same SHA | PASS |
| `skipped`, `cancelled`, `neutral`, `timed_out`, `stale`, `failure`, … | NOT_PASS (the conclusion is named) |
| run still `queued` / `in_progress` | NOT_FINAL, verdict NOT_CERTIFIED |
| no run of a required workflow on that SHA | MISSING |
| green run on any other commit | ignored and counted; it never transfers |
| no runs at all, fetch failure, malformed page | INSUFFICIENT_EVIDENCE (exit 2) |

Re-runs of the same workflow on the same SHA supersede earlier attempts. An
abbreviated SHA cannot be certified.

## How to run

```sh
# from a saved page of GET /repos/{owner}/{repo}/actions/runs?head_sha=<sha>
python tools/exact_sha_certify.py --sha <40hex> --runs-json runs.json --output report.json

# live, with a token that can read Actions
GITHUB_TOKEN=… python tools/exact_sha_certify.py --sha <40hex> --fetch --repo owner/name

# contradict a scorecard claim: exact_sha_ci=PASS must be for a certified SHA
python tools/exact_sha_certify.py --sha <40hex> --runs-json runs.json \
    --scorecard docs/benchmark/current-scorecard.json
```

The `Release certification (exact SHA)` workflow (`workflow_dispatch`) runs the
live check and uploads the report. Dispatch it after the other workflows have
finished; its job is red when the SHA is not certified, which is the point.

Exit codes: 0 CERTIFIED, 1 NOT_CERTIFIED or scorecard contradiction,
2 INSUFFICIENT_EVIDENCE. Tests: `tests/test_exact_sha_certify.py`.

## Scorecard coupling

`scripts/update_readme_scorecard.py` refuses `exact_sha_ci: PASS` unless
`docs/benchmark/exact-sha-certification.json` exists, is `CERTIFIED`, is
`final`, and certifies exactly `last_evidence_sha`. Commit the report written
by `--output` there when claiming PASS; `UNPROVEN`, `FAIL`, `NOT_RUN` and
`NOT_APPLICABLE` need no report. Root CI runs the scorecard check on every
commit, so a PASS that outlives its commit fails the build.

`OLD_SHA_PASS != CURRENT_SHA_PASS`. `SKIPPED/CANCELLED != PASS`.
