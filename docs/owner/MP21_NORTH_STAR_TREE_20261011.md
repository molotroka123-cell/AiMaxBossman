# MP21 Track A: North Star tree vs sources, 11.10.2026

Branch `mp/a-map-20261011` (base `250e24ec`). Nothing was published or deployed. The public site was not touched.

## Three copies, three different dates

| Copy | Location | Nodes | Built from | Note |
|---|---|---:|---|---|
| Seed in the repo | `command-center/bcc/capability_tree_seed.json` | 893 at base, 896 after this pass | `as_of 2026-10-05`, `source_sha e4539337d971`; leaves appended by `tools/tree_registry_sync.py` | the only copy that is under git |
| Publisher data | `C:\Users\asd\Bossman\site-tree-publisher\data\tree.json` (own git, HEAD `5b1c3af`) | 879 | snapshot of `4044058b0544` (`ux/genjutsu-live-constructor`), generated 2026-10-11T07:28Z | `meta.json`: 13 zones, 14 hidden, `live_tests_run: false`, `current_ci_audited: false` |
| Deployed clone | `C:\Users\asd\Bossman\site-subtle-baklava\data\tree.json` (HEAD `2e43fd3`) | 836 | 07.10 swarm data, "green stays 556" | what the Netlify repo `subtle-baklava` carried at last local sync; the live URL was not fetched in this pass |

Status counts, publisher copy: working 492, recorded 172, reported 94, branch 31, idea 25, prepared 20, mixed 16,
code 16, blocked 13. In the deployed clone the same leaves are mostly `reported`; 491 moved `reported -> working`
between the two (receipt re-statusing, not new code).

## Ladder (real step names, from `meta.json` and `ladder.override.json`)

| # | Step id | Title on the site | Reached on the site |
|---|---|---|---|
| 1 | `SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT` | Инфраструктура самоулучшения есть | yes (only entry in `ladder.override.json`) |
| 2 | `SELF_REPAIR_SINGLE_CYCLE_PASS` | Один цикл самоисправления пройден | no |
| 3 | `SELF_REPAIR_3_CYCLE_PASS` | Три цикла подряд | no |
| 4 | `TRANSFER_MEASURED_GAIN` | Перенос навыка измерен | no |
| 5 | `24H_SOAK_PASS` | 24 часа без сбоев | no |
| 6 | `48H_SOAK_PASS` | 48 часов без сбоев | no |
| 7 | `WEEK_MODE_READY` | Недельный режим | no |
| 8 | `REVENUE_CAPABLE_PILOT` | Пилот с доходом | no |

Local evidence for steps 2 to 5 is `docs/evidence/north-star-ladder-20261010.md` (branch
`feat/north-star-ladder-20261010`): step 2 PASS under the site definition and PARTIAL under the stricter doc gate
(analogous-task element failed), step 3 FAIL (1 of 3), step 4 NOT RUN, step 5 in progress and likely FAIL.
`ladder.override.json` was deliberately not edited by that run and was not edited here. Steps 6 to 8 have no local
evidence at all. The ladder step ids are not defined anywhere in executable code; they appear only in docs and
site data (`grep SELF_REPAIR_3_CYCLE_PASS` hits only `.md` files), so "reached" is a hand-maintained override.

## Seed against publisher

- Every publisher node is in the seed (`pub - seed = 0`). The seed has 14 more:
  - 8 `retired` (they fall off the public tree by design): `mod-pattern_miner`, `mod-promotion_gate`,
    `mod-v15_owner`, `oss-109` (omniget), `skill-23`, `skill-24`, `skill-38`, `reg-promo_video`.
  - 6 leaves that exist in this branch and not yet in the 4044058b snapshot (missing on the public tree, present in code):
    `reg-cu_task_scoped_apps` (reported), `reg-faceswap_hairline_guard` (reported),
    `reg-faceswap_makeup_layer` (prepared), `reg-genjutsu_live_constructor` (reported),
    `reg-trend_edit_analyzer` (prepared), `reg-video_animation_gate` (prepared).
- One status difference: a node `prepared` in the seed is `idea` in the publisher copy.

## Seed against sources (does the leaf point at something that exists at HEAD?)

Method: each non-retired seed node carries `sources[].path`; a leaf is "backed" when at least one path is a tracked
file at `250e24ec`. Check script: inline Python in this pass, rerunnable from the seed.

