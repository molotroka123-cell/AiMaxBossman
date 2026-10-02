# Telegram live calls — acceptance table

> **STATUS 2026-09-29 — частично устарел.** Верно то, что сказано в `CLAUDE_MASTER_1_9.md` (главнее этого файла): база — линия 1.9 (`feat/bossman-1.9-freeze-20260929`), мозг звонка — Jeff (`bcc/pit/call_surface.py`, не telegram_companion), секреты — Vault в каталоге данных Bossman, API — `/api/telegram/calls/*` (алиас `/api/calls/*`). Реальный двусторонний звонок: NOT_RUN.


Verdicts: **PASS / FAIL / BLOCKED / NOT_RUN**. Level of evidence is stated per row: `unit` (fakes/loopback, pure control-flow),
`sandbox` (this cloud session: no Telegram, no model weights), `installed` (Bossman installed product), `owner-live` (owner's machine, real accounts).
A stub, a loopback run or a successful API request is **not** a conversation.

Same-product contract: dashboard panel, `bossman call …` and the worker use the ONE Command Center backend (see `ARCHITECTURE.md`).
North Star ladder: this module does not advance it; the achieved level stays whatever the latest release evidence says.

_Status as of the current branch head — see `CONTINUE.md` for the next step._

| # | Check | Level | Verdict | Note |
|---|---|---|---|---|
| 1 | Audio core: PCM/resampler, VAD, endpointer, echo guard, playout (incl. negative controls) | unit | PASS | `tests/telegram_calls/test_audio_*`, `test_endpointer`, `test_echo`, `test_playout` |
| 2 | Silero VAD + endpointer on real Russian speech (espeak-ng synthetic voice) | sandbox | PASS | start/end detected, one-word answer kept; robotic voice, not the owner's |
| 3 | CallSession control flow: barge-in, echo gate, STOP, no redial, timeouts, outcomes, context | unit (loopback) | PASS | `tests/telegram_calls/test_session.py` (23 tests) |
| 4 | Credentials in Vault, calls-off default, one-peer guard, STOP flag, uncertainty state | unit | PASS | `test_account_store.py` |
| 5 | Telethon login flow (phone → code → 2FA), error mapping | unit (fake client) | PASS | `test_account_login.py`; real Telegram login is row 12 |
| 6 | Real transport (py-tgcalls/ntgcalls) | — | NOT_RUN | not implemented at this checkpoint |
| 7 | STT / TTS / brain adapters on real local models | — | NOT_RUN | model weights are not downloadable in the sandbox (egress policy) |
| 8 | Worker / manager / API / dashboard panel / `bossman call` | — | NOT_RUN | not implemented at this checkpoint |
| 9 | Offline self-test through API + real `bossman` + UI | — | NOT_RUN | after row 8 |
| 10 | Latency end-of-speech → first outgoing frame | — | NOT_RUN | no number is claimed before a measurement |
| 11 | Echo / intelligibility / stability measurements | — | NOT_RUN | |
| 12 | Owner local login (phone → code → 2FA) on the real account | owner-live | BLOCKED | needs the owner; `api.telegram.org` / `my.telegram.org` are denied in this session |
| 13 | Real two-way call to the chosen second account | owner-live | BLOCKED | needs row 12 and the owner's second account |
| 14 | Multi-turn context, barge-in, STOP, manual re-dial in the real call | owner-live | BLOCKED | needs row 13 |
