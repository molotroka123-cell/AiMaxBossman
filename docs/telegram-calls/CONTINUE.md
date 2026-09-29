# CONTINUE — one next action

Branch: `claude/telegram-live-calls-ah9gwl` (base `release/bossman-owner` @ `90a807b`). Master prompt: `CLAUDE_MASTER_1_9.md`.

**Next action:** implement `call/pytgcalls_transport.py` (real private-call transport) and the speech adapters (`speech/stt.py`, `speech/tts.py`,
`speech/brain.py`), then `call/worker.py` + `call/manager.py`, then API/UI/CLI — in that order (see section 3 of the master prompt).

Checkpoint 1 state: audio core, session state machine, loopback transport, credentials/guard/login layer — **140 unit tests passing** (fakes; no real Telegram).
Not done: everything in `ACCEPTANCE.md` rows 6–14.

Tracks against the same-product Terminal Run contract: nothing here creates a second brain, memory, task queue or secret store.
North Star ladder: unchanged by this module.
