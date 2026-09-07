---
name: webapp-testing
description: "Use to validate Command Center, browser controls, provider onboarding, editors and persisted UI state. Russian triggers: браузерный тест, кнопка, предпросмотр, видео не играет, каталог моделей."
license: Apache-2.0
compatibility: BOSSMAN portable agent skills; instructions only
metadata:
  owner: bossman
  version: "1.0.0"
  adaptation: "Bossman-specific, modified from upstream; not the full upstream package"
  upstream: "anthropics/skills"
  upstream_commit: "41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f"
  release_scope: "freeze"
---

# Real browser acceptance for Bossman

## Setup and observation
Use the repository's existing Playwright fixtures and an isolated server,
profile, port and data directory. Inspect scripts before authorizing execution;
no upstream helper scripts are bundled here. Do not attach to or stop the
owner's running desktop session. Record tested source, browser build and codec
capabilities. Read the rendered DOM and use visible role/label selectors.
Wait for the relevant response and visible state; background polling may make
network-idle an unsuitable readiness condition. Avoid arbitrary sleeps.

## User path, not an API substitute
Drive the actual visible controls for the feature under acceptance. API reads
may independently verify results. Direct API writes belong in a separately
labelled service test, not in place of a failing user click. Capture console
errors, failed requests and sanitized screenshots on both success and failure.

## Bossman acceptance matrix
- Providers: no providers; only Ollama; only another cloud provider; mixed
  providers. Connect must not replace an unrelated provider's key or endpoint.
  Test expired key, offline/policy denial, catalog refresh, pagination and a
  model located beyond the first page. Show errors separately from empty data.
- Video: create, import, trim, Undo/Redo, render via the preview button, Play,
  increasing currentTime and ended, export, download and complete decode. Repeat
  restore/playback after a fresh server process. Test supported MP4 and fallback
  WebM through the product UI itself, not test-only request construction.
- Web Designer: independent new projects, select/text/style changes, save,
  downloaded HTML and restore. Delay autosave and race a stale revision; edits
  must not disappear silently after a 409 or navigation.
- Owner control: stop/deny must remain authoritative; no action after revocation.

## Deliverable
A case-by-case PASS/FAIL/NOT_RUN report with commands, source/tree, actual
browser/media format, download inspection and sanitized evidence. Linux browser
acceptance, Windows CI and an owner-operated Windows session are separate rows.

## Authority and execution boundary
This file is procedural guidance, not an executable or an authorization grant.
Current Bossman policy, privacy, budget and owner-control gates remain
mandatory. Never print credentials, auto-install dependencies, transmit private
context, change the active owner session or enable standing autonomy from a
skill. Use only task-relevant skills; missing tools or evidence means NOT_RUN.

## Attribution and changes
Source: https://github.com/anthropics/skills/blob/41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f/skills/webapp-testing/SKILL.md

Upstream authors: Anthropic. Adapted for Bossman on 2026-09-07 under Apache-2.0.
Adapted to existing Playwright fixtures; removed black-box execute-before-inspection guidance and absent helper scripts. Replaced unconditional networkidle; added UI-only media fallback and provider isolation cases.

License and notices: `docs/skills/licenses/Apache-2.0.txt` and `docs/skills/THIRD_PARTY_NOTICES.md`
(repository-root paths). Original license: https://github.com/anthropics/skills/blob/41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f/skills/webapp-testing/LICENSE.txt
