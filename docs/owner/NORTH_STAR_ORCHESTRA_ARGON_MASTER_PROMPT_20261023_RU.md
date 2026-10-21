# NORTH STAR ORCHESTRA — ARGON MASTER PROMPT / 23.10.2026

**Owner goal:** close every outstanding capability in ONE AiMaxBossman by verified evidence, and prove that Bossman can invent, write, execute, test and reuse its own skills through a real terminal.

**Repository:** https://github.com/molotroka123-cell/AiMaxBossman
**Canonical branch:** `main`; never assume that an older SHA is current.
**Hard boundary:** do not claim LIVE/VERIFIED from a test mock, a historical README badge, a model-generated report, or a green CI on a different SHA.

## 0 — Required reading; 904 direct references

Read these files first:
- `docs/owner/NORTH_STAR_LEAF_INDEX_20261022.md` — **all 904 active capability entries, one row PER ID**, zone, label, status, source path and permalink. The source is the canonical tree seed at audit SHA `fe5872f337d9272117839d3b8c4e337a3315fe61`; do not rely on that SHA after new merges.
- `docs/owner/CHECKPOINT_20261022_01_AUDIT_15_RU.md` — all 15 workstream risks and latest known gates.
- `docs/owner/CHECKPOINT_20261022_02_FRESH_OSS_RU.md` — upstream releases from 15–22 Oct; **release existence verified, integration not run**.
- `docs/owner/BOSSMAN_15_VECTORS_45_STEPS_20261019_RU.md` — 15 directions and 45 prioritised increments.
- `docs/owner/BOSSMAN_UNIFIED_15X3_FINAL_ACCEPTANCE.md` and `docs/owner/BOSSMAN_MOBILE_OPENCODE_ACCEPTANCE.md` — prior Argon **claims**; independently re-verify against exact target SHA.
- `tools/tree_registry_sync.py`, `tools/tree_apply_evidence.py`, `command-center/bcc/capability_tree_seed.json`, `docs/owner/MP21_NORTH_STAR_TREE_20261011.md`.
- `README.md` section `Codex / ChatGPT` — independent architecture opinion, not an acceptance certificate.

## 1 — Materialize a per-leaf execution board before coding

Run `git fetch`, record `origin/main` exact SHA and installed runtime SHA; inspect open PRs, CI, worktrees, local dirty files and current live tasks.

Regenerate the indexed ID set directly from `capability_tree_seed.json`; compare against the 904-row snapshot; preserve existing IDs and statuses. If new capabilities appeared, append them with grounded paths and tests; if an older ID was retired, record the retirement with rationale rather than silently dropping it.

For **EVERY active ID** create or update an execution row:
`id, zone, source_paths, source_exists_on_SHA, dependency_ids, test_id, ci_run_url, runtime_build_sha, owner_pc_receipt, security_gate, status, blocker, next_step`.

Suggested output: `docs/owner/north-star-execution-board.jsonl` (one row per ID, stable sorted order, machine-readable and diff-friendly) and `docs/owner/NORTH_STAR_EXECUTION_SUMMARY.md`.

Status vocabulary must distinguish:
- `VERIFIED_OWNER_PC`: genuine owner hardware/browser evidence at exact runtime SHA;
- `CI_PASS_ON_SHA`: CI succeeded on the exact source SHA (not an owner-PC proof);
- `TEST_PASS_LOCAL`: a recorded executable test, no current CI implication;
- `CODE_ONLY`: tracked code with no proof;
- `HISTORICAL_REPORTED`: a prior declaration without current evidence;
- `MISSING_SOURCE`, `BLOCKED`, `NOT_RUN`, `RETIRED`.

Do not promote 492 historical `working` or 172 `recorded` statuses automatically. Fix 45 missing-source references by locating actual code or proven branch history; recover or retire only with honest rationale. Resolve 23 unreferenced capabilities via concrete source/test links or leave `NOT_RUN`. **The tree is a map, not a certificate.**

## 2 — Close all 15 directions as one system, in dependency order

Process every row in the index (including nested descendants). Use logical workstreams, not 15 long-lived divergent Git branches:

1. **Jeff**: protect outgoing Telegram messages, prove hold/pause policy and recovery; never mass-send or resume Jeff without owner confirmation.
2. **UX**: one Mobile Web Pult + Command Center; 15-minute iPhone/PC live flow; good reconnect and STOP.
3. **Apps**: one session ID/task ID, one tool registry, one ownership/permissions system, unified artifacts and receipts.
4. **Models**: local Gemma/Qwen chosen by measured task/tool reliability on Radeon 8060S; no paper-only acceleration.
5. **Cloud**: real model health, cheap/free fallback, token accounting, hard budgets, strict privacy and RunPod DENY.
6. **Computer**: observable window identity, screenshot, mouse/keyboard, terminal, browser, verification and emergency STOP.
7. **Media**: real image/video/audio artifact, provenance, playback, GPU/RAM measurement and cancellation.
8. **Memory**: survives restart, retrieval with provenance, no sensitive leakage, blinded generalization measurement.
9. **Agents**: one North Star orchestrator; checkpoint/recover without duplicate side effects; independent verifier.
10. **Skills**: select existing skill; autonomously synthesize new SKILL.md + code + tests in worktree; run and reuse.
11. **Plugins**: MCP schema correctness, source sanitisation, reconnect, role enforcement and nested tool safety.
12. **Ops**: exact installed SHA, CI, watchdog, restore from backup, 24/48-hour soak only after stable build.
13. **OSS**: compare documented pinned external release to baseline; PoC first, license/security audit, rollback; skip if no gain.
14. **Commerce**: Exchange & War Terminal/Prozorro read-only lawful workflows; calculate repeatable costs, keep receipts; no fabricated revenue and no autonomous bid submissions/payments/outreach.
15. **Devices**: one PC plus iPhone, other user-owned device only if present; pairing/revoke, scoped credentials, no duplicate agent core.

