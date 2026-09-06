# Independent V5 verification — publication handoff

This directory publishes the previously completed audit, not a new run and not a freeze approval.

- Tested source SHA: `9cb1fb4665060d97839d1dd237f31e2b4b229911`.
- Historical result: 33 passed / 16 failed / 0 skipped across 49 executed cases.
- Six additional AT-01 cases: NOT_RUN (syntax checked only).
- Publication branch: `audit/astra-v5-independent-9cb1fb4-20260907`, based on the tested upstream commit, not the synthetic local audit history.
- Opus remains the sole integrator. No protected production source is changed.

## Start here

Read `CLAUDE_MINI_PROMPT_RU.md`, `findings.jsonl`, `PRODUCTION_CHAINS_AND_NOT_RUN.md` and the proposed acceptance matrix. `OWNER_REPORT_RU.md` and `OPUS_HANDOFF.md` are the ORIGINAL audit documents, preserved byte-for-byte. Their `PUSH_CONFIRMED=NO`, older refs, unavailable write-action statements and patch instructions describe the earlier audit session, not this later publication. Do not replace their historical tested SHA with the publication commit.

All 18 files from the original code overlay are published: three test files, the existing `tools/astra_acceptance.py` extension and fourteen report/proposal files. The two `proposals/*.patch` files remain data for sequential integrator review; neither is applied to production.

## Raw evidence

`evidence.tar.xz` contains all 118 original evidence files, including failed bootstrap attempts, final logs/JUnit, in-process import origins, command records, manifests, provenance refusal and real subprocess proof fixtures. No test or evidence file was rewritten to turn a failure green.

Archive SHA-256: `998544ac6fdc01358ece7e5c868e6bea73d905b71c7bdd2eafeace131c036d29`

Git blob SHA: `6742458a4f675c942c36250b8ec408c854306ac7`

Archive size: 12632 bytes. All entries are regular relative-path files under `evidence/`; no links, absolute paths or parent traversal. A local extract/read comparison matched all 118 original files before publication.

Extract into a NEW isolated evidence directory, not the owner's runtime. For example, from this directory:

```sh
mkdir ../astra-v5-evidence-unpacked
cd ../astra-v5-evidence-unpacked
python -m tarfile -e ../astra-v5-independent-9cb1fb4/evidence.tar.xz .
```

The extracted `evidence/` layout matches report-relative artifact paths. Absolute paths inside old manifests describe the old isolated audit environment; they are provenance, not paths to execute on the owner's machine. Source blob bindings are in the manifests and refer to the original upstream tested SHA. Source-snapshot copies and the redundant portable full overlay patch remain in the original downloadable ZIP; use this commit's Git diff and upstream SHA for repository integration instead.

Original downloadable bundle SHA-256 (not the evidence-only archive): `7f6e73bcd43f14e74919d149ebc232f86ff7129a667090b8800996be15541131`.

## Integration and scheduling status

Fetch current refs again before choosing a NEW fixed candidate. Reproduce on an isolated complete checkout; later upstream changes must be reviewed rather than mixed into historical results. Keep required failures visible, use the existing acceptance harness and preserve audit-intake ownership. A green helper/ledger suite does not prove actual queue service, model retention, mission recovery or desktop acceptance.

This publication does NOT install a background coding agent or a timer. No automatic 90-minute audit or reminder was scheduled by publishing these files. The connected Make workspace lacked a GitHub connection, and task scheduling was not confirmed. The Claude prompt delegates work only when the integrator actually starts it.

No main merge, force push, live-PC control, private-data access, real payment or paid-model invocation is part of this publication.
