# Telegram live calls — acceptance table

Verdicts: **PASS / FAIL / BLOCKED / NOT_RUN**. Level of evidence is stated per row: `unit` (fakes/loopback, pure control-flow),
`sandbox` (this cloud session: no Telegram, no model weights), `installed` (Bossman installed product), `owner-live` (owner's machine, real accounts).
A stub, a loopback run or a successful API request is **not** a conversation.

Same-product contract: dashboard panel, `bossman call …` and the worker use the ONE Command Center backend (see `ARCHITECTURE.md`).
North Star ladder: this module does not advance it; the achieved level stays whatever the latest release evidence says.

_Status: branch `claude/telegram-live-calls-ah9gwl` on the 1.9 line, after the S7 / one-prefix / audit-fix commits. See `CONTINUE.md`. Real two-way call: **NOT DONE**._

| # | Check | Level | Verdict | Note |
|---|---|---|---|---|
| 1 | Audio core: PCM/resampler, VAD, endpointer, echo guard, playout (incl. negative controls) | unit | PASS | `tests/telegram_calls/test_audio_*`, `test_endpointer`, `test_echo`, `test_playout` |
| 2 | CallSession control flow: barge-in, echo gate, STOP (also while ringing and before run), hangup while ringing, no redial, timeouts, outcomes, context | unit (loopback) | PASS | `test_session.py`; the three ringing/before-run tests fail on the previous code (negative control) |
| 3 | Credentials in Vault, calls-off default, one-peer guard, STOP flag (also over an unwritable file), uncertainty state | unit | PASS | `test_account_store.py`, `test_hardening.py` |
| 4 | Telethon login flow (phone -> code -> 2FA), error mapping | unit (fake client) | PASS | `test_account_login.py`; the real login is row 13 |
| 5 | py-tgcalls/ntgcalls transport, worker IPC, manager, API, `bossman call` | unit / offline e2e | PASS | 437 tests of `tests/telegram_calls` + `test_restrict_to_owner_directory` green on Windows (venv, this branch) |
| 6 | Jeff as the brain (call surface S1-S5), doctor row S6, calls plane in owner STOP-all S7 | unit | PASS | `test_jeff_call_surface`, `test_jeff_engines`, `test_doctor_row`, `test_owner_stop_all_hangs_up_a_live_call_and_never_redials` |
| 7 | One API prefix `/api/telegram/calls`, events `telegram_call.*`, no alias | unit | PASS | `test_there_is_one_api_prefix_and_no_calls_alias`; UI/CLI/docs use it |
| 8 | Panel «Telegram-звонки» in a real browser: empty state without console/4xx errors, disabled buttons explain themselves, owner path, STOP, no redial from the panel | browser (offline test mode) | PASS | `test_panel_browser.py`; `node --test ui/tests/telegram_calls.test.mjs` 19/19 (Node 20 needs `--experimental-detect-module`) |
| 9 | Add-on install (`bossman call install`), `doctor`, `selftest basic` on a fresh data dir | installed (dev venv) | PASS (doctor WARN) | doctor WARN: Jeff voice env (Piper/Whisper paths) unset in the dev venv; selftest label «ТЕСТ БЕЗ TELEGRAM». Windows-lock install on the owner machine: NOT_RUN |
| 10 | Independent read-only audit (secrets, STOP races, redial, peer guard, pit perimeter, ACL) | audit | PASS after fixes | High findings fixed: STOP lost while engines load; STOP/hangup ignored while ringing; empty DACL (root cause: directory grant without inheritance). Open (low): `global_stop_active` fails open on a read error; self-dial guard skipped while `me_id` is unknown; call turns skip `participant_profile.gate_reply`; UI «confirm unknown» box stays ticked |
| 11 | Latency end-of-speech -> first outgoing frame (p50/p95, >=10 turns, STT/LLM/TTS split) | — | NOT_RUN | no number is claimed before a measurement on the owner machine |
| 12 | Echo / intelligibility (1-5) / stability >= 5 min | owner-live | NOT_RUN | needs row 14 |
| 13 | Owner local login (api_id/api_hash, phone, code, 2FA) | owner-live | BLOCKED | typed only by the owner, locally; never through chat |
| 14 | One real two-way call to the chosen second account (dial once, no auto-redial) | owner-live | BLOCKED | needs row 13 and the owner |
| 15 | Multi-turn context, barge-in, STOP, manual re-dial in the real call | owner-live | BLOCKED | needs row 14 |
