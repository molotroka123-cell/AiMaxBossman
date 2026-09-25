# CODEX OWNER RUN — BOSSMAN 1.5 FREE-FIRST CLOSURE

Date: 2026-09-25
Target branch: `feat/bossman-1.5-economy-orchestrator-20260924`

## Mission

Finish and test Bossman 1.5 while minimizing Codex/Claude token usage.

**Codex is the owner integrator, not the primary worker.** Use Bossman itself for the bulk of model work.

The external auditor is NOT a runtime dependency. It may perform one independent closure/red-team pass after Bossman has already demonstrated its own repair/learning loop. Normal 1.5 operation must continue with that auditor disconnected.

Jev is the runtime System-1 controller inside Bossman.

Do not modify Bossman 1.0 release closure while its exact-SHA CI is running.

## Required model routing

Through Bossman/OpenRouter only:

### Three parallel/independent learning workers
Model:
`nvidia/nemotron-3-ultra-550b-a55b:free`

Run as three separate agents:
1. `nemotron_evidence`
2. `nemotron_strategy`
3. `nemotron_adversary`

They must not share one generated answer and call it three agents.

### Coding/test worker
`inclusionai/ling-3.0-flash-fin:free`

Use it for:
- reproducing 1.5 bugs;
- writing minimal patches;
- writing/running targeted tests;
- neighbouring regressions;
- harness repairs.

Use:
`tools/bossman_15_ling_coder.py`

Codex verifies Ling's patch. Do not spend Codex context rewriting a patch Ling
can safely produce and test itself.

### Paid finalizer
`z-ai/glm-5.3-flash`

Only after:
- free workers ran;
- executable verifier found a real unresolved blocker;
- Jev chooses `glm_finalizer`;
- run is started with `--allow-glm`;
- configured dollar cap permits it.

Default cap:
`BOSSMAN_15_GLM_BUDGET_USD=0.25`

Do not use GLM as a general worker.

## Source truth

First:

```
git fetch --all --prune
git status --short
git rev-parse origin/feat/bossman-1.5-economy-orchestrator-20260924
git rev-parse origin/release/bossman-owner
```

1.0 and 1.5 remain separate until 1.0 is tagged.

Read:
- `docs/v1.5/ECONOMY_ORCHESTRATOR.md`
- `docs/trading/YOUTUBE_TEACHER_INGEST.md`
- `docs/owner/MARKET_LEVELS_MULTIHORIZON_YOUTUBE_20260924.md`
- `docs/v1.5/ACCEPTANCE_AND_LEARNING.md`
- `docs/JEV_DECISION_ENGINE.md`
- current learning/evolution docs.

## Step 0 — Aster fleet refresh before Codex work

Aster runs first and owns the initial model-fleet truth pass. Read
`config/v1.5/model-routing-stack.json` and resolve the owner's aliases to real
installed identities before Codex spends tokens.

Required routing intent:

- simple → Qwen3.6;
- coding/project → Qwen3.8 with Xing/Occamy workers;
- screen/browser → Nex-N2.5;
- very hard reasoning → Flash-Next;
- verifier → gpt-oss-120B;
- image → Qwen-Image-2.1;
- video → LTX-2.5 / Wan.

Aster must write the live mapping/benchmarks first. Codex consumes that compact
report and must not independently rediscover the whole local fleet unless the
report is contradictory or a test fails.

## Step A — preflight

Verify:
- OpenRouter key is available to Bossman, never print it;
- Jev live contract/selftest;
- `yt-dlp`, FFmpeg, local ASR/vision paths;
- current exact YouTube URL from owner context/config.

The exact URL is expected in:
`BOSSMAN_K1MBA_YOUTUBE_URL`

Do not guess a different channel and do not ask the owner if the value already
exists in environment/config/history.

## Step B — YouTube training window

Use the exact owner-selected public K1m6a source already pinned in
`config/v1.5/economy-orchestrator.json`:

`https://www.youtube.com/channel/UC2KGf4oWao2NMOnIA88ZwJQ/videos`

Only:

**2026-08-14 through 2026-08-27**

Prepare the evidence inbox with the shipped read-only acquisition tools:

