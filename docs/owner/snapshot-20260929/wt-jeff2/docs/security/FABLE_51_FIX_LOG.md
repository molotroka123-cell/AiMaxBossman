# FABLE 5.1 — PHASE 2 FIX LOG

Phase 1 findings were frozen first (`FABLE_51_FINDINGS_INITIAL.json`, `INITIAL_FINDINGS_FROZEN=YES`).
Only reproduced findings with an understood root cause and a minimal, test-provable repair were touched.
Design-level findings (F-009 sandbox semantics, F-010/011/013/014/016 command-center, F-008 gateway
egress model, F-012 verification-by-fresh-observation) are **documented, not silently rewritten**
(prompt §33–34).

Fixes applied this session, all in `bossman-core` toolkit path-confinement (one root-cause class):

## FIX-1 — F-001 (HIGH): `fs.search` glob escapes workspace

- **Reconfirmed exploit:** `.agents/redteam/poc_search_glob.py` →
  `fs.search(pattern='.', glob='../../outside/*')` returned the canary two levels above the workspace.
- **Root cause:** `fs_search` iterated `ctx.workdir.rglob(glob)` and **never** applied `_resolve` to the
  glob; a `../` glob (or a directory junction inside the workdir) yielded paths outside the root, whose
  matching lines were returned to the model.
- **Minimal fix** (`bossman/toolkit/files.py`): resolve each candidate and skip anything not contained
  in the resolved workdir root, using the new `_contains(root, p)` helper (relative-path/`parents`
  containment, not string prefix). Directory junctions/symlinks are caught because the *resolved* target
  is compared.
- **Re-attack after fix:** blocked (VERDICT: blocked). Variants also blocked
  (`.agents/redteam/poc_variants.py`): junction + default glob, junction + explicit `j/*` glob.
- **No false negative:** legitimate in-workdir search still returns hits (`test_fs_search_still_finds_in_workdir`).

## FIX-2 — F-002 (MED): `fs.*` root confinement by `str.startswith` (sibling-prefix escape)

- **Reconfirmed exploit:** `.agents/redteam/poc_sibling_probe.py` → with workdir `.../coder`,
  `fs.read('../coder-secrets/s.txt')` and `fs.write('../coder-secrets/pwned.txt')` both succeeded,
  because `str(p).startswith(str(workdir))` is true for the sibling `.../coder-secrets`.
- **Root cause:** prefix string comparison in `_resolve` instead of a real containment relation.
- **Minimal fix:** `_resolve` now uses `_contains(root, p)` (`p == root or root in p.parents`).
  `fs_list` recursion additionally refuses to descend into a directory whose resolved target escapes the
  root (junction/symlink inward-out).
- **Re-attack after fix:** both read and write blocked with `PermissionError`; nested
  `../coder/../coder-secrets/x` blocked; `fs.list` no longer recurses through a junction
  (`poc_variants.py` V3/V4).

## FIX-3 — F-003 (MED): `media.probe` lacked path validation

- **Reconfirmed exploit:** `probe` built `(workdir/arg).resolve()` and passed it to `ffprobe` with no
  check, unlike its `ffmpeg` twin which uses `_path_arg_ok`.
- **Minimal fix** (`bossman/toolkit/media.py`): apply the existing `_path_arg_ok` to `probe`'s `path`,
  rejecting absolute paths, drive letters, UNC, and `..` — the same barrier ffmpeg already enforces.
- **Re-attack after fix:** `../../../etc/passwd`, `/etc/passwd`, `C:\Windows\win.ini` all rejected
  (`test_media_probe_refuses_escape_paths`).

## Tests added (`bossman-core/tests/test_tools.py`)

- `test_fs_search_glob_cannot_escape_workdir` (F-001 regression)
- `test_fs_search_still_finds_in_workdir` (no false negative)
- `test_fs_read_sibling_prefix_escape_blocked` (F-002)
- `test_fs_write_sibling_prefix_escape_blocked` (F-002)
- `test_media_probe_refuses_escape_paths` (F-003)

## Not fixed this session (documented-open, with rationale)

| Finding | Why deferred |
|---|---|
| F-009 (HIGH) terminal.run sandbox root-skip | Intentional "run anywhere inside a container" semantics; a safe fix must confine sandbox `cwd` to allowed roots and/or require approval — a design decision + needs docker to validate. Recommend: confine `cwd` to allowed roots even in sandbox mode, keep container isolation as defense-in-depth. |
| F-004 http SSRF | Not granted to default agents; correct fix (scheme allowlist + private-IP/metadata block + redirect cap) is a policy addition to review. |
| F-005 projects builtin/cmd bypass | Requires routing the projects pipeline through `runner._call_tool`'s enforcement wrapper — a structural change. |
| F-006/F-007 untrusted-marker gaps | Consistent marking across ingestion paths is a design change; recommend marking retrieved/system-injected and exec/write output uniformly. |
| F-008 gateway cloud header fail-open | Recommend fail-closed default for `x-bossman-cloud-allowed`, pass `cloud_allowed` in embeddings, and derive audit cloudness from the resolved route not the alias prefix. |
| F-010/011/013/014/016 command-center | Design-level (default browser allowlist, session ownership binding, approval-by-content-hash, MCP metadata trust, fail-closed cloud gating). Documented for owner. |
| F-015/F-017 owner-route integrity | Within single-token owner authority; harden by verifying approvals against the table and validating `app_id`/URLs. |
| F-018 dead security code | Wire or remove: `bcc/v2/permissions.py`, `code_index._within`, `context_os/*`, `sandbox/secrets.py`, `capabilities.py`. |
