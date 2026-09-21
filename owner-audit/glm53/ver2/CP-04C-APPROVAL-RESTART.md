# CP-04C — Controlled approval restart matrix

Tested SHA: `0c3e22ffd44c9b2c3e4f90b2e86456c28ca43f39`

## A — API-created approvals

Markers `GLM53-API-RESTART-1/2/3` created approvals 7, 8, 9 through `POST /api/approvals`.
Each row was visible in read-only `bcc.db` before restart and remained `pending` in DB after restart. The default endpoint and `?status=pending` returned all three after each restart. `?status=all` returned `[]` despite persisted rows; this is an API filter/contract ambiguity, not data loss.

Conclusion: API approvals are durable in this runtime. The earlier Aster disappearance is disproved as a repeatable product behavior and is classified as a test/runtime anomaly.

## B — Task-bound approvals

Three real agent tasks used agent `Пилот`, local MAIN, and the safe `memory.write` tool with unique test markers:

- B1: task 9 / run 8 / approval 10 / digest prefix `5f0daa428fb19439`
- B2: task 10 / run 9 / approval 11 / digest prefix `8ee67c975b1d5813`
- B3: task 11 / run 10 / approval 12 / digest prefix `846e566a62184276`

Every task reached `waiting_approval`; after a backend restart task, run checkpoint, task binding, arguments and pending approval were present; approval was accepted once; task completed; and exactly one safe memory artifact was created per marker. No duplicate effects were observed.

## C — hard process kill

The backend was stopped by force and relaunched for each A and B cycle. The model servers stayed local and the backend recovered through the installed `bcc.desktop --web` runtime. The controlled B cycles preserved state and completed once.

## D — cross-action reuse

The API-created approvals are `manual`, have no task binding, and expose only a boolean decision endpoint. They cannot be attached to a different task/action through the decision API. Task-bound approvals carry task/run/digest/argument binding; no cross-task approval was accepted in B1–B3.

## Result

`BOTH DURABLE` for this controlled runtime. Confirmed approval P1 is not reproduced. Keep a P2 contract note for `status=all` returning an empty list while pending rows exist.
