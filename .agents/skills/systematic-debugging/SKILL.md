---
name: systematic-debugging
description: "Use for a reproducible bug, failing CI, launch failure, timeout or unexplained behavior. Russian triggers: баг, не работает, не открывается, зависает, ошибка, падает тест."
license: MIT
compatibility: BOSSMAN portable agent skills; instructions only
metadata:
  owner: bossman
  version: "1.0.0"
  adaptation: "Bossman-specific, modified from upstream; not the full upstream package"
  upstream: "obra/superpowers"
  upstream_commit: "b36e0829c6d0140e93cfef2ca599b1b07d4a7797"
  release_scope: "freeze"
---

# Systematic debugging for Bossman

## Procedure
1. Pin the failing commit and actual checkout tree. Record OS, Python, browser,
   FFmpeg, configuration names and sanitized errors. A PR head and its merge
   commit are different experiments; record both, not just the branch name.
2. Reproduce the smallest failing user path. Run the same case against a clean
   baseline when claiming a regression or an environment-only failure.
3. Trace the first incorrect value across UI, API, service, storage, gateway and
   model boundaries. Record presence/status, not environment dumps or keys.
4. Form one falsifiable hypothesis and change one variable. Compare to a working
   sibling path. Do not increase timeouts, retry counts or token caps merely to
   hide an unexplained failure.
5. Add a regression that fails for that cause, apply the smallest fix, then run
   it and the affected neighbors. Preserve unrelated work and active sessions.
6. After three unsuccessful hypotheses, summarize evidence and ask the
   integrator for a focused architecture decision; do not rewrite the subsystem.

## Bossman probes
- A model catalog failure needs the actual HTTP/policy/error state: authentication,
  endpoint normalization, quota, empty catalog and transport failure differ.
- A preview returning HTTP 200 and decoding in FFmpeg is not proof of browser
  playback. Capture the exact media stream, browser codec answers, MediaError,
  response headers, readyState, currentTime and ended. Do not generalize one
  browser build to every Linux browser.
- A timed-out action may already have happened. Diagnose and park ambiguous
  effects; never retry an irreversible effect to make a test pass.

## Deliverable
Report: tested SHA/tree, reproduction command, expected/observed behavior,
root cause or unresolved hypothesis, changed files, failing-before/passing-after
results, impact scope and remaining uncertainty. Delegate final completion
claims to the existing proof-before-done skill.

## Authority and execution boundary
This file is procedural guidance, not an executable or an authorization grant.
Current Bossman policy, privacy, budget and owner-control gates remain
mandatory. Never print credentials, auto-install dependencies, transmit private
context, change the active owner session or enable standing autonomy from a
skill. Use only task-relevant skills; missing tools or evidence means NOT_RUN.

## Attribution and changes
Source: https://github.com/obra/superpowers/blob/b36e0829c6d0140e93cfef2ca599b1b07d4a7797/skills/systematic-debugging/SKILL.md

Upstream authors: Jesse Vincent / obra. Adapted for Bossman on 2026-09-07 under MIT.
Condensed for Bossman; removed environment/keychain dump examples, external helper dependencies and instructions to delete work. Added exact-checkout, media and ambiguous-effect probes.

License and notices: `docs/skills/licenses/SUPERPOWERS-MIT.txt` and `docs/skills/THIRD_PARTY_NOTICES.md`
(repository-root paths). Original license: https://github.com/obra/superpowers/blob/b36e0829c6d0140e93cfef2ca599b1b07d4a7797/LICENSE
