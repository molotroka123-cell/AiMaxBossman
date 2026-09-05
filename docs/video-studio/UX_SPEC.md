# Video Studio UX specification and independent frontend inventory

Baseline: `debee6930f29595b84cea64d526b6f8bef139e8a`. Native BCC route: `#/video-studio?project_id=…`. This inventory compares the supplied request with actual editor controls; backend tests alone do not establish complete UI behavior. No VEGAS parity is claimed. The editor is still PARTIAL overall.

## Layout and shared editing

The supplied reference informs the dark, dense panels, cyan selection and multitrack timeline. Native Bossman Chat / Video Studio tabs preserve the linked task. Project selection, workspaces (Монтаж / Цвет / Звук / VFX / ИИ), RU/EN, undo/redo and export sit above the library, preview, inspector and collapsible agent panel. Resizable library, inspector and timeline retain local preferences; Reset layout restores defaults. The agent panel moves to an overlay within the editor below 1500 px. Actual Chromium checks covered 1720×1100 and 1280×900; exhaustive mobile/accessibility parity remains unverified.

Edits use the existing authenticated `/commands` envelope with project ID, source revision and operation ID. Review preserves the exact command, operation ID and base revision for apply. Stale revisions are rejected visibly. Background events never replace the revision under an active text input; selection and playhead are retained. Inspector/drag obtains a human object lease for 30 seconds, renewed after 15 seconds while that edit remains active. Drag retains the starting revision. Ordinary UI edits are immediate reversible commands; model proposals require the explicit review/apply controls.

The plain-language agent panel sends only an objective, explicit selected clip ID and project/revision identity to the canonical local proposal job. It displays the raw model response and host validation. A validated draft is not applied automatically. A change to the selected clip, proposal text or revision invalidates the pending result. It shares the same command review and undo/history layer. Existing task links open the linked Bossman conversation.

## Request-to-control inventory

`IMPLEMENTED` below is limited to the stated behavior and evidence; it does not imply every related specialist editor feature is complete. `PARTIAL` means a working subset, a structured command workflow, or missing end-to-end verification. Backend-only capabilities remain separate in the root capability matrix.