A leaf with code already present: **verify rather than rewrite**. A missing or failing leaf: write regression test RED → minimal implementation GREEN → full integration/CI; independent review; owner test if needed. A physically unavailable feature stays BLOCKED/NOT_RUN, not green.

## 3 — North Star proof of SELF-INVENTED SKILL (mandatory)

Run a benign, reproducible real skill challenge using Bossman's own Mobile OpenCode/terminal/agent route; not an external engineer manually writing the skill.

Example: supply Bossman with a previously unseen fixture of messy **local CI log files**, and ask: "Diagnose which failures are actionable, classify transient vs deterministic failures, emit a structured Markdown report and reusable skill so you can repeat this later."

Acceptance:
1. Bossman identifies missing capability and emits a scoped proposal, baseline measurement and success criteria.
2. Selects local/free coding route by measured quality and budget. No hidden premium calls.
3. Opens an isolated git worktree from exact SHA and **itself writes** `.agents/skills/north-star-ci-triage/SKILL.md`, executable parser, strict JSON Schema and tests.
4. Initial RED test proves missing behaviour; tool/terminal executes real parser; GREEN tests pass.
5. Independently verify with separate unseen logs, malicious log text (prompt-injection resistance), whitespace/Unicode/Windows line endings, error cases and output schema.
6. Record tool invocation trace, actual terminal output, sandbox constraints, elapsed time, token cost and diff; model may not fabricate receipts.
7. Owner reviews patch and explicitly authorizes promotion. The skill cannot change the permission policy or self-promote into live.
8. A new session then **discovers and selects its own newly installed skill** to solve a second unseen log set without an external coding agent.
9. Benchmark vs no-skill baseline: useful result quality, retries, cost, latency, and no hidden regressions.
10. Restart Bossman; confirm skill persists, provenance remains and correct tool selection works.

Then run **three independent self-repair + skill reuse cycles**, varying fixture families. One success is not three. Transfer must be measured on holdout data, not memory recall. Do not declare model weight training without actual fine-tuning proof.

## 4 — North Star advancement ladder (no manual badge overrides)

G0 infrastructure installed + exact live SHA
G1 one complete self-repair and real terminal-run skill creation
G2 three distinct end-to-end repair cycles with independent verification
G3 holdout transfer gain without regression on historical tasks
G4 24-hour soak (only once stable)
G5 48-hour soak
G6 week-ready unattended monitoring with owner-required actions still gated
G7 revenue-capable pilot only with lawful and actual commercial evidence

Promotion requires signed or independently reproduced receipts and repository links. `ladder.override.json` must not be modified solely to display green.

## 5 — Non-negotiable owner safety and budget

- Never use `--dangerously-skip-permissions`; never broaden `AUTO` into an unbounded permissions bypass.
- Read-only and sandbox file-write tasks may be automatic under policy; outbound messaging, financial transactions, account access, destructive/system changes, device pairing, software installs and applying code to `main` require appropriate owner approval.
- STOP must override every queued worker and process; no newly initiated actions after STOP.
- No credentials in logs, commits, MCP tool output or cloud prompts.
- Local → free cloud → low-cost cloud only if needed; cloud requires consent if private data would leave machine. Hard US$ budget caps.
- RunPod is forbidden without separate explicit permission.
- No staging worktree overwrites active Windows processes while tasks are running; backup and rollback.

## 6 — GitHub checkpoint protocol

**At each meaningful verified phase**, commit an immutable checkpoint to the working branch and push:
- `CP00` inventory exact SHA, dirty state, tree ID diff, CI;
- `CP01` P0 permission/STOP and release identity;
- `CP02` unified Mobile OpenCode + terminal + PC tool chain;
- `CP03` first self-invented skill with RED/GREEN/reuse evidence;
- `CP04` three cycles and blinded transfer;
- `CP05` all 15 zones leaf registry with precise statuses, blockers;
- `CP06` stable release candidate + owner acceptance and README.

For every checkpoint save `git rev-parse HEAD`, `git status`, parent SHA, changed files, actual commands/tests and their exit codes, workflow URLs, deployed SHA (if any), owner-PC receipts and remaining BLOCKED. No invented SHA, no optimistic PASS.

Create a draft PR from a single integration candidate. Merge into `main` only after exact-SHA green required CI, independent review, safe live smoke, and explicit owner sign-off. README must link current execution board and distinguish Argon report from independently verified result.

## 7 — Your output format

Begin with a short factual status in Russian: current SHA, open P0 blockers, `TOTAL/VERIFIED/UNPROVEN/BLOCKED/MISSING_SOURCE`, critical path.

Then do the engineering, not just a plan. Keep outputs concise per checkpoint and update tree/README with verifiable links.

The owner's success criterion: **one actual installed Bossman** controllable via phone, that can autonomously propose and create a useful skill, run its own code through the real terminal, independently verify it, request approval for promotion, remember it, and select it again later.

A few green unit tests or a perfect-looking skill description **do not meet North Star**. If hardware is unavailable, finish everything independently testable, identify the exact owner-run commands, leave hardware gates `NOT_RUN`, and never claim the final product is fully verified.
