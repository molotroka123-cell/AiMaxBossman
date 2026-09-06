# Saved work checkpoints

Owner instruction: save every completed, tested checkpoint as a commit and
publish it. Each entry below is a reviewable checkpoint, not an epoch release.
Date: 2026-09-06. Preserve upstream work; no force pushes or implicit merges.

| Work | Published location | Evidence / next gate |
|---|---|---|
| Epoch 4 Continuity / Epoch 5 Steward plans | `astra/epoch4-plan-20260905`, `13db72ee0c8b98cd2c5c5897e04a844fffb2536c`, PR #7 | Canonical `docs/v4/EPOCH_4_PLAN.md`, `docs/v5/EPOCH_5_PLAN.md`, product and creative-app contracts |
| Continuity foundations and reaction coordinator | `astra/epoch4-evolution`, PR #8; code checkpoint `d4b655c21834cd91234e9ba2cd20908d6e91ae10` | 247 root tests; 272 selected Core tests + 1 open-defect strict xfail; 17 CC truth tests; 71 reaction/visual tests; selections overlap. `docs/v4/FOUNDATION_REPORT.md` |
| Responsive web-design preview | `astra/web-designer-properties`, `dfd72a6fc87ee420d9bad675c6b4bf4caff36d7f`, PR #9 | 16 JS tests; 21 Python passed / 2 Chromium skips. Actual GUI qualification, stale-edit protection and SVG-preserving edits remain open |
| Linked frame-aligned video editing | `astra/video-frame-edit`, `5a3053319583e04874b3efd35647af8f4adc08cb`, PR #10 | 72 Python passed / 1 then-open export strict xfail; 11 JS passed. `docs/video-studio/FRAME_SPLIT_CHECKPOINT.md` on app branch records editing scope |
| Exact CFR export endpoints | `astra/video-frame-edit`, `c45cb788fbd9ea79c590878dd1516481bc1ae851`, PR #10 | 53 focused tests passed, zero skips/xfails. Historical 25→24-frame loss fixed; actual first/last content and rational ranges verified. Broader 66 passed / 2 skipped / 1 unchanged color-preview failure reproduced on pristine base. `docs/video-studio/CFR_RENDER_CHECKPOINT.md` |

App PRs target their existing app branches. Foundation PR targets the published
plan branch. These branches have not been merged into Fable's primary or
presented as a finished integrated application. Published code trees were
checked against the tested local trees; API-created commit IDs differ from
local commit IDs because publication uses the canonical remote parents.

## Continuation rules

1. Read canonical plans and latest checkpoint report on the relevant branch.
2. Fetch the exact primary and app branches; inspect new diffs before integration.
3. Fable's last observed primary is `d6b43cea0a1127bba7fa2cdabbd80dfa6da681bc`.
   Its six workflows still have three failures. Recheck fresh results; do not
   treat this record as current CI or a repair claim.
4. Keep V4 runtime activation blocked on M0, including the open unsigned
   in-flight journal replay defect E4-RT-001. V5 implementation depends on
   accepted V4; its plan is defined, not its runtime.
5. Use bounded specialist slices, affected tests and one independent review.
   Commit/publish each accepted checkpoint before expanding scope.
6. Record actual hardware/browser evidence. Neither 3x performance nor
   human-level computer control nor professional-editor superiority is measured.

The owner's supplied Video Studio image guides the intended media bin,
preview, inspector, AI panel and multitrack timeline layout. Pixel-level
visual matching and complete browser interaction have not been certified.
