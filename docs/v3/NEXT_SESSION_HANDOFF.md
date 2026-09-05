# Handoff для следующей сессии (Opus) — компактный контекст

> Обновляется в конце каждой сессии Fable. Цель — начать работу без перечитывания истории.
> Сначала `git fetch origin && git log --oneline -15 origin/claude/bossman-control-v03-43igbk`.

## 1. Где мы

- Ветка: `claude/bossman-control-v03-43igbk`. V2 (`command-center/bcc`) заморожен на `ffda281`; правки V2 — только по доказанному P0/P1 (FL-01 fencing, TR-01 цены).
- V3 живёт в `bossman-core/bossman_v3/`: `memory` (TaskJournal, FailureMemory, ContextAssembler), `execution` (CompoundRunner), `adapters/command_center.py` (V3-порты → живой bcc), `organization` (КТО), `fleet` (ГДЕ), `benchmark_overlay` (пассивный бенчмарк, если влит).
- Разделение слоёв: Organization=КТО · Fleet=ГДЕ · Model Broker (`bcc/v2/model_router`)=КАКАЯ МОДЕЛЬ · V3/V2=КАК и ДОКАЗАНО ЛИ · Memory=ЧТО ПЕРСИСТИТСЯ · Autonomous Ops=КОГДА (НЕ начато) · Benchmark/Scorecard=КАК измеряем.
- Инварианты (не ослаблять): `SIDE_EFFECT_REQUIRED && !VERIFIED → !SUCCESS`; `любой обязательный ребёнок не VERIFIED → родитель не COMPLETE`; `PLACED ≠ DISPATCHED ≠ EXECUTED ≠ VERIFIED`; `DUPLICATE_SIDE_EFFECT_COUNT=0`; текст/событие/размещение/кэш ≠ доказательство; fail-closed.

## 2. Документы, которые читать первыми

| Что | Где |
|---|---|
| Аудит 10×10 и ТЗ (порядок исполнения) | `docs/audit/2026-09-05_AUDITOR_SCORECARD_10x10.md`, `docs/audit/tz/TZ-01..TZ-10` |
| Organization: архитектура/безопасность/журнал работ | `docs/v3/organization/{ARCHITECTURE,SECURITY,WORK_LOG,HANDOFF}.md` |
| Fleet: архитектура/adoption table/безопасность | `docs/v3/fleet/{ARCHITECTURE,SECURITY,HANDOFF}.md` |
| ZIP-артефакты (что удалять после релиза) | `docs/v3/ZIP_ARTIFACTS.md` |
| Заимствованные паттерны и дедуп | `docs/v3/ARCHITECTURE_PATTERNS.md` |
| Live scorecard (если влит) | `README.md` блок `BOSSMAN_LIVE_SCORECARD_*`, `docs/benchmark/current-scorecard.json`, `scripts/update_readme_scorecard.py --check` |

## 3. Как гонять тесты (важно)

- venv: `/tmp/bccvenv/bin/python` (editable-инсталлы указывают на ОСНОВНОЙ checkout). В git-worktree ОБЯЗАТЕЛЬНО:
  `cd bossman-core && PYTHONPATH=$PWD:$PWD/../command-center:$PWD/.. /tmp/bccvenv/bin/python -m pytest -q -p no:cacheprovider tests/test_v3_*.py`
- Целевые наборы: `tests/test_v3_organization_*.py`, `tests/test_v3_fleet_*.py` (E2E #1–#4), `test_v3_command_center_adapters.py` (живой bcc), `test_company_mode.py`.
- Полный регресс ядра — один раз перед пушем: `BOSSMAN_RUN_REAL_SANDBOX=0 ... pytest tests/ --timeout=300` (~4 мин; не коммитить во время прогона — benchmark-тесты проверяют SHA).
- Secret scan: `python tools/ci_secret_scan.py` из корня (канарейки — только с маркером `ci-secret-scan: allow`). Сканер видит только tracked-файлы: запускать ПОСЛЕ `git add`.
- Коммиты: trailer'ы `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` (или актуальная модель) и `Claude-Session: <url>`; ID находок (TR-01, FL-01, EH-01…) в сообщении; один коммит = одно ТЗ.
- CI: смотреть Actions по точному SHA (root-ci, Bossman Core CI, Command Center CI, Bossman V2 Auto-Repair); пуш поверх запущенного прогона отменяет его (cancel-in-progress) — это не красный.

## 4. Состояние по ТЗ (заполняется в конце сессии)

| ТЗ | Статус | Коммит(ы) | Что осталось |
|---|---|---|---|
| TZ-09 казначейство | _см. §6_ | | |
| TZ-05 fencing/флот | _см. §6_ | | |
| TZ-01 подпись улик/верификаторы | _см. §6_ | | |
| TZ-04 организация | ORG-03..07 закрыты (`084ad3a`); ORG-01/02 — _см. §6_ | | saga (ORG-08) |
| TZ-08 наблюдаемость | _см. §6_ | | span'ы, dead-click |
| TZ-02 / TZ-07 | OPEN | | скан 2.0, rate-limit, hash-chain, coverage gate, windows job, skips registry |
| TZ-06 память | MEM-02 явное наследование (`084ad3a`); остальное OPEN | | решётка по дереву, ScopeToken, эмбеддинги, токен-оценка |
| TZ-03 инструменты | OPEN | | CapabilitySpec, выдача инструмента на run, no-progress |
| TZ-10 UX | OPEN | | статусы blocked/capability_unavailable, aria, control-plane page |

## 5. Известные P0/P1 вне V3 (для владельца)

- P0 `bossman-core/bossman/gateway/auth.py:52` — `allow_unauthenticated_loopback` даёт loopback-клиенту `allowed_aliases={"*"}` (Tailscale-serve → внешний трафик как 127.0.0.1). Рекомендация: default OFF.
- P0 `command-center/bcc/tools.py:286-290` — `tool_rules` применяются последними и могут вернуть `auto` после ужесточения hook'ом; нужен неизменяемый пол политики (V2 заморожен — решение владельца).
- P1 bcc `approvals.consume` по `(kind, preview)` без срока/скоупа; V3-адаптер уже привязывает preview к `task#<id>`.
- P1 `bossman.company.runtime` может закрыть задачу DONE по самоотчёту без улик (kind ≠ read без evidence_requirements); путь Organization этого не допускает.

## 6. Итог последней сессии

_(заполняется перед пушем: FINAL_SHA, что влито из wave-1 агентов A–E, цифры тестов, CI по SHA, следующий шаг)_
