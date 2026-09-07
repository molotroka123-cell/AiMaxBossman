# V6 Quickstart Commands

**Цель:** Максимально быстрая диагностика и запуск V6 работ

---

## 1. Быстрая диагностика (30 секунд)

```bash
# Текущий HEAD и грязь
cd /path/to/AiMaxBossman
git rev-parse HEAD && git status --porcelain | wc -l

# Ожидается:
# ddea21112f89c978df50aae8948c5955d7dada2e
# 0 (чистое дерево)
```

---

## 2. P0/P1 Issues статус (10 секунд)

```bash
# Посчитать открытые ISSUE-1..8
grep -c '^### ISSUE-[1-8]' AUDIT_ISSUES_BACKLOG.md

# Ожидается: 8 (все ещё открыты)
```

---

## 3. V5 Scorecard проверка (10 секунд)

```bash
# Проверить наличие V5 документов
ls -la docs/v5/

# Ожидается:
# EPOCH_5_PLAN.md
# OBJECTIVE_CONTRACT_EVIDENCE.md
# V5_RELEASE_SCORECARD.md
```

---

## 4. Performance evidence сбор (5-10 минут)

```bash
# Собрать baseline
python scripts/epoch4_performance.py collect --label baseline

# Собрать candidate
python scripts/epoch4_performance.py collect --label candidate

# Сравнить (если >=100 пар)
python scripts/epoch4_performance.py compare

# Проверить вердикт
cat output/performance_verdict.json | jq '.verdict'

# Ожидается: MET / NOT_MET / INSUFFICIENT_EVIDENCE
```

---

## 5. Editor acceptance (2-5 минут)

```bash
# Установить зависимости
python -m playwright install --with-deps chromium
sudo apt-get install -y ffmpeg

# Запустить тесты
python -m pytest -c command-center/pyproject.toml \
  command-center/tests/test_editors_user_acceptance.py \
  command-center/tests/test_web_designer_sandbox_ui.py \
  command-center/tests/test_web_designer_viewport.py \
  -q --tb=short

# Ожидается: 5 passed, 0 failed, 0 skipped
```

---

## 6. Security negative controls (1-2 минуты)

```bash
# Context denial
python -m pytest command-center/tests/test_terminal_context_denial.py -q

# Owner roots
python -m pytest command-center/tests/test_v21_tools_terminal_browser.py::test_terminal_refuses_cwd_outside_roots -q

# Descriptor verification
python -m pytest command-center/tests/test_video_descriptor_verification.py -q

# Ожидается: все PASS
```

---

## 7. Windows acceptance checklist

```bash
# На реальной Windows машине:

# 1. Encoding test
python -c "'── 1 passed ── ✓ →'.encode('cp1251')"
# Ожидается: UnicodeEncodeError (подтверждает проблему)

# 2. Desktop path
python -c "import os; print(os.path.expanduser('~\\Desktop'))"
# Сравнить с реальным Desktop (может быть в OneDrive)

# 3. Shell availability
where sh
where bash
where cmd

# 4. File lock test
python tests/test_windows_file_lock.py
```

---

## 8. Local model acceptance

```bash
# 1. Запустить модель
ollama run llama3.1:70b

# 2. Измерить latency
python scripts/measure_model_latency.py --model llama3.1:70b

# 3. Собрать выборку
python scripts/collect_model_samples.py --model llama3.1:70b --count 100

# 4. Проверить retention
python scripts/check_model_retention.py --model llama3.1:70b
```

---

## 9. Git cleanup (P0 closure)

```bash
# ISSUE-1: Удалить ZIP из истории
git filter-repo --force --invert-paths \
  --path 'AiMaxBossman_*.zip' \
  --path 'BOSSMAN_*.zip' \
  --path 'Bossman_*.zip' \
  --path '*.zip'

echo '*.zip' >> .gitignore

# ISSUE-3: Удалить IMG
git rm IMG_3955.png
echo 'IMG_*.png' >> .gitignore

# ISSUE-4: Удалить .bossman-state
git rm -r .bossman-state
echo '.bossman-state/' >> .gitignore

# Commit
git add .gitignore
git commit -m "chore: close P0 issues (ZIP, IMG, state)"
```

