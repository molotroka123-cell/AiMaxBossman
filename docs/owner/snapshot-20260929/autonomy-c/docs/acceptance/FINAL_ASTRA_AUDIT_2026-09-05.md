# Final Astra supervised acceptance audit — 2026-09-05

This report closes mission `astra-acceptance-20260905`. It contains observable
execution evidence and verified outcomes only. It contains no private
chain-of-thought, credentials, raw private logs, or inferred success.

## Verdict

```text
MISSION_STATUS=COMPLETE_WITH_OPEN_FINDINGS
ACCEPTANCE_RESULT=FAIL
AUDIT_BASE_REMOTE_SHA=7b4e674a5f06c1d0b623268532fa0f9f14efe886
MAX_OPENROUTER_SPEND_USD=3.00
ACTUAL_OPENROUTER_SPEND_USD=0.001836
BUDGET_EXCEEDED=NO
TEMPORARY_OPENROUTER_PROVIDER_REMOVED=YES
SECRETS_WRITTEN_TO_TRACKED_ARTIFACTS=NO
```

Bossman has verified browser, approval persistence, deterministic continuity,
Organization and Fleet foundations. The requested full acceptance does not pass:
safe file mutation failed in the live Windows executor, one task reached a false
`completed` state, structured output was not exercised, and live GLM-to-free
hot-swap was not implemented or tested.

## Final GLM 5.3 Flash metrics

The exact ID was discovered from the live OpenRouter catalog and was never added
as a Bossman core constant.

```text
GLM53_FLASH_MODEL_ID=z-ai/glm-5.3-flash
GLM53_FLASH_CALLS=5
GLM53_FLASH_MISSIONS=5
GLM53_FLASH_VERIFIED_MISSIONS=2

GLM53_FLASH_TOOL_CALL_SUCCESS_RATE=50.00% (4/8 strict end-to-end outcomes)
GLM53_FLASH_TOOL_SELECTION_ACCURACY=100.00% (8/8 accepted selections)
GLM53_FLASH_ARGUMENT_SCHEMA_ACCURACY=100.00% (8/8 accepted schemas)
GLM53_FLASH_STRUCTURED_OUTPUT_SUCCESS_RATE=NOT_MEASURED

GLM53_FLASH_FALSE_SUCCESS_COUNT=1
GLM53_FLASH_MALFORMED_TOOL_CALLS=0
GLM53_FLASH_SCHEMA_FAILURES=0
GLM53_FLASH_RETRIES=0

GLM53_FLASH_INPUT_TOKENS=12586
GLM53_FLASH_OUTPUT_TOKENS=3570
GLM53_FLASH_COST_USD=0.001836
GLM53_FLASH_COST_PER_VERIFIED_SUCCESS=0.000918
GLM53_FLASH_VERIFIED_SUCCESS_PER_1M_TOTAL_TOKENS=123.79

GLM53_FLASH_MEDIAN_LATENCY=17.947s
GLM53_FLASH_HUMAN_INTERRUPTS=0
```

Strict tool success counts the final observable effect. Two calls recorded as
`executed` still returned a failed process/sandbox outcome and are failures here.
The rejected `git push` is also not counted as tool success; its non-execution is
the expected approval-test result.

## Cheap-model acceptance subset

| Test | Result | Observable evidence |
| --- | --- | --- |
| A. Read-only repository mission | FAIL | Post-rejection `git status` path reached an unavailable Docker sandbox. |
| B. Safe file mutation | FAIL | One allowed-root denial and one Windows nested-quote `SyntaxError`; target file absent. |
| C. Structured output | NOT_RUN | No measured live structured-output mission. |
| D. Tool call | PASS | Browser navigation to Example Domain independently verified. |
| E. Failed-tool / false-success trap | PASS_DETECTION | Model reported failure honestly, but Bossman task status became `completed`; false success recorded. |
| F. Approval flow | PASS | `git push` parked, survived server restart, was rejected in UI, and produced zero push side effects. |
| G. Restart/resume | PARTIAL | Approval state resumed; live model hot-swap continuity was not exercised. |
| H. Organization delegation | DETERMINISTIC_ONLY | Tests pass; no live GLM delegation mission. |
| I. Fleet placement | DETERMINISTIC_ONLY | Core tests pass; Fleet remains disabled in Command Center. |
| J. Memory continuation | DETERMINISTIC_ONLY | Persistence/redaction tests pass; no live model-switch continuation. |
| K. Small real code fix | NOT_RUN_WITH_GLM | Codex repaired live defects; GLM did not implement and verify a code fix. |

The Twitch mission additionally passed navigation, screenshot and DOM reading.
The screenshot visibly contains CVD and Open Interest panels. Exact values remain
`NOT_VERIFIED` because they are blurred pixels in the video and the model received
no vision/OCR observation. No stream advertisement was visible; a cookie-consent
banner was correctly kept separate from an advertisement.

## Verified deterministic and regression evidence

Final tests ran on the current remote base, with UTF-8 enabled and the correct
per-project pytest configuration:

```text
V3_CONTINUITY_ORGANIZATION_FLEET_TESTS=47 passed
COMMAND_CENTER_ORGANIZATION_ROUTER_MODEL_TESTS=17 passed
ACTION_CONTRACT_AND_LIVE_UI_REGRESSION_TESTS=8 passed
TOTAL_FINAL_TESTS=72 passed
```

The deterministic suites verify first-unfinished-step resume, no replay of
verified effects, persistent memory receipts, independent Organization review,
budget blocking, Fleet lease/placement safety and node-loss recovery. They do not
prove live provider failover.

