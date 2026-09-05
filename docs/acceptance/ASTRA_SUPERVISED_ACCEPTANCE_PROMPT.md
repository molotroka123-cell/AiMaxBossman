CRITICAL OPERATING MODE:

ASTRA = SUPERVISOR
CODEX = LOCAL EXECUTOR
BOSSMAN = SYSTEM UNDER TEST
GLM 5.3 FLASH = DEFAULT CHEAP TEST BRAIN
FREE CAPABLE MODEL = CAPACITY/BUDGET FALLBACK

Astra must supervise, diagnose and correct the run continuously.
Codex must checkpoint + push every coherent VERIFIED repair.
Never lose verified work because a model/session capacity limit was reached.

# Bossman acceptance mission — model, budget and supervisor policy

This is the consolidated prompt for sections 5A–5J and 6A–6L.
Apply it alongside the acceptance mission's scope and Mission Contract.
These instructions define required behavior; this document is not evidence
that the runtime implements it or that acceptance has passed.

## Budget policy (replaces earlier acceptance budget blocks)

```text
DEFAULT_CLOUD_TEST_MODEL = GLM 5.3 FLASH

MAX_OPENROUTER_SPEND_USD = 3.00

TARGET_GLM53_FLASH_SHARE_OF_PAID_CALLS >= 80%

AFTER_PAID_BUDGET_EXHAUSTION:
    PAID_CALLS = DENY
    FREE_MODEL_FAILOVER = ENABLED

FREE_MODEL_SELECTION =
    BEST_CURRENTLY_AVAILABLE_FREE_MODEL
    THAT_SUPPORTS_REQUIRED_CAPABILITIES

MODEL_SWITCH:
    PRESERVE mission_id
    PRESERVE verified state
    PRESERVE permissions
    PRESERVE budget
    REBUILD minimal context
    NEVER replay verified side effects
```

Policy precedence:

- Sections 5G–5I override the paid escalation ladder in 5B for availability,
  capacity, credit, quota and budget failures.
- Paid quality escalation requires evidence of a MODEL bottleneck and remaining
  budget. The $3.00 ceiling includes retries and optional comparisons; switching
  models, providers, processes or sessions never resets mission spend.
- Reserve a bounded worst-case request cost before dispatch, including in-flight
  calls. Reconcile actual usage afterward. Unknown pricing or uncertain remaining
  spend must not authorize a paid call. Persist the exhausted-budget latch.
- A free candidate must have zero applicable request charges, not just a free
  name or zero input price. Account/provider restrictions must also permit it.
- Use the effective provider context limit and leave room for output and tools.
- Record missing or insufficient measurements as null / NOT_MEASURED, never PASS
  or a fabricated zero. Preserve numerators, denominators and evidence references.
- Treat provider metadata as advertised capability until verified at runtime.
  Preserve canonical model IDs in evidence, but rediscover routing IDs from the
  current registry/catalog; do not embed a fixed provider ID in Bossman core.