| Group | Count | Meaning |
|---|---:|---|
| Backed by a tracked file | 806 | code or doc exists; this is not proof the function works |
| No `sources` at all | 36 | 13 zones and umbrella nodes, `cap-10` (Звонки), `cap-37`, `skill-51`, the 5 `d0710-*` and 15 `s0710-*` nodes (ideas and 07.10 session notes) |
| All sources missing at HEAD | 49 | see below |

The 49 with missing sources (12 + 9 + 3 + 1 + 19 + 5):

- 12 `cap-*` leaves whose evidence file is not in this tree: `MODEL_MARKET_20261004.md` (cap-15..19, cap-44, six leaves),
  `docs/owner/runs/evidence-20261001/*` (cap-0, cap-6, cap-45), `docs/audits/SELF_IMPROVEMENT_2_0_AUDIT_20261003.md`
  (cap-11, cap-31, cap-35, all three `blocked`). These are historical claims whose files live on another branch or on disk.
- 9 `mod-*` feature modules listed as `branch`: `assistant_plan`, `candidate_generator`, `eval_engine`, `mimik`,
  `observatory`, `open_news`, `trace_recorder`, `twitter`, `viral_vfx`. The code is not in `command-center/bcc/features/`
  at this commit. Status: missing here, present on some other branch.
- 3 `skill-*` (`mimik`, `open-news`, `game-3d-model`) whose `.agents/skills/*` paths are absent.
- 1 `app-osiris` (`apps/osiris/README.md` absent).
- 19 `module-*` leaves: 11 point at `bossman-core/bossman/{apprentice/selector_repair,cognitive/*,reality/*}`
  (the `cognitive` and `reality` directories do not exist in `bossman-core/bossman/` at this commit) and 8 at
  `command-center/bcc/telegram_calls/{addons,doctor,guard,stopflag,speech/brain|stt|tts|testing}.py`. For the
  telegram_calls ones the current tree has `addon.py`, `doctor_rows.py`, `hardening.py` and `speech/{factory,
  jeff_engines,scripted,text}.py`, so some are probable renames and some are simply gone. They need a `sources`
  re-sync by someone who can tell which; no file was guessed here.
- 5 `ci-*` leaves whose "source" is a CI job name, not a path. Not file-checkable; recorded, not verified here.

## Code without a leaf (existing in code, not in the tree as its own leaf)

The seed groups by feature, not by file, so file-level absence is noisy (407 files had no direct path match). The
useful gaps, found by comparing module families against `mod-*`, `reg-*` and `cap-*` labels, are:

- `bcc/autonomy/*` (supervisor, constitution, journal, stop, lease, budget, workers): 17 files, tree has the
  aggregate `cap-35` (blocked) and no per-module leaf.
- `bcc/code_mode/*`, `bcc/context_os/*`, `bcc/direct_gen/*`, `bcc/file_intelligence/*`, `bcc/hybrid/*`,
  `bcc/market/*` (17 files), `bcc/rave/*`, `bcc/studio/*`, `bcc/telegram_companion/*` (17 files),
  `bcc/terminal_cli/*`, `bcc/v2/*` (30 files), `bcc/video_studio/*`: packages with no direct per-file leaf. See the
  inventory in `docs/owner/MP21_PROJECT_MAP_20261011.md` for LOC, tests and docs per package.
- 9 tools with no test reference and no leaf: `cu_acceptance` (1507 LOC, computer-use acceptance harness),
  `north_star` (1761 LOC), `runpod`, `limits_out_orchestra`, `lessons_catalog_20261010`, `bossman_tomorrow`,
  `web_access_probe`, `swarm_verify_patch`, `ux_attack`.

## Leaves added in this pass (manifest `docs/owner/runs/mp21_a_leaf_manifest_20261011.json`)

All three enter at status `code`; green status only comes from receipts via `tools/tree_apply_evidence.py`
and none was given here.

| Leaf id | Parent zone | Source | Test | Status |
|---|---|---|---|---|
| `reg-local_only_switch` | models | `bossman_shared/privacy.py` | `command-center/tests/test_local_only_switch.py` (21 passed together with test_offline_mode) | code |
| `reg-offline_smoke_local_only` | models | `tools/offline_smoke_local_only.py` | same test file; smoke itself rc=0 | code |
| `reg-mp21_project_map` | ops | `tools/mp21_project_map.py` | none | code |

Seed after the pass: 896 nodes; `test_capability_tree.py` and `test_tree_apply_evidence.py`: 44 passed.

## Not done and why

- No change to `ladder.override.json`, no publish, no deploy (owner instruction).
- The 49 broken `sources` were not repaired: they need a decision on which branch holds the originals, and a wrong
  guess would write a false path into a "source" field.
