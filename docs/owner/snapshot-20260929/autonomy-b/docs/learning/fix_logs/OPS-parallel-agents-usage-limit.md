# Learning Case: OPS-parallel-agents-usage-limit

## Metadata
MODEL: claude-fable-5-1
AGENT: fable-lead
START_SHA: 1283894dc46b11d37534be373bde2c4e2edbb5ef
END_SHA: 3ec4c81d72b4930e1ac9006541ac7ebd8036ab6a
LEARNING_STATUS: FAILED_EXPERIMENT
OUTCOME: REJECTED
VERIFIED_BY:
CONFIDENCE: 0.7
TAGS: {"domain": "operations", "bug_class": "budget_exhaustion", "component": "session_orchestration", "severity": "LOW"}
FINDINGS:

## Task
Parallelize security remediation across 4 sub-agents to finish faster

## Symptom
All four sub-agents terminated on the shared session usage limit mid-work; their edits stayed uncommitted on disk; nothing was pushed for ~2h of work.

## Reproduction
- spawn 4 background agents with broad file ownership and no checkpoint instruction; wait for rate_limit 429

## Evidence
- task notifications: 'Agent terminated early due to an API error: session limit' ×4
- working tree had 25 modified files, 0 commits since T0
- recovery cost: ~1h re-reading partial diffs to judge coherence

## Hypotheses considered
- shared usage budget consumed by 4 concurrent agents plus lead
- agents instructed to 'not commit' without an alternative durable checkpoint
- broad scopes meant each agent spent most tokens on discovery

## Rejected hypotheses + why
- container restart lost work — no, files survived; the loss was verification/commit state, not bytes

## Root cause
Parallelism multiplied token burn on discovery while the only durable checkpoint (commit) was reserved for the lead, so the limit hit before any unit was verifiable.

## Relevant code paths
- docs/security/REMEDIATION_STATE.md

## Fix strategy
Two agents max, narrow file ownership, test-first RED specs in the prompt, 'write to disk early', lead commits each boundary immediately after focused tests; checkpoint-or-stop rule.

## Alternatives considered
- one agent doing everything sequentially (slower but safe)
- agents committing to their own branches (merge cost, history noise)

## Why this fix was chosen
Keeps parallel throughput where file ownership is disjoint while making every boundary durable within minutes.

## Original reproduction after fix
second wave (A2, C2, company) completed and was committed within ~30 min each

## Regression
n/a

## Failed approaches / recovery lessons
- Partial agent diffs are usable if the agent wrote coherent code early; audit by compileall + focused tests before trusting
- Never let 'do not commit' be the only instruction — pair it with 'write files early and report state'

## Generalizable lessons
- Discovery, not reasoning, is what parallel agents burn budget on; give them the exact files and RED tests
- Checkpoint frequency must be tied to budget risk, not to task completion

## Teach local model
- Recognize: many agents, one shared budget, no durable checkpoint
- Prefer: ≤2 agents, disjoint ownership, RED tests as spec, commit per boundary

## Limitations / follow-up
- single observation of the failure; the mitigated wave is one data point
