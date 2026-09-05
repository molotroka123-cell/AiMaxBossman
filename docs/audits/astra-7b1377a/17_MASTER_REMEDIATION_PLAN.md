# Master remediation plan

Priority = (risk_reduction × user_value × leverage) / implementation_cost. Inputs are ordinal planning estimates, not financial forecasts. Same-axis score gains overlap and must not be summed. Detailed file/tests/effort/dependencies are in machine/roadmap.csv and supporting/FINDING_REGISTER.md.

- **FIX-ASTRA-001** [P1 NEXT; priority 216.0]: Core worker accepts model final text as done without an effect obligation gate. Effort: 2–4 engineer-days.
- **FIX-ASTRA-002** [P1 NEXT; priority 216.0]: Compound resume trusts unsigned journal completion flags. Effort: 2–4 engineer-days.
- **FIX-ASTRA-003** [P1 NEXT; priority 216.0]: Resumed plan identity compares step IDs but not actions or expectations. Effort: 2–4 engineer-days.
- **FIX-ASTRA-004** [P1 NEXT; priority 216.0]: Contract accepts validly signed old evidence from another mission and ignores expected value. Effort: 2–4 engineer-days.
- **FIX-ASTRA-005** [P1 NEXT; priority 216.0]: Crash after irreversible effect but before journal record can be classified as not started. Effort: 2–4 engineer-days.
- **FIX-ASTRA-CI-101** [P1 NEXT; priority 216.0]: Exact audited SHA is not green and branch has no enforced required checks. Effort: 1-2 days.
- **FIX-ASTRA-SEC-101** [P1 NEXT; priority 216.0]: Core HTTP policy has a DNS validation/connection race. Effort: 1-2 days.
- **FIX-ASTRA-SEC-103** [P1 NEXT; priority 216.0]: ZIP scanner omits private-key members and treats scan failures as clean. Effort: 1-2 days.
- **FIX-F001** [P1 NEXT; priority 162.0]: Lease acquisition violates shared/exclusive exclusion and fences live sharers. Effort: M.
- **FIX-F002** [P1 NEXT; priority 162.0]: Fencing checked after dispatch, not before resource mutation. Effort: L.
- **FIX-F003** [P1 NEXT; priority 162.0]: Queue completion has no owner or fence check. Effort: M.
- **FIX-F004** [P1 NEXT; priority 162.0]: Retry and human-wait decisions leave queue immediately claimable. Effort: M.
- **FIX-F005** [P1 NEXT; priority 162.0]: Unified memory admission double-counts physical headroom. Effort: M.
- **FIX-F006** [P1 NEXT; priority 162.0]: Minimized contract drops constraints while retaining sensitive step arguments. Effort: M.
- **FIX-O001** [P1 NEXT; priority 162.0]: Private/local-only contract can route to cloud-tier agent. Effort: L.
- **FIX-O002** [P1 NEXT; priority 162.0]: Cross-mission work ID collision corrupts ownership and payload. Effort: L.
- **FIX-O003** [P1 NEXT; priority 162.0]: Negative and non-finite resources bypass budget admission. Effort: M.
- **FIX-O004** [P1 NEXT; priority 162.0]: Mandatory risk role is formed but never reviewed when reviewer exists. Effort: M.
- **FIX-O005** [P1 NEXT; priority 162.0]: Treasury reserve and execution state are not a crash-consistent transaction. Effort: L.
- **FIX-O007** [P1 NEXT; priority 162.0]: Knowledge parent read trusts caller-supplied prefixes instead of lineage. Effort: M.
- **FIX-PROD-002** [P1 NEXT; priority 162.0]: Unknown OpenRouter prices become free. Effort: 1-2 days.
- **FIX-ASTRA-006** [P2 LATER; priority 96.0]: Benchmark absence of recovery/context events earns maximum sub-scores. Effort: 2–4 engineer-days.
- **FIX-ASTRA-CI-102** [P2 LATER; priority 96.0]: SAST and dependency audit cannot block and installation errors are swallowed. Effort: 1-2 days.
- **FIX-ASTRA-CI-103** [P2 LATER; priority 96.0]: Linux-only CI misses a demonstrated Windows permission assertion failure. Effort: 2-3 days.
- **FIX-ASTRA-CI-104** [P2 LATER; priority 96.0]: Known hangs and real sandbox execution are outside normal CI evidence. Effort: 2-4 days.
- **FIX-ASTRA-SEC-102** [P2 LATER; priority 96.0]: Core HTTP response has no byte budget before buffering or logging. Effort: 1 day.
- **FIX-ASTRA-SEC-104** [P2 LATER; priority 96.0]: Default startup announcement writes the installation bearer token to stdout. Effort: 0.5-1 day.
- **FIX-O006** [P2 LATER; priority 72.0]: Renewable capacity is accumulated as lifetime spend. Effort: M.
- **FIX-PROD-001** [P2 LATER; priority 72.0]: V2 context pack exceeds its serialized token budget. Effort: 0.5-1 day.
- **FIX-PROD-003** [P2 LATER; priority 72.0]: Unavailable NVIDIA process telemetry becomes measured zero. Effort: 0.5-1 day.
- **FIX-PROD-004** [P2 LATER; priority 72.0]: Global latest100 task fetch hides active mission children. Effort: 1 day.
- **HW-01** [OWNER-HARDWARE BLOCKED; priority 56]: AI MAX target acceptance capsule. Effort: 3–5 days after hardware access.
- **EXP-01** [EXPERIMENTAL; priority 18]: Offline counterfactual placement policy. Effort: 4–6 days.

Sequence: freeze and test a candidate SHA; close false-success/replay/unknown-effect boundaries; enforce privacy at egress and durable ownership/budget limits; repair CI alerts with narrowly justified fixture/public-data handling; then unify mission projection and measure owner workflows. Keep real irreversible operations behind current owner controls until the negative tests succeed. P0 NOW may legitimately be empty when no P0 is established. P1 defects are still blockers to autonomous-operation readiness. No speculative application rewrite or live provider call was performed for this audit.

Additional centrally reproduced boundaries: ASTRA-007 path confinement, ASTRA-008 full PEM redaction, ASTRA-009 authenticated scope ancestry (P1 NEXT), and ASTRA-010 final V3 context budgeting (P2 LATER). Full fix/tests/effort/risk/dependencies are in the roadmap and finding register.
