# 🔍 AiMaxBossman — Полный аудит: бэклог задач

> Создано: 2026-09-07 | Автор: Perplexity Audit  
> Статус: Готов к трекингу

---

## 🔴 КРИТИЧНО

### ISSUE-1 · chore(repo): удалить ZIP-архивы из корня

**Проблема**  
В корне репозитория хранится ~20 ZIP-файлов общим весом >4MB.  
Git не предназначен для хранения бинарников — они раздувают историю навсегда.

**Затронутые файлы**
- `AiMaxBossman_ASTRA_Fixes_35390ec.zip`
- `AiMaxBossman_ASTRA_Fixes_35390ec(1).zip` ← дубль
- `AiMaxBossman_Benchmark_Overlay_DropIn_v2.zip`
- `AiMaxBossman_Fleet_OS_Complete_Foundation_DropIn.zip`
- `AiMaxBossman_Organization_Layer_DropIn.zip`
- `AiMaxBossman_Organization_Layer_DropIn_v2.zip` ← дубль
- `AiMaxBossman_V1_FINAL_ACCEPTANCE_PACK_2026-08-30.zip`
- `AiMaxBossman_V3_7Pack.zip`
- `BOSSMAN_5_APPS_V1_FINAL_CODE_PACK.zip`
- `BOSSMAN_APP_ICON_PACK.zip` ← 2.1MB!
- `BOSSMAN_FABLE_NEXT_SESSION_PACK.zip`
- `BOSSMAN_FABLE_NEXT_SESSION_PACK_V2.zip` ← дубль
- `BOSSMAN_META_INTELLIGENCE_7_MODULES_PASS3_FINAL.zip`
- `BOSSMAN_PROMPT_CACHE_OPUS5_DROPIN.zip`
- `BOSSMAN_SOCIAL_FARM_APP4_TECH_SPEC_V1_1.zip`
- `BOSSMAN_UX_2_PROMPT_PACK_REBUILT.zip`
- `BOSSMAN_V3_AUTONOMOUS_OPERATOR_EXPANDED_FULL_PACK.zip`
- `BOSSMAN_ZIP_LEVEL_3_FINAL_REPACKED.zip`
- `Bossman_CyberSec_Training_Engine_V1.zip`
- `self_learning_orchestrator.zip`

**Решение**
- [ ] Перенести релизные архивы в GitHub Releases
- [ ] Удалить файлы из git-истории через `git filter-repo`
- [ ] Добавить `*.zip` в `.gitignore`

---

### ISSUE-2 · security: влить или переработать PR #1 (10 security fixes)

