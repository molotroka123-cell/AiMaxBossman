Let me analyze this diff carefully to find concrete defects. I need to look for:

1. Auth/identity bypass or cross-client leak
2. Way to reach "delivered" without every gate
3. Idempotency/race/event-log inconsistencies
4. Migration data loss

Let me go through the code systematically.

First, let me understand the architecture:
- Orders belong to a client participant (64-hex key)
- Owner is a special role (`request.state.principal.role == "owner"`)
- F7: Client identity via server-side token (SHA-256 at rest), X-Participant header alone is no longer a client credential
- State machine: draft -> quoted -> accepted -> preflight -> generating -> qc_failed|qc_passed -> owner_review -> rework|approved -> delivered -> closed, plus cancelled (any state before delivered) and reconcile_required
- Delivery gates: AI-label disclosure + visible watermark bound to file SHA-256, consent for face jobs, third-party brand block, owner explicit "yes" bound to file hashes
- Idempotent retries: Idempotency-Key header + replay of identical last step
- Append-only JSONL event log
- Per-order threading.Lock
- Schema-1 migration on load (in_work -> generating)

Let me look for defects:

### 1. Auth/Identity Bypass or Cross-Client Leak

Looking at `_ctx` function in studio_orders.py:

```python
async def _ctx(x_participant: str | None, request: Request, client: str | None) -> tuple[str, str]:
    token = request.headers.get(CLIENT_TOKEN_HEADER)
    if token is not None:
        key = await _run(_book(request).tokens.resolve, token.strip())
        if key is None:
            raise HTTPException(401, {"code": "invalid_client_token", "message": "unknown, revoked or expired token"})
        claimed = _who(x_participant, request) if (x_participant is not None
                                                   or request.headers.get("x-bcc-participant") is not None) else key
        if claimed != key:
            raise HTTPException(403, {"code": "participant_mismatch",
                                      "message": "the participant header does not match the client token"})
        if client is not None:
            raise HTTPException(403, {"code": "owner_only", "message": "only the owner may address another client"})
        return key, key
    if _who(x_participant, request) != "owner":
        raise HTTPException(403, {"code": "client_token_required",
                                  "message": f"a client is identified by its {CLIENT_TOKEN_HEADER.title()} only"})
    _owner_credential(request)
    if client is None:
        return "owner", "owner"
    try:
        return "owner", valid_participant(client)
    except ValueError as exc:
        raise HTTPException(422, {"code": "invalid_participant", "message": str(exc)}) from None
```

Wait, there's a potential issue here. When a client token is provided, it resolves to a key. But what if the token resolves to "owner"? Let me check `ClientTokens.resolve`:

```python
def resolve(self, token: str | None) -> str | None:
    if not token or not str(token).startswith(self.PREFIX) or len(token) > 200:
        return None
    digest, found = token_hash(token).encode(), None
    for key, rec in self._read()["clients"].items():
        stored = rec.get("token_sha256") if isinstance(rec, dict) else None
        if stored and hmac.compare_digest(digest, str(stored).encode()) and not _expired(rec) and not rec.get("revoked_at"):
            found = key
    # a hand-edited file must never turn a client token into the owner (Qwen review 25.10)
    return found if found and CLIENT_KEY.fullmatch(found) else None
```

Good, it checks `CLIENT_KEY.fullmatch(found)` which is `^[0-9a-f]{64}$`, so "owner" (which is not 64 hex) would be rejected. But wait, what if someone manually edits clients.json to have a key "owner"? The `CLIENT_KEY.fullmatch(found)` would fail for "owner" since it's not 64 hex chars. So that's protected.

But there's another issue: In `_ctx`, when there's no token, it calls `_who(x_participant, request)` and checks if it's "owner". Let me check what `_who` does (it's imported from `.direct_gen`). I don't have that code, but based on the comment "the owner ('owner', the default) addresses a client's orders with `?client=<64-hex key>`", it seems like `_who` returns "owner" for the owner's credential.

But there's a potential bypass: What if a client sends a request WITHOUT a token, but with an X-Participant header that makes `_who` return something other than "owner"? The code says:
```python
if _who(x_participant, request) != "owner":
    raise HTTPException(403, {"code": "client_token_required", ...})
```

So if `_who` returns a client key (not "owner"), it would raise 403 saying client token required. That seems correct.

But wait - what if `_who` returns "owner" for a non-owner? Let me think... The comment says "the owner ('owner', the default) addresses a client's orders with `?client=<64-hex key>`". So `_who` probably checks some authentication and returns "owner" only for the actual owner.

Actually, looking at `_owner_credential`:
```python
def _owner_credential(request: Request) -> None:
    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise HTTPException(401, {"code": "owner_credential_required", "message": "owner authentication required"})
    if getattr(principal, "role", None) != "owner":
        raise HTTPException(403, {"code": "owner_credential_required",
                                  "message": "an agent-scoped token cannot act as the studio owner"})
```

This checks `request.state.principal.role == "owner"`. So the owner is identified by the principal's role.

Now, the cross-client leak: When owner calls with `client=<key>`, it returns `"owner", valid_participant(client)`. The namespace becomes the client's key. Then in the API endpoints, they call `book.get(ns, order_id)` or `book.list(ns)`. Let me check if `OrderBook.get` and `list` properly isolate by participant.

In `OrderBook._load`:
```python
def _load(self, participant: str, order_id: str) -> dict:
    try:
        return migrate(json.loads(read_text_retry(self._path(participant, order_id))))
    except (OSError, ValueError):
        raise OrderError(404, "unknown_order", "order not found") from None
```

And `_path`:
```python
def _path(self, participant: str, order_id: str) -> Path:
    return self._dir(participant) / f"{order_id}.json"
```

```python
def _dir(self, participant: str) -> Path:
    return self.data_dir / "direct-gen" / "participants" / participant / "orders"
```

So the path is built from the participant. If owner calls with `client=client_key`, the namespace is `client_key`, and the path is `.../participants/client_key/orders/...`. That seems correct - orders are isolated by participant directory.

But wait - what about `OrderBook.list`? 
```python
def list(self, participant: str) -> list[dict]:
    return [self.view(o) for o in self._raw_list(valid_participant(participant))]
```

```python
def _raw_list(self, participant: str) -> list[dict]:
    base = self._dir(participant)
    out = []
    for path in base.glob("*.json") if base.is_dir() else []:
        try:
            out.append(migrate(json.loads(read_text_retry(path))))
        except (OSError, ValueError):
            continue
    for o in out:
        o.pop("_migrating", None)
    return sorted(out, key=lambda o: o.get("created_at", ""), reverse=True)
```

This lists orders in the participant's directory. So if owner uses `client=client_key`, they see that client's orders. That seems correct.

But there's a potential issue: `OrderBook.get_for_client`:
```python
def get_for_client(self, participant: str, order_id: str) -> dict:
    return self.client_view(self._load(valid_participant(participant), order_id))
```

This loads the order from the participant's directory. If a client calls with their token, `ns` is their key, so they load from their own directory. Good.

But what if a malicious client somehow gets another client's token? They could access that client's orders. But that's token theft, not a code defect.

Let me look for a more subtle issue. In `_ctx`:
```python
claimed = _who(x_participant, request) if (x_participant is not None
                                           or request.headers.get("x-bcc-participant") is not None) else key
if claimed != key:
    raise HTTPException(403, {"code": "participant_mismatch", ...})
```

If a client provides a token that resolves to `key`, but also provides an `x_participant` header that makes `_who` return something different, it raises 403. Good.

But what if the client provides NO x_participant header? Then `claimed = key`, so it passes. That's fine.

Now, what about the owner path? When no token, it checks `_who(x_participant, request) != "owner"`. If `_who` returns "owner", it proceeds. But what if `_who` returns a client key? Then it raises 403. So only owner can access without token.

But here's a potential issue: The owner can specify `client=<any_64_hex>`. The code does `valid_participant(client)` which validates it's 64 hex. But it doesn't check if that client actually exists or has orders. That's fine - it just means the owner can create orders for new clients.

Wait, in `create`:
```python
@router.post("/orders", status_code=201)
async def create(body: OrderIn, request: Request, client: str | None = None,
                 x_participant: str | None = Header(default=None)):
    actor, ns = await _ctx(x_participant, request, client)
    _owner(actor)
    return await _run(_book(request).create, ns, body.model_dump())
```

The owner must be the actor. `ns` is the client key (or "owner" if no client). Then `book.create(ns, ...)` creates an order in `ns`'s directory. That seems correct.

But there's a defect: In `OrderBook.create`, the participant is used directly:
```python
def create(self, participant: str, body: dict) -> dict:
    ...
    order = {
        "order_id": uuid.uuid4().hex, "participant": participant, "status": status, "schema": SCHEMA,
        ...
    }
```

The participant is stored in the order. But when loading, `_load` uses the participant from the path, not from the order. So if someone manually moves an order file to another participant's directory, the `participant` field in the order would be wrong. But that's a manual file system attack, not an API defect.

Let me look for more serious issues.

### 2. Way to reach "delivered" without every gate

The delivery gates are checked in `delivery_problems`:
1. Every job completed with result file
2. Disclosure on every file (with correct SHA-256)
3. Watermark on every file (with correct SHA-256)
4. Consent for face jobs
5. No uncleared third-party brands
6. Owner approval "yes" bound to file hashes

The `deliver` method:
```python
def deliver(self, participant: str, order_id: str, key: str | None = None) -> dict:
    def fn(order: dict, moves: list) -> None:
        if "delivered" not in TRANSITIONS.get(order["status"], ()):
            raise OrderError(409, "invalid_transition", f"order is {order['status']}; cannot become delivered")
        problems = self.delivery_problems(order)
        if problems:
            raise _refusal("cannot deliver", problems)
        receipt = self.receipt(order)
        order["licence_warnings"] = self.licence_warnings(order)
        self._move(order, moves, "delivered")
        order["receipt"] = receipt | {"frozen_at": now_iso(), "licence_warnings": order["licence_warnings"]}
    return self._step(participant, order_id, "deliver", fn, key=key)
```

And `TRANSITIONS`:
```python
TRANSITIONS: dict[str, tuple[str, ...]] = {
    ...
    "approved": ("delivered", "rework", "cancelled"),
    ...
}
```

So only from "approved" can you go to "delivered". And "approved" requires owner's explicit "yes" via `approve` method.

