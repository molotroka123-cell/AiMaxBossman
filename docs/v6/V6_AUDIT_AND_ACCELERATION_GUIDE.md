# V6 Audit & Acceleration Guide

**Repository:** molotroka123-cell/AiMaxBossman  
**Current HEAD:** `ddea21112f89c978df50aae8948c5955d7dada2e`  
**Audit Date:** 2026-09-07  
**Status:** V5 Frozen → V6 Planning

---

## Executive Summary

Репозиторий находится в состоянии **V5 Freeze Candidate** с открытыми критическими finding'ами. V6 требует закрытия V4/V5 долгов перед началом новой функциональности.

### Ключевые метрики

| Показатель | Значение | Статус |
|------------|----------|--------|
| Total Commits | 30+ | ✅ Active |
| Open P0 Issues | 4 | 🔴 BLOCKING |
| Open P1 Issues | 4 | 🟠 HIGH |
| V5 Release Scorecard | ✅ Present | 🟢 Ready |
| V4 Performance Protocol | ✅ Implemented | 🟢 Ready |
| Editor Baseline | ✅ PASS (Linux) | 🟢 Ready |
| Windows Acceptance | NOT_RUN | ⚠️ Pending |
| Local Model Acceptance | NOT_RUN | ⚠️ Pending |

---

## V6 Position Analysis

### Откуда начинаем V6

**V3 Completion:**
- ✅ Video Studio baseline (176 passed, 133 skipped)
- ✅ Web Designer baseline (5 passed, 0 failed)
- ✅ Editor user acceptance (real Chromium, fresh process, restart proof)
- ✅ Security fixes (S1-S4, context denial, owner roots)

**V4 Completion:**
- ✅ Paired performance protocol (epoch4_performance.py)
- ✅ Triple performance measurement framework
- ✅ Real workload telemetry collection
- ✅ Evidence-based scoring (INSUFFICIENT_EVIDENCE honest verdicts)

**V5 Completion:**
- ✅ V5 Release Scorecard
- ✅ Objective Contract Evidence
- ✅ Epoch 5 Plan
- ⚠️ Runtime activation закрыта флагом
- ⚠️ Fleet experimental (no multi-node evidence)

### V6 Starting Constraints

1. **4 P0 issues не закрыты** — ZIP в корне, security PR, IMG, .bossman-state
2. **AT-01/AT-03 открыты** — complete obligations, external UI freshness
3. **Windows acceptance NOT_RUN** — все исправления на Linux симуляции
4. **Local model NOT_RUN** — нет same-model measured evidence
5. **Intelligence retention INSUFFICIENT_EVIDENCE** — <100 пар, <30 наблюдений

---

## Critical Path to V6

### Phase 0: V4/V5 Closure (1-2 дня)

```bash
# 1. Закрыть ISSUE-1: ZIP архивы в корне
git filter-repo --force --invert-paths --path 'AiMaxBossman_*.zip' --path 'BOSSMAN_*.zip' --path 'Bossman_*.zip' --path '*.zip'
echo '*.zip' >> .gitignore

# 2. Закрыть ISSUE-2: Security PR #1
# Review + merge или close с явным решением
gh pr view 1
gh pr merge 1 --merge  # или gh pr close 1

# 3. Закрыть ISSUE-3: IMG_3955.png
git rm IMG_3955.png
echo 'IMG_*.png' >> .gitignore
git commit -m "chore: remove phone photo from root"

# 4. Закрыть ISSUE-4: .bossman-state в git
# Мигрировать в SQLite/Redis, удалить из git
git rm -r .bossman-state
echo '.bossman-state/' >> .gitignore
git commit -m "chore: move runtime state out of git"
```

### Phase 1: AT-01/AT-03 Closure (1 день)

```bash
# AT-01: Complete all obligations
# Требуется: тест на ВСЕ обязательства из objective contract
# Не просто "любой эффект", а все задекларированные

# AT-03: External UI freshness
# Требуется: тест на external state observation freshness
# Не internal clock, а реальное external UI состояние
```

### Phase 2: Windows Acceptance (1-2 дня)

```bash
# Требуется реальная Windows машина владельца
# Не Linux симуляция cp1251/OneDrive/COMSPEC

# Checklist:
# - Encoding: utf-8 с errors="replace" на raw output
# - Desktop: SHGetKnownFolderPath, не %USERPROFILE%\Desktop
# - Shell: Git-Bash или %COMSPEC% /c, не sh -c
# - File lock: msvcrt.seek(0) + LK_LOCK + except в finally
# - ACL: icacls best-effort с warning
```

