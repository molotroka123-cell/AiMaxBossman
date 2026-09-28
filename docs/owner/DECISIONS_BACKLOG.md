# Decisions backlog — Bossman 1.9 RC

Ideas found while closing 1.9 that are **not** part of the freeze. Each needs
an owner decision before implementation. Format: problem → example → benefit →
cost → risk → acceptance test. Items marked *(owner action)* need only the
owner, not code.

## Runtime / install

1. **Retire the split legacy shortcuts** *(owner action)*
   - Problem: Desktop «Bossman» → install 93f1f1c3 on :8801, «Bossman CMD» → install
     e97bad5b on :8800; both on the owner data root.
   - Benefit: one product, one backend. Since a5d9bf6e the second backend is refused
     (exit 5), but old installs don't have that guard.
   - Cost: minutes. Risk: none if the RC shortcuts are verified first.
   - Test: after switching, `backend.json` in the data root names one PID/port/SHA and
     both shortcuts attach to it.
2. **API key stored as a Desktop file name** *(owner action)* — move it into the vault
   and delete the file; rotate the key if it was ever shared.
3. **Memory-aware routing controls in the UI** — expose `router.rules.memory_headroom_mb`
   and show `router.memory_pressure` events on the Resources page.
   Benefit: the owner sees why a run went to a free model. Cost: S. Risk: low.
   Test: UI shows the event for a task that switched.

## Computer Use (from workstream B threat model)

4. Attach a target-window crop to each approval question (owner sees what they approve).
   Cost: M. Risk: privacy of other windows — crop only. Test: approval payload has a PNG
   of the approved window only.
5. Crop observation screenshots to the observed window (R1). Cost: S–M.
6. Refuse context-menu shortcuts (shift+F10) inside file dialogs (R2). Cost: S.
7. Restrict the file-exists check to owner-approved folders (R4). Cost: S.
8. Studio-only token for Jeff instead of the full-authority backend token (R7).
   Cost: M. Risk: Jeff studio flows break if a route is missed. Test: Jeff token gets
   403 on every non-studio route.
9. Retire or re-gate the legacy bossman-core operator (`/computer/tasks`) (R8).
10. Refused lease on `computer.act` → HTTP 409 instead of 500 (R6). Cost: XS.
11. Prefer the button when a click target matches a button and its label. Cost: S.

## Motion Studio (1.8 candidate, workstream E)

12. Vertical/square formats as a spec field — real layout redesign; test with
    `verify_library` at each size.
13. Russian narration: optional user-installed Piper (GPL, not bundled), Cyrillic font
    subset, Russian timing estimate; test with ASR WER.
14. Make the invented-number check blocking; neutral numbers in the few-shot example.
15. Fact-check non-numeric claims in specs.
16. Chromium in CI so engine tests run there.

## Learning / intelligence preservation

17. Intelligence Preservation needs a corpus large enough for the 98 % lower bound
    (see `docs/owner/RC19_RELEASE_PROCEDURE.md`); the current 20 items/metric cannot
    pass even when perfect. Decision: grow the corpus (cost: authoring + many hours of
    local model time) or change the gate definition (owner decision, never silently).
