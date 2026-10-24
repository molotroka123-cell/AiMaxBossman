# AGENTS.md — история (перенесено из AGENTS.md 24.10.2026)

Датированные разделы, которые были в корневом `AGENTS.md` до уборки 24.10.2026. Перенесены **дословно**;
они описывают поручения на свою дату и **не** являются текущими указаниями. Действующие правила — в
[`AGENTS.md`](../../../AGENTS.md). Ни одно правило безопасности при переносе не ослаблено: все запреты из этих
разделов сохранены в «Постоянных правилах» `AGENTS.md` (часть — в усиленном виде, например абсолютный запрет RunPod).

Исходная редакция предложения из раздела «OWNER NORTH STAR 23.10.2026», заменённая на абсолютный запрет RunPod:

> These are read-first owner instructions, **not** new execution permission. Follow all restrictions below: no bypass of security/STOP, no unsolicited messages, no RunPod without approval, no auto-merge to main, no stale PASS transfer. For each tested leaf, persist evidence checkpoint, commit and push to integration branch, then update tree only through evidence tooling. `AGENTS.md` by itself cannot trigger an unattended agent; the Argon session must be start or scheduled separately.

---

## Старт 23.10.2026 (запись 22.10): рой самоулучшения и Tier 5

Сначала прочитай [`docs/owner/TOMORROW_NORTH_STAR_TIER5_RUN_20261022_RU.md`](docs/owner/TOMORROW_NORTH_STAR_TIER5_RUN_20261022_RU.md) (runbook), затем [`docs/owner/CLAIMS_AUDIT_20261022_RU.md`](docs/owner/CLAIMS_AUDIT_20261022_RU.md), [`docs/owner/CLEANUP_INVENTORY_20261022_RU.md`](docs/owner/CLEANUP_INVENTORY_20261022_RU.md) и [`docs/owner/TRAINING_CLOSEOUT_20261022_RU.md`](docs/owner/TRAINING_CLOSEOUT_20261022_RU.md). Прогресс по лестнице North Star на 22.10: ступень SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT; Tier 5 не доказан (`tier5_status: NOT_PROVEN`), перенос, soak 24/48 ч и выручка — NOT_RUN/BLOCKED. Ничего из этого не ослабляет правила ниже.

## Актуальный план выпуска и первого коммерческого пилота (21.10.2026)

Перед планированием нового объёма прочитай
[`docs/owner/production-20261021/README_RU.md`](docs/owner/production-20261021/README_RU.md),
[`COMMERCIAL_PILOTS_RU.md`](docs/owner/production-20261021/COMMERCIAL_PILOTS_RU.md) и
[`OSS_SHORTLIST_RU.md`](docs/owner/production-20261021/OSS_SHORTLIST_RU.md).
Это **предложения Кодекса и датированный снимок**, а не доказательство готовой
production-сборки, Tier 5, дохода или разрешение на внешние действия.
Перед выполнением перепроверь `origin/main`, installed/runtime SHA, required CI,
дерево и живые квитанции. Приоритет плана: устранить красный CI → доказать одну
общую миссию и STOP → принять один продаваемый результат → измерить перенос
самоулучшения → оформить клиентскую поставку и оплату. Сохраняй все owner gates
ниже; документация не ослабляет их.

## Актуальный план 15 направлений (18.10.2026, вечер)

Перед расширением возможностей прочитай
`docs/owner/BOSSMAN_15_VECTORS_45_STEPS_20261019_RU.md`: там зафиксированы
изменения последних 48 часов по всем 15 направлениям и предложены по три
следующих измеримых инкремента для каждого. Это **backlog**, не утверждение о
готовности 45 функций и не замена живой приёмке. База плана — `main` `1599f2ef`;
перед работой перепроверь remote, CI, runtime и незакоммиченные изменения.
Приоритет: красный CI и идентичность сборки → один task ID/STOP/receipt →
owner-run сценарий → измеренное обучение → поставка клиенту и реальная оплата.
Один интегратор меняет общий код; соседние аудиторы могут читать тот же SHA.
Сохраняй владельческие gates и всё нижеследующее.

## Последнее поручение владельца — допилить существующий Bossman (2026-10-18)

Цель ближайшего прогона — к 19.10.2026 довести до проверяемого результата один
уже начатый рабочий сценарий Bossman. Это продолжение текущего продукта, не новый
бот, не параллельный backend и не переписывание системы. До выбора исправления
сверить актуальные `origin/main`, checkout, runtime и занятые процессы; не доверять
старым SHA и handoff как доказательству текущего состояния.

Сначала собирать безопасные логи и evidence существующего пути: время, task ID,
source/runtime SHA, наблюдаемое действие, результат и ошибка. Не открывать секреты,
личную переписку или нерелевантные данные; перед сохранением удалять токены и
приватное содержимое. Не запускать второй poller/backend, не перезапускать и не
останавливать чужие или неидентифицированные процессы. Если журналирования не
хватает, добавить только минимальную безопасную диагностику в существующий модуль.

По evidence найти одно конкретное недостающее звено, исправить его в существующем
коде и проверить целевым безопасным сценарием. Сохранять текущие backend, очередь,
хранилище, интеграции, permissions, модельный runtime и интерфейсы. Не создавать
дубликаты этих компонентов и не считать код/документацию/live-проверку одним и тем
же. В отчёте указать точный SHA, логи/evidence, PASS/FAIL/NOT_RUN/BLOCKED и остаток.
Утверждение «готово» допустимо только для сценария, реально проверенного на том же
SHA и runtime. Эта запись supersedes несовместимые старые указания начинать новую
реализацию; остальные защитные gates и правила ниже остаются в силе.

## Поручение владельца — временное замещение на время прогона (14.10.2026)

Argon на время текущего прогона замещает владельца в оперативном ведении уже
порученной работы и является основным интегратором Bossman. Прямые поручения
владельца определяют приоритет; исторические планы уступают актуальному заданию.
В пределах полученного разрешения Argon самостоятельно выбирает порядок действий,
исправляет дефекты, выполняет проверки, готовит документацию, коммиты и кандидат
сборки, без повторного запроса уже предоставленного разрешения. Эта правка
документации явно поручена владельцем отдельно от продуктовой реализации Argon.

Замещение относится к исполнению задач, а не к личности или полномочиям человека:
Argon остаётся агентом и не вправе выдавать собственное решение за подтверждение
владельца. Личная кнопка владельца для release_main, подтверждения расходов и
внешних действий, защита секретов, действующая конституция и её пин, permissions,
STOP и обязательные проверки сохраняются. Не менять agent/human-маркеры и не
обходить gates ради замещения. Новое разрешение не выводится из этой формулировки.
При блокировке продолжать независимую разрешённую работу и фиксировать конкретный
блокер. Владелец может уточнить или отменить замещение в любой момент; оно
завершается с окончанием текущего прогона и не действует автоматически в следующих.