---

## 10. V6 readiness final check

```bash
# Чеклист
echo "=== V6 Readiness ==="
echo "P0 closed: $(grep -c '✅' AUDIT_ISSUES_BACKLOG.md) / 8"
echo "AT-01: $(grep -i 'AT-01' docs/ -r --include='*.md' | grep -ci closed)"
echo "AT-03: $(grep -i 'AT-03' docs/ -r --include='*.md' | grep -ci closed)"
echo "Windows: $(grep 'WINDOWS_ACCEPTANCE' docs/ -r --include='*.md' | grep -o 'PASS\|NOT_RUN')"
echo "Local model: $(grep 'LOCAL_MODEL_ACCEPTANCE' docs/ -r --include='*.md' | grep -o 'PASS\|NOT_RUN')"
echo "Intelligence: $(cat output/performance_verdict.json 2>/dev/null | jq -r '.verdict' || echo 'NOT_COLLECTED')"
```

---

## 11. Push acceleration

```bash
# 1. Создать V6 ветку
git checkout -b v6/acceleration-20260907

# 2. Закоммитить изменения
git add .
git commit -m "feat(v6): acceleration checkpoint"

# 3. Push с force (если нужно)
git push -u origin v6/acceleration-20260907 --force-with-lease

# 4. Создать PR
gh pr create \
  --title "V6 Acceleration Checkpoint" \
  --body "V6 readiness: [вставить checklist результат]" \
  --base main \
  --head v6/acceleration-20260907
```

---

## 12. CI verification

```bash
# Проверить статус CI
gh run list --limit 5

# Проверить конкретный run
gh run view <run_id> --log

# Проверить артефакты
gh run view <run_id> --dir artifacts
```

---

## 13. Artifact download

```bash
# Скачать артефакты последнего run
gh run download --dir output/latest-ci

# Или конкретный артефакт
gh run download <run_id> --name editors-user-proof --dir output/
```

---

## 14. Emergency rollback

```bash
# Если V6 пошёл не туда:
git log --oneline -10
git checkout <previous-good-sha>
git reset --hard HEAD

# Или откатить последний коммит
git reset --hard HEAD~1
```

---

## 15. Final V6 checkpoint

```bash
# Зафиксировать V6 completion
cat > docs/v6/V6_RELEASE_SCORECARD.md << 'EOF'
# V6 Release Scorecard

**SHA:** $(git rev-parse HEAD)
**Date:** $(date -Iseconds)

## Completion Status

| Criterion | Status | Evidence |
|-----------|--------|----------|
| P0 Issues | $(grep -c '✅' AUDIT_ISSUES_BACKLOG.md)/8 | AUDIT_ISSUES_BACKLOG.md |
| AT-01 | $(grep -i 'AT-01' docs/ -r --include='*.md' | grep -ci closed) | docs/... |
| AT-03 | $(grep -i 'AT-03' docs/ -r --include='*.md' | grep -ci closed) | docs/... |
| Windows | $(grep 'WINDOWS_ACCEPTANCE' docs/ -r --include='*.md' | grep -o 'PASS\|NOT_RUN') | docs/... |
| Local Model | $(grep 'LOCAL_MODEL_ACCEPTANCE' docs/ -r --include='*.md' | grep -o 'PASS\|NOT_RUN') | docs/... |
| Intelligence | $(cat output/performance_verdict.json 2>/dev/null | jq -r '.verdict' || echo 'NOT_COLLECTED') | output/performance_verdict.json |

## Verdict

V6_READY=$(если все PASS то YES иначе NO)
EOF

git add docs/v6/V6_RELEASE_SCORECARD.md
git commit -m "docs(v6): release scorecard"
git push
```

---

**Generated:** 2026-09-07  
**Purpose:** V6 acceleration quick reference  
**Valid for SHA:** `ddea21112f89c978df50aae8948c5955d7dada2e`
