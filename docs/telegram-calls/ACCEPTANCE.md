# Telegram calls — acceptance table

Status of the whole module: **NOT VERIFIED. No real Telegram call has ever been placed by this module.**
Everything marked `unit`/`loopback` proves control flow and plumbing of OUR code on fake engines and a fake
transport, never a real call, never speech quality.

Legend. Status: `PASS` (proven at the stated evidence level), `FAIL`, `BLOCKED` (cannot be run yet, cause named),
`NOT_RUN`. Evidence level: `unit` (fakes, in-process), `loopback` (whole session on the loopback transport),
`installed` (real `bossman` from an installed archive), `owner-live` (real two-account Telegram call on the
owner's machine). Only `owner-live` can prove a real call. `OWNER_REQUIRED` = the owner must supply something.

## Table

| # | Criterion | Status | Evidence level | Evidence / note |
|---|---|---|---|---|
| 1 | Calls off by default; one confirmed peer, not the own account; dial has no peer parameter | PASS | unit | `tests/telegram_calls/test_guard.py`, `test_settings.py` |
| 2 | Dial exactly once, no auto-redial in any outcome; uncertain outcome needs explicit confirm | PASS | loopback | `test_session.py`, `test_guard.py` |
| 3 | STOP silences audio synchronously, starts no new generation, post-STOP summary is mechanical | PASS | loopback | `test_session.py`, `test_playout.py`, `test_stopflag.py` |
| 4 | Barge-in, echo gate, silence and connection-loss handling | PASS | loopback | `test_session.py`, `test_echo.py`, `test_endpointer.py` (scripted signals, not real acoustics) |
| 5 | Secrets (api_id/api_hash/session) only in the existing Vault, never in logs/events/responses | PASS | unit | `test_credentials.py`, `test_login.py` (fake Telethon client); doctor leak test in `test_doctor.py` |
| 6 | Add-on installer: verified sha256 before unpack, path-traversal-safe, https + pypi hosts only, idempotent, refuses unhashed | PASS | unit | `test_addons.py` (fake wheels; bad hash, traversal, foreign host, unhashed all rejected) |
| 7 | Add-on installer really installs telethon + pyaes + rsa + pyasn1 | BLOCKED | - | hashes not verifiable offline: marked `UNPINNED_NEEDS_HASH`, installer refuses (see OPEN_QUESTIONS Q1) |
| 8 | py-tgcalls 3.0.0 / ntgcalls 3.0.0 wheels install from PyPI and load on the owner's Python 3.12 | NOT_RUN | installed | needs network + Windows; pins are recorded, never downloaded here |
| 9 | Doctor: PASS/WARN/BLOCKED per check with a Russian remedy, no model load, no secret | PASS | unit | `test_doctor.py` |
| 10 | Doctor on a real installed archive with real models | NOT_RUN | installed | owner runs `bossman call doctor` |
| 11 | `scripts/bossman_doctor.py` reports the calls module as WARN, never BLOCKED | PASS | unit | `test_doctor.py::test_script_check_*` |
| 12 | Offline self-test labelled «ТЕСТ БЕЗ TELEGRAM», never claims a real call | PASS | loopback | `test_selftest.py`; scripted engines only, WER not measured |
| 13 | Self-test with real Whisper + Piper: WER and latency p50/p95 over 10 turns | NOT_RUN | installed | needs Whisper model + Russian Piper voice on the owner's machine |
| 14 | Real transport (py-tgcalls/ntgcalls) frame format, dial, events, hangup | NOT_RUN | owner-live | unit-tested against a fake engine (`test_pytgcalls_transport.py`); real engine untouched |
| 15 | Real STT/TTS/brain engines (faster-whisper, Piper, companion local LLM) | NOT_RUN | installed | unit-tested with injected fakes (`test_stt.py`, `test_tts.py`, `test_brain.py`); no real model was loaded |
| 16 | Post-call memory summary + task drafts (never executed) | PASS | unit | `test_postcall.py`; not run against the real memory service |
| 17 | API `/api/telegram/calls/*`, dashboard panel «Telegram-звонки», `bossman call ...`, command-bar block, STOP hangup | NOT_RUN | - | owned by Line C; not verified by Line D |
| 18 | Verification through the real installed `bossman` and real UI (Playwright) | NOT_RUN | installed | not done |
| 19 | Owner login (number, code, 2FA) and second-account selection | NOT_RUN | owner-live | OWNER_REQUIRED |
| 20 | Real outgoing call to the second account, two-way talk | NOT_RUN | owner-live | OWNER_REQUIRED |
| 21 | Several turns with context in a real call | NOT_RUN | owner-live | OWNER_REQUIRED |
| 22 | Barge-in in a real call | NOT_RUN | owner-live | OWNER_REQUIRED |
| 23 | STOP during a real call, then manual restart | NOT_RUN | owner-live | OWNER_REQUIRED |
| 24 | Latency: end of user speech -> first outgoing frame, p50/p95 over >= 10 turns | NOT_RUN | owner-live | no number is claimed; see `LATENCY.md` |
| 25 | Intelligibility: owner score 1-5 and WER of own TTS through STT | NOT_RUN | owner-live | OWNER_REQUIRED |
| 26 | Echo: false barge-ins and self-replies counted | NOT_RUN | owner-live | OWNER_REQUIRED |
| 27 | Stability: real call of >= 5 minutes | NOT_RUN | owner-live | OWNER_REQUIRED |
| 28 | Integration into the release line (ledger, lock recompute on the Windows runner) | NOT_RUN | - | feature branch only; `windows_bundle_lock.txt` untouched, extra `calls` is outside `runtime` |

## Owner test checklist (you only enter the number/code/2FA, pick the second account, answer and talk)

Prerequisites the owner provides once: a Windows machine with Bossman installed, the local models running
(companion routes), a Whisper model directory, a Russian Piper voice, a SECOND Telegram account that is in the
main account's contacts and allows calls from it.

1. `bossman call install`. If it says `UNPINNED_NEEDS_HASH`, stop and report that (nothing was installed; see
   OPEN_QUESTIONS Q1). Never install the packages by hand for the test.
2. `bossman call doctor`. Every line must be PASS (WARN on «аккаунт не подключён» / «собеседник не выбран» is fine
   at this point). Any BLOCKED line has its remedy printed in Russian; fix it and repeat.
3. `bossman call selftest`. Expect the label «ТЕСТ БЕЗ TELEGRAM». With real models present it prints WER and
   latency over 10 turns; write those two numbers down. It does not call anyone.
4. Open the dashboard, page «Telegram-звонки». On the connect screen enter, yourself and only there:
   api_id and api_hash (from my.telegram.org), your phone number, the code Telegram sends you, the 2FA password
   if asked. Do not paste any of it into chat, files or Telegram messages.
5. Pick the SECOND account from the contacts and confirm it. Switch «Разрешить звонки» on.
6. On the second account keep Telegram open and be ready to answer.
7. Press «Позвонить». Answer on the second account and talk: 5+ turns in Russian, refer to something you said
   earlier (context), interrupt the assistant once (barge-in), stay in the call at least 5 minutes.
8. Press STOP once during a reply: the voice must cut at once. Then start a manual new call (it must ask for
   confirmation only if the previous outcome was uncertain).
9. Afterwards write down: your intelligibility score 1-5, how many times it answered itself or reacted to its own
   echo, and copy the latency line (p50/p95, n) from the call record. Report failures as they are.

Until steps 4-9 are done and recorded here, rows 19-27 stay `NOT_RUN` and the module is not called verified.
