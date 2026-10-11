# VIBE_STUDIO_CHECKS_20261011 - evidence

Machine: Windows 11, one pytest process, no `-n`, GPU queue untouched. cwd = `command-center` (cwd inside `bcc/` shadows stdlib `secrets`).
Versions: Python 3.12.10, pytest 9.1.1, node v20.20.2, Chromium via Playwright (`tests/browser_support.py`).
Base: `origin/feat/bossman-genjutsu-jeff-unified-20261010` @ e27abf18; branch `feat/vibe-studio-20261011`.
Upstream audited: caffeinum/vibeOS @ 1815394ce4a77732a217e8efc35450d962d3dd13, MIT (read-only clone `mp-vibe-temp`).

## Commands and results

| # | Command | rc | Result |
| - | --- | - | --- |
| 1 | `python -m py_compile bcc/long_run.py bcc/vibe/*.py bcc/features/vibe.py bcc/features/apps.py bcc/provider_governance.py bcc/mission_budget.py bcc/economy_orchestrator.py` | 0 | compile-ok |
| 2 | `node --check ui/pages/vibe.js` | 0 | ok |
| 3 | `python -m pytest tests/test_vibe_studio.py tests/test_long_run_profile.py tests/test_vibe_ui_static.py tests/test_vibe_sandbox_browser.py -q -p no:cacheprovider` | 0 | **60 passed** in 38.9 s |
| 4 | regression: `tests/test_free_only_contract.py test_free_only_registry.py test_mission_budget.py test_provider_governance_memory.py test_economy_orchestrator.py test_economy_swarm.py test_fable_hard_cap.py test_apps_manifest_probe_paths.py test_apps_probe_isolation.py test_apps_launchable.py test_v6_apps_probe_cost.py test_feature_tick_health.py test_v2_core.py test_release_health.py test_direct_gen_ui_static.py test_v6_lazy_pages.py` | 0 | **182 passed, 1 skipped** in 140.8 s |
| 5 | earlier same session: `test_apps_control.py test_apps_owner_path_regression.py test_free_only_contract.py test_mission_budget.py test_provider_governance_memory.py test_v6_apps_probe_cost.py test_fable_hard_cap.py` | 0 | 103 passed |
| 6 | `python -m bcc.long_run --root <tmp> enable/status/stop/resume/disable` (CLI smoke) | 0 | profile written with caps clamped (haiku 25 -> 10), STOP shown as `owner STOP: autonomy STOP (demo)`, cleared, disabled |

## What the 60 tests cover

`test_vibe_studio.py` (41): build writes only under `apps/<slug>/v1`, event order planning < generating < file_written < check_passed < completed, cursor polling, preview CSP/inlining, fetch to foreign host rejected + nothing written + one repair round, repair success, traversal slugs (8 forms) and file names (8 forms) rejected, 22 scan negative controls, benign app + allowlist, size/count limits, ladder fallback local -> free (paid never called), local_only never calls cloud, secret-like request keeps off cloud, cloud payload canary (previous code never sent, local rung gets it), cancel in flight, STOP blocks start / halts in flight, re-queue after restart, versions/diff/rollback keep every version, tampered version refused at preview, promotion needs owner approval (pending -> approved -> LIVE card) and rejection stays out, real `RegistryLadder` ordering + profile gating + paid call through the ledger.

`test_long_run_profile.py` (13): default profile unchanged (3 paid models refused, $2.00/400k), broken profile files change nothing, exactly 3 families on their hosts, cap clamp to EUR 10, booking + report, loud cap hit (provider not called, ERROR log, refusal row), per-provider/day totals, unknown outcome keeps worst case, STOP blocks and clears, free/local never touch ledger, mission limits larger but finite and meta wins, CLI round trip, price units per-1M vs per-token.

`test_vibe_ui_static.py` (4) and `test_vibe_sandbox_browser.py` (2, real Chromium): sandbox attribute without allow-same-origin; hostile page under sandbox+CSP - attacker listener received 0 requests, parent DOM / localStorage / cookie blocked; negative control without sandbox/CSP - the same page leaks (hits > 0).

## Bug found by the tests during the work

Price units: `models.price_in/out` are USD per 1M tokens, the economy orchestrator's synthetic model is USD per token. The first estimator assumed per-token and refused a normal GLM call (EUR 15.27 "worst case"). Fixed with `price_unit: per_token` marker; covered by `test_price_units_registry_per_million_vs_orchestrator_per_token` and the real-registry ladder test.

## Not run

- Real generation with a live local or cloud model (GPU busy with the queue; no paid spend): all model calls in tests are fakes.
- Full 680-file suite (CPU budget): only the files above.
