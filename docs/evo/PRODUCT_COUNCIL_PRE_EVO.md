# Bossman Product Council — Model Freshness, UX and Web Designer Baseline

Date: 2026-09-20

This is the decision record for the next owner-hardware phase. It does not authorize autonomous production self-modification.

## Council roles

The product should be reviewed from six independent roles:

1. CEO advocate — can a non-engineer understand what is happening?
2. UX reviewer — is the interface responsive, calm, obvious and recoverable?
3. Agent/runtime engineer — can the system actually complete the action behind every control?
4. Local-model specialist — is the current fleet still the best fit for the exact hardware?
5. Security/privacy reviewer — does convenience ever bypass LOCAL_ONLY/ASK/approval boundaries?
6. Red-team tester — what breaks when the user double-clicks, changes pages, restarts, loses a provider or gives an ambiguous request?

No single role may promote its own change directly to stable.

## Model freshness: recommendation-only autopilot

Bossman should not hard-code today's model names forever.

Create/maintain a model discovery registry with:

- model/provider/repository id;
- release/update date;
- license;
- architecture/active parameters;
- modalities;
- tool/structured-output support;
- context;
- available quantizations;
- known Strix Halo runtime support;
- artifact hash;
- benchmark source/evidence date;
- local measured results;
- status: DISCOVERED / CANDIDATE / QUALIFIED / DEFAULT / REJECTED / RETIRED.

### Freshness loop

On a scheduled cadence:

DISCOVER
→ verify official release/model card
→ check license
→ check whether a practical quant/runtime exists for Ryzen AI Max+ 395 / Radeon 8060S / 128 GB
→ add as CANDIDATE
→ benchmark in isolation
→ run owner-specific holdout tasks
→ compare against current role incumbent
→ independent verifier
→ recommend promotion.

For PRE-EVO the last step is only:

RECOMMEND TO OWNER.

No silent download of enormous weights, no silent replacement of the default fleet, and no stable self-modification.

### Promotion score

Do not rank on a single leaderboard.

Per role compare:

- real task completion;
- correctness;
- tool/schema reliability;
- owner interventions;
- latency/TTFT/decode;
- context/prefill behavior;
- peak unified memory;
- system responsiveness;
- crash/soak stability;
- license/privacy;
- verifier corrections.

A newer model only replaces the incumbent if it materially improves the role without unacceptable regressions.

## UX council verdict

The current Command Center has accumulated strong safety/recovery logic, but the owner experience must be tested against a modern-agent standard rather than a developer dashboard standard.

Target UX:

### Home
Show only:
- Ask Bossman / New Mission;
- current mission and progress;
- recent results;
- approvals needing owner attention;
- health summary: READY or N THINGS NEED ATTENTION;
- cost/budget at a glance.

Technical logs, provider internals, raw events and benchmark details belong behind Advanced/Diagnostics.

### Global command surface
A persistent command box should accept natural language from every main screen.

The owner should not have to decide which internal app/tool is needed before asking for a task.

### Mission view
One clear timeline:

UNDERSTAND
→ PLAN
→ WORKING
→ WAITING FOR YOU
→ VERIFYING
→ DONE / NEEDS ATTENTION.

Each stage should expand to evidence on demand.

### Responsive feedback
For every action:
- immediate pressed/loading state;
- optimistic UI only for reversible local UI state;
- server-confirmed success for effects;
- no duplicate submit;
- cancellable long action where safe;
- progress/status within ~100 ms of click even if backend work takes minutes;
- preserve draft/navigation state.

### Errors
Never lead with a stack trace.

Show:
WHAT HAPPENED
WHY IT MATTERS
WHAT BOSSMAN WILL DO / WHAT OWNER CAN DO.

Advanced details remain expandable.

### Navigation
Preserve:
- current project;
- unsaved draft;
- selected element;
- scroll/viewport where practical;
- running mission.

Back/forward/reload/restart should not feel like entering a different product.

### Performance budget
Instrument and report:
- first meaningful paint;
- route transition;
- click→visible feedback;
- API latency;
- long-task first progress event;
- dropped/stalled UI states.

Do not fake responsiveness by reporting success before the effect is verified.

## UX meeting acceptance

Before freeze, run a council pass over every main screen and produce:

KEEP
SIMPLIFY
HIDE_IN_ADVANCED
REWORK

for every major panel/control.

No redesign is merged only because it “looks modern”; it must preserve capability, accessibility and owner completion rate.

## Web Designer baseline

The deterministic template generator remains a safe fallback and test fixture.

The **AI site-build path**, when a model is available, should use the premium creative brief in:

`command-center/bcc/web_designer_creative_brief.py`

The seven stages are:

1. creative direction;
2. visual/3D world;
3. premium UX architecture;
4. cinematic motion;
5. micro-interactions;
6. production engineering;
7. final studio polish.

Important: do not force 3D or animation where it hurts performance, accessibility or the brand. The brief says premium and intentional, not “maximum effects everywhere”.

The generated result must still pass:
- responsive/mobile;
- accessibility;
- reduced-motion behavior;
- keyboard navigation;
- performance budget;
- no console errors;
- no dead controls;
- no fake forms/CTAs unless clearly marked;
- security sandbox for preview;
- version/rollback;
- owner-visible preview before publish.

## EVO 1.0 handoff

EVO may later automate the discovery/benchmark/recommendation loop.

Stable production remains:

stable
→ isolated candidate
→ benchmark
→ verifier/red-team
→ owner-visible recommendation
→ OWNER APPROVAL
→ promotion
→ monitoring
→ rollback.
