# Архив документации — индекс

Что это: исторические документы, которые **перенесены, а не удалены** при уборке 22.10.2026
(`git mv`, история файла сохранена; побайтово восстанавливается из git, рецепт — в
[`docs/owner/CLEANUP_INVENTORY_20261022_RU.md`](../owner/CLEANUP_INVENTORY_20261022_RU.md)).
Ничто здесь не является текущим указанием: всё, что написано в этих файлах про
«завтра», «сейчас» и «готово», относится к дате файла. Действующий вход —
[`docs/owner/TOMORROW_NORTH_STAR_TIER5_RUN_20261022_RU.md`](../owner/TOMORROW_NORTH_STAR_TIER5_RUN_20261022_RU.md).

Правило архива: перенос только при нуле ссылок из кода, тестов, CI и скриптов; все входящие
ссылки в Markdown переписаны на новые пути. Снимки-инвентари (`integration-backlog.json` и др.)
намеренно не переписывались: они фиксируют пути на момент снимка.

## 2026-09 (корень репозитория)

| Файл | Дата | Одна строка |
|---|---|---|
| [BOSSMAN_TOMORROW_2026-09-24_START_HERE.md](2026-09/BOSSMAN_TOMORROW_2026-09-24_START_HERE.md) | 24.09 | одноразовая «точка входа на завтра» 24.09; подробности остались в `docs/tomorrow-2026-09-24/` |
| [README_PRE_CONVERGENCE_6cb.md](2026-09/README_PRE_CONVERGENCE_6cb.md) | 18.09 | прежний README до схождения (V8, `6cb…`) |
| [README_GUIDE_20260918.md](2026-09/README_GUIDE_20260918.md) | 18.09 | расширенный README V8 с витринными картинками (картинки `docs/v8/readme-visuals/` в дереве отсутствуют) |
| [BUG_MAP.md](2026-09/BUG_MAP.md) | 02.09 | карта дефектов миссии LONGHORIZON-FREEZE-001 |
| [KEY_FUNCTION_TOUCH_MATRIX.md](2026-09/KEY_FUNCTION_TOUCH_MATRIX.md) | 02.09 | матрица проверок ключевых функций той же миссии |
| [RESTART_RESUME_PROOF.md](2026-09/RESTART_RESUME_PROOF.md) | 02.09 | доказательство рестарта менеджера той же миссии |

## 2026-09 (`docs/owner`)

| Файл | Дата | Одна строка |
|---|---|---|
| [owner/TOMORROW_CHECKLIST.md](2026-09/owner/TOMORROW_CHECKLIST.md) | 21.09 | короткий чек-лист прогона Bossman 1.0-RC |
| [owner/TOMORROW_MASTER_PROMPT_QWEN_FIRST.md](2026-09/owner/TOMORROW_MASTER_PROMPT_QWEN_FIRST.md) | 22.09 | мастер-промпт «Qwen-first» для закрытия 1.0 |
| [owner/CONTINUATION_PROMPT_20260929.md](2026-09/owner/CONTINUATION_PROMPT_20260929.md) | 29.09 | промпт продолжения на конец дня 29.09 |
| [owner/CONTINUATION_PROMPT_20260930.md](2026-09/owner/CONTINUATION_PROMPT_20260930.md) | 30.09 | промпт продолжения после облачного чекпоинта 30.09 |
| [owner/HANDOFF_20260930_CHECKPOINT.md](2026-09/owner/HANDOFF_20260930_CHECKPOINT.md) | 30.09 | handoff чекпоинта Bossman 1.9 |
| [owner/HANDOFF_20260930_EVENING.md](2026-09/owner/HANDOFF_20260930_EVENING.md) | 30.09 | handoff вечера 30.09 (1.9 → 2.0) |

## 2026-10 (`docs/owner`)

| Файл | Дата | Одна строка |
|---|---|---|
| [owner/MASTER_PROMPT_OWNER_RUN_20261001.md](2026-10/owner/MASTER_PROMPT_OWNER_RUN_20261001.md) | 30.09–01.10 | мастер-промпт прогона 1.9 «баг-тест» |
| [owner/HANDOFF_20261001.md](2026-10/owner/HANDOFF_20261001.md) | 01.10 | передача прогона 01.10 (PR #89, Bossman 2.0 / Jeff 2.0) |
| [owner/BOSSMAN_2_0_CLOSEOUT_MASTER_PROMPTS_20261002.md](2026-10/owner/BOSSMAN_2_0_CLOSEOUT_MASTER_PROMPTS_20261002.md) | 02.10 | мастер-промпты закрытия Bossman 2.0 |
| [owner/WHERE_WE_STOPPED_20261011.md](2026-10/owner/WHERE_WE_STOPPED_20261011.md) | 11.10 | «где мы остановились» на 11.10 (MP21) |
| [owner/CURRENT_CLOSEOUT_20261011_RU.md](2026-10/owner/CURRENT_CLOSEOUT_20261011_RU.md) | 11–12.10 | срез завершения 11.10; SHA в нём заменены `FINAL_CLOSE_20261012_RU.md` |

## Ранее заархивированное (до 22.10)

| Каталог | Что внутри |
|---|---|
| [owner-preflight-05a1bb02/](owner-preflight-05a1bb02/README.md) | README/INSTALL/handoff до подготовки владельческого прогона 21.09 (тот же blob SHA) |
| [root-history/](root-history/) | исторические корневые отчёты (`AUDIT_STATE`, `HANDOFF_STATE`, `RED_TEAM_HANDOFF` и др.) |

См. также: `docs/REPOSITORY_ARTIFACT_CLEANUP.md` (уборка бинарных архивов из корня, раньше).