## Defect register

| ID | Failure class | Status | Evidence / repair |
| --- | --- | --- | --- |
| ASTRA-LIVE-01 | UI / approval contract | FIXED | Browser UI forged `approved=true`; backend returned 403 and UI treated it as logout. Removed forged approval and kept 403 as an action refusal. |
| ASTRA-LIVE-02 | UI rendering | FIXED | OpenRouter Connect button disappeared because `appendChild` received multiple children. Replaced with `append`. |
| ASTRA-LIVE-03 | Verifier / schema | FIXED_FOCUSED | Dotted `terminal.run` was parsed as filename evidence and `use/write` wording bypassed the contract. Parser fixed; focused and final regressions pass. |
| ASTRA-LIVE-04 | Executor | OPEN | Nested quoting in the Windows project-host command produced `SyntaxError`; safe mutation was not completed. |
| ASTRA-LIVE-05 | Vision capability | OPEN | Screenshot path is returned as text; pixel-only CVD/OI facts are unavailable to the model. Add bounded vision/OCR with confidence and provenance. |
| ASTRA-LIVE-06 | Model broker continuity | OPEN | No live capacity/credit classification, dynamic free fallback and same-mission resume test. HTTP 402 is absent from the observed gateway failover set. |
| ASTRA-LIVE-07 | Fleet integration | OPEN | Fleet safety works in core tests but Command Center reports Fleet disabled/not wired. |

`FIXED_FOCUSED` means the parser repair and regression tests pass, while the full
real mutation remains blocked by the separate executor defect.

## Model escalation and failover

```text
TARGET_GLM53_FLASH_SHARE_OF_PAID_CALLS=80%
ACTUAL_GLM53_FLASH_SHARE_OF_PAID_CALLS=100%
FREE_MODEL_FALLBACK_CALLS=0
PAID_ESCALATION_CALLS=0
MODEL_SWITCHES=0
MODEL_SWITCH_RESUME_SUCCESS_RATE=NOT_MEASURED
CAPACITY_EXHAUSTION_OCCURRED=NO
CAPACITY_SALVAGE_SUCCESS=YES (checkpointing after context warnings)
```

Eligible free models were discovered dynamically, but none was promoted because
there was no historical minimum sample and no live fallback test was completed.
No stronger paid model was used to hide executor, verifier, UI or infrastructure
failures.

## Supervisor and learning metrics

```text
ASTRA_SUPERVISION_CYCLES=8
BUGS_DETECTED_DURING_LIVE_RUN=5
BUGS_FIXED_DURING_LIVE_RUN=3

LOOPS_DETECTED=0
LOOPS_RECOVERED=0
LOOPS_UNRESOLVED=0

VERIFIED_LESSONS_CREATED=6
NEGATIVE_EXAMPLES_CREATED=2
FAILURE_PATTERNS_CREATED=5

CHECKPOINT_COMMITS=5
CHECKPOINT_PUSHES=5
LAST_REMOTE_SHA_RECORDED_BEFORE_FINAL_AUDIT=7b4e674a5f06c1d0b623268532fa0f9f14efe886
```

Known mission commits retained in the remote ancestry include `36375b0`
(operating policy), `7213dae` (live UI repair), `b5441f7` (action-contract repair)
and `4cc3360` (Twitch evidence checkpoint).

## Testing-period journal checkpoint

```text
TESTING_PERIOD_SESSION=0ded40f7389f
TESTING_PERIOD_EVENTS=2788
TESTING_PERIOD_RECORDED_ERRORS=9
TESTING_PERIOD_REDACTIONS=0
TESTING_PERIOD_COMMIT=f242e27911ee
TESTING_PERIOD_PUSHED=YES
PUBLISHED_OPENROUTER_KEY_PATTERN_FOUND=NO
```

The owner-visible counter was 639 before final activity. The publication snapshot
grew while audit, cleanup and status requests were executed, and the durable
sanitized export contains 2,788 accumulated events. The error count is retained
as evidence and is not converted into a pass.

## Durable evidence

- `docs/acceptance/ASTRA_SUPERVISED_ACCEPTANCE_PROMPT.md`
- `docs/acceptance/CONTINUITY_DETERMINISTIC_2026-09-05.md`
- `docs/acceptance/LIVE_HANDOFF_2026-09-05.md`
- `docs/acceptance/evidence/bossman-browser-fixed.png`
- `docs/acceptance/evidence/openrouter-connect.png`
- `docs/acceptance/evidence/twitch-k1m6a-glm53.png`
- `docs/learning/astra_acceptance/decision_trace.jsonl`
- `docs/learning/astra_acceptance/model_performance.jsonl`
- `docs/learning/astra_acceptance/failure_patterns.jsonl`
- `docs/learning/astra_acceptance/successful_repairs.jsonl`

## Next exact engineering actions

1. Fix Windows project-host argument transport without nested shell quoting; rerun
   the same safe-file mission and independently reopen the file.
2. Add strict completion gating so an honest model failure cannot project to
   `tasks.status=completed`; reproduce task 7 before closing the defect.
3. Implement explicit provider-limit classes including credit exhaustion, query
   the registry for capable zero-cost models and preserve the same mission state.
4. Run the exact hot-swap test with unchanged prompt/tools/permissions and assert
   zero replayed verified steps and zero duplicate effects.
5. Wire Fleet into Command Center before claiming live Fleet placement.
6. Add screenshot-to-vision/OCR observations with confidence thresholds; never
   convert unreadable pixels into training facts.
