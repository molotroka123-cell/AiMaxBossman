# Мини-промпт для следующей сессии (закрытие аудита 10×10)

```
Роль: исполнитель ТЗ по независимому аудиту AiMaxBossman.
Репозиторий: molotroka123-cell/AiMaxBossman, ветка claude/bossman-control-v03-43igbk. Сначала `git fetch` и работай от remote HEAD.

Прочитай:
  docs/audit/2026-09-05_AUDITOR_SCORECARD_10x10.md   (оценки, находки EH/SEC/TL/ORG/FL/MEM/CI/OBS/TR/UX, инварианты INV-1..6)
  docs/audit/tz/TZ-01 … TZ-10                         (требования MUST/SHOULD, математика, приёмка, чек-листы)

Порядок (строго): TZ-09 §2 → TZ-05 §2 → TZ-01 §2.1–2.3 → TZ-04 §2–§4 → TZ-08 §2.1,2.2,2.5 → TZ-02 §2.1–2.3 + TZ-07 §2.1,2.5 → TZ-06 → TZ-03 → TZ-10.
Причина порядка: сначала P0 (цены/токены, fencing), затем инварианты честности, затем продуктовые слои.

Правила:
  1. Один коммит = одно ТЗ (или его пронумерованный раздел); в сообщении коммита — ID находок (например «TR-01 TR-02 TR-03»).
  2. Каждый коммит содержит тесты из раздела «Приёмка» соответствующего ТЗ; без них ТЗ не считается закрытым.
  3. Инварианты INV-1..INV-6 не ослаблять. Fail-closed везде: неизвестное = отказ.
  4. V3 BOUNDARY соблюдать: Visual State Engine, Universal Computer Agent, Fleet Mode сверх TZ-05, Self-Healing expansion — не начинать.
  5. Регрессия дёшево: targeted → группа Command Center → один полный прогон в конце; exact-SHA CI обязателен, включая новый windows-job (TZ-07 §2.5).
  6. Секрет-скан на каждом коммите; никаких секретов/CoT в логах и доках.
  7. После каждого закрытого ТЗ обнови колонку Grounded в таблице scorecard и поставь галочки в чек-листе ТЗ.
  8. Финальный отчёт — поля: START_SHA, FINAL_SHA, COMMITS_CREATED, по каждому TZ: CLOSED/PARTIAL/OPEN + список ID,
     COMMAND_CENTER_REGRESSION, ROOT_REGRESSION, WINDOWS_CI, SECRET_SCAN, OPEN_P0, OPEN_P1, SCORE_AFTER (10 чисел), FREEZE_READY.
     FREEZE_READY=YES только при OPEN_P0=0 и всех ТЗ ≥ 9 по чек-листу.
```
