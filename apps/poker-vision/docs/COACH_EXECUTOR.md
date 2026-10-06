# COACH and EXECUTOR — roles, contracts, gates

```
source ──► Vision (observed state) ──► validated state (reconciler) ──► Policy (action + size) ──► PolicyDecision (frozen)
                                                                                    │
                          COACH shows: options, explanation, uncertainty, disclaimer │
                                                                                    ▼
 Locator (WHERE is the element)  ──►  Executor (permission, preflight, map, click)  ──►  Verifier (new frame must show the effect)
```

| role | does | must not |
|---|---|---|
| Vision (`adapters/*`, `reconcile.py`) | restores the **observable** hand state with per-field confidence, time, source; UNKNOWN allowed | invent hidden cards, guess unreadable numbers |
| Policy (`coach.py` → `strategy.py`) | chooses action **and** size; produces a frozen `PolicyDecision` (`kind`, `raise_to`, acceptable `[size_min, size_max]`, `hand_key`, `street`, `to_call`) | see opponents' cards, deck or engine state (`VisibleInfo` has no such field) |
| Locator (`control/locator.py`) | answers *where* the element named `label` is, on **this** fresh frame | change the label or the amount |
| Executor (`control/executor.py`) | checks permission and every precondition, maps frame→screen, presses, verifies, halts on doubt | decide strategy; retry an unconfirmed click |
| Verifier (inside executor) | a new frame must show the effect (hero no longer to act / pot or hand changed / panel open / read-back amount equals the preset) | accept "no change" |

A UI-grounding **model** may be plugged in as a Locator (`ModelLocator`). Its answer is used only if it names the **same label**, lies inside the frame and **overlaps the button vision itself sees** (IoU ≥ 0.30). Otherwise the click is refused. A model therefore cannot redirect a click to another element, another label, or outside the window (`tests/test_control.py::test_model_locator_cannot_redirect_the_click`).

## Preflight before every click (all re-done per click, nothing cached)
1. STOP not set; action budget; executor not halted.
2. Fresh frame; **window identity** = handle + process id + process start time (never the title): closed / reopened-as-another-process / minimised / hidden / occluded (>2 %) / resized (profile must be re-verified) / frame older than 1.2 s ⇒ halt.
3. Frame was captured at the same window rectangle as it is now (moved or resized since capture ⇒ halt); frame/window aspect must match (cropped or stale frame ⇒ refused).
4. Committed state not blocked (stale, frozen, pending, transition errors); hero's turn; same hand and street as the decision; the action is on screen; `CALL` amount equals the decided one.
5. **Re-locate** the button on this frame; exactly one match.
6. Map frame→screen with window position, DPI/scale and the pointer's unit scale; the point must lie inside the window and the target box.
7. Ask the window system who is on top at that point; another window ⇒ halt. The same check is repeated immediately before the press.
8. Press → read new frames until the state really changed (≤ 6 s) or halt. **An unconfirmed click is never repeated**; control stays halted until the owner reviews and resumes.

Single-frame doubts before any click (a button not read on one frame) are re-read up to 6 fresh frames, then halt. A decision whose state no longer exists (new hand/street, hero no longer to act) is dropped without halting.

## Raise sizing (amount is policy-owned)
`RAISE` = click RAISE (panel opens) → read presets and the "Raise to" number from the panel → the executor picks the preset **inside the policy's range** closest to the policy's target (none ⇒ halt, the clicker never changes the amount) → read-back must equal the preset value → only then CONFIRM.

## Modes (page buttons «Наблюдение / Подсказки / Управление»)
* Наблюдение: show capture + overlay + readings + history.
* Подсказки: adds options, explanation, uncertainty and the disclaimer: unknown cards and ranges; equity is against random hands; not optimal play, not a guarantee.
* Управление: adds execution; also pause, change source, STOP.
STOP sets one event that every loop and every step checks before it does anything; a click already sent is reported as "not verified".

## Honest limits
* Control is proven only in the sandbox desk against the owner's own trainer (no money), on the layouts listed in `evidence/REPORT_COACH_EXECUTOR.md`. DPR 2 and other layouts: see its table; vision there is mostly UNKNOWN and the executor therefore mostly refuses.
* The sandbox desk simulates window operations; it is not Windows behaviour.
* A window can still move in the microseconds between the last check and the OS click; this cannot be removed in user space.
