# Full-suite triage — owner Windows run (62 failed, 7 errors, 193 skipped)

Source: `owner-repair/evidence/cc-full-suite-failures.txt` (Windows, Python venv, **ffmpeg NOT on PATH**,
Git-for-Windows `sh` on PATH). The Windows log itself (`cc-full-suite.log`) was not committed, so for the
Windows column only the FAILED/ERROR verdict is known; the mechanism was reconstructed from the test and
product source and, where possible, reproduced on Linux by substituting the Windows code path.

Method (measured, not assumed):
* every one of the 69 node ids was re-run on Linux with ffmpeg + Chromium, `BCC_REQUIRE_BROWSER=1`,
  on **current HEAD** (`BCC_DATA_DIR=/home/user/runs/triage-cur`) and on the **baseline worktree 0c3e22ff**
  (`/home/user/wt-baseline`, `PYTHONPATH` pinned; `bcc.__file__` verified as the baseline copy);
* a full current-HEAD suite ran in parallel (`/home/user/runs/cc-full-current.log`, summary below);
* classification: REGRESSION / PREEXISTING_SOFTWARE / HARNESS_CONFIG / UNSUPPORTED_PLATFORM /
  EXTERNAL_UNAVAILABLE / UNRESOLVED. UNRESOLVED = no Windows traceback and no mechanism found in source.
  Nothing is marked "expected pass" without evidence; "Windows after fix" is **unverified** everywhere —
  the owner must rerun on Windows (with `PATH=<app>\media;%PATH%`).

No test in the failure list changed between 0c3e22ff and HEAD (`git diff --stat 0c3e22ff HEAD -- command-center/tests`),
and every failing id behaves identically on baseline and current Linux → **no REGRESSION found in this list**.

## Table

Legend: W = Windows owner run; B = baseline 0c3e22ff Linux; C = current HEAD Linux (before my fixes);
"→" = result after the fix on Linux.

