# FABLE 5.1 — INDEPENDENT RETEST (treat every fix as untrusted)

Each applied fix was re-attacked with the ORIGINAL PoC and at least one variant (prompt §35).

## FIX-1 — F-001 fs.search glob escape

| Attack | Before | After |
|---|---|---|
| Original: `fs.search('.', glob='../../outside/*')` (poc_search_glob.py) | leaks canary outside workspace | **blocked** (no canary in result) |
| Variant: directory junction inside workdir + default glob `*` (poc_variants.py V1) | leaks (pre-fix junction read) | **blocked**; legit in-workdir hit still present |
| Variant: junction + explicit glob `j/*` (V2) | leaks | **blocked** |
| No-false-negative: legit `*.txt` search (V5, test_fs_search_still_finds_in_workdir) | — | **returns hit** |

## FIX-2 — F-002 fs.* sibling-prefix escape

| Attack | Before | After |
|---|---|---|
| Original: `fs.read('../coder-secrets/s.txt')` (poc_sibling_probe.py) | reads sibling outside workdir | **blocked** (PermissionError) |
| Original: `fs.write('../coder-secrets/pwned.txt')` | writes outside workdir | **blocked**; file not created |
| Variant: nested `../coder/../coder-secrets/x` (poc_variants.py V3) | — | **blocked** |
| Variant: `fs.list` recursion through junction (V4) | would enumerate outside | **blocked** (junction listed as entry, not recursed) |

## FIX-3 — F-003 media.probe path validation

| Attack | Before | After |
|---|---|---|
| `probe('../../../etc/passwd')`, `/etc/passwd`, `C:\Windows\win.ini` (test_media_probe_refuses_escape_paths) | ffprobe on out-of-workdir path | **blocked** (отказ по пути) |

## Regression

- New focused tests: `bossman-core/tests/test_tools.py` — **12 passed** (5 new + 7 existing), 0 failed.
- Self-contained security subset (tools, sandbox security/core/redteam, stage13 hostexec/operator,
  gateway cloud policy, browser policy, shell host approval, stage12 security):
  **158 passed, 3 skipped, 0 failed.**
- Full bossman-core suite with fixes applied: **1234 passed, 50 skipped, 0 failed** (0 regressions).

- Command-center code was **not modified** by these fixes; its behavior is unchanged from its own baseline.

All three fixes hold under re-attack and their variants; no security regression observed in the
exercised subset.
