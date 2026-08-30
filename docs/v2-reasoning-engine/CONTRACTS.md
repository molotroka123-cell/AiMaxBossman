# V2 — Typed Contracts

All inter-stage messages conform to one of these schemas.
Invalid JSON must never reach the Executor.

---

## TaskEnvelope

```json
{
  "$schema": "v2/task-envelope/1.0",
  "task_id": "<uuid>",
  "objective": "<string>",
  "task_class": "coding | planning | tool_use | json | vision | long_context | classification | security_review",
  "risk_level": "low | medium | high | critical",
  "reasoning_level": "fast | standard | deep | multi_pass",
  "budget": {
    "max_llm_calls": 6,
    "max_cost_usd": 0.25,
    "max_latency_ms": 30000
  },
  "available_tools": ["<tool_id>"],
  "tool_reliability": { "<tool_id>": "high | medium | low | unknown" },
  "required_verification": ["test | diff | command_exit | dom_state | api_response | schema_check"],
  "context_refs": ["<ref_id>"]
}
```

---

## Plan

```json
{
  "$schema": "v2/plan/1.0",
  "plan_id": "<uuid>",
  "task_id": "<uuid>",
  "steps": [
    {
      "step_id": "<uuid>",
      "description": "<string>",
      "depends_on": ["<step_id>"],
      "risk": "low | medium | high | critical",
      "requires_approval": false
    }
  ],
  "typed_actions": ["<TypedAction>"],
  "abort_conditions": ["<string>"]
}
```

---

## TypedAction

```json
{
  "$schema": "v2/typed-action/1.0",
  "action_id": "<uuid>",
  "step_id": "<uuid>",
  "kind": "tool_call | code_patch | browser_action | shell_command | answer | approval_request",
  "input": {},
  "expected_result": {
    "description": "<string>",
    "schema": {}
  },
  "risk": ["<string>"],
  "sensitive": false,
  "reversible": true,
  "verification": {
    "kind": "test | command_exit | diff_check | dom_state | api_response | schema_check",
    "criteria": {}
  }
}
```

---

## CandidateSet

```json
{
  "$schema": "v2/candidate-set/1.0",
  "task_id": "<uuid>",
  "step_id": "<uuid>",
  "candidates": [
    {
      "candidate_id": "<uuid>",
      "model_profile": "<string>",
      "typed_action": {},
      "reasoning_trace": "<string>",
      "self_confidence": 0.0
    }
  ],
  "selection_criteria": ["test_pass_rate | risk | cost | latency | rollback_complexity | verifier_score"]
}
```

---

## CriticOutput

```json
{
  "$schema": "v2/critic-output/1.0",
  "plan_id": "<uuid>",
  "risks": [
    {
      "step_id": "<uuid>",
      "severity": "low | medium | high | critical",
      "description": "<string>",
      "mitigation": "<string>"
    }
  ],
  "missing_constraints": ["<string>"],
  "unsafe_assumptions": ["<string>"],
  "verdict": "proceed | revise | block",
  "required_changes": ["<string>"]
}
```

---

## ObservationResult

```json
{
  "$schema": "v2/observation/1.0",
  "action_id": "<uuid>",
  "observation_kind": "test_run | git_diff | command_output | dom_snapshot | api_response | screenshot",
  "raw_result": "<string>",
  "exit_code": null,
  "success": true,
  "errors": [],
  "evidence_refs": ["<ref_id>"]
}
```

---

## VerifierVerdict

```json
{
  "$schema": "v2/verifier-verdict/1.0",
  "action_id": "<uuid>",
  "verdict": "pass | fail | low_confidence",
  "confidence": 0.0,
  "evidence_used": ["<ObservationResult.observation_kind>"],
  "failed_criteria": ["<string>"],
  "reason_codes": ["schema_invalid | test_fail | command_fail | evidence_missing | confidence_below_threshold | safety_flag"]
}
```

---

## DecisionRecord

```json
{
  "$schema": "v2/decision/1.0",
  "record_id": "<uuid>",
  "task_id": "<uuid>",
  "stage": "classifier | router | planner | critic | verifier | repair | gate",
  "decision": "pass | retry | replan | escalate | abort",
  "confidence": 0.0,
  "evidence": ["<string>"],
  "reason_codes": ["<string>"],
  "selected_model_profile": "<string>",
  "rejected_profiles": ["<string>"],
  "next_state": "<string>",
  "ts": 0.0
}
```

---

## ToolManifest

Before reasoning, the model receives a validated tool manifest:

```json
{
  "$schema": "v2/tool-manifest/1.0",
  "tools": [
    {
      "tool_id": "<string>",
      "name": "<string>",
      "description": "<string>",
      "available": true,
      "reliability": "high | medium | low",
      "requires_approval": false,
      "schema": {}
    }
  ],
  "generated_at": 0.0,
  "ttl_ms": 60000
}
```

Models must use only tools present in the manifest and must prefer
higher-reliability tools over lower ones when multiple options exist.
Facts obtainable via a tool must not be reasoned from training data.
