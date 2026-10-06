# Jeff UX integration handoff — 2026-09-26

Branch: `feat/jeff-ux-voice-avatar-20260926`

Purpose: give a separate integration agent a small, tested Jeff presentation layer without consuming context on the whole Bossman repository and without disturbing the 1.5–1.7 closure lane.

## What this branch contains

Runtime surface:

- `command-center/ui/jeff.html`
- `command-center/ui/jeff.css`
- `command-center/ui/jeff.js`
- `command-center/bcc/jeff_desktop.py`
- `tools/desktop/install-jeff-shortcut.ps1`

Tests:

- `command-center/tests/test_jeff_ux_isolation.py`
- `command-center/tests/test_jeff_ux_browser.py`
- `tests/test_jeff_ux_packet.py`

Agent helpers:

- `tools/jeff_ux_packet.py`
- `tools/jeff-ux-fast-check.ps1`

The branch intentionally does **not** change the canonical Bossman backend, task engine, PIT runtime, Telegram transport, memory authority, Studio, Jev or provider registry.

## First command for another agent

Do not read the whole repository first.

Run:

```powershell
git fetch --all --prune
powershell -ExecutionPolicy Bypass -File tools\jeff-ux-fast-check.ps1
python tools/jeff_ux_packet.py
```

The packet reports:

- current canonical SHA;
- Jeff branch SHA;
- merge base;
- exact changed files;
- unexpected files;
- executable authority findings;
- the minimal targeted test list.

If `STATUS=BLOCKED`, stop and fix the reported boundary before spending a strong-model call.

## Integration strategy

The canonical 1.5–1.7 line may advance while this branch is tested.

Do **not** merge this branch wholesale.

Create an isolated test branch from the latest canonical/frozen Bossman SHA and import only the runtime/test/helper files listed by the packet.

This prevents stale history from overwriting closure fixes.

## Jeff authority boundary

Jeff remains a participant assistant.

The UX must not directly call:

- owner task creation;
- `/api/control-plane`;
- terminal;
- browser control;
- OpenHands/coding owner APIs;
- shell;
- Computer Use.

The web composer currently stays a local UX preview by design. Live participant chat remains Telegram until a reviewed participant-safe web transport is wired through the existing PIT policy.

Visible "Computer Use" is allowed only as a disabled/locked capability card explaining that it belongs to the owner control plane.

## Desktop model

`python -m bcc.jeff_desktop`:

- opens `/jeff.html`;
- uses the same exact-build Command Center backend;
- uses a separate browser profile;
- refuses a different-build backend;
- must not kill a backend currently used by the normal Bossman desktop.

Separate shortcut:

```powershell
powershell -ExecutionPolicy Bypass -File tools\desktop\install-jeff-shortcut.ps1
```

This creates `Jeff.lnk` and leaves the Bossman shortcut unchanged.

## Voice / avatar foundation

Current zero-cost UX:

- local browser/system TTS through `speechSynthesis`;
- system speech recognition when available;
- avatar presets stored in local presentation preferences;
- voice/rate/pitch stored locally.

After the 1.5–1.7 freeze, move only presentation settings into the participant profile:

```text
avatar_id
voice_id
voice_rate
voice_pitch
voice_reply_mode
```

Suggested voice modes:

- `TEXT_ONLY` default;
- `VOICE_ON_REQUEST`;
- `VOICE_AUTO`.

Do not put secrets, owner state, risk scores or authority in the presentation profile.

## Telegram parity target

Reuse the existing PIT/Telegram stack.

Future path:

```text
Jeff Desktop/Web
  -> same participant identity
  -> public_guard
  -> own participant memory
  -> participant-safe Jev/model route
  -> presentation renderer

Telegram
  -> same participant policy
  -> same presentation profile
```

No second Telegram bot stack.

For voice replies:

```text
Jeff reply
  -> Voice Broker
  -> verified audio bytes
  -> existing Telegram sender
```

No silent paid TTS fallback.

## Regression requirement

Before any merge into canonical:

- Jeff isolation tests PASS;
- Jeff browser smoke PASS;
- PIT neighbours PASS;
- desktop build identity PASS;
- Bossman normal desktop still opens;
- Jeff closes without killing active Bossman;
- cross-user leaks = 0;
- owner-memory leaks = 0;
- Jeff PC control = DENIED.

A pretty UI is not acceptance evidence.
