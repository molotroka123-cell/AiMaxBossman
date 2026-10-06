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
