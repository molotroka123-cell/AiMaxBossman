# 24H_SOAK_PASS attempt, started 10.10.2026 — status IN_PROGRESS (not a PASS)

What runs: `tools/ux_soak/soak.py` (repo's own UX/API/CLI soak) from an immutable snapshot of `44caf013`
(`git archive` -> `C:\Users\asd\Bossman\soak-20261010\src`), server mode (`python -m bcc`) + headless Playwright
Chromium + CLI/ConPTY chat, fake model stub (no live model, no paid calls), fresh data dir. Backend is killed and
restarted every 25 interactions (crash / running-task / idle), invariants checked after each restart (no lost task,
terminal status immutable, no stale `running` 150 s after restart, session/draft kept, CLI vs API agree).

- Launcher: `C:\Users\asd\Bossman\soak-20261010\run-soak.cmd` (copy here), started via WMI Win32_Process.Create
  (hidden, BELOW_NORMAL, detached from the Claude session). Command:
  `python -X utf8 src\tools\ux_soak\soak.py --launch server --port 8871 --stub-port 8878 --dead-port 8879
  --cdp-port 8877 --data-dir ...\data --out ...\out --profile ...\rc19-ux-profile-soak --pylib ...\pylib
  --minutes 1440 --max-interactions 1000000 --restart-every 25 --pause 1`
- Port 8871 (stub 8878, dead 8879). Data dir `C:\Users\asd\Bossman\soak-20261010\data` (not the owner root;
  soak.py refuses it). Owner services (:8801, Jeff, pult, BossmanOne-*) untouched.
- `pylib` = prompt_toolkit + wcwidth copied from the installed bundle 6de18f8d (system Python lacks them; the CLI chat
  needs them). Profile dir name contains `rc19-ux-profile` so soak.py's browser-RSS sampler sees it.
- PIDs: soak python 26628 (cmd wrapper 31184), monitor pythonw 25012. See `RUN.json`.
- Start 2026-10-10T14:28:41Z. Soak loop ends 2026-10-11T14:28:41Z (+ final restart + summary, a few minutes).
  Monitor ends 2026-10-11T14:58:41Z.

Smoke run before the real run (3 min requested, 5.4 min actual incl. final restart): 26 interactions, 2 restarts,
3 backend starts, 0 findings (high 0 / medium 0 / low 0), 0 console errors / 0 failed requests while up, exit 0.
Files: `smoke-metrics.json`, `smoke-findings.json`, `smoke-console_up.json`, `smoke-net_up.json`.

Evidence while running:
- `C:\Users\asd\Bossman\soak-20261010\heartbeat.jsonl` — independent monitor (`monitor.py`), one JSON line per
  5 min: soak alive, /health/live of :8871 (errors expected during deliberate restarts), process-tree RSS, python/
  ollama/chrome counts, free RAM, out size, events count, new Traceback/FAIL/[high] lines in soak stdout.
- `C:\Users\asd\Bossman\soak-20261010\out\events.jsonl` (every step, restart, finding), `out\soak.stdout.log`.
- At the end: `out\metrics.json`, `out\findings.json`, `out\console_up.json`, `out\net_up.json`,
  `out\soak.exit.txt` (exit=0 means no high finding), `monitor-final.json` (beats, gaps > 15 min, max RSS).

How to check: `Get-Content C:\Users\asd\Bossman\soak-20261010\heartbeat.jsonl -Tail 3`; stop early: kill PID 26628.

PASS rule: `24H_SOAK_PASS` may be claimed only after reading the FINAL `out\metrics.json` with duration_min >= 1440,
`by_severity.high == 0`, no unexplained growth in `backend_rss_mb`/`browser_rss_mb` slopes, and `monitor-final.json`
with no gaps > 15 min and soak alive until its own end. Limits: fake model only (no real model load/leak), one task
class (UI/API/CLI + restarts), no self-improvement work inside the soak; the North Star gate also names STOP, retry
storms, orphan models and Git corruption, which this harness does not exercise.
