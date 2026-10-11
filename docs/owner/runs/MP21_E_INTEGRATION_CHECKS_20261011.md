# MP21-E: integration checks, 11.10.2026

Worktree `C:\Users\asd\Bossman\wt-unified-20261010`, branch `feat/bossman-genjutsu-jeff-unified-20261010`.
Machine: Windows 11 10.0.26200, Python 3.12.10, pytest 9.1.1, git 2.55.0.windows.3, node not used.
CPU policy: video renders had priority, so one pytest process at a time, no xdist, no repo-wide run.
`PYTHONPATH=<wt>/command-center;<wt>;<wt>/bossman-core` was set for every command (a bare `python` imports an OLD checkout
on this machine, see `MP21_A_CHECKS_20261011.md`). Solana tests use the isolated interpreter
`C:\Users\asd\Bossman\venv-solana-20261011\Scripts\python.exe` (Python 3.12.10, pytest 9.1.1, has `solders`).

## 1. What was integrated (all by merge commit, no history rewrite, no force)

| Source | Source SHA | How | Conflicts and decision |
|---|---|---|---|
| `mp/a-map-20261011` | `c1f5f5eb` | merge | none |
| `mp/b-20261011` (Poker docs + evidence) | `9627d580` | merge | none |
| `mp/b-poker-20261011` Bossman part (BotLab rake/report) | `1e3d25d0`, `6d9f2020` | cherry-pick -x (different base, only `apps/poker-botlab`) | none; pokertrain repo commits are NOT copied, SHAs only (see WHERE_WE_STOPPED) |
| `mp/c-exchange-20261011` | `62874cb1` (includes owner TZ decision) | merge | none; redacted summary has counts and hashes only, no amounts |
| `mp/d-trading-audit-20261011` | `01345739` | merge | none |
| `feat/jeff-browser-search-20261011` | `72a68663` | merge | none; one test canary marked for the secret scan |
| `feat/ads-queue-20261010` (committed files only) | `0bdd4af0`, then `c951c8fe` | merge | `tools/trend_edit/edit_core.py`: ads side rewrote the whole file with CRLF (real change 7 lines); kept the unified file and re-applied the 7 lines by hand |
| `feat/hair-mask-20261010` | `c82c71d7` | file ported under another name | same path as the ads sheet; ads version (newer, 11.10) is the spec, hair-mask sheet kept as `HAIR_MASK_V2_METRICS_M1_M6_20261010.md` |
| `feat/promo-queue-20261010` | `c9c8d07a` | merge | none (not previously on the line: `git cherry` showed `+`) |
| `feat/video-v2-artifacts-20261010` | `95bd6d97` | merge | none textual; tests re-run |
| `feat/runpod-lora-v2-20261010`, `feat/mimics-gate-20261010`, `feat/t3-video-eval-20261011` | `2f791573`, `43e8d245`, `bd41f154` | merge | none |
| night branch `claude/bossman-final-convergence-hu2702` | `e9ac78aa`, `6f6a3873`, `595133f1` | 3 merges | README.md: unified kept, night README archived as `docs/owner/NIGHT_BRANCH_README_ARCHIVE_20261011.md`; `tools/release_candidate.json`: unified kept (night declaration `rc-2026-09-20` is stale); `pyproject.toml`: unified extras + night `setuptools>=68`; `bcc/studio/dispatch.py`: unified kept + night `free_only` passed to the provider; `.agents/skills/solana-volume-suite/SKILL.md`: night body + stricter MP21-D quarantine description |

