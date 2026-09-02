# RESTART_RESUME_PROOF — LONGHORIZON-FREEZE-001 (mission_id LFZ-20260902-GLM-7f13546)

## Manager/process restart (mission-level)

- Pre-restart state: capsule `docs/mission/LONGHORIZON-FREEZE-001/capsule.json` written atomically after each atomic unit; manager context rotated multiple times during the session without owner re-prompt.
- Restart execution: a genuinely separate OS process (pid 39232) was started from a fresh interpreter; it loaded ONLY the capsule (no transcript), asserted `mission_id == LFZ-20260902-GLM-7f13546`, verified repository consistency (git HEAD `b6fd5e1`), appended its restart segment and exited.
- Continuation: the SAME mission continued afterwards in this session without any owner continuation prompt.

## No duplicated work / no duplicated effects

- Re-running the identical restart worker a second time was REFUSED: `DUPLICATED WORK: restart segment already present` — the capsule's completed-work set is an idempotency guard for mission-level work items.
- Runtime side-effect idempotency across restarts is proven separately at the safety layer: `tests/test_apprentice_live_safety.py::test_claim_survives_real_process_restart` kills a real spawned process (os._exit after claim) and the same side-effect id remains blocked in a fresh store process; nonce replay, cooldown, pending approval and teacher-reliability penalty all survive store recreation (same file, PASS).

## Evidence

- Worker transcript (this run): pre-restart pid 44292 → resumed pid 39232 → duplicate refused on second attempt.
- Capsule fields: `restart_proof {resumed_pid, resumed_at, head, same_mission_id, no_duplicated_work}`.
- Durable-safety restart evidence: bossman-core `pytest -m restart` → 2 passed (real spawn/kill boundaries).
