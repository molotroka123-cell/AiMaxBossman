# CONTINUATION — Bossman 1.0 repair (checkpoint 2026-09-21T21:00Z)

Status: **engineering checkpoint, NOT 1.0, NOT certified.** Paused on the owner's request.

## Branch / commits (release/bossman-owner, pushed on top of 0c3e22ff)
| SHA | What | State |
|---|---|---|
| 79a12276 | B4 browser download (P1) | FIXED, regression PASS (real Chromium) |
| 84cbd788 | AP-ALL approvals filter (P2) | FIXED, regression PASS |
| 8fe29925 | TEL-001 honest model speed (P2) | FIXED, live check PASS (MAIN 10.2, FAST 50.1 tok/s) |
| 9cf8fe4c | Computer Use wired into owner product | FIXED, live Notepad 12/12 PASS |
| 6384c8f0 | Local AI video/image engine (sd.cpp Vulkan) | WIP (see below) |

Exact-SHA CI for the pushed head: **not yet checked**. Windows artifact: **not built**.

## Open items, in order
1. **Full command-center suite triage.** Run log: `owner-repair/evidence/cc-full-suite.log`
   (run WITHOUT ffmpeg on PATH — rerun with `PATH=<app>\media;%PATH%`). About 15–20 failures
   seen. For each failure, compare with a baseline worktree at 0c3e22ff before calling it a regression.
   Known pre-existing/host: `test_v21_tools_terminal_browser::test_model_runs_real_command_and_reads_output`
   (Git sh), `bossman-core test_windows_host_shell::test_posix_local_shell_unchanged`,
   `test_studio_integrations::test_web_designer_uses_existing_edit_and_version_gate` (to confirm on base).
2. **Media engine finish:**
   - Write `C:\Users\asd\Bossman\models\media\MANIFEST.json` (sha256 + bytes for all 6 files;
     `download.sh` there has pinned revisions). Engine: sd.cpp `master-890-74988b2`,
     vulkan zip sha256 `744c8f81…c5d896`, at `C:\Users\asd\Bossman\media-runtime\sdcpp\vulkan\sd-cli.exe`.
   - Isolate the noise cause: `--vae-tiling` versus low resolution/steps. Variant A (832x480, 20 steps,
     no fa, no tiling) = correct clip, `C:\Users\asd\cu-work\gen\varA.webm` sha256 `33a95853…223610`.
     Variants smoke/B = noise.
   - Real product-path runs through `/api/studio/jobs`: T2V ≥3 s, I2V from a Z-Image result,
     cancel, error; ffprobe + full decode + frame check; write `owner-repair/video-generation-proof.md`.
   - Test Z-Image-Turbo (files downloaded). ROCm torch works (ComfyUI venv at
     `C:\Users\asd\Bossman\media-runtime`), so it is an option for the existing ComfyUI image adapter.
   - Owner install configuration: set BOSSMAN_SDCPP_BIN / BOSSMAN_MEDIA_MODELS (app-support env or docs).
3. Computer Use: add a STOP/Resume button to the UI; check the agent-driven path with a local model.
4. Coaching loop (broken counter, owner-test-pack 02_coding_app), ≥5 tasks, holdout, restart reuse.
5. Owner scenarios 1–10 through the installed UI (Playwright driver like `C:\Users\asd\Bossman\ui-*.py`), MVČR
   up to the legal boundary.
6. Exact-SHA CI → Windows artifact → clean install → rerun → independent red team → 1.0 tag.
7. Owner wishes: desktop shortcut and startup animation (at the very end).

## Running processes / environment
- llama-server MAIN :8081 (qwen3.8-27b) and FAST :8082 (qwen3.6-35b-a3b) started by the engineer; leave or stop.
- Lease file: `C:\Users\asd\Bossman\handoff\LEASE.json` (engineer holds desktop/backend/GPU).
- Dev venv: `wt-release\.venv` (command-center[dev,browser], bossman-core[dev,windows]).
  Browser tests: `PLAYWRIGHT_BROWSERS_PATH=<app>\browser`, ffmpeg in `<app>\media`.
- Test work folders: `C:\Users\asd\cu-work` (Computer Use + generation scratch). No user data touched;
  one stray test file in Documents was created and removed (ledger).

## Known limitation to document
- Agent cannot type text containing "bossman"/"approve"/"confirm action"/"emergency" (core anti-self-approval
  policy; kept on purpose).
- open-higgsfield upstream has no license → not vendored (REFERENCE_ONLY). "Higgsfield" in Bossman 1.0 =
  Studio product path with a local engine; cloud Higgsfield = NOT_RUN.
