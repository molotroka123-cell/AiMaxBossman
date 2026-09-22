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
c16ba1b sd.cpp hardening · fcaf2ad shield/skips · 4c2197c red-team fixes + B4 inline documents · 63e66c3/a9d8d32 RC
declarations 3–4 · merge of canonical `release/bossman-owner` @ 0c1cbe6 (11 commits by the other integrator, ported by
meaning — see the ledger entry of 2026-09-22) · RC declaration 5.

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

---

## Preserved: canonical-line checkpoint by the other integrator (release/bossman-owner @ 0c1cbe6)
Merged into this line on 2026-09-22 (ledger entry). Of its "Open items": 1 (MEDIA-RESTART) and 2 (STOP/Resume
button) are closed on this line; 3, 4 and 5 are addressed by the coaching runner, `media_ab_preset.py` and the
candidate-5 declaration. Their text follows unchanged for the record.

Status: **engineering checkpoint — RC track, NOT 1.0, NOT owner-hardware-certified.**
Honest ceiling today: READY_FOR_OWNER_RUN once the exact-SHA matrix is green on a
frozen SHA and a clean Windows artifact is accepted on CI (see "Remaining").

## Fixes pushed this session (release/bossman-owner, on top of 0c3e22ff → 9c3369b4)
| SHA | What | State |
|---|---|---|
| cb7aefe7 | CI obligatory root lane: studio catalog count 6→8 (sd.cpp models), higgsfield selected by identity, price invariant kept with teeth; regenerated SKIPS_REGISTRY.md | FIXED, verified: catalog 18 passed, skips --check PASS, hygiene gates PASS |
| 59daf7c3 | Computer Use CU-VERIFY (unknown/mistyped expect → not verified) + CU-APPROVAL (owner ASK from declared semantic OR named target/text; approval no longer derived from a model field; `approved` is a trusted param) | FIXED, 9 new regressions + 57 existing computer tests PASS |
| def1a714 | F-17 UX-settle oracle race: #view[data-rendered]=<page id> end-of-render signal; oracle waits for it | FIXED, probe: new oracle 0/36 bad vs old race 33/36; torture suite green x2 |
| 26ef7f76 | sd.cpp MEDIA-HASH (real byte verification, cached; expected+observed provenance) + MEDIA-CANCEL (no spawn after cancel, kill process tree, bounded read) | FIXED, 2 new regressions PASS |

Every commit: small, single-purpose, no product weakening, negative controls kept,
whitespace/secret/scorecard gates green locally. No force-push, no history rewrite,
no new final branch.

## Full-suite triage (mandate Priority #1) — DONE
The local "62 failed, 3704 passed, 193 skipped, 7 errors" run was made WITHOUT
ffmpeg on PATH. Re-running the exact 69 failing/erroring node ids in an
engineering env WITH ffmpeg 6.1 + Chromium present: **67 passed, 2 failed.**
- The 62 fails + 7 errors were overwhelmingly HARNESS_CONFIG (no ffmpeg) — media
  roundtrip, video studio, studio integrations, apps files all pass with ffmpeg.
- Residual 1: `test_release_ux_torture ... monkey[99017]` = F-17 — now FIXED above.
- Residual 2: `test_v21_e2e_mission` returned 503 "MCP SDK не установлен" — with the
  declared `mcp` extra installed it PASSES. Missing declared extra, not a defect.
**Classification: zero real product REGRESSIONS.** All failures were
HARNESS_CONFIG (missing ffmpeg / missing declared `mcp` extra) or the F-17 test
oracle (fixed). Install must ship the `mcp` extra and put ffmpeg on PATH — both
already declared (`command-center[...,mcp,...]`, `<app>\media`).

## CI lane status (measured, not assumed)
- **Obligatory lanes green after these fixes:** root pytest + hygiene (catalog+skips),
  browser-user-paths (skips), Command Center CI (F-17 oracle now deterministic).
- **Non-mandatory lanes still red BY OWNER POLICY (not defects):**
  `measured intelligence retention` (needs docs/benchmark/intelligence-preservation-current.json,
  produced only on owner hardware) and `Human-speed components` on windows
  (Windows 15.625 ms timer quantization → degenerate_measurement; the gate honestly
  refuses to fake it). Both are OUTSIDE DEFAULT_REQUIRED — they do not block
  exact-SHA certification. This is the standing owner decision, unchanged.

## Open items, in order
1. **MEDIA-RESTART** (sd.cpp `_jobs` is in-memory): after a provider restart the
   job vanishes. Wire it to the durable studio job store, or return explicit
   `interrupted/needs-reconciliation`. NOT done this session; feature is gated
   (BOSSMAN_SDCPP_BIN + 25 GB models, owner hardware only).
2. **Computer Use §5 remainder:** STOP/Resume are HTTP endpoints
   (/api/computer/stop|resume) already; add the owner-visible STOP/Resume BUTTON
   to the UI (not model-callable). CU-STOP queue-of-two / STOP-after-restart and
   CU-TARGET freshness are unverified on real hardware.
3. **Coaching §7:** pipeline exists; local-gain not measured this session (needs the
   owner local model). Status: COACHING_PIPELINE_PRESENT, LOCAL_LEARNING_GAIN_NOT_MEASURED,
   WEIGHTS_UNCHANGED. EVO/self-modification NOT started.
4. **Media preset A/B (§6):** 832×480/20 steps = correct clip; low profile = noise,
   cause not isolated. Prepare A/B with one variable on owner hardware tomorrow.
5. **Freeze:** once the branch stops moving, declare one candidate via
   tools/release_candidate.json, run the FULL exact-SHA matrix on that exact SHA,
   build the Windows artifact, clean-install + rerun + independent red team, then a
   1.0-RC prerelease. Note: another session is co-pushing (docs/evo); coordinate the
   freeze so the candidate SHA is not superseded before its matrix completes (this
   is the exact F-22 "moving candidate" hazard — the matrix takes ~40 min, the head
   has moved every few minutes today).

## Environment facts for the next engineer
- Engineering env here has ffmpeg 6.1, ffprobe, Chromium (/opt/pw-browsers), py3.11+3.12.
- Editable installs used: root `-e .`, `-e ./command-center`, `-e ./bossman-core`,
  plus `redis`, `psutil`, `mcp`. The `mcp` install needs `--ignore-installed PyJWT`
  in this Debian image.
- Local `C:\Users\asd\...` paths in the earlier ledger belong to the owner's prior
  session, not reachable here. All work this session was GitHub-only.
