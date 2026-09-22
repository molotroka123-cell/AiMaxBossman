# Owner-only Telegram channel for Codex

Implemented at the owner's explicit request on 2026-09-22. This is a separate,
persistent Codex app-server thread, not a claim to attach the current ChatGPT UI
conversation. Initial project context points to the ASTER checkpoint ledger.

## User commands

- Plain text or `/codex task`: submit to the pinned thread.
- `/model`: query the live Codex model catalog; `/model exact-id`: select the next turn's model.
- `/status`, `/last`: inspect status or request another copy of the last visible answer.
- `/stop`: persist the stop latch, invalidate approvals, interrupt the active turn.
- `/resume`: requires a fresh idle-thread observation; permits new messages, never replays old work.
- `/approve nonce`, `/reject nonce`: one decision on the exact displayed request, expiring after 5 minutes.
- `/answer nonce text`: answer one question; for multiple questions supply a JSON object keyed by question ID.

Only the existing configured numeric owner user ID in its exact private chat is
accepted. Guests, groups, channel posts, edited/forwarded messages and callbacks
are rejected. Telegram identity never comes from a username or model-generated
field. The owner ID and bot token are rechecked at effect/delivery boundaries.

## Execution and recovery

`tools/telegram_codex_bridge.py` uses the official local Codex app-server over
stdio, with the existing ChatGPT login. No public HTTP listener or direct `/sh`
command is added. The thread uses `workspace-write`, `untrusted` approvals and
the user as reviewer. Defaults are verified in the server response; no
`danger-full-access`, approval bypass, or session-wide approval is introduced.
The working root is the ASTER worktree. Broader operations may need a supported
one-shot approval. Unknown permission/auth/MCP approval types fail closed.

The bridge has encrypted local state, durable receipt IDs, no automatic repeat
after an uncertain dispatch, and at-most-once attempted output delivery. A send
with an unknown outcome is not retried automatically; `/last` is an explicit
owner request to retrieve the answer again. Existing Codex thread history
continues to follow Codex's own storage policy. No tokens or private chat IDs
belong in Git.

Only one poller can run, using the existing companion's OS file lock. Cursor
handoff reads the old durable offset; it does not drain Telegram to discover
an owner. The old companion is stopped when this route is deployed on the same
bot. Its configuration and database are preserved for rollback. This switch is
not a simultaneous integration of all previous Bossman bot commands: use this
channel's `/help`.

The Windows launcher supervises the bridge. Autostart is registered in the
current user's Startup folder; it runs after login, not as an unattended system
service before login. Program copies are deployed outside the writable project
directory and checked against the source SHA-256.

## Validation, 2026-09-22

- 28 targeted tests: identity boundaries, duplicate update, crash receipt,
  STOP-before/during-submit, stale/one-shot approval, strict model selection,
  foreign-thread output, uncertain delivery and idle-only Resume.
- 73 existing companion tests passed alongside the earlier 26-test revision;
  the last two added tests and all 26 preceding bridge tests passed after the
  final change. Existing companion source was not modified.
- LIVE: existing ChatGPT auth accepted; Codex returned `CODEX_TELEGRAM_READY`
  on requested `gpt-6-astra`, turn status completed (3.72 s end-to-end, not tok/s).
- LIVE: a fresh app-server process resumed the same persisted thread and returned
  the model catalog. The zero-turn probe exposed and fixed an empty-thread
  lifecycle edge: app-server has no rollout until the first actual turn.
- LIVE: the Telegram greeting was acknowledged by Telegram in the configured
  owner's private chat. This proves outbound delivery; full inbound owner-command
  acceptance is separate until the owner sends a message.
- Full PC control, every approval type, Windows reboot, and model-quality ranking
  are NOT certified by this bounded check. An available catalog entry is not a
  completed inference.

## Operations

Local runtime: `%LOCALAPPDATA%/Bossman/codex-telegram`.
`launcher.json` contains paths and the initial model, never credentials.
`bridge.log` contains concise connection/error codes. `bridge.sqlite3` and
`secret.key` are private runtime state and excluded from commits.

To disable retries, create a `DISABLED` marker in that runtime directory, then
stop only the validated supervisor/bridge processes when no turn is active.
The Startup shortcut will also respect the marker. To roll back, stop this
poller before starting the original companion with its preserved config; never
run two consumers on the same bot token. Reconcile unknown outcomes before
resuming tasks.

Protocol reference: [official Codex app-server documentation](https://learn.chatgpt.com/docs/app-server).