| # | test | W | B | C | class | root cause | action / new result |
|---|---|---|---|---|---|---|---|
| 1 | test_apps_control::test_a_restart_does_not_turn_our_own_app_into_a_foreign_process | FAIL | PASS | PASS | PREEXISTING_SOFTWARE (Windows-only) | `apps_control._process_namespace_verified()` compares `psutil.Process().exe()` with `sys.executable`. In a Windows venv (3.11+) `Scripts\python.exe` is `venvlauncher.exe`: the process is the BASE `python.exe`, `sys.executable` is the venv path → False → `_record_write` skips the record → after restart `recovered` is never set, the owner's app becomes "foreign" | FIXED `bcc/features/apps_control.py`: identity = `{sys.executable, sys._base_executable}` (resolved). Regression + negative control `test_apps_release_runtime::test_namespace_check_accepts_only_our_own_interpreter` PASS. Windows: unverified |
| 2 | test_apps_release_runtime::test_reused_or_tampered_process_record_never_grants_stop | FAIL | PASS | PASS | PREEXISTING_SOFTWARE (Windows-only) | first line `assert ctl._process_namespace_verified()` ("OWNER_REQUIRED … standard Linux/Windows host") — same venv-launcher false negative as #1 | same fix as #1 |
| 3 | test_apps_files_browser_owner::test_apps_files_owner_browser_restart_and_persistence | FAIL | PENDING_B | PASS | UNRESOLVED | passes on Linux; no Windows traceback. Starts the real File Commander Mini on fixed port 8911 through the product path; app code has Windows branches (`safety._parent` yields None off POSIX). Candidates: port 8911 held by the owner's installed product, or an app-side Windows failure not visible here | none; needs Windows traceback (rerun with `-x --tb=long`) |
| 4 | test_apps_files_http_owner::test_actual_apps_files_http_install_contract | FAIL | PENDING_B | PASS | UNRESOLVED | as #3 | as #3 |
| 5 | test_apps_files_lost_response::test_a_lost_response_after_a_real_move_does_not_move_anything_twice | FAIL | PENDING_B | PASS | UNRESOLVED | as #3 (same fixed port, same app) | as #3 |
| 6 | test_editors_user_acceptance::test_video_http_native_edit_preview_export_restart | FAIL | PASS | PASS | HARNESS_CONFIG | calls `ffmpeg`/`ffprobe` directly (lines 397, 483) — not on PATH in the owner run | rerun with ffmpeg on PATH |
| 7 | test_editors_user_acceptance::test_video_ui_import_trim_undo_preview_export_restart | FAIL | PASS | PASS | HARNESS_CONFIG | same (lines 541, 600) | same |
| 8 | test_feat_openrouter_agent_flow::test_env_configured_openrouter_models_drive_the_same_tool_loop | FAIL | PASS | PASS | UNRESOLVED | in-process fake adapter, `terminal_run "echo hi"`; nothing platform-specific found in the test; no traceback | needs Windows traceback |
| 9 | test_file_intelligence_contract::test_argv_is_a_list_so_a_filename_is_never_a_command | FAIL | PASS | PASS | HARNESS_CONFIG (test defect) | asserts the POSIX literal `"/tmp/; rm -rf …"` is in argv; on Windows `str(Path(...))` is `\tmp\; rm …` | FIXED test: compares `str(hostile)`; keeps the "one whole token" assertion. PASS on Linux |
| 10 | test_file_intelligence_contract::test_same_size_different_contents_with_restored_mtime_is_still_stale | FAIL | PASS | PASS | HARNESS_CONFIG (test defect) | corpus written with `write_text` → CRLF on Windows; the test's `"X"*len(text)` replacement is 8 bytes shorter on disk → its own `st_size ==` precondition fails | FIXED test: bytes in, bytes out. PASS on Linux |
| 11 | test_file_intelligence_hostile::test_shell_metacharacters_in_a_filename_are_just_characters | FAIL | PASS | PASS | UNSUPPORTED_PLATFORM (one name) | `a|b.txt` cannot exist on NTFS (`|` is illegal) → `write_text` raises | FIXED test: on win32 only the `|` name is dropped; the other five hostile names still run. PASS on Linux |
| 12 | test_file_intelligence_hostile::test_a_sidecar_that_never_answers_is_a_timeout_not_a_wait_forever | FAIL | PASS | PASS | HARNESS_CONFIG (test defect) | spawns `/bin/sh -c sleep 30` — no `/bin/sh` on Windows; the product runner itself is portable | FIXED test: uses `sys.executable -c "time.sleep(30)"`; same assertions. PASS on Linux |
| 13 | test_file_intelligence_hostile::test_the_process_is_not_left_running_after_a_timeout | FAIL | PASS | PASS | HARNESS_CONFIG (test defect) | `/bin/sh -c "sleep 1.5; echo alive > marker"` | FIXED test: interpreter script writes the marker; the kill-after-timeout property is now measured on Windows too. PASS on Linux |
| 14 | test_file_intelligence_recovery::test_a_stale_lock_from_a_dead_process_is_recoverable | FAIL | PASS | PASS | PREEXISTING_SOFTWARE (Windows-only) | `runtime_lock._process_is_running` uses `os.kill(pid, 0)`. On Windows that is `OpenProcess`+`TerminateProcess`: a non-existent pid raises plain `OSError` → read as "alive" (stale lock never recoverable), and a LIVE lock holder would be killed by the liveness check itself | FIXED `bcc/file_intelligence/runtime_lock.py`: on `nt` use `psutil.pid_exists`, POSIX path unchanged. New test `test_liveness_probe_on_windows_never_signals_and_sees_a_dead_pid_as_dead` (live pid kept, dead pid released, no signal sent) PASS |
| 15 | test_golden_missions::test_mission_02_real_file_edit | FAIL | PASS | PASS | HARNESS_CONFIG (Git sh) | command is `f"{sys.executable} - <<'PY'…"`; on Windows `terminal_control.host_shell()` picks Git `sh -lc` and the unquoted `C:\Users\…\python.exe` loses its backslashes → "command not found". (Without Git sh, cmd.exe has no heredoc → UNSUPPORTED) | FIXED test: interpreter path passed through `shlex.quote` (`_PY`); identical on Linux (PASS). Windows: unverified |
| 16 | test_golden_missions::test_mission_03_terminal_command | FAIL | PASS | PASS | HARNESS_CONFIG (Git sh) | same | same |
| 17 | test_golden_missions::test_mission_12_multi_step_mixed_mission | FAIL | PASS | PASS | HARNESS_CONFIG (Git sh) | same (fix + `pytest` commands) | same |
| 18 | test_media_roundtrip_studio::test_media_binaries_are_mandatory_not_optional | FAIL | PASS | PASS | HARNESS_CONFIG | by design: "no ffmpeg/ffprobe → fail with the binary name, never skip" | rerun with ffmpeg on PATH |
| 19–26 | test_media_roundtrip_studio:: video_fixture / image_fixture / video_studio_roundtrip / bossman_edit_visible / render_does_not_mutate / image_storage_roundtrip / image_studio_api / h264_named_png (8 FAILED) | FAIL | PASS | PASS | HARNESS_CONFIG | every test renders or probes through ffmpeg/ffprobe | same |
| 27–33 | test_media_roundtrip_studio:: legitimate_export / truncated_output / missing_audio / wrong_duration / wrong_frame_count / matroska_named_mp4 / negative_control_battery (7 ERROR) | ERROR | PASS | PASS | HARNESS_CONFIG | fixture setup runs ffmpeg → ERROR at setup | same |
| 34 | test_openrouter_provider_isolation::test_ui_binds_only_to_the_identity_the_server_named | FAIL | PASS | PASS | UNRESOLVED | `node --test` suite (skips only when node is absent, so node was present); stdout captured with `text=True` and no encoding; no traceback | needs Windows traceback |
| 35 | test_owner_acceptance_tool_path::test_a_model_that_only_says_done_does_not_pass_the_agent_path | FAIL | PASS | PASS | UNRESOLVED | local stub HTTP model, real `owner_acceptance.verify` (starts a real server, restarts it); passes on Linux; no traceback. "False completed" is NOT reproduced: on Linux the model that only says DONE is refused with `tool_not_called` | needs Windows traceback |
| 36 | test_release_ux_torture::test_safe_random_navigation_monkey_is_reproducible[99017] | FAIL | **FAIL** | **FAIL** | PREEXISTING (test race, see §Torture) | fails on Linux on both trees ("visible view is empty"): `navigate()` changes `location.hash` synchronously, the router replaces `#view` with a skeleton later; the settle check accepted the PREVIOUS page's text, then read the skeleton | FIXED test: settle waits until the pre-click view node is disconnected. See §Torture for the residual failure investigation |
| 37 | test_secrem_f009_terminal::test_ap001_owner_configured_roots_still_take_priority | FAIL | PASS | PASS | HARNESS_CONFIG (test defect) | compares `str(Path("/some/owner/…"))` with the POSIX literal; differs on Windows | FIXED test: `roots == [Path(configured)]`. PASS |
| 38 | test_secrem_f009_terminal::test_ap001_the_canary_is_readable_when_it_is_legitimately_in_scope | FAIL | PASS | PASS | HARNESS_CONFIG (Git sh) | asks `pwd` through the terminal; under Git sh the answer is the MSYS form `/c/Users/…`, which `Path(...).is_dir()` rejects | FIXED test: asks the interpreter for `os.getcwd()` instead. PASS on Linux; Windows unverified |
| 39 | test_smoke_live_owner::test_owner_starts_a_real_app_from_the_dashboard | FAIL | PENDING_B | PASS | UNRESOLVED | as #3 (real File Commander Mini on 8911 through the dashboard) | as #3 |
| 40 | test_studio_cloud::test_video_completion_downloads_decodes_and_keeps_key_off_cdn | FAIL | PASS | PASS | HARNESS_CONFIG | builds the fixture with `binary('ffmpeg')` (line 195) | rerun with ffmpeg |
| 41 | test_studio_gallery_ui::test_studio_reference_import_and_reframe_ui | FAIL | PASS | PASS | HARNESS_CONFIG | drives a reframe job in the UI; reframe runs `binary('ffmpeg')` | rerun with ffmpeg |
| 42 | test_studio_integrations::test_video_import_preserves_provenance_and_revision_gate | FAIL | PASS | PASS | HARNESS_CONFIG | `/runs/{id}/video` → `media.import_file` → `probe()` → `binary("ffprobe")` | rerun with ffmpeg |
| 43 | test_studio_integrations::test_reframe_real_binary_and_unknown_operation_refusal | FAIL | PASS | PASS | HARNESS_CONFIG | real ffmpeg reframe | same |
| 44 | test_studio_integrations::test_web_designer_uses_existing_edit_and_version_gate | FAIL | PASS | PASS | PREEXISTING_SOFTWARE (Windows-only, owner Studio→Web path) | `bcc/features/studio.py::web` read the verified descriptor with plain `os.read` AFTER `digest_descriptor`. On POSIX `os.pread` leaves the offset at 0; the Windows `pread` fallback seeks the shared pointer to EOF → `os.read` returns 0 bytes → sha mismatch → **409 "Output changed during read" for an unchanged file** (same family as the already-fixed reframe bug documented in `integrations.copy_verified`) | **Reproduced on Linux** by substituting the Windows `pread` byte-for-byte (409), FIXED: positional read loop from offset 0. New test `test_web_transfer_survives_the_windows_positional_read_fallback` (200 + negative control: changed bytes still 409). File: 13/13 PASS |
| 45 | test_studio_integrations::test_generated_bytes_to_timeline_export_and_reopen | FAIL | PASS | PASS | HARNESS_CONFIG | ffmpeg fixture + render | rerun with ffmpeg |
| 46 | test_studio_integrations::test_declared_media_surface_matches_container_and_audio_mime | FAIL | PASS | PASS | HARNESS_CONFIG | `binary('ffmpeg')` lines 139/146 | same |
| 47 | test_studio_integrations::test_reframe_survives_the_windows_positional_read_fallback | FAIL | PASS | PASS | HARNESS_CONFIG | real ffmpeg reframe (the Windows-fallback substitution itself passes on Linux) | same |
| 48 | test_testing_period::test_publish_commits_to_the_current_branch_without_force | FAIL | PASS | PASS | UNRESOLVED | real `git` in tmp repo; candidate: `subprocess.run(text=True)` without `encoding` decodes git's UTF-8 with the Windows ANSI code page, so the Cyrillic commit subject "журнал тестового периода" would not match in `git log`; unproven | needs Windows traceback |
| 49 | test_ux2_desktop::test_real_chromium_app_window_renders_command_center | FAIL | PASS | PASS | UNRESOLVED | real `--app` Chromium window, `--headless=new`, DevToolsActivePort in a temp profile; browser/profile behaviour on Windows not measurable here | needs Windows traceback |
| 50 | test_ux2_desktop::test_second_window_refused_while_first_instance_alive | FAIL | PASS | PASS | HARNESS_CONFIG (test defect) | lock fixture uses `pid: 1` as the "alive first instance"; Windows pids are multiples of 4, pid 1 never exists → `desktop._pid_alive` (correct Windows branch) says stale → launcher runs | FIXED test: `pid: os.getpid()`. PASS |
| 51 | test_v21_e2e_mission::test_autonomous_mission_with_ten_plus_tool_calls | FAIL | PASS | PASS | UNRESOLVED | deterministic HTTP model + real terminal (`python - <<'PY'` — plain `python`, works under Git sh) + Chromium + MCP; too many candidates, no traceback | needs Windows traceback |
| 52 | test_v21_tools_terminal_browser::test_model_runs_real_command_and_reads_output | FAIL | PASS | PASS | HARNESS_CONFIG (test defect, "Git sh" in CONTINUATION) | test picks `type hello.txt` when `os.name == "nt"`, but the product's `host_shell()` picks Git `sh -lc` when `sh` is on PATH, where `type` is the shell builtin | FIXED test: chooses `cat`/`type` from `host_shell()` (cmd.exe → `type`). PASS on Linux; Windows unverified |
| 53 | test_video_descriptor_boundary::test_repeated_requests_do_not_leak_descriptors | FAIL | PASS | PASS | HARNESS_CONFIG (test defect) | `descriptors()` falls back to `psutil.Process().num_fds()` — UNIX-only (AttributeError on Windows) | FIXED test: `num_handles()` on `nt`. PASS |
| 54 | test_video_descriptor_boundary::test_failed_verification_and_abandoned_handles_leak_nothing | FAIL | PASS | PASS | HARNESS_CONFIG | same | same |
| 55 | test_video_descriptor_boundary::test_body_aborted_by_the_client_still_closes_the_descriptor | FAIL | PASS | PASS | HARNESS_CONFIG | same | same |
| 56–61 | test_video_studio_container_choice:: 6 tests | FAIL | PASS | PASS | UNRESOLVED | pure `node` driver importing the product ES module (`MODULE.as_uri()`); skips when node is absent, so node was present; no ffmpeg involved; no traceback | needs Windows traceback |
| 62 | test_video_studio_owner_recovery::test_preflight_recovery_uses_original_draft_after_actual_media_upload | FAIL | PASS | PASS | HARNESS_CONFIG | fixture via `ffmpeg` (line 57) | rerun with ffmpeg |
| 63–67 | test_video_studio_playback_stall:: 5 tests | FAIL | PASS | PASS | HARNESS_CONFIG | every test calls `fixture_clip()` → `ffmpeg` (line 117) | rerun with ffmpeg |
| 68 | test_windows_desktop_onedrive::test_known_folder_probe_is_inert_off_windows | FAIL | PASS | PASS | UNSUPPORTED_PLATFORM (harness defect: no skip marker) | by its own docstring it checks the probes OFF Windows ("должны быть безопасны на Linux") — on Windows they legitimately answer | FIXED test: `skipif(sys.platform == "win32")` |
| 69 | test_windows_secret_acl::test_posix_token_creation_spawns_nothing | FAIL | PASS | PASS | UNSUPPORTED_PLATFORM (harness defect: no skip marker) | docstring "POSIX не меняется"; on Windows `icacls` IS spawned by design | FIXED test: `skipif(sys.platform == "win32")` |

