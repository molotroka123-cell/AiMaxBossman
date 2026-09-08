# Higgsfield Browser Worker — Bossman Contract

## Why browser mode exists

The generator should support a browser execution route when an approved API/MCP integration is unavailable for the owner's account/workflow. This route uses the owner's already-authenticated browser session and normal product UI. It is not an anti-bot bypass and must never attempt to defeat CAPTCHA, access controls, quotas or account restrictions.

## State machine

`DISABLED -> STARTING -> AUTH_CHECK -> READY -> SUBMITTING -> WAITING_PROVIDER -> OUTPUT_READY -> COLLECTING -> VERIFYING -> COMPLETE`

Terminal/intervention states:

- `NEEDS_OWNER_AUTH`
- `HUMAN_CHALLENGE`
- `RATE_LIMITED`
- `UI_CHANGED`
- `POLICY_BLOCKED`
- `TIMEOUT`
- `FAILED`

A challenge/re-auth state stops the affected worker. No repeated clicking and no automated challenge solving.

## Browser profile rules

- profile must be explicitly configured by owner;
- never export cookies/localStorage/session tokens;
- no browser profile data in cloud prompts;
- one active generation lease per profile by default;
- the owner can disable the worker immediately;
- screenshots/DOM evidence must be sanitized before cloud use.

## Adapter interface

A provider-specific adapter should expose conceptually:

```python
class BrowserGenerationAdapter(Protocol):
    provider: str
    async def detect_state(self, page) -> BrowserProviderState: ...
    async def open_generator(self, page, request) -> None: ...
    async def fill_request(self, page, request) -> None: ...
    async def submit(self, page) -> SubmissionReceipt: ...
    async def observe(self, page, receipt) -> JobObservation: ...
    async def collect(self, page, observation, destination) -> ArtifactReceipt: ...
```

Selectors should prefer semantic roles, accessible names, stable labels and state evidence rather than brittle absolute CSS paths.

## Generation request

Required fields:

- `job_id`
- `mission_id`
- `media_kind`: image/video
- `prompt`
- optional negative/avoid guidance
- input/reference assets under approved workspace
- aspect ratio
- duration when relevant
- generation preset/model if owner policy allows
- output workspace
- deadline
- max attempts

Optional settings must be explicit. The worker must not carry settings silently from an unrelated previous job.

## Submission proof

Before declaring a job submitted, capture at least two independent observations when available:

- UI moves to generating state;
- generation/job card appears;
- submit control changes state;
- provider-visible job/status identity;
- new network/UI activity associated with generation.

A click alone is not proof.

## Polling

Use bounded adaptive polling. Example policy:

- initial observation after 2-5 seconds;
- then increasing interval up to a configured maximum;
- total deadline from job policy;
- no model calls merely to ask whether the UI changed when deterministic DOM/state inspection is enough.

## Result collection

Download only after output is observably ready. Files land first in a quarantine/output-staging directory. Validate:

- file exists;
- non-zero size;
- recognized media type;
- image/video dimensions where possible;
- video duration/decodability where possible;
- content hash;
- no HTML/error page saved as media.

Then move/copy into the approved media workspace and record evidence.

## Prompt and reference handling

The media prompt may be sent to the generator because that is the requested generation content. Do not include unrelated Bossman memory, API keys, private paths, user browser secrets or system prompts.

Reference images must be explicitly part of the media job and taken from an allowlisted workspace.

## UI change handling

When expected semantic controls cannot be located or the page state is inconsistent:

1. stop interactions;
2. classify `UI_CHANGED`;
3. capture safe screenshot;
4. capture sanitized list of roles/names near expected surface;
5. record adapter version;
6. create an OpenHands repair mission if allowed;
7. keep other providers/local generation running.

Do not make random clicks to rediscover the interface.

## Human challenge handling

Signals include CAPTCHA text/widget, explicit bot-check page, forced login, MFA, suspicious-login confirmation or account policy warning.

Action:

`HUMAN_CHALLENGE/NEEDS_OWNER_AUTH -> owner notification -> worker paused`.

The worker resumes only after the owner has completed the normal interaction and the adapter independently observes a valid READY state.

## Rate-limit and plan handling

If the service UI reports a cooldown, limit or unavailable capability, record it honestly and route to another allowed strategy. Do not alter plans, purchase credits or change subscription settings automatically.

## Local-vs-browser routing

Prefer local generation when measured quality is adequate and safe memory capacity is available. Prefer browser Higgsfield when its capability materially improves the requested scene or the local fleet cannot satisfy the requirement. Keep a fallback route to evergreen content for live continuity.

## Tests to build

Hermetic fixtures must cover:

- already authenticated READY page;
- login required;
- human challenge;
- missing submit control / UI drift;
- successful submit;
- no observable submission after click;
- provider failure message;
- rate limit;
- output ready;
- download failure;
- invalid downloaded media;
- timeout;
- selector repair fixture;
- secret redaction;
- duplicate submission prevention;
- worker restart with an in-flight receipt.

A real browser acceptance should be separate and run only against the owner's normal account/session.