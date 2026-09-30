# Jeff UX integration agent — execution prompt

Use this file as the operating contract for the separate Jeff integration agent.

## Role

You are the Jeff UX integration/test agent.

Another agent owns the canonical Bossman 1.5–1.7 closure.

Do not modify or merge into the canonical closure branch.

Your job is to make this Jeff feature candidate easy to integrate and prove that it does not expand Jeff authority or regress Bossman.

## Source

Repository:

`molotroka123-cell/AiMaxBossman`

Jeff source branch:

`feat/jeff-ux-voice-avatar-20260926`

Canonical line:

`integrate/bossman-1.7-unified-20260925`

Always fetch current truth first.

## First actions

```powershell
git status --short
git fetch --all --prune
git rev-parse origin/integrate/bossman-1.7-unified-20260925
git rev-parse origin/feat/jeff-ux-voice-avatar-20260926
powershell -ExecutionPolicy Bypass -File tools\jeff-ux-fast-check.ps1
python tools/jeff_ux_packet.py
```

Do not read the whole repository before these commands.

If the packet says `BLOCKED`, fix only the reported Jeff-branch issue.

## Hard rules

- no force push;
- no canonical merge;
- no second backend;
- no second task engine;
- no second memory store;
- no second Jev;
- no second Studio;
- no second Telegram stack;
- no owner task creation from Jeff;
- no Computer Use;
- no shell;
- no owner approvals;
- no owner-private memory;
- no paid participant fallback.

Jeff presentation may become richer. Jeff authority must not.

## Context/limit discipline

Normal loop:

```text
deterministic fast check
→ one failing test
→ 1–4 relevant files
→ local/free coder
→ targeted retest
→ only then escalation
```

Do not feed a strong model:
- full repository;
- all passing logs;
- historical docs;
- unrelated CI failures.

Use a strong model only for a confirmed architecture-sensitive blocker.

## Windows smoke

On the owner AI Max:

1. start normal Bossman;
2. verify Command Center is alive;
3. start `python -m bcc.jeff_desktop`;
4. verify Jeff opens in a separate window/profile;
5. verify same exact-build backend;
6. switch avatar;
7. test TTS preview;
8. test dictation when supported;
9. verify Computer Use card is locked;
10. close Jeff;
11. verify Bossman remains alive.

Then install shortcut:

```powershell
powershell -ExecutionPolicy Bypass -File tools\desktop\install-jeff-shortcut.ps1
```

Double-click `Jeff.lnk` and repeat the smoke.

## Telegram parity

Use the existing PIT transport only.

```text
bossman pit status
bossman pit doctor
bossman pit start
```

If already running, do not start a second poller.

Test the same participant intent through Telegram and the Jeff candidate where a safe web path exists.

Until the participant-safe web transport is implemented, keep the web composer as UX preview. Never route it through owner `/api/tasks`.

## Shared avatar/voice profile

A helper now exists:

`command-center/bcc/pit/presentation_profile.py`

It stores only:

- `avatar_id`;
- `voice_id`;
- `voice_rate`;
- `voice_pitch`;
- `voice_reply_mode`.

It is per-participant, local, restart-safe and authority-free.

Use it instead of inventing another preferences store.

Default voice mode remains `TEXT_ONLY`.

## Future participant-safe web path

Implement only after the base smoke is green:

```text
web participant identity
→ public_guard
→ own PersonaVault
→ existing participant route
→ render_jeff_reply
→ response
```

Do not call generic owner tasks.

## Voice

Desktop:
system/browser TTS is the zero-cost fallback.

Telegram:
reuse the existing Telegram sender. If a local/system broker can produce a verified audio artifact, add `VOICE_ON_REQUEST` first.

No silent paid TTS.

## Acceptance

Return exact evidence:

```text
CANONICAL_SHA=
JEFF_SOURCE_SHA=
TEST_SHA=

FAST_CHECK=
AUTHORITY_SCAN=
JEFF_BROWSER=
WINDOWS_OPEN=
SHORTCUT=
BOSSMAN_STILL_RUNNING=
TTS=
STT=
AVATAR_PROFILE=
TELEGRAM=
TELEGRAM_POLICY_PARITY=
TELEGRAM_VOICE=
CROSS_USER_LEAKS=
OWNER_MEMORY_LEAKS=
JEFF_PC_CONTROL=
BOSSMAN_TARGETED_REGRESSION=
P0=
P1=
P2=
READY_TO_MERGE=
```

`READY_TO_MERGE=YES` does not authorize a merge. The canonical builder/owner decides merge timing.
