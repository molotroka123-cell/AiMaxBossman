# V7 Priorities

## Auditor Identity

**AUDITOR_MODEL:** Perplexity AI  
**AUDITOR_PROVIDER:** Perplexity AI  
**AUDITED_SHA:** a1074f6454ccbf19c4de0490b549ec671c12591f  
**DATE:** September 07, 2026, 9:27 PM CEST  

---

## P0 — V7 Must Have (Maximum 5)

### 1. World State Graph

**Description:** Unified representation of all entities (resources, tasks, models, tools, effects, evidence) as nodes with temporal edges showing state transitions and causality.

**Why P0:** Enables counterfactual reasoning, look-ahead search, and unified state tracking. Foundation for all other V7 capabilities.

**REUSES:** Existing memory/context infrastructure, evidence system  
**MODIFIES:** Reality Compiler v0.1 state tracking  
**REPLACES:** None (new layer)  
**CONFLICTS_WITH:** None if designed as extension  
**MIGRATION:** Incremental—start with new tasks, gradually migrate existing  
**ROLLBACK:** Disable state graph, fall back to current memory system

---

### 2. Strategy Search Layer

**Description:** Counterfactual simulation engine that evaluates action sequences before execution using the state graph.

**Why P0:** Transforms Bossman from reactive to proactive. Identifies high-leverage interventions and avoids dead-ends.

**REUSES:** World State Graph, Reality Compiler effect verification  
**MODIFIES:** Task admission logic to include search phase  
**REPLACES:** None  
**CONFLICTS_WITH:** None  
**MIGRATION:** Opt-in for complex tasks initially  
**ROLLBACK:** Disable search, use direct execution

---

### 3. Adaptive Model Orchestrator

**Description:** Dynamic model selection based on task complexity, latency budget, and confidence requirements.

**Why P0:** Optimizes cost/performance tradeoff. Uses right model for right task.

**REUSES:** Existing local model routing, model registry  
**MODIFIES:** Model selection logic  
**REPLACES:** Static routing rules  
**CONFLICTS_WITH:** None  
**MIGRATION:** A/B test against current routing  
**ROLLBACK:** Revert to static routing config

---

### 4. Automated Effect Verification

**Description:** Extend Reality Compiler to automatically detect when expected post-conditions are not met and trigger recovery.

**Why P0:** Reduces manual intervention. Catches failures early.

**REUSES:** Reality Compiler v0.1, evidence system  
**MODIFIES:** Post-condition checking  
**REPLACES:** Manual effect verification  
**CONFLICTS_WITH:** None  
**MIGRATION:** Add to new effects first  
**ROLLBACK:** Disable auto-verification, keep manual

---

### 5. Mission Visibility Dashboard

**Description:** Owner-facing view showing current mission, active strategies, resource allocation, and confidence levels.

**Why P0:** Reduces owner cognitive load. Increases trust through transparency.

**REUSES:** Existing dashboard, state tracking  
**MODIFIES:** UI to show mission/strategy  
**REPLACES:** None  
**CONFLICTS_WITH:** None  
**MIGRATION:** Parallel deployment  
**ROLLBACK:** Hide new panels

---

## P1 — High Value (Maximum 10)

1. **Skill promotion automation** from verified successful traces
2. **Dynamic agent team formation** based on task decomposition
3. **Attention scheduler** for resource contention resolution
4. **Unified memory scheduling** across local and cloud models
5. **Structured output enforcement** for all model calls
6. **Vision model integration** for UI state understanding
7. **Image/video model routing** for media tasks
8. **Benchmark regression protection** with automated canary testing
9. **Budget tracking** with automatic spend alerts
10. **Evidence graph** linking decisions to outcomes

---

## P2 — Useful Later (Maximum 10)

1. Self-improving skills through automated prompt optimization
2. Multi-agent debate for high-stakes decisions
3. Counterfactual learning from simulated outcomes
4. Goal-first UX redesign
5. Local Cognitive Fabric for low-latency operations
6. Adaptive Reality OS extensions
7. Reality Compiler 2.0 with causal inference
8. Parallel action execution with conflict detection
9. Automated documentation from verified traces
10. Community skill sharing marketplace

---

## REJECTED — Explicitly NOT Recommended

1. **Complete architecture rewrite:** V6 systems (Reality Compiler, Fleet, learning) are working—extend, don't replace.

2. **Adding more models without orchestration:** More models ≠ better intelligence. Focus on adaptive selection, not accumulation.

3. **Real-time everything:** Not all operations need real-time. Use batch processing for non-urgent learning tasks.

4. **Fully autonomous mode without owner oversight:** Safety requires human-in-the-loop for high-stakes decisions.

5. **Blockchain/decentralized components:** Unnecessary complexity for a personal AI assistant.

6. **General AGI research:** Stay focused on Bossman's specific mission, not general intelligence.

---

## Sign-off

**Independent audit by: Perplexity AI**  
**Session:** 2 (perplexity-2 namespace)  
**Date:** September 07, 2026, 9:27 PM CEST