```text
==================================================
5A. DEFAULT TEST MODEL — GLM 5.3 FLASH
==================================================

For routine live acceptance runs through OpenRouter,
use the CHEAP GLM 5.3 Flash model as the DEFAULT test brain.

Preferred configured model:

GLM 5.3 Flash

IMPORTANT:
The exact OpenRouter model ID must be discovered from current provider/model
configuration or current OpenRouter availability.

Do NOT hard-code a stale model identifier into Bossman core.

Use GLM 5.3 Flash for:

- read-only repository tasks
- structured output tests
- tool-calling tests
- file operations
- approval-flow tests
- Organization mission smoke tests
- Fleet routing smoke tests
- memory/context continuation tests
- restart/resume tests
- browser navigation tests
- routine bug reproduction
- routine code fixes
- benchmark repetition

Reason:

GLM 5.3 Flash is the COST-DEFAULT model for this acceptance mission.

The purpose is to test Bossman,
not to spend money proving that an expensive frontier model can solve easy tasks.

==================================================
5B. MODEL ESCALATION POLICY
==================================================

Default escalation ladder:

DETERMINISTIC
→ GLM 5.3 FLASH
→ STRONGER CONFIGURED CLOUD MODEL
→ FRONTIER MODEL ONLY IF NECESSARY

Do NOT escalate simply because:

- one answer was imperfect
- a tool failed
- the UI has a bug
- a deterministic test failed
- repository inspection is required

First determine whether the failure belongs to:

MODEL
BOSSMAN ROUTING
TOOLING
SCHEMA
EXECUTOR
VERIFIER
UI
INFRASTRUCTURE

Only escalate the MODEL when evidence shows the cheaper model is the bottleneck.

==================================================
5C. GLM 5.3 FLASH ACCEPTANCE METRICS
==================================================

Create a dedicated result block:

GLM53_FLASH_MODEL_ID=
GLM53_FLASH_CALLS=
GLM53_FLASH_MISSIONS=
GLM53_FLASH_VERIFIED_MISSIONS=

GLM53_FLASH_TOOL_CALL_SUCCESS_RATE=
GLM53_FLASH_TOOL_SELECTION_ACCURACY=
GLM53_FLASH_ARGUMENT_SCHEMA_ACCURACY=
GLM53_FLASH_STRUCTURED_OUTPUT_SUCCESS_RATE=

GLM53_FLASH_FALSE_SUCCESS_COUNT=
GLM53_FLASH_MALFORMED_TOOL_CALLS=
GLM53_FLASH_SCHEMA_FAILURES=
GLM53_FLASH_RETRIES=

GLM53_FLASH_INPUT_TOKENS=
GLM53_FLASH_OUTPUT_TOKENS=
GLM53_FLASH_COST_USD=
GLM53_FLASH_COST_PER_VERIFIED_SUCCESS=

GLM53_FLASH_MEDIAN_LATENCY=
GLM53_FLASH_HUMAN_INTERRUPTS=

==================================================
5D. CHEAP-MODEL STRESS FIRST
==================================================

Before using a stronger model,
run the full cheap-model acceptance subset on GLM 5.3 Flash:

A. read-only mission
B. safe file mutation
C. structured output
D. tool call
E. failed tool / false-success trap
F. approval flow
G. restart/resume
H. Organization delegation
I. Fleet placement
J. memory continuation
K. small real code fix

If GLM 5.3 Flash succeeds:

DO NOT rerun the same mission on a more expensive model unless needed for comparison.

If it fails:

record exact failure class before escalation.

==================================================
5E. PAID MODEL COST PROTECTION
==================================================

Prefer GLM 5.3 Flash for the majority of paid calls.

Suggested target:

>= 80% of cloud test calls
should use GLM 5.3 Flash

unless its current availability or capabilities make this impossible.

Frontier model usage should be exceptional.

Every stronger-model escalation must record:

ESCALATION_REASON=
FAILED_MODEL=
FAILED_TASK=
FAILURE_CLASS=
WHY_STRONGER_MODEL_REQUIRED=
ADDITIONAL_COST_USD=
RESULT=

Unexplained escalation = benchmark penalty.

==================================================
5F. SAME-MISSION MODEL COMPARISON
==================================================

After primary acceptance is complete,
optionally compare GLM 5.3 Flash with ONE stronger configured model
using the SAME small mission set.

Do not change:

prompt
tools
permissions
Mission Contract
verification criteria
Fleet placement logic
benchmark weights

Compare:

VERIFIED_SUCCESS_RATE
FALSE_SUCCESS_RATE
TOOL_SELECTION_ACCURACY
ARGUMENT_SCHEMA_ACCURACY
TOKENS
COST
LATENCY
RETRIES
HUMAN_INTERRUPTS

Primary optimization metric:

COST_PER_VERIFIED_SUCCESS

Secondary:

VERIFIED_SUCCESS_PER_1M_TOKENS

The more expensive model wins only if the quality/reliability improvement
justifies the additional cost.

==================================================
5G. CAPACITY / CREDIT EXHAUSTION FAILOVER
==================================================

GLM 5.3 Flash remains the preferred paid test model.

However, model/provider limits MUST NOT unnecessarily terminate
the acceptance mission.

Detect explicitly:

CREDIT_EXHAUSTED
RATE_LIMITED
MODEL_CAPACITY_EXHAUSTED
MODEL_UNAVAILABLE
PROVIDER_UNAVAILABLE
DAILY_LIMIT_REACHED
CONTEXT_LIMIT_REACHED

When GLM 5.3 Flash becomes unavailable because of capacity,
credits, quota, or provider limits:

DO NOT repeatedly retry it.

Immediately:

1. persist current mission checkpoint
2. persist completed VERIFIED work
3. persist pending work
4. record provider/model failure
5. query the existing Model Broker for eligible alternatives
6. prefer the strongest currently available FREE model
7. resume the SAME mission_id
8. continue from the first safe unfinished step

Do NOT create a new mission merely because the model changed.

MODEL_CHANGE != MISSION_RESTART

==================================================
5H. FREE MODEL FALLBACK
==================================================

Fallback order:

DETERMINISTIC
→ GLM 5.3 FLASH
→ STRONG FREE AVAILABLE MODEL
→ OTHER FREE AVAILABLE MODEL
→ PAUSE / OWNER ATTENTION

The exact free model MUST NOT be hard-coded.

Discover it dynamically through the existing provider/model registry.

Eligibility requires:

MODEL_AVAILABLE
AND COST_USD == 0
AND REQUIRED_CAPABILITIES_SUPPORTED
AND CONTEXT_REQUIREMENT_SUPPORTED
AND STRUCTURED_OUTPUT_SUPPORTED when required
AND TOOL_CALLING_SUPPORTED when required
AND PRIVACY_POLICY_PERMITS

Among eligible free models prefer by:

1. historical VERIFIED_SUCCESS_RATE
2. tool-selection reliability
3. argument-schema reliability
4. structured-output reliability
5. context compatibility
6. latency

Do NOT select a free model merely because its name sounds stronger.

Use observed Bossman benchmark evidence when available.

==================================================
5I. NO PAID ESCALATION AFTER BUDGET EXHAUSTION
==================================================

Once:

MAX_OPENROUTER_SPEND_USD

has been reached:

PAID_MODEL_CALLS_ALLOWED = FALSE

for the remainder of this acceptance mission.

Do not silently increase the budget.

Do not use another paid provider as a workaround.

Continue with:

deterministic execution
or
eligible free models.

If no capable free model exists:

MISSION_STATUS=WAITING_OWNER

Do not fabricate completion.

==================================================
5J. MODEL HOT-SWAP CONTINUITY TEST
==================================================

Explicitly test:

GLM 5.3 Flash
→ performs steps A/B/C
→ provider capacity exhausted
→ checkpoint
→ free model selected
→ SAME mission_id resumes
→ previously VERIFIED steps are NOT repeated
→ free model receives minimal reconstructed context
→ mission continues

Required assertions:

MISSION_ID_UNCHANGED=YES
VERIFIED_STEPS_REPLAYED=0
DUPLICATE_SIDE_EFFECT_COUNT=0
CONSTRAINTS_RETAINED=YES
PERMISSIONS_RETAINED=YES
BUDGET_POLICY_RETAINED=YES

==================================================
6A. ASTRA SUPERVISOR MODE
==================================================

Astra is the SUPERVISOR of this acceptance mission.

Astra should continuously inspect observable execution evidence from Codex/Bossman:

tool results
test results
git diff
git status
runtime state
UI state
verification receipts
benchmark results
provider failures
mission state

Astra must NOT merely wait for the final report.

Use an iterative supervision loop:

OBSERVE
→ IDENTIFY FAILURE
→ CLASSIFY ROOT CAUSE
→ ISSUE CORRECTION
→ EXECUTE
→ VERIFY
→ RECORD LESSON
→ CONTINUE

When Codex/Bossman gets stuck:

do not allow repeated identical attempts indefinitely.

After repeated equivalent failure:

STOP CURRENT APPROACH
→ inspect evidence
→ form a new bounded hypothesis
→ change strategy
→ retry only when justified.

==================================================
6B. STRUCTURED REASONING MEMORY
==================================================

Do NOT store private chain-of-thought.

Instead maintain a durable STRUCTURED ENGINEERING MEMORY.

Suggested path:

docs/learning/astra_acceptance/

Create compact machine-readable records such as:

decision_trace.jsonl
failure_patterns.jsonl
successful_repairs.jsonl
model_performance.jsonl

Each decision trace should contain:

timestamp
mission_id
task_id
problem
observable_evidence
failure_class
hypothesis_summary
chosen_action
why_this_action
files_or_components
tests_run
result
verification
cost
model_used
reusable_lesson

Do not include hidden internal reasoning.

The goal is to teach future local models:

WHAT FAILED
WHAT EVIDENCE IDENTIFIED IT
WHAT ACTION WAS CHOSEN
WHETHER IT WORKED
WHAT SHOULD BE DONE NEXT TIME

==================================================
6C. FAILURE PATTERN MEMORY
==================================================

Whenever a real bug is found, record a reusable pattern.

Example schema:

PATTERN_ID=
SYMPTOM=
COMPONENT=
ROOT_CAUSE=
DETECTION_SIGNAL=
BAD_APPROACHES=
SUCCESSFUL_FIX=
VERIFICATION_TEST=
REGRESSION_RISK=

Before attempting a fix:

search existing failure memory.

If the same failure pattern already exists:

reuse the proven repair strategy where applicable.

Do not repeat previously failed approaches without new evidence.

==================================================
6D. TEACH THE LOCAL MODEL FROM VERIFIED OUTCOMES
==================================================

Training/learning artifacts must be based on VERIFIED outcomes.

GOOD training example:

problem
→ evidence
→ action
→ test
→ PASS
→ fresh verification

BAD training example:

model guessed solution
→ no execution
→ stored as successful lesson

Only promote a lesson to:

VERIFIED_LESSON

when its associated fix/action is independently verified.

Failed attempts may still be stored as:

NEGATIVE_EXAMPLE

This is valuable for teaching the future local model what NOT to do.

==================================================
6E. MODEL PERFORMANCE MEMORY
==================================================

Record performance separately for every model used.

For each model:

MODEL_ID
MISSIONS
VERIFIED_SUCCESS
FALSE_SUCCESS
TOOL_SELECTION_ERRORS
ARGUMENT_SCHEMA_ERRORS
STRUCTURED_OUTPUT_ERRORS
RETRIES
TOKENS
COST
LATENCY
FAILURE_CLASSES

This data should feed future Model Broker decisions.

Do not let model reputation change from one anecdotal result.

Use minimum sample thresholds.

==================================================
6F. ASTRA WATCHDOG
==================================================

Astra should detect when the executor is looping.

Examples:

same failing test repeatedly
same malformed tool call repeatedly
same patch reverted repeatedly
same provider error repeatedly
same UI action with unchanged state repeatedly

Maintain:

ATTEMPT_FINGERPRINT

If the same failure fingerprint repeats >= 2 times without new evidence:

LOOP_DETECTED=YES

Then:

1. stop repeating
2. inspect current state
3. consult failure memory
4. change hypothesis or executor/model
5. retry once with the new strategy

If still blocked:

mark BLOCKED with exact evidence.

==================================================
6G. COMMIT / PUSH CHECKPOINT POLICY
==================================================

Do NOT wait until the entire mission ends before preserving good work.

After each coherent VERIFIED repair:

1. inspect diff
2. run targeted tests
3. run relevant invariant tests
4. secret scan changed files
5. commit
6. push to the authorized feature branch
7. fetch remote
8. verify LOCAL_SHA == REMOTE_SHA
9. record SHA in structured engineering memory

This protects progress from:

capacity exhaustion
session termination
model crash
context loss
tool failure

Do NOT commit half-working experiments.

Do NOT push failing code.

Do NOT force push.

==================================================
6H. CAPACITY-EMERGENCY CHECKPOINT
==================================================

If Astra/Codex receives a warning such as:

capacity nearly exhausted
context nearly exhausted
session limit approaching
provider quota nearly exhausted

IMMEDIATELY switch into:

SALVAGE_CHECKPOINT_MODE

Actions:

git status
git diff --stat
run smallest relevant tests

If current changes are verified and coherent:

commit
push
verify remote SHA

Then write:

CURRENT_MISSION_ID
LAST_VERIFIED_SHA
COMPLETED_WORK
OPEN_WORK
FAILED_APPROACHES
NEXT_EXACT_ACTION

into the handoff artifact.

Do this BEFORE attempting further architecture work.

==================================================
6I. CONTEXT COMPACTION
==================================================

When Astra context becomes large:

do NOT reread the entire repository.

Compress current knowledge into:

architecture_map
verified_facts
open_findings
completed_fixes
failed_approaches
current_SHAs
next_actions

Then continue from those structured artifacts.

Repository files + durable engineering memory are authoritative.

Conversation memory is not sufficient.

==================================================
6J. AUTONOMOUS BUG CORRECTION
==================================================

During the real acceptance run:

if a bug is reproduced and the root cause is clear:

Astra may instruct Codex to fix it immediately.

Required sequence:

REPRODUCE
→ EVIDENCE
→ ROOT_CAUSE
→ MINIMAL_PATCH
→ TARGETED_TEST
→ REAL FUNCTION RETEST
→ REGRESSION CHECK
→ COMMIT
→ PUSH
→ VERIFY REMOTE SHA
→ STORE VERIFIED LESSON

Do not defer obvious P0/P1 defects merely to keep auditing.

Audit and repair should operate as a closed loop.

==================================================
6K. LEARNING QUALITY GATE
==================================================

Never train future local models on:

unverified guesses
private chain-of-thought
secrets
API keys
raw credentials
private user data
huge raw logs
duplicate traces
failed fixes labelled as successful

Every stored learning artifact must be:

compact
sanitized
outcome-labelled
evidence-linked
reusable

Target:

high-information engineering traces,
not huge token dumps.

==================================================
6L. FINAL SUPERVISOR METRICS
==================================================

Report:

ASTRA_SUPERVISION_CYCLES=
BUGS_DETECTED_DURING_LIVE_RUN=
BUGS_FIXED_DURING_LIVE_RUN=

LOOPS_DETECTED=
LOOPS_RECOVERED=
LOOPS_UNRESOLVED=

GLM53_FLASH_CALLS=
FREE_MODEL_FALLBACK_CALLS=
PAID_ESCALATION_CALLS=

MODEL_SWITCHES=
MODEL_SWITCH_RESUME_SUCCESS_RATE=

VERIFIED_LESSONS_CREATED=
NEGATIVE_EXAMPLES_CREATED=
FAILURE_PATTERNS_CREATED=

CHECKPOINT_COMMITS=
CHECKPOINT_PUSHES=
LAST_REMOTE_SHA=

CAPACITY_EXHAUSTION_OCCURRED=
CAPACITY_SALVAGE_SUCCESS=
```