```powershell
$root = "$env:LOCALAPPDATA\Bossman\owner-run\v1.5-economy"
$manifest = "$root\youtube-window.json"
$inbox = "$root\youtube-inbox"

.\runtime\python.exe -I .\app-support\youtube_trader_ingest_batch.py discover `
  --source-url "https://www.youtube.com/channel/UC2KGf4oWao2NMOnIA88ZwJQ/videos" `
  --from-date 2026-08-14 --to-date 2026-08-27 --out $manifest

.\runtime\python.exe -I .\app-support\youtube_trader_ingest_batch.py ingest `
  --manifest $manifest --output-root $inbox
```

Then start the worker swarm **through the already running Bossman Command Center**,
not by calling OpenRouter directly and not by making Codex impersonate the workers.

Use Bossman's authenticated owner client/session to call:

`POST /api/v15/economy/start`

with:

```json
{
  "inbox": "<the youtube-inbox path above>",
  "allow_paid_finalizer": true,
  "glm_cap_usd": 0.50,
  "run_ling_scenarios": true
}
```

Poll:

`GET /api/v15/economy/status`

STOP if needed:

`POST /api/v15/economy/stop`

Never print/copy the owner token into logs or the repository.

For every discovered video:
1. canonical URL-only ingest;
2. captions/local ASR + local visual evidence;
3. deterministic typed Price/CVD/OI/levels/horizons;
4. three independent Nemotron agents;
5. raw output saved as UNVERIFIED;
6. future outcome/independent verifier required before promotion.

Do not train directly on raw transcript/model prose.

**Anti-lookahead:** `future_outcomes` stay local and verifier-only. They are
intentionally removed from the Nemotron/Ling evidence prompt. Never add them
back to the worker context to improve a score.

## Step C — learning and memory

For every useful result:

`RAW_CANDIDATE -> verifier -> verified lesson -> unseen transfer -> promotion`

Use existing Bossman:
- skill catalog;
- LearningStore/memory;
- evolution verifier;
- holdout;
- distill recorder/export.

Promote only lessons that have:
- source provenance;
- executable or deterministic evidence;
- no lookahead;
- independent verifier;
- unseen transfer result.

Measure BEFORE vs AFTER.

A memory hit without transfer gain is not learning.

For engineering/operations lessons, verified unseen transfer may be compiled
with `tools/bossman_15_learning_compile.py` into LearningStore + workflow +
a non-activated skill proposal.

For **trading strategy**, do NOT use the generic compiler. Promotion must go
through `bossman.trading_learning.TradingMemory.promote()` with its existing
minimum independent episodes, anti-lookahead, out-of-sample EV, provenance and
independent-verifier gates. AUTHOR_CLAIM/HYPOTHESIS alone never becomes a
procedural trading rule.

## Step D — free coding swarm

Run Ling against 1.5 red tests and incomplete paths.

Pattern:

`REPRODUCE -> RED -> LING PATCH -> TARGET TEST -> NEIGHBOURS -> NEGATIVE CONTROL -> CODEX VERIFY`

Codex should intervene in coding only when:
- Ling cannot solve after bounded retries;
- patch touches a critical security/approval boundary;
- verifier finds an unresolved regression.

Do not ask Ling or Nemotron to push Git.

## Step E — GLM final pass

Feed GLM only a compact bundle:
- failing test names;
- minimal diff;
- verifier reasons;
- remaining blocker list.

No raw videos, giant logs or repeated full repo context.

One final pass maximum unless a measured cost report proves another is needed
and remains under the owner budget.

## Step F — Independent closure audit (optional for runtime)

After the product itself has run, an external auditor may independently inspect:
- Jev route decisions;
- cost ledger;
- free/paid model identities;
- no owner-private egress;
- learning promotion boundaries;
- false PASS and test weakening;
- exact branch/SHA;
- self-repair evidence and 1.5 acceptance matrix.

The auditor must not implement a routine fix and then certify its own fix. After this check, disconnect it and prove the same owner controls still work.

## Step G0 — One-button owner run

Before manual subtests, run the same path the owner will use after closure:

```powershell
Bossman-1.5.cmd quick-test
Bossman-1.5.cmd start --repo "<CLEAN_BOSSMAN_CHECKOUT>"
Bossman-1.5.cmd status
```

Or use the **Bossman 1.5** page in Command Center. UX and CMD must reach the same `/api/v15/owner-run/*` backend and show the same state.

One Start must make the following independently observable in parallel:
- self-improvement / runtime self-repair;
- YouTube economy/learning;
- Twitch OI/CVD collection + verified Telegram delivery;
- Telegram owner-input / approvals.

Do not accept a second hidden 1.5 runner or a second owner-state file.

## Step G1 — Telegram owner-input -> browser fill

Use a harmless synthetic form. Bossman must request missing ordinary fields with `browser.request_owner_fields`. Telegram should proactively show the request; the owner replies:

`/input <request_id> key=value; key2=value`

Then `browser.fill_owner_fields` must fill the fresh bound fields. PASS requires:
- values encrypted at rest;
- plaintext values absent from model/tool output;
- owner-input bound to the same task/session;
- encrypted answer cleared after successful fill;
- fresh DOM observation after fill;
- no automatic submit.

Passwords/OTP/CVV/card numbers/API keys are not part of this Telegram acceptance. Registration, login submit, ToS, CAPTCHA and payment remain separate owner/approval boundaries.

## Step G2 — Real runtime failure -> self-repair candidate

Plant one small reversible defect in an isolated test/candidate environment. Let an ordinary Bossman task encounter it.

Required trace:

`task failure -> v1.5 self-repair inbox -> isolated git worktree -> free coding worker -> executable green test -> independent compile/targeted verifier -> local bossman-self-repair/<signature> branch`

PASS requires:
- stable/release HEAD unchanged;
- network/CAPTCHA/approval/owner-input failures are NOT misclassified as code bugs;
- coding worker cannot claim DONE without at least one executable green test;
- failed candidate is not promoted;
- successful candidate is explicitly `NOT_PROMOTED_REQUIRES_UNSEEN_TRANSFER`.

## Step G3 — Prove learning with unseen transfer

Take a second unseen instance of the repaired task family. Compare BEFORE vs AFTER verified lesson/skill/workflow. Primary metric is verified success; secondary metrics are retries, tool/schema errors, owner interventions, duration and cost.

For skills also compare NO_SKILL vs SKILL_ENABLED. A skill that lowers success is rejected. A memory hit alone is not learning.

## Step G4 — Restart / STOP and auditor independence

While market + self-improve are active:

```powershell
Bossman-1.5.cmd status
Bossman-1.5.cmd stop
```

Verify durable STOP, no duplicate external effect, no stale Twitch frame promoted as fresh, owner-input remains bound/expired rather than applied to another task, and repair candidates remain Git-addressable. Then run normal status/owner controls with the external auditor disconnected.

## Step G — 1.5 closure report

Write:
`docs/owner/BOSSMAN_1_5_OWNER_RUN_FINAL.md`

Include:
- source SHA;
- exact YouTube source + video ids/dates;
- Nemotron 3-agent counts;
- Ling patches/tests;
- GLM calls and exact measured USD;
- Jev decisions/fallbacks;
- raw/verified/promoted lesson counts;
- before/after unseen transfer;
- remaining P0/P1/P2;
- capabilities matrix;
- 1.0 status separately.

Do not label Bossman 1.5 RELEASE_CERTIFIED unless its own required acceptance is
actually complete.

## Absolute rules

- FREE-FIRST.
- No fabricated PASS.
- No live trading.
- No exchange write credentials.
- No raw YouTube claim directly into procedural memory.
- No paid fallback hidden behind a free alias.
- No Codex-heavy rewrite when Ling can do the bounded repair.
- No Claude-heavy routine coding.
- No feature commits to the frozen Bossman 1.0 release.
- No automatic external account creation or quota-evasion accounts.
- No stable self-write: repair stays on an isolated candidate until verifier + unseen transfer.
- No external auditor as a runtime dependency.
- STOP/budgets/approvals/privacy remain authoritative.

Primary optimization target:

**verified useful result per dollar / owner intervention / expensive coding token**, not raw model call count.

Bossman 1.5 is OWNER_READY only when unified UX/CMD, Twitch→Telegram, owner-input→browser fill, real runtime-failure→tested repair candidate, unseen transfer and restart/STOP all pass with no open P0 or release-blocking P1.
