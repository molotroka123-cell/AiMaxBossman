# POST_FREEZE_BACKLOG (deferred P2, no freeze impact)
- DirectApiBudget multi-reservation bookkeeping (worst-case per-record hold map)
- symlink-escape privilege on Windows accounts without symlink rights (test skips honestly)
- outreach live: repeat the bounded public sweep over more sources/regions until a verifiable problem -> real WAIT_APPROVAL
- budget-vs-capacity telemetry for the benchmark LIVE tier
- PORTED da2b62f7: MEDIA-RESTART layer 1 (Windows Job Object KILL_ON_JOB_CLOSE, e2183fc3). The Job Object binding already existed (6451029a, bound after start); da2b62f7 adds the missing part on the current _spawn/sidecar design: the engine is created CREATE_SUSPENDED, bound to the existing owner job, then resumed (fail closed if resume fails). No parallel .engine.json records. Regression: tests/test_studio_media_lifecycle.py (Windows real-kernel test added, not executed off Windows).
- PORTED ecaeb397: R6 stop-epoch (d22c3095). ComputerState.stop_epoch bumps on STOP and Resume; act() re-checks it under the lock and before every desktop call, so a queued action (incl. launch/wait) never runs after STOP, even STOP+instant Resume. UI not ported: ui/pages/control.js already has owner STOP/Resume. Regression: tests/test_computer_stop_race.py.
- Telegram companion (feat/telegram-local-llm-20260922): a new capability after the freeze; ships after 1.0 as a separate release.
