# Bossman 1.5 — Autonomous Personal Operator

**CODE_COMPLETE / FROZEN FOR OWNER RUN / OWNER LIVE ACCEPTANCE PENDING, 2026-09-25.**

Canonical 1.5 branch: `feat/bossman-1.5-economy-orchestrator-20260924`.

1.5 is no longer only a specification. The branch now contains the autonomy kernel, free-first worker economy, runtime self-repair inbox, isolated repair candidates, owner-input via Telegram, unified UX/CMD owner-run, Twitch OI/CVD collection, YouTube learning ingestion and dedicated 1.5 CI. None of that by itself is a release certificate: the 2026-09-25 owner run must prove the installed/runtime path.

| Документ | Назначение |
|---|---|
| [PRODUCT_ARCHITECTURE.md](PRODUCT_ARCHITECTURE.md) | Цель, общая архитектура и этапы реализации без второго ядра |
| [AUTONOMY_AND_UNRESTRICTED.md](AUTONOMY_AND_UNRESTRICTED.md) | Максимально свободный локальный профиль, рутина без вопросов, отдельная authority |
| [CAPABILITY_FACTORY.md](CAPABILITY_FACTORY.md) | Поиск/создание/тест/установка apps, skills, MCP и adapters |
| [VOICE_AND_PHONE.md](VOICE_AND_PHONE.md) | Собственный голос, запись, STT/TTS, телефония, живой тест |
| [SHOPPING_AND_IDENTITY.md](SHOPPING_AND_IDENTITY.md) | Покупка кроссовок, адрес, финальная цена, платёж, receipt и доставка |
| [INTERNET_MEDIA_AND_MODELS.md](INTERNET_MEDIA_AND_MODELS.md) | Интернет/Computer Use/Jev, hybrid video, модельные профили |
| [GAME_STUDIO.md](GAME_STUDIO.md) | Unreal/Game Studio: автономная разработка и проверка оригинального 15-минутного AAA-FPS vertical slice |
| [MODEL_STACK_REFRESH_2026-09-23.md](MODEL_STACK_REFRESH_2026-09-23.md) | Свежая ревизия локального стека, Xing4 challenger, маршрутизация и owner-hardware A/B |
| [MODEL_ROUTING_STACK_20260925.md](MODEL_ROUTING_STACK_20260925.md) | Канонический owner-стек ролей и обязательный Aster-first live fleet refresh |
| [ACCEPTANCE_AND_LEARNING.md](ACCEPTANCE_AND_LEARNING.md) | 20 классов неизвестных задач, safety-гейты, измерение обучения |
| [AUTONOMY_SELF_REPAIR.md](AUTONOMY_SELF_REPAIR.md) | Главный контракт 1.5: task failure → isolated repair → verifier → unseen transfer; Telegram/UX/CMD supervision |
| [ECONOMY_ORCHESTRATOR.md](ECONOMY_ORCHESTRATOR.md) | Jev-managed free-first swarm: 3× Nemotron, Ling tester/coder, capped paid GLM |
| [CODEX_OWNER_RUN_20260925.md](CODEX_OWNER_RUN_20260925.md) | Завтрашний owner-run: Codex как интегратор, дешёвые модели делают bulk work |
| [MERGE_TODAY.md](MERGE_TODAY.md) | Пошаговое безопасное сведение сегодняшних delta и exact-SHA |
| [CLAUDE_MERGE_MASTER.md](CLAUDE_MERGE_MASTER.md) | Исполняемый handoff локальному Claude, 5 агентов |
| [SOURCES_AND_BASELINE.md](SOURCES_AND_BASELINE.md) | Проверенные источники, snapshot и неизвестное |
| [BOSSMAN_1_5_CODE_FREEZE_20260925.md](BOSSMAN_1_5_CODE_FREEZE_20260925.md) | Канонический code-freeze: scope закрыт, завтра только owner-run/self-improvement evidence |\n| [BOSSMAN_1_5_FINAL_CLOSURE_20260925.md](BOSSMAN_1_5_FINAL_CLOSURE_20260925.md) | Финальный контракт автономности 1.5: self-improvement, society, skill compiler, operating graph, resource manager |
| [ASTER_SELF_IMPROVEMENT_MASTER_20260925.md](ASTER_SELF_IMPROVEMENT_MASTER_20260925.md) | Owner-run: внешний аудитор запускает и доказывает собственное самоулучшение Bossman |
| [OPEN_SOURCE_AGI_REUSE_20260924.md](OPEN_SOURCE_AGI_REUSE_20260924.md) | Какие идеи взяты из современных OSS agent/memory/routing систем и почему без второго backend |

| [contracts/source-refs.json](contracts/source-refs.json) | Зафиксированные remote-указатели, которые нужно обновить после fetch |
| [contracts/capability-manifest.schema.json](contracts/capability-manifest.schema.json) | Проект формата регистрируемой capability |
| [contracts/owner-grant.schema.json](contracts/owner-grant.schema.json) | Проект формата делегирования прав на кампанию |
| [contracts/autonomy-profile.example.json](contracts/autonomy-profile.example.json) | Неактивный пример профиля, НЕ действующее разрешение |

## Что является сутью 1.5

```
GOAL
 -> plan / route / execute
 -> verify
 -> if code failure: self-repair candidate
 -> executable test
 -> independent verifier
 -> unseen transfer
 -> verified skill/workflow/memory
 -> continue
```

Owner supervision is available through Command Center, CMD and Telegram. The normal runtime does not depend on an external coding/audit agent. Missing ordinary form values can be supplied from the phone and filled by the browser runtime; consequential submit/login/ToS/payment remains under the existing authority gates.

Market observation and training may run in parallel with self-repair. Trading execution remains OFF/PAPER.

## Обязательное различие

`SPECIFICATION → IMPLEMENTED → CONTRACT_PASS → LOCAL_LIVE_PASS → OWNER_LIVE_PASS → RELEASE_CERTIFIED`.

Отдельно: авторство `LOCAL_STUDENT / TEACHER / FIXTURE`; результат `PASS / FAIL / BLOCKED / NOT_RUN / UNKNOWN_OUTCOME`; помощь `UNASSISTED / COACHED / TEACHER_PATCH`.

Документация не подменяет код. Mock не доказывает звонок, покупку, живую генерацию или исправление локальной моделью. Новый docs commit не сертифицирует новый runtime. Факт наличия функции в другой ветке не доказывает её интеграцию.

## Разрешение владельца

Подготовка пакета и сведения сегодняшней согласованной работы разрешена. Без дополнительных вопросов допустимы обычные локальные инженерные действия внутри уже разрешённого проекта: читать, писать, тестировать, исправлять минимальный баг и публиковать безопасный код/evidence. Эти документы не разрешают реальные звонки, оплаты, выпуск голоса, публикацию рекламы или расширение доступа к личным файлам. Для таких задач заранее оформляется конкретная делегация; внутри неё повторные вопросы не нужны.

Существующие `never/ask/allowed`, Cost Governor, STOP, approvals, privacy и rollback не заменяются параллельной системой. Новые schemas — проект расширения существующих контрактов, до реализации их нельзя принимать как authority.

## Что считать закрытием 1.5

Минимальный owner-ready результат:

- один UX/CMD start запускает market + self-improvement;
- Twitch → VERIFIED OI/CVD → Telegram trace проходит;
- missing browser fields → Telegram owner input → runtime fill проходит без утечки values в model output;
- planted code defect → durable repair inbox → isolated tested candidate branch проходит;
- skill/memory/workflow получает unseen-transfer measurement;
- restart/STOP проходит без duplicate side effect;
- cost routing остаётся free-first и bounded;
- обычная работа проходит без внешнего auditor/coding agent;
- открытых P0 и release-blocking P1 по 1.5 surface нет.

Canonical tomorrow directive: [../owner/CODEX_BOSSMAN_1_5_RUN_20260925.md](../owner/CODEX_BOSSMAN_1_5_RUN_20260925.md).

---

## ОБЯЗАТЕЛЬНЫЙ ПЕРЕХОД 1.5 → 1.6 В ТОМ ЖЕ OWNER-RUN

**Цель одного прогона: закрыть Bossman 1.5 и без отдельного дня сразу перейти к Bossman 1.6.**

Отдельная ветка 1.6:
[feat/bossman-1.6-secret-intake-20260925](https://github.com/molotroka123-cell/AiMaxBossman/tree/feat/bossman-1.6-secret-intake-20260925)

Переход разрешён при полном закрытии 1.5 или при состоянии **V1_5_NEAR_COMPLETE**:
- software P0 = 0;
- release-blocking P1 по коду = 0;
- targeted/regression/hidden tests зелёные либо остаток относится только к soak/owner-live/внешней среде;
- canonical 1.5 SHA зафиксирован;
- оставшиеся OWNER_REQUIRED пункты перечислены и не требуют новой архитектуры.

При достижении этого состояния координатор не ждёт нового дня:
1. фиксирует checkpoint 1.5;
2. оставляет длительные 1.5 soak/owner-live проверки идти отдельно;
3. сразу переключает рабочий поток на ветку 1.6;
4. закрывает 1.6 security/owner scenarios в этом же owner-run;
5. в финале отдельно показывает статусы и evidence 1.5 и 1.6.

Aster после перехода остаётся координатором и независимым аудитором, а не массовым кодером. Codex/Claude не должны снова становиться bulk-исполнителями: основная работа идёт через Bossman, local/free/дешёвые workers и политику экономии 1.5.

Финальная цель одного owner-run:
`BOSSMAN_1_5_CLOSED -> BOSSMAN_1_6_CLOSED -> SELF_IMPROVEMENT_WITHOUT_PRIMARY_ASTER_CLAUDE_DEPENDENCY`.
