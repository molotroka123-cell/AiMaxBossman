# BUG DOSSIERS — kimi/final-residual-closure-20260906 @ 779bb44
Host: Windows 11 Home 10.0.26200, i9-14900HX, 16 GB RAM, RTX 4060 Laptop,
Python 3.14.3, FFmpeg 8.1-full_build-www.gyan.dev, Ollama :11435
(qwen2.5:7b, qwen2.5-coder:14b, llama3.2). Venv: accept-venv
--system-site-packages + kimi-shim/sitecustomize.py (ALL product imports forced
inside the worktree; stale editable bossman_core finder dropped).
Tier policy: LIVE / IN_PROCESS / MOCK / NOT_RUN. MOCK never promoted.

---

## BUG-001 [OPEN] CFR range [2-4] rejected — zero-tolerance duration check
- Status: OPEN, REAL_CODE_BUG (backend proof; UI repro still required before closure)
- Repro (exact, ~2 s):
  `PYTHONPATH=<shim> python -m pytest "command-center/tests/test_video_studio_cfr_frames.py::test_rational_range_keeps_selected_final_source_frame" -q -p no:cacheprovider`
  from `<wt>/command-center`. Fails ONLY param `[2-4]`; `[3-7]` etc. pass.
- Error: `bcc.video_studio.model.StudioError: Clip extends beyond source duration`
  raised at `command-center/bcc/video_studio/model.py:236`
  (`if c["source_out"] > media["duration_ticks"]` in `validate_project`).
- Root cause: fixture builds 4 frames @ 30000/1001 and sets
  `clip.source_out = frame_ticks(4) = 133467`
  (`command-center/tests/test_video_studio_cfr_frames.py:42,51`), but real
  FFmpeg 8.1 MP4 probes as `time_base=1/30000, duration_ts=3990 → 0.133000s →
  duration_ticks=133000` (`command-center/bcc/video_studio/media.py:135-143`
  via `float(format.duration)`): 467 ticks short of ideal 4004/30000.
  Container duration quantization, not a bad clip. `[3-7]` probes exact
  (100100/233567) so only `[2-4]` trips the strict `>` check.
- Environment excluded: repo expects exactly `8.1-full_build-www.gyan.dev`
  (`docs/video-studio/BACKEND.md:7`); host has exactly that.
- Fix direction (code, NOT test): bounded one-frame container slop in
  `validate_project`, e.g.
  `slop = max(2000, math.ceil(2 * TICKS / float(rate(seq["fps"]))))`
  `if c["source_out"] > media["duration_ticks"] + slop and media["duration_ticks"] > 0: raise ...`
  Hostile retest MUST prove genuinely out-of-range clips are still rejected
  (slop bounded to ~2 frames; negative/large overshoots still raise).
- UI repro required (per REAL_USER_UI_E2E_ACCEPTANCE.md bug rule): import
  fixture → trim to the failing range → export via visible button → must
  succeed after fix; then same flow green + post-state (decodable artifact).
- Evidence: junit `video.xml` (65 passed, 1 failed [2-4], 1 skipped) on SHA
  6fcc406; failure path is fixture→clip.add→apply_command→validate_project,
  i.e. BEFORE any render-range code.

## BUG-002 [CLOSED/ARTIFACT] M1 restart trio — cross-tree import contamination
- Status: CLOSED as environment artifact. NOT a product bug. No code changed.
- Symptoms (STALE, contaminated env): 3 failed in
  `bossman-core/tests/test_v3_command_center_adapters.py`
  (`test_restart_between_steps_resumes_from_the_unfinished_one`,
  `test_real_chain_completes_after_owner_approval_with_real_files_and_receipts`,
  `test_tool_ran_but_expected_effect_absent_is_not_completed`):
  `approve_all_pending() == 0`, `a.txt` never created.
- Root cause (env, proven): machine python has editable `bossman_core`
  install pointing at the owner's MAIN checkout
  (`C:\AiMaxBossman-...\bossman-core`, branch feat/web-designer-live-panel @
  d07284b) + default CWD = main checkout. Test files ran from kimi-wt but
  product code (`bcc`, `bossman`, `bossman_shared`) imported from the FOREIGN
  tree → version skew → ASK/execution mismatch.
  Proof: `python -c "import bcc; print(bcc.__file__)"` → main checkout.
