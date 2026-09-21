# Owner Hardware Hotspots — predicted failures and immediate-fix playbook

This file is intentionally written **before** the owner's physical Bossman run.

Purpose: predict the most likely real-machine failure points so that, if one occurs, the engineering agent can move directly from evidence → reproduction → regression → fix → push instead of starting another broad audit.

Canonical branch: `release/bossman-owner`.

## Rule of engagement

A hotspot is a hypothesis, not a defect.

Do NOT preemptively weaken behavior or rewrite working code because this document predicts a failure.

When a hotspot is observed:

1. capture exact owner-visible symptom;
2. capture app/version, current TESTED_SHA, model/provider, task id and timestamps;
3. preserve relevant logs/screenshots/state without secrets;
4. reproduce on test data;
5. add a failing regression at the highest practical product layer;
6. fix the root cause;
7. run the focused regression plus neighboring contracts;
8. commit/push to `release/bossman-owner`;
9. rebuild the installed artifact when the fix affects shipped product;
10. rerun the failed HW case plus one adjacent journey.

Never force-push. Never turn a real failure into PASS by weakening an assertion.

## H01 — First boot / installed package differs from source checkout

**Probability:** very high.

Likely symptom:
- UI loads but one page/button/asset fails;
- FFmpeg/browser/model path exists in dev checkout but not installed build;
- Python import/resource path accidentally points at repository.

Hot fix trigger:
Any feature works from source but not from the clean installed artifact.

Immediate response:
- record resolved executable/module/resource paths;
- reproduce outside repository;
- remove implicit PYTHONPATH/current-working-directory dependency;
- add installed-artifact regression;
- rebuild Windows artifact.

PASS only when the installed artifact works outside the repo.

## H02 — Local runtime/model compatibility on Strix Halo

**Probability:** very high.

Likely symptom:
- model loads in one runtime but fails in another;
- ROCm/Vulkan backend mismatch;
- unsupported quant/projector;
- OOM despite nominal fit;
- first load stalls;
- context size causes sudden memory pressure.

Immediate response:
- record runtime/version/backend/quant/context and peak unified memory;
- do not label the model itself broken;
- test alternate supported backend/quant;
- update hardware profile/router eligibility;
- keep a known-good fallback model resident.

A model that is unstable becomes NOT_SUITABLE_FOR_THIS_HARDWARE_PROFILE, not a global product failure, unless Bossman cannot fall back.

## H03 — Router chooses the clever model instead of the practical model

**Probability:** high.

Likely symptom:
- heavy model used for trivial work;
- GUI task goes to coder;
- image/video model loaded unnecessarily;
- local render is evicted to answer a cheap background task.

Immediate response:
- capture route decision and reason;
- compare expected task class;
- fix capability/cost/resource scoring, not the task prompt;
- add same-task routing regression;
- verify fallback order.

## H04 — Computer Operator target drift

**Probability:** very high.

Likely symptom:
- correct button identified but UI moves before click;
- scaling/DPI changes coordinates;
- modal steals focus;
- browser zoom/window position differs;
- coordinate fallback clicks old location.

Immediate response:
- preserve before/probe/after observations;
- require fresh effect-boundary observation;
- invalidate stale target;
- prefer semantic/accessibility resolution;
- coordinate fallback only after named target resolution;
- add regression for moved target / wrong foreground / stale screenshot.

Never “fix” by lowering confidence globally.

## H05 — Computer Operator loops without making progress

**Probability:** high.

Likely symptom:
- observe→click→same screen repeated;
- planner keeps trying synonymous actions;
- approval repeatedly requested;
- task burns steps without state change.

Immediate response:
- compare state fingerprints and last verified effects;
- trigger bounded re-plan/escalation;
- preserve loop reason in owner-visible status;
- add no-progress regression.

## H06 — Browser download is not the file Bossman thinks it is

**Probability:** high.

Likely symptom:
- HTML login/error page saved as PDF;
- browser download still partial;
- duplicate filename selects stale version;
- government site redirects to another document.

Immediate response:
- verify MIME + magic bytes + size + final URL;
- hash artifact;
- reopen/parse downloaded document;
- bind provenance to final URL and retrieval time;
- reject HTML masquerading as PDF.

This is especially hot for HW-10 MVČR.

## H07 — Real website defeats deterministic browser assumptions

**Probability:** very high for MVČR.

