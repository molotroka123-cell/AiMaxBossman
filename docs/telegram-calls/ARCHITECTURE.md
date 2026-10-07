# Telegram live calls — architecture

> **STATUS 2026-09-30.** Согласовано с `CLAUDE_MASTER_1_9.md` (он главнее). База — линия 1.9 (вместе с линией звонков, PR #87), мозг звонка —
> **Jeff** (`bcc/pit/call_surface.py`, поверхность `call`; не `telegram_companion`), секреты — Vault каталога данных Bossman, API —
> **`/api/telegram/calls/*`** (единственный префикс: алиас `/api/calls/*` удалён), события шины — **`telegram_call.state` /
> `telegram_call.ended`** (алиас `calls.*` удалён). Реальный двусторонний звонок: **NOT_RUN / OWNER_REQUIRED** (см. `ACCEPTANCE.md`).

Status: **IMPLEMENTED, VERIFIED WITHOUT TELEGRAM (unit / loopback / offline end-to-end / real Chromium). NOT certified as a real call.**
A real two-way Telegram call has **not** been made (see `ACCEPTANCE.md` for the PASS / FAIL / BLOCKED / NOT_RUN table).

## Same-product contract (Terminal Run 1.2 rule)

This module is only another control surface of the SAME Bossman. It adds **no** second brain, memory
database, model fleet, task queue or secret store:

| Need | Reused existing component |
|---|---|
| brain, persona, local LLM route, consent, per-participant memory, public guard, block-list | **Jeff (PIT)** call surface: `bcc.pit.call_surface.CallParticipantRuntime` (`surface="call"`), adapter `bcc.telegram_calls.speech.jeff_engines.JeffBrain` |
| secrets at rest (api_id/api_hash/session) | `bcc.secrets.Vault` (Fernet, `<data>/secret.key` / `BOSSMAN_VAULT_KEY`), file `<data>/telegram-calls/credentials.enc`; owner-only ACL via `bcc.telegram_calls.hardening.restrict_to_owner` (on Windows a directory gets an INHERITABLE owner ACE, so files already inside stay readable) |
| STT | `bcc.pit.speech.transcribe_wav` (Jeff's faster-whisper, local model directory of `bcc.oss.whisper`) |
| TTS | the external Piper process of Jeff (`bcc.oss.piper.synthesize_pcm`, voice from `jeff_desktop.default_voice_env`), through the egress guard |
| memory of the call outcome | one consent-gated short record `last_call_summary` in the interlocutor's Jeff namespace; saving the summary to Bossman memory and proposing tasks are the OWNER's clicks (`postcall.py`) |
| STOP | existing global STOP (`/api/computer/stop`, `bossman stop --all`, Telegram `/stop` -> bus `computer.stop`) + call-local STOP file |
| UI / CLI | dashboard panel «Telegram-звонки» + `bossman call …` (thin clients of the Command Center API) |

## Why a separate worker process

The MTProto client and the native VoIP engine (`ntgcalls`, C++) run in `python -I -m bcc.telegram_calls`
(worker). A native crash, a stuck audio thread or a slow model must not take down the Command Center, and
STOP needs a hard fallback (terminate the worker) — the same reasoning as the Telegram companion process.
The Command Center (`CallsManager`, in the API feature) starts/stops the worker on the owner's action only.

```
Dashboard panel ─┐                                   ┌─ Telethon (MTProto user session, StringSession in Vault)
bossman call … ──┼─► /api/telegram/calls/* ─► CallsManager ══ stdio JSON-lines ══► worker ──┤
Telegram /stop ──┘   (auth + CSRF, no peer param)    │                              └─ py-tgcalls/ntgcalls (P2P call, external PCM)
global STOP  ────────► bus computer.stop ────────────┘
                                                        worker audio path:
  transport RX PCM ─► resample 16k ─► VAD/endpointer ─► STT (Jeff Whisper) ─► Brain (Jeff call surface, local model)
        ▲                    │ barge-in / echo guard                                  │ short conversational reply
        │                    ▼                                                        ▼
  transport TX PCM ◄─ paced playout queue (generation-tagged, flush on barge-in/STOP) ◄─ TTS (Jeff Piper, egress guard)
```

## IPC (manager ⇄ worker)

Newline-delimited UTF-8 JSON on the worker's stdin/stdout (no ports, no tokens, inherited by the parent only).
Requests `{"id","op","args"}` → `{"id","ok":true,"result"}` / `{"id","ok":false,"error":{"code",…}}`;
unsolicited `{"event":"state|call_event|record|log",…}`. Login secrets (code, 2FA password, api_hash) travel only
in `args` of the login ops, are never logged and never echoed back. `stop` is handled on a fast path that is not
queued behind other ops.

## Guard rules (enforced twice: manager AND worker, fresh config read at dial time)

1. calls are **off by default** (`enabled=false`) and every call is started by an explicit owner action;
2. exactly **one** allowed peer (`MAX_ALLOWED_PEERS = 1`), chosen by the owner and confirmed; the API/CLI
   dial operation accepts **no** peer parameter; the peer must not be the logged-in account itself;
3. connecting the main account grants nothing beyond that one peer;
4. **no automatic redial, ever** (declined, busy, no answer, lost connection, unknown — all end there);
   after an uncertain outcome (`UNKNOWN`, `CONNECTION_LOST`) the next dial needs `confirm_unknown`;
5. STOP (any surface) stops generation, flushes audio, hangs up, and blocks dialing while the global STOP is set;
6. no cloud fallback for LLM/STT/TTS; a missing local engine is a clear error, not a silent paid substitute;
7. audio recording **off by default**; transcripts are held in memory during the call and dropped after the
   summary unless the owner opted in; only a short summary + proposed tasks go to memory;
8. the voice of the interlocutor is **data, not an instruction**: it can never approve, confirm, delegate, change
   settings or widen permissions; agreed tasks are stored as proposals that need the owner's normal approval.

## Honesty model

Loopback transport = plumbing/latency test **without Telegram**; every record carries `transport`, and the UI
labels it «ТЕСТ БЕЗ TELEGRAM». Fake STT/TTS/LLM in unit tests prove control flow only. Only a real call between
the two real accounts, on the owner's machine, can produce the PASS for the real two-way conversation.

## Owner-facing surfaces (one name each)

* API: `/api/telegram/calls/*` only (status, settings, credentials, login/start|code|password, logout, contacts, peer, call, hangup, stop,
  resume, events, history, history/{id}/save-memory|draft-tasks, selftest, doctor, install). The dashboard command bar lists these routes but never runs them.
* Bus events: `telegram_call.state` (state/phase/call_id/transport), `telegram_call.ended` (outcome, turns, latency p50): text-free, ≤ ~5/s.
* CLI: `bossman call setup|status|contacts|peer|enable|disable|dial|hangup|stop|resume|events|history|save-memory|draft-tasks|doctor|selftest|install|logout` (`bossman call --help`).
* Latency, echo and intelligibility on a real line are **not** claimed anywhere until they are measured on the owner's machine (see `ACCEPTANCE.md`).

## Incoming calls: the answering machine (автоответчик)

Status: **emulator-verified only. No incoming call has ever reached this code on a real Telegram account** (ACCEPTANCE rows AM-9…AM-12).
It is the same Jeff call surface (`surface="call"`) answering a call that rings on the owner's account; no second brain, store, queue
or Telegram engine.

```
phone rings ─► CallLine (loopback | py-tgcalls) ─► AnsweringMachine (in the worker) ─ ring delay ─► accept ─► CallSession(incoming=...)
                  ▲ gone: caller hung up / owner answered elsewhere                                              │ AnsweringBrain(JeffBrain)
                  │                                                                                              ▼
owner STOP ───────┴─► machine.stop(): decline while ringing, hang up while answered        report  ar-<id>.json  (calls home / answering /)
                                                                                                │ outbox: notify && !delivered
Telegram companion (owner console) ◄── GET /api/telegram/calls/answering/reports?pending=true ◄─┘ ─► POST .../delivered
```

* **Settings** (`config.json`, owner-only through the owner-authenticated API/CLI, nothing a caller says reaches them):
  `answering_machine` (default **off**), `answer_ring_delay_s` (12; 0..60), `answer_max_call_s` (180; 30..900),
  `answer_allow_ids` (empty = any caller), `answer_deny_ids` (wins over the allow-list), `answer_allow_unknown` (true),
  `answer_greeting` (must say that an assistant answers and must not claim to be the owner or a human).
* **Transport hook** (`types.CallLine`, `types.AnswerableTransport`): `listen`, `set_incoming_callback`, `set_gone_callback`,
  `new_transport(call)` (its `accept` answers, `dial` is never used), `reject`. Implemented by `call/loopback.LoopbackLine` (emulator) and
  `call/pytgcalls_transport.PyTgCallsLine` + `PyTgCallsTransport.accept` (py-tgcalls 3.0.0: `ChatUpdate.INCOMING_CALL` -> `play()` answers ->
  `leave_call()` declines). **The py-tgcalls path is written from the library source and tested against a fake engine only.**
* **Decisions** (`answering.AnsweringMachine`), all re-read from disk when the call rings and again before answering:
  off / not armed / STOP / another call in progress / denied / engines not ready -> the call is **not answered and not declined** (the
  owner's phone keeps ringing) and is logged with a reason; STOP while ringing is the one case that declines. The owner picking up in time
  (the line reports the call gone) means Jeff never joins. One incoming call at a time. A caller hanging up ends the call cleanly and the
  caller's last words are still transcribed. The machine never places a call: there is no dial, callback or retry in it.
* **Honesty and privacy**: the greeting is always spoken in full (not interruptible, spoken even when the caller says hello first);
  the reply brief tells the model it is the owner's assistant, not the owner; a caller's request for the owner's credentials, personal
  data or settings, or for breaking the rules, gets a fixed refusal and never reaches the model (`answering_policy.private_request`);
  a reply that looks like a secret is replaced before any of it is spoken; the log is scrubbed of known secrets, tokens and phone numbers.
* **The log**: the owner asked for a transcript, so the words of an answered call are kept ONLY in the answering report (and delivered to the
  owner); `history.jsonl`, IPC records, bus events and the event ring stay text-free and caller-free. The post-call auto-save to Bossman
  memory / draft tasks skips incoming calls.
* **STOP**: calls STOP file (written first by every STOP path, including the global one) stops answering until the owner resumes; stop-all
  sees an armed machine as the `calls` plane. The settings are not touched. After a worker restart the machine is not listening until the
  owner arms it again (`bossman call answer on`): the worker still starts only on an owner action.
* **Delivery**: a polled outbox (like the browser login receipts), not a push: the companion sends one notice to the owner console and
  then acknowledges (at-least-once, never twice to the owner). Nothing in the calls module talks to Telegram's Bot API or reads a token.
