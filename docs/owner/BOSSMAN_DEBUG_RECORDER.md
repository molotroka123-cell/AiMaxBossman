# Bossman owner-run debug recorder

Status: first diagnostic version, frozen for the next owner run. It is a
standalone observer and is not enabled automatically with Bossman.

## Purpose

The recorder correlates the owner's mouse actions with newly written runtime
records from Bossman, Jev, Telegram, Ollama, Claude and Codex. It continues to
write its own trace when the Bossman process exits, which helps distinguish a
UI failure from a backend, model-route or delivery failure.

## Start and stop

The installer creates `Bossman Debug Recorder.lnk` on the Windows desktop:

```powershell
powershell -ExecutionPolicy Bypass -File tools\bossman_debug_recorder\install_desktop_shortcut.ps1
```

For the next owner run:

1. Open `Bossman Debug Recorder` from the desktop before Bossman.
2. Leave its console window open throughout the run.
3. Perform the owner flow normally.
4. Run `tools\bossman_debug_recorder\STOP-RECORDER.cmd`, or press `Ctrl+C`
   in the recorder console.
5. Inspect the latest timestamped directory under
   `tools\bossman_debug_recorder\runs` in a source checkout. A copied desktop
   package writes under its own `runs` directory.

Each recording contains:

- `clicks.jsonl`: timestamp, mouse button, coordinates, foreground window,
  process and PID;
- `runtime-stream.jsonl`: new records appended after recorder startup;
- `status.txt`: lifecycle state;
- `SUMMARY.md`: duration and record counts;
- `LATEST.txt`: path to the last completed recording.

## Privacy boundary

The recorder does not capture keyboard input or screenshots. It starts at the
end of every existing watched file, so old conversations are not copied.
Common API key, token, password and Bearer patterns are redacted before a line
is persisted. Review a trace before attaching it to an issue because arbitrary
application text may still contain private information outside known patterns.

## Watched locations

- `%LOCALAPPDATA%\Bossman`
- `%USERPROFILE%\.bossman`
- `%USERPROFILE%\.claude\projects`
- `%USERPROFILE%\.codex\sessions`
- `%USERPROFILE%\.ollama\logs`

Only newly appended `.log`, `.jsonl` and `.ndjson` records are collected.
An answer rendered only on screen and never written by its application cannot
appear in the runtime stream; its triggering click and foreground process are
still recorded.

## Current limitations for tomorrow's run

- No screenshot or OCR capture.
- No browser network interception.
- No keyboard or prompt capture outside application-owned logs.
- No automatic startup with Bossman in this first version.
- No completeness verdict is generated automatically; Codex/Claude should
  analyze the resulting timeline after the run.

These limits are intentional for the first run. Extend the recorder only after
observing what evidence is missing.