Likely symptom:
- cookie dialog;
- Czech/English route differs;
- dynamically generated page;
- CAPTCHA/BankID;
- PDF link moved;
- form cannot actually be submitted electronically.

Immediate response:
- re-observe rather than use stale selector;
- identify official domain/source;
- hand off CAPTCHA/identity checkpoint;
- never fake electronic submission;
- update task plan from current official process.

Do not hard-code a brittle selector merely to pass once.

## H08 — PDF/form filling breaks layout or fields

**Probability:** high.

Likely symptom:
- fields visually blank despite values;
- Czech characters corrupt;
- flattened form loses data;
- text overflows;
- saved file differs from preview.

Immediate response:
- preserve untouched original;
- write working copy;
- reopen with an independent parser/render;
- compare intended field values;
- generate page previews for owner review;
- keep unresolved fields explicit.

## H09 — Context works until restart

**Probability:** medium-high.

Likely symptom:
- document is searchable before restart but absent afterward;
- wrong project context appears;
- same bytes in two projects deduplicate incorrectly;
- deletion leaves stale retrieval chunks.

Immediate response:
- record project/document/chunk IDs before restart;
- verify durable index and namespace;
- run A/B project-isolation regression;
- test delete→search absence;
- never repair by broadening search across projects.

## H10 — Approval is technically valid but bound to the wrong current state

**Probability:** medium-high.

Likely symptom:
- approval arrives after page/document changed;
- callback replay;
- two devices approve simultaneously;
- old approval applies to changed command/file revision.

Immediate response:
- bind approval to task + operation + payload digest/revision + actor;
- consume atomically;
- reject stale/replayed approvals;
- reconcile uncertain external effect before retry.

## H11 — Telegram real-world callback/network behavior differs from contract tests

**Probability:** high.

Likely symptom:
- delayed callback;
- duplicate update;
- webhook/polling reconnect;
- wrong chat/account;
- Bossman restart while waiting.

Immediate response:
- persist waiting approval before transport;
- dedupe by provider update id + approval identity;
- validate owner/chat identity;
- resume exactly once;
- never put secrets in callback logs.

## H12 — Image Studio product edit works, generation backend does not

**Probability:** high.

Likely symptom:
- native edit passes but selected generation model/provider cannot initialize;
- wrong model format;
- memory contention;
- output saved outside project.

Immediate response:
- separate editor status from generation-provider status;
- fall back to another eligible image engine;
- persist/import final artifact through Bossman;
- record memory and latency;
- do not downgrade native editor PASS because one generator is unavailable.

## H13 — Video model fits alone but not inside a running Bossman fleet

**Probability:** very high.

Likely symptom:
- LTX/Wan loads alone but OOMs with resident LLM/vision worker;
- Windows starts paging heavily;
- render makes desktop unusable;
- audio/video dependency mismatch.

Immediate response:
- measure total resident memory and page pressure;
- suspend/unload nonessential local models according to router policy;
- optionally route PUBLIC background work to allowed cloud;
- preserve PRIVATE work locally;
- verify render via ffprobe/full decode.

This is a router/resource-governor problem, not just a video-model problem.

## H14 — Structured output passes model benchmark but fails a real tool schema

**Probability:** high.

Likely symptom:
- correct tool but wrong enum/type;
- missing required field;
- hallucinated field;
- JSON wrapped in prose;
- retry changes intended operation.

Immediate response:
- enforce schema/constrained decoding where supported;
- validate before effect;
- return structured validation error to model;
- bounded correction retry;
- never coerce dangerous malformed arguments silently.

## H15 — Free cloud fallback leaks context

**Probability:** high-impact.

Likely symptom:
- router sends whole conversation/project to free endpoint;
- screenshot contains private data;
- public coding task carries owner memory unnecessarily.

Immediate response:
- stop dispatch;
- classify payload;
- minimize context;
- enforce LOCAL_ONLY/PRIVATE at gateway boundary;
- add negative regression proving the provider adapter receives no protected fields.

Treat actual private-data transmission as P0.

## H16 — Free provider fails and Bossman silently spends money

**Probability:** medium-high.

Likely symptom:
- 429/timeout on free model immediately calls paid model;
- retry storm consumes budget;
- premium verifier receives task without approval.

Immediate response:
- freeze paid dispatch;
- verify budget/fallback policy;
- require explicit allowed escalation;
- dedupe retries;
- add cost ledger regression.

