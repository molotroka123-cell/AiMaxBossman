# Delta-аудит BOSS-V3-PRODUCTIZATION-CLOSURE-002 (по коду, против аудита на `e8f348d`)

Статусы: OPEN · PARTIALLY_FIXED · FIXED · OBSOLETE · FALSE_POSITIVE · OWNER_BLOCKED · HARDWARE_BLOCKED.
Правило: FIXED только с тестом в репозитории; ничто из FIXED повторно не реализовывалось.

| Finding | Было (e8f348d) | Сейчас | Улика (файл / тест / коммит) | Действие в этой миссии |
|---|---|---|---|---|
| P0-A gateway loopback `allowed_aliases={"*"}` за прокси | OPEN | FIXED | `bossman/gateway/auth.py` `_is_direct_loopback`, `loopback_allowed_aliases`; `tests/test_gateway_loopback_proxy.py`; `cbdabf2` | сделано |
| P0-B tool_rules ослабляют hook/deny | OPEN | FIXED | `bcc/tools.py` пол политики, `ToolSpec.hook_is_floor`; `tests/test_policy_algebra.py`; `eb0e969` (минимальная правка V2, freeze-exception задокументирован в коммите) | сделано |
| EH-01 доверие уликам по префиксу source | OPEN (084ad3a закрыл только границу флота) | FIXED | `bossman_shared/evidence.py`, `Evidence.signed`, `contracts._trusted`; `test_v3_evidence_signing.py`; `000f331` | сделано |
| EH-02 верификаторы пост-состояния (11 семейств) | OPEN | OPEN | только `file/db/browser/app` в `bcc/v2/verification.py` | следующий шаг (TZ-01 §2.2) |
| EH-03 классификация с абстенцией | OPEN | OPEN | regex-лексикон | не начато (SHOULD) |
| EH-04 единый `finalize()` | OPEN | OPEN | 7 мест записи completed | следующий шаг (TZ-01 §2.3) |
| EH-05 `requeue` обязателен при FAIL | OPEN | FIXED | `engine._malformed_hook_result`; `test_gate_contract_requeue.py`; `4c8fec2` | сделано |
| SEC-01 секрет-скан 2.0 | OPEN | FIXED (энтропия только для кода/конфига; ZIP — паттерны) | `tools/ci_secret_scan.py`, `tests/test_ci_secret_scan.py`; коммит этой миссии | сделано |
| SEC-02/03 сессии 720 ч, rate-limit логина | OPEN | OPEN | `bcc/sessions.py`, `POST /api/login` | не начато (V2, P1 — отдельный минимальный патч) |
| SEC-04 сканы как гейт (pip-audit/bandit) | OPEN | OPEN | `continue-on-error` в CI | не начато (риск флейков; нужен baseline-файл) |
| SEC-05 (capability-токены на инструменты) | OPEN | OPEN | — | не начато (SHOULD) |
| SEC-06 hash-chain approvals/events | OPEN | OPEN | — | не начато (SHOULD) |
| TL-01 Windows job | OPEN | OPEN | нет `windows-latest` в CI | OWNER/INFRA: требует раннера и маркеров |
| TL-02 выдача инструмента на run | OPEN | OPEN | `action_router._before_run` только browser | не начато |
| TL-03 observe→act контракт, no-progress | OPEN | OPEN | — | не начато |
| TL-04 CapabilitySpec | OPEN | OPEN | — | не начато (нужен реестр как единый источник; планировщик ORG-02 пока читает bcc REGISTRY) |
| ORG-01 HTTP/включение | OPEN | FIXED | `bcc/features/organization.py`; `test_feat_organization.py`; `efaa55f` | сделано |
| ORG-02 планировщик шагов | OPEN | FIXED (детерминированный; модельный — OPEN) | `organization/planner.py`; `test_v3_organization_planner.py`; `efaa55f` | сделано |
| ORG-03 deadline | OPEN | FIXED | `084ad3a`, `test_deadline_missed_blocks_before_placement` | не трогалось |
| ORG-04/05/06 обучение/UCB/E[calls] | OPEN | FIXED | `084ad3a` (`learning.py` n_raw/half-life, `marketplace.py` Thompson/UCB, бюджет E[calls]) | не трогалось |
| ORG-07 INV-3 конверты, резервы после рестарта | OPEN | PARTIALLY_FIXED | `PartitionViolation` в `treasury.py` (`084ad3a`); резервы намеренно не восстанавливаются (задокументировано) | не трогалось |
| ORG-08 saga-компенсации | OPEN | OPEN | — | не начато (SHOULD) |
| FL-01 fencing-токен движка | OPEN | FIXED | `bcc/db.py` `task_runs.fence`, `bcc/engine.py`; `test_fence_fl01.py`; `2487694` | сделано |
| FL-02/03 реестр узлов/планировщик | OPEN (ZIP не влит) | FIXED в V3 (`bossman_v3/fleet`, `558a764`), OPEN для V2-пути (`BCC_FLEET_ENABLED`) | `test_v3_fleet_core.py`, `test_v3_fleet_e2e.py` | не дублировалось |
| FL-04 (transport/auth production) | OPEN | OPEN — REMOTE_TRANSPORT_PRODUCTION_READY=NO, NODE_AUTH_PRODUCTION_READY=NO | `RemoteNodeTransport` → `RemoteTransportUnavailable`; `test_v3_fleet_safety_proofs.py` | честно не заявлено |
| FL-05 GPU-учёт | OPEN | OPEN | `Resources.gpu_seconds` есть, учёта из метрик нет | не начато |
| Fleet safety proofs 1–10 (§4 миссии) | — | FIXED (реестр + 2 новых теста) | `test_v3_fleet_safety_proofs.py` | сделано |
| MEM-01 решётка скоупов | OPEN | OPEN | плоские строки | не начато |
| MEM-02 явное наследование | OPEN | FIXED | `KnowledgePort.include_parents` (`084ad3a`) | не трогалось |
| MEM-03..05 эмбеддинги/TTL/токены | OPEN | OPEN | `HashEmbedder` заглушка | не начато |
| CI-01 exact-SHA truth | OPEN | PARTIALLY_FIXED | scorecard `exact_sha_ci` ставится только по Actions; UNPROVEN/NOT_RUN ≠ PASS | сделано в scorecard; windows-job/coverage OPEN |
| CI-02 coverage/skips/mutation | OPEN | OPEN | — | не начато |
| OBS-01 ретеншн событий | OPEN | OPEN | — | не начато |
| OBS-02/04 гистограммы/SLO/span'ы | OPEN | OPEN | control-plane честно `slo: NOT_IMPLEMENTED` | не начато |
| OBS-03 CEO snapshot по HTTP | OPEN | FIXED | `bcc/features/control_plane.py`; `test_feat_control_plane.py`; `5709611` | сделано |
| OBS-05 dead-click | OPEN | OPEN | `ui/testing.js` | не начато |
| OBS-07 приватность телеметрии | — | FIXED | `test_no_private_fields_in_events.py`; `5709611` | сделано |
| TR-01/02/03 цены/токены/потолок | OPEN | FIXED | `bossman_shared/fable_budget.py`; `e724a44` | сделано ранее в сессии |
| TR-04 usd как view над ledger | OPEN | OPEN | — | не начато |
| TR-05/06 GPU-секунды, burn-rate | OPEN | PARTIALLY_FIXED | burn-rate/час в `/api/control-plane` из `task_runs.cost_usd` | частично |
| UX-01..04 статусы/aria/i18n/dead-click | OPEN | OPEN | дизайн-ветка `claude/v2-ui-sidebar-compact` не влита | не начато |
| UX-05 страница control-plane | OPEN | PARTIALLY_FIXED | данные есть (`/api/control-plane`), страницы нет | не начато |
| Benchmark overlay ZIP | ZIP_PRESENT (не интегрирован; ZIP_ARTIFACTS.md ошибочно писал «влит») | FIXED (IMPLEMENTED, live-прогон не записан) | `bossman_v3/benchmark_overlay`, `docs/benchmark/BENCHMARK_OVERLAY.md`; `663a720` | сделано, запись исправлена |
| Live Scorecard | отсутствовал | FIXED | `dd658b7` | сделано |
| OpenRouter provider path | отсутствовал | FIXED (env → vault; live smoke opt-in по `OPENROUTER_API_KEY`) | `3e673d3`, `test_feat_openrouter_smoke.py` | сделано |
| Cross-layer E2E (§11) | частично (fleet e2e, cc e2e) | FIXED | `test_v3_cross_layer_e2e.py` (+ реестр proofs) | сделано |
| Autonomous Operations | — | НЕ НАЧАТО по мандату | — | — |
