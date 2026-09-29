# CONTINUE — one next action

Branch: `claude/telegram-live-calls-ah9gwl` (draft PR #87, base `feat/bossman-1.9-freeze-20260929`). Authority: `CLAUDE_MASTER_1_9.md`.

**Next action (owner):** local login on the Telegram-calls screen (api_id/api_hash, phone, code, 2FA are typed ONLY by the owner, locally),
choose the second account through the UX with a confirmation, allow calls, one «Позвонить». No auto-redial. Then the measurements of
`ACCEPTANCE.md` rows 12-16 (latency p50/p95 over >=10 turns, echo, >=5 minutes, intelligibility 1-5).

Done and covered by tests: audio core, session state machine, py-tgcalls transport, worker/manager/API/panel/`bossman call`, Vault credentials,
add-on installer, Jeff call brain (S1-S5), doctor row (S6), calls plane in owner STOP-all (S7), one API prefix, browser acceptance of the panel.
Not done: any real Telegram call (NOT_RUN / BLOCKED on the owner). Nothing here advances the North Star ladder.
