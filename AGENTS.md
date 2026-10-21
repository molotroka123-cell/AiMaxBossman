# AGENTS.md — Mandatory context for AI contributors

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

- `BOSSMAN_1_5_START_HERE.md` and `docs/v1.5/README.md` — owner-authorized 1.5 specification and today's convergence handoff, 2026-09-23; documentation is not activation or certification;
- `docs/terminal/TERMINAL_RUN_1_2_MASTER.md` — latest owner clarification, 2026-09-23;
- `docs/evo/BOSSMAN_1_1_NORTH_STAR.md`;
- `CLAUDE_NEXT_ACTION.md`;
- current repair/audit checkpoints relevant to your work.
- `docs/trading/K1M6A_RESUME_AND_PROVE_LEARNING_20261008.md` — owner-authorized 08.10 continuation, independent ten-video audit and blind learning/transfer checks; read for K1m6a work.
- `docs/audits/2026-10-08-tree-audit-13-zones/README.md` — 08.10 checkpoint of the 13-zone capability-tree audit (what exists, what is missing, where to start) and `BRIEFS.md` (3 auditor roles per branch, 2 open-source candidates per leaf, selection by the existing G·P·D·S·V ≥ 9/10 rule). Status PARTIAL: agent waves failed on session limits; re-run per section 6 of that README before claiming any zone result.

## Non-negotiable direction

**NORTH STAR: Bossman = verified continuous self-improvement with retained, applicable experience and owner-approved commercially useful work.**

Latest owner clarification: **Terminal Run 1.2 is ONLY an additional control surface for the SAME Bossman**. CLI, dashboard and Telegram must use the same configured backend, project/files, models, tasks, memory, skills, permissions and evidence. No separate terminal product, data directory, model fleet, memory database or task engine. Candidate workspaces remain the existing safety mechanism; the learner still cannot directly rewrite installed stable.

Claude Code will teach and audit through structured CLI operations rather than repeatedly clicking the dashboard. Reduced orchestration overhead is a hypothesis to measure on identical tasks, not a guaranteed model speed/intelligence increase. Genuine browser/desktop operations still use their real tools and verification.

The owner's latest target is **4–6 days after the first accepted owner run** to demonstrate transferable learning and move toward first commercial results/revenue through ideas, workflows and the owner's hardware. This updates the earlier 4–7-day horizon only; it is not a profit guarantee or a new permission grant. Actual revenue, deliverable value and saved operating cost are separate metrics.

After release-critical safety/correctness blockers, this is the highest product priority. Terminal Run is an explicitly authorized scope addition to old freeze instructions, not permission to rewrite the architecture.

Do not claim learning from memory hits, teacher-written patches, repeated known tasks or weakened tests.

Use isolated candidate → tests → independent verification → review → promotion. Retain owner STOP, budgets, privacy and external-action approval boundaries across every interface.

Major handoffs/checkpoints must mention both the same-product Terminal Run contract and current progress against the North Star ladder:
SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT → SELF_REPAIR_SINGLE_CYCLE_PASS → SELF_REPAIR_3_CYCLE_PASS → TRANSFER_MEASURED_GAIN → 24H_SOAK_PASS → 48H_SOAK_PASS → WEEK_MODE_READY → REVENUE_CAPABLE_PILOT.

A specification, generated screenshot or new CLI entry point is not proof of an achieved level.

## Owner amendment — 1.5 specification and consolidation

Owner requested autonomous App/Skill acquisition, internet, an explicitly selected low-overrefusal local profile, own-voice/phone and shopping. Requirements live in `docs/v1.5/`. Reuse the existing backend and permission system; do not build four new cores. `LOCAL_UNRESTRICTED` controls model behavior, not authority over credentials, money or stable code.

## Owner amendment — capability tree is a map, not a certificate (2026-10-08)

