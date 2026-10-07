# Handoff: keys guard (2026-10-07 night)

Goal (owner order): API keys must not be lost. Spec: tools/keys_guard.py with backup / restore / verify / ensure, DPAPI blob + rotating copies, hooked into owner_one_bossman launch --ensure and the Backup action.

## Done
- tools/keys_guard.py chunk 1: KeysGuardError, dpapi_protect / dpapi_unprotect (ctypes CryptProtectData / CryptUnprotectData), atomic_write_bytes. Written by Bossman worker nvidia-nim (nvidia/nemotron-3-super-120b-a12b), task c05fd783f300. DPAPI roundtrip on a dummy string checked: OK. Not yet audited line by line, no pytest tests yet.
- coding_tasks._worker_key already reads %LOCALAPPDATA%\Bossman\keys\provider-keys.env first (spec item 5 satisfied, no change).

## Learned
- One big task (whole spec) fails: nemotron-3-super hit max_steps (40) with a 19-line test; nemotron-ultra-free emitted degenerate import lists and no_tool_call. Small chunks (under 90 lines, exact content listed) work.
- Task mode needs source_repo inside the owner's allowed roots: use C:\Users\asd\Bossman\worker-src (branch worker/keysguard, fetched from this branch). Do not use evo-tree-src. Driver script is in the session scratchpad (launch_keys.py: worker, wish file, out json, comma-separated allowed paths); client must be the installed runtime python with discover("http://127.0.0.1:8801", data dir CommandCenter).
- Apply worker diff: git apply the task's diff to this worktree.

## Left (names only)
1. Chunk 2: env parse/serialize, union merge (env wins only if non-empty), backup (primary blob + timestamped copy in %LOCALAPPDATA%\Bossman\backups\keys, keep 5, never overwrite a corrupt blob).
2. Chunk 3: restore (atomic env write, icacls owner-only), verify (names only, exit code), ensure, argparse main.
3. command-center/tests/test_keys_guard.py with negative controls: missing file restored, partial completed, corrupt blob does not overwrite good env, values never in stdout/stderr, union/empty rules, rotation 5, atomic replace failure.
4. Hook ensure at the start of launch() in tools/owner_one_bossman.py (try/except, status line only) and into Do-Backup in tools/owner_one_bossman.ps1 (copy keys folders into the backup).
5. Run tests + launcher tests, run `ensure` live (names only), push.
