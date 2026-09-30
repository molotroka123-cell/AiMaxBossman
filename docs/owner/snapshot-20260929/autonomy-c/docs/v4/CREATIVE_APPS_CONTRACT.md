# Creative applications — implementation contract

Owner amendment: 2026-09-06. Extends V4 M8/M9, with V5 upkeep using the same
mission contracts. This is ordered work, not an assertion of shipped features.
Target product comparisons require a matched task benchmark; no Vegas,
ChatGPT or Claude feature/performance parity is currently established.

## Existing code and ownership

Video work starts from `codex/video-studio` at
`ac2cde8eb3e0f4985178cb3fb54a7419aeb3d8e1`; web-design work starts from
`feat/web-designer-live-panel` at `279eeb6adb992c119697ce1de00c068a529fb615`.
The current primary contains Video Factory's scene/job/render pipeline; the
two application branches require independent integration review. Improve their
existing architectures in isolated worktrees. No wholesale merge into the
failing V3 base. Publish reviewed slices with explicit base and tests.

## Video editor

The product is a non-destructive editor with a project bin, preview, multitrack
timeline, inspector and prompt panel. AI proposes a versioned edit plan; the
owner can preview, alter, undo or accept it. Source media is retained. Render
and export use existing authorization, bounded resources and output verification.

| Order | Capabilities | Acceptance |
|---|---|---|
| V1: editing foundations | Inspect and strengthen existing split, trim, move, ripple, snapping, track mute/lock and undo/redo; implement a missing coherent slice first | Frame-consistent edges; no zero/negative clips or unintended overlap; locked tracks unchanged; round-trip serialization and undo restore exact state |
| V2: usable timeline | Zoom, keyboard shortcuts, thumbnails/waveforms, playhead/selection, proxy previews, relink missing media, autosave/recovery | Bounded caches; responsive large timeline; missing source visible; crash cannot corrupt accepted project revision |
| V3: finishing | Keyframes, transforms, transitions, titles, captions, color controls, audio gain/fades, export profiles and queued renders | Local fixture render/readback with duration, frame, audio and subtitle checks; finite job limits; cancel/restart behavior |
| V4: AI assistance | Transcript-based edits, suggested cuts, caption correction, scene organization, rough-cut proposals, beat-aware suggestions where observed audio permits | Proposal lists affected clips and evidence; ambiguous instructions require clarification; no destructive source overwrite or silent export |
| V5: professional qualification | Multicam synchronization, color-managed workflow, advanced audio and high-resolution/GPU acceleration where supported | Feature-specific real-media tests and target hardware attestations; unsupported codecs/features explicitly unavailable |

First implementation must improve an actual current operation, with tests and
existing API/UI integration where applicable. Deferred capabilities must not
appear as working buttons. AI features consume the existing Gateway and budget;
no paid inference is required for deterministic edit-plan fixture tests.

## Web-design application

The product is a project workspace with chat/proposals, component tree,
editable inspector, responsive preview, version history and reviewable export.
User code/assets remain scoped to their project. Preview isolation, local file
boundaries and current network policy remain mandatory.

| Order | Capabilities | Acceptance |
|---|---|---|
| W1: editing foundations | Inspect existing live panel; improve versioned edits, undo/redo and responsive viewport controls in a coherent slice | Stale revision rejected; undo/redo lossless; invalid preset/property leaves accepted project unchanged; actual UI controls wired |
| W2: design system | Component insertion/reordering, spacing, typography, color tokens, reusable variants, asset management | Valid component tree; shared token updates predictable; keyboard access; no raw arbitrary executable property injection |
| W3: AI design loop | Prompt-to-draft, selected-component refinement, comparison variants, explainable patch preview and scoped apply | Model output remains untrusted proposal; diff contains only intended files/components; rollback restores prior project |
| W4: complete project | Multiple pages, navigation, breakpoint overrides, forms, themes, accessibility inspection, source/export bundle | Internal links and responsive states tested; output schema valid; export path bounded; forms have truthful unavailable states until connected |
| W5: publish readiness | Visual regressions, broken-link checks, image optimization, SEO metadata and performance diagnostics | Measured preview/build results; no synthetic scores; publishing is a distinct owner-authorized action |

## Shared gates and integration order

Inspect branch code → pin existing document/schema contracts → implement one
vertical slice per app → targeted tests and independent review → publish app
branch changes → selective integration after V3 closure and V4 M1/M3/M5 gates.
Applications keep existing stores until a reviewed migration is required; no
parallel task, permission, budget or completion authority is introduced.
V1/W1 can progress while V3 is blocked; runtime activation in the Epoch 4
release still requires the canonical epoch dependency gates.

Every slice reports base SHA, changed files, test commands/results, missing
platform/render/browser evidence and rollback. Revert code with a compatible
project reader, retain assets/version history, and park uncertain render jobs.
Never weaken preview isolation or effect checks to make a demo appear complete.

Lead integrates; video and web specialists implement in parallel; a separate
reviewer examines the actual patch. Reuse prior audits and run affected tests
first to conserve usage. Large feature inventories do not replace accepted
working slices. Broad capability and comparative product claims remain blocked
until their corresponding acceptance evidence exists.
