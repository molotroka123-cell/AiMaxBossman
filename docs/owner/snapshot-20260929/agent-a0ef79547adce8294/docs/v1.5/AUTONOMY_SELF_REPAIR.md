# Bossman 1.5 — Autonomous Self-Repair / Self-Improvement Contract

Status: **IMPLEMENTED ON THE DEDICATED 1.5 BRANCH; OWNER LIVE ACCEPTANCE PENDING**.

Bossman 1.5 is not defined by the number of agents or models. Its defining
product behavior is that ordinary work, failures and verified outcomes feed one
bounded loop that can improve the system without turning every defect into a
manual external-coding session.

This is an **AGI-style personal operator architecture**, not a scientific claim
that general intelligence has been achieved.

## 1. Closed-loop behavior

Normal task path:

```
owner goal
 -> Bossman chooses tools/models/skills
 -> execute
 -> verify external/product result
 -> success OR classified failure
```

Code/harness failure path:

```
real task failure
 -> redacted durable self-repair inbox
 -> isolated git worktree
 -> free-first coding worker
 -> reproduce / patch / regression
 -> at least one executable green test required before DONE
 -> independent compile/targeted verifier
 -> local bossman-self-repair/<signature> candidate branch
 -> evolution / security non-regression
 -> unseen transfer
 -> verified lesson/skill/workflow
 -> promotion or rejection
```

The current stable/release branch is never directly rewritten by the student.
A model saying DONE is never a verifier.

Network outages, rate limits, CAPTCHA, approvals, missing credentials and
owner-input waits are not classified as product-code defects.

## 2. Owner supervision, not owner micromanagement

The owner supervises through three equivalent surfaces:

- Command Center page **Bossman 1.5**;
- `Bossman-1.5.cmd start|status|stop`;
- Telegram owner console.

One start launches in parallel:

1. the self-improvement/economy lane;
2. Twitch OI/CVD market observation and verified Telegram delivery.

One STOP requests the native STOP mechanisms for both.

An external coding/audit agent is **not a runtime dependency**. It may be used
as an optional independent red-team before a major release.

## 3. Missing form data from the phone

When browser work reaches fields whose values Bossman cannot know:

1. `browser.request_owner_fields` creates a durable request bound to the
   current task/browser session and fresh DOM refs;
2. the Telegram companion proactively sends the request to the owner;
3. the owner replies with `/input <id> key=value; ...` or a JSON object;
4. values are encrypted locally;
5. `browser.fill_owner_fields` retrieves them inside the runtime and fills the
   page;
6. the model sees only which field keys were filled, never their plaintext;
7. the encrypted answer is erased after successful fill.

Typing data into fields is not form submission.

Registration, login submission, accepting ToS, CAPTCHA/human checks, payments
and other consequential external effects retain the existing ASK/DENY boundary.
Bossman must not auto-create provider accounts, accept terms or evade quotas.

For secret/password fields, local credential storage or Human Take Over is
preferred: Telegram itself is an external transport.

## 4. Capacity expansion

The provider pool exists to reduce cost and survive provider limits, not to
circumvent them.

Rules:

- existing owner-created accounts/keys may be probed;
- unknown price is blocked;
- a key is not treated as zero-cost until the owner confirms the free tier;
- no automatic signup;
- no multi-account quota evasion;
- no CAPTCHA bypass;
- Bossman may prepare/open a signup form and fill ordinary owner-supplied data,
  but the final account-creation action is OWNER_REQUIRED.

If an account is missing, Bossman should send the owner a concise Telegram
request containing the provider link, required fields and the local environment
variable that will be expected afterward.

## 5. Learning architecture

Bossman improves through several layers. They are measured separately.

### Verified memory

Store a lesson only after executable/deterministic evidence. Keep provenance,
scope, counterexample and source SHA.

### Skills

A skill is not assumed to help. Run no-skill vs skill-enabled evaluation on the
same unseen task class. Pass/fail is primary; time, tokens, tool errors and owner
interventions are secondary.

This follows the useful lesson from current agent-skill evaluation work: a
skill can improve an agent, do nothing, or reduce performance, so every promoted
skill needs an A/B result.

### Workflows

Repeated successful sequences are compiled into reusable workflows, e.g.

`REPRODUCE -> REGRESSION -> PATCH -> TARGET TEST -> NEIGHBOURS -> NEGATIVE CONTROL -> VERIFIER`.

### Distillation/training candidates

Cloud-worker traces remain RAW_CANDIDATE until license, privacy, verifier and
dataset gates accept them. Training data is not equivalent to raw chat history.

### Weight updates

The 1.5 runtime does not pretend weights changed when only memory/skills changed.
A future weight-training stage must be a separate measured experiment.

## 6. Trading learning

The live market collector remains READ-ONLY/PAPER.

```
Twitch fresh frame
 -> local vision
 -> VERIFIED OI/CVD/price
 -> append-only ledger
 -> deterministic analysis
 -> optional local prose
 -> Telegram
```

YouTube teacher material is evidence input, not ground truth.