- Fix (harness, env-only, zero repo edits):
  `kimi-shim/sitecustomize.py` via `PYTHONPATH=<shim>` + workdir INSIDE the
  worktree: drops stale editable finders, puts `<wt>/bossman-core` + `<wt>`
  first on sys.path, PRELOADS `bcc` from `<wt>/command-center/bcc` WITHOUT
  exposing `command-center/` on sys.path (else its `tests/__init__.py`
  hijacks top-level `tests` and breaks `from tests.test_v3_fleet_e2e import`).
  Verified resolution: bcc, bossman, bossman_shared, bossman_v3,
  tests.test_v3_fleet_e2e ALL → kimi-wt.
- Clean proof: `junit/adapters-clean.xml` → tests=6 errors=0 failures=0
  skipped=0 (2026-09-06T15:08+02:00). M1 restart/resume semantics HOLD.
- Action for coder: none on product code. Keep shim (outside repo) for all
  future runs on this machine. CI (fresh venv + pip install -e per suite) is
  NOT affected by this artifact.
- Invalidated: ALL kimi-wt runs before 15:00 (cycles 0/A/B-first-pass) are
  STALE_EVIDENCE and are being re-run under the shim.

## BUG-003 [TO-PORT] context_slice hashes text, test hashes bytes (Windows)
- Status: REAL bug, fixed+verified on glm worktree, NOT YET ported to kimi
  branch. Action: port below, re-test, commit, push as next checkpoint.
- Files: `tools/context_slice.py:187,240` (`hashlib.sha256(text.encode())`
  after `read_text()`); contract in docstring says "content sha256 (path +
  bytes)" (`tools/context_slice.py:126-131`); `repo_map` sibling at line 118
  already uses `read_bytes()`.
- Windows mechanism: `Path.write_text("Y = 3\n")` stores CRLF on disk;
  `read_text()` translates back to LF for hashing in lib, while tests hash
  `read_bytes()` (raw CRLF) → `57dca5... != 0f702b...`
  (`tests/test_context_slice.py:203`). Linux-unaffected (LF everywhere) →
  CI-green, host-red. Classic portability gap.
- Fix (code): hash `p.read_bytes()` at both lines (keep `text` for
  tokens/AST). Test robustness (same class as repo SKIP_HOST convention):
  line 72 expectation `sha256(b"Y = 3\n")` → `sha256((root/"pkg"/"d.py").read_bytes())`
  (identical on Linux, truthful on Windows); symlink block lines 94-98 →
  try/except OSError → `pytest.skip("SKIP_HOST: ...")` (WinError 1314, no
  SeCreateSymbolicLinkPrivilege on this host).
- Verified on glm tree: `test_context_slice + test_skips_registry +
  test_v5_observers` → 34 passed, 3 skipped; registry regen
  `python tools/skips_registry.py` → 127 entries, 0 without reason.

## NOTE-004 [ENV] WinError 1314 symlink privilege (2+1 tests)
- `tests/test_v5_observers.py` (2 tests) + `tests/test_context_slice.py`
  (1 state) cannot create symlinks: no Developer Mode / not elevated.
  Convention fix (repo-wide, e.g. command-center/tests/test_plugins_adapter.py:208):
  `pytest.skip("SKIP_HOST: symlink privilege unavailable...")`.
  Applied on glm tree; port with BUG-003. Coverage preserved on capable
  hosts. No Owner action required (do NOT demand elevation).

## NOTE-005 [SCOPE] Cycle-0 full-tree collection errors (20) — NOT product bugs
- `pytest` from repo ROOT collects 3451 + 20 errors; per-suite as CI runs:
  root `pytest tests` → 709/0 errors; bossman-core → 2492/0;
  command-center → 1950/0.
- The 20: apps/* not pip-installed (separate products, installed per-app in
  their own pipelines); `resource` = POSIX-only stdlib (Windows
  EXTERNAL_BLOCKER); cross-suite `tests` package collision (CI avoids by
  per-dir runs); handoffs/* = UNTRUSTED data, not a suite.
- Playbook rule proposed moving files/adding deps — WRONG here (would pollute
  gates). Correct action: document scope, run per-suite. No commit.

## OPEN_P0 / OPEN_P1 (for coder)
- P0: none proven yet (BUG-001 is P1 until UI repro + fix; V3/V4/V5 gates green where run clean).
- P1: BUG-001 (CFR [2-4]); BUG-003 port pending (fix known, 10 min work).
- EXTERNAL: WinError 1314 (no symlink priv); POSIX `resource` module (ai-3d-maker tests on Windows).
