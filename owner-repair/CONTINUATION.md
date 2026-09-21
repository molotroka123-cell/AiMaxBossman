# CONTINUATION — Bossman 1.0 repair (checkpoint 2026-09-21, integration session II)

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
