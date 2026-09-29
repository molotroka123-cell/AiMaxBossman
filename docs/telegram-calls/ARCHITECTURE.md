# Telegram live calls — architecture

Status: **IMPLEMENTATION IN A FEATURE BRANCH. NOT INTEGRATED INTO `release/bossman-owner`, NOT CERTIFIED.**
A real two-way Telegram call has **not** been verified yet (see `ACCEPTANCE.md` for the PASS / FAIL / BLOCKED / NOT_RUN table).

## Same-product contract (Terminal Run 1.2 rule)

This module is only another control surface of the SAME Bossman. It adds **no** second brain, memory
database, model fleet, task queue or secret store:

| Need | Reused existing component |
|---|---|
| local LLM routes, persona, owner conversation context | `bcc.telegram_companion` (`config.load`, `Models` routes main/fast, `Store.history/profile`) |
| secrets at rest (api_id/api_hash/session) | `bcc.secrets.Vault` (Fernet, key file 0600 / `BOSSMAN_VAULT_KEY`) + `bcc.auth._restrict_to_owner` |
| STT engine | `faster-whisper` (already the optional `speech` extra, `bcc.oss.whisper` model directory convention) |
| memory of the call outcome | the existing memory path (see `MEMORY.md` section below) |
| STOP | existing global STOP (`/api/computer/stop`, `bossman stop --all`, Telegram `/stop`) + call-local STOP |
| UI / CLI | dashboard panel + `bossman call …` (thin client of the Command Center API) |

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
  transport RX PCM ─► resample 16k ─► VAD/endpointer ─► streaming STT ─► Brain (companion local LLM, streamed)
        ▲                    │ barge-in / echo guard                                  │ sentence chunks
        │                    ▼                                                        ▼
  transport TX PCM ◄─ paced playout queue (generation-tagged, flush on barge-in/STOP) ◄─ streaming TTS
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

## MEMORY

After a call ends (`postcall.py`) only a SHORT summary goes to the existing memory service (same path as
`memory.write`, kind `session`, tags `telegram-call`, `call-<id>`, idempotent file name derived from the call id).
Agreed tasks become inert drafts (`status="draft"`, `run_now=false`, `client_request_id=call-<id>-t<n>`) that never
run without the owner's normal approval. No transcript and no audio are stored unless the owner opted in. After STOP
the summary is mechanical and proposes no tasks; a loopback (self-test) call writes nothing. Nothing new: no second
memory store.

## OSS (what is adopted, pinned, and how it is installed)

Adopted, not rebuilt: `py-tgcalls==3.0.0` + `ntgcalls==3.0.0` (voice transport), `telethon==1.45.0` (MTProto user
login), `faster-whisper` (STT, existing `speech` extra), Piper (TTS), Silero VAD through `pysilero-vad`, the
Telegram companion's own local model routes (brain). The calls packages are the optional extra `calls` in
`command-center/pyproject.toml`, deliberately OUTSIDE `runtime`, so `tools/windows_bundle_lock.txt` is unchanged.
The embedded Python has no pip: `bossman call install` (`addons.py`) downloads pinned wheels into
`<data_dir>/addons/telegram-calls/packages`, only over https from pypi.org / files.pythonhosted.org, verifies sha256
against OUR pin before unpacking, unpacks path-traversal-safe, is idempotent and owner-triggered only. Artifacts without a
verified hash are refused, never skipped (currently telethon, pyaes, rsa, pyasn1 are `UNPINNED_NEEDS_HASH`; see
`OPEN_QUESTIONS.md`). The worker calls `addons.activate(data_dir)` to put that directory on `sys.path` (it runs with
`-I`). `bossman call doctor` (`doctor.py`) reports every prerequisite as PASS/WARN/BLOCKED with a Russian remedy
and loads no model; `scripts/bossman_doctor.py` shows the module as PASS or WARN only.

## LATENCY

The metric is end of the interlocutor's speech -> first outgoing PCM frame, per turn and with a per-stage breakdown,
summarised as p50/p95. `bossman call selftest` (`call/selftest.py`, «ТЕСТ БЕЗ TELEGRAM») exercises the whole
session on the loopback transport; with real Whisper and Piper it also reports WER, otherwise it runs scripted
engines and says so. No real-call latency exists yet: see `LATENCY.md` for what was measured (plumbing only) and
what was not.

## ACCEPTANCE

`ACCEPTANCE.md` is the single PASS/FAIL/BLOCKED/NOT_RUN table with evidence levels and the owner test checklist.
`CONTINUE.md` names the one next step; `OPEN_QUESTIONS.md` is the owner's non-blocking list.
