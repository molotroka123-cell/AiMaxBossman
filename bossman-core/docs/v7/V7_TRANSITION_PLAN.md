# V7 Transition Plan

**From:** V6 OpenHands Integration  
**To:** V7 Reality/Strategy Layer  
**Branch:** v6/velocity-phase0-baseline-20260907

---

## Current State (V6)

### What Works

- ✅ UI 34/34 passing
- ✅ OpenRouter routing confirmed (GLM/Claude)
- ✅ 202 events real behavior dataset
- ✅ Review verdicts + model + cost + interventions logged
- ✅ OpenHands repository implementation complete

### P0 Problems Found

1. **Review-Escalation Deadlock ×4** — waiting_approval, хотя подтверждать нечего
2. **Approval Storm** — ~121 approval на правку документа
3. **Token Inefficiency** — 1.28M tokens на простую задачу
4. **Autonomy Loop Broken** — agent принимает плохие решения

---

## V7 Architecture Goals

### Current (V6)

```
task → action → review → uncertainty → approval → review → escalation → approval...
```

### Target (V7)

```
Mission IR → authority known → strategy → execute → evidence → evaluate post-state
                                    ↓
                    (only if authority boundary crossed → owner approval)
```

---

## Phase 1: P0 Fixes (Tonight)

### B3 — Apps Control Autonomy
**Fix:** Автоматическая инициализация + self-healing  
**Acceptance:** 24h без ручного вмешательства

### B4 — Unified Streaming Path
**Fix:** Единый streaming adapter для GLM + OpenRouter  
**Acceptance:** Streaming работает для обоих без изменений

### B5 — Timeout/Fallback/Health
**Fix:** Health check <5s, auto-fallback  
**Acceptance:** Dead модель детектится за <10s

### B2 — Local Bridge / Relay
**Fix:** Local bridge: cloud QA → local Bossman  
**Acceptance:** Cloud QA может отдавать команды

### P0 — Review Deadlock
**Fix:** Approval dedup + termination semantics + budget  
**Acceptance:** Deadlock rate = 0, approval efficiency <5

---

## Phase 2: V7 Core Architecture

- Mission IR → authority known
- Strategy layer
- Execute → evidence → evaluate
- Authority boundary detection
- Owner approval only when needed

---

## Phase 3: Metrics & Benchmarks

| Metric | Target |
|--------|--------|
| Approval Efficiency | <5 (was 121) |
| Token Efficiency | <100k (was 1.28M) |
| Deadlock Rate | 0 (was 4) |
| Autonomous Completion | >90% |
| Intervention Rate | <10% |

---

## Phase 4: Regression Corpus

- Save tasks-trace.jsonl (202 events)
- Deterministic replay
- After each change: prove no regression

---

## Timeline

| Night | Focus |
|-------|-------|
| 1 | B2, B3, B4, B5, P0 |
| 2 | V7 Mission IR + Authority |
| 3 | Metrics + Tracking |
| 4 | Regression corpus |
| 5 | Full acceptance test |

---

**Status:** Ready to start  
**Timebox:** 5 nights  
**Confidence:** HIGH

---

**Created:** 2026-09-08
