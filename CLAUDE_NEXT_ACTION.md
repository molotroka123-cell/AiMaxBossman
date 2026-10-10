# 10.10.2026 — video gates, Jeff close-out, unified branch (read first)

Read `docs/owner/HANDOFF_20261010.md` first (state, proof, owner blockers, ordered steps), then
`docs/owner/VIDEO_PIPELINE_STAGES_20261010.md` and `docs/owner/BOSSMAN_PROD_AUDIT_AND_CLEANUP_TZ_20261010.md`.
`main` = `2f4af952` (untouched). Candidate `claude/main-prod-20261009` = `a1564fef`, PR #107 → `main`, CI 76 pass /
6 fail (root pytest py3.11/3.12, browser-user-paths, retention). Work continues on the local, unpushed
`ux/genjutsu-live-constructor` (tip `7db5facf`). Installed build `6de18f8d` is 129 commits behind `a1564fef`.
Owner stage order: Gate 0 → face/person swap → clothes → Mimics 2.0 → background; then Jeff close-out; then the
unified branch `feat/bossman-genjutsu-jeff-unified-20261010` → `main` (owner 10.10: after 3 awaited generations + tests).

Next 5 actions:
1. Commit the uncommitted video files from the ux checkout (`tools/video_gate/*`, `tools/trend_edit/analyze_edit.py`,
   `.agents/skills/video-face-swap-facefusion/SKILL.md`).
2. Gate 0: finish the metric-v2 rerun on the final H.264 of `ref-clip-01` (v1 FAIL stays recorded; thresholds unchanged).
3. Stage 1: full-clip face swap through Bossman + `animation_gate.py --gate 1`; owner confirms in the pult.
4. Owner actions before Stage 2/3: set `TdrDelay` + reboot (Wan HIP crashes), clarify what «Mimics 2.0» is.
5. Jeff close-out (7 audit gaps, each red → green) and the unified branch: + the 6 product commits from
   `jeff/one-20261009` (`5b08411a`, `5f855ff0`, `87453f9d`, `1d0a34d4`, `d4ac267d`, `d8de6ccb`), fix the 6 red
   check-runs, never merge `bossman-control-v03` / `busy-carson` (fake windows-100 workflow), no force-push.

Pult writes only to the owner; no paid service without an explicit yes; personal media never to the cloud.

---

# 08.10.2026 — one Bossman line

