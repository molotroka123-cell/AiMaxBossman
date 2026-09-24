# Bossman 1.6 — Business Growth / Revenue Engine

Branch: `feat/bossman-1.6-bossnet-foundation-20260925`

Status: **DESIGN + IMPLEMENTATION CONTRACT / OWNER LIVE ACCEPTANCE PENDING.**

This document extends the existing 1.6 BossNet foundation without creating a second Bossman, a second memory store, a second approvals system or a second agent runtime.

## Product goal

Bossman 1.5 proved/implemented the foundations for persistent agents, verified self-improvement, skill compilation, cost-aware routing, web research and Money MVP work.

Bossman 1.6 applies that machinery to the owner's real businesses.

Primary loop:

```
business objective
 -> research
 -> hypothesis
 -> creative / offer
 -> owner approval for consequential external actions
 -> publish / launch
 -> lead / booking / revenue observation
 -> attribution
 -> verifier
 -> keep / reject / scale
 -> verified lesson / skill
```

The optimization target is not views, followers or model activity. The preferred business metric is:

`verified gross profit per Kč spent / per owner intervention`.

Revenue, booked revenue, qualified leads, clicks and views remain separate measures. A viral post with no attributable business outcome must not be promoted over a smaller post that creates verified profitable bookings.

## Reuse, not rewrite

1. **Scientific Self-Improvement** -> business hypothesis/experiment loop.
2. **Persistent Agent Society** -> Market, Creative, Social, CRM, Sales, Finance, Compliance and Verifier roles.
3. **Skill Compiler** -> only verified winning growth workflows become reusable skills.
4. **Personal Operating Graph** -> businesses, offers, campaigns, creatives, accounts, leads, bookings, revenue and experiment provenance.
5. **Autonomous Resource Manager / Economy Orchestrator** -> local/free first, paid model only when justified.
6. **web_research** -> market/competitor research with source provenance.
7. **Browser / Computer Operator** -> interactive sites and account surfaces where a supported API is unavailable.
8. **Studio** -> image/video/audio production.
9. **Social Farm** -> social account/session/provider foundation.
10. **Telegram approvals** -> owner control for external writes.
11. **Existing STOP / never-ask-allowed / egress guards** -> unchanged.

## Business namespaces

Every observation and action belongs to a business namespace. Initial namespaces:

- `fresh_vibes_beauty`
- `fresh_vibes_dental`
- `swapme`

Cross-business memory may contain generic verified marketing lessons, but customer data, credentials, account sessions and private commercial data stay scoped to their business.

## Core business objects

Minimum graph entities:

- `Business`
- `Offer`
- `Service`
- `Audience`
- `Competitor`
- `Channel`
- `SocialAccount`
- `Campaign`
- `Creative`
- `ContentBatch`
- `Experiment`
- `Lead`
- `Booking`
- `RevenueEvent`
- `CostEvent`
- `AttributionEvent`
- `GrowthSkillCandidate`

Every metric must carry provenance and time. Unknown revenue/cost is UNKNOWN, never zero.

## Business agent roles

### Market Agent
Uses allowed web research and public sources to track competitors, offers, prices, new services, local demand signals and content patterns.

### Offer Agent
Turns evidence into bounded testable hypotheses. No fabricated discounts, medical claims or guarantees.

### Creative Agent
Produces content briefs and uses the existing image/video stack.

### Social Operator
Owns draft -> approval -> publish -> verify for connected channels. It cannot silently publish owner-facing commercial content.

### CRM / Conversion Agent
Maps inbound leads to source/campaign when possible, records funnel state and proposes next actions. Medical advice and clinical promises require a medical/compliance boundary.

### Finance Agent
Tracks ad spend, content production cost, attributable revenue and gross-margin estimates. It does not invent missing costs.

### Compliance Agent
Checks brand rules, medical-claim restrictions, privacy and platform-specific constraints before owner approval.

### Verifier
Independent from the creator. Checks actual external state, not model statements.

## Web research readiness

The repository already contains local-agent tools:

- `web.search`
- `web.open`
- `web.find`
- `web.cite`

They are gated by `BOSSMAN_WEB_RESEARCH_ENABLED=1` and `BOSSMAN_OSIRIS_ENABLED=1`.

General-web search is expected to use a configured SearXNG instance or an approved search provider. This is **implemented code but not OWNER LIVE PASS on the target AI Max until the owner run proves a real search/open/cite cycle on the installed build.**

Business 1.6 must never label market research LIVE if only fixtures/unit tests were used.

## Fresh Vibes Beauty pilot

First production pilot:

`Fresh Vibes Beauty Instagram Day 1`

Deliverables:
- connect one owner-controlled empty Fresh Vibes Beauty Instagram account;
- complete truthful profile fields;
- create and set one avatar;
- create and publish one feed post;
- create and publish one Story;
- create one Highlight from approved Story content;
- research and propose exactly five relevant accounts to follow;
- follow only the owner-approved five;
- verify all resulting external states;
- store the resulting evidence and content provenance.

This pilot is not accepted as a revenue loop merely because publishing succeeds.

## Revenue experiment gate

The next stage after account bootstrap:

```
RESEARCH_READY
 -> OFFER_READY
 -> CREATIVE_VERIFIED
 -> OWNER_APPROVED
 -> PUBLISHED_VERIFIED
 -> LEADS_OBSERVED
 -> BOOKINGS_OBSERVED
 -> REVENUE_ATTRIBUTED
 -> MARGIN_MEASURED
 -> EXPERIMENT_VERIFIED
 -> LESSON_CANDIDATE
```

Only after the final verification may the system promote a growth lesson.

## 1.6 business acceptance ladder

- `BUSINESS_GRAPH_READY`
- `WEB_RESEARCH_OWNER_LIVE_PASS`
- `SOCIAL_ACCOUNT_OWNER_LIVE_PASS`
- `TELEGRAM_CONTENT_APPROVAL_PASS`
- `PUBLISH_AND_VERIFY_PASS`
- `LEAD_ATTRIBUTION_PASS`
- `BOOKING_ATTRIBUTION_PASS`
- `VERIFIED_REVENUE_LOOP_PASS`

`VERIFIED_REVENUE_LOOP_PASS` requires a real attributable business outcome. A generated report, scheduled post, click or lead alone does not qualify.

## Safety and owner authority

Never weaken:
- STOP;
- exact account identity checks;
- single-use approval decisions;
- secret redaction;
- LOCAL_ONLY handling;
- egress guard;
- no automatic account signup;
- no CAPTCHA/2FA bypass;
- no silent payment/ad-budget change;
- no unapproved mass outreach or follow/unfollow farming;
- no medical outcome guarantees.

Credentials are infrastructure secrets, not model context.

## CEO surface

Target owner summary:

```
Fresh Vibes Beauty
Yesterday: 18 leads / 7 bookings / 42,000 Kč booked revenue
Attributed spend: 8,420 Kč
Top experiment: Rejuran RU creative B
Proposal: +800 Kč test budget
Evidence: linked
[APPROVE] [CHANGE] [REJECT]
```

The owner should not need GitHub or Claude Code for routine operation.
