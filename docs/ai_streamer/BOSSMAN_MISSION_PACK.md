# Bossman AI Streamer — Mission Pack

## Mission 1 — Generate one Higgsfield clip through browser

Goal: create one approved 9:16 video asset from a prepared shot specification using the owner's authenticated browser session.

Acceptance:

- browser adapter observes authenticated READY state;
- exactly one submission for one job id;
- submission has observable evidence;
- bounded wait terminates;
- output downloaded into approved workspace;
- media validation passes;
- SHA-256 and QA receipt recorded;
- no cookies/tokens in evidence;
- challenge/auth state stops with `NEEDS_OWNER`, never bypasses it.

## Mission 2 — Produce a 60-second social video

Pipeline:

`idea -> 3 hooks -> script -> claim check -> shot list -> media generation -> continuity QA -> timeline -> playable preview -> caption`.

Acceptance:

- at least two strategies considered for generation route;
- local route rejected/selected using capability + resource evidence;
- failed media gets targeted repair, maximum configured attempts;
- finished preview is playable and all used assets are traceable to receipts;
- no publish action unless separately authorized.

## Mission 3 — Fill a 60-minute stream buffer

Produce approved segments until `buffer_minutes_ready >= 60`.

Acceptance:

- media generation and broadcast queues are decoupled;
- provider failure does not stop buffer service;
- on Higgsfield challenge, local/evergreen strategy is considered;
- no generation job retries indefinitely;
- owner alerts are coalesced;
- resource pressure does not trigger unsafe local model load.

## Mission 4 — Six-hour unattended media factory soak

Run the content factory for six hours against a fixture/live-safe environment.

Measure:

- generated jobs;
- accepted outputs;
- retry rate;
- owner interventions;
- browser UI drift incidents;
- buffer health;
- tokens/cost;
- local RAM peak;
- model/provider fallback count;
- deadlock count.

Mandatory: `deadlock_count = 0`. Human challenge should pause only the affected provider lane.

## Mission 5 — OpenHands selector repair

Input: deterministic `UI_CHANGED` fixture plus sanitized page-state evidence.

OpenHands must:

1. reproduce failure;
2. add/update test;
3. patch semantic selector/state adapter;
4. run focused regression;
5. return diff/evidence.

Forbidden: cookie access, random coordinate clicking, disabling challenge detection, direct deploy/push authority.

## Mission 6 — Local-vs-cloud media routing

Given a set of generation requests and synthetic resource/provider observations, prove the router can choose:

- deterministic/local cheap path for simple work;
- local media model when capability + safe memory fit;
- browser Higgsfield when local capability is insufficient;
- alternate provider on failure;
- evergreen buffer when no safe generation route exists.

The test must include an OOM-prevention negative control.

## Mission 7 — Persona continuity episode

Generate multiple scenes containing the same virtual creator/persona.

Continuity QA checks:

- identity/reference adherence;
- clothing/props when locked;
- location continuity;
- narrative state;
- voice/style constraints;
- forbidden changes.

Conflicts are rejected or marked contested; they do not silently rewrite persistent persona canon.

## Mission 8 — Reactive stream segment

Input: sanitized live-chat/trend summary.

The signal is observation only. Bossman proposes a segment and then runs normal policy/generation/QA gates. Audience messages cannot directly invoke browser, shell, publishing or payment actions.

## Mission 9 — Generator outage drill

Inject:

- timeout;
- rate limit;
- login expiry;
- human challenge;
- UI drift;
- invalid downloaded media.

Expected strategy changes:

`retry once when transient -> alternate route -> local/other generator -> evergreen buffer -> compact owner alert`.

No blind retry loop.

## Mission 10 — Nightly content batch

While owner is away, prepare a draft content queue, not uncontrolled publishing.

Target:

- 10 concepts;
- cheap hook/script generation;
- top concepts selected;
- media jobs generated within configured limits;
- QA and draft timelines;
- one morning summary: completed / rejected / needs owner / spend / buffer minutes.

## Definition of repository-ready

Repository-side implementation is ready when deterministic tests prove contracts/state transitions, challenge behavior, evidence handling, bounded recovery, resource-aware route rejection, OpenHands repair workflow and media validation.

## Definition of live-ready

Requires owner-machine evidence for:

- actual authenticated Higgsfield browser flow;
- current UI selectors/state model;
- real output download;
- local image/video model resource profile;
- Video Studio/stream output integration;
- six-hour soak.

Do not convert repository tests into claims of live acceptance.