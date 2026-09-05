# ZIP-артефакты в корне репозитория — классификация (BOSS-FLEET-INTEGRATION-001 §28)

Правило: владельческие артефакты не удаляются молча. Ниже — рекомендация; удаление/архивация — решение владельца
(протокол «бинарный карантин» из мастер-промпта запрещает новые ZIP в ветках разработки, но не предписывает
удалять уже загруженные).

| Файл | Статус после интеграции | Рекомендация |
|---|---|---|
| `AiMaxBossman_ASTRA_Fixes_35390ec.zip` | отчёт и payload проверены; исправления памяти/контекста подтверждены чтением кода и прогоном тестов, разбор — `docs/v3/CODEX_REALITY_MERGE_ANALYSIS.md` | **REDUNDANT_AFTER_INTEGRATION** (полностью заменён версией `(1)`) |
| `AiMaxBossman_ASTRA_Fixes_35390ec(1).zip` | то же плюс повторный аудит; установщик и VERIFY.py не запускались — слияние выполнялось адресно | **ARCHIVE_CANDIDATE** (источник; после архивации в релиз-теге удаляется) |
| `AiMaxBossman_Organization_Layer_DropIn.zip` (v1) | исходники полностью заменены `bossman_v3/organization`; v2 содержит всё, что было в v1 | **REDUNDANT_AFTER_INTEGRATION** |
| `AiMaxBossman_Organization_Layer_DropIn_v2.zip` | интегрирован (adoption table в `docs/v3/organization/ARCHITECTURE.md`); уникальный текст `FABLE_10_INNOVATIONS.md` скопирован в docs | **ARCHIVE_CANDIDATE** (источник ТЗ; после архивации в релиз-теге можно удалить) |
| `AiMaxBossman_Fleet_OS_Complete_Foundation_DropIn.zip` | интегрирован (adoption table в `docs/v3/fleet/ARCHITECTURE.md`); `FLEET_10_INNOVATIONS.md` скопирован | **ARCHIVE_CANDIDATE** |
| `AiMaxBossman_Benchmark_Overlay_DropIn_v2.zip` | интегрирован в `bossman_v3/benchmark_overlay` (adoption table: `docs/benchmark/BENCHMARK_OVERLAY.md`; до этого коммита запись была преждевременной — пакета не существовало) | **ARCHIVE_CANDIDATE** |
| `AiMaxBossman_V3_7Pack.zip` | источник `bossman_v3/*` семи модулей (интегрированы ранее) | ARCHIVE_CANDIDATE |
| `BOSSMAN_V3_AUTONOMOUS_OPERATOR_EXPANDED_FULL_PACK.zip` | относится к стадии Autonomous Operations — **не начата** по мандату | KEEP_SOURCE_ARTIFACT |
| `AiMaxBossman_V1_FINAL_ACCEPTANCE_PACK_2026-08-30.zip`, `BOSSMAN_ZIP_LEVEL_3_FINAL_REPACKED.zip`, `BOSSMAN_META_INTELLIGENCE_7_MODULES_PASS3_FINAL.zip` | исторические приёмочные паки | ARCHIVE_CANDIDATE |
| `BOSSMAN_5_APPS_V1_FINAL_CODE_PACK.zip`, `BOSSMAN_APP_ICON_PACK.zip`, `BOSSMAN_SOCIAL_FARM_APP4_TECH_SPEC_V1_1.zip`, `BOSSMAN_UX_2_PROMPT_PACK_REBUILT.zip` | продуктовые/дизайн-паки, вне V3 | KEEP_SOURCE_ARTIFACT (решение владельца) |
| `BOSSMAN_FABLE_NEXT_SESSION_PACK.zip`, `_V2.zip`, `BOSSMAN_PROMPT_CACHE_OPUS5_DROPIN.zip` | сессионные промпт-паки | ARCHIVE_CANDIDATE |
| `Bossman_CyberSec_Training_Engine_V1.zip`, `self_learning_orchestrator.zip` | отдельные подсистемы; есть ветки `claude/self-learning-orchestrator` | KEEP_SOURCE_ARTIFACT |

Предложение владельцу: один коммит `chore(repo): move drop-in ZIPs to release assets` после выпуска тега — перенести
ARCHIVE_CANDIDATE в GitHub Release assets, удалить из дерева. Ни один из них не нужен тестам или CI
(`tools/ci_secret_scan.py` пропускает `.zip`).

## Состояние на 2026-09-05 (мини-чистка)

В корне 21 архив, 3.9 МБ. Правило репозитория — «владельческие артефакты не
удаляются молча» — соблюдено: ни один файл не удалён, решение остаётся за
владельцем. Механически закрыт только вход: `*.zip` в `.gitignore`, поэтому
новые архивы больше не попадут в коммит случайно.

**Удалять НЕЛЬЗЯ, на них ссылается код:**

- `docs/audits/astra-7b1377a/AIMAXBOSSMAN_ASTRA_FULL_AUDIT_7b1377a.zip` —
  `tools/ci_secret_scan.py` держит на него закреплённый хеш;
- `BOSSMAN_SOCIAL_FARM_APP4_TECH_SPEC_V1_1.zip` —
  `apps/social-farm/tests/unit/test_browser_selectors.py:21`.

Остальные 19 упоминаются только этим каталогом, то есть удаление ничего не
сломает; содержимое остаётся в истории git и достаётся `git show <sha>:<файл>`.

**Ветки.** На удалённом 24 ветки. Полностью влита в рабочую ровно одна —
`claude/v3-memory-context-kernel`, её можно удалять безопасно. Остальные 22
содержат неслитые коммиты: пять активных (`codex/*`, `feat/web-designer-live-panel`,
рабочая), три dependabot и четырнадцать августовских лент аудита. Удаление
неслитой ветки теряет работу, поэтому список только назван — чистку веток
делает владелец.
