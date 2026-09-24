# Owner test packet — 2026-09-24

Purpose: one handoff packet for **Aster → convergence first → Jev test → Twitch/local-AI E2E**.

## Read in this exact order

1. `docs/owner/ASTER_CONVERGE_THEN_JEV_MASTER_PROMPT.md`
2. `docs/owner/JEV_TWITCH_LOCAL_AI_E2E_ACCEPTANCE.md`
3. `docs/owner/MARKET_ANALYSIS_TELEGRAM_CONTRACT.md`
4. `docs/owner/JEV_TWITCH_COLLECTOR_RUNBOOK.md`
5. `docs/owner/JEV_TOMORROW.md`
6. `docs/JEV_DECISION_ENGINE.md`
7. `docs/trading/BTC_ORDERFLOW_PLAYBOOK.md`
8. `docs/trading/LOCAL_MODEL_SYSTEM_PROMPT.md`
9. `data/trading/case_registry.json`
10. `data/trading/canonical_cases/*`
11. `data/trading/btc_casebook_2026_09.jsonl`

## Hard order

**DO NOT run Jev first.**

Required sequence:

`fetch → converge Exact + Twitch branch → prove merge → baseline tests → Jev selftest → Jev live shadow → Twitch calibration → live collection → deterministic analysis → local LLM explanation → Telegram owner delivery`

If convergence or baseline tests fail, stop there and fix them. A Jev PASS on an unconverged branch is not acceptable owner evidence.

## Known refs before this packet

- Bossman 1.0 Exact candidate: `c489cc62398b7213efbc820f10b64e57f4f054e5`
- Twitch/Jev collector implementation contained through: `2a74c63be9df0c85758bd82be741e38742836d48`
- Target working branch: `feat/jev-twitch-collector-20260924`

Do not force-push or rewrite history. Preserve both lines of work.

## Success definition

PASS only means a real owner-PC chain was demonstrated:

`Twitch fresh frame → verified CVD/OI/price → deterministic regime → local model explanation → Telegram delivery`

Jev is a routing/DOM assistant, **not** the source of chart numbers and not the trading authority.
