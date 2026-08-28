---
name: browser-research
description: Navigate and inspect websites efficiently using DOM-first browser automation, screenshots only when visual understanding is needed, and strict approval gates for sensitive actions.
compatibility: BOSSMAN, OpenCode, Claude-compatible agent skills
metadata:
  owner: bossman
  version: "1.0"
---

# Browser Research

Use for website research, lead collection, UI inspection, and browser tasks.

## Efficiency rule

Prefer:

`DOM snapshot -> deterministic browser action -> DOM snapshot`

Use vision/screenshot only when:
- layout or design must be judged
- canvas/image content matters
- DOM is ambiguous
- a visual regression must be verified

Do not send full screenshots to a large model every step.

## Safe defaults

AUTO:
- navigate to ordinary pages
- read DOM
- screenshot
- click non-sensitive navigation
- type into non-sensitive local/search fields

ASK:
- login
- upload
- download
- submit an external form
- send a message
- create or modify an account
- change settings on a third-party service

DENY unless a separate explicit policy exists:
- purchase
- payment
- wallet transaction
- bank transfer
- disabling security controls

## Research evidence

For each factual finding save:
- source URL
- page title
- relevant extracted text or structured field
- timestamp when freshness matters

Do not invent a website problem that was not observed.

## Human takeover

If takeover is active:
- stop autonomous clicks/typing
- keep session alive
- re-snapshot the page before resuming
