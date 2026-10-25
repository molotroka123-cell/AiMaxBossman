### Defect Report

**1. Auth/Identity Bypass or Cross-Client Leak**
* **`features/studio_orders.py:_ctx`**: A client can bypass the `X-Studio-Client-Token` requirement by setting `X-Participant: owner`, which satisfies the `_who(...) == "owner"` check and proceeds to `_owner_credential` (relying on global auth state that may be scoped to an agent/client).
* **`direct_gen/orders.py:ClientTokens.resolve`**: The linear scan of `clients.json` on every request allows a performance-based DoS or latency leak if the owner issues many tokens.

**2. Way to reach "delivered" without every gate**
* **`direct_gen/orders.py:OrderBook.start`**: The `preflight_problems` check (consent/brands) is skipped when transitioning from `rework`, `qc_failed`, or `reconcile_required`. If a job's metadata is modified in the `JobStore` during these states, the order proceeds to `generating` (GPU spend) without re-validating gates.

**3. Idempotency/Race/Event-Log Inconsistencies**
* **`direct_gen/orders.py:OrderBook._step`**: Replay logic (without `Idempotency-Key`) is actor-blind; if an owner cancels an order, a subsequent identical cancel attempt by the client is treated as a replay of the owner's action, returning `replayed: True` and failing to log the client's intent.
* **`direct_gen/orders.py:OrderBook._commit`**: The order JSON is saved (`_save`) before the event log is appended. A crash between these calls results in a bumped `rev` in the JSON that has no corresponding entry in the `.events.jsonl` log.
* **`direct_gen/orders.py:OrderBook._lock`**: The `_locks` dictionary grows indefinitely with every unique order path accessed, causing a memory leak in the long-lived API process as locks are never purged.

**4. Migration Data Loss**
* **`direct_gen/orders.py:migrate`**: If a legacy order is loaded and migrated in memory but the process crashes before a mutating `_commit` occurs, the `_migrating` flag and the `migrated` event are lost (though the migration logic is idempotent on next load).

### Pytest Cases

1. **`test_preflight_bypass_on_rework`**: Assert that adding a face-swap job to an order in `rework` state and calling `start` fails with `consent_missing` (currently it would proceed to `generating`).
2. **`test_idempotency_actor_collision`**: Assert that an owner `cancel` followed by a client `cancel` (same reason, no key) records two distinct events in the log instead of replaying the first.
3. **`test_token_rotation_invalidation`**: Assert that issuing a new token for an existing client immediately causes the previous token to return 401 in `_ctx`.
4. **`test_approval_stale_on_file_swap`**: Assert that `deliver` returns `approval_stale` if a job's SHA-256 changes after the owner has called `approve`.
5. **`test_watermark_gate_enforcement`**: Assert that `deliver` returns `watermark_missing` if a job has neither a pipeline `provenance.watermark` nor a manual `watermark()` record.
6. **`test_migration_rev_consistency`**: Assert that a schema-1 order with 3 timeline entries is migrated to `rev: 3` and its first schema-2 event is `rev: 4`.
7. **`test_concurrent_order_access_lock`**: Assert that two simultaneous `start` calls for the same `order_id` result in one success and one `replayed: True` (or 409 if keys differ).
8. **`test_client_token_owner_impersonation`**: Assert that a request with a valid client token and `X-Participant: owner` returns 403.