But wait - what about the `start` method? It can go from "accepted" or "preflight" to "generating". And from "generating" you can go to "qc_passed" then "owner_review" then "approved" then "delivered".

But is there a way to skip steps? Let me check the state machine transitions:
- draft -> quoted, cancelled
- quoted -> quoted, accepted, cancelled
- accepted -> preflight, cancelled
- preflight -> generating, cancelled
- generating -> qc_passed, qc_failed, reconcile_required, cancelled
- qc_failed -> generating, rework, cancelled
- qc_passed -> owner_review, cancelled
- owner_review -> approved, rework, cancelled
- rework -> generating, cancelled
- approved -> delivered, rework, cancelled
- reconcile_required -> generating, qc_passed, qc_failed, cancelled
- delivered -> closed
- closed -> (none)
- cancelled -> (none)

So the only path to delivered is: ... -> approved -> delivered.

And `approve` requires:
1. Order status is "owner_review"
2. Actor is owner
3. Answer is exactly "yes"
4. All deliverable hashes are present (jobs completed)
5. Then it sets `owner_approval` with hashes

And `deliver` requires:
1. Order status is "approved" (via TRANSITIONS check)
2. `delivery_problems` returns empty

`delivery_problems` checks:
- All jobs completed with files
- Disclosure on each job (with matching SHA-256)
- Watermark on each job (with matching SHA-256)
- Consent for face jobs
- Brand flags cleared
- Owner approval "yes" AND approval hashes match current file hashes

So it seems all gates are enforced. But let me check if there's a race condition or TOCTOU.

The `_step` method uses a per-order lock:
```python
def _step(self, participant: str, order_id: str, step: str, fn: Callable[[dict, list], dict | None], *,
          args: Any = None, actor: str = "owner", key: str | None = None, replayable: bool = True,
          reason: str = "") -> dict:
    participant = valid_participant(participant)
    if key is not None and not IDEMPOTENCY_KEY.fullmatch(str(key)):
        raise OrderError(422, "invalid_idempotency_key", "Idempotency-Key: 1..128 of [A-Za-z0-9._:-]")
    fp = fingerprint(step, args)
    with self._lock(participant, order_id):
        order = self._load(participant, order_id)
        ...
```

So the lock is held during the entire step, including loading, checking, and committing. That should prevent races.

But wait - the lock is per-order, but what about the job files? The jobs are loaded via `self.jobs.load(participant, job_id)`. The jobs are in a separate store. Could a job be modified between when `delivery_problems` checks it and when the order is delivered?

The `delivery_problems` reads job data, checks status, SHA-256, etc. Then if no problems, it proceeds to deliver. But the job could be changed by another process (e.g., the Direct-Generator job completes or is modified) after the check but before the commit. However, the order lock doesn't protect the job files.

But the delivery gates require the job to be "completed" with a result file and SHA-256. If the job is modified after the check, the SHA-256 would change, and the approval hashes would become stale (since `approval_stale` checks if approval hashes match current job SHA-256). So on the next delivery attempt, it would fail. But the current delivery attempt would have already succeeded with the old hashes.

Actually, let me trace through:
1. `delivery_problems` reads job, gets SHA-256 `sha1`
2. Checks approval hashes match `sha1` - they do
3. No problems, proceeds to deliver
4. Meanwhile, job is re-rendered, new SHA-256 `sha2`
5. Order is committed as "delivered" with receipt frozen
6. But the delivered files now have `sha2`, not `sha1`!

The approval was bound to `sha1`, but the actual delivered files are `sha2`. The `approval_stale` check in `delivery_problems` would catch this on a RE-delivery attempt, but the first delivery already succeeded.

Is this a real issue? The job status would have to change from "completed" to something else and back, or the result file replaced. The Direct-Generator probably doesn't do that once completed. But it's a theoretical TOCTOU.

More critically: The `delivery_problems` checks the job's current SHA-256, but the approval hashes are stored at approval time. If the job is re-rendered AFTER approval but BEFORE delivery, the approval becomes stale. But `delivery_problems` checks `approval.get("hashes")` against current job SHA-256, so it would catch it and refuse delivery. Good.

But what if the job is re-rendered DURING the delivery check? The lock is on the order, not the job. So:
- Thread A: deliver() acquires order lock, calls delivery_problems()
- Thread B: job completes rendering, writes new result with new SHA-256
- Thread A: reads job, sees new SHA-256, checks approval hashes (old), mismatch -> approval_stale -> refuses delivery

That's actually correct behavior - it refuses delivery because approval is stale.

But what if:
- Thread A: deliver() acquires order lock, calls delivery_problems()
- Thread A: reads job, sees SHA-256 `sha1`, approval hashes match `sha1`
- Thread B: job re-rendered, new SHA-256 `sha2`
- Thread A: continues, no problems found, commits delivery
- Now order is delivered, but files have `sha2`

The approval hashes in the order are `sha1`, but the actual files are `sha2`. The order's `owner_approval.hashes` still has `sha1`. The `receipt` is frozen with `licence_warnings` at delivery time.

Is this a defect? The delivery gate says "owner explicit 'yes' bound to the SHA-256 of every deliverable (a file changed after the approval is 'approval_stale')". So if the file changes after approval, it should be approval_stale. But in this race, the check passed because it read the old SHA-256, then the file changed, then delivery committed.

The fix would be to re-check the hashes at commit time, or to lock the jobs too. But the jobs are in a separate store.

Actually, looking at `_deliverable_hashes`:
```python
def _deliverable_hashes(self, order: dict) -> tuple[dict[str, str], list[dict]]:
    hashes, problems = {}, []
    for item in order["items"]:
        for job_id in item["job_ids"]:
            try:
                job = self.jobs.load(order["participant"], job_id)
            except KeyError:
                problems.append({"job_id": job_id, "code": "unknown_job"})
                continue
            sha = (job.get("result") or {}).get("sha256")
            if job.get("status") != "completed" or not sha or self._result_file(job) is None:
                problems.append({"job_id": job_id, "code": "job_not_completed", "status": job.get("status")})
            else:
                hashes[job_id] = sha
    return hashes, problems
```

This is called in `approve` and `delivery_problems`. In `approve`, it stores the hashes in `owner_approval`. In `delivery_problems`, it compares current hashes with approval hashes.

The race is: approve reads hashes H1, stores in approval. Then job re-renders to H2. Then deliver reads hashes H2, compares with approval H1 -> mismatch -> approval_stale. Good.

But if deliver reads hashes H1 (before re-render), compares with approval H1 -> match. Then job re-renders to H2. Then deliver commits. Now delivered with H2 files but approval for H1.

The window is small but exists. However, this requires the job to be re-rendered after being completed, which may not happen in practice. But it's a theoretical defect.

Let me look for more concrete defects.

### 3. Idempotency/race/event-log inconsistencies

Looking at `_step` and idempotency:

```python
def _step(self, participant: str, order_id: str, step: str, fn: Callable[[dict, list], dict | None], *,
          args: Any = None, actor: str = "owner", key: str | None = None, replayable: bool = True,
          reason: str = "") -> dict:
    participant = valid_participant(participant)
    if key is not None and not IDEMPOTENCY_KEY.fullmatch(str(key)):
        raise OrderError(422, "invalid_idempotency_key", "Idempotency-Key: 1..128 of [A-Za-z0-9._:-]")
    fp = fingerprint(step, args)
    with self._lock(participant, order_id):
        order = self._load(participant, order_id)
        if key is not None and key in (order.get("idempotency") or {}):
            if order["idempotency"][key].get("fp") != fp:
                raise OrderError(409, "idempotency_conflict", "this Idempotency-Key was used for another request")
            return self.view(order) | {"replayed": True}
        if key is None and replayable and (order.get("last_step") or {}).get("fp") == fp:
            return self.view(order) | {"replayed": True}
        moves: list[dict] = []
        try:
            extra = fn(order, moves) or {}
        except _CommitThenFail as stop:
            if moves:                       # keep the state it reached (e.g. preflight); a pure re-check: nothing
                self._commit(order, step, fingerprint(step + ":refused", [args, order["rev"]]), actor, moves,
                             key=None)
            raise stop.error from None
        self._commit(order, step, fp, actor, moves, key, reason)
        return self.view(order) | extra
```

Idempotency key check: if key exists in order.idempotency and fp matches, replay. If fp doesn't match, 409.

Without key: if last_step.fp matches current fp, replay.

Potential issues:

1. **Idempotency key collision across different steps**: The idempotency key is stored per-order, not per-step. So if you use the same idempotency key for two different steps (e.g., "start" and "qc"), the second one would see the key exists but fp differs (since step is different), and return 409. That's correct behavior - idempotency keys should be unique per request.

2. **Replay without key uses last_step.fp**: This only works for repeating the exact same step with same args. If you do step A, then step B, then try to replay step A without key, it won't match last_step (which is B). That's by design.

3. **Event log consistency**: In `_commit`:
```python
def _commit(self, order: dict, step: str, fp: str, actor: str, moves: list[dict], key: str | None,
            reason: str = "") -> None:
    rows = []
    migrated = order.pop("_migrating", None)
    if migrated:
        rows.append({"step": "migrated", "from": order["migrated_from"].get("status"), "to": migrated,
                     "actor": "system", "reason": "schema 1 -> 2"})
    rows += [{"step": step, "from": m["from"], "to": m["to"], "actor": actor, "reason": m.get("reason", "")}
             for m in moves] or [{"step": step, "from": order["status"], "to": order["status"], "actor": actor,
                                  "reason": reason}]
    order["rev"] = int(order.get("rev") or 0) + 1
    order["updated_at"] = at = now_iso()
    order["last_step"] = {"step": step, "fp": fp, "rev": order["rev"]}
    if key is not None:
        order.setdefault("idempotency", {})[key] = {"step": step, "fp": fp, "rev": order["rev"], "at": at}
    self._save(order)
    self._append_events(order, [{"rev": order["rev"], "at": at, "key": key, **r} for r in rows])
```

The order is saved FIRST, then events are appended. If the process crashes after `_save` but before `_append_events`, the order is updated but the event log is missing the entry. On next load, the order has the new state but no event for it. The event log would be inconsistent with the order state.

Also, `_append_events` opens the file in append mode, writes, flushes, fsyncs. But if crash after fsync of events but before... actually the order is already saved. The order save uses `atomic_write_text` which should be atomic (write to temp, rename). So order save is atomic. Events are append-only with fsync.

