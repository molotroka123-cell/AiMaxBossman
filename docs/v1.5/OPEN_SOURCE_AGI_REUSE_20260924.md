# Bossman 1.5 — open-source autonomy reuse map

Date: 2026-09-24.

Bossman 1.5 does not vendor another agent framework into the critical path.
We reuse ideas and contracts where they are stronger than reinventing the same
pattern, while keeping one Bossman backend, one policy system and local-first
state.

## Patterns adopted

### Stateful persistent agents

Reference: https://github.com/letta-ai/letta-code

Useful ideas:
- persistent agent identity across sessions;
- agent-scoped memory and skills;
- long-horizon learning from experience;
- communication through local/remote channels.

Bossman mapping:
- PersistentAgentSociety keeps durable role identity, skill refs and measured
  verifier-backed task-class performance.
- Existing Bossman memory remains the source of truth; no second Letta server
  is required for 1.5.

### Executable skill library

Reference: https://github.com/MineDojo/Voyager

Useful ideas:
- successful behavior becomes reusable executable skills;
- skill retrieval avoids solving the same task from scratch;
- execution errors and self-verification drive refinement.

Bossman mapping:
- existing SkillFactory already creates candidates only from verified traces;
- SkillCompiler adds independent unseen-transfer evidence before compilation;
- production promotion still requires the existing shadow/reliability gates.

### Temporal context graph

Reference: https://github.com/getzep/graphiti

Useful ideas:
- facts have valid time and ingestion time;
- superseded facts remain queryable historically;
- every relationship retains provenance.

Bossman mapping:
- PersonalOperatingGraph implements these temporal/provenance rules in a small
  local JSON-backed graph rather than adding a graph database to the critical
  path;
- nodes cover projects, companies, people, files, tasks, money, models,
  workflows, markets, branches, agents, benchmarks, skills and artifacts.

### Cost/quality model routing

References:
- https://github.com/lm-sys/RouteLLM
- https://github.com/ulab-uiuc/LLMRouter

Useful ideas:
- route to the cheapest model that still meets a quality target;
- evaluate routing on held-out workloads rather than assuming a model is
  sufficient;
- cost and quality are joint optimization targets.

Bossman mapping:
- AutonomousResourceManager combines quality lower bounds, price, latency,
  energy estimate, local preference and real unified-memory capacity;
- unknown cloud price fails closed;
- Jev may choose between authorized candidates but cannot expand authority.

### Multi-agent collaboration and evaluation

References:
- https://github.com/ag2ai/ag2
- https://github.com/OpenHands/benchmarks

Useful ideas:
- explicit cooperating roles;
- independent verifier/evaluator;
- repeatable benchmark suites for long-horizon software work.

Bossman mapping:
- PersistentAgentSociety selects durable specialists based on historical
  verifier-backed performance;
- Bossman self-improvement candidates must pass regression, independent
  verification and unseen transfer before promotion.

## Deliberately not adopted

- no autonomous account creation or ToS acceptance;
- no second memory/backend just to copy an OSS architecture;
- no model output may certify its own patch;
- no auto-promotion from YouTube/teacher text into procedural memory;
- no real trading authority is introduced by learning infrastructure.

When external provider onboarding is required, Bossman produces OWNER_REQUIRED
with the exact fields/URL/instructions. The owner may provide missing data
through Telegram owner-input and Bossman resumes the task.


## 2026-09-24 research refresh

Additional patterns checked before the owner run:

- OpenHands Software Agent SDK + OpenHands benchmarks: keep the evaluator separate
  from the coding agent and use reproducible software/GAIA/safety-style tasks. Bossman
  reuses the pattern, not a second runtime: its candidate worktrees and RESULT_VERIFIER
  remain authoritative.
- DSPy optimizers (Bootstrap/MIPROv2/SIMBA/GEPA): treat prompts, examples and workflows
  as optimizable program parameters against an explicit metric. Bossman 1.5 should first
  optimize skills/workflows/prompts with held-out evidence; weight training remains a
  separate later experiment.
- LangGraph supervisor/persistence: durable checkpoint + long-term store patterns support
  restartable multi-agent teams. Bossman already has its own campaign checkpoints,
  persistent society and memory, so LangGraph is a reference rather than a dependency.
- Mem0: useful reference for scoped memory and hybrid retrieval. Current Mem0 OSS v3 no
  longer provides graph memory; therefore Bossman must not claim it is importing an OSS
  Mem0 graph. PersonalOperatingGraph stays local and Bossman-owned.

The implementation rule remains: reuse a proven contract/pattern when it is better,
but do not replace Bossman's policy, evidence, memory or exact-SHA authority with an
external framework.

---

## SAME-RUN HANDOFF: 1.5 → 1.6

После закрытия 1.5 или достижения `V1_5_NEAR_COMPLETE` этот же owner-run сразу переходит в отдельную ветку:
[feat/bossman-1.6-secret-intake-20260925](https://github.com/molotroka123-cell/AiMaxBossman/tree/feat/bossman-1.6-secret-intake-20260925)

`V1_5_NEAR_COMPLETE`: software P0 = 0, release-blocking P1 = 0, основные targeted/regression/hidden tests зелёные, canonical SHA зафиксирован; остаток только owner-live/soak/внешняя среда.

Не ждать отдельного следующего дня. Цель одного owner-run:
`BOSSMAN_1_5_CLOSED -> BOSSMAN_1_6_CLOSED`.

Evidence 1.5 и 1.6 сохраняются раздельно по своим SHA.
