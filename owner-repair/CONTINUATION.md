# CONTINUATION — Bossman 1.0-RC (checkpoint 2026-09-21, GitHub-only closure)

Status: **BOSSMAN_1.0_RC candidate declared; READY_FOR_OWNER_RUN pending the exact-SHA certificate of the
final push** (see the handoff comment on PR #71 for CANDIDATE_SHA, CI matrix and the inner ZIP SHA-256).
OWNER_HARDWARE_CERTIFIED: **not claimed** — the owner runs tomorrow's protocol on the new archive.

## Lines and commits
| what | value |
|---|---|
| FINAL_BRANCH | `claude/bossman-1-0-rc-owner-ready-cfesui` (harness-designated; PR #71 → `release/bossman-owner`) |
| START_SHA | `9c3369b400cbea08e794017a652bf7567ad4f70d` (remote HEAD of release/bossman-owner at start; nothing rolled back) |
| previous owner baseline | `0c3e22ffd44c9b2c3e4f90b2e86456c28ca43f39` |
| CANDIDATE_SHA | the tip of FINAL_BRANCH after the push carrying `tools/release_candidate.json` = `rc-2026-09-21-bossman-1.0-rc-owner-ready` |

Commits on top of START_SHA (all pushed, no force-push):
b8134d5 computer use · deb9470 learning loop · fe56874 release tooling · 508f2be CI contract · 4e74a23 triage fixes ·
c16ba1b sd.cpp hardening · fcaf2ad shield/skips · 4c2197c red-team fixes + B4 inline documents · (this) RC declaration.

## What was done today (measured in this container, Linux, ffmpeg + Chromium)
- Computer Use: CU-VERIFY/APPROVAL/STOP/TARGET/PATH fixed, 27 regressions + real-Chromium STOP/Resume UI test.
- sd.cpp media: real sha256 verification (expected/observed), safe cancel (no orphan, bounded pump), durable
  sidecars + orphan reconciliation on restart, Pillow-decoded inputs, atomic verified output, backend observed
  from the engine log, A/B preset planner; config in `<data_dir>/media/config.json`; 68 hostile MOCK_ENGINE tests.
- Learning: LessonBook over the canonical LearningStore; poison filter with Unicode/base64 normalisation;
  coaching runner (5 train + 5 holdout). COACHING_PIPELINE_TESTED · LOCAL_LEARNING_GAIN_NOT_MEASURED · WEIGHTS_UNCHANGED.
- Full-suite triage: 69 Windows failures classified (0 regressions; 3 Windows-only product defects fixed;
  45 harness/ffmpeg; 12 UNRESOLVED need the Windows traceback). `owner-repair/full-suite-triage.md`.
- Red team (separate context): 65 cases, 4 OPEN defects → fixed; `owner-repair/redteam-rc-20260921.md`.
- Release tooling: Owner-Run.cmd, Media-Setup.cmd, Coaching.cmd, Collect-Diagnostics.cmd; doctor probes for
  llama-server MAIN/FAST and the media engine; START_TOMORROW_RU.md; docs/owner/ROLLBACK_RU.md.

## Known open / honest limits
- Real sd.cpp generation, I2V, noise-cause isolation: NOT RUN (no engine/weights here) — A/B plan shipped.
- Learning gain on the local model: NOT MEASURED (mock backend only).
- 12 UNRESOLVED Windows-only failures: need `pytest -x --tb=long` on the owner machine with ffmpeg on PATH.
- Harness-only Linux failures in this container: `test_packaging_installed`/`test_installed_product_paths`
  (venv without pip), `test_benchmark_truth` (shallow clone lacks commit 8a13f1d), `test_fable_manifest_mac_pinning_opt_in`
  (timed out only under 4 parallel suites; passes alone in ~100 s).
- Startup animation: not implemented (owner wish, lowest priority). Shortcut: existing path, tests green.

## Next command (if resumed)
```
git fetch origin claude/bossman-1-0-rc-owner-ready-cfesui && git checkout claude/bossman-1-0-rc-owner-ready-cfesui
python tools/exact_sha_certify.py --sha $(git rev-parse HEAD) --fetch --repo molotroka123-cell/AiMaxBossman
```
If NOT_CERTIFIED: read the failing job log, fix, push a small commit (a new push = a new candidate SHA), re-certify.
If CERTIFIED: download `bossman-windows-<SHA>` from the bundle run, record the inner ZIP SHA-256 in the PR
handoff, then the owner runs `START_TOMORROW_RU.md`.
