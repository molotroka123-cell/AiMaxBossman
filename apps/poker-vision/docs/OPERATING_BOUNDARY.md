# Operating boundary (hard, code-enforced)

| surface | observe | replay | act (clicks) | live advice |
|---|---|---|---|---|
| Poker Train (owner's own trainer, loopback `127.0.0.1`/`localhost`/`::1`) | yes | yes | **yes, gated** | yes (own trainer only) |
| TON Poker in Telegram (and any other external UI) | yes, after calibration **and** held-out verification | yes | **no** | **no** |
| unknown layout | refused (`layout_not_calibrated` / `layout_not_verified…`) → every field UNKNOWN | same | no | no |

What enforces it (each has a test):

* `sources.assert_loopback` — the trainer source and the page route guard refuse any non-loopback host (`tests/test_boundaries.py`).
* `adapters/ton_poker.py` declares `act=False, advise_live=False`; `service.start` raises `ActionRefused` for `act=True` on a captured window or on an adapter without `act` (`tests/test_service_api.py::test_refusals`).
* `actuator.TrainerActuator` only accepts a `LoopbackBrowserSource`; before every click it checks STOP, adapter capability, action budget, committed-state blockers (stale, frozen, pending change, transition errors), hero's turn, hero cards committed, committed-state age. After a click the state must change within 6 s or acting halts (ambiguity ⇒ stop).
* STOP is a `threading.Event` checked before every frame and every click; `stop()` joins the loop. The Bossman route falls back to stopping the process through the existing apps control if the service does not answer.
* Strategy receives `VisibleInfo` only (no field for opponent cards, deck, engine state, seeds); `tests/test_boundaries.py::test_strategy_type_cannot_hold_hidden_state`.
* The existing BotLab boundary (no screen capture / OS input in `apps/poker-botlab`) is untouched; Vision lives in its own app and does not import or modify BotLab except to reuse its Monte-Carlo equity (read-only).
* No money, bet-sizing for real stakes, anti-detection, or OS-level input exists anywhere in the package (`test_no_money_or_external_click_code_paths`).

Connecting an external client does not authorise money bets or bypassing that client's restrictions; nothing here does either.

## Update — COACH / EXECUTOR and the source panel

| surface | Наблюдение | Подсказки (COACH) | Управление (EXECUTOR) |
|---|---|---|---|
| Poker Train in the sandbox desk (own trainer, loopback, no money) | yes | yes | **yes, only after the owner's explicit tick** (`confirm_control`) |
| recorded frames | yes | yes | no |
| any captured window / TON Poker / other client | yes (after calibration + held-out check for readings) | **no** (UNVERIFIED) | **no** (not implemented, not authorised) |

* Control needs three independent things: the owner's tick in the page (`CONTROL_NEEDS_OWNER_CONFIRM` otherwise), an adapter with `act` (only `poker_train`), and a surface kind that is proven (`sandbox`). A recording or a window is refused with `NOT_ALLOWED`.
* **No OS-level pointer backend is shipped.** The executor's pointer in this build is the sandbox page mouse. Clicking real desktop windows would need a separate owner decision, a separate adapter run on the exact SHA, and (for Bossman Computer Use) a change of its allow-list, which today lists only Notepad/Calculator and demands one owner approval per action. `ComputerUseBackend` is a thin client of that existing tool: whatever Computer Use refuses is a refusal here. Nothing here widens its perimeter.
* Opening a window in Bossman is a DISPLAY of the capture, not hosting the foreign application. Any capturable surface may be shown; recognition, advice and control are enabled per interface only where a verifying run exists.