But the order of operations: save order, then append events. If crash after order save but before event append, order is new state, events missing last transition. On recovery, the order shows new state but event log doesn't have the transition. This violates "an event is committed with its order rev".

The comment says: "Every committed step bumps rev and appends one JSON line per transition to <order_id>.events.jsonl (append-only; an event is committed with its order rev)."

But the implementation commits the order (bumps rev, saves) THEN appends events. They should be atomic together, or events first then order. But events are append-only JSONL, order is atomic JSON. Hard to make atomic together.

Actually, the event includes the rev number. If order is saved with rev=5, then events for rev=5 are appended. If crash after order save, order has rev=5 but no events for rev=5. Next load sees rev=5, but events only up to rev=4. Inconsistent.

Better would be: append events first (with new rev), fsync, then save order. But if crash after events but before order save, events have rev=5 but order has rev=4. On load, order has rev=4, events have rev=5 entry. Also inconsistent.

The current approach: order save is atomic (atomic_write_text), events are append. The "event committed with its order rev" is not guaranteed atomic.

But is this a defect? The spec says "an event is committed with its order rev". The implementation doesn't guarantee that. If the process crashes at the wrong time, they can be out of sync.

4. **_CommitThenFail event logging**: When a step raises `_CommitThenFail`, it commits the moves (state change) with a modified fingerprint `fingerprint(step + ":refused", [args, order["rev"]])` and key=None. This creates an event for the state change (e.g., entering preflight) but marks it as refused. The fp is different so it won't match a replay. But the state change is kept (e.g., order goes to preflight). Then the error is raised. This seems intentional for preflight: you enter preflight, check problems, if problems, stay in preflight but record the refusal.

But the fp for the refusal is `fingerprint(step + ":refused", [args, order["rev"]])`. The `order["rev"]` is the rev BEFORE the commit (since commit hasn't happened yet). In `_commit`, `order["rev"]` is incremented. So the refusal event gets a new rev. But the fp includes the old rev. If you retry the same step with same args, the fp would be `fingerprint(step, args)` which is different from the refusal fp. So it would not replay, it would run again. That seems correct - you want to re-check preflight.

But there's a potential issue: The refusal commit uses `key=None`, so it doesn't store the idempotency key. If the original request had an idempotency key, the refusal doesn't record it. So a retry with the same idempotency key would see the key not in idempotency (since it wasn't stored), and would run again. But the order is now in preflight (from the refusal commit). The retry would run preflight again, find problems, raise refusal again, commit another refusal event. Each retry creates a new refusal event and bumps rev. The idempotency key is never recorded, so every retry is a new attempt. That defeats idempotency for failed preflight checks.

Wait, let me trace:
1. Client calls start with Idempotency-Key: "abc", args={}
2. _step: fp = fingerprint("start", {})
3. key="abc" not in idempotency, last_step.fp != fp
4. fn runs: moves to preflight, finds problems, raises _CommitThenFail
5. _CommitThenFail caught: commits with step="start", fp=fingerprint("start:refused", [{}, rev]), key=None
6. order.rev incremented, order.status="preflight", idempotency unchanged (key=None)
7. Error raised to client
8. Client retries with same Idempotency-Key: "abc"
9. _step: fp = fingerprint("start", {}) (same as before)
10. key="abc" not in idempotency (was never stored), last_step.fp = fingerprint("start:refused", ...) != fp
11. Runs fn again, same thing happens
12. Each retry creates a new "start:refused" event, bumps rev, but idempotency key never stored

So the idempotency key is effectively ignored for failed preflight checks. The client can retry infinitely, each time creating a new event and rev. But the state doesn't change (stays in preflight). This is a waste but not a correctness issue per se. However, it violates the idempotency promise: "an Idempotency-Key already used for the same step + arguments returns the stored state (replayed)".

The promise is only for successful commits. For failed steps, the key is not stored. But the doc says: "Retries are idempotent: an Idempotency-Key already used for the same step + arguments returns the stored state (replayed), with other arguments it is 409; without a key, repeating the last committed transition step with the same arguments (a lost response) is a replay too."

It says "already used for the same step + arguments returns the stored state". If the step failed, there's no stored state for that key. So maybe it's by design that failed steps don't consume the idempotency key. But then the client can't safely retry a failed step with the same key - it would run again. That's actually the point of idempotency keys: you can retry safely. If the step failed, you want to retry and have it either succeed or fail the same way, not run again.

Actually, standard idempotency: the key is stored when the request is first processed, with the result. If the request fails, you might not store the key, so retry runs again. But that means the operation might be executed multiple times. For non-idempotent operations, that's bad. But here the operation is a state transition check.

For preflight, it's a check that doesn't change state (except moving to preflight). The first call moves to preflight. Subsequent calls with same key should probably replay the result (which was "preflight failed with problems"). But currently, the key is not stored on failure, so each retry re-runs the check.

This could be a defect: idempotency key not stored on _CommitThenFail, so retries of failed preflight are not idempotent.

But let's see if there are other _CommitThenFail uses. Only in `start` method for preflight failure. Other steps don't use _CommitThenFail.

### 4. Migration data loss

Schema migration in `migrate`:
```python
def migrate(order: dict) -> dict:
    if order.get("schema") == SCHEMA:
        return order
    legacy = order.get("status")
    status = "generating" if legacy == "in_work" else legacy if legacy in TRANSITIONS else "reconcile_required"
    order.update(schema=SCHEMA, status=status, migrated_from={"schema": order.get("schema", 1), "status": legacy})
    order.setdefault("rev", len(order.get("timeline") or []))
    order.setdefault("attempt", 1 if status == "generating" else 0)
    order.setdefault("idempotency", {})
    order["_migrating"] = status
    return order
```

Schema 1 statuses: quoted, accepted, in_work, delivered, cancelled.
Schema 2 adds many more states.

Migration: in_work -> generating. Other legacy statuses that are in TRANSITIONS keep their status. Unknown legacy statuses -> reconcile_required.

Potential data loss:
- `rev` is set to `len(timeline)`. But timeline in schema 1 might not have all transitions? The old code had `_move` which appended to timeline. So timeline should have all status changes. `rev` becomes timeline length. That seems ok.
- `attempt` set to 1 if status == "generating" else 0. For migrated in_work orders, attempt=1. For others, 0. But what if an order was in_work and had multiple attempts? The old schema didn't have attempts. So this is a best-effort.
- `idempotency` initialized to empty dict. Any previous idempotency keys are lost. But schema 1 didn't have idempotency, so that's fine.
- `_migrating` flag set, then on next commit, a "migrated" event is logged and `_migrating` popped.

But what about orders that were "delivered" or "cancelled" in schema 1? They are in FINAL states. The migration keeps their status. But `TRANSITIONS` for "delivered" only allows "closed". For "cancelled", no transitions. So they can't be moved further. That's correct.

But there's a defect: In `_raw_list`, after loading and migrating each order, it does:
```python
for o in out:
    o.pop("_migrating", None)
```

But the migrated order is not saved! The migration happens in `_load` (which is called by `_raw_list`), but `_raw_list` doesn't save the order. It just returns the migrated order in memory, pops `_migrating`, and returns. The order on disk remains schema 1.

Then when someone calls `get` or `list`, they get the migrated view in memory, but the disk is unchanged. The next time the order is loaded, it migrates again. This means:
- The migration is not persisted until the order is modified and saved.
- The "migrated" event is only logged on the first save after migration (in `_commit` when `_migrating` is popped).
- But `_raw_list` pops `_migrating` without saving, so the "migrated" event is never logged for orders that are only listed but never modified!

Let me trace:
1. Order on disk: schema 1, status "in_work", no schema field.
2. Owner calls GET /studio/orders (list)
3. `list` -> `_raw_list` -> loads order, calls `migrate(order)`
4. `migrate` sets schema=2, status="generating", adds `_migrating="generating"`
5. `_raw_list` pops `_migrating` from the in-memory dict
6. Returns migrated order (without `_migrating`)
7. Order on disk UNCHANGED (still schema 1)
8. Owner calls GET /studio/orders/{id} (get)
9. `get` -> `_load` -> loads order from disk (schema 1), calls `migrate` again
10. Same thing, `_migrating` popped in `_load`? No, `_load` returns the migrated order with `_migrating` set.
11. `get` calls `view(order)` which pops `_migrating` from the order dict (in `view`: `out = {k: v for k, v in order.items() if k not in ("receipt", "idempotency", "_migrating")}`)
12. But the order on disk still not saved!
13. Only when a mutating step is called (e.g., `start`), `_step` loads, runs fn, then `_commit` saves and logs migrated event.

So the migration is lazy - only persisted on first write. But the "migrated" event is only logged on first write. If an order is never written after migration (e.g., already delivered), the migrated event is never logged. That's a data loss in the event log - the migration event is missing.

Also, if the process crashes after migration in memory but before any write, the migration is lost and will be redone on next load. That's fine (idempotent migration). But the event log never records it unless a write happens.

The defect: Orders that are in FINAL state (delivered, cancelled, closed) will never be written again, so their migration event is never logged. The event log is incomplete.

Also, `_raw_list` modifies the in-memory order (pops `_migrating`) but doesn't save. If another thread loads the same order, it will migrate again. But since migration is idempotent (checks schema==2), it's ok. But the `_migrating` flag is popped in `_raw_list`, so if a write happens later, `_commit` won't see `_migrating` and won't log the migrated event.

Wait, `_raw_list` does:
```python
out.append(migrate(json.loads(read_text_retry(path))))
...
for o in out:
    o.pop("_migrating", None)
```

It pops `_migrating` from the list items, but those are in-memory dicts. The original order on disk is unchanged. When `_load` is called later (e.g., for `get`), it loads from disk, calls `migrate`, which sets `_migrating` again. So `_migrating` is set on every load until a commit happens.

In `_commit`:
```python
migrated = order.pop("_migrating", None)
if migrated:
    rows.append({"step": "migrated", "from": order["migrated_from"].get("status"), "to": migrated,
                 "actor": "system", "reason": "schema 1 -> 2"})
```

So the migrated event is logged on the first commit after migration. If no commit ever happens (order in final state), no migrated event. That's a defect: the event log is missing the migration event for final-state orders.

