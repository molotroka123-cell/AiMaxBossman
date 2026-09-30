# 01 — One-pager / Одностраничник

Goal `OOTB-001-700GB-AUTONOMOUS-BOSSMAN` · Draft 2026-09-29 · **NOT_SUBMITTED** · Every claim → row in 07.

## RU

**Продукт.** Bossman + Jev — локальный автономный инженер-разработчик на компьютере владельца. Jev ставит ограниченную
задачу, Bossman даёт «руки» (терминал, файлы, браузер/рабочий стол, git-worktree, сервисы), Claude Code CLI и Codex CLI
по очереди пишут и проверяют код в изолированной копии, изменение принимается только при одобрении **обоих** для
одного и того же SHA, после тестов и staging. Деньги, внешние сообщения, ключи и релиз — всегда решение пользователя
(конституция, закреплённая по SHA-256).

**Проблема.** Автономные агенты для кода либо небезопасны (сами мержат, тратят деньги), либо дороги и отправляют весь
код в облако. Малым командам нужен агент, который работает локально, доказывает каждое изменение и останавливается у
человеческого гейта.

**Что работает сегодня (измерено).**
- Одна сборка сертифицирована CI на точном SHA 11/11; Windows-пакет: чистая установка, рестарт, 6 откатов — PASS (07 A1–A2).
- Работает на реальных данных владельца с автозапуском; Computer Use на реальном Windows a–g PASS (07 A3–A4).
- Локальные модели на Ryzen AI Max+ 395 / 128 GB: GPT-OSS-120B 7/7 за 90 с, 49 ток/с (07 A7).
- 24/7-цикл обучения без Claude: 211 циклов, 0 ошибок, $0 (07 A11).
- Худшая задержка панели управления p95: 6688 → 168 мс (07 A12).
- Jeff 2.0: 10 модулей (безопасность, память, исследование, медиа локально) с тестами (07 A13).

**Честный статус.**
- Контур автономии (lease, hands, journal, staging) — **в разработке**; ни одного полного цикла M1 ещё нет (07 G1–G2).
- Обучение локальной модели: **NOT_PROVEN** (50/52 → 52/52, ниже порога) (07 A8).
- Клиентов, пилотов, выручки — **нет** (07 G7).

**Запрос.** Временный узел 8×H200 (≈42 узло-часа, 04) через кредиты стартап-программ для бенчмарка DeepSeek-V3.2
(685B, ≈689.5 GB) против текущего набора моделей (03); затем — по результатам — доступ/лизинг машины с 700+ GB
когерентной памяти (эталон DGX Station GB300, 748 GB).

## EN

**Product.** Bossman + Jev is a local-first autonomous software engineer running on the owner's workstation. A planner
(Jev) issues bounded goals; Bossman provides controlled "hands" (terminal, files, browser/desktop, git worktrees,
services); Claude Code CLI and OpenAI Codex CLI alternate as writer and independent reviewer in isolated worktrees. A
change is accepted only when **both** approve the same immutable commit SHA after deterministic tests and staging.
Spending, external communication, credentials and production release always require the human owner, enforced by a
SHA-256-pinned, user-owned constitution.

**Problem.** Autonomous coding agents are either unsafe (self-merge, uncontrolled spend) or expensive and cloud-only,
shipping the whole codebase to third parties. Small teams need an agent that runs locally, proves every change with
evidence and stops at a human release gate.

**Working today (measured, see evidence index).**
- One build certified by exact-SHA CI (11/11 workflows); Windows bundle clean install, restart and 6 rollbacks PASS.
- Runs on the owner's real data with autostart; real Windows computer-use checks a–g PASS.
- Local models on a 128 GB unified-memory workstation: GPT-OSS-120B solved 7/7 bake-off tasks in 90 s at 49 tok/s.
- 24/7 self-improvement loop without any Claude call: 211 cycles, 0 errors, $0 readiness run.
- Control-plane worst-endpoint p95 latency reduced from 6,688 ms to 168 ms (other axes did not improve; reported).

**Honest status.** The autonomy control plane (lease, hand broker, journal, staging) is under construction; no complete
supervised cycle (M1) has been recorded yet. Local-model learning is NOT_PROVEN on our held-out A/B. There are no
customers, pilots or revenue yet.

**Ask.** Temporary 8×H200 capacity (~42 node-hours) to benchmark self-hosted DeepSeek-V3.2 (685B parameters, ~689.5 GB
FP8 checkpoint) with SGLang against free, local, Claude, Codex and paid-API arms on a fixed 60-task coding set, measuring
time and cost per accepted change, review disagreement and privacy. If the pre-registered rule passes, access to a 700+ GB
coherent-memory workstation (reference: NVIDIA DGX Station GB300, 748 GB).

Sources: [DeepSeek-V3.2](https://huggingface.co/deepseek-ai/DeepSeek-V3.2),
[DGX Station](https://www.nvidia.com/en-us/products/workstations/dgx-station/), accessed 2026-09-29.
Contact / company / website: **owner to fill (09)**.
