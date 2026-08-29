# Audit — new commits after 20d204a

Current observed HEAD during packaging: `1ba7e3e258a4d4448d8f9be299b1705dc508341c` on `claude/bossman-control-v03-43igbk`.

## Good changes

- `74e538b`: SafeRuntime now forwards the managed egress proxy into `http_proxy/https_proxy/all_proxy` with empty `NO_PROXY`; `coder` receives `sandbox.*` tools with confirmation on create/run. This closes the earlier “proxy exists but process is unaware of it” gap.
- `262e8c`: durable handoff reconciled after merge; local handoff claims 347 passing tests and 85 sandbox tests.
- `7080c38`: Windows UTF-8 fixes for filesystem tools, malformed Gateway JSON -> HTTP 400, sandbox import fails closed on NT/unsupported paths, browser-related tests use more honest skip behavior.
- `55508b7`: Postgres absence now becomes a structured `DEPENDENCY_UNAVAILABLE`/503-style domain failure rather than raw asyncpg traceback; schema is read as UTF-8. Chromium discovery is centralized/cross-platform with bounded launch timeout. Handoff claims 354 passing tests without browser env overrides.
- `1ba7e3e`: context/handoff updated to the latest audit-fix state.

## Remaining findings

### P1 — Stage 8 direct-socket egress bypass is still open
Proxy environment variables are not a network security boundary: a hostile process can ignore them and open a socket directly. The durable handoff itself still calls for netns+nftables or a stronger container/MicroVM network boundary. Do not treat CONNECTED/ALLOWLIST SAFE runtime as hostile-code secure until direct sockets are technically blocked.

### P1 — no independent GitHub CI gate on current HEAD
The GitHub combined status for current HEAD exposes zero statuses. Local commit/handoff claims (347/354 passing) are useful evidence but are not an independent required-check gate. Before autonomous merges, require CI + branch protection.

### P2 — encoding regression in `bossman/gateway/app.py`
Commit `7080c38` added a UTF-8 BOM and visibly mojibaked Russian comments (`Ð...`) throughout Gateway `app.py`. Runtime Python code appears intact, but this is a repository hygiene/regression signal and can create noisy future diffs or editor/tooling confusion. Restore the file as clean UTF-8 without BOM and recover readable comments; add an encoding/hygiene check if Windows agents keep rewriting files.

### P2 — test evidence text is internally noisy
The `55508b7` commit message contains an embedded `collected 0 items / no tests ran` block immediately before the “354 passed” claim. This may be output from a wrong working-directory invocation followed by a successful real run, but evidence should be normalized: record the exact command, cwd and final pytest summary in `TEST_STATUS.md`/CI artifact.

## Stage 12 interaction
Stage 12 package intentionally reuses Stage 6 `remote_client` auth/scopes and does not depend on the unresolved sandbox egress work. It should be merged only after running the included `test_stage12_mobile_api.py` together with existing remote-client tests on the actual checkout.
