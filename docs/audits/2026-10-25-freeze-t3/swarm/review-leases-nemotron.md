**Defects (most severe first):**

1. **Idempotency key race → IntegrityError crash** (leases.py:583-590, 605)  
   Concurrent `request` with same `idempotency_key` pass the `SELECT` check, then both `INSERT`; the second hits `UNIQUE` constraint and raises uncaught `sqlite3.IntegrityError`.  
   *Fix:* Catch `sqlite3.IntegrityError` on insert, re-read and return existing lease.

2. **Owner-interactive queued does not block new background GPU work** (leases.py:200)  
   `plan` only checks `any(x.priority == "owner_interactive" for x in granted)`; a queued `owner_interactive` (waiting for memory) allows background GPU leases to be granted, violating “new background GPU work waits while the owner chats”.  
   *Fix:* Change condition to `any(x.priority == "owner_interactive" for x in active if x.state in ("granted","queued","preempting"))`.

3. **`Plan.live_blocked` uses stale `kind` from first `fits` call** (leases.py:190, 230)  
   After preempting victims, the final `Plan` returns `kind == "live"` where `kind` is from the *initial* `fits` (may be "budget"/"gpu"), causing `_unload_needed` to skip unload when live memory is the real blocker, or unload unnecessarily.  
   *Fix:* Capture the `kind` from the last `fits` call in the loop and use that for `live_blocked`.

4. **Unload hook races with concurrent grants → may unload in-use models** (leases.py:560-575)  
   `_unload_needed` reads `active` leases outside the write transaction; another thread can grant a new lease whose models are not in `keep` and get unloaded while in use.  
   *Fix:* Move the unload decision inside the transaction (or re-verify `keep` after acquiring write lock).

5. **Optimistic `avail` adjustment in `_promote` ignores that preempted victims still hold memory** (leases.py:480)  
   `avail -= added_gb(g, active)` assumes victims’ memory is instantly freed, but they remain in `preempting` state and count in `committed_gb`; subsequent queued leases may be granted over live memory.  
   *Fix:* Do not adjust `avail` for preempted victims; only adjust when a lease actually releases (in `release`).

6. **Memory guard skips preempting leases but they still count toward budget** (leases.py:440)  
   Guard selects victim only from `state == "granted"`; if all granted are `paid_render`, it logs `guard_skip` even though preempting non-paid leases exist and hold memory.  
   *Fix:* Include `preempting` non-paid leases in victim pool (they’re already stopping, but guard should log accurately).

7. **`attach_process` does not verify the process actually holds the token** (leases.py:530)  
   A worker can call `attach_process` with any pid/create_time; token presence is only checked at recovery/guard. A buggy worker could hold a lease without the token, blocking others.  
   *Fix:* Probe the pid in `attach_process` and require `env_token` match before attaching.

8. **`recover` releases foreign leases but leaves their memory unaccounted** (leases.py:600)  
   Foreign process keeps running and holding memory; the lease becomes `released` (not counted in `committed_gb`), so budget is undercounted until the foreign process dies.  
   *Fix:* Keep such leases in a “zombie” state that still counts toward budget until the pid is confirmed gone.

9. **`queue_order` aging bonus uses `created_at` of the lease, not queue-entry time** (leases.py:240)  
   A lease that was granted, preempted, and re-queued keeps its original `created_at`, gaining excessive aging priority.  
   *Fix:* Track a separate `queued_at` timestamp updated on each transition to `queued`.

10. **`OllamaUnloader` adds `:latest` suffix incorrectly for tagged names** (leases.py:330)  
    `keep_l` adds `:latest` to any name without a colon, but a name like `model:v1` already has a tag and should not get `:latest`; however, the comprehension `{k.lower() + ":latest" for k in keep if ":" not in k}` correctly excludes it. *No defect here.*  
    *Actual defect:* `loaded()` returns `size_gb` rounded to 2 decimals, but `unload` uses `name` from that list; if Ollama returns a name with different casing/tag, the `keep_l` case-insensitive match may fail to protect it.  
    *Fix:* Normalize both sides to lowercase *without* tag for comparison, or use Ollama’s model digest.

---

**Missing test cases (≤5):**

1. **Concurrent idempotency-key requests** – 50 threads request same `idempotency_key`; exactly one lease created, no `IntegrityError`.
2. **Owner-interactive queued blocks background GPU** – Grant background GPU, then request owner-interactive that queues on memory; new background GPU request must queue with “владелец сейчас в чате”.
3. **Unload hook does not unload models of concurrently granted lease** – Thread A triggers unload for lease X; thread B concurrently grants lease Y using a model not in A’s `keep`; verify Y’s model stays loaded.
4. **Memory guard with preempting non-paid leases present** – Fill budget with paid_render + preempting background; drop live memory below guard; verify guard preempts a granted non-paid lease (not skip).
5. **Recovery rejects process with valid token but mismatched create_time** – Start lease, kill process, restart governor, spawn new process with same pid but different create_time and correct token; verify lease is released (not readopted) and new process untouched.