Also, there's a potential issue: `order.setdefault("rev", len(order.get("timeline") or []))`. If the order has no timeline (unlikely), rev=0. But then `_commit` does `order["rev"] = int(order.get("rev") or 0) + 1`, so rev becomes 1. But the timeline might have entries. The rev should match the number of timeline entries? Not necessarily, but it's a counter.

Another migration issue: `order.setdefault("attempt", 1 if status == "generating" else 0)`. For an order that was "in_work" and had multiple rendering attempts (in the old system, maybe it went in_work -> in_work -> in_work?), the attempt count is lost, set to 1. But the old system didn't track attempts, so this is expected.

But what about an order that was "accepted" in schema 1? It migrates to "accepted" (since "accepted" is in TRANSITIONS). `attempt` = 0. Then when `start` is called, it goes accepted -> preflight -> generating, and `attempt` becomes 1. That seems correct.

An order that was "quoted" -> migrates to "quoted", attempt=0. Good.

An order that was "delivered" -> migrates to "delivered", attempt=0. Good.

Now, let me look for more defects.

### Cross-client leak in event log?

The `events` endpoint:
```python
@router.get("/orders/{order_id}/events")
async def events(order_id: str, request: Request, client: str | None = None,
                 x_participant: str | None = Header(default=None)):
    actor, ns = await _ctx(x_participant, request, client)
    _owner(actor)
    return {"events": await _run(_book(request).events, ns, order_id)}
```

Only owner can access events (`_owner(actor)`). And `ns` is the client key (or owner). `book.events(ns, order_id)` loads the order from `ns`'s directory and reads the event log. So owner can see events for any client's order by specifying `client=key`. That's by design.

But what if a client tries to access events? The `_ctx` for a client (with token) returns `key, key` where key is their participant key. Then `_owner(actor)` would fail because actor is the client key, not "owner". So clients can't access events. Good.

### Defect: `ClientTokens.resolve` constant-time comparison issue

```python
def resolve(self, token: str | None) -> str | None:
    if not token or not str(token).startswith(self.PREFIX) or len(token) > 200:
        return None
    digest, found = token_hash(token).encode(), None
    for key, rec in self._read()["clients"].items():
        stored = rec.get("token_sha256") if isinstance(rec, dict) else None
        if stored and hmac.compare_digest(digest, str(stored).encode()) and not _expired(rec) and not rec.get("revoked_at"):
            found = key
    return found if found and CLIENT_KEY.fullmatch(found) else None
```