**Проблема**  
[PR #1](https://github.com/molotroka123-cell/AiMaxBossman/pull/1) висит с 30 августа.  
10 security-уязвимостей (RISK-1 → RISK-11) не закрыты месяц.

**Решение**
- [ ] Открыть PR #1 и просмотреть diff
- [ ] Если актуален — смержить в `claude/bossman-control-v03-43igbk`
- [ ] Если устарел — закрыть и создать свежий PR с теми же фиксами

---

### ISSUE-3 · chore(repo): убрать случайные бинарники из корня

**Проблема**  
`IMG_3955.png` (962KB) — фото с телефона в корне репо. Нет документации, нет контекста.

**Решение**
- [ ] Удалить файл из репо
- [ ] Добавить правило в `.gitignore`: `IMG_*.png`

---

### ISSUE-4 · chore(state): вынести `.bossman-state/` из git

**Проблема**  
Runtime-состояние приложения хранится в git. Каждое изменение state = лишний коммит.  
State должен быть в БД, Redis или отдельном хранилище.

**Решение**
- [ ] Определить тип данных в `.bossman-state/`
- [ ] Мигрировать в SQLite / Redis / внешний storage
- [ ] Добавить `.bossman-state/` в `.gitignore`
- [ ] Удалить из истории через `git filter-repo`

---

## 🟠 СЕРЬЁЗНО

### ISSUE-5 · chore(structure): устранить дублирование директорий Solana

**Проблема**  
Два варианта одного модуля:
- `apps/solana-volume-suite/` (kebab-case)
- `solana_volume_suite/` (snake_case)

Непонятно, какая директория canonical и актуальна.

**Решение**
- [ ] Определить canonical источник
- [ ] Удалить или смёрджить дублирующую директорию
- [ ] Зафиксировать convention в CONTRIBUTING.md

---

### ISSUE-6 · chore(monorepo): добавить монорепо-тулинг

**Проблема**  
Структура монорепо (`bossman-core/`, `bossman-infra/`, `bossman_shared/`, `command-center/`, `apps/*`) без инфраструктуры:
- Нет `pnpm workspaces` / `turborepo` / `nx`
- Нет корневого `package.json`
- Нет общей entry point

**Решение**
- [ ] Выбрать инструмент: `pnpm workspaces` (если JS) или `uv workspaces` (если Python)
- [ ] Добавить `workspace` конфиг в корень
- [ ] Добавить `Makefile` с командами `build`, `test`, `lint`

---

### ISSUE-7 · chore(branches): очистить stale protected ветки

**Проблема**  
50+ веток, все `protected`. Мёртвые ветки типа `fix/higgsfield-browser-fastpath-20260906` засоряют namespace.

**Затронутые ветки (примеры)**
- `fix/higgsfield-*` — после closure
- `fix/editor-*` — после merge
- `audit/*` — после intake
- `dependabot/*` — после merge PR #3, #5, #6

**Решение**
- [ ] Снять protection с закрытых веток
- [ ] Удалить ветки старше 14 дней после merge
- [ ] Добавить автоматическое удаление `head branch after merge` в настройках репо

---

### ISSUE-8 · chore(pr): закрыть или влить зависшие PR (>7 дней)

**Проблема**  
PR'ы зависают без action:

| PR | Открыт | Статус |
|----|--------|--------|
| [#2 Hard Reasoning V2](https://github.com/molotroka123-cell/AiMaxBossman/pull/2) | 30 авг | 🔴 висит |
| [#7 Roadmap](https://github.com/molotroka123-cell/AiMaxBossman/pull/7) | 5 сент | 🟡 |
| [#17 integration unified](https://github.com/molotroka123-cell/AiMaxBossman/pull/17) | 6 сент | 🟡 |
| [#26 V3-V5 residual closure](https://github.com/molotroka123-cell/AiMaxBossman/pull/26) | 6 сент | 🟡 |
| [#37 V5 closure](https://github.com/molotroka123-cell/AiMaxBossman/pull/37) | 6 сент | 🟡 |

**Решение**
- [ ] Каждый PR — review + decision: merge / close / convert to draft

---

## 🟡 УМЕРЕННО

### ISSUE-9 · chore(docs): очистить корень от debug/freeze отчётов

**Проблема**  
Корень репо захламлён постмортемами и debug-файлами:
- `AUTONOMY_TRAINER_FREEZE_REPORT.md`
- `FRESH_FREEZE_BASELINE.md`
- `RESTART_RESUME_PROOF.md`
- `CLAIMS_NOT_PROVEN.md`
- `ZIP3_INGEST_REPORT.md`
- `HANDOFF_STATE.md`

**Решение**
- [ ] Переместить в `docs/postmortems/`
- [ ] Или конвертировать в закрытые GitHub Issues
- [ ] Корень должен содержать только: `README.md`, `INSTALL.md`, `pyproject.toml`, скрипты запуска

---

### ISSUE-10 · chore(naming): унифицировать naming convention

**Проблема**  
Смешение `kebab-case` и `snake_case` в именах директорий:

| Директория | Стиль |
|-----------|-------|
| `bossman-core/` | kebab |
| `bossman-infra/` | kebab |
| `bossman_shared/` | snake |
| `solana_volume_suite/` | snake |
| `apps/solana-volume-suite/` | kebab |

**Решение**
- [ ] Выбрать один convention (рекомендация: `kebab-case`)
- [ ] Переименовать все директории
- [ ] Зафиксировать в `CONTRIBUTING.md`

---

### ISSUE-11 · chore(handoffs): устранить дублирование handoff-данных

**Проблема**  
Оба существуют:
- `HANDOFF_STATE.md` (файл в корне)
- `handoffs/` (директория)

Неясно, что canonical.

**Решение**
- [ ] Определить один источник истины для handoff-данных
- [ ] Удалить или перенести дублирующий файл

---

## 🔵 КОСМЕТИКА

### ISSUE-12 · chore(gitignore): обновить .gitignore

**Проблема**  
`.gitignore` не покрывает:
- `*.zip`
- `IMG_*.png`
- `.bossman-state/`

**Решение**
- [ ] Добавить все паттерны выше в `.gitignore`
- [ ] Запустить `git rm --cached` для уже отслеживаемых файлов

---

## 📊 Сводка приоритетов

| # | Задача | Приоритет | Сложность |
|---|--------|-----------|-----------|
| ISSUE-1 | Удалить ZIP из корня | 🔴 Критично | Средняя |
| ISSUE-2 | Влить security PR #1 | 🔴 Критично | Низкая |
| ISSUE-3 | Убрать IMG_3955.png | 🔴 Критично | Низкая |
| ISSUE-4 | Вынести `.bossman-state/` | 🔴 Критично | Высокая |
| ISSUE-5 | Дедуплицировать Solana dirs | 🟠 Серьёзно | Средняя |
| ISSUE-6 | Монорепо тулинг | 🟠 Серьёзно | Высокая |
| ISSUE-7 | Очистить stale ветки | 🟠 Серьёзно | Низкая |
| ISSUE-8 | Разобрать зависшие PR | 🟠 Серьёзно | Низкая |
| ISSUE-9 | Очистить корень от MD | 🟡 Умеренно | Низкая |
| ISSUE-10 | Унифицировать naming | 🟡 Умеренно | Средняя |
| ISSUE-11 | Дедуплицировать handoffs | 🟡 Умеренно | Низкая |
| ISSUE-12 | Обновить .gitignore | 🔵 Косметика | Низкая |

---

*Документ сгенерирован автоматически по результатам аудита репозитория 2026-09-07.*
