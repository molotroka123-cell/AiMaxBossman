# Live Mission Control — three-panel prototype

Open `tools/mission_control_prototype/index.html` in a modern browser. No server, packages, authentication, GPU, or provider keys needed.

## Three integrated panels
1. **Hardware** — synthetic CPU, Radeon 8060S GPU, 128 GB unified RAM, temperature and load history. No actual machine polling.
2. **Agents & Tasks** — pause/resume DEMO agents, add mock tasks, simulate one tick to advance the queue.
3. **Tokens & Cost** — synthetic token counters, split local/cloud, adjustable daily budget and warnings.

The interface has **two non-mixing modes**:
- **DEMO SIMULATION**: mock values, mock agents/tasks; reset, tick, and export snapshot.
- **REAL / VERIFIED**: fixed GitHub reference snapshot (16 Oct 2026) for repository and open PR #110/#111/#112. All CPU/GPU/RAM, runtime agent status, tasks, usage and bills display **NOT CONNECTED**. Do not imply the snapshot is streaming/live.

Export JSON includes an explicit `source` discriminator. Do not use demo values for live decision-making.

## Live integration contract (proposal only; NOT implemented)

Read-only routes, authenticated by existing Bossman owner auth:
- `GET /api/mission-control/v1/hardware`: `{source:"local_telemetry",observed_at:"ISO",cpu_percent:number|null,gpu_percent:number|null,ram_used_gb:number|null,ram_total_gb:128,temperature_c:number|null}`
- `GET /api/mission-control/v1/agents`: `{source:"runtime",observed_at:"ISO",agents:[{id,name,state,task_id}]}`
- `GET /api/mission-control/v1/tasks`: `{source:"runtime",observed_at:"ISO",tasks:[{id,title,agent_id,state}]}`
- `GET /api/mission-control/v1/usage`: `{source:"metered",observed_at:"ISO",window:"day",input_tokens,output_tokens,local_tokens,cloud_tokens,usd_estimate,usd_invoiced,budget_usd}`

Rules:
- Separate estimated vs invoiced cost. An offline local model must not acquire fictional dollar billing.
- Every read includes `source`, `observed_at`, machine identity, freshness/TTL and explicit null for unavailable metrics.
- Limit polling frequency; prefer existing endpoints/collectors; never leak environment variables or provider keys.
- Any future task lifecycle button must require owner approval and must call explicit guarded APIs. Prototype never starts actual work.
- No runpod or paid GPU calls without explicit owner consent. No silent PR merge.
- Integrate under existing Command Center navigation; preserve CSS isolation and current security gates.
- Confirm tests on Windows Ryzen AI Max+ 395; do not mark installed or live until owner run passes.

## Mini integration prompt

> In `molotroka123-cell/AiMaxBossman`, review `tools/mission_control_prototype/index.html` and this README. Integrate the three panels (hardware, agents/tasks, tokens/cost) into the existing Command Center, preserving DEMO vs REAL separation. Reuse existing authenticated read-only runtime APIs; only add v1 endpoints for missing metrics. Display UNKNOWN/STALE when sources fail. Maintain owner approval for actions, cap polling, avoid secret leakage, do not spend money. Run targeted tests and Windows owner smoke-test, attach evidence for exact SHA, open a PR for review; never auto-merge or report unverified live operation.
