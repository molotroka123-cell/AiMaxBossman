# V7 Nightly Run — Claude Prompt

**Branch:** `v6/velocity-phase0-baseline-20260907`  
**Goal:** Закрыть 5 критических пунктов за 1 прогон

---

## Мини-промпт для Claude

```markdown
# Mission: V7 Nightly Run — 5 P0/P1 Fixes

**Branch:** v6/velocity-phase0-baseline-20260907
**Timebox:** 1 night run
**Priority:** Close P0 deadlock + approval storm

## Objectives (MUST CLOSE)

### B3 — Apps Control Autonomy
**Problem:** Apps control зависит от ручной магии после каждого запуска
**Fix:**
- Автоматическая инициализация Apps control при старте
- Сохранение состояния между перезапусками
- Self-healing при detect stale state
**Acceptance:** Apps control работает 24h без ручного вмешательства

### B4 — Unified Streaming Path
**Problem:** GLM/OpenRouter streaming не совместимы
**Fix:**
- Единый streaming adapter для всех провайдеров
- GLM + OpenRouter через один path
- Fallback при provider failure
**Acceptance:** Streaming работает для GLM и OpenRouter без изменений кода

### B5 — Timeout/Fallback/Health Classification
**Problem:** Молчащая free-модель считается «живой"
**Fix:**
- Health check с timeout (<5s response)
- Auto-fallback при timeout >2x
- Health classification: healthy/degraded/dead
**Acceptance:** Dead модель детектится за <10s, fallback за <15s

### B2 — Local Bridge / Relay
**Problem:** Cloud-агент не может пробить loopback
**Fix:**
- Local bridge: cloud QA → local Bossman
- Sanitized evidence relay
- Command tunnel без прямого loopback
**Acceptance:** Cloud QA может отдавать команды локальному Bossman и получать evidence

### P0 — Review-Escalation Deadlock
**Problem:** waiting_approval ×4, хотя подтверждать нечего
**Fix:**
- Approval dedup/coalescing
- Review termination semantics
- Approval budget (0-1 для безопасных правок)
**Acceptance:** Deadlock rate = 0, approval efficiency <5 для простых задач

## Test Matrix

1. **3-System QA**
   - Video agent closes task autonomously
   - Web agent closes task autonomously
   - Apps agent closes task autonomously
   - NO manual intervention

2. **Approval Efficiency**
   - owner approvals / successful mission <5
   - intervention rate <10%
   - token efficiency <100k tokens per simple task

3. **Deadlock Replay**
   - Replay 4 deadlock traces
   - Verify 0 deadlocks
   - Verify approval storm resolved

## Execution Plan

```bash
cd bossman-core
export OPENROUTER_API_KEY="***"
export GLM_API_KEY="***"

python -m bossman.v7.nightly_run \
  --objectives B2,B3,B4,B5,P0 \
  --timeout 8h \
  --auto-commit

python -m bossman.v7.verify \
  --check autonomy \
  --check approval_efficiency \
  --check deadlock_replay
```

## Success Criteria

- ✅ B3 closed — Apps control autonomous 24h
- ✅ B4 closed — Unified streaming path
- ✅ B5 closed — Health classification working
- ✅ B2 closed — Local bridge functional
- ✅ P0 closed — Deadlock rate = 0
- ✅ 3-system QA — All agents autonomous
- ✅ Approval efficiency <5
- ✅ Token efficiency <100k per simple task

## Output

- tasks-trace.jsonl (golden regression corpus)
- nightly_report.md
- approval_metrics.json
- deadlock_analysis.md
```

---

**Ready to run:** YES  
**Timebox:** 1 night (8h)

---

**Created:** 2026-09-08
