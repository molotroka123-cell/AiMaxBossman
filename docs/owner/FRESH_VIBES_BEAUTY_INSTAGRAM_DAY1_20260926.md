# Fresh Vibes Beauty — Instagram Day-1 Owner Run

Planned owner date: **2026-09-26**
Branch: `feat/bossman-1.6-bossnet-foundation-20260925`

Goal: bootstrap one empty owner-controlled Fresh Vibes Beauty Instagram account through the normal Bossman surface with explicit Telegram approval and verified external results.

## Do not send the password through chat

Tomorrow the owner should **not paste the Instagram password into GitHub issues, Telegram approval messages, model prompts or documentation**.

Preferred flow:
1. Bossman opens the bound Instagram browser profile locally.
2. Owner enters username/password directly into the local browser/secure secret surface.
3. Owner completes 2FA/security checkpoint personally if Instagram asks.
4. Bossman verifies the visible account identity.
5. The resulting authenticated browser session is reused; the raw password is not retained in task memory.

If official Instagram API onboarding is available and worth doing, create/authorize the Meta app/token separately. The Day-1 pilot must not be blocked solely on official API setup if the governed browser path can perform the small owner-approved bootstrap safely.

## Frozen Day-1 scope

Bossman may prepare:
- one avatar;
- one feed post;
- one Story;
- one Highlight;
- complete profile fields;
- five follow candidates.

Bossman may externally apply/publish/follow only after owner approval.

No ads. No DMs. No mass likes/comments. No additional follows. No account creation. No CAPTCHA bypass.

## Phase 0 — preflight

Record:
- installed Bossman exact SHA/build;
- Fresh Vibes business namespace;
- expected Instagram username;
- current account type;
- browser/API connection readiness;
- Telegram owner identity;
- STOP availability;
- `web_research` readiness;
- SearXNG/approved general-search readiness if used.

If web research is not LIVE, mark that and continue only with sources that are genuinely available. Do not fake competitor research.

## Phase 1 — account connection

1. Open the isolated Fresh Vibes Instagram browser profile.
2. Owner performs initial login locally.
3. Handle 2FA/checkpoint by human handoff only.
4. Read back username/profile identity.
5. If identity != expected Fresh Vibes account: STOP/BLOCKED.
6. Persist only the governed session reference, not the raw password.

Optional API lane:
- verify whether account is Professional/Business;
- verify current official Instagram API requirements/permissions;
- authorize through the official owner flow;
- store token in the approved vault only;
- prove one read-only call before any API write.

## Phase 2 — truthful business data collection

Use:
- Fresh Vibes Beauty website/public pages;
- owner-confirmed information;
- existing Fresh Vibes business graph.

Prepare a profile data sheet with confidence/provenance.

Required fields where truthfully available:
- display name;
- category;
- Prague/location wording;
- bio;
- website/booking URL;
- contact email/phone if owner-confirmed;
- address if owner-confirmed;
- languages/services wording;
- avatar.

Unknown fields remain blank or await owner input.

## Phase 3 — local market/SEO research

Goal: create a small evidence-backed launch, not a generic beauty post.

Research:
- Prague aesthetic/beauty positioning;
- Fresh Vibes website services;
- current local competitor wording/content;
- discoverability terms in CZ/RU/EN relevant to the real services;
- five relevant accounts for local network seeding.

Candidate follow score:
- Prague/local relevance;
- audience overlap;
- authenticity;
- recent activity;
- brand safety;
- no bot/follower-farm characteristics.

Produce exactly five candidates for approval. Do not treat following as guaranteed SEO improvement.

## Phase 4 — creative package

Create:

### Avatar
- square source master;
- visually legible at small circular crop;
- Fresh Vibes brand-consistent;
- no tiny unreadable text.

### Feed post #1
Purpose: introduce Fresh Vibes Beauty / core value proposition truthfully.

Package:
- final media;
- CZ/RU/EN copy variants if useful;
- selected caption;
- alt text when publishing route supports it;
- hashtags/keywords only where natural;
- no unsupported medical outcome claims.

### Story #1
Purpose: short introduction/CTA consistent with the feed post.

### Highlight #1
- title selected from actual profile strategy;
- cover prepared;
- created from approved Story content after Story exists.

Do not create fake before/after patient results.

## Phase 5 — one Telegram approval bundle

Before any external write, send the owner one structured launch bundle:

```
FRESH VIBES BEAUTY — INSTAGRAM DAY 1

PROFILE
- display name: ...
- bio: ...
- website: ...
- category/contact: ...

AVATAR
[preview]

FEED POST
[preview]
caption: ...

STORY
[preview]

HIGHLIGHT
title: ...
cover: ...

FOLLOW 5
1. @... — reason
2. @... — reason
3. @... — reason
4. @... — reason
5. @... — reason

External effects:
profile edit + avatar + 1 feed post + 1 Story + 1 Highlight + exactly 5 follows

[APPROVE ALL] [CHANGE] [REJECT]
```

Implementation may split profile/content/follow approvals if the existing approval engine requires separate effect hashes. What matters is that every exact external effect is owner-approved before execution.

Any changed creative/text/account/follow list invalidates the prior approval.

## Phase 6 — execution order

After approval:

1. verify account identity again;
2. apply profile fields;
3. verify profile readback;
4. set avatar;
5. verify avatar;
6. publish feed post;
7. verify feed post id/visible state;
8. publish Story;
9. verify Story;
10. create Highlight from that Story;
11. verify Highlight;
12. follow candidate 1 -> verify;
13. candidate 2 -> verify;
14. candidate 3 -> verify;
15. candidate 4 -> verify;
16. candidate 5 -> verify;
17. final full-profile readback.

If any step encounters auth/challenge/ambiguous UI, stop at that step and do not “make up” the remaining result.

## Phase 7 — final report

Owner receives:

```
Fresh Vibes Beauty Instagram Day-1
PROFILE VERIFIED: yes/no
AVATAR VERIFIED: yes/no
FEED POST VERIFIED: yes/no
STORY VERIFIED: yes/no
HIGHLIGHT VERIFIED: yes/no
FOLLOWS VERIFIED: 0..5 / 5
UNAUTHORIZED EXTRA EFFECTS: 0 required
CREDENTIAL LEAKS: 0 required
OWNER INTERVENTIONS: N
ROUTE: browser/API per action
EVIDENCE: ...
STATUS: PASS / PARTIAL / BLOCKED
```

## After Day-1

Do not immediately increase volume.

Next experiment should measure:
- reach;
- profile visits;
- website clicks;
- DMs/leads if available;
- bookings;
- attributable revenue where evidence exists.

Only actual outcomes may train/promote a growth skill.

The first meaningful 1.6 business milestone is not “Instagram filled”. It is the later `VERIFIED_REVENUE_LOOP_PASS`.
