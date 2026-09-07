# V7 Architecture

## Core pipeline

`Owner Intent`
-> `Reality Compiler`
-> `Mission IR`
-> `World State Graph`
-> `Strategy Generator`
-> `Utility Scorer`
-> `Policy / Permission / Budget Gate`
-> `Executor / Agent Team / Tool Path`
-> `Fresh Effect Verification`
-> `Post-State Observer`
-> `Proof Ledger`
-> `Learning Trace`
-> `Promotion Pipeline`

## 1. Reality Compiler 2.0

Compiles free-form goals into versioned Mission IR.

Required fields:
- objective
- current-state assumptions
- desired-state predicates
- effect obligations
- proof obligations
- permissions
- privacy class
- money/token/time/resource budgets
- deadline/priority
- rollback contract
- uncertainty policy
- human escalation conditions

Compilation never grants authority. It only formalizes intent.

## 2. World State Graph

Every fact must carry:

`subject | predicate | value | source | observed_at | valid_until/freshness | confidence | scope | revision | invalidation_rule`

Sources include API/tool results, filesystem, GitHub, browser, UIA, process state, model runtime, databases and explicit owner statements.

Conflicting facts coexist until reconciled; no last-write-wins without provenance rules.

## 3. Strategy Engine

Generate deterministic and model-assisted candidates. Candidate examples:
- direct API/tool path
- browser/computer-use path
- small local model + tools
- large local model checkpoint
- cloud escalation
- temporary multi-agent team
- human escalation

Score only with evidence-backed estimates.

Initial utility model:

`U = P(success)*goal_value - latency_cost - money_cost - risk_penalty - resource_pressure - uncertainty_penalty`

Hard policy denials remain outside the utility function and cannot be bought off by high utility.

## 4. Adaptive Router / Local Cognitive Fabric

Treat models as heterogeneous compute resources.

Maintain capabilities and measured runtime properties by model/device/config generation:
- tool accuracy
- structured-output accuracy
- coding quality
- vision quality
- latency distribution
- memory residency
- reload cost
- token cost
- failure rate

Use a fast router/state model continuously; wake larger models only when predicted value exceeds latency/resource cost.

## 5. Attention and QoS

Mission scheduler considers:
- owner waiting
- deadline
- business value
- safety/recovery urgency
- dependency criticality
- resource pressure
- background class

Interactive owner work can preempt/yield safe background media/indexing/training.

## 6. Dynamic Mission Teams

Teams are temporary execution structures, not permanent agent personas.

Possible roles:
- planner
- researcher
- executor
- verifier
- red-team critic
- recovery specialist

The simplest sufficient team wins. One-agent execution remains preferred for simple missions.

## 7. Strategy Recovery

Recovery changes approach, not merely retry count.

Example ladder:
`API -> accessibility/UIA -> browser vision -> alternate provider -> human escalation`

Each fallback must preserve permissions, freshness and proof obligations.

## 8. Skill Compiler

Successful traces may produce candidate skills, but candidates have no production authority.

Pipeline:
`trace -> normalize -> parameterize -> replay -> adversarial replay -> shadow -> benchmark -> canary -> promote -> monitor -> rollback`

## 9. Mission / Reality UX

Primary owner surface should show:
- goal
- current believed state
- stale/unknown facts
- proposed change
- selected strategy and alternatives
- expected cost/time/risk
- approvals needed
- live execution state
- post-state evidence
- rollback/recovery state

Specialized workspaces such as Video Studio, Web Designer, Trading and Browser remain available but are subordinate to the mission model.
