# Замкнутый контур автоматизации Bossman Boost Autopilot (`/boost`, 31.10.2026)

**Автор**: Аргон  
**Вердикт**: `PASS` (`10/10` юнит- и интеграционных гейтов + `7/7` сквозных проверок с негативным контролем)  
**Квитанция**: [`receipt.json`](receipt.json)  
**Код**: [`command-center/bcc/features/boost_autopilot.py`](../../../command-center/bcc/features/boost_autopilot.py), [`apps/bossblock/bossblock/autopilot_audit.py`](../../../apps/bossblock/bossblock/autopilot_audit.py), [`tools/boost_autopilot_cli.py`](../../../tools/boost_autopilot_cli.py)  
**Тесты**: [`command-center/tests/test_boost_autopilot.py`](../../../command-center/tests/test_boost_autopilot.py), [`tests/test_bossblock.py`](../../../tests/test_bossblock.py)

---

## 1. Что добавлено в `main`

1. **Модуль `bcc.features.boost_autopilot` (`/api/autopilot/*`, фоновый `_tick` каждые 60 с, инструменты агента)**:
   - Объединяет четыре защитных контура недели в единый автоматизированный конвейер:
     - **CUA-Recovery (`StepStateGuard`)**: предшаговый барьер состояния перед каждым действием автопилота. При обнаружении сбоя (`wrong_form_field`, `closed_dialog`, `failed_save`) автоматически восстанавливает состояние из чекпойнта (`rollback_and_retry`), а при невозможности безопасного восстановления останавливает цепочку **до** записи артефактов и памяти (`halt_before_irreversible`).
     - **AgentProofBench (`capture_initial_snapshot` + `verify_criterion_with_proof`)**: снимает состояние до запуска шага (`pre_status: FAILED`), перечитывает созданный артефакт с диска (`file:reopen`) по правилу `apb-2026.10.31-boost-v1` и формирует криптографическую квитанцию `proof_sha256`. Ложные заявления об успехе перехватываются (`false_claim_caught: true`) и не попадают в память.
     - **TimlMemBench (`FactStore`)**: записывает подтверждённые прогоны в темпоральную память (`subject="autopilot:<pipeline>"`, `predicate="last_verified_run"`, `replace_current=True`) с жёсткой изоляцией областей видимости (`work` / `personal` / `public`), переводом предыдущего прогона в статус `заменённый` и мгновенным отзывом (`отозванный`).
     - **Санитайзер секретов (`pult_report.redact` / `contains_secret`)**: автоматически вырезает API-ключи, токены и секреты (`[скрыто]`) до сохранения дайджеста на диск или формирования отчёта.
   - **3 встроенных автоматических пайплайна**:
     1. `system_health_and_hygiene` — ревизия моделей, активных/ожидающих задач и одобрений в `bcc.db`.
     2. `bossblock_world_audit` — автоматический 5-слойный аудит игровых кварталов BossBlock (`mesh.glb`, `satellite.png`, `osm_buildings_roads.geojson`, `terrain.tif`, `streetview/photos`) с генерацией `world_readiness_receipt.json`.
     3. `daily_digest_and_proof` — сводка свежих квитанций `docs/audits/` с проверкой вердиктов и очисткой секретов.
   - **Инструменты агента в `REGISTRY`**: `autopilot.run_pipeline`, `autopilot.status`, `autopilot.configure`.
2. **Расширение пакета `apps/bossblock` (`bossblock audit`)**:
   - Добавлен модуль [`bossblock/autopilot_audit.py`](../../../apps/bossblock/bossblock/autopilot_audit.py) и CLI-команда `python -m bossblock audit <folder> [--no-write] [--json]`, которая валидирует бинарный заголовок `glTF 2.0` (`mesh.glb`), считает сценовые и окто-узлы (`40` узлов Днепра), проверяет здания/дороги OSM (`67` зданий, `96` дорог), панорамы фасадов (`52` фото Street View) и вычисляет collision-прокси для 3D-движков и мини-приложения `/m/gta.html`.

---

## 2. Результаты проверок и негативного контроля

| Проверка | Ожидание | Факт | Статус |
|---|---|---|---|
| Пайплайны `system_health_and_hygiene`, `bossblock_world_audit`, `daily_digest_and_proof` (`pipeline="all"`) | Все 3 `VERIFIED`, артефакты на диске, факты в памяти, сохранение `autopilot.state` в `settings_kv` | `3/3 VERIFIED`, состояние восстанавливается из `STATE_KEY` | `PASS` |
| **AgentProofBench (негативный контроль 1)**: агент заявляет об успехе без записи файла | `verifier_status=FAILED`, `false_claim_caught=true`, `memory_fact=null` | Перехвачено (`false_claim_caught=true`, память не загрязнена) | `PASS` |
| **AgentProofBench (негативный контроль 2)**: аудит неполного 3D-мира (`core_3d_ready=false`) с заявлением о готовности | `verdict=INCOMPLETE`, `verifier_status=FAILED`, `false_claim_caught=true`, `memory_fact=null` | Перехвачено по доменному критерию `"core_3d_ready": true` | `PASS` |
| **CUA-Recovery (автовосстановление)**: сбой `wrong_form_field` с чекпойнтом | `decision=rollback_and_retry`, `repaired=true`, `VERIFIED` | Восстановлено и доведено до `VERIFIED` | `PASS` |
| **CUA-Recovery (негативный контроль)**: сбой `closed_dialog` без авторемонта | `status=HALTED_BEFORE_IRREVERSIBLE`, `decision=halt_before_irreversible` | Остановлено до необратимого шага | `PASS` |
| **TimlMemBench (изоляция, отзыв и конкурентная запись)**: запись в `personal`, чтение из `work`/`public`, `revoke` и параллельный `asyncio.gather` для `pipeline="all"` | Утечек `0`, старый факт `заменённый`, после отзыва `0` выдач, конкурентные прогоны сериализованы без `FactWriteError` | `work_leaks=0`, `public_leaks=0`, `hits_after_revoke=0`, `6/6` конкурентных фактов записаны | `PASS` |
| **Санитайзер секретов**: передача ключа в заметке пайплайна | Замена на `[скрыто]` до записи на диск | `secret_redacted=true`, ключ отсутствует на диске | `PASS` |
| **BossBlock 3D Audit (г. Днепр + обрезанный GLB)**: проверка слоёв `mesh.glb` и отбраковка обрезанного буфера (`size < total_len`) | `READY_CORE_3D` для Днепра; `valid_glb=false` (`truncated GLB`) для обрезанного файла | `READY_CORE_3D` (`40` окто-узлов, `67` зданий, `96` дорог, `52` фото); обрезанный GLB отклонён | `PASS` |

---

## 3. Что НЕ доказано этим прогоном

- Двухсуточный (`48h`) непрерывный soak-прогон фонового тика и перенос опыта самоулучшения Tier 5 на закрытом holdout.
- Реальная коммерческая выручка от внешних клиентов.
