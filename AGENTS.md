# AGENTS.md — Mandatory context for AI contributors

Read this file before any work on Bossman. Section 1 holds the standing rules; they apply to every agent
(Claude, Argon, Codex, Kimi, local models) on every interface (Command Center, mobile pult, Telegram, CLI) and
override any older or dated instruction. Dated owner directives follow in section 2. Superseded dated sections
were moved verbatim to [docs/archive/2026-10/agents-history.md](docs/archive/2026-10/agents-history.md); they are
history, not instructions. A document never grants new permission by itself.

## 0. MAIN GOAL — owner directive 26.10.2026 (read this before everything else)

**Owner, verbatim:** «Обнови главную документацию … где у них цель максимальным способом максимально довести Боссмана
до того что задумали мы.» Every agent works toward ONE thing: bring Bossman as far as possible, by the strongest
verified means, to the product the owner and his agents have planned — and prove each step, not describe it.

**The product we planned** (each line points to the source of truth; read it before touching that area):
1. **One local-first Bossman.** One backend, one memory, one model fleet; the Command Center, mobile pult and Telegram pult are
   surfaces of the SAME system (§3 «One runtime = one tree»). Local Gemma 4 31B is the daily driver; Qwen/Strata are
   measured alternatives, chosen by data, not by parameter count.
2. **An operator of the owner's PC.** The owner writes a task in the pult or the UX; the local agent plans, acts on the PC
   (files, processes, shell, windows/UI, browser), verifies the result and may improve its own skills through a safe loop
   (branch → tests → sandbox → owner confirmation → auto-rollback). It never edits the immutable core (STOP, approvals,
   protected paths, audit log, keys). Spec: [os-agent master prompt](docs/owner/os-agent-20261026/MASTER_PROMPT_OS_AGENT_RU.md);
   night protocol for Argon + Codex + Claude: [trio runbook](docs/owner/os-agent-20261026/TRIO_NIGHT_RUNBOOK_RU.md).
3. **Verified continuous self-improvement → revenue-capable pilot** (the North Star ladder in §3 «Non-negotiable direction»).
4. **A local creative studio at Higgsfield level.** Local character replacement in real video (`tools/motion_studio/local_genjutsu.py`,
   `--head-zoom`; measured 26.10: face ArcFace 0.28–0.31 vs Higgsfield 0.40 — NOT yet parity), Motion Studio paid pilot, earning pipelines.
5. **A model and tool stack chosen by measurement.** Inputs: tournament `D:\Bossman\eval\runs\tournament-full-20261026`
   (Strata 40/42, Gemma 42/42; both failed several safety/STOP scenarios — fix in the core, not in the prompt) and the
   open-stack research `D:\Bossman\research\open-stack-20261026\*.md` (every claim there has a URL or is marked UNVERIFIED).
6. **Speed ×2 and a responsive PC** (§2 performance plan): measured, not assumed.

**Order of work.** (a) class-1 reds the owner can see broken and safety/STOP defects; (b) the PC-operator agent (P0→P5);
(c) measured performance; (d) studio quality; (e) stack upgrades proven on the owner's PC; (f) everything else. For work of the last
10 days walk the documents in the trio runbook §7 and fill `docs/audits/trio/COVERAGE_MATRIX.md` (promised → in code → test →
live receipt → status). «Maximum» means the strongest PROVEN result, never an unproven claim.

**Dated owner amendments recorded 26.10.2026.** They narrow some Section 1 prohibitions ONLY inside the stated scope;
anything outside the scope stays forbidden, and if unsure follow Section 1. The owner should confirm them once in the pult to
harmonise the Section 1 wording.
- *RunPod* (owner 25.10: «Ранпод только для qlora»): allowed ONLY for QLoRA fine-tuning, with an explicit budget cap (current cap $30),
  key delivered by the owner via pult `/key`, a local watchdog that terminates the pod, no pod left running, NO personal data/photos on
  the pod, eval500 holdout never in training data. Tournaments, evaluation, generation and everything else stay local.
- *Merge to main* (owner 26.10: «допилишь сам все в main» and «в мейн теперь вливаем только единую версию Боссман готовую и без моего
  участия за ночь»): `main` receives ONLY **one unified, finished Bossman version** — a single integration line, never piecemeal branches or
  half-done features. It may be merged overnight **without the owner's participation**, and only by the **Claude integrator** (not Argon,
  not Codex), when ALL hold: the whole unified line is green on the exact SHA, secret scan clean, STOP and trap scenarios proven, no
  class-1 finding, the PC-operator agent passes its acceptance criteria, rollback tag `pre-merge-<time>` created, health and pult checked
  afterwards (auto-rollback if health fails within 5 minutes), no force, no hook bypass. If any condition fails: do not merge, keep
  working on the integration line and report what is missing. Other agents never merge.
