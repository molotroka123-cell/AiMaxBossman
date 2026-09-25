# Bossman 1.5 — Codex owner run pointer

**Do not execute this file as an independent runbook.**

The canonical, current owner directive is:

`docs/owner/CODEX_BOSSMAN_1_5_RUN_20260925.md`

That file owns:
- the exact K1m6a YouTube source and 2026-08-14..2026-08-27 window;
- Bossman API execution through `/api/v15/economy/*`;
- three independent free Nemotron roles;
- free Ling coding/test work;
- Jev routing;
- the capped paid GLM finalizer;
- anti-lookahead rules;
- TradingMemory vs generic LearningStore promotion boundaries;
- Codex/Aster token-saving responsibilities;
- the final evidence report.

Bossman 1.0 status must be read fresh from `release/bossman-owner`; this pointer
does not claim that 1.0 is certified.

Do not fork a second 1.5 master prompt. Update the canonical owner directive
instead if the run changes.

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