It iterates through ALL clients and does `hmac.compare_digest` for each. This is constant-time per comparison, but the loop stops early when a match is found (`found = key` but continues looping? No, it doesn't break, it continues to check all). Actually, it sets `found = key` but doesn't break, so it continues checking all entries. That's good for timing attack prevention - it always checks all entries.

But there's a subtle issue: `hmac.compare_digest(digest, str(stored).encode())` - `digest` is bytes, `str(stored).encode()` is bytes. `compare_digest` requires both arguments to be bytes or both str. Here both are bytes, ok.

But `digest = token_hash(token).encode()` - `token_hash` returns hex string, `.encode()` makes it bytes. `stored` is already a hex string from `token_hash` when issued. So `str(stored).encode()` is the same format. Good.

However, the loop doesn't break early, so it's constant-time with respect to the number of clients. Good.

But there's a potential issue: If there are many clients, this loop could be slow. But that's a performance issue, not a security defect.

### Defect: `valid_participant` allows "owner" as participant key?

In `ClientTokens._key`:
```python
@staticmethod
def _key(client: str) -> str:
    try:
        key = valid_participant(client)
    except ValueError:
        raise OrderError(422, "invalid_participant", "client must be a 64-hex participant key") from None
    if key == "owner":
        raise OrderError(422, "invalid_participant", "the owner does not get a client token")
    return key
```

It explicitly rejects "owner". Good.

But `valid_participant` (from store) - I don't have that code. But presumably it validates 64-hex or "owner".

### Defect: `OrderBook.clients_with_orders` includes "owner"?

```python
def clients_with_orders(self) -> list[str]:
    root = self.jobs.root
    return sorted(p.name for p in (root.iterdir() if root.is_dir() else [])
                  if p.name != "owner" and any((p / "orders").glob("*.json")))
```

It excludes "owner". Good.

### Defect: `OrderBook.create` allows client to create order for another client?

```python
@router.post("/orders", status_code=201)
async def create(body: OrderIn, request: Request, client: str | None = None,
                 x_participant: str | None = Header(default=None)):
    actor, ns = await _ctx(x_participant, request, client)
    _owner(actor)
    return await _run(_book(request).create, ns, body.model_dump())
```

`_owner(actor)` ensures actor is "owner". So only owner can create orders. Owner can specify `client=key` to create for that client. That's by design.

But what if a client somehow gets owner credential? That's auth system issue.

### Defect: `disclose` and `watermark` don't check order status?

```python
def disclose(self, participant: str, order_id: str, job_ids: list[str] | None = None,
             caption: str = DISCLOSURE, key: str | None = None) -> dict:
    caption = str(caption or "").strip()[:200]
    if DISCLOSURE_MARK.lower() not in caption.lower():
        raise OrderError(422, "disclosure_invalid", f"the caption must say «{DISCLOSURE_MARK}»")
    return self._bind(participant, order_id, "disclose", "disclosures", {"caption": caption}, job_ids, key)
```

`_bind`:
```python
def _bind(self, participant: str, order_id: str, step: str, field: str, record: dict,
          job_ids: list[str] | None, key: str | None) -> dict:
    wanted = set(job_ids) if job_ids else None

    def fn(order: dict, moves: list) -> dict:
        self._open(order)
        known = {j for it in order["items"] for j in it["job_ids"]}
        if wanted and not wanted <= known:
            raise OrderError(422, "unknown_job", "a listed job is not part of this order")
        done = []
        for item in order["items"]:
            for job_id in item["job_ids"]:
                if wanted is not None and job_id not in wanted:
                    continue
                job = self._job(order["participant"], job_id)
                sha = (job.get("result") or {}).get("sha256")
                if job.get("status") != "completed" or not sha or self._result_file(job) is None:
                    raise OrderError(409, "job_not_completed", f"job {job_id} has no finished file to {step}")
                item.setdefault(field, {})[job_id] = {**record, "sha256": sha, "file": job["result"]["file"],
                                                      "at": now_iso()}
                done.append(job_id)
        return {"disclosed" if step == "disclose" else "watermarked": done}
    return self._step(participant, order_id, step, fn, args=[sorted(wanted or []), record], key=key,
                      replayable=False)
```

`_open(order)`:
```python
@staticmethod
def _open(order: dict) -> None:
    if order["status"] in FINAL:
        raise OrderError(409, "invalid_transition", f"order is {order['status']}")
```

So disclose/watermark can be called in any non-FINAL state. That's by design - you can disclose before delivery.

But `delivery_problems` checks for disclosure and watermark. So you must disclose/watermark before deliver. That's fine.

### Defect: `brand_flags` and `brand_ownership` case sensitivity

```python
def brand_flags(self, order: dict) -> list[dict]:
    """Check the brief and every job's prompt for denylisted brands (case-insensitive whole word)."""
    denylist = self.denylist()
    if not denylist:
        return []
    text = " ".join([order.get("brief", "")] + [self._job_prompt(order["participant"], j)
                    for item in order["items"] for j in item["job_ids"]])
    flags = []
    for brand in denylist:
        pattern = re.compile(rf"\b{re.escape(brand)}\b", re.IGNORECASE)
        if pattern.search(text):
            cleared = brand.lower() in (order.get("brand_ownership") or {})
            flags.append({"brand": brand, "cleared": cleared, "where": "brief" if pattern.search(order.get("brief", "")) else "prompt"})
    return flags
```

Denylist check is case-insensitive whole word. `brand_ownership` keys are stored as `b.lower()`:
```python
owned = order.setdefault("brand_ownership", {})
for b in names:
    owned[b.lower()] = {"brand": b, "set_by": "owner", "at": now_iso(), "note": str(note or "")[:200]}
```

And check: `cleared = brand.lower() in (order.get("brand_ownership") or {})`. So case-insensitive matching. Good.

### Defect: `consent_recorded` checks `provenance.consent_confirmed` on job or line item

```python
def consent_recorded(job: dict, item: dict) -> bool:
    rec = (job.get("provenance") or {}).get("consent") or (item.get("consent") or {})
    return rec.get("confirmed") is True and bool(rec.get("at"))
```

Checks job's provenance.consent or item's consent. Good.

### Defect: `needs_consent` - not shown in diff, but imported. Assume it's correct.

### Defect: `watermark` binding - job's provenance.watermark vs order's watermarks

```python
@staticmethod
def _watermark_problem(job: dict, item: dict, sha: str) -> str | None:
    recs = [(item.get("watermarks") or {}).get(job["job_id"]), (job.get("provenance") or {}).get("watermark")]
    recs = [r for r in recs if isinstance(r, dict) and str(r.get("mark") or "").strip()]
    if not recs:
        return "watermark_missing"
    return None if any(r.get("sha256") == sha for r in recs) else "watermark_stale"
```

Checks both order's watermarks and job's provenance.watermark. Good.

### Defect: `approve` binds to hashes at approval time, but `deliver` checks current hashes

```python
def approve(self, actor: str, participant: str, order_id: str, answer: str, key: str | None = None) -> dict:
    ...
    def fn(order: dict, moves: list) -> None:
        if order["status"] != "owner_review":
            raise OrderError(409, "invalid_transition", f"order is {order['status']}; cannot become approved")
        hashes, problems = self._deliverable_hashes(order)
        if problems:
            raise _refusal("cannot approve", problems)
        self._move(order, moves, "approved", "owner: yes")
        order["owner_approval"] = {"answer": "yes", "by": "owner", "at": now_iso(), "hashes": hashes}
```

Approval stores current hashes. Then `deliver` checks `approval.get("hashes")` against current job hashes. If job re-rendered between approve and deliver, hashes differ -> approval_stale. Good.

But what if the job is re-rendered DURING the approve step? The lock is on the order, not the job. So:
- approve: lock order, read job hashes H1, store in approval, commit
- job re-renders to H2 (no lock conflict)
- deliver: lock order, read job hashes H2, compare with approval H1 -> mismatch -> refuse

Good.

### Defect: `rework` clears owner_approval

```python
def rework(self, participant: str, order_id: str, reason: str = "", key: str | None = None,
           actor: str = "owner") -> dict:
    ...
    def fn(order: dict, moves: list) -> None:
        self._move(order, moves, "rework", reason)
        order.pop("owner_approval", None)
```

Good, rework voids approval.

### Defect: `reconcile` from generating -> reconcile_required

```python
def reconcile(self, participant: str, order_id: str, reason: str = "", key: str | None = None) -> dict:
    reason = str(reason or "")[:200]
    return self._step(participant, order_id, "reconcile",
                      lambda o, m: self._move(o, m, "reconcile_required", reason), args=[reason], key=key)
```

From generating to reconcile_required. Then from reconcile_required can go to generating, qc_passed, qc_failed, cancelled. Good.

### Defect: `qc` method

```python
def qc(self, participant: str, order_id: str, passed: bool, note: str = "", key: str | None = None) -> dict:
    note = str(note or "")[:200]

    def fn(order: dict, moves: list) -> None:
        if not passed:
            self._move(order, moves, "qc_failed", note)
            return
        if "qc_passed" not in TRANSITIONS.get(order["status"], ()):
            raise OrderError(409, "invalid_transition", f"order is {order['status']}; cannot become qc_passed")
        _, problems = self._deliverable_hashes(order)
        if problems:
            raise _refusal("qc cannot pass", problems)
        self._move(order, moves, "qc_passed", note)
        self._move(order, moves, "owner_review")
```

If passed=True, it checks all jobs completed (via `_deliverable_hashes`). Then moves to qc_passed then owner_review. Two moves in one step. The `_move` appends to timeline and moves list. Then `_commit` creates events for both moves? Let's see:

`_commit`:
```python
rows += [{"step": step, "from": m["from"], "to": m["to"], "actor": actor, "reason": m.get("reason", "")}
         for m in moves] or [{"step": step, "from": order["status"], "to": order["status"], "actor": actor,
                              "reason": reason}]
```

So for each move in moves, it creates an event row with the step name (e.g., "qc") and the from/to. So two events for one step: qc_passed and owner_review. That's fine.

But the fp for the step is `fingerprint(step, args)` where args=[bool(passed), note]. The same fp for both transitions. The idempotency key is stored with this fp. If you replay, it would replay the whole step (both transitions). Good.

### Defect: `start` method - preflight problems cause _CommitThenFail

```python
def start(self, participant: str, order_id: str, key: str | None = None) -> dict:
    def fn(order: dict, moves: list) -> None:
        if order["status"] in ("accepted", "preflight"):
            if order["status"] == "accepted":
                self._move(order, moves, "preflight")
            problems = self.preflight_problems(order)
            if problems:
                raise _CommitThenFail(_refusal("preflight failed", problems))
        self._move(order, moves, "generating", f"attempt {int(order.get('attempt') or 0) + 1}")
        order["attempt"] = int(order.get("attempt") or 0) + 1
    return self._step(participant, order_id, "start", fn, key=key)
```

If status is "accepted", it moves to "preflight", then checks problems. If problems, raises _CommitThenFail. The _CommitThenFail handler commits the moves (which includes the move to preflight) with a refusal fp. So the order ends up in "preflight" with a refusal event.

If status is "preflight" (re-check), it doesn't move to preflight again (already there), checks problems. If problems, raises _CommitThenFail. The moves list is empty (since no _move called). The _CommitThenFail handler: `if moves:` - moves is empty, so it doesn't commit. Then raises error. So the order stays in preflight, no new event, no rev bump. That's correct for a pure re-check.

But wait: the first time (from accepted), moves = [{"from": "accepted", "to": "preflight", ...}]. Problems found. _CommitThenFail commits with moves, key=None. Order status becomes "preflight". Rev incremented. Event logged for the move to preflight with step="start:refused".

Second time (from preflight), moves = []. Problems found. _CommitThenFail: moves is empty, so no commit. Error raised. Order stays preflight, no new rev, no new event.

But the idempotency key: first call with key="abc" - key not stored (key=None in commit). Second call with key="abc" - key not in idempotency, last_step.fp is for "start:refused" which differs from "start" fp. So it runs again. Each retry from preflight with problems creates no event, no rev bump. But the idempotency key is never stored, so retries are not idempotent.

Actually, for the first call (accepted -> preflight -> problems), the commit uses key=None, so idempotency key not stored. The client's idempotency key "abc" is not recorded. So a retry with "abc" will run again. But the order is now in preflight. The retry will run the preflight check again, find problems, raise _CommitThenFail with empty moves, no commit. So the retry does nothing (no state change, no event). But it still returns an error. The client gets the same error each time. That's actually idempotent in effect (same result), but the key is not marked as used, so it doesn't return `replayed: true`. The client gets an error each time, not a replayed success.

But the spec says: "an Idempotency-Key already used for the same step + arguments returns the stored state (replayed)". Here the key was used but no state stored, so it doesn't replay. It re-executes. For a failed preflight, re-executing is safe (no side effects except logging). But it violates the idempotency contract.

More importantly, if the preflight check has side effects (it doesn't seem to), it would be bad. But it's just reading jobs and brands.

### Defect: `OrderBook._save` not shown but uses `atomic_write_text`. Let me check if there's a defect in atomic write.

Not in diff.

### Defect: `OrderBook.view` removes `idempotency` and `_migrating` but not `owner_approval`? It keeps `owner_approval`. That's fine.

### Defect: `client_view` timeline compression

```python
def client_view(self, order: dict) -> dict:
    ...
    timeline: list[dict] = []
    for t in order.get("timeline", []):
        status = CLIENT_STATUS.get(t["status"], t["status"])
        if not timeline or timeline[-1]["status"] != status:
            timeline.append({"status": status, "at": t["at"]})
    return {"order_id": order["order_id"], "status": CLIENT_STATUS.get(order["status"], order["status"]),
            ...
            "timeline": timeline}
```

It compresses consecutive same client-status entries. But `CLIENT_STATUS` maps many internal statuses to "in_work". So the client sees a compressed timeline. That's by design.

### Defect: `OrderBook.list_for_client` returns client_view of all orders. Good.

### Defect: `OrderBook.get_for_client` returns client_view. Good.

### Defect: `OrderBook.receipt` freezes at delivered/closed

```python
def receipt(self, order: dict) -> dict:
    if order.get("status") in ("delivered", "closed") and order.get("receipt"):
        return order["receipt"]
    ...
```

Good.

### Defect: `OrderBook.licence_warnings` - not shown in diff but used.

### Defect: `OrderBook._pricing` and `_price_items` - not shown.

### Defect: `OrderBook._job_prompt` - not shown.

### Defect: `OrderBook.denylist` - not shown.

### Defect: `OrderBook._job` loads job from JobStore. Good.

### Defect: `OrderBook._result_file` checks if result file exists. Good.

### Defect: `OrderBook._bind` for disclose/watermark - checks job completed and file exists. Good.

### Defect: `OrderBook.deliver` only allows from "approved" (via TRANSITIONS). Good.

### Defect: `OrderBook.close` only from "delivered". Good.

### Defect: `OrderBook.cancel` from any non-FINAL state. Good.

### Defect: `OrderBook.quote` for draft or quoted. Good.

### Defect: `OrderBook.accept` from quoted. Good.

### Defect: `OrderBook.set_brand_ownership` - now uses `_step` with `_open` check (not FINAL). Good.

### Defect: `OrderBook._step` replayable=False for quote, brands, disclose, watermark. Why?

For quote: `replayable=False`. So even without key, it won't replay based on last_step. But with key, it still checks idempotency dict. `replayable` only affects the `key is None` branch. So for quote, you must use idempotency key to get replay. That's odd. Maybe because quote changes pricing based on current price list, so replaying without key would use old price list? But the fp includes args (which is None for quote). If price list changes, the result would be different, but fp same. So replay would return old pricing. That's why replayable=False - to force explicit idempotency key for quote.

Similarly for brands, disclose, watermark - they have args (names, caption, mark), so fp includes those. If you repeat with same args, fp matches. But replayable=False means without key, it won't replay even if fp matches last_step. With key, it will replay if key matches. So you need idempotency key for these to be idempotent. That's a design choice.

But for `start`, `qc`, `approve`, `rework`, `reconcile`, `cancel`, `close`, `deliver` - replayable=True (default). So they can replay without key if last_step matches.

### Defect: `OrderBook._step` actor parameter

For `accept`, the API passes `actor="owner" if actor == "owner" else "client"`. So the event records who accepted. Good.

For `cancel`, same.

For others, actor defaults to "owner" (since _owner(actor) is called in API).

### Defect: `OrderBook._commit` uses `order["last_step"] = {"step": step, "fp": fp, "rev": order["rev"]}`. This is used for keyless replay. But if a step has multiple moves (like qc -> qc_passed -> owner_review), the last_step records the step as "qc" and the final status is "owner_review". A keyless replay of "qc" with same args would match fp and replay. Good.

### Defect: Event log includes `key` (idempotency key) in each event. Good for audit.

### Defect: `ClientTokens.issue` generates key with `secrets.token_hex(32)` if not provided. But the client parameter is the participant key (64 hex). The owner can specify a client key or let it generate one. The generated key is 32 bytes = 64 hex. Good.

But `ClientTokens._key` validates with `valid_participant(client)`. If client is provided, it must be valid participant. If not provided, it generates a new 64-hex key. But it doesn't check if that key already exists in clients.json or as a participant directory. Could collide. But 64 hex is 256 bits, collision negligible.

### Defect: `ClientTokens.issue` returns `{"token": token}` with the raw token. Only returned once. Good.

### Defect: `ClientTokens.revoke` sets `token_sha256=None` and `revoked_at=now_iso()`. But `now_iso` not defined in that scope! Let me check:

```python
def revoke(self, client: str) -> dict:
    key = self._key(client)
    with self._lock:
        data = self._read()
        rec = data["clients"].get(key)
        if rec is None:
            raise OrderError(404, "unknown_client", "no token was issued for this client")
        rec.update(token_sha256=None, revoked_at=now_iso())
        self._write(data)
    return self._public(key, rec)
```

`now_iso` is not imported or defined in this class. It's defined at module level? Let me check the top of the file.

In the diff, at the top:
```python
from datetime import datetime, timedelta, timezone
```

And `now_iso` is used in many places. It must be defined somewhere. In the original code (before diff), there might be a `now_iso` function. In the diff, I see `now_iso()` used in many places but not defined in the diff. It's probably defined in the original file. But in `ClientTokens.revoke`, it uses `now_iso()` without importing. If `now_iso` is not in scope, this would raise NameError.

Looking at the diff, `now_iso` is used in:
- `OrderBook.create`: `created = now_iso()`
- `OrderBook._commit`: `at = now_iso()`
- `OrderBook._move`: `order["timeline"].append({"status": status, "at": now_iso(), ...})`
- `OrderBook.set_brand_ownership`: `at: now_iso()`
- `OrderBook.start`: `f"attempt {int(order.get('attempt') or 0) + 1}"` (no now_iso)
- `OrderBook.qc`: `note` only
- `OrderBook.approve`: `"at": now_iso()`
- `OrderBook.rework`: `reason` only
- `OrderBook.reconcile`: `reason` only
- `OrderBook.cancel`: `reason` only
- `OrderBook.deliver`: `now_iso()` in receipt
- `OrderBook._bind`: `"at": now_iso()`
- `OrderBook.delivery_problems`: no now_iso
- `ClientTokens.issue`: `now.isoformat()` (uses `now = datetime.now(timezone.utc)`)
- `ClientTokens.revoke`: `now_iso()` - **HERE!**
- `ClientTokens._public`: `_expired(rec)` uses `datetime.now(timezone.utc)`
- `_expired`: `datetime.now(timezone.utc)`
- `_refusal`: no now_iso

So `now_iso` is used in `ClientTokens.revoke` but not defined in the class or imported. It must be a module-level function. In the original file, there's probably:
```python
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
```

But in the diff, it's not shown. If it's missing, that's a defect. But since it's used everywhere else, it's likely defined. However, the diff doesn't show the import or definition. But the review is of the diff, so I should only consider what's in the diff. The diff doesn't add `now_iso` definition, but uses it in new code. If it was already defined in the original file, it's fine. But `ClientTokens` is a new class in the diff, and it uses `now_iso` in `revoke`. If `now_iso` is defined at module level (in the original orders.py), then it's accessible. Looking at the original file (before diff), it probably had `now_iso` since the old code used it (e.g., in `create`, `_move`, etc.). The diff shows `now_iso()` used in existing methods that were modified. So it's likely defined. Not a defect in the diff.

### Defect: `ClientTokens.resolve` - `_expired` check

```python
def _expired(rec: dict) -> bool:
    try:
        return bool(rec.get("expires_at")) and datetime.fromisoformat(rec["expires_at"]) <= datetime.now(timezone.utc)
    except (TypeError, ValueError):
        return True
```

If `expires_at` is None, `bool(None)` is False, so returns False (not expired). Good.
If `expires_at` is invalid format, returns True (expired). Good.

But `datetime.fromisoformat` requires Python 3.7+. The code says Python 3.12, so fine.

### Defect: `ClientTokens._read` - if file doesn't exist, returns `{"clients": {}}`. Good.

### Defect: `ClientTokens._write` - uses `atomic_write_text`. Good.

### Defect: `OrderBook._locks` - per-order lock using `threading.Lock`. But the API runs in `asyncio.to_thread`, so multiple threads. The lock is per-process. If the app runs with multiple workers (e.g., gunicorn with multiple workers), the lock won't work across processes! Each worker has its own `_locks` dict. So concurrent requests to the same order from different workers would not be serialized.

This is a **major defect**: The per-order lock is thread-local, not process-safe. In a multi-worker deployment, race conditions can occur.

The comment says: "A per-order lock serialises concurrent calls." But it only serializes within a single process. If the FastAPI app runs with multiple workers (common in production), this lock is ineffective.

The fix would be to use a file-based lock (e.g., `fcntl` or `portalocker`) or a distributed lock (Redis). But the current implementation uses `threading.Lock` which is process-local.

This is a concrete defect: **Race condition in multi-worker deployments due to process-local locking.**

### Defect: `OrderBook._append_events` opens file in append mode, writes, flushes, fsyncs. But if multiple processes append to the same events file, they could interleave writes. Each write is a single line + newline. On POSIX, writes to a file opened with O_APPEND are atomic if the write is less than PIPE_BUF (typically 4KB). The JSON line is probably less than 4KB. So individual lines won't interleave. But the `flush` and `fsync` are per-process. If two processes write concurrently, the lines might be in arbitrary order, but each line is intact. However, the `rev` numbers in events might not match the order of events if processes race. But the order's rev is saved in the order file (atomic), so the order file is the source of truth. The event log is append-only audit. Interleaved events from different processes would have increasing revs but possibly out-of-order in the file. But since each process holds the order lock (threading.Lock), within a process they're serialized. Across processes, no serialization. So two processes could both load the order, both increment rev, both save order (last write wins), both append events. The order file would have the last writer's state. The event log would have events from both, but revs might conflict (both use same rev number? No, each loads the order, sees rev=N, increments to N+1, saves. Last write wins. Events would have two entries with rev=N+1. Inconsistent.

This is a **major defect**: In multi-worker setup, the per-order lock doesn't prevent cross-process races, leading to lost updates and inconsistent event logs.

### Defect: `OrderBook._load` calls `migrate` on every load. If an order is loaded concurrently by two threads in the same process, the lock in `_step` protects mutations, but `_load` is called outside the lock in some places? Let's check:

- `_step`: calls `_load` inside the lock. Good.
- `get`: calls `_load` directly (no lock). But `get` is read-only. `migrate` modifies the in-memory dict (adds schema, status, etc., sets `_migrating`). If two threads call `get` concurrently on a schema-1 order, both will migrate in memory, but only the first to call a mutating step will save. The second will migrate again on next load. Since migration is idempotent (checks schema==2), it's safe but wasteful. No data corruption.

- `list` / `_raw_list`: calls `migrate` on each order, pops `_migrating`, returns. No save. Concurrent lists: each migrates in memory, no conflict.

- `events`: calls `_load` (which migrates), then reads event log. No lock. Safe.

So migration is thread-safe for reads (idempotent), and writes are serialized by lock.

### Defect: `OrderBook._save` not shown, but uses `atomic_write_text`. Assuming it's atomic.

### Defect: `OrderBook.create` sets `rev=1` and appends event with `rev=1`. But `_commit` also bumps rev. For create, it doesn't use `_step`, it directly saves and appends events. Let's check:

```python
def create(self, participant: str, body: dict) -> dict:
    ...
    order = {
        "order_id": uuid.uuid4().hex, "participant": participant, "status": status, "schema": SCHEMA,
        "rev": 1, "attempt": 0, "idempotency": {},
        ...
    }
    order["licence_warnings"] = self.licence_warnings(order)
    self._save(order)
    self._append_events(order, [{"rev": 1, "at": created, "key": None, "step": "create", "from": None,
                                 "to": status, "actor": "owner", "reason": ""}])
    return self.view(order)
```

It sets rev=1, saves, appends event with rev=1. Good. No lock used for create. But create is for a new order_id (uuid), so no conflict. The order_id is unique. The directory is participant-specific. So no race on create.

### Defect: `OrderBook._step` for `accept` - the API allows client or owner to accept. The `actor` is passed as "owner" or "client". The `_step` uses this actor for events. Good.

### Defect: `OrderBook.accept` doesn't check if order is in "quoted" status? It uses `_step` with fn that calls `_move(order, m, "accepted")`. `_move` checks `TRANSITIONS[order["status"]]`. So it enforces the transition. Good.

### Defect: `OrderBook.quote` for draft or quoted. The fn checks `if order["status"] not in ("draft", "quoted"):`. Good.

### Defect: `OrderBook.start` - from accepted or preflight. If accepted, moves to preflight, checks problems. If problems, _CommitThenFail (commits move to preflight). If preflight, just checks problems. If problems, _CommitThenFail with empty moves (no commit). Then moves to generating, increments attempt.

But what if status is "rework"? The TRANSITIONS for "rework" allows "generating". But the fn checks `if order["status"] in ("accepted", "preflight"):`. If status is "rework", it skips that block and goes straight to `self._move(order, moves, "generating", ...)`. But the comment says: "rework / qc_failed / reconcile_required -> generating (new attempt)". The code only handles accepted/preflight for preflight check. For rework, qc_failed, reconcile_required, it should also do preflight check? The comment says "preflight (re-check) / rework / qc_failed / reconcile_required -> generating as a new attempt." But the code only does preflight check for accepted/preflight. For rework, it goes straight to generating without preflight check.

Let me check TRANSITIONS: "rework" -> ("generating", "cancelled"). So from rework, you can go to generating. The `start` method is used for that transition. But it doesn't run preflight_problems for rework. Is that intentional? The comment says "preflight (re-check) / rework / qc_failed / reconcile_required -> generating as a new attempt." This suggests preflight re-check should happen for rework too. But the code doesn't do it.

Similarly for qc_failed and reconcile_required: TRANSITIONS allows them to go to generating. But `start` doesn't check preflight for those states.

This could be a defect: **Missing preflight check when starting from rework, qc_failed, or reconcile_required.**

But maybe preflight is only needed once (at accepted->preflight). The rework/qc_failed/reconcile_required are already past preflight. But the comment says "preflight (re-check)" implying it should be re-checked.

Let me check the docstring: "accepted -> preflight -> generating (a failed preflight keeps the order in preflight, 409 + problems); preflight (re-check) / rework / qc_failed / reconcile_required -> generating (new attempt)."

It says "preflight (re-check)" - meaning if you're already in preflight, you re-check. But for rework, it doesn't say re-check. It says "rework / qc_failed / reconcile_required -> generating (new attempt)". So maybe no preflight check for those. The comment might be ambiguous.

But the code only does preflight check for accepted and preflight. For rework, qc_failed, reconcile_required, it skips to generating. That seems intentional based on the comment.

### Defect: `OrderBook.start` increments attempt even for preflight re-check? No, attempt is incremented only when moving to generating. For accepted->preflight (first time), if problems, it moves to preflight but doesn't increment attempt (attempt increment is after the preflight check block). Let's trace:

```python
def fn(order: dict, moves: list) -> None:
    if order["status"] in ("accepted", "preflight"):
        if order["status"] == "accepted":
            self._move(order, moves, "preflight")
        problems = self.preflight_problems(order)
        if problems:
            raise _CommitThenFail(_refusal("preflight failed", problems))
    self._move(order, moves, "generating", f"attempt {int(order.get('attempt') or 0) + 1}")
    order["attempt"] = int(order.get("attempt") or 0) + 1
```

Case 1: status=accepted, no problems.
- move to preflight (moves=[accepted->preflight])
- no problems
- move to generating (moves=[accepted->preflight, preflight->generating])
- attempt = 0+1 = 1
- commit: both moves in one step, rev+1, attempt=1

Case 2: status=accepted, problems.
- move to preflight
- problems -> _CommitThenFail
- commit with moves=[accepted->preflight], key=None, fp=start:refused
- attempt NOT incremented (after the raise)
- order status=preflight, attempt=0 (unchanged)

Case 3: status=preflight, no problems.
- no move (already preflight)
- no problems
- move to generating (moves=[preflight->generating])
- attempt = 0+1 = 1
- commit

Case 4: status=preflight, problems.
- no move
- problems -> _CommitThenFail with moves=[]
- no commit (moves empty)
- error raised
- order stays preflight, attempt unchanged

Case 5: status=rework, no problems (not checked).
- skip if block
- move to generating (moves=[rework->generating])
- attempt = previous_attempt + 1
- commit

This seems correct. Attempt counts generating attempts.

### Defect: `OrderBook.qc` - if passed=False, moves to qc_failed. If passed=True, checks deliverable hashes, then moves to qc_passed then owner_review. But what if status is not generating? TRANSITIONS: generating -> qc_passed, qc_failed. qc_failed -> generating, rework, cancelled. So qc can only be called from generating (for passed) or generating (for failed). The fn checks `"qc_passed" not in TRANSITIONS.get(order["status"], ())` for passed case. For failed, it just moves to qc_failed (which is allowed from generating). But what if called from qc_failed? TRANSITIONS[qc_failed] doesn't include qc_failed. So `_move` would fail. Good.

### Defect: `OrderBook.approve` - requires status=owner_review. Good.

### Defect: `OrderBook.rework` - from qc_failed, owner_review, approved. TRANSITIONS confirms. Good.

### Defect: `OrderBook.reconcile` - from generating to reconcile_required. TRANSITIONS confirms. Good.

### Defect: `OrderBook.cancel` - from any non-FINAL. `_open` checks not FINAL. Good.

### Defect: `OrderBook.close` - from delivered to closed. TRANSITIONS confirms. Good.

### Defect: `OrderBook.deliver` - from approved to delivered. Checks delivery_problems. Good.

### Defect: `OrderBook.delivery_problems` - checks approval.hashes match current job hashes. Good.

### Defect: `OrderBook._deliverable_hashes` - returns hashes and problems. Used by approve and delivery_problems. Good.

### Defect: `OrderBook._bind` for disclose/watermark - checks job completed and file exists. Good.

### Defect: `OrderBook._watermark_problem` - checks both order watermarks and job provenance.watermark. Good.

### Defect: `OrderBook.brand_flags` - checks denylist against brief and job prompts. Good.

### Defect: `OrderBook.licence_warnings` - not shown, but used in view and receipt.

### Defect: `OrderBook.receipt` - calculates cost from job timing. Good.

### Defect: `OrderBook.view` - includes next transitions from TRANSITIONS. Good.

### Defect: `OrderBook.client_view` - compresses timeline. Good.

### Defect: API endpoints - all use `_run` which wraps in `asyncio.to_thread`. Good.

### Defect: `_client_safe` - for client callers, returns client_view. But for accept and cancel, it calls `book.get_for_client` after the step. The step returns owner view (from `_step` -> `view`). Then `_client_safe` calls `get_for_client` to get client view. But `get_for_client` loads the order again! That's an extra load. But more importantly, the `replayed` flag from the step result is preserved: `return view | ({"replayed": True} if out.get("replayed") else {})`. Good.

But for other steps (start, qc, approve, etc.), the API calls `_run(book.method, ...)` directly and returns the result. But those are owner-only (`_owner(actor)`), so no client_safe needed. Good.

### Defect: `accept` endpoint allows client or owner. `_client_safe` handles view. Good.

### Defect: `cancel` endpoint allows client or owner. `_client_safe` handles view. Good.

### Defect: `create` endpoint - only owner. Good.

### Defect: `quote` endpoint - only owner. Good.

### Defect: `start`, `qc`, `approve`, `rework`, `reconcile`, `disclose`, `watermark`, `brands`, `deliver`, `close` - all owner only. Good.

### Defect: `events` endpoint - only owner. Good.

### Defect: `clients` endpoints - only owner. Good.

### Defect: `price_list` endpoint - only owner. Good.

### Defect: `_owner_credential` checks `principal.role == "owner"`. Good.

### Defect: `_ctx` for client token - resolves token to key, checks claimed participant matches. Good.

### Defect: `_ctx` for owner - checks `_who` returns "owner", then `_owner_credential`. Good.

### Defect: `_who` function - not in diff, imported from `.direct_gen`. Assume it works.

### Defect: `valid_participant` - not in diff, imported from store. Assume it validates 64-hex or "owner".

### Defect: `OrderBook._path` uses `valid_participant(participant)`. Good.

### Defect: `OrderBook._dir` uses participant directly. Good.

### Defect: `OrderBook.clients_with_orders` iterates `self.jobs.root` which is the participants directory. Excludes "owner". Good.

### Defect: `ClientTokens` path: `self.path = Path(data_dir) / "direct-gen" / "studio" / "clients.json"`. Good.

### Defect: `ClientTokens.issue` - if client not provided, generates `secrets.token_hex(32)` (64 hex). But it doesn't check if this key already exists in clients.json or as a participant. Could theoretically collide. But 256 bits, negligible.

### Defect: `ClientTokens.issue` - `token = self.PREFIX + secrets.token_urlsafe(32)`. `token_urlsafe(32)` generates 43 chars (32 bytes -> 43 base64url chars). Prefix "stc_". Total ~47 chars. Stored as SHA-256. Good.

### Defect: `ClientTokens.resolve` - checks `len(token) > 200`. Good.

### Defect: `ClientTokens.resolve` - iterates all clients, constant-time compare. Good.

### Defect: `ClientTokens._public` - returns active status based on token_sha256 exists, not revoked, not expired. Good.

### Defect: `ClientTokens.list` - returns all clients with public info. Good.

### Defect: `OrderBook.client_tokens` - returns issued tokens + clients with orders but no token. Good.

### Defect: `OrderBook.set_brand_ownership` - now uses `_step` with `_open` (not FINAL). Good.

### Defect: `OrderBook._move` - appends to timeline and moves list. Good.

### Defect: `OrderBook._commit` - handles migrated event. Good.

### Defect: `OrderBook._append_events` - opens file, writes lines, flush, fsync. Good.

### Defect: `OrderBook.events` - reads event log, skips invalid JSON lines. Good.

### Defect: `OrderBook._lock` - uses `self._locks` dict with `self._locks_guard`. This is a lock for the locks dict. But `self._locks` is a dict mapping Path to threading.Lock. The `_locks_guard` protects the dict itself. But the Lock objects are per-path. This is standard.

But as noted, **threading.Lock is process-local**. In multi-worker, this doesn't work.

### Defect: `OrderBook._step` - the lock is acquired via `with self._lock(participant, order_id):`. This lock is per-order-path. But if two workers, each has its own `_locks` dict, so they get different Lock objects for the same path. No mutual exclusion.

### Defect: `OrderBook._save` - uses `atomic_write_text` which likely does write-to-temp-then-rename. This is atomic on POSIX for the file system. But if two processes write concurrently, last rename wins. The first process's write is lost. The lock doesn't prevent this across processes.

### Defect: Event log append - same issue. Two processes could append concurrently. Lines might interleave if write > PIPE_BUF, but JSON lines are small. However, the rev numbers would conflict.

### Defect: Migration event not logged for final-state orders (as analyzed).

### Defect: Idempotency key not stored for _CommitThenFail (preflight failure), so retries not idempotent.

### Defect: TOCTOU in delivery - job can change between delivery_problems check and commit. But the approval_stale check would catch it on re-delivery, but the first delivery could succeed with stale approval. However, the delivery_problems check reads the job hashes, and if they match approval, it proceeds. If job changes after check but before commit, the delivered files don't match approval. But the order is marked delivered with the old approval hashes. The client receives files with new hashes. The approval is bound to old hashes. This violates "approval bound to SHA-256 of every deliverable".

But is this exploitable? The job would have to be re-rendered after being completed. The Direct-Generator probably doesn't re-render completed jobs. But if it does (e.g., manual intervention), it's a problem.

### Defect: `OrderBook._bind` for disclose/watermark - uses `_step` with `replayable=False`. But the fn checks job completed and file exists. If the job is completed, it binds the disclosure/watermark to the current SHA-256. If the job is later re-rendered, the disclosure/watermark becomes stale (delivery_problems checks sha256 match). Good.

### Defect: `OrderBook.disclose` and `watermark` don't check if already disclosed/watermarked. They just overwrite. That's fine.

### Defect: `OrderBook.delivery_problems` checks `approval.get("answer") != "yes"` -> adds "approval_missing". But `approve` sets answer="yes". So if approve was called, answer is "yes". Good.

### Defect: `OrderBook.delivery_problems` checks `approval.get("hashes")` for each job. If approval exists but hashes missing for a job, it adds "approval_stale". Good.

### Defect: `OrderBook.approve` requires all jobs completed (via `_deliverable_hashes` which returns problems if not completed). So you can't approve incomplete jobs. Good.

### Defect: `OrderBook.qc` with passed=True requires all jobs completed. Good.

### Defect: `OrderBook.start` from accepted/preflight requires preflight check (jobs exist, consent, brands). Good.

### Defect: `OrderBook.preflight_problems` checks jobs exist, consent, brands. Good.

### Defect: `OrderBook.brand_flags` checks denylist against brief and prompts. Good.

### Defect: `OrderBook.denylist` - not shown, but loads from brands.json or default.

### Defect: `OrderBook.price_list` - not shown.

### Defect: `OrderBook._pricing` - not shown.

### Defect: `OrderBook._price_items` - not shown.

### Defect: `OrderBook._job_prompt` - not shown.

### Defect: `OrderBook.licence_warnings` - not shown.

### Defect: `OrderBook._result_file` - checks file exists. Good.

### Defect: `OrderBook._job` - loads from JobStore. Good.

### Defect: `JobStore` - not in diff.

### Defect: `atomic_write_text` - not in diff.

### Defect: `read_text_retry` - not in diff.

### Defect: `OrderError` - not in diff, but used.

### Defect: `Feature` - not in diff.

### Defect: `_who` - not in diff.

Now, let me prioritize the defects by severity:

1. **Auth/identity bypass or cross-client leak**: 
   - The per-order lock is process-local, not effective in multi-worker deployments. This could allow race conditions that bypass state machine transitions or cause cross-client leaks if orders are corrupted. But more directly, is there an auth bypass?
   - In `_ctx`, when client token is provided, it resolves to key. Then `claimed = _who(x_participant, request) if (x_participant is not None or request.headers.get("x-bcc-participant") is not None) else key`. If the client provides an x_participant header that makes `_who` return a different key, it raises 403. Good.
   - But what if the client provides NO x_participant header? Then `claimed = key`, passes. Good.
   - What if the client provides a token that resolves to key1, but the order they're accessing belongs to key2? The `ns` is key1 (from token), so they access their own orders. They can't access key2's orders because the path is built from ns. Good.
   - What if the owner tries to access a client's orders without `client=` parameter? Then `ns="owner"`, so they access owner's orders. To access client's orders, they must provide `client=key`. Good.
   - Is there a way for a client to act as owner? They would need `principal.role == "owner"`. The token system doesn't give that. Good.
   - **Potential auth bypass**: In `ClientTokens.resolve`, it returns the key if token matches. But what if the clients.json has a key that is not a valid participant (not 64 hex)? The `CLIENT_KEY.fullmatch(found)` checks it's 64 hex. But what if the key is "owner"? `CLIENT_KEY.fullmatch("owner")` is False (not 64 hex). So returns None. Good.
   - **Cross-client leak**: In `OrderBook.events`, it loads the order from `ns` (client key) and reads events. Only owner can call this. Good.
   - **But**: The `OrderBook._load` uses `self._path(participant, order_id)`. If an attacker can control `participant` and `order_id`, they could traverse directories? `valid_participant` validates the participant. `ORDER_ID` regex validates order_id (32 hex). So path traversal unlikely.

2. **Way to reach "delivered" without every gate**:
   - The state machine enforces approved -> delivered.
   - `approve` requires owner_review status, owner actor, answer="yes", all jobs completed.
   - `deliver` requires approved status, delivery_problems empty.
   - `delivery_problems` checks: jobs completed, disclosure, watermark, consent, brands, approval hashes match.
   - All gates seem enforced.
   - **But**: The TOCTOU race where job changes between delivery_problems check and commit could allow delivery with stale approval. However, this requires the job to be modified after completion, which may not be possible in the system design. But it's a theoretical defect.
   - **Another issue**: `delivery_problems` checks `approval.get("answer") != "yes"`. But what if `owner_approval` is not set at all? `approval.get("answer")` returns None, != "yes", so adds "approval_missing". Good.
   - **Another issue**: `delivery_problems` checks `approval.get("hashes")` for each job. If approval hashes missing for a job, adds "approval_stale". Good.
   - **But**: What if the order has no jobs? `items` min_length=1 in API, but could be empty in JSON? `MAX_ITEMS=50`, but min not enforced in model. `OrderIn` has `items: list[ItemIn] = Field(min_length=1, max_length=50)`. So at least one item. Each item has `job_ids` min_length=1. So at least one job. Good.

3. **Idempotency/race/event-log inconsistencies**:
   - **Major**: Per-order lock is `threading.Lock`, ineffective in multi-worker deployments. Race conditions on order state and event log.
   - **Major**: Event log append happens AFTER order save. Crash between them leaves order updated but event log missing entry. Violates "event committed with its order rev".
   - **Medium**: Idempotency key not stored for _CommitThenFail (preflight failure), so retries not idempotent.
   - **Medium**: Keyless replay only works for immediate repeat of last step. If another step intervenes, can't replay. By design.
   - **Minor**: `replayable=False` for quote, brands, disclose, watermark means keyless replay disabled for those. By design.

4. **Migration data loss**:
   - **Major**: Migration event not logged for orders in FINAL state (delivered, cancelled, closed) because they're never saved after migration. The `_raw_list` and `get` migrate in memory but don't persist. Only a mutating step triggers `_commit` which logs the migrated event. Final-state orders never mutate, so migration event never logged. Event log incomplete.
   - **Minor**: `attempt` set to 1 for migrated in_work orders, but old system didn't track attempts. Acceptable.
   - **Minor**: `rev` set to timeline length. If timeline incomplete, rev wrong. But old code appended to timeline on each move, so should be complete.

Now, let me formulate the concrete defects with file/function and one-line failing scenario.

### Defect 1 (Severity 1 - Auth/Identity): Multi-worker race condition due to process-local locking
**File/Function**: `OrderBook._lock` / `OrderBook._step` (orders.py)
**Scenario**: Two concurrent requests to `POST /studio/orders/{id}/start` from different gunicorn workers both pass preflight checks and transition the same order to `generating`, causing duplicate rendering attempts and inconsistent event log rev numbers.

### Defect 2 (Severity 1 - Auth/Identity): Client token resolution allows timing attack on client enumeration
**File/Function**: `ClientTokens.resolve` (orders.py)
**Scenario**: An attacker can enumerate valid client keys by measuring response time differences when `hmac.compare_digest` matches early vs. late in the loop (though the loop doesn't break, the `found = key` assignment and subsequent `CLIENT_KEY.fullmatch` check may leak timing). Actually, the loop doesn't break, so it always iterates all clients. But `hmac.compare_digest` is constant-time. The `found = key` is not constant-time? It's a simple assignment. The `CLIENT_KEY.fullmatch(found)` at the end is on the found key. But the loop always runs all iterations. So timing is constant with respect to number of clients. However, if there are many clients, the loop takes longer. But that's not a per-client timing leak. I think this is actually OK. Let me reconsider.

Wait, the loop:
```python
for key, rec in self._read()["clients"].items():
    stored = rec.get("token_sha256") if isinstance(rec, dict) else None
    if stored and hmac.compare_digest(digest, str(stored).encode()) and not _expired(rec) and not rec.get("revoked_at"):
        found = key
```
It iterates ALL clients. `hmac.compare_digest` is constant-time. The `if` condition evaluates all parts. `stored` check, `compare_digest`, `_expired`, `revoked_at`. `_expired` does `datetime.fromisoformat` which may take variable time. But it's per record. The loop doesn't short-circuit. So total time is proportional to number of clients, not which client matches. So no timing leak for which client. But it does leak the number of clients. That's minor.

But there's a defect: `hmac.compare_digest(digest, str(stored).encode())` - `digest` is bytes, `str(stored).encode()` is bytes. But `stored` is a hex string from `token_hash`. `token_hash` returns `hashlib.sha256(...).hexdigest()` which is lowercase hex. `digest = token_hash(token).encode()` - also lowercase hex bytes. Good.

But `str(stored).encode()` - if stored is not a string (e.g., None), `str(None).encode()` = b'None'. But the `if stored` guards against None. Good.

### Defect 3 (Severity 2 - Delivery gate bypass): TOCTOU in delivery allows approval-stale files to be delivered
**File/Function**: `OrderBook.deliver` / `OrderBook.delivery_problems` (orders.py)
**Scenario**: After `delivery_problems` verifies approval hashes match current job SHA-256s, a concurrent job re-render changes the file SHA-256s; `deliver` commits anyway, marking order `delivered` with approval bound to old hashes while actual files have new hashes.

### Defect 4 (Severity 3 - Idempotency/Race): Idempotency key not recorded for failed preflight, breaking retry idempotency
**File/Function**: `OrderBook._step` / `OrderBook.start` (orders.py)
**Scenario**: Client retries `POST /studio/orders/{id}/start` with same `Idempotency-Key` after a preflight failure; each retry re-executes preflight checks instead of returning `replayed: true`, creating duplicate `start:refused` events and incrementing `rev` on each retry.

### Defect 5 (Severity 3 - Idempotency/Race): Event log written after order save, risking inconsistency on crash
**File/Function**: `OrderBook._commit` (orders.py)
**Scenario**: Process crashes after `self._save(order)` but before `self._append_events`; order on disk has new `rev` and status but event log lacks the corresponding transition event, violating append-only audit integrity.

### Defect 6 (Severity 4 - Migration data loss): Schema-1 migration event never logged for orders in final states
**File/Function**: `OrderBook.migrate` / `OrderBook._raw_list` / `OrderBook._commit` (orders.py)
**Scenario**: A schema-1 order with status `delivered` is loaded via `GET /studio/orders`; migration runs in-memory but `_raw_list` discards `_migrating` flag without saving, so the `migrated` event is never written to the event log, leaving the audit trail incomplete.

### Defect 7 (Severity 2 - Delivery gate bypass): Missing preflight re-check when starting from `rework`, `qc_failed`, or `reconcile_required`
**File/Function**: `OrderBook.start` (orders.py)
**Scenario**: Order transitions `rework` -> `generating` via `start` without re-validating consent, brands, or job existence, allowing rendering to proceed with missing consent or uncleared brands.

### Defect 8 (Severity 1 - Auth/Identity): `ClientTokens.revoke` uses undefined `now_iso` function
**File/Function**: `ClientTokens.revoke` (orders.py)
**Scenario**: Owner calls `POST /studio/clients/{key}/revoke`; server crashes with `NameError: name 'now_iso' is not defined` because `now_iso` is not imported or defined in the `ClientTokens` class scope.

Wait, `now_iso` is likely a module-level function in the original file. The diff doesn't show it being added, but it's used in many places. If it was in the original file, it's fine. But the diff shows new usage in `ClientTokens.revoke`. If the original file had `now_iso`, it's accessible. I'll assume it's defined. But if not, it's a defect. Since the review is of the diff, and the diff doesn't add `now_iso`, but uses it in new code, it's a potential defect if `now_iso` wasn't already there. But the old code used `now_iso()` in `create`, `_move`, etc. So it must exist. Not a defect in the diff.

Let me check the diff for `now_iso` definition. Not present. But the original file (before diff) likely had it. The diff shows modifications to existing functions that use `now_iso`. So it's pre-existing. OK.

### Defect 9 (Severity 3 - Idempotency): Keyless replay uses `last_step.fp` but `last_step` is updated even for failed steps with `_CommitThenFail`
**File/Function**: `OrderBook._step` / `OrderBook._commit` (orders.py)
**Scenario**: A `start` request