**READ FIRST (owner sync 2026-10-14):** `docs/owner/ARGON_SYNC_20261014_RU.md`, `docs/owner/BOSSMAN_ONE_PRESENTATION_20261014_RU.md`, and `docs/owner/READY_FOR_PULT_20261014_RU.md` — current branch/SHA map (`release/bossman-one-20261014`), owner limits that must not change (constitution v2 + pin, merge into `main` only by the owner's button, Stars paused, no RunPod, keys never pushed), what is proven vs not proven for stage 2.2, how the live system is started, and the pult acceptance steps.

## Owner amendment — Windows agent and model selection (2026-10-13)

Latest owner instruction: Argon is the sole implementer of further changes.
Follow `docs/v2.2-20261013/ARGON_IMPLEMENTATION_PLAN_RU.md` for the Windows agent,
model selection, four monetization paths and one completed commercial pilot.
Do not appoint parallel coding executors. A separate later audit will assess his
diff and evidence against the quality rubric; no quality score is proven yet.

This is the active architecture contract; dated handoffs below remain historical.
Read `docs/v2.2-20261013/README.md`, `AGENT_ARCHITECTURE_RU.md`,
`GENERATION_MODEL_SELECTION_RU.md`, `MASTER_PROMPT_RU.md` in that directory and
`docs/owner/CONTINUATION.md`. Owner wants one agent with broad Windows access from
the existing owner-only Telegram pult: task -> real observation/action -> independent
postcondition check -> result/file delivery. Reuse the same backend, storage, tasks,
models, permissions, memory and queues. Prove GUI work with actual mouse/keyboard,
window identity, STOP/restart and receipts; API-only work is not a GUI proof.

Source checkpoint: main b046b3b3; occupied checkout wt-unified-20261010 is on
feat/bossman-2-2-20261013 at af92a0fd before this documentation change. Recheck all
refs and runtime; do not switch/reset the busy checkout or steal dirty changes.
Canonical destination remains main through applicable promotion gates. The Qwen
operator already has Lab, read-only and approved host/Motion API capabilities;
extend its real task/tool flow instead of building a second agent core.
Commit af92a0fd records owner-adopted constitution v2; old "not adopted" claims are
historical. Check current runtime pin/hash; agents must not write the owner pin.

New MC22-16 specifies owner-only selection of prompt/image/edit/video models,
preferred uncensored profile per role, encrypted preferences, compatible catalog
entries and immutable enqueue snapshots. This documentation does not activate the
proposed /genmodel command. Unavailable profiles are explicit; never silently label
ordinary Qwen-Image-Edit/Wan5B uncensored or claim downloaded A14B is connected.
Text Qwen is not the image/video engine; read actual context config, not old 256k
claims (checkpoint operator has adaptive default 64k). Existing permissions and
media rules remain. Benign clothing edits should not fail on one keyword alone.

Retain all 39 requirement IDs, the full capability tree, four commercial pilots,
North Star, licenses, source/runtime/archive identity and evidence gates. Do not
resume cancelled hourly audits or the completed readiness monitor. Existing owner
authorization remains valid within scope; do not repeatedly ask for the same action.

Before making product, release, training, memory, model-routing or self-improvement decisions, read:

- `docs/archive/root-history/BOSSMAN_1_5_START_HERE.md` and `docs/v1.5/README.md` — owner-authorized 1.5 specification and today's convergence handoff, 2026-09-23; documentation is not activation or certification;
- `docs/terminal/TERMINAL_RUN_1_2_MASTER.md` — latest owner clarification, 2026-09-23;
- `docs/evo/BOSSMAN_1_1_NORTH_STAR.md`;
- `CLAUDE_NEXT_ACTION.md`;
- current repair/audit checkpoints relevant to your work.
- `docs/trading/K1M6A_RESUME_AND_PROVE_LEARNING_20261008.md` — owner-authorized 08.10 continuation, independent ten-video audit and blind learning/transfer checks; read for K1m6a work.
- `docs/audits/2026-10-08-tree-audit-13-zones/README.md` — 08.10 checkpoint of the 13-zone capability-tree audit (what exists, what is missing, where to start) and `BRIEFS.md` (3 auditor roles per branch, 2 open-source candidates per leaf, selection by the existing G·P·D·S·V ≥ 9/10 rule). Status PARTIAL: agent waves failed on session limits; re-run per section 6 of that README before claiming any zone result.

## Owner amendment — 1.5 specification and consolidation

Owner requested autonomous App/Skill acquisition, internet, an explicitly selected low-overrefusal local profile, own-voice/phone and shopping. Requirements live in `docs/v1.5/`. Reuse the existing backend and permission system; do not build four new cores. `LOCAL_UNRESTRICTED` controls model behavior, not authority over credentials, money or stable code.

## Из раздела «Owner amendment — video gates, pult, unified branch (2026-10-10)»

**Unified branch.** Candidate `claude/main-prod-20261009` (`a1564fef`, PR #107) plus the local `ux/genjutsu-live-constructor` work and the 6 product commits still only on `jeff/one-20261009` are to be combined in `feat/bossman-genjutsu-jeff-unified-20261010` (planned, not created on 10.10). Owner 10.10: it goes into `main` after 3 awaited generations and tests, through a PR with green required CI on the exact SHA. Never merge `claude/bossman-control-v03-43igbk` or `claude/busy-carson-ydh991` (fake windows-latest "100 scenarios" workflow). No force-push.
