# Video Studio UX specification and frontend evidence

Baseline: `debee6930f29595b84cea64d526b6f8bef139e8a`. The frontend is an additive native BCC page, `#/video-studio?project_id=…`. The supplied reference image informs the dark compact panels, cyan selection and timeline geometry. Production UI contains no demonstration assets or invented progress.

## Layout and navigation

Native Bossman Chat / Video Studio tabs preserve the linked task. The editor has a project switcher, RU/EN switch, Edit/Color/Audio/VFX/AI workspaces, undo/redo and export. Media library is left, source/project preview center, selected object properties right, and multitrack timeline below. The collapsible agent panel is a fourth column on wide screens and an overlay below 1500px. Library, inspector and timeline dividers resize and persist locally; Reset layout restores defaults. Narrow screens rearrange the inspector below preview.

Media cards use actual backend thumbnails, metadata, folder/tag search and name/duration/size sorting. Multi-file input and external file drop upload browser File streams through the authenticated common API. Files are not decoded into long video buffers for the timeline. Double-click or drag imports a media reference into a track. Right-click media edits name/folder/tags. Source properties expose server analysis, local transcription and relinking to an already imported replacement.

Timeline uses integer microsecond ticks, rational speed and FPS-aware timecode. Tracks expose mute/solo/lock and properties. Drag moves a clip; edge handles trim; S splits at the absolute playhead; Delete removes and Shift+Delete requests ripple removal. Snapping considers other clip edges, markers and playhead. Ruler scrubs, timeline zoom and horizontal/vertical scrolling preserve access to long sequences. Marker and range dialogs use explicit times. Titles and timed caption editing have dedicated forms. Advanced commands use explicit object IDs and JSON review, not ambiguous natural-language positions.

## Common command and collaboration behavior

All editing calls send `{project_id,expected_revision,operation_id,command,dry_run}` to `/commands`. Dry-run does not mutate state. The review panel retains the same operation ID, command and source revision for apply; a changed proposal or revision invalidates its review. Command errors remain visible and do not display successful edits. Known conflicts require a refresh and renewed review.

The inspector and drag acquire a 30-second human object lease through the canonical store. Drag additionally retains the starting revision. Background project updates do not replace the document revision underneath an active text input. An update never chooses a different clip or moves the playhead. Render progress updates only the jobs region; they do not rebuild a playing preview or active input. Leases currently expire rather than being renewed through an indefinitely open edit: revision checks still reject stale application; prolonged interactive lease renewal remains a limitation.

Ctrl/Cmd+Z undoes; Ctrl/Cmd+Shift+Z redoes; Ctrl/Cmd+K opens the command palette; Space toggles preview playback. Shortcuts ignore editable fields and open dialogs. Clip selection focuses the editor without scrolling the page. Changed-object buttons navigate to affected clips; history presents revision actors, commands and server comparison. The agent panel edits proposals through the same human-reviewed command layer; it does not invoke an independent model, grant permissions or claim weight training.

## Preview, export and failure states

Source preview is clearly distinguished from rendered project preview. A project preview is produced by the same FFmpeg pipeline as export and labels the source revision. Older previews are marked outdated. Export submits a job into the canonical BCC TaskEngine and displays the actual job state, stage and measured rendered time. Acceptance of a job ID is not completion. Download links appear only after server completion; verification details expose measured dimensions, duration, streams, decoding and sampled frames. Cancel requests the existing task stop API. Project open/reload fetches its existing jobs rather than creating duplicates.

The UI distinguishes empty project, importing, unavailable source, command conflict, active render, failed/stopped/unknown outcome, and successful verified output. Network errors remain visible. Unsupported formats, disk errors, unavailable models and provider capabilities use backend error messages. No image/video generation result is fabricated. This first verified frontend wave does not claim the complete requested editor or VEGAS parity.

## Current validation

- `command-center/ui/tests/video_studio_state.test.mjs`: six passing pure tests for microsecond time, rational speed/ramp/freeze duration, FPS timecode, active-sequence lookup/snapping, revision/dry-run identity, and nonmutating media filtering.
- `command-center/ui/tests/video_studio_browser.cjs`: real Chromium and authenticated isolated BCC, real FFmpeg test fixture, no network or API mocks. Creates a project through the UI, imports a two-second MP4, adds a clip, trims to one second, changes saturation, produces preview and export, checks server verification and reloads the queue.
- Initial actual run: preview 320×180 and export 1280×720, 25 fps, one second, AAC stereo/48 kHz, full decode and three sampled-frame hashes passed; zero browser page errors. Screenshots were visually inspected. A global disabled command-bar overlay was reported to the integration owner and fixed there. Editor height was adjusted to fit the available viewport.

Run state tests with `node --test command-center/ui/tests/video_studio_state.test.mjs`. Browser test requires Playwright and an isolated loopback server: set `VIDEO_UI_URL`, `VIDEO_UI_TOKEN_FILE` and `VIDEO_UI_FIXTURE`; run `node command-center/ui/tests/video_studio_browser.cjs` from repository root. The token file must belong to the disposable test instance. `.audit-work` holds test media/screenshots; they are test artifacts, not product demonstration content.

## Remaining frontend limitations

Advanced color curves/LUT, some effects, history comparison and general operations use structured forms/JSON rather than specialist graphical editors. Waveforms, scopes, wheels, direct keyframe curve manipulation, proxy selection, multi-selection grouping/linking, automatic B-roll/framing and full nested/multicam visual editors are not verified as complete. Some technical parameter labels remain English in RU mode. Project portability/duplication, active object lease renewal and exhaustive accessibility/mobile coverage still require further work. Capability matrix status must account for these limitations separately from renderer/backend coverage.