- *Paid generation* (owner 26.10): Higgsfield may be used with the balance the owner loaded on his own API key (price observed ≈ $0.22 per
  second of video, not the advertised «from $0.05»); each use is reported with its cost. Any other paid service still needs his explicit yes.
- *Bypass modes* (owner 26.10): Claude may run with bypassPermissions and Codex in `--approve-for-me` auto mode in their own worktrees; this
  removes confirmation prompts, not rules — immutable core, STOP, key/privacy rules and the no-`--no-verify` / no-`git add -A` rules stay in force.

## 1. Standing rules (hard limits — unchanged and not negotiable)

**Owner authority.** Product changes reach `main` only by the owner's button, through a PR with green required CI on
the exact SHA (explicitly owner-authorized documentation corrections are the only exception, see «canonical line»); no auto-merge, no force-push, no new "final/canonical" line. The constitution v2 and its pin stay in force;
agents never write the owner pin. STOP, permissions (ask/auto/deny), T2/T3 approvals and external-action approval
are never bypassed. An agent standing in for the owner executes tasks; it never presents its own decision as the
owner's confirmation and never changes agent/human markers. Never merge `claude/bossman-control-v03-43igbk` or
`claude/busy-carson-ydh991` (fake windows-latest "100 scenarios" workflow).

**Keys.** Keys, the vault and DPAPI material are never committed, pushed, sent to a model or to the pult. Scan
before every push. Show only masks.

**Privacy.** Never read the owner's chats/turn contents, personal data or photos — debug by ids, statuses and
counters; tests use synthetic inputs. Personal media never leaves the PC (no foreign pods, no cloud vision).
Faces only with consent; the NSFW filter, face-swap frame QA and consent checks are never weakened, whatever the
wording of the request.

**Pult.** The companion pult is owner-only and receives nothing without the owner's request. Every pult incident
becomes a regression test plus a code fix, not a note.

**Jeff.** Jeff (the Telegram participant bot) is paused, including Telegram Stars, until the owner resumes it.

**Cloud agents are chat-only.** Cloud models get no PC access (no terminal, files, apps, desktop). The single
exception is **Kimi K3.1 Pro (agent 47)**: PC access with the owner's approval for **each** command. Local
agents act on the PC only within their granted permissions.

**No RunPod — absolute.** No RunPod and no other GPU/pod rental, for any task or reason. Work
runs on the owner's PC (AMD Ryzen AI Max+ 395, 128 GB) or CPU.

**Budget.** Local and free first (local models, OpenRouter `:free`, NVIDIA NIM free tier). Paid models only within
the owner's explicit caps: **GLM 5.3 Flash — $2/day**, **Haiku — $2/day**, **7API sub-key — $5 total**. Unknown
pricing is never treated as free; budget gates fail closed. Any other paid service (Higgsfield, Genjutsu, any
paid API, purchases) needs the owner's explicit yes. `LOCAL_UNRESTRICTED` and similar profiles control model
behaviour, not authority over credentials, money or stable code.

**Labels.** A model is labelled "uncensored" only when its model card says so; never silently relabel ordinary
weights or claim downloaded weights are connected when they are not.

**No clarifying questions overnight.** When the owner is silent (more than 15 minutes, or at night), do not stop
to ask A/B/C: take the safe recommended option or decide with `python tools/three_model_audit.py` (three models
vote; the decision is recorded under `docs/audits/<date>-three-model-audit/`). Hard limits above are never put to
a vote and never relaxed by a decision.

**Processes and shared code.** Do not start a second poller or backend; do not restart, stop or kill foreign or
unidentified processes; do not switch/reset a busy checkout or take another agent's dirty changes. One integrator
changes shared product code at a time; other agents audit the same SHA. Reuse the existing backend, queue, storage,
permissions and models — no parallel cores.

**Evidence over claims.** Report exact SHA, logs/evidence and PASS/FAIL/NOT_RUN/BLOCKED. "Done" only for a
scenario actually verified on the same SHA and runtime. Never transfer an old PASS to a new SHA.

## 2. Current owner directives (22–23.10.2026)

### OWNER AGENTS FOUNDATION — 22.10.2026

Latest owner request: extend the existing Tasks/Agents desktop/mobile UX so local Gemma can delegate bounded work to selectable local, free-cloud and paid-cloud specialists. Read [the Codex foundation and Argon handoff](docs/agents/2026-10-22-foundation/README_RU.md) before this work. Reuse current tasks, registry, model routing, memory, permissions and STOP. Source review identifies STOP/idempotency/dependency/shared-budget gaps to verify against current code. The package is a proposal plus source observations, not a live acceptance, spending grant, enabled skill or weight-training result. Argon remains the sole product integrator; docs work must preserve his dirty files. Existing release, privacy, budget, North Star and performance gates still apply.

