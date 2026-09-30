# REPO HYGIENE REPORT — POLISH Wave 1

Ветка: `claude/bossman-control-v03-43igbk`. Метод: инвентаризация корня →
классификация каждого объекта → **доказательство отсутствия уникальной копии
кода/спеки/теста/архитектурного решения ПЕРЕД удалением** → git mv/git rm.
Ничего уникального не удалено; все перемещения обратимы через git history.

## Классификация корня

| Объект | Класс | Действие | Обоснование |
|---|---|---|---|
| `README.md` | KEEP | оставлен в корне | точка входа; добавлена ссылка на `docs/context/CURRENT_STATE.md` |
| `INSTALL.md` | KEEP | оставлен в корне | инструкция установки, актуальна |
| `99_BEFORE_CLAUDE_TESTS.md` | MOVE_TO_DOCS_ARCHIVE | `git mv → docs/archive/` | исторический pre-test лог |
| `AUDIT_NEW_COMMITS_2026-08-29.md` | MOVE_TO_DOCS_ARCHIVE | → `docs/archive/` | датированный аудит-снимок |
| `FINAL_PRE_RC_BUG_HUNT.md` | MOVE_TO_DOCS_ARCHIVE | → `docs/archive/` | отчёт прошлой фазы (RC) |
| `FINAL_SUMMARY.md` | MOVE_TO_DOCS_ARCHIVE | → `docs/archive/` | сводка прошлой фазы |
| `PYTHIA_WORLD_INTELLIGENCE_INTEGRATION_REPORT.md` | MOVE_TO_DOCS_ARCHIVE | → `docs/archive/` | отчёт интеграции Pythia (код уже в дереве) |
| `RC_TEST_B_INTERMEDIATE_2026-08-30.md` | MOVE_TO_DOCS_ARCHIVE | → `docs/archive/` | промежуточный RC-лог |
| `STATIC_CODE_AUDIT.md` | MOVE_TO_DOCS_ARCHIVE | → `docs/archive/` | статический аудит прошлой фазы |
| `ГПТ от GLM-5.3/` | MOVE_TO_DOCS_ARCHIVE | → `docs/archive/handoffs/GLM-5.3/` | foreign-model handoff: только status/worklog MD, кода нет |
| `ГПТ от muse-spark-1.2/` | MOVE_TO_DOCS_ARCHIVE | → `docs/archive/handoffs/muse-spark-1.2/` | foreign-model handoff: только status/audit MD, кода нет |
| `BOSSMAN_POLISH_PHASE_V1.zip` | DELETE_INTEGRATED_ARTIFACT | `git rm` | transfer-пак этой фазы; все требования интегрированы (мастер-промт применён, backlog отработан); `.gitignore` уже исключает `/*.zip` |
| `BOSSMAN_SOCIAL_FARM_APP4_TECH_SPEC_V1_1.zip` | REVIEW_UNIQUE / KEEP | оставлен | ТЗ отдельного будущего приложения (App4); НЕ интегрировано в Bossman → удаление потеряло бы уникальную спеку |
| прочие `*.zip` в корне | UNTRACKED | не трогаю | не в индексе git (`.gitignore /*.zip`); локальные копии, в коммит не попадают |
| `_staging/`, `.bossman-v2-pack/` | UNTRACKED | не трогаю | не отслеживаются git |
| `.bossman-state/`, `.agents/`, `.claude/`, `.github/` | KEEP | не трогаю | рабочие/служебные каталоги |

## Доказательства «нет уникальной копии» перед удалением
- `BOSSMAN_POLISH_PHASE_V1.zip`: содержимое (00_AUDIT, 01_BACKLOG, 02_MASTER_PROMPT,
  03_EVENING_ACCEPTANCE, 04_REFERENCE_GITHUB_CROSSCHECK, README, INVENTORY.json) —
  это инструкции фазы, а не код/тесты. Требования отражены в коде и в отчётах
  `docs/context/*` (POLISH_*). Артефакт-передатчик, уникального содержимого-продукта нет.
- Исторические MD не удалены, а перемещены (git mv) — полностью восстановимы.
- Foreign handoff-каталоги содержат только markdown-статусы; исходников/тестов нет
  (проверено `git ls-files` — 7 и 8 .md-файлов соответственно).

## Итог корня после хайджина
Корень: `README.md`, `INSTALL.md` + рабочие каталоги
(`bossman-core/`, `command-center/`, `bossman-infra/`, `apps/`, `docs/`, `tools/`, `data/`)
+ один уникальный будущий-app spec zip. Конкурирующих исторических MD в корне нет.
Канонический статус — `docs/context/CURRENT_STATE.md`, на него ссылается README.