| Requested behavior | Actual editor control / route | Status and remaining gap |
|---|---|---|
| Create/open/rename | Header project selector and project menu, persistent command API | IMPLEMENTED; actual UI create/open/reload exercised |
| Duplicate/archive/restore | Project menu, archived-project selector, canonical commands | PARTIAL; wired to actual store, dedicated UI round-trip regression still needed |
| Autosave/restart/version history | Every mutation is saved; reload GET; history/compare dialogs | IMPLEMENTED for command persistence/reload/history; crash-recovery outcomes depend on existing engine |
| Multiple import/drop | Browser File streams, multi-input, media/library drop, authenticated `/media` | IMPLEMENTED for real upload; OS folder recursion is not implemented |
| Folders/tags/search/sort/metadata | Media context properties, folder filter, text search, metadata sorting | IMPLEMENTED; pure filtering tests and actual imported metadata |
| Missing source/relink | Source error state, imported replacement selector, `/media/relink` | IMPLEMENTED for explicit replacement; automated disk search UI not implemented |
| Thumbnail/proxy/waveform | Canonical prepare job, cached-only image/video GET, source proxy toggle and whole-file waveform | IMPLEMENTED with real PNG and playable proxy proof; waveform is a source overview, not per-clip resampled waveform editing |
| Long media | Metadata-only clips, visible horizontal timeline window, backend cached media | PARTIAL; no full long-video RAM load, but large vertical track counts and long-project stress tests remain |
| Portable project | Project menu queues verified ZIP including sources | IMPLEMENTED export/download; native ZIP import control is connected to verified queued restoration but final UI round trip is pending; optional omission of sources remains a gap |
| OTIO / Shotcut interchange | Project menu downloads OTIO/MLT; OTIO file import | IMPLEMENTED OTIO native round trip; Shotcut is a constrained export, foreign MLT execution/import absent |
| Multiple tracks/add/mute/solo/lock | Timeline headers, track dialog, mixer, canonical track commands | IMPLEMENTED basic controls; complete track-reorder gesture is still command-palette based |
| Move/trim/split/delete | Pointer move/trim handles, source/timeline numeric fields, S/Delete/Shift+Delete | IMPLEMENTED basic operations; keyboard split real UI proof |
| Ripple/roll/slip/slide | Ripple Delete shortcut; palette ripple trim/roll; slip/slide forms | PARTIAL; structured operations work, direct roll/ripple gesture tools not complete |
| Snapping/markers/range/zoom/scroll | Timeline toolbar, ruler, marker/range forms, clip-edge snapping | IMPLEMENTED basic behavior; exhaustive drag edge cases need further UI testing |
| Group/link/detach/copy | Ctrl/Shift multiselect, Group/Link toolbar, palette detach/copy/ungroup | PARTIAL; grouping tested through real UI; richer linked-selection and detach workflows remain |
| Nested sequences | Sequence settings/duplicate/select commands and typed palette | PARTIAL; no dedicated nested timeline navigation/creation editor |
| Speed/reverse/freeze/ramp | Speed numeric field, reverse button, freeze form, ramp palette | PARTIAL; structured ramp rather than a speed curve editor |
| Multicam sync | Explicit manual offsets in palette, backend available analysis separately | PARTIAL; camera angle view and interactive sync editor not implemented |
| Position/scale/rotation/opacity/crop/PiP | Numeric transform/crop fractions, overlapping video tracks | PARTIAL; actual transform command layer, no on-canvas handles/presets verified |
| Keyframes/easing | Clip-local keyframe form, visible keyframe list/remove, volume automation | IMPLEMENTED numeric automation and easing persistence; graphical curve manipulation absent |
| Transitions/crossfades | Fade effect forms / effect JSON | PARTIAL; no transition edge drag/browser; full transition parity not claimed |
| Masks/chroma/blend/adjustment | Mask/chroma numeric forms, effect JSON, adjustment track kind | PARTIAL; mask drawing, blend picker and adjustment-layer specialist UI remain |
| Stabilization/tracking | Stabilize command; backend tracking availability reported | PARTIAL; no tracked object rectangle/trajectory editor yet |
| Brightness/contrast/saturation/gamma | Color numeric inspector | IMPLEMENTED saturation render flow; exact exposure/temperature semantics are not claimed |
| White balance/highlights/shadows | Color balance channel forms | PARTIAL; numeric channel adjustment, not calibrated temperature/tint controls |
| Curves/wheels/LUT/copy | RGB point JSON, LUT text form, effect.copy palette | PARTIAL; color wheels and graphical curve interaction absent |
| Scopes | Real queued histogram/waveform/vectorscope PNG with timestamp/hash | IMPLEMENTED source snapshot; explicitly not realtime or post-effect scopes |
| Before/after | Source preview vs versioned rendered project preview; effect toggle/history comparison | PARTIAL; side-by-side/wipe image comparison absent |
| Audio gain/pan/mixer | Clip fields and per-track mixer strips using canonical commands | IMPLEMENTED real clip gain and track pan UI tests; realtime meters absent |
| Audio fades/EQ/compressor/limiter/noise/loudness | Numeric effect forms and backend processing | PARTIAL; working forms, every combination not browser-audited |
| Ducking/clipping/A-V sync | Effect JSON and measured analysis/verification output | PARTIAL; dedicated sidechain selector/meter workflow remains |
| Titles/fonts/style/animation | Timed title form with font/color/size, transform keyframes | PARTIAL; reusable style templates and full title animation editor absent |
| Timed captions/SRT/VTT | Caption table, actual upload, local download, common replace command | IMPLEMENTED real SRT import/edit/VTT preservation; rendered-file decoding tested with captions present |
| ASR/translation | Local capability gating and canonical jobs | PARTIAL; local ASR depends on the host model; saved EN→RU captions queue a local draft, explicit review/apply retains IDs/times and source revision; real UI acceptance is pending resource admission |
| Text-based montage | `transcript.cut` typed range command | PARTIAL; no clickable transcript selection editor |
| Scene/pause analysis | AI/source inspector canonical local analysis jobs with measured output | PARTIAL; results are observations, not automatic rough-cut completion |
| Rough cut/short versions/search/duplicates/B-roll/framing | Local caption/tag query, result navigation, existing-asset B-roll add and duplicate observations plus general command review | PARTIAL; retrieval controls are connected, UI model/search acceptance pending; autonomous rough-cut quality is not claimed |
| Image/video generation | AI capability reason shown; no generated placeholder output | BLOCKED when no permitted verified provider is configured |
| Voice enhancement/background/upscaling | Actual available deterministic backend effects via command layer | PARTIAL; model-specific capabilities and dedicated workflows are not implied |
| Export all/range/profile/dimensions/FPS/codec/container/bitrate/audio | Export form; blank dimensions use profile defaults; exact rational FPS and integer tick range | IMPLEMENTED real Reels 1080×1920 render; audio layout explicitly fixed 48 kHz stereo |
| Hardware/stream copy | Hardware encode-probe job, profile-bound successful encoder options; stream-copy mode | PARTIAL; actual profile probe required, current hardware result does not promise other sizes/codecs |
| Progress/cancel/retry/verify | Existing TaskEngine job stages, cancel, new export after correction, completion receipt/download | IMPLEMENTED completion verification/reload; exhaustive UI cancellation/restart/device/disk failure cases remain |
| Agent review/change navigation/undo/compare | Proposal panel, changed-object navigation, history, common undo | IMPLEMENTED actual local trained draft→review→apply and collaborative stale-edit rejection |

## Verified browser scenarios

All browser evidence uses an authenticated disposable loopback BCC instance, actual Chromium, actual FFmpeg media, no API or rendering mocks. A tiny generated fixture is a test input, never production demonstration content.