### OWNER FACE-SWAP REFERENCE LESSON — 22.10.2026

For the owner's request to teach reference-based face swap, read [the reviewed existing workflow](docs/owner/face-swap-reference-20261022/WORKFLOW_RU.md) and `.agents/skills/video-face-swap-facefusion/SKILL.md`. Reuse Direct Generator jobs and the current permission boundary. This lesson grants no new tool, filesystem, installation or spending authority; content/consent/QA/STOP checks remain. Multiple-target selection is not exposed in the reviewed swap API. Stored instructions and source review are not a live rendering or weight-training result.

### OWNER PERFORMANCE ×2 — 23.10.2026 (P0 READ FIRST FOR ARGON/CODEX)

**New direct owner instruction (22.10): BEFORE ANY North Star code run tomorrow, read [the mandatory 2× performance plan](docs/owner/BOSSMAN_2X_PERFORMANCE_NORTH_STAR_20261023_RU.md).** First measure the actual end-to-end bottlenecks from iPhone -> Mobile Pult -> Orchestrator -> model/tools -> verified result, then run targeted llama.cpp Vulkan/MTP, prompt/KV cache, context/skill-routing, API and existing Resource Governor A/B. Do not assume prior 2.09× *decode* implies 2× Bossman, and do not promise an unmeasured gain. **Target** >=2.00× median on preregistered coding/agent suite with equal quality and no permission regression. Push CP-PERF-00..06 with exact runtime SHA, tests, results, owner gates. Do NOT bypass STOP, Jeff hold, T2/T3 approval, RunPod prohibition or paid-model budget. **This is a required next-step document, not authorization to auto-install or auto-merge.**


### OWNER NORTH STAR 23.10.2026 — READ BEFORE ARGON/CODEX WORK

Owner explicitly requested tomorrow's autonomous integration run and per-leaf checkpoint pushes.
Before any coding, **read**:
- [Master prompt](docs/owner/ARGON_NORTH_STAR_MASTER_PROMPT_20261023_RU.md)
- [Audited tree overlay](docs/owner/NORTH_STAR_TREE_AUDIT_OVERLAY_20261022_RU.md)
- [All 912 descendant node IDs, source refs and next actions](docs/owner/NORTH_STAR_LEAF_INDEX_20261022.json)
- [15-direction exact-SHA audit](docs/owner/CHECKPOINT_20261022_01_AUDIT_15_RU.md)
- [Recent OSS release candidates, not proven in Bossman](docs/owner/CHECKPOINT_20261022_02_FRESH_OSS_RU.md)

These are read-first owner instructions, **not** new execution permission. Follow all restrictions below: no bypass of security/STOP, no unsolicited messages, no RunPod (absolute, see standing rules), no auto-merge to main, no stale PASS transfer. For each tested leaf, persist evidence checkpoint, commit and push to integration branch, then update tree only through evidence tooling. `AGENTS.md` by itself cannot trigger an unattended agent; the Argon session must be start or scheduled separately.

## 3. Engineering contracts (standing)

### A feature is PROVEN, not wrapped (owner directive 23.10, class-1 rule)

«Когда я говорю добавлять функции — это значит не обёртку сделать, а протестировать и доказать.» A capability
counts as added only with all three: (1) unit tests incl. a negative control, (2) a live proof on the owner's PC
through the real path the owner uses (pult/chat/API), with synthetic inputs and a receipt JSON under
`docs/audits/<date>-<topic>/`, (3) the receipt referenced in the merge commit. No receipt = `NOT_PROVEN`, not
"done". A capability the owner can see is broken (e.g. «Gemma не видит изображения») is a **class-1 red**: it
pre-empts other work, gets a root cause, a fix, a regression test and a live re-proof.

Chat attachments (fixed 23.10): every attachment must reach the chat model as content — text files and
pdf/docx/xlsx/pptx as extracted text; images and text-less PDFs through the LOCAL vision model
(`bcc/features/chat_vision.py`, loopback only, never cloud) as an embedded description; a failure is shown to the
model as «НЕ распознано (причина)» so it tells the owner instead of inventing. Proof: `tools/prove_chat_attachments.py`.
The local Gemma 4 ollama build has no vision projector — it "sees" only through this path.

Photo edit from the chat (fixed 23.10): every chat picture is offered to the agent with its `attachment_id`; the
agent refines the prompt itself and calls `photo.edit` (`bcc/features/tools_photo_edit.py`) → a LOCAL Direct Gen job
of the owner (default `qwen-image-2.1-uc-edit`, the same queue/STOP/FaceLock/history/«send to pult» as the pult
window), then `photo.status`. Needs the tool in the agent's list + permission `media.generate` (granted → AUTO,
otherwise ASK). The pult's fixed content rules (`gen_refine.safety_violation(photo=True)`) apply to agents too.
Pictures never leave the PC. Proof: `tools/prove_chat_photo_edit.py` (hand-off + finished edit + colour check).
Before restarting the backend check `/api/direct-gen/status` — Direct Gen jobs live inside the backend process.

