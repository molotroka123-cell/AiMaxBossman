# CYBERSEC AI V1 — ИТОГОВЫЙ ОТЧЁТ

```
START_SHA=afa106d
CODE_SHA=ca6e2b9                     (код + тесты + docs/security)
DOCS_SHA=7f5caab                     (README / CURRENT_STATE / этот отчёт)
ALL_COUNTERS_MEASURED_AT=7f5caab     (счётчики ниже относятся к этому дереву)
FINAL_SHA=e64250d                    (коммит, вписавший SHA выше; кода не трогал)

CYBERSEC_MODULES_CONNECTED=10/10
  01 Prompt Injection Firewall   bossman/cybersec/injection.py
  02 Agent Behavior IDS          bossman/cybersec/ids.py
  03 Secret Guardian             bossman/cybersec/secret_guardian.py
  04 Repo Security Scanner       bossman/cybersec/repo_scanner.py
  05 Sandbox / Blast Radius      bossman/cybersec/blast_radius.py
  06 Supply Chain Guardian       bossman/cybersec/supply_chain.py
  07 Cyber Recovery Mode         bossman/cybersec/recovery.py
  08 Security Memory             bossman/cybersec/security_memory.py
  09 Security Benchmark Lab      bossman/cybersec/benchmark.py
  10 AI Red-Team Lab             bossman/cybersec/redteam.py
  (+ служебные: gates, trust, defender, evidence, learning, training)

LAYERED_NOT_DUPLICATED=YES
  Второй Policy / Approval / Gateway / Tool Registry / Secret Store / EventBus /
  Memory НЕ создан. Карта «модуль → усиленный авторитет» — cybersec.LAYERED_OVER.
  security_memory пишет в каноничную failure_memory (Postgres), своего стора нет.
  secret_guardian ре-экспортирует obs.redact — второго скраббера нет (тест).

DEFAULT_STATE=OFF (BOSSMAN_CYBERSEC_V1_ENABLED != 1)
TRAINING_ENGINE_FROZEN=YES
  Тройной гейт (2 env + ACK-строка) + SandboxFacts(disposable, no prod secrets,
  no prod network). Умолчания SandboxFacts небезопасны → SandboxFacts() отклоняется.
STRESS_TEST_EXECUTED=NO (по требованию: подготовить и заморозить)

RED_BOUNDARY_ENFORCED=YES
  AttackIntent.validate() режет 17 запрещённых ключей (command/shell/cmd/payload/
  executable/binary/socket_target/credential/secret/token/api_key/production_host/
  network_target/argv/...), сравнение по lower().
DIFFICULTY_DOES_NOT_GRANT_PERMISSIONS=YES
  permissions_for_level(L) == frozenset() для всех L0..L5 (тест).

LEARNING_PIPELINE=PROPOSED -> BENCHMARKED -> SHADOW -> VERIFIED -> PROMOTED
AUTO_PROMOTION_TO_PRODUCTION=NO (promote требует owner_approved=True И stage>=VERIFIED)

SECURITY_TESTS=PASS
  CyberSec V1: 79 passed (unit + интеграция + структурные регрессии)
  Security-срез core (security/perimeter/approval/scope/permission/injection/secret): 248 passed
  Security-срез command-center: 78 passed

REGRESSION=PASS
  bossman-core без Postgres:          1072 passed, 10 skipped, 0 failed
  bossman-core с живым PostgreSQL 16.13: 1077 passed, 5 skipped, 0 failed
  test_pg_memory_gate.py (живой PG):     5 passed
  command-center:                      610 passed, 2 skipped, 0 failed
  tools/ci_secret_scan.py:             PASS

OFFLINE_BASELINE=14/14 сценариев каталога сдержаны, containment_rate=1.0,
  SecurityScore.passing=True. Это офлайн-база детерминированного защитника,
  НЕ результат живого стресс-теста.

NEW_P0=0
NEW_P1=1 (найден и закрыт — см. ниже)
OPEN_BLOCKERS=нет блокеров для заморозки; для реального стресс-теста нужен
  owner hardware и одноразовая песочница.

READY_FOR_FUTURE_RED_BLUE_STRESS_TEST=YES (подготовлено и заморожено)
```

## P1, найденный и закрытый в этом проходе
**P1-FLAKY — `tests/test_telegram_callbacks.py::test_callback_is_opaque_bound_and_single_use`.**
Тест утверждал `assert "42" not in token`, где `token = secrets.token_urlsafe(18)`
(24 символа из алфавита в 64 знака). Двухсимвольная подстрока «42» попадается
случайно примерно в 0.5% прогонов — тест падал без всякой связи с дефектом кода
(воспроизведено: один прогон полного набора упал, два следующих прошли).
Тест **не отключён и не пропущен**: цель заменена на длинный характерный
идентификатор, так что проверяется ровно то же свойство (токен не раскрывает
target_id), но детерминированно.