- `ui/tests/video_studio_state.test.mjs`: **10 PASS**. Exact ticks/rational time, duration, snapping/lookup, immutable filtering, command identity, profile-default dimensions, exact FPS/range validation, VTT text preservation, and cross-project binding of late model drafts.
- `ui/tests/video_studio_browser.cjs`: actual UI import → trim → saturation → preview → export → reload. Preview 320×180; export 1280×720, one second, AAC 48 kHz stereo; decode and three frame samples verified, no browser page errors.
- `ui/tests/video_studio_advanced_browser.cjs`: **11 real checks PASS**. Ruler/keyboard split; multiselect/group; clip volume and mixer pan; timed audio keyframe/easing; SRT import/timed edit/VTT; relink; canonical prepare/cached waveform/playable proxy; measured histogram snapshot; real Reels render; verified portable ZIP download; native OTIO round trip.
- Advanced run: project `99b916b72b90581f9ecb5cb6b334f67d`, final revision 13; Reels job `1c96bb118c2647f29a1568e2da5c8230`, source revision 12, actual 1080×1920/25 fps/one second, verified decode/AAC/sample frames and zero A/V start offset. No browser page errors.
- Real trained UI job `4a09bf912f4d4f11b43897594de4cf65`: explicit selected clip + objective volume 0.5; host-valid local LoRA draft did not mutate project. Shared dry-run retained revision 9; explicit Apply persisted volume 0.5 at revision 10. No browser page errors. This proves that observed task, not general model correctness.
- Additional disposable collaboration script exercised eight checks: viewport fit, EN switch, stale manual input rejection after a concurrent change, dry-run no mutation, explicit apply, retained selection/playhead, undo and compact layout. The detached-modal concurrency regression is now persisted separately.

Screenshots in `.audit-work`: `video-studio-color.png`, `video-studio-export.png`, `video-studio-compact-en.png`, `video-studio-trained-draft.png`, `video-studio-advanced.png`. Visual review caught and corrected a disabled shell overlay (integration owner), an agent overlay that covered the header, and a stale media thumbnail after relink. Relink preview/thumbnail/waveform URLs now include the source content hash so stable media IDs cannot retain the previous source in the browser cache.

Run pure tests with `node --test command-center/ui/tests/video_studio_state.test.mjs`. Browser scripts require Playwright and explicit `VIDEO_UI_TOKEN_FILE`, `VIDEO_UI_FIXTURE`, and for advanced tests `VIDEO_UI_REPLACEMENT`; `VIDEO_UI_URL` defaults to the disposable `http://127.0.0.1:8878`. Never point these mutating acceptance scripts at a production project or token.

- `ui/tests/video_studio_conflict_browser.cjs`: **PASS**. A real websocket-triggered project read occurred during an open caption modal. Remote rename advanced revision 13→14; stale modal apply returned 409, captions remained unchanged, zero browser page errors.
- `ui/tests/video_studio_ai_browser.cjs`: two bounded attempts timed out while translation remained **queued**, with no work executed and no model result fabricated. Measured free RAM was 1470 MB versus the existing 1536 MB estimate plus 1024 MB reserve floor. Isolated retry follows release of task-owned idle model memory; admission policy is unchanged.

## Observed defects corrected in this UI wave

Final bounded AI acceptance subsequently passed after releasing the idle model process:
**5 real checks PASS**, translation job `a6298c9d43d541469f28eea35ab318a1`,
project `99b916b72b90581f9ecb5cb6b334f67d`, revision 18, zero page errors.
Verified draft translation without mutation, reviewed Apply preserving cue IDs/times,
caption search with explicit navigation, duplicate evidence without deletion, and
existing-library B-roll candidate added through the shared command layer.
The earlier queued attempts above remain recorded as resource-admission observations.

1. Profile selection sent sequence width/height unconditionally, overriding Reels/square dimensions. Blank size fields now preserve renderer defaults; actual Reels output verified.
2. VTT download used a global timestamp replacement that changed decimal commas inside dialogue. Time formatting now touches timestamps only; actual downloaded Russian caption preserves `1,234`.
3. Select fields inherited option text in their accessible label. Explicit field labels allow reliable keyboard/accessibility discovery and real E2E interaction.
4. Relink retained the media ID while replacing content; the browser could retain the former thumbnail/video. Source-hash URL identity now changes with content.

5. Detached dialogs were outside the editor focus test, so a background refresh could advance their source revision. Dialogs now retain the original document revision; actual concurrent UI regression proves rejection without overwriting captions.
6. Polling from a previous project could deliver its output after a project switch. Poll callbacks now verify captured project identity before changing any preview or result state. Opening a different project clears the old pending-proposal flag; late admission replies are ignored. A dedicated ten-test pure suite verifies project/revision/clip/draft binding; the actual cross-project browser race remains pending.

## Remaining product work

The request remains larger than the implemented UI. Major specialist gaps are graphical curves/wheels/masks, live post-effect scopes/audio meters, multicam/nested visual editors, transcript-led editing, final portable import UI regression, advanced AI planning/generation routes, and comprehensive long-media/edge-state accessibility testing. Some technical effect labels remain English in Russian mode. Unsupported providers/models report their actual cause. Working first-path and advanced tests do not establish completion of these remaining functions.
Restored analysis jobs currently expose their raw results but may lose the session-specific action label because the status API does not return the original action. Specialized translation review/scope presentation after a reload is a remaining recovery UX limitation.
