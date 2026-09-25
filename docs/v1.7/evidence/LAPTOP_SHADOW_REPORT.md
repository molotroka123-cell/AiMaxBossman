# LAPTOP SHADOW REPORT — Bossman 1.7 PIT / Jeff

Status: `IN_PROGRESS` — live owner shadow running. This file is updated as
evidence accumulates; no status below is claimed stronger than measured.

## Run setup (live)

- Machine: owner laptop (AI Bossman box), Windows, Python 3.12.
- Branch: `feat/bossman-1.7-personal-identity-training-20260925`.
- Bot: `@Chatgptpenis_bot` (owner-provided test bot; token in local encrypted
  credential store only, never in Git/logs).
- Allowlist: owner ID + `allowlist_open: true` (owner decision 2026-09-25 —
  friends join as zero-start participants without manual ID collection).
- Owner launch path: `bossman pit setup|doctor|status|start|stop` (existing
  `bossman` CLI subcommand; no private launcher).
- Data root: `%LOCALAPPDATA%\Bossman\CommandCenter\pit-v1.7\` — outside Git;
  verified by `bossman pit doctor` (`data_root_outside_repo: PASS`).

## Route (free-only contract)

- Remote: OpenRouter catalog checked LIVE at every refresh; only models with
  prompt=0 AND completion=0 are eligible. Confirmed zero-cost at 2026-09-25:
  `nvidia/nemotron-3-ultra-550b-a55b:free`, `nex-agi/nex-n2.5-pro:free`.
  `typesafe/jev-1.5` is NOT in the live catalog → NOT_ELIGIBLE (unknown price
  is not free; route refused by policy, kept in allowlist as owner candidate).
- Local (owner decision 2026-09-25): Ollama loopback `http://127.0.0.1:11434/v1`
  with `bossman-fast-qwen36-35b-a3b-q5:latest` and
  `bossman-main-qwen38-27b-q5:latest` probed live every 300 s; local route is
  preferred (local_bonus=2.0) and falls back to the remote free route in the
  same turn when the local call fails.
- Paid routes: OFF (allow_paid=False everywhere; zero_cost_only=True).
- Per-turn route telemetry: `<pit-v1.7>/logs/route_log.jsonl` (model, provider,
  ok/fail, latency, context chars/tokens estimate, tokens in/out — no content).

## Photo/vision (owner decision 2026-09-25)

- Photo ANALYSIS: enabled on this box via the existing local Qwen vision model
  (`bossman-fast-qwen36-vision:latest` through Ollama OpenAI-compatible API).
  Bytes-only ingest, 10 MiB cap, JPEG/PNG/WebP magic bytes, per-person storage.
- Photo EDITING: intentionally still `«Скоро научусь, малышка 😊»` until a local
  Qwen-Image-Edit is registered in the existing Bossman Studio catalog.
- Image generation: laptop placeholder until the real local path passes live.

## Live traffic evidence (accumulating):

| metric | value |
| --- | --- |
| updates ingested (durable inbox) | 15 (continues) |
| distinct participants | 2 (owner + first friend) |
| learning-log turns | 10 (pre-restart accumulation; continues) |
| duplicate/replay updates | refused at ingest (idempotent) |
| delivery errors stored | 0 |
| transport errors stored | 0 |
| soak target | >= 60 min continuous run before freeze; process restarted 5x during bring-up without duplicate effects (update offset persisted) |

Observed live behavior:
- onboarding: short AiBossman/Jeff intro only (owner decision — no consent maze);
- memory starts enabled silently; `/pause_memory` `/delete_me` revoke;
- remote-personalization stays OFF by default: persona context is withheld from
  the remote model (`remote_personalization_enabled=false` observed live);
- engagement 50 → 75 through real conversation (local telemetry only);
- behavior/risk ledgers local-only; not in export, not in model context.

## Synthetic battery (offline, deterministic)

- `command-center/tests/test_pit_laptop_replay.py`: 200 mixed updates across
  4 synthetic IDs; per-ID memory isolation (MARKER cross-user leak = none);
  20 duplicate/restart boundaries (replayed update ids never double-fire);
  provider failure → honest failure text, no paid fallback; web prompt
  injection framed as untrusted evidence; roleplay per-ID + restart persistence;
  delete → zero-start recreation; adversarial probes increment local risk only.

## Test matrix (current SHA)

- PIT foundation: 46 passed (locally + CI `Bossman 1.7 PIT foundation` green).
- PIT photo foundation: 12 passed.
- PIT runtime contracts: 34 passed.
- PIT CLI contracts: 12 passed.
- PIT replay battery: 7 passed.
- Telegram contracts: 246 passed (full `telegram_contracts/` tree).
- Studio/media neighbours: 123 passed (with ffmpeg 9.0.2 portable in PATH).
- 1.5 critical regressions ON the 1.7 tree: 99 passed, 1 skipped
  (`test_v3_self_improvement`, `test_owner_run_self_improve`,
  `test_self_improve_lab`, `test_self_improve_lab_observers`,
  `test_youtube_trader_ingest`, `test_youtube_trader_ingest_auto`).
- Total PIT lane: 111 contracts green at the current SHA.

## PHASE 10 drift (honest classification)

The master-run document lists compatibility tests that do not exist on the
1.7 base tree (`release/bossman-1.5-rc2 @ 21a8092b` descendant):

- `command-center/tests/test_economy_swarm.py` — absent (exists on the newer
  1.5 economy branch);
- `tests/test_bossman_15_economy_scripts.py` — absent (same drift);
- `command-center/tests/test_bossnet_foundation_v16.py`,
  `test_coding_limit_saver_v16.py`, `test_game_bootstrap_v16.py` — 1.6
  foundation tests live on the 1.6 branch only.

These are recorded as `NOT_RUN (BRANCH_DRIFT)` for the 1.7 freeze; they are
mandatory at the convergence gate where all three lines meet. Nothing was
ported from 1.5/1.6 into 1.7 during the isolated laptop pass (isolation rule).

## Live route verification (measured on the real Ollama)

- Local chat through the existing Bossman provider adapter:
  `bossman-fast-qwen36-35b-a3b-q5:latest` answered in 25.4 s (thinking model,
  222 output tokens) — REAL inference, not a mock.
- Local Qwen vision (`bossman-fast-qwen36-vision:latest`): live analyze_fast
  on a test image returned a description. Root cause of earlier failures was
  the 20 s default fast timeout vs Ollama cold-load — fixed to 90/240 s
  (`fd4d4fb1`).
- Route log will accumulate on the next live turns; `bossman pit status`
  aggregates per-model ok/fail and p50/p95 latency.

## Known pre-existing red (NOT caused by 1.7 work, documented for freeze)

On the 1.7 base head `b1f471b7` — before any 1.7 RC commits — these shared
workflows were already red: Command Center CI (pytest py3.11/3.12/3.14),
PostgreSQL run contracts (all matrix), windows paths, ASTRA acceptance,
root-ci (root pytest + hygiene). Root causes observed:
- root-ci secret scan flagged a pre-existing redaction-test canary in
  `test_pit_foundation.py` — FIXED in `44a63cb4` (marked `ci-secret-scan: allow`).
- root-ci `test_shipped_runners_console` — `bossman_15_*`/`v15_*` tool scripts
  on the 1.7 base predate the UTF-8 console helper fixes that exist on the
  newer 1.5 branches; porting them into 1.7 would touch 1.5/1.6 work directly —
  deferred to the convergence chat per isolation rules.
- PostgreSQL contracts / ASTRA / CC CI fail the same way on the 1.5 economy
  branch heads (observed 2026-09-25), i.e. repo-wide pre-existing state.