## Evidence and reporting conventions

- The paid-call share target in the replacement budget block is authoritative.
  Also report the share of all cloud calls from 5E; free failover can lower that
  share without violating the paid-call target. Explain availability exceptions.
- Count each dispatched provider attempt, including retries, as a call. Count
  mission IDs uniquely per model; a hot-swapped mission can appear for both
  models, but its final verified outcome is counted once in campaign totals.
- Cost per verified success uses all applicable attempt costs divided by
  independently verified successes. Report null when the denominator is zero.
  Record latency in milliseconds and sample counts with all reliability rates.
- Record the minimum sample threshold and benchmark version used for broker
  ranking. Insufficient evidence is UNKNOWN, not evidence of strong performance.
- Checkpoint before switching. Reconcile ambiguous in-flight side effects using
  durable receipts/idempotency keys before resuming; do not blindly replay them.
- Test exhaustion with a controlled fault and label injected evidence explicitly.
  Do not deliberately burn credits. Distinguish a deterministic continuity test
  from proof of an actual live provider/model handoff.
- Decision traces contain concise engineering explanations supported by visible
  evidence. Do not request, capture or export hidden model reasoning fields.
- Store outcome labels and verification references with each learning record.
  Independent verification means an executable check or external receipt, not
  the same model declaring its own answer correct. Deduplicate and sanitize
  records before promotion; retain failed attempts as NEGATIVE_EXAMPLE.
- If remote checkpointing fails, preserve the local verified commit and handoff,
  record the exact failure and remote status, and never claim a successful push.
  Do not reset, overwrite or force-push unrelated work to manufacture SHA parity.