### Phase 3: Local Model Acceptance (2-3 дня)

```bash
# Требуется same-model measured evidence
# Не "модель работает", а измеренные:
# - Latency p50/p95/p100
# - Token throughput
# - Context window utilization
# - Same-model retention (не cross-model)

# scripts/epoch4_performance.py collect --label baseline
# scripts/epoch4_performance.py compare
```

---

## V6 Feature Candidates

### V6.1: Multi-Node Fleet

**Current State:** Fleet experimental, loopback only, no multi-node evidence

**Requirements:**
- [ ] 2+ реальных узла (не localhost:8800 + localhost:8801)
- [ ] Lease distribution proof
- [ ] Memory reservation across nodes
- [ ] delete_lease cleanup verified

**Evidence Needed:**
```sql
SELECT COUNT(DISTINCT node_id) FROM leases WHERE status='active';
-- Должно быть >= 2
```

### V6.2: Production Canary

**Current State:** Local only, no prospective cohort, no durable evidence

**Requirements:**
- [ ] Prospective cohort (>=100 missions)
- [ ] Durable evidence (external ledger, not in-memory)
- [ ] Real failed member (не simulated failure)
- [ ] Denial proof (context denial before ASK)
- [ ] Rollback proof (monotonic anchor)
- [ ] Second restart (fresh process, same state)

### V6.3: Intelligence Retention

**Current State:** INSUFFICIENT_EVIDENCE (<100 пар, <30 наблюдений)

**Requirements:**
- [ ] >=100 пар нагрузок (baseline vs candidate)
- [ ] >=30 наблюдений в каждом семействе
- [ ] Cost measurement (не ноль, не null)
- [ ] Same-model retention (не cross-model)
- [ ] Human comparison (не model-only)

**Protocol:**
```python
# scripts/epoch4_performance.py
# 1. collect --label baseline (реальные нагрузки, не синтетика)
# 2. collect --label candidate (те же pair_id)
# 3. compare (предрегистрированный манифест)
# 4. Вердикт: MET/NOT_MET/INSUFFICIENT_EVIDENCE
```

### V6.4: V5 Runtime Activation

**Current State:** Закрыта флагом, не завершена

**Requirements:**
- [ ] Снять флаг активации
- [ ] Или задокументировать как intentional (feature flag)
- [ ] N7 completion evidence

---

## Acceleration Commands

### Quick Diagnostics

```bash
# 1. Проверить текущий HEAD
git rev-parse HEAD
# Ожидается: ddea21112f89c978df50aae8948c5955d7dada2e

# 2. Проверить грязное дерево
git status --porcelain
# Ожидается: пусто для чистого freeze candidate

# 3. Проверить открытые P0/P1
cat AUDIT_ISSUES_BACKLOG.md | grep -E '^### ISSUE-[1-8]'

# 4. Проверить V5 scorecard
cat docs/v5/V5_RELEASE_SCORECARD.md

# 5. Проверить AT-01/AT-03 статус
grep -r 'AT-01\|AT-03' docs/ --include='*.md' | grep -i open
```

### Performance Collection

```bash
# 1. Собрать baseline коллекцию
python scripts/epoch4_performance.py collect --label baseline

# 2. Собрать candidate коллекцию
python scripts/epoch4_performance.py collect --label candidate

# 3. Сравнить (только если >=100 пар, >=30 наблюдений)
python scripts/epoch4_performance.py compare

# 4. Проверить вердикт
cat output/performance_verdict.json
# Ожидается: MET/NOT_MET/INSUFFICIENT_EVIDENCE
```

### Editor Acceptance

```bash
# 1. Запустить editor baseline CI
# .github/workflows/editors-user-safety.yml

# 2. Локально (требуется Chromium + FFmpeg)
python -m playwright install --with-deps chromium
sudo apt-get install -y ffmpeg

python -m pytest -c command-center/pyproject.toml \
  command-center/tests/test_editors_user_acceptance.py \
  command-center/tests/test_web_designer_sandbox_ui.py \
  command-center/tests/test_web_designer_viewport.py \
  -q

# Ожидается: 5 passed, 0 failed, 0 skipped
```

### Security Negative Controls