Worker prompts cannot see future outcomes. Promotion of trading knowledge must
go through the existing TradingMemory gates with provenance, anti-lookahead,
independent episodes and out-of-sample evidence.

No 1.5 autonomy rule grants live-trading authority.

## 7. Tomorrow's 1.5 owner acceptance

The run is intentionally execution-heavy and coding-light.

### Gate A — one launch

From the installed bundle or checkout:

```
Bossman-1.5.cmd start
Bossman-1.5.cmd status
```

Or press **Start** on the Bossman 1.5 page.

PASS: market + self-improve lanes are independently observable.

### Gate B — Twitch / Telegram

PASS requires fresh VERIFIED observations and at least one trace:

`frame hash -> metric crop hash -> ledger -> typed analysis -> Telegram message`.

### Gate C — owner-input from Telegram

Use a safe synthetic form.

PASS:
- Bossman detects missing fields;
- Telegram receives the field request;
- owner answers from phone;
- browser fields are filled;
- model/tool output contains field names but not values;
- submit remains unexecuted until its own approval.

### Gate D — planted product defect

Plant one bounded, reversible defect in a test/candidate environment.

PASS:
- normal task exposes the failure;
- failure enters self-repair inbox;
- repair worker creates a diff;
- DONE is impossible without executable green test;
- candidate is committed only to `bossman-self-repair/<signature>`;
- stable branch remains unchanged.

### Gate E — learning transfer

Run the same task family without and with the new verified lesson/skill on an
unseen instance.

PASS only on measured gain or maintained pass rate with meaningful efficiency
gain. A memory hit alone is not learning.

### Gate F — free-first economics

Verify free workers are used for bulk work, paid finalizer only after a verified
blocker, and every paid call stays within the campaign cap.

### Gate G — restart and STOP

Restart Bossman while work is active.

PASS:
- no duplicate external effect;
- STOP remains durable;
- market collector does not turn stale frame into fresh data;
- self-repair/evolution state resumes from evidence, not model memory.

### Gate H — auditor independence

Run the normal 1.5 path without an external auditor/coding agent.

PASS: the product remains usable, repair candidates can be produced and all
owner controls work.

## 8. Closure verdict

`BOSSMAN_1_5_OWNER_READY` requires:

- unified UX/CMD start/status/stop;
- self-repair inbox -> tested local candidate demonstrated;
- owner-input Telegram -> browser fill demonstrated;
- Twitch -> Telegram trace demonstrated;
- free-first cost guard demonstrated;
- skill/memory transfer measurement;
- restart/STOP acceptance;
- no open P0 or release-blocking P1 in the 1.5 surfaces.

`OWNER_READY` does not mean stable was autonomously promoted. Release promotion
remains a separate exact-SHA decision.

## 9. Research principles reused

Bossman deliberately reuses ideas instead of pretending every mechanism is
novel:

- OpenHands' 2026 skill-evaluation guidance: compare with/without a skill using
  deterministic verifiers.
- Ouroboros (2026): self-development should remain attributable through reviewed
  code history, memory and reversible evolution.
- Agent Lightning v1.0 (2026): agent trajectories can be separated from the
  training backend so the real harness, tools and long-horizon control loop
  remain part of the learning system.

The product-specific contribution is integrating those principles with a
local-first owner computer, strict external-effect authority, self-repair
candidates, phone-supplied form data, market learning and cost-aware model
routing in one owner workflow.

---

## SAME-RUN HANDOFF: 1.5 → 1.6

После закрытия 1.5 **или** достижения `V1_5_NEAR_COMPLETE` этот же owner-run сразу продолжает работу в отдельной ветке 1.6:

[feat/bossman-1.6-secret-intake-20260925](https://github.com/molotroka123-cell/AiMaxBossman/tree/feat/bossman-1.6-secret-intake-20260925)

`V1_5_NEAR_COMPLETE` = нет software P0 и release-blocking P1, основные targeted/regression/hidden tests зелёные, canonical SHA зафиксирован, а остаток относится только к owner-live/soak/внешней среде.

Не ждать отдельного следующего дня. Цель одного прогона:
`BOSSMAN_1_5_CLOSED -> BOSSMAN_1_6_CLOSED`.

Evidence и финальные статусы 1.5 и 1.6 сохраняются раздельно по своим SHA.

---

## Immediate handoff to Bossman 1.6

As soon as Bossman 1.5 reaches OWNER_READY closure — or only explicitly non-blocking P2 / optional OWNER_REQUIRED items remain — continue the SAME owner run in:

[Bossman 1.6 self-evolution branch](https://github.com/molotroka123-cell/AiMaxBossman/tree/feat/bossman-1.6-self-evolution-20260925)

Start with `BOSSMAN_1_6_START_HERE.md`.

One-run objective:

`BOSSMAN_1_5_OWNER_READY -> 1.6 HANDOFF -> BOSSMAN_SELF_EVOLUTION_RUNNING`

Do not use this handoff to hide an open 1.5 P0 or release-blocking P1. The goal is to close both stages in one continuous owner run whenever the critical 1.5 gates allow it.