## H17 — Provider/model catalog drift

**Probability:** high over time.

Likely symptom:
- model ID changed;
- free endpoint removed;
- price/context/tool support changed;
- provider responds with incompatible API schema.

Immediate response:
- health-check provider catalog;
- mark route unavailable;
- fall back within same allowed cost/privacy class;
- never treat documentation snapshot as permanent runtime truth.

## H18 — Bossman reports completion from intent rather than effect

**Probability:** highest-severity cross-cutting hotspot.

Likely symptom:
- “downloaded” but file absent;
- “sent” but no receipt;
- “edited” but bytes unchanged;
- “submitted” after only clicking Send;
- “rendered” but corrupt artifact.

Immediate response:
Every consequential task needs independent postcondition evidence.

If evidence is absent:
- status is UNKNOWN/NOT_VERIFIED, never SUCCESS;
- reconcile actual state;
- retry only when duplicate effect is safe.

## H19 — Restart occurs at the worst possible boundary

**Probability:** high.

Test intentionally during:
- model turn;
- browser download;
- file write;
- WAIT_APPROVAL;
- external send;
- render;
- cloud call.

Expected:
- durable state;
- no duplicate irreversible effect;
- uncertain external effect enters reconciliation;
- owner receives truthful status.

## H20 — UX looks healthy but backend call is broken

**Probability:** high.

Likely symptom:
- button renders and toast says success;
- network shows 404/422/500;
- spinner never resolves;
- UI uses stale API schema.

Immediate response:
- capture console + network + backend correlation id;
- fix UI/API contract;
- add browser-level regression;
- verify visible result, not just HTTP 200.

## H21 — Long autonomy exhausts approval/budget/step limits

**Probability:** high.

Likely symptom:
- 4-hour mission stops for an avoidable confirmation;
- approval fatigue;
- context grows until model quality collapses;
- worker fleet accumulates stale tasks.

Immediate response:
- use scoped leases only within explicit authority;
- checkpoint/summarize context;
- prune completed workers;
- expose remaining budget/steps;
- fail honestly rather than self-expand authority.

## H22 — A/B test is unfair

**Probability:** medium but important.

Likely symptom:
- Bossman receives tools/context the bare model could not access;
- different quant/context/settings;
- tasks accidentally tuned to Bossman.

Immediate response:
- same underlying model/quant/context where possible;
- same task facts;
- clearly document tool availability differences;
- use unseen holdout tasks;
- report completion and owner intervention, not a vanity score.

## H23 — MVČR-specific likely break sequence

Most likely first real complex-task failure chain:

1. Bossman finds a correct official page.
2. It encounters navigation/cookie/language variance.
3. It downloads a document but must prove it is current and really PDF.
4. It lacks one or more verified residence dates.
5. It must ask the owner rather than infer them.
6. Form filling exposes PDF encoding/layout issues.
7. Submission route may require a human identity step or may not support online submission.
8. Bossman must stop at WAIT_APPROVAL with a reviewable package.

A PASS is a correct prepared package and truthful next action — not necessarily electronic submission.

## Hot severity

### P0 — fix immediately, stop owner run
- private data sent to unauthorized cloud;
- approval bypass/replay causing unauthorized effect;
- payment/submission/destructive external effect without required approval;
- fabricated success after consequential action;
- cross-project private-context leak;
- secret exposure.

### P1 — fix before continuing that feature
- installed product cannot invoke advertised feature;
- computer-use repeatedly targets wrong UI;
- restart loses task state;
- corrupt media/document artifact reported as success;
- UI/API mismatch on normal owner journey;
- router cannot recover from unavailable model/provider.

### P2 — record and continue when safe
- cosmetic UI issue;
- non-blocking latency;
- suboptimal model routing with correct result;
- optional candidate model unavailable while fallback works.

## Owner-run engineering handoff

When the owner reports a failure, the engineering agent should receive:

- current remote SHA;
- installed artifact hash;
- HW case id;
- exact owner instruction;
- expected result;
- actual result;
- screenshot/log/event ids if available;
- model/provider/runtime;
- whether any external effect may already have happened.

Then execute:

REPRODUCE → REGRESSION → FIX → VERIFY → PUSH → BUILD → RERUN FAILED CASE.

Do not launch a broad architecture rewrite during owner acceptance.
