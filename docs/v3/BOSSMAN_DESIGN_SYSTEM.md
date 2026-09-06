# Bossman creative suite — design system

Derived from the Video Studio reference design. It exists so that the video
studio, the web designer and the assistant panel read as one application rather
than three, and so that five people building in parallel converge without a
later reconciliation pass.

This describes a **dark, dense, desktop-grade** tool. Density is a feature: an
editor is used for hours by someone who already knows where things are. Do not
pad it out into a marketing page.

## Layout skeleton

Every editor page uses the same five regions.

| Region | Role | Width |
|---|---|---|
| Top bar | identity, project breadcrumb, mode tabs, global actions | full, 48px tall |
| Left rail | resources: trees, libraries, asset grid | 240–280px, resizable |
| Centre | the artefact being edited, with its own header and transport | fills |
| Right inspector | properties of the current selection, tabbed | 260–300px, resizable |
| Assistant column | the AI panel, dismissible | 300–340px, resizable |
| Bottom | timeline or equivalent working surface | 300px, resizable |
| Status bar | environment truth, never decoration | full, 28px tall |

Panel sizes persist per user. The assistant column and the bottom surface can be
collapsed; the centre never can.

## Colour

Neutral ramp, darkest to lightest. Use the role, not the hex, in code.

| Token | Value | Use |
|---|---|---|
| `--bg-app` | `#0b0e13` | window background, behind everything |
| `--bg-panel` | `#12161d` | rails, inspector, assistant |
| `--bg-raised` | `#1a1f28` | cards, inputs, clip bodies |
| `--bg-hover` | `#222834` | hover on a row or control |
| `--bg-active` | `#2b3342` | selected row, pressed control |
| `--line` | `#232a35` | dividers, panel edges, input borders |
| `--text` | `#e6ebf2` | primary text |
| `--text-dim` | `#8b95a5` | labels, counts, secondary |
| `--text-faint` | `#5a6474` | disabled, placeholder |

Accent and state. One accent carries interaction; one carries the primary
action. Do not add a third.

| Token | Value | Use |
|---|---|---|
| `--accent` | `#3ddad7` | active tab underline, focus ring, completed step, automation line |
| `--primary` | `#2f6fed` | the single primary button on a screen (Export, Publish) |
| `--ok` | `#3fb950` | ready, healthy, saved |
| `--warn` | `#d29922` | degraded, approval required |
| `--danger` | `#f04747` | destructive action, failed step, clipping |

Track identity colours, used on clip bodies and track headers so a glance
identifies the layer: video `#3b6ea8`, b-roll `#4a7fb5`, titles `#7a5cc4`,
effects `#2f7d6e`, voice `#2f7d5a`, music `#2f6fed`, sound effects `#8a6d3b`.

## Type

One family: the platform UI stack. `-apple-system, "Segoe UI", Roboto,
"Helvetica Neue", Arial, sans-serif`. Numeric readouts — timecode, dB, counts,
coordinates — use `ui-monospace, "SF Mono", Consolas, monospace` with
`font-variant-numeric: tabular-nums`, so digits do not shift as values change.

| Role | Size | Weight |
|---|---|---|
| Panel title | 13px | 600 |
| Body and controls | 12px | 400 |
| Label | 11px | 500, `--text-dim` |
| Micro (counts, durations) | 10px | 400, `--text-dim` |
| Timecode readout | 15px | 500, monospace |

## Spacing and shape

A 4px grid. Spacing steps 4, 8, 12, 16, 24. Panel padding 12px; control gap
8px; group gap 16px.

Radii: 4px on inputs and small controls, 6px on cards, thumbnails and clips,
8px on panels, 999px on pills and chips. Borders are 1px `--line`. One shadow
only, for floating surfaces: `0 8px 24px rgba(0,0,0,.45)`.

## Controls

**Numeric field.** A boxed input, right-aligned, monospace, with the unit in
`--text-dim` inside the box. Dragging horizontally on the label scrubs the
value; Shift is coarse, Alt is fine. Every field that can be animated carries a
keyframe diamond, and every field that has been changed from its default carries
a reset arrow. Both sit to the right of the field and are `--text-faint` until
hovered.