### One runtime = one tree (owner directive 23.10, TZ_POLISH_AND_MERGE_20261023_RU)

When the live candidate changes, repoint ALL five anchors to the same worktree, or `-I` subprocesses (coding
sidecar, Jeff) silently run another checkout's code: (1) `owner_one_bossman.py launch --src` for backend/jeff/companion,
(2) scheduled tasks `BossmanOne-*` (`--src`, governor/backup/watchdog tool paths), (3) `one-bossman/governor.json`
`paths.launcher_src`, (4) pip editables `bossman-core`, `bossman-command-center`, `bossman-mobile`, `bossman-shared`,
`war-terminal` (`pip install -e <tree>/... --no-deps`), (5) terminal roots for coding tasks. Verify with
`/health` `build_sha` + `python -I -c "import bossman; print(bossman.__file__)"`.

### Non-negotiable direction

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

### Owner amendment — capability tree is a map, not a certificate (2026-10-08)

The public tree site (`molotroka123-cell.github.io`, pages `ai/bossman-development.md` and `subtle-baklava-676893/`) is unreachable from cloud containers (network policy). Its data source in this repository is `command-center/bcc/capability_tree_seed.json`; per-leaf evidence levels live in `docs/architecture/tree-registry.json`. As of 08.10 that registry records **0 leaves at `ci` and 0 at `owner_pc`**; `reported`/`recorded`/`code` statuses are claims, not proof. Never change leaf statuses by hand — only via `tools/tree_registry_sync.py` / `tools/tree_apply_evidence.py` with evidence files. Owner 08.10: documentation-only work; do not write product code for the tree audit; reconcile site SHA, seed SHA, installed build SHA and registry head before any "green" claim.

### Owner amendment — canonical line (2026-10-08)

**Canonical integration and release destination is `main`, as explicitly directed by the owner on 2026-10-08.** This supersedes older instructions naming `release/bossman-owner` as canonical, including that destination in `docs/v1.5/CLAUDE_MERGE_MASTER.md`. `release/bossman-owner` is a historical release line; do not send new consolidation work there.

Use an existing isolated integration/owner/fix staging branch for candidate work, then integrate reviewed and verified changes into `main`. Do not create another final/canonical line, force-push, or automatically merge product changes into `main`. Required failures block product promotion. Explicit owner-authorized documentation corrections may be committed directly to `main`; they do not certify the product.

Release and owner-test handoffs must record the accepted source SHA, installed build SHA, and capability-tree source SHA. Do not claim one unified Bossman until these identities are reconciled and verification is recorded for the accepted revision. Naming `main` canonical does not certify its current CI, installed build, or autonomy. Document presence does not enable calls, purchases, voice enrollment or background spending.

### Owner amendment — video gates and pult (2026-10-10)

Current handoff: `docs/owner/HANDOFF_20261010.md`. Video work follows the owner's stage order in `docs/owner/VIDEO_PIPELINE_STAGES_20261010.md`: Gate 0 → 1 face/person swap → 2 clothes → 3 Mimics 2.0 → 4 background; a stage opens only after VERIFIED and the owner's confirmation in the pult.

**Gate thresholds come before tests.** Thresholds are fixed and committed before the measuring tool runs (T1..T7 in `a704eda9`) and are never moved after a result is seen. If a metric itself is wrong, fix the method under a new `metric_version`, keep the failed version's result on record, and rerun everything (10.10: v1 FAILed on telecine twin frames and largest-face matching; v2 is the current method). QUEUED, mock, sampled frames or an old SHA are not PASS. Keep the source framing, timing and audio; never stretch to 16:9. Faces only with consent; personal media never leaves the PC. Wan runs only on an otherwise idle PC.

**Pult is owner-only.** The companion pult bot delivers only to the owner (`6273524c`); Jeff answers everyone it talked with. No paid service (Genjutsu, Higgsfield, any paid API) without the owner's explicit yes.


**Video skills:** `.agents/skills/video-face-swap-facefusion/` (FaceFusion face swap through Bossman, pose gate, Gate 0/1 proof with `tools/video_gate/`), `.agents/skills/genjutsu-character-swap/` (`tools/genjutsu/film.py`, Wan2.1 VACE via sd-cli on Vulkan), `.agents/skills/video-editing/`. `tools/trend_edit/analyze_edit.py` describes an edit's structure; it is not a view predictor.
