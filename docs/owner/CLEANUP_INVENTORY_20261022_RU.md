# Инвентаризация уборки — 22.10.2026

Метод: `git ls-files` (7660 файлов, ~192 МБ), поиск ссылок по всему дереву (код, тесты, CI, скрипты, документы; `docs/testing/sessions/*.jsonl` не читались). Правило: переносим только при **0 ссылок из кода/тестов/CI/скриптов**; удаляем только регенерируемое. Предыдущая уборка бинарников — `docs/REPOSITORY_ARTIFACT_CLEANUP.md`; кэшей, `build/`, `*.egg-info`, `*.pyc` в индексе нет (и так в `.gitignore`).

## Сделано (коммит `ce2367b1`)

| Действие | Что | Проверка ссылок |
|---|---|---|
| DELETE (из индекса) | `data/learning/.lock` (0 байт, рантайм-замок; `learning/trace.py` создаёт его `open(..., "a+b")` по требованию) | кода-читателя нет; добавлено в `.gitignore` |
| ARCHIVE | 6 корневых файлов: `BOSSMAN_TOMORROW_2026-09-24_START_HERE.md`, `README_PRE_CONVERGENCE_6cb.md`, `README_GUIDE_20260918.md`, `BUG_MAP.md`, `KEY_FUNCTION_TOUCH_MATRIX.md`, `RESTART_RESUME_PROOF.md` → `docs/archive/2026-09/` | код/CI/тесты: 0; входящие ссылки в Markdown переписаны |
| ARCHIVE | 11 файлов `docs/owner/` (TOMORROW_CHECKLIST, TOMORROW_MASTER_PROMPT_QWEN_FIRST, CONTINUATION_PROMPT_20260929/30, HANDOFF_20260930_CHECKPOINT/EVENING, HANDOFF_20261001, MASTER_PROMPT_OWNER_RUN_20261001, BOSSMAN_2_0_CLOSEOUT_MASTER_PROMPTS_20261002, CURRENT_CLOSEOUT_20261011_RU, WHERE_WE_STOPPED_20261011) → `docs/archive/2026-09/owner/`, `docs/archive/2026-10/owner/` | код/CI/тесты: 0 |
| IGNORE | `/X`, `/X/`, `*.tmp`, `.ruff_cache/`, `.mypy_cache/` | в индексе совпадений нет |

Перенесено 236 158 байт (17 файлов); удалено 0 байт. **Размер репозитория не уменьшился**: уборка про корень и ясность, а не про вес. Индекс: [`docs/archive/INDEX_RU.md`](../archive/INDEX_RU.md). Новый инструмент: `tools/docs_link_check.py` (относительные ссылки; базовая линия до уборки — 157 битых ссылок в 1133 `.md`, после — 157, новых нет).

## Оставлено намеренно (KEEP) и почему

- `START_TOMORROW_RU.md` — ссылки из `tools/owner_run_tomorrow.py`, `tools/build_windows_bundle.py`, `.github/workflows/windows-bundle.yml`, тестов.
- `BOSSMAN_SOCIAL_FARM_APP4_TECH_SPEC_V1_1.zip` — вход тестов `apps/social-farm/tests/unit/test_browser_*`.
- `docs/owner/TOMORROW_OPERATOR_RUNBOOK.md` — на него ссылается `bossman_critical_errors.json` (читается тестами и `tools/import_operational_lessons.py`).
- `docs/testing/sessions/*.jsonl` (19,8 МБ) — читаются кодом, тестами и документами как улики; **при уборке не открывались; интегратору: проверить на персональные данные до публикации**.
- `docs/evidence/north-star-20261010/*.log` и 0-байтные `*.err.log` — часть цитируемого набора улик.
- `docs/constitution/proposals/*` — побайтово равны принятой конституции; не трогать (пин конституции).
- Снимки-инвентари (`integration-backlog.json` и др.) не переписывались: пути в них даны на момент снимка.
- Дубли `LICENSE` и схемы `schemas/autonomy` и `command-center/bcc/autonomy/schema_data` — используются.

## Откат

- Всё сразу: `git revert ce2367b1`.
- Один файл: `git checkout ce2367b1~1 -- <старый путь>` (например `BUG_MAP.md`).
- Замок (не обязателен): `git checkout ce2367b1~1 -- data/learning/.lock`.
