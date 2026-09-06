# One primary Video Studio — 2026-09-06

Canonical editor: `#/video-studio`, shipped from the repository's default
branch. `codex/video-studio` is the earlier implementation branch, not a
second product to install. The default branch retains that editor plus the
later linked-frame, CFR endpoint, SDR and media-read verification fixes.

This consolidation keeps the professional media bin, preview, inspector,
multitrack timeline, AI proposals and revision-guarded commands. Fluent
header styling follows the desktop theme; preview and timeline retain dark
surfaces. On compact workspaces the AI panel is docked below the inspector,
instead of floating over the image. Narrow screens stack the same panels.

The ad-hoc global Chat/Video tab strip is removed. The shared sidebar and
desktop launcher open the same canonical editor. The existing chat, uploads
and task history remain available through `#/bossman-chat`, under More and
from the editor's AI panel. No projects, revisions, jobs or media are migrated
or deleted, and no alternate backend is introduced.

Project selection continuity now works after the editor changes its URL via
replaceState. Russian `открой` requests correctly retain the selected project.
Closing the AI panel persists that display preference; commands are unchanged.

Validation: 17 Node tests passed, including project continuity, listener
disposal, the canonical route and existing editing/desktop contracts. All
three changed JavaScript modules passed syntax checks; whitespace check
passed. Chromium is unavailable in this environment: actual responsive
browser screenshots and visual acceptance remain unverified. No new backend
or render changes require repeating the prior FFmpeg qualification.

Rollback: revert this UI-only commit; project data and command contracts are
compatible. Continue developing this editor in the default branch, rather
than publishing another independently styled copy.
