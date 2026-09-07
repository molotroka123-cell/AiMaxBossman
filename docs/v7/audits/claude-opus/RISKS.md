# V7 Risks — Claude Opus 4

**AUDITOR:** Claude Opus 4 (Anthropic)
**DATE:** 2026-09-07 21:31 CEST

---

## High Priority Risks

### RISK-1: WSG Complexity Overhead

**Description:** World State Graph adds significant complexity without proportional value.

**Probability:** MEDIUM
**Impact:** HIGH
**Mitigation:** Start with minimal WSG schema (5-10 node types), measure value before expanding.

### RISK-2: Strategy Search Ineffectiveness

**Description:** Strategy search produces strategies no better than simple heuristics.

**Probability:** MEDIUM
**Impact:** MEDIUM
**Mitigation:** Compare strategy search vs heuristics with epoch4_performance.py, abandon if no improvement.

### RISK-3: Parallel Track Dilution

**Description:** Parallel debt closure + V7 architecture dilutes focus, neither completes well.

**Probability:** HIGH
**Impact:** MEDIUM
**Mitigation:** Assign separate owners to each track, weekly sync, clear milestones.

### RISK-4: Reality Compiler Conflict

**Description:** WSG conflicts with existing Reality Compiler v0.1, creates competing source of truth.

**Probability:** MEDIUM
**Impact:** HIGH
**Mitigation:** WSG explicitly extends Reality Compiler, REUSES/MODIFIES analysis for every change.

### RISK-5: Owner Cognitive Overload

**Description:** WSG dashboard overwhelms owner with state visibility, not clarity.

**Probability:** MEDIUM
**Impact:** MEDIUM
**Mitigation:** Owner-first design, minimal dashboard, progressive disclosure.

---

## Medium Priority Risks

### RISK-6: Model Routing Overhead

**Description:** Model routing adds latency without improvement.

**Probability:** LOW
**Impact:** MEDIUM
**Mitigation:** Measure routing overhead, disable if net negative.

### RISK-7: Agent Teams Complexity

**Description:** Dynamic agent teams add orchestration complexity.

**Probability:** MEDIUM
**Impact:** LOW
**Mitigation:** Start with static teams, add dynamics only if proven valuable.

### RISK-8: WSG Query Performance

**Description:** Temporal WSG queries too slow for real-time use.

**Probability:** MEDIUM
**Impact:** LOW
**Mitigation:** Indexing, caching, query optimization, fallback to simple queries.

---

## Rollback Plan

Every V7 change must have:

1. **Rollback commit** — Prepared in advance
2. **Rollback test** — Weekly rollback drills
3. **Rollback evidence** — Logs showing successful rollback

**WSG-specific rollback:**
- Snapshot WSG before changes
- Restore from snapshot on failure
- Reality Compiler remains authoritative during transition

---

*Independent risks by: Claude Opus 4 (Anthropic)*
