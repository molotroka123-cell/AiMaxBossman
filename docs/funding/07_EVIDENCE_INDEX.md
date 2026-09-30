# 07 — Evidence index / Индекс доказательств

Goal: `OOTB-001-700GB-AUTONOMOUS-BOSSMAN`. Status of this package: **NOT_SUBMITTED** (draft, 2026-09-29).
Repository: `github.com/molotroka123-cell/AiMaxBossman` (visibility and reviewer access: owner decides). SHAs below were resolved with `git cat-file`/`git rev-parse`
against `origin` on 2026-09-29. A file path "outside git" means the evidence lives on the owner's machine and must be
exported by the owner before a reviewer can see it.

Правило: каждое утверждение в пакете ссылается на строку этой таблицы. Утверждение без строки = не использовать.

## A. Что доказано (measured / certified)

| # | Claim (EN) | Утверждение (RU) | Artifact | Branch / SHA |
|---|---|---|---|---|
| A1 | One Bossman build certified by exact-SHA CI, 11/11 workflows | Одна сборка сертифицирована CI на точном SHA, 11/11 | `docs/owner/RC19_SESSION_AUDIT_20260928.md` §2–3 | PR #84 `claude/bossman-freeze-closure-ohvmon` (tip `235a6f54`), certified `84f5e0ac` |
| A2 | Windows bundle: clean install, start/stop/restart, 6 rollbacks — PASS | Windows-пакет: чистая установка, старт/стоп/рестарт, 6 откатов — PASS | same file §3 (`WINDOWS_BUNDLE=PASS`); ZIP SHA-256 `e05bf381…9019` | `84f5e0ac` |
| A3 | Bossman runs on the owner's real data with autostart (one backend, :8801) | Bossman работает на реальных данных владельца с автозапуском | same file §2; `tools/owner_one_bossman.*` | `rc19/k-one` `ace1827d` |
| A4 | Real Windows Computer Use checks a–g pass (h mock only) | Computer Use на реальном Windows: a–g PASS, h только mock | same file §3 (`COMPUTER_USE=REAL_PASS`) | `84f5e0ac` |
| A5 | Security and execution-truth axes rated VERIFIED (9.0 / 9.1 of 10), with tests named | Оси Security и Execution Truth — VERIFIED (9.0 / 9.1) | `docs/benchmark/current-scorecard.md` (evidence freshness: PARTIALLY_STALE, live attestation PENDING) | evidence SHA `a2790632` / `0c1cbe65` |
| A6 | Internal autonomy benchmark: release tier READY, VerifiedSuccessRate 1.0 (n=21, Wilson 95% CI 0.845–1.0), UnsafeActionRate 0, cost $0 — **deterministic MOCK/SIMULATED fixtures, not live model work** | Внутренний бенчмарк: READY, но это детерминированные фикстуры, не живая работа модели | `docs/autonomy/benchmark_history/history.jsonl`, `docs/autonomy/AUTONOMY_LEARNING_BENCHMARK.md` | commit `00686399` |
| A7 | Local model bake-off on owner hardware (Ryzen AI Max+ 395, 128 GB): GPT-OSS-120B 7/7 in 90 s at 49 tok/s; Qwen3.8-27B Q5 6/7, 316 s, 10.0 tok/s | Замер локальных моделей на железе владельца | `owner-repair/lab-20260922/CHECKPOINT-LAB.md` | `d6e25fb4` |
| A8 | Local held-out A/B (52 items ×2): baseline 50/52, candidate 52/52, 0 safety violations, $0; verdict **NOT_PROVEN** (below pre-registered +3 threshold) | A/B на отложенной выборке: результат NOT_PROVEN | `docs/benchmark/LOCAL_MODEL_LEARNING_RC19.md` | in this tree (`d162237b`) |
| A9 | Coaching exam: lesson recall 1.0 but outcomes unchanged (1/4 unassisted with and without lessons) | Уроки вспоминаются, но исход не меняют | `owner-repair/coaching-exam-20260922/report.md` | `1793272a` |
| A10 | Intelligence-preservation run (940-task public corpus, local Qwen): FULL agent regressed vs raw on 4 axes (e.g. task_completion 0.60→0.15), gained on structured 0.23→0.775 | Честно измеренные регрессии агента FULL | `RC19_SESSION_AUDIT_20260928.md` §3 | PR #84; fix `rc19/m-ip` `1ae271ea` (re-measure NOT DONE) |
| A11 | 24/7 learning loop readiness run: 211 cycles, 0 errors, $0, 0 Claude calls, STOP 1.1 s | Прогон готовности 24/7: 211 циклов, 0 ошибок, $0 | same audit §5; `docs/owner/LEARNING_247.md` | `rc19/n-self` `73049233` (final verdict still to be collected) |
| A12 | Control-plane performance: worst endpoint p95 6688 ms → 168 ms (-97.5 %); 151 endpoints; other axes did not improve (stated) | Производительность: худший эндпоинт p95 −97.5 %; остальное не улучшилось | `docs/audits/PERF_20260929.md` | `feat/bossman-perf-2.0` `79cbecba` (build `d3385ec2`) |
| A13 | Jeff 2.0: ten modules (safety, model_guard, director, memory_palace, persona, research, media, proactive, quality_lab, insights) with tests | Jeff 2.0: десять модулей с тестами | `docs/pit/JEFF_2_0_*.md`, `command-center/tests/test_jeff_2_*.py` | merged in `2ff3ab79`/`88769581` (parent of this branch) |
| A14 | User constitution v1 and the autonomy interface contract exist | Конституция пользователя и контракт автономии существуют | `docs/constitution/BOSSMAN_CONSTITUTION.md`, `docs/autonomy/AUTONOMY_CONTRACT.md` | `feat/bossman-autonomy` `d162237b` |
| A15 | Existing reusable parts: Jev client (OpenRouter), Claude/Codex CLI connectors, isolated workspaces | Готовые компоненты: Jev, коннекторы Claude/Codex CLI, изолированные рабочие копии | `command-center/bcc/jev/`, `command-center/bcc/rave/{connectors,workspace}.py` | this tree |