The public tree site (`molotroka123-cell.github.io`, pages `ai/bossman-development.md` and `subtle-baklava-676893/`) is unreachable from cloud containers (network policy). Its data source in this repository is `command-center/bcc/capability_tree_seed.json`; per-leaf evidence levels live in `docs/architecture/tree-registry.json`. As of 08.10 that registry records **0 leaves at `ci` and 0 at `owner_pc`**; `reported`/`recorded`/`code` statuses are claims, not proof. Never change leaf statuses by hand — only via `tools/tree_registry_sync.py` / `tools/tree_apply_evidence.py` with evidence files. Owner 08.10: documentation-only work; do not write product code for the tree audit; reconcile site SHA, seed SHA, installed build SHA and registry head before any "green" claim.

## Owner amendment — canonical line (2026-10-08)

**Canonical integration and release destination is `main`, as explicitly directed by the owner on 2026-10-08.** This supersedes older instructions naming `release/bossman-owner` as canonical, including that destination in `docs/v1.5/CLAUDE_MERGE_MASTER.md`. `release/bossman-owner` is a historical release line; do not send new consolidation work there.

Use an existing isolated integration/owner/fix staging branch for candidate work, then integrate reviewed and verified changes into `main`. Do not create another final/canonical line, force-push, or automatically merge product changes into `main`. Required failures block product promotion. Explicit owner-authorized documentation corrections may be committed directly to `main`; they do not certify the product.

Release and owner-test handoffs must record the accepted source SHA, installed build SHA, and capability-tree source SHA. Do not claim one unified Bossman until these identities are reconciled and verification is recorded for the accepted revision. Naming `main` canonical does not certify its current CI, installed build, or autonomy. Document presence does not enable calls, purchases, voice enrollment or background spending.

## Owner amendment — video gates, pult, unified branch (2026-10-10)

Current handoff: `docs/owner/HANDOFF_20261010.md`. Video work follows the owner's stage order in `docs/owner/VIDEO_PIPELINE_STAGES_20261010.md`: Gate 0 → 1 face/person swap → 2 clothes → 3 Mimics 2.0 → 4 background; a stage opens only after VERIFIED and the owner's confirmation in the pult.

**Gate thresholds come before tests.** Thresholds are fixed and committed before the measuring tool runs (T1..T7 in `a704eda9`) and are never moved after a result is seen. If a metric itself is wrong, fix the method under a new `metric_version`, keep the failed version's result on record, and rerun everything (10.10: v1 FAILed on telecine twin frames and largest-face matching; v2 is the current method). QUEUED, mock, sampled frames or an old SHA are not PASS. Keep the source framing, timing and audio; never stretch to 16:9. Faces only with consent; personal media never leaves the PC. Wan runs only on an otherwise idle PC.

**Pult is owner-only.** The companion pult bot delivers only to the owner (`6273524c`); Jeff answers everyone it talked with. No paid service (Genjutsu, Higgsfield, any paid API) without the owner's explicit yes.

**Unified branch.** Candidate `claude/main-prod-20261009` (`a1564fef`, PR #107) plus the local `ux/genjutsu-live-constructor` work and the 6 product commits still only on `jeff/one-20261009` are to be combined in `feat/bossman-genjutsu-jeff-unified-20261010` (planned, not created on 10.10). Owner 10.10: it goes into `main` after 3 awaited generations and tests, through a PR with green required CI on the exact SHA. Never merge `claude/bossman-control-v03-43igbk` or `claude/busy-carson-ydh991` (fake windows-latest "100 scenarios" workflow). No force-push.

**Video skills:** `.agents/skills/video-face-swap-facefusion/` (FaceFusion face swap through Bossman, pose gate, Gate 0/1 proof with `tools/video_gate/`), `.agents/skills/genjutsu-character-swap/` (`tools/genjutsu/film.py`, Wan2.1 VACE via sd-cli on Vulkan), `.agents/skills/video-editing/`. `tools/trend_edit/analyze_edit.py` describes an edit's structure; it is not a view predictor.
