# Bossman 1.5 — CODE FREEZE

Date: 2026-09-25  
Canonical branch: `feat/bossman-1.5-economy-orchestrator-20260924`

**Status: CODE_COMPLETE / OWNER-LIVE ACCEPTANCE PENDING.**

The freeze SHA is the commit containing this file. Resolve it with:

```
git rev-parse HEAD
```

From this point, no new 1.5 product scope is allowed before the owner run. Only a
reproduced release blocker may change code, and every such change creates a new
candidate SHA and invalidates old exact-SHA evidence.

## Product loop frozen in 1.5

`goal -> plan -> cheapest capable team -> execute -> verify -> self-repair candidate -> independent verifier -> unseen transfer -> skill/workflow/memory candidate -> continue/report`

Normal runtime must not depend on Codex, Claude or Aster.

Aster is an **external auditor/coordinator only**. Codex is an owner integrator,
not the bulk coder. Bossman owns the routine loop.

## Frozen autonomy pillars

1. **Scientific self-improvement** — bounded evolution loop, isolated candidates,
   executable verifier, regression, unseen transfer, promote/reject decision.
2. **Persistent agent society** — role history, verified quality, skill/memory
   refs, verifier rejection history, cost/latency history survive restart.
3. **Skill compiler** — verified traces can become EXPERIMENTAL skills only after
   independent unseen-transfer PASS.
4. **Personal operating graph** — temporal project/task/branch/agent/benchmark/
   skill relations with provenance.
5. **Autonomous resource manager** — quality lower bound + cost + latency + RAM +
   locality + health; unknown cloud price is blocked.
6. **Runtime self-repair** — code-shaped runtime failures enter a durable inbox,
   a bounded worker creates an isolated candidate, and stable is never rewritten.
7. **Owner supervision** — Command Center + `Bossman-1.5.cmd` + Telegram
   owner-input all use the same Bossman backend/data root.

## Canonical local routing stack

Before any owner-run work Aster refreshes `config/v1.5/model-routing-stack.json` against the real machine.

- simple → Qwen3.6;
- coding/project → Qwen3.8 with Xing/Occamy workers;
- screen/browser → Nex-N2.5;
- very hard reasoning → Flash-Next;
- independent verifier → gpt-oss-120B;
- image → Qwen-Image-2.1;
- video → LTX-2.5 / Wan.

No silent model substitution. Missing/unloadable roles are DEGRADED/BLOCKED until a fallback passes the same role gate.
## Frozen economy/model policy

Bulk work goes through Bossman/OpenRouter:

- 3 independent roles on `nvidia/nemotron-3-ultra-550b-a55b:free`;
- code/test worker `inclusionai/ling-3.0-flash-fin:free`;
- final paid verifier/fixer `z-ai/glm-5.3-flash` only after a verified blocker;
- Jev is the runtime System-1 router/order controller.

Paid GLM remains bounded by policy: maximum 4 calls and maximum $0.50 per
campaign. Unknown cost blocks further paid calls. No automatic recharge.

Provider overflow is allowed only through already owner-configured credentials.
If more free capacity needs an account/key, Bossman creates an OWNER_REQUIRED
packet with the official signup URL and required secret name. Bossman must not:
- auto-register accounts;
- accept provider Terms of Service;
- solve/bypass CAPTCHA;
- create multiple accounts to evade quotas;
- auto-recharge.

## Trading-learning freeze

Trading remains strictly:

`collect -> verify -> replay/backtest -> paper -> measure`

Live exchange execution is OFF.

K1m6a YouTube training window:
- 2026-08-14 through 2026-08-27;
- public source only;
- local caption/ASR/vision evidence first;
- three independent Nemotron reviews;
- Ling independent code/test verification;
- optional bounded GLM finalizer;
- all raw teacher claims begin UNVERIFIED.

No raw transcript/model prose is promoted directly. Trading strategy promotion
must use the existing anti-lookahead/outcome/out-of-sample/verifier gates.

Twitch OI/CVD collection remains evidence-backed and read-only.

## Tomorrow — only owner run

Canonical order:

1. `git fetch --all --prune`; checkout this frozen branch/SHA.
2. Aster FIRST refreshes the canonical local routing stack on the real machine.
3. Prove one coding route + one independent verifier + Jev/STOP/budget boundaries.
4. Immediately start Bossman's real self-improvement campaign.
5. Run dedicated Bossman 1.5 CI/target tests and provider expansion in parallel.
6. Build/install the exact Windows candidate if required by the runbook.
7. `Bossman-1.5.cmd quick-test`.
8. `Bossman-1.5.cmd start --repo "<clean checkout>"`.
9. Confirm parallel lanes:
   - self-improvement/runtime self-repair;
   - YouTube economy/learning;
   - Twitch verified collector;
   - Telegram owner-input/approvals.
7. Plant one bounded code defect and prove:
   `failure -> repair inbox -> isolated candidate -> executable test -> verifier -> unseen transfer`.
8. Restart and prove continuity.
9. Aster independently attacks boundaries/evidence.
10. Write the owner report.

## Aster's single primary mission

Aster must achieve at minimum:

`SELF_IMPROVEMENT_PROCESS_STARTED`

Then attempt:

`SELF_REPAIR_SINGLE_CYCLE_PASS`

Then:

`TRANSFER_MEASURED_GAIN`

Aster must not replace the learner by writing routine fixes itself. A valid
negative experiment (candidate rejected) still proves the scientific loop if
the evidence, rollback and lesson are preserved.

If extra provider capacity is useful, Aster/Bossman may request owner onboarding,
but automated account creation or quota evasion is not part of 1.5.

## Closure truth

Code-complete does **not** mean release-certified.

Final progression remains:

`CODE_COMPLETE -> CONTRACT_PASS -> LOCAL_LIVE_PASS -> OWNER_LIVE_PASS -> RELEASE_CERTIFIED`

The next change after this freeze is allowed only for a reproduced blocker found
by the owner run.

Canonical execution documents:
- `docs/v1.5/ASTER_SELF_IMPROVEMENT_MASTER_20260925.md`
- `docs/owner/CODEX_BOSSMAN_1_5_RUN_20260925.md`
- `docs/v1.5/ECONOMY_ORCHESTRATOR.md`
- `docs/v1.5/BOSSMAN_1_5_FINAL_CLOSURE_20260925.md`

---

## SAME-RUN HANDOFF: 1.5 → 1.6

После закрытия 1.5 **или** достижения `V1_5_NEAR_COMPLETE` этот же owner-run сразу продолжает работу в отдельной ветке 1.6:

[feat/bossman-1.6-secret-intake-20260925](https://github.com/molotroka123-cell/AiMaxBossman/tree/feat/bossman-1.6-secret-intake-20260925)

`V1_5_NEAR_COMPLETE` = нет software P0 и release-blocking P1, основные targeted/regression/hidden tests зелёные, canonical SHA зафиксирован, а остаток относится только к owner-live/soak/внешней среде.

Не ждать отдельного следующего дня. Цель одного прогона:
`BOSSMAN_1_5_CLOSED -> BOSSMAN_1_6_CLOSED`.

Evidence и финальные статусы 1.5 и 1.6 сохраняются раздельно по своим SHA.