## Дефекты эталонного ZIP, исправленные при интеграции
Полный список из 12 пунктов с объяснением — `docs/security/CYBERSEC_V1_ZIP_DELTA.md`.
Самое существенное: `eligible_for_shadow` в эталоне вычислялся как «действие
защиты входит в список ВСЕХ возможных действий», то есть всегда `True` —
любой эпизод немедленно становился кандидатом на обучение (fake green).

## AUTONOMOUS_ENGINEERING_DECISIONS

### AED-5 — Улики редактируются по значениям, а не по тексту JSON
OLD (эталон): `json.loads(redact(json.dumps(records)))` — редакция регуляркой по
сериализованному JSON, затем обратный парсинг.
NEW: `obs.redact_obj(records)` по значениям, JSON собирается уже из очищенного объекта.
WHY: секрет, содержащий кавычку или скобку, ломал JSON → исключение при записи →
**потеря улик** ровно в том эпизоде, где они важнее всего.
EVIDENCE: `test_secrets_in_attack_text_never_reach_the_evidence_file` — токен
`sk-live-…` отсутствует в `episode.json`.
SPEED: обход объекта вместо двух сериализаций — не медленнее.
QUALITY: запись улик больше не зависит от содержимого секрета.
SECURITY: + (нет пути, где падение редактора теряет улики).
ROLLBACK: revert коммита.

### AED-6 — Небезопасные умолчания у SandboxFacts (fail-closed)
OLD (эталон): поля `SandboxFacts` без умолчаний — безопасность зависит от того,
что вызывающий их аккуратно заполнит.
NEW: умолчания = самые небезопасные значения (`is_disposable=False`, секреты
смонтированы, сеть разрешена), поэтому `SandboxFacts()` по умолчанию **отклоняется**.
WHY: забытый аргумент должен закрывать лабораторию, а не открывать её.
EVIDENCE: `test_lab_requires_all_three_gates_and_sandbox`,
`test_engine_stays_frozen_in_a_non_disposable_sandbox`.
SECURITY: + (ошибка вызывающего больше не открывает лабораторию). ROLLBACK: revert.

### AED-7 — Каталог сценариев в коде, уровень выводится из карты уровней
OLD (эталон): `scenarios/catalog.json` из 12 записей со своим полем `difficulty`,
ни на что не влиявшим; карта уровней жила отдельно и могла разойтись с каталогом.
NEW: `redteam.CATALOG` из 14 типизированных шаблонов; `ScenarioTemplate.level`
**выводится** из `LEVELS`, дублирующего поля нет.
WHY: два места, где записан уровень сценария, — два источника правды.
EVIDENCE: `test_catalog_level_matches_level_map`, `test_catalog_covers_every_attack_class`.
QUALITY: расхождение каталога и уровней структурно невозможно. ROLLBACK: revert.

### AED-8 — REJECT_AND_CONTINUE считается сдерживанием
OLD: malformed input падал в `SANDBOX_AND_OBSERVE` («понаблюдаем») и в бенчмарке
считался НЕ сдержанным, из-за чего каталог не мог пройти собственный гейт.
NEW: отдельное действие `REJECT_AND_CONTINUE` — ввод отброшен на границе и в
контекст не попал; это сдерживание.
WHY: «наблюдать за некорректным вводом» — неверная модель; корректная реакция —
отвергнуть и продолжить.
EVIDENCE: `test_whole_catalog_is_contained_and_passes_the_gate` (14/14, gate PASS).
ROLLBACK: revert.

### AED-9 — Структурные запреты проверяются AST, а не поиском подстрок
OLD (первая версия моего же теста): поиск подстрок `subprocess/socket/requests/…`
по исходникам. `secret_requests=1` ложно срабатывал как «сетевая библиотека».
NEW: обход AST — запрещённые импорты, вызовы `eval/exec/compile/__import__`,
атрибуты `.system()/.popen()`, аргумент `shell=`.
WHY: подстрочный линтер даёт ложные срабатывания и провоцирует ослабить правило.
EVIDENCE: `test_cybersec_layer_has_no_shell_or_network_primitives` зелёный и при
этом реально ловит подделку (проверено на `re.compile` — не путается).
ROLLBACK: revert.

### AED-10 — Флаки-тест починен, а не отключён
См. раздел P1 выше. Отключение/скип теста запрещено правилами; исправлен сам
источник недетерминизма в утверждении.