Canonical is `main`. The 2.1 one-line integration (green/tree-leaves 4a14c26c + old main + 08.10 fixes) lands
through PR #98. Start with `docs/owner/BOSSMAN_SIMPLE_STATE_20261008.md` (what exists / what is missing),
`docs/owner/OWNER_RUN_20261008.md` (install, checks, rollback) and `docs/trading/K1M6A_DAY_RUNBOOK_20261008.md`
(K1m6a from #26, sealed reference, BASELINE/LESSONS/RESTART_TRANSFER, unsupervised 2 h). Do not open new final
branches; work on an isolated branch and integrate into `main` through a PR with green CI.
The documentation-only checkpoints below (13-zone audit, K1m6a plan) were written earlier the same night; the owner
then asked to finish ONE working Bossman from the latest green/tree-leaves line, which is what PR #98 carries.

---

# Owner directive — tree audit checkpoint, 2026-10-08 (documentation only)

Read `docs/audits/2026-10-08-tree-audit-13-zones/README.md` first. It states, in plain words, what Bossman has, what is missing and where to start, and records that the 13-zone / 3-agents-per-branch / 2-open-source-per-leaf audit was launched but **not completed** (all agent waves failed on session limits; the tree site is unreachable from cloud containers). Re-run it per section 6 and `BRIEFS.md` before claiming any zone result. Owner 08.10: write documentation, do not write product code, land documentation in `main` and stop. Status ladder unchanged: `SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT`; `tree-registry.json` has 0 leaves proven at `ci` or `owner_pc`.

---

# Owner directive — K1m6a continuation on 2026-10-08

Read `docs/trading/K1M6A_RESUME_AND_PROVE_LEARNING_20261008.md` and current `AGENTS.md` before continuing the learning run. Resume from the verified local checkpoint (last recorded video #25), independently audit the next ten videos, and prove before/after gains on locked unseen screenshots plus restart transfer. The owner authorized continuation; this push prepares the work but does not launch the PC. Preserve release-critical fixes and the same-product contract. Canonical is `main`; historical destinations below are superseded.

---

# CURRENT OWNER PRIORITY — TERMINAL RUN 1.2

Owner clarification: 2026-09-23. Read and implement `docs/terminal/TERMINAL_RUN_1_2_MASTER.md`, then its open-source shortlist, acceptance matrix and teacher prompt.

**This is ONLY another control surface for the SAME Bossman.** CMD/CLI, dashboard and Telegram share one configured backend, files/projects, models, memory, skills, tasks, approvals and evidence. Do not make a terminal-only fork, new memory database or direct-to-model chat that bypasses Bossman.

At the next owner-run, the user chats in Windows CMD and Claude Code teaches/audits via structured CLI operations, avoiding dashboard-click overhead. Prove the time/cost advantage with same-task measurements; do not assume it. Preserve real Computer Use when the task itself needs it.

Latest business horizon: 4–6 days after the accepted first owner-run to move toward useful commercial deliverables and first revenue. This replaces the older 4–7 target only as an experiment goal, never a guaranteed result or authority to spend/send/trade.

## Implementation order now (CLOUD_PREPARE)

1. Fetch current remote. Reconcile PR #73/#74, `codex/bossman-v1.1-evolution`, and newer shared release commits by meaning. Preserve their code/evidence. Final destination is now `main` (owner amendment 2026-10-08; `release/bossman-owner` is historical); no force-push or another final branch.
2. Reuse `bossman-core/bossman/cli.py` and its existing argparse/entry point. Add the same-product API client, JSON exec/status/events/result first; interactive Russian chat second. Preserve old commands. Existing Core direct-DB task command is not automatically the Command Center task path.
3. Reuse open source: prompt_toolkit for input, Rich for display; Codex is a UX reference, not a replacement engine. Pin actual dependencies/licenses in the shipped bundle. Do not build a new browser/dashboard/Electron app.
4. Connect existing Coding/local_sidecar and bounded evolution to the CLI. Same scopes, verifier, STOP, budgets, memory/skills and model registry. All available product operations need a registry-backed parity mapping; unavailable ones must say why.
5. Prove shared file/state behavior both ways between CLI and UI, and shared task/STOP state with Telegram. Add reconnect, idempotency, Unicode, terminal-escape safety, non-TTY JSON and installed Windows tests.
6. Prepare `docs/owner/OWNER_RUN_NEXT.md` with the actual verified commands and `docs/terminal/CLAUDE_TEACHER_TERMINAL.md` for the local teacher. Prepare 40-minute bounded attempts, bad-patch rejection, lesson/restart/unseen transfer, MVČR draft-to-WAIT_APPROVAL and the later business pilot. No owner-machine launch/download before its separate command.
7. Build one exact-SHA Windows ZIP with the CLI and existing product. No source-copy/pip repair after unpacking. Apply existing mandatory CI plus Terminal Run acceptance; report missing live checks as NOT_RUN.
8. Handoff: source SHA, PR/branch, ZIP/hash, tested launch command, parity matrix, code/fixture/live distinctions and open blockers. Documentation completion is not product readiness. A UI change alone is not learning.

This owner directive explicitly supersedes only the old scope exclusions «no new functions / EVO recommendations only» for the approved v1.1 evolution and step 1.2 terminal work. All safety, preservation, regression and release gates below remain in force. Historical PR #67 is not permission to ignore current PR #73/#74 or their successors.

---

# MANDATORY NORTH STAR — READ FIRST

Before any implementation or release decision, read:
`docs/evo/BOSSMAN_1_1_NORTH_STAR.md` and the latest Terminal Run owner amendment above.

**NORTH STAR: Bossman 1.1 = verified continuous self-improvement; the latest 4–6 day learning target aims at measurable transfer and owner-approved revenue-capable work, without bypassing stable/review/approval boundaries.**

After release-critical safety/correctness blockers, this is the highest product priority.

Every major handoff must report the current achieved level:
`SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT / SELF_REPAIR_SINGLE_CYCLE_PASS / SELF_REPAIR_3_CYCLE_PASS / TRANSFER_MEASURED_GAIN / 24H_SOAK_PASS / 48H_SOAK_PASS / WEEK_MODE_READY / REVENUE_CAPABLE_PILOT`.

Do not demote this goal into an indefinite backlog. Do not fake progress: memory hit != learning; Claude patch != student success; generated business idea != revenue capability.

# Multi-agent convergence note — 2026-09-22

Before the existing actions below, read `docs/agents/MULTI_AGENT_FULL_SURFACE_CONVERGENCE.md`.

Parallel lanes now exist:
- `audit/codex-full-surface-20260922` — bounded engineering/audit;
- `audit/aster6-full-surface-20260922` — economical independent audit.

Claude remains the final integrator. Do not share a worktree with them. Consume only committed deltas/checkpoints, preserve contradictory evidence, and integrate by meaning onto the current working tip. Canonical destination remains `release/bossman-owner` through PR #72 after re-verification.

The objective is full-surface convergence: keep closing the originally planned Bossman functionality after the 1.0 baseline, not only the minimum release scenarios.

---

# Прежний release-checklist — технические требования сохраняются

Репозиторий: `molotroka123-cell/AiMaxBossman`.
Единственная финальная ветка: `release/bossman-owner`. Актуальные PR и scope определяются текущим блоком выше, не историческим номером #67.

Ты — последний интегратор. Цель: закончить текущую сборку, запушить, проверить и передать один Windows-архив. Не новый аудит вместо работающего результата.

1. Fetch актуального remote HEAD, прочитай README, INSTALL, OWNER_ACCEPTANCE, KNOWN_LIMITATIONS, CONVERGENCE_DECISIONS и salvage ledger. Сохрани чужие новые коммиты. Не создавай финальную ветку, не меняй default branch, не делай force-push и не сливай в старую базу ради уборки.
2. Закрой подтверждённые программные P0/P1 и текущие красные обязательные проверки: воспроизведение → падающая регрессия → исправление → повторный тест. Не ослабляй проверки. Проверь реальные связи UI/API, creative brief → AI-сборка сайта, контекст/рестарт, approvals, Telegram, image/video и упаковку. Не переписывай уже работающие подсистемы.
3. Прогони установленный продукт в чистом Windows и настоящий браузер: создать → изменить → сохранить → открыть снова; двойной клик, отмена, reload, рестарт и отказ провайдера. Собери console/pageerror/requestfailed и неожиданные 4xx/5xx. Для медиа проверь файл, пиксели/ожидаемую правку, ffprobe и полное декодирование. В отчёте укажи фактическое покрытие, не заявляй «все кнопки» без полного реестра.
4. Закончи release-critical salvage по каждой способности. Переноси необходимые согласованные изменения, включая текущие v1.1/Terminal Run. Остальное явно в backlog, не выдавай за перенесённое. Не удаляй ветки и не закрывай PR с полезной неперенесённой работой.
5. Сохрани never/ask/allowed, LOCAL_ONLY, бюджеты и защиту от повторных эффектов. Трейдинг только READ-ONLY/PAPER. MVČR только подготовка до WAIT_APPROVAL. Реальная модель через разрешённый провайдер — отдельно от mocks; нет ключа/железа — честный статус, не fake PASS. Evolution не даёт ученику права прямо переписывать stable.
6. Подготовь HW-01…HW-13 и рабочие команды внутри архива. Исправь устаревший диапазон из 12 кейсов в owner-acceptance.ps1, если он ещё существует; проверь, что wrapper использует нужный runtime и не принимает старый JSON после неудачного запуска. Не заставляй владельца клонировать репозиторий или вручную чинить зависимости.
7. Собери исправления в один согласованный push, объяви кандидат штатным tools/release_candidate.json и прекрати обычные push во время финального прогона. Выполни все обязательные workflows из текущего tools/exact_sha_certify.py: учти push/PR/dispatch, проверь checkout/build SHA и выполненные jobs. Старый SHA, пропуск, отмена, очередь, action_required и ноль jobs не PASS. Не подделывай fail-closed измерение интеллекта и не убирай обязательный gate.
8. Передай: FINAL_BRANCH, TESTED_SHA, Windows URL, SHA-256 точного ZIP, результаты CI/owner/UX/Terminal Run, открытые P0/P1, ограничения и три шага запуска. Только при выполнении критериев — READY FOR OWNER TEST. Иначе NOT READY с точными блокерами. После freeze новых функций нет. Финальный отчёт прикрепи к тому же SHA, не создавай commit только ради галочки сертификата.

Выполняй доступные исправления до результата; при реальном внешнем блокере сохрани работу и назови его без выдуманной готовности. Финальная ветка уже выбрана: `release/bossman-owner`.