Counts: HARNESS_CONFIG 45 (ffmpeg missing 32, Git-sh/test-defect 13) · PREEXISTING_SOFTWARE 4 (3 root causes, all fixed) ·
UNSUPPORTED_PLATFORM 3 · UNRESOLVED 12 (#3,4,5,8,34,35,39,48,49,51,56–61 = 6 ids) · PREEXISTING test race 1 · REGRESSION 0.

## Full run on the candidate line (Linux, ffmpeg + Chromium, HEAD before the last fixes)

`8 failed, 3941 passed, 37 skipped` in 37 min (`/home/user/runs/cc-full-current.log`). The run overlapped
concurrent edits and a parallel triage run; each failure was re-run alone afterwards:

| test | class | outcome |
|---|---|---|
| test_browser_download_b4::test_b4_download_matrix_real_chromium | PREEXISTING_SOFTWARE (browser-mode dependent) | the container's full Chromium (141, new headless) renders a direct PDF inline: no download event, `goto` 200, no file. Fixed in `bcc/v2/browser_control.py`: a navigation whose response is a document type (PDF/archive/binary) and produced no download is fetched through the session request context and saved through the same policy/limit/.part/sha256 path. PASS after fix; the Windows bundle (headless shell) path is unchanged |
| test_double_submit_real_buttons | load flake | PASS alone |
| test_installed_product_paths::test_the_wheel_carries_the_interface | HARNESS_CONFIG | venv without `pip` (the CI runner has it) |
| test_release_ux_torture ×3 | PREEXISTING test oracle race (see §Torture) | fixed (router marker) |
| test_single_flight::test_no_module_went_back_to_asyncio_shield | REGRESSION (media worker) | `asyncio.shield` in the new sdcpp.py — replaced by `await_shared` (fcaf2ad) |
| test_smoke_live_owner | HARNESS (port 8911 held by an orphan File Commander from a parallel run) | PASS alone after the orphan was removed |

## §Torture

Residual failure (≈1 in 3 runs on Linux, both baseline and HEAD): after `go_forward` the monkey captured the
transient skeleton as the "previous node", so the settle check accepted the previous page's text and then read
the router's skeleton. The product behaviour was correct; the test had no signal that the rendered content
belongs to the target page. Fix: `ui/app.js` now sets `#view[data-page]` to the rendered page id (removed while
the skeleton is up) and `_assert_page_settled` requires it; the back/forward branch settles after `go_forward`.

## §Skips

Windows owner run: 193 skipped, without ffmpeg and with the owner's browser profile. Linux candidate run with
ffmpeg + Chromium present: 37 skipped. The difference (156) is the media/browser-conditional skips that become
executed tests when the binaries are present; on the owner's machine they execute with `PATH=<app>\media;%PATH%`.
The remaining Linux skips seen in verification runs are platform-conditional by contract (NTFS junctions need
Windows; docker daemon absent; PostgreSQL DSN absent). The mandatory owner paths were measured executing, not
skipped, on Linux: B4 download (real Chromium), AP-ALL approvals filter, TEL-001 model speed, Computer Use
decision plane + owner STOP/Resume UI, sd.cpp provider contract (MOCK_ENGINE), Video Studio roundtrip and
playback, golden missions, owner acceptance tool path. A per-reason table of all 37 skips is produced by the
final `-rs` run recorded in CONTINUATION.md.
