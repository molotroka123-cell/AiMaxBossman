# V2 — Capability Routing

## Model profiles

Profiles abstract actual model names. The router resolves a profile to a
concrete model+provider at runtime using the current scorecard.

| Profile | Typical binding | Notes |
|---|---|---|
| `local-fast` | qwen2.5:3b / ollama | Sub-2s, low cost, simple tasks |
| `local-coder` | qwen2.5-coder:14b / ollama | Code generation and review |
| `local-reasoning` | qwen2.5:14b / ollama | Planning, structured reasoning |
| `local-structured` | qwen2.5:7b / ollama | JSON-critical tasks, schema enforced |
| `cloud-coder` | claude-sonnet / anthropic | Complex multi-file code |
| `cloud-reasoning` | claude-sonnet / anthropic | Architecture, deep planning |
| `cloud-structured` | claude-sonnet / anthropic | High-stakes JSON/tool contracts |
| `cloud-long-context` | claude-sonnet / anthropic | Large codebases, long docs |
| `security-model` | claude-sonnet / anthropic | Security review, always with second model |
| `vision` | llava / ollama or cloud | Screenshots, UI observation |

---

## Routing matrix

| Task class | Primary profile | Fallback profile | Verification required |
|---|---|---|---|
| `classification` | `local-fast` | `local-reasoning` | schema / rules |
| `json` | `local-structured` | `cloud-structured` | JSON Schema |
| `coding` | `local-coder` | `cloud-coder` | tests + git diff |
| `planning` | `local-reasoning` | `cloud-reasoning` | critic pass |
| `tool_use` | `local-coder` | `cloud-coder` | fresh observation |
| `long_context` | `cloud-long-context` | `cloud-reasoning` | citation / context check |
| `vision` | `vision` | cloud vision | DOM / screenshot |
| `security_review` | `security-model` | independent second | human approval |

---

## Escalation conditions

Escalate from primary to fallback/stronger profile when **any** applies:

1. JSON Schema output is invalid after one repair attempt
2. Verifier returns `fail`
3. Composite confidence below calibrated threshold (default: 0.65)
4. Task `risk_level` is `high` or `critical`
5. Two consecutive repair attempts failed
6. Candidates conflict materially
7. Action touches code, permissions, money, production data, or secrets
8. Historical scorecard for primary profile is below floor for this task class

---

## Tool-aware routing

Before the Planner constructs a plan, the router injects the ToolManifest
(see CONTRACTS.md). Selection rules:

- If a fact is obtainable via an available, high-reliability tool → use the tool; do not reason it.
- If multiple tools cover the same need → prefer the one with the higher observed success rate.
- If a required tool has `available: false` → reclassify task or abort with reason.
- Tool availability is evaluated at request time, not at model training time.

---

## Speculative local-first execution

Eligible task classes: `coding`, `planning`, `tool_use`, `json`

```
1. local-coder / local-reasoning generates draft plan + typed action
2. Local schema + rule checks run (no LLM call)
3. If checks pass AND risk_level is low/medium:
     → Execute and observe
     → If verifier passes → DONE (no cloud call needed)
4. If local checks fail OR risk_level is high OR verifier fails:
     → Stronger model receives: local draft + execution evidence
     → Stronger model corrects or replaces (not regenerates from scratch)
```

This pattern can reduce expensive model calls by 40–70% on routine tasks.

---

## Conditional debate (multi_pass only)

Debate is **disabled by default** and enabled only when:

- `risk_level` is `critical`
- Task class is `security_review`
- Two repair loops failed on the same task
- Candidate outputs conflict on a key decision
- Action is irreversible and confidence is below 0.80

In debate mode, each model receives a different role and independent
evidence. The Verifier/Judge selects the winner by typed criteria,
not by majority vote or self-assessment.