**Slider.** 4px track, `--bg-raised` unfilled, `--accent` filled, a 12px round
handle. The numeric value sits to the right as an editable field, never as a
label only.

**Toggle.** A 28×16px switch, `--bg-raised` off, `--accent` on. Use a switch for
"this feature is on", a checkbox for "this item is selected". Do not mix them.

**Collapsible group.** A 28px header row: chevron, title in label style, and any
group-level control right-aligned. Collapsed state persists.

**Tabs.** Text only, 12px, with a 2px `--accent` underline on the active tab.
No boxes, no pills.

**Colour wheel.** A circular hue ring with a draggable point, a neutral centre,
and its name beneath in label style. Three of them, shadows/midtones/highlights,
sit in one row.

**Buttons.** Default is a quiet button: `--bg-raised`, 1px `--line`, `--text`.
The primary button is filled `--primary` with white text, and there is exactly
one per screen. A destructive button is filled `--danger`. Disabled controls
drop to `--text-faint` and must expose the reason on hover, never simply go
dead.

## Timeline

Tracks are rows with a fixed 132px header holding, in order: track identity
letter and name, a type icon, a dropdown, an eye toggle, a lock toggle, and for
audio a mute and solo pair. The ruler shows timecode at an interval chosen from
the zoom level, never a fixed one.

Clips are 6px-radius bodies in their track identity colour, carrying a filmstrip
of real thumbnails for video and a real waveform for audio. A label sits at the
top left, and any speed or effect badge at the top right. Selected clips take a
1px `--accent` border, not a colour change, so identity stays readable.

Audio tracks draw an automation line with draggable keyframe points over the
waveform. The playhead is a 1px `--accent` line with a grab handle in the ruler.
Snapping is a toggle in the timeline toolbar and must visibly indicate when a
drag has snapped.

Level meters sit to the right of the tracks with a real dB scale: 0, -6, -12,
-18, -24, -36, -48. Peaks hold briefly. Clipping turns the segment `--danger`
and stays until reset.

## The assistant column

This is the part that makes the suite different, and it has one rule that
overrides visual preference: **it must never present a reported success as a
verified one.** The repository spent a whole cycle fixing exactly that
confusion.

Structure, top to bottom: a sparkle mark with the assistant name and one line of
subtitle; the current request as a quiet card; the execution plan; quick
actions; a before/after comparison when the work produced one; a prompt box; and
example prompts as pills.

Each plan step is a row: a state mark, the step title, one line of detail, and
elapsed time right-aligned in monospace. The state marks are distinct and
non-negotiable:

| Mark | Meaning |
|---|---|
| filled `--accent` check | the post-state was independently observed and matched |
| hollow `--accent` ring | the tool reported success, verification has not run |
| `--warn` dot | waiting on the owner |
| `--danger` cross | failed, with the typed reason on the row |
| `--text-faint` ring | not started |

A hollow ring and a filled check must never be drawn the same way, and a step
must never be shown as complete because the model said so.

Quick actions are a two-column grid of quiet buttons, each an icon and a short
verb phrase. Each names a capability; if no executor can serve it, the button is
disabled and says why on hover.

## Status bar

The status bar carries environment truth and nothing else: project locality,
autosave state with its timestamp, derived-asset readiness, and on the right the
duration, resolution, frame rate and channel layout. A green mark here is a
claim about the system and must be backed by an actual check, not by optimism.

## States

Every panel defines four: loading, empty, error and offline. An empty panel says
what would appear here and offers the action that creates it. An error says what
failed, in the typed reason the backend returned, and what the owner can do. A
blank region is never acceptable.

## Accessibility and input

Full keyboard operation of every control. A visible 2px `--accent` focus ring,
never removed. Roles and labels on custom controls. Text contrast at least 4.5:1
against its background; the neutral ramp above satisfies this for `--text` and
`--text-dim` on `--bg-panel`, and `--text-faint` is reserved for disabled and
placeholder only.

Shortcuts follow editor convention: space toggles playback, J/K/L shuttle, I and
O set in and out, B razors, V selects, and Cmd/Ctrl+Z and Shift+Cmd/Ctrl+Z undo
and redo. A discoverable shortcut overlay lists them all.

## Offline

The suite ships offline. No external font, script, style or icon may be fetched
at runtime. Icons are inline SVG in a single sprite.