Skipped (and why): `jeff/one-20261009` (62 commits ahead, old, not on the owner's list), `integrate/bossman-2.1-one-20261006`,
`consolidate/jeff-20261007`, `audit/motion-concert-20261006`, 17 `worktree-agent-*` branches: pre-date this line or are
other agents' work; no port without an owner decision.

## 2. Commands and results

| # | Command (cwd) | rc | Result |
|---|---|---|---|
| T1 | `pytest tests/test_studio_authoritative_tariff.py tests/test_studio_cloud.py tests/test_studio_paid_dispatch_audit.py tests/test_mimik_skill.py tests/test_open_news_skill.py tests/test_skill_build_context_regression.py tests/test_open_news_query_regression.py tests/test_metrics_cancelled_waiter_regression.py -k "not test_http_failure_never_becomes_success"` (command-center) | 0 | 166 passed, 5 deselected (the deselected parametrised test aborts in teardown on Windows: `PYTEST_CURRENT_TEST` exceeds 32767 characters; NOT counted as passed) |
| T2 | `pytest tests/test_local_only_switch.py tests/test_offline_mode.py tests/test_jeff_browser_search.py` (command-center) | 0 | 66 passed |
| T3 | `pytest tests/test_capability_tree.py tests/test_tree_apply_evidence.py` (command-center) | 0 | 44 passed, 1 warning |
| T4 | `pytest tests/test_direct_gen_faceswap.py` (command-center) | 0 | 34 passed |
| T5 | `pytest tests/test_trend_edit.py` (repo root) | 0 | 29 passed |
| T6 | `pytest tests` (apps/poker-botlab) | 0 | 72 passed |
| T7 | `pytest tests/exchange_volume` (repo root) | 0 | 25 passed, 1 skipped (local anonymised export, absent in CI) |
| T8 | `pytest tests/test_trading_mp21_d_fixes.py tests/test_trading_pump_creator_cohort.py tests/test_trading_strategy_lab.py tests/test_trading_mm_sim.py tests/test_trading_holder_monitor.py tests/test_trading_live_gate.py` (bossman-core) | 0 | 43 passed |
| T8b | `pytest tests/test_trading_*.py` (bossman-core), run before the last merge | 0 | 197 passed, 3 skipped |
| T9 | `pytest tests/test_mp21_d_volume_suite_quarantine.py` (repo root) | 0 | 2 passed |
| T10 | isolated venv: `pytest solana_volume_suite/tests tests/test_solana_safety.py` (repo root) | 0 | 99 passed |
| S1 | `set -o pipefail; python tools/ci_secret_scan.py` (repo root) | 0 | `Secret-pattern scan: PASS` (after marking one synthetic key canary with the repo's `ci-secret-scan: allow`; the first run rc 2 reported `test_jeff_browser_search.py:325: openai key`) |
| C1 | `python tools/mp21_dataset_counters.py docs/owner/runs/MP21_E_DATASET_COUNTERS_20261011.json` | 0 | counters written (302 videos, byte-identical duplicates counted) |

## 3. What was NOT run (not a PASS)

* No repo-wide pytest and no `-n` run (owner CPU priority). `docs/owner/MP21_PROJECT_MAP_20261011.md` therefore still has
  309 `partial` rows: "not proven here", not "broken".
* No exact-SHA CI, no Windows archive build, no install, no restart of :8801, no UI sweep, no browser acceptance.
* No JS tests (token-launch-helper has no `node_modules`; UI node tests not run), no ruff/mypy (not installed).
* New ads modules (`build_ads.py`, `face_gate.py`, `hair_v2.py`, `accept_ref.py`) were only `py_compile`d; they need the video environment.
* `tests/test_mainnet_setup_safety.py`: 6 failures reported by the night branch before this integration (stale NameError in the
  retired setup stub); not in the CI workflow; not re-run here.
* Live computer-control drive, 24-hour soak, live poker match, real-GPU video renders: NOT RUN in this pass.
* Network: no cloud call, no push of media, no deploy, no site publish.

## 4. Tree state

See section "Final state" below (written at push time).

## 5. Final state (at push time)

Recorded in `docs/owner/WHERE_WE_STOPPED_20261011.md` section 1 and in the final chat report: canonical SHA, night-branch SHA,
`git merge-base --is-ancestor` results and `git ls-remote` output. They are not repeated here because the commit that
contains this file cannot name its own SHA.
