# CYBERSEC AI V1 — реализованный защитный СЛОЙ

Пакет: `bossman-core/bossman/cybersec/` (16 модулей).
Тесты: `bossman-core/tests/test_cybersec_v1.py` + `tests/test_cybersec_integration.py`.

## Главное правило
CyberSec V1 — **слой поверх существующих авторитетов**, а не вторая система.
Ни один модуль не выдаёт разрешений: он может только **ужесточить** решение,
добавить детект или потребовать approval. Второй Policy / Approval / Gateway /
Tool Registry / Secret Store / EventBus / Memory **не создаётся**.

```
intent → typed action → [CyberSec pre-filter] → policy/scopes → approval →
executor → fresh observation → [CyberSec post-verify] → verification → audit
```

## Карта: модуль → авторитет, который он усиливает
Машинно-читаемая версия — `bossman.cybersec.LAYERED_OVER`.

| Модуль | Файл | Поверх чего | Что добавляет |
|---|---|---|---|
| 1. Prompt Injection Firewall | `injection.py` | граница ingest недоверенного текста | 10 правил, `sanitize` в `<<<UNTRUSTED_CONTENT>>>`, trust никогда не растёт |
| 2. Agent Behavior IDS | `ids.py` | EventBus / audit-сигналы | взвешенный скоринг поведения; sandbox-escape всегда → containment |
| 3. Secret Guardian | `secret_guardian.py` | канонический `bossman.obs.redact` | детект ЗАПРОСА на эксфильтрацию + fail-closed egress-проверка |
| 4. Repo Security Scanner | `repo_scanner.py` | `tools/ci_secret_scan.py` | правила опасных примитивов (shell=True, eval/exec, pickle, yaml.load, TLS off, curl\|sh) |
| 5. Sandbox / Blast Radius | `blast_radius.py` | Policy decision | `combine` умеет только ужесточать; IRREVERSIBLE из UNTRUSTED — deny |
| 6. Supply Chain Guardian | `supply_chain.py` | допуск в Tool Registry | deny-by-default для предложений skill/tool/MCP |
| 7. Cyber Recovery Mode | `recovery.py` | Computer Operator recovery / Recovery Kernel | порядок шагов; улики строго ДО отката |
| 8. Security Memory | `security_memory.py` | каноничная `failure_memory` (Postgres) | типизированный вид инцидента, `error_class=security_incident` |
| 9. Security Benchmark Lab | `benchmark.py` | существующий benchmark-путь | метрики безопасности + гейт (0 утечек, 0 обходов, ≥95% containment) |
| 10. AI Red-Team Lab | `redteam.py` | — | ТОЛЬКО типизированный `AttackIntent` + каталог из 14 сценариев |

Служебные: `gates.py` (feature-гейты), `trust.py` (порядок доверия),
`defender.py` (BLUE-реакция), `evidence.py` (ledger), `learning.py` (конвейер),
`training.py` (ЗАМОРОЖЕННЫЙ движок эпизодов).

## Гейты (всё OFF по умолчанию)
| Переменная | Что открывает |
|---|---|
| `BOSSMAN_CYBERSEC_V1_ENABLED=1` | защитный слой (firewall/IDS/guardian) |
| `BOSSMAN_CYBER_LAB_ENABLED=1` | тренировочная лаборатория |
| `BOSSMAN_CYBER_LAB_ACK=I_UNDERSTAND_THIS_IS_A_SANDBOX` | явное подтверждение владельца |

Плюс **факты о среде** (`SandboxFacts`), которые обязан передать вызывающий, —
мы их не угадываем: `is_disposable=True`, `production_secrets_mounted=False`,
`production_network_allowed=False`. Любое несовпадение → `LabFrozen` (fail-closed).

## Порядок доверия (`trust.py`)
`OWNER_POLICY > SIGNED_INTERNAL > VERIFIED_TOOL > TRUSTED_REPO > EXTERNAL > UNTRUSTED`.
`has_authority` — deny-by-default. Недоверенный текст **не может поднять**
собственный уровень доверия: `FirewallVerdict.effective_trust` никогда не выше
уровня источника.

## Конвейер обучения (без авто-обучения в продакшне)
```
episode → evidence → LearningProposal(PROPOSED) → BENCHMARKED → SHADOW
       → VERIFIED → PROMOTED (только явное решение владельца)
```
* эпизод без реального containment или без ссылки на улики даёт `reasons` —
  такое предложение не двигается вообще;
* `security_regression=True` откатывает предложение обратно в `PROPOSED`;
* `promote(..., owner_approved=False)` не продвигает никогда.

## Что заморожено
`training.FrozenTrainingEngine.run_episode` не выполняется без тройного гейта и
одноразовой песочницы. Реальный RED (Fable через OpenCode) vs BLUE (Bossman)
стресс-тест **не запускался** — см. `FUTURE_RED_BLUE_STRESS_TEST.md`.
