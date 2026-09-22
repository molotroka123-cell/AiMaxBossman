# POST_FREEZE_BACKLOG (deferred P2, no freeze impact)
- DirectApiBudget multi-reservation bookkeeping (worst-case per-record hold map)
- symlink-escape privilege on Windows accounts without symlink rights (test skips honestly)
- outreach live: repeat the bounded public sweep over more sources/regions until a verifiable problem -> real WAIT_APPROVAL
- budget-vs-capacity telemetry for the benchmark LIVE tier
- MEDIA-RESTART layer 1 (Windows Job Object KILL_ON_JOB_CLOSE, e2183fc3 / fix/media-restart-orphan-20260922): the RC already kills orphans at restart (sidecar + reconcile_orphans with create_time/argv identity); the Job Object would kill the engine immediately when the backend dies. Port onto the current _spawn/sidecar design instead of the parallel .engine.json records.
- R6 stop-epoch (d22c3095 / fix/cu-stop-button-opencode-20260922): the RC already persists STOP across restart and invalidates observations on Resume (generation bump); the explicit stop_epoch re-check before every desktop call is extra defence against an action queued behind the lock — port onto the current ComputerState.
- Telegram companion (feat/telegram-local-llm-20260922): a new capability after the freeze; ships after 1.0 as a separate release.
