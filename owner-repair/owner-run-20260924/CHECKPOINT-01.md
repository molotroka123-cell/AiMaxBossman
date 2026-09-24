# Owner run 2026-09-24 — checkpoint 01

BASE_SHA `5a2b6f1339392b7e2af52af20d30c6b92912f3c5` (final-1, not certified: 2 blockers)
HEAD_SHA `95b2f5df` on `claude/bossman-1-0-exact-20260924` (local; 02a534aa pushed)

## Release blockers
| Blocker | Reproduced | Fix | Evidence |
|---|---|---|---|
| A — `/export out\s.md` on POSIX | CI job 107411138942 (Linux py3.12): `assert False` at test_terminal_chat_claude_parity.py:305, 1 failed / 4656 passed | 40a5c14a + 95b2f5df: `export_target()` — both separators, cwd containment, device names, FS errors, exclusive create | local Windows 77 passed (terminal suites); Linux = CI on 02a534aa (pending at writing) |
| B — Windows-100 nodeid/root mismatch | CI job 107418451382: `ERROR: file or directory not found: tests/telegram_contracts/...`, 0 tests ran | 02a534aa + 95b2f5df: per-project collect/execute, per-nodeid verdict, 2 negative controls, allow-listed exclusions, pinned FFmpeg | windows-latest run 35980607145 **success** on 02a534aa; local PASS 100/100; control A+B caught; no-FFmpeg → GateError |

## Independent red team (fresh agent, no shared context)
Confirmed P2: /export OSError crashed chat; negative control red for the wrong reason. Plausible P2: skip laundering, sticky outcomes, XPASS, POSIX `\x.md`, device names. **All fixed in 95b2f5df.** No P0/P1.
Note: the red-team agent wrote to System32 during probing — the Claude session is elevated (known), not a machine ACL defect; files removed.

## Local full suites on Windows (owner machine, venv, 02a534aa+)
- root: 2633 passed, 2 failed, 8 errors — venv redirector pid (passes with base python), target-hardware probe correctly detects real target HW, owner_scenarios Windows file-lock/teardown.
- Command Center (-n 6): 4731 passed, 17 failed — the **identical 17 fail on untouched baseline 5a2b6f13** (Node 20 ESM/CJS, MCP SDK absent, Git-bash launcher pid, TEMP) → environment, not regressions. OPEN as P2 harness: full CC suite is never run on Windows in CI.

## HW-01 models (fresh, RUNTIME=OLLAMA 0.34.3 Vulkan :11435, think=false; SAC blocks llama.cpp)
| | bake-off A–G | TTFT | gen tok/s (3×256) | prefill tok/s @6.9k | RAM (ollama ps) |
|---|---|---|---|---|---|
| MAIN Qwen3.8-27B Q5 | 6/7 (205.7 s) | 891 ms | 9.5 / 9.6 / 8.4 | 144.5 | 22.3 GB @64k ctx |
| FAST Qwen3.6-35B-A3B Q5 | 6/7 (101.8 s) | 329 ms | 32.4 / 38.3 / 38.1 | 680.1 | 25.1 GB @32k ctx |
Both fail F_tool_recovery: after "file not found" they call `search_code` instead of retrying `read_file` with the corrected root-relative path. Grader unchanged.

## Found
Intelligence Preservation CI `measured-gate` requires an in-tree file whose `evaluated_sha == github.sha` — unsatisfiable by construction. Measurement will be taken on owner hardware out-of-tree with `--expect-sha FINAL`.

## Next
CC CI on 02a534aa → push 95b2f5df + candidate-2 declaration → exact-SHA certification → ZIP → HW.
