# CyberSec V1 — что взято из эталонного ZIP и что пришлось починить

Источник: `Bossman_CyberSec_Training_Engine_V1.zip` (standalone-пакет
`bossman_cyber_training`, 9 python-модулей, 9 тестов). Пакет **не** коммитился в
репозиторий — в репо попал только адаптированный код и отчёты.

Пакет использован как **контракт и словарь** (классы атак, тройной гейт, порядок
доверия, L0–L5, «обучение только предложением»). Как реализация он не переносился:
ниже перечислено, что в нём не работало или отсутствовало.

## Взято без изменений (идея/контракт)
* три env-гейта + `SandboxFacts` (disposable / no prod secrets / no prod network);
* порядок доверия `owner policy > signed internal > verified tool > trusted repo > external > unknown`;
* типизированный `AttackIntent` вместо исполняемой нагрузки, список запрещённых полей;
* «уровень сложности не меняет права»;
* «эпизод → улики → предложение → benchmark/shadow → владелец».

## Дефекты эталона, исправленные при интеграции

| # | Где в ZIP | Дефект | Как сделано здесь |
|---|---|---|---|
| D1 | `learning.propose_learning` + `engine.run` | `verified = action in {все 4 возможных действия}` → **всегда True**, а `eligible_for_shadow = verified`. Любой эпизод немедленно становился кандидатом на обучение — **fake green** | `learning.Stage` PROPOSED→BENCHMARKED→SHADOW→VERIFIED→PROMOTED; у каждой стадии своё условие; `PROMOTED` только по `owner_approved=True` |
| D2 | `evidence.write_episode` | редакция делалась **по тексту JSON**: `json.loads(redact(json.dumps(...)))`. Секрет с кавычкой/скобкой ломает JSON → исключение при записи → **потеря улик**. Плюс `d.mkdir()` без `parents/exist_ok` | редакция **по значениям** каноническим `obs.redact_obj`, JSON собирается уже из очищенного объекта; `mkdir(parents=True, exist_ok=True)` |
| D3 | `secret_guardian.redact` | **второй скраббер секретов** — конкурирующий источник правды с `bossman.obs`; его regex ловит только `key=value` и не видит bare-токен вида `sk-live-…` | собственного скраббера нет: `obs.redact`/`redact_obj` ре-экспортируются; тест `test_cybersec_layer_defines_no_second_redactor` запрещает возврат |
| D4 | `defender.defend` | `requires_owner_approval` **никогда** не выставляется в True — «сработала граница авторитета», а владельца не спрашивают | у authority/integrity-классов `requires_owner_approval=True` + `Containment.DENY`/`REQUIRE_APPROVAL` |
| D5 | `defender.defend` | `MALICIOUS_SKILL_PROPOSAL` и `DEPENDENCY_RISK_SIMULATION` не обрабатываются и падают в `SANDBOX_AND_OBSERVE` — «понаблюдаем», хотя это допуск в Tool Registry | оба в integrity-наборе → `ISOLATE_AND_REVERIFY`; допуск решает `supply_chain.review_proposal` (deny-by-default) |
| D6 | `models.AttackIntent.validate` | запрещено 6 ключей (`command, shell, payload, executable, socket_target, credential`), сравнение регистрозависимое → `Shell`/`API_KEY`/`argv` проходили | 17 ключей, сравнение по `lower()`; добавлены `cmd, binary, secret, token, api_key, production_host, network_target, argv` |
| D7 | ARCHITECTURE.md vs код | документ обещает 10 модулей, в пакете их **нет**: injection firewall, repo scanner, blast radius, supply chain, recovery, security memory, benchmark lab | реализованы все 10 (см. `CYBERSEC_AI_V1.md`) |
| D8 | документация | «difficulty changes complexity, never permissions» — только текстом, проверить нечем | `redteam.permissions_for_level()` (всегда пустое множество) + тест на все L0–L5 |
| D9 | `ids.score_behavior` | нет сигналов `injection_hits` и `sandbox_escape_attempts`, нет флага «нужно сдерживание» — побег из песочницы нечем даже обозначить | добавлены оба сигнала + `recommend_containment`; sandbox-escape **всегда** → containment |
| D10 | `gates.SandboxFacts` | поля без значений по умолчанию; безопасность зависит от того, что вызывающий их заполнит | значения по умолчанию — **небезопасные** (`is_disposable=False`, секреты смонтированы, сеть разрешена), поэтому `SandboxFacts()` по умолчанию **отклоняется** (fail-closed) |
| D11 | `gates` | `RuntimeError` без типа — не отличить «лаборатория заморожена» от любой другой ошибки | отдельный `LabFrozen(RuntimeError)`; сообщение не раскрывает, какие именно секреты/сеть присутствуют |
| D12 | `engine.run` | в улики кладётся `asdict(intent)` целиком — недоверенный текст без ограничения длины | `untrusted_excerpt` обрезается до 500 символов и проходит редакцию |

## Чего в ZIP не было и что добавлено сверх контракта
* **Каталог сценариев в коде** (`redteam.CATALOG`, 14 шаблонов) — в ZIP это
  `scenarios/catalog.json` из 12 записей, ни на что не влиявший. Уровень сценария
  выводится из `LEVELS`, поэтому каталог и карта уровней не могут разойтись.
* Два класса атак сверх ZIP: `MALFORMED_INPUT` (L0) и `SANDBOX_ESCAPE_SIMULATION` (L4).
* Действие `REJECT_AND_CONTINUE` для некорректного ввода: отбрасывание на границе —
  это тоже сдерживание, а не «наблюдение».
* Structural-регрессии: слой не импортирует `subprocess/socket/requests/httpx/urllib`,
  не вызывает `eval/exec/compile/__import__/.system()/.popen()` и не передаёт `shell=`
  (проверяется AST-обходом, а не поиском подстрок).
* 79 тестов вместо 9, включая сквозной эпизод по каждому сценарию каталога.
