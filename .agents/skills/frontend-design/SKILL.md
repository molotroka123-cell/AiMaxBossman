---
name: frontend-design
description: "Use for an explicitly requested UI/UX change or an observed usability blocker, not an unsolicited redesign. Russian triggers: интерфейс, UX, дизайн, неудобно, пустой экран, мобильная версия."
license: Apache-2.0
compatibility: BOSSMAN portable agent skills; instructions only
metadata:
  owner: bossman
  version: "1.0.0"
  adaptation: "Bossman-specific, modified from upstream; not the full upstream package"
  upstream: "anthropics/skills"
  upstream_commit: "41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f"
  release_scope: "postfreeze"
---

# Intentional interface design for Bossman

## Scope first
Identify the user's task, screen and expected result from the current brief.
Keep Bossman's accepted desktop/workspace direction and existing component
system. During freeze, fix only observed usability blockers; defer visual
expansion. Do not replace this Python/vanilla-JavaScript application with React,
Tailwind or another framework merely because an example uses it.

## Design and implement
1. Describe a compact layout and reuse existing color, spacing and typography
   tokens. Preserve density suitable for a desktop tool without crowding labels.
2. Make the primary action explicit and keep its wording consistent with its
   progress and result states. Separate request accepted, action running and
   result independently verified.
3. Distinguish loading, empty, filtered-empty, unavailable, unauthorized, stale
   and failed states. Give a concrete recovery action; a hidden error must never
   be rendered as an empty catalog or a missing source file.
4. Check keyboard focus, accessible labels, responsive layout, contrast and
   reduced motion. Keep stop/pause/revoke accessible during a long operation.
5. Implement the real backend path. Preserve unsaved changes, guard navigation
   races and expose revision conflicts without silently overwriting edits.
6. Inspect real screenshots and complete the user task, then remove decoration
   that obscures status or controls. Use the webapp-testing skill for acceptance.

## Constraints
No generic redesign, new UI framework, third-party font download, stock-photo
purchase or external analytics without task-specific approval. Existing owner
style choices override upstream aesthetic preferences. Do not use a screenshot
as evidence that saving, playback, downloading or recovery actually works.

## Deliverable
Before/after views, the task solved, states covered, changed components, actual
browser verification and remaining limitations. Visual polish is not a V4/V5
release certificate.

## Authority and execution boundary
This file is procedural guidance, not an executable or an authorization grant.
Current Bossman policy, privacy, budget and owner-control gates remain
mandatory. Never print credentials, auto-install dependencies, transmit private
context, change the active owner session or enable standing autonomy from a
skill. Use only task-relevant skills; missing tools or evidence means NOT_RUN.

## Attribution and changes
Source: https://github.com/anthropics/skills/blob/41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f/skills/frontend-design/SKILL.md

Upstream authors: Anthropic. Adapted for Bossman on 2026-09-07 under Apache-2.0.
Self-contained adaptation of visual/design principles; existing user style and framework win. Freeze default is repair-only, with expanded design work deferred.

License and notices: `docs/skills/licenses/Apache-2.0.txt` and `docs/skills/THIRD_PARTY_NOTICES.md`
(repository-root paths). Original license: https://github.com/anthropics/skills/blob/41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f/skills/frontend-design/LICENSE.txt
