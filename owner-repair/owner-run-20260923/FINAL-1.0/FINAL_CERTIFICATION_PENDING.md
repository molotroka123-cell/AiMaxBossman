# Bossman 1.0 — exact-SHA certification checkpoint

Recorded: 2026-09-24 (Europe/Prague)

## Candidate

- FINAL_CANDIDATE_SHA: `5a2b6f1339392b7e2af52af20d30c6b92912f3c5`
- Certification staging: `claude/bossman-1-0-cert-20260924`
- Clean exact-SHA ref: `claude/bossman-1-0-exact-20260924`
- Product fix source: `fix/owner-run-20260923-p1 @ 16a5b40229c9b83a36ef30db9337ef27d632d581`
- Release baseline included: `release/bossman-owner @ 69f09d1715a9df7b7e01d224f2e33cf72d9f8744`
- `main` unchanged.

The candidate is a descendant of both the owner/fix line and the current release line. It contains the owner-run P0/P1 fixes, controlled apply, Terminal/Jev/Studio changes, review hardening, current 1.5/Money-MVP documentation and release-gate changes.

## CI repair made during certification

The first fresh root run exposed only repository hygiene in
`bossman-core/bossman/apprentice/local_sidecar.py`: CRLF/trailing whitespace caused
`git diff --check` to fail after tests, compileall and the secret scan had passed.
The file was normalized without behavior changes in `16a5b402`.

## Windows-100

The historical workflow that printed “OK” 100 times is NOT accepted as evidence.

The candidate adds:
- `tools/windows_100_real.py`
- `.github/workflows/windows-stress-100.yml`

The new gate collects real pytest node IDs from five product areas
(Terminal/Telegram, Studio/media, security/contracts, coding, memory/skills),
requires the declared quota in every area, selects exactly 100 unique cases and
executes those exact 100 cases on `windows-latest`. Selection and stdout/stderr
are uploaded as artifacts.

`Windows 100 real checks` was also added to
`tools/exact_sha_certify.DEFAULT_REQUIRED` and to the owner-facing workflow
contract. Therefore 1.0 cannot be exact-SHA certified without a green real
Windows-100 run.

## Current gate state

The release-candidate marker was updated in candidate commit `5a2b6f13`.
Required workflows were raised for that exact SHA, including:
- root-ci
- Bossman Core CI
- Command Center CI
- Bossman V2 Auto-Repair
- ASTRA acceptance
- Solana safety gates
- Fable media and Fleet acceptance
- One-download Windows application
- Windows owner run
- Owner scenarios
- Windows 100 real checks

At this checkpoint GitHub Actions has a large repository queue (dozens of queued
runs, with older long Command Center matrices still occupying hosted runners).
The candidate gates are QUEUED/PENDING, not PASS and not FAIL.

## Merge rule

Do NOT call 1.0 RELEASE_CERTIFIED while any required exact-SHA workflow is
queued, pending, cancelled, skipped or failed.

When every required gate is completed/success on
`5a2b6f1339392b7e2af52af20d30c6b92912f3c5`:

1. run/record exact-SHA certification;
2. record the Windows bundle artifact SHA-256;
3. verify `release/bossman-owner` has not moved unexpectedly;
4. fast-forward/merge this exact candidate into `release/bossman-owner`;
5. verify release tree == candidate tree;
6. leave `main` unchanged;
7. write final 1.0 release report.

Until then the truthful status is: **FINAL_CANDIDATE_PUSHED / CERTIFICATION_PENDING**.
