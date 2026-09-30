# Continuity desktop checkpoint

The Command Center shell now uses a blue/violet CSS wallpaper, frosted desktop
surfaces, a compact taskbar, and a light default theme. Existing saved dark-theme
preferences remain supported. Existing home commands, mission data, app manifests,
approvals, connection warnings, and navigation still drive the product.

The taskbar resolves its destinations from registered pages. Video Studio is shown
only when `video-studio` is registered; this checkpoint does not invent a video
endpoint. Web Designer, owner controls, and mission console use the existing router.
No hardware figures, agents, or task completion states are fabricated for the design.

Accessibility includes current-page announcements, a skip link that focuses content
without changing routes, visible keyboard focus, the existing mobile navigation,
reduced-motion and reduced-transparency preferences, and opaque glass fallbacks.
The global command panel clears the taskbar and mobile navigation.

## Verification

- `node --test command-center/tests/js/desktop.test.mjs`: 4 passed.
- `node --check command-center/ui/app.js`: passed.
- `test_continuity_desktop_ui.py` plus existing `test_ux2_home_attention.py`:
  3 browser tests skipped because Chromium is unavailable in this environment.
- Visual screenshot and real-browser acceptance remain unverified; the generated
  concept image is a design reference, not evidence of rendered application output.

Rollback: revert this checkpoint. The API, persisted mission state, and backend
execution semantics are unchanged. The saved theme preference remains compatible.