```bash
# 1. Проверить context denial
python -m pytest command-center/tests/test_terminal_context_denial.py -q

# 2. Проверить owner roots
python -m pytest command-center/tests/test_v21_tools_terminal_browser.py::test_terminal_refuses_cwd_outside_roots -q

# 3. Проверить descriptor verification
python -m pytest command-center/tests/test_video_descriptor_verification.py -q

# Ожидается: все PASS
```

---

## V6 Readiness Checklist

### Pre-V6 (V4/V5 Closure)

- [ ] ISSUE-1: ZIP архивы удалены из корня и git history
- [ ] ISSUE-2: Security PR #1 merged/closed
- [ ] ISSUE-3: IMG_3955.png удалён
- [ ] ISSUE-4: .bossman-state мигрирован в external storage
- [ ] ISSUE-5: Solana dirs дедуплицированы
- [ ] ISSUE-6: Монорепо тулинг добавлен (pnpm/turborepo/nx)
- [ ] ISSUE-7: Stale ветки очищены
- [ ] ISSUE-8: Зависшие PR разобраны

### V6 Entry Criteria

- [ ] AT-01: Complete all obligations — CLOSED с уликой
- [ ] AT-03: External UI freshness — CLOSED с уликой
- [ ] WINDOWS_ACCEPTANCE: PASS (реальная Windows, не симуляция)
- [ ] LOCAL_MODEL_ACCEPTANCE: PASS (same-model measured)
- [ ] INTELLIGENCE_RETENTION: >=100 пар, >=30 наблюдений
- [ ] V5_RUNTIME_ACTIVATION: флаг снят или задокументирован

### V6 Feature Gates

- [ ] V6.1 Multi-Node Fleet: >=2 реальных узла
- [ ] V6.2 Production Canary: prospective cohort, durable evidence
- [ ] V6.3 Intelligence Retention: performance verdict MET/NOT_MET
- [ ] V6.4 V5 Runtime: activation flag resolved

---

## Risk Register

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Windows acceptance fails | MEDIUM | HIGH | Ранняя приёмка на реальной Windows машине |
| Local model latency regression | HIGH | MEDIUM | Same-model benchmark до/после |
| Fleet multi-node complexity | HIGH | HIGH | Начать с 2 узлов, не кластер |
| Performance verdict INSUFFICIENT | MEDIUM | MEDIUM | Честный вердикт, не synthetic data |
| Owner runtime disruption | LOW | CRITICAL | Изолировать test runtime от active worktree |

---

## Handoff Protocol

### Для следующего агента

1. **Прочитать этот документ** — V6 position и constraints
2. **Запустить quick diagnostics** — проверить текущий HEAD
3. **Закрыть Phase 0** — 4 P0 issues
4. **Закрыть AT-01/AT-03** — с уликами (тест/лог/SHA)
5. **Собрать performance evidence** — scripts/epoch4_performance.py
6. **Задокументировать V6 completion** — новый V6_RELEASE_SCORECARD.md

### Evidence Paths

```
output/
  performance_verdict.json    # MET/NOT_MET/INSUFFICIENT_EVIDENCE
  editors-user-proof/         # Editor baseline artifacts
  audit/                      # V6 audit findings
  v6/                         # V6 release documentation
```

### Forbidden Actions

- ❌ Не использовать synthetic data для performance verdict
- ❌ Не ослаблять тесты для PASS
- ❌ Не смешивать SHA разных прогонов
- ❌ Не перезапускать active owner runtime
- ❌ Не claim Windows acceptance без реальной Windows
- ❌ Не claim local model acceptance без same-model evidence

---

## Appendix: Key Documents

| Document | Path | Purpose |
|----------|------|---------|
| V5 Release Scorecard | `docs/v5/V5_RELEASE_SCORECARD.md` | V5 completion status |
| V5 Objective Contract | `docs/v5/OBJECTIVE_CONTRACT_EVIDENCE.md` | V5 obligations |
| V5 Epoch Plan | `docs/v5/EPOCH_5_PLAN.md` | V5 roadmap |
| V4 Epoch Plan | `docs/v4/EPOCH_4_PLAN.md` | V4 performance protocol |
| Audit Backlog | `AUDIT_ISSUES_BACKLOG.md` | P0/P1 issues |
| Claims Not Proven | `CLAIMS_NOT_PROVEN.md` | What this SHA does NOT prove |
| Editor Baseline | `docs/audits/EDITORS_USER_BASELINE_20260906.md` | Editor acceptance |

---

**Generated:** 2026-09-07  
**Author:** Independent V6 Audit Agent  
**SHA:** `ddea21112f89c978df50aae8948c5955d7dada2e`