## B. Честные пробелы (NOT yet proven) / Honest gaps

| # | Gap | Current state | What closes it |
|---|---|---|---|
| G1 | Autonomy control plane (`bcc/autonomy/`: goals, lease, hands, policy, journal, staging) | **IN_PROGRESS**: only `types.py`, `schemas.py` on `feat/bossman-autonomy-a` `86b59167` | Line A + Line B merged, tests green, exact-SHA CI |
| G2 | M1: one complete supervised cycle Jev → Claude/Codex → tests → dual approval → staging → user gate | **NOT_ACHIEVED** | recorded journal of one real cycle (identity-leak task) |
| G3 | M2: ten bounded cycles with correct gating and rollback | NOT_ACHIEVED | ten journal entries + one exercised rollback |
| G4 | North Star ladder `SELF_REPAIR_SINGLE_CYCLE_PASS` and above | not attested | AGENTS.md ladder evidence |
| G5 | Learning changes outcomes (`TRANSFER_MEASURED_GAIN`) | NOT_PROVEN (A8, A9) | harder held-out set, n ≥ 150, baseline 60–80 % |
| G6 | Real coding value with a real model (`tools/coding_value_sim.py`) | harness exists, no PASS recorded in this package | two synthetic cases PASS with a real model |
| G7 | Any customer, pilot, revenue, user count | **NONE** — no traction exists | M3 pilot (see 05) |
| G8 | Any 8×H200 / DGX / DeepSeek-V3.2 self-hosted measurement | NONE — this is what the credits are for | 03_BENCHMARK_PLAN.md |
| G9 | `CORE_FREEZE`, `LEARNING` markers | BLOCKED (IP, local learning NOT_PROVEN) | `rc19/m-ip` re-measure |
| G10 | Scorecard freshness | PARTIALLY_STALE, live hardware attestation PENDING | re-run on current SHA |

## C. How a reviewer verifies

1. Owner grants read access to the repository (or exports the listed files as PDF) — owner action, see 09.
2. `git show <SHA>:<path>` for every row above.
3. Evidence outside git (`C:\Users\asd\Bossman\evidence\rc19\…`, `ip-corpus-rc19\`) is shared only after the owner removes private data.
