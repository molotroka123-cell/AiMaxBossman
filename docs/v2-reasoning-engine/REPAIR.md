# V2 — Self-Repair Policy

## Classification of failures

| Failure category | First response | Limit | After limit |
|---|---|---:|---|
| Transient tool / API error | Retry with exponential backoff | 2 | Replan or abort |
| Invalid structured output | Schema repair prompt | 1 | Escalate model |
| Test failure | Targeted repair of failing assertions | 2 | Replan |
| Missing evidence / observation | Fresh observation call | 1 | Abort |
| Permission / approval denied | Abort | 0 | Report to caller |
| Safety / policy violation | Abort | 0 | Escalation + approval |
| Context overflow | Compress history / retrieve relevant | 1 | Long-context route |
| Identical failure twice in sequence | Circuit breaker → Abort | — | Report |

---

## Circuit breaker conditions

A run is force-aborted when **any** limit is exceeded:

- `budget.max_llm_calls` reached
- `budget.max_cost_usd` reached
- `budget.max_latency_ms` reached
- `max_replans` reached (default: 2 per reasoning level)
- Same failure signature observed twice in sequence
- Verifier returns `fail` 3 times on the same action

Circuit breaker always emits a `DecisionRecord` with full evidence,
reason codes, and the last `ObservationResult`.

---

## Repair loop contract

```
verifier.verdict == fail
  → classify_failure(observation, verdict)
  → if retry_eligible AND retry_count < limit:
        Executor(same action, with backoff)
  → elif replan_eligible AND replan_count < limit:
        Planner(original objective, failure_context=last_observation)
  → elif escalate_eligible:
        CapabilityRouter(stronger_profile, task, failure_context)
  → else:
        Abort(evidence=last_observation, reason_codes=verdict.reason_codes)
```

---

## Self-assessment is not evidence

The model is not allowed to mark a repair successful based on:
- Its own generated text claiming success
- A restatement of the expected result
- A prior observation from before the repair

A repair is successful only when a fresh `ObservationResult` is produced
**after** the repair action and the Verifier passes on that new evidence.
