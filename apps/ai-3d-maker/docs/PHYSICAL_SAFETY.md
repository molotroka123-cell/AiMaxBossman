# Physical safety

Everything in this application is digital except one module. `printer.py` is
the only code that can move a heater or a motor, and reaching it is deliberately
awkward.

## The three gates

`printer.execute_physical` requires **all** of the following. Failing any one is
a refusal, never a downgrade to "do it anyway".

1. **A real transport.** The default is `simulator`, which touches nothing. Only
   `tf_card` and `usb_serial` are hardware transports.
2. **Environment consent.** `AI3D_ALLOW_PHYSICAL_PRINT=true`. Off by default.
3. **A human confirmation token.** Derived from the job id **and the sha256 of
   the exact artifact**:

   ```
   PRINT-CONFIRM-<first 16 hex of sha256("<job_id>:<artifact_sha256>")>
   ```

   Because the digest is part of it, the token changes the instant the file
   changes. A human must have looked at *that* file, not at "the job".

## Ahead of all three

G-code whose safety scan returned `FAILED` is refused before the confirmation
gate is even evaluated. There is no flag, no transport and no token that
overrides an unsafe scan.

## What each transport can and cannot do

| Transport | transfer | preheat | start print | move axes |
|---|---|---|---|---|
| `simulator` | simulated | simulated | simulated | simulated |
| `tf_card` | **yes** (copies the file) | refused | refused | refused |
| `usb_serial` | `BLOCKED_BY_HARDWARE` | `BLOCKED_BY_HARDWARE` | `BLOCKED_BY_HARDWARE` | `BLOCKED_BY_HARDWARE` |

TF-card transfer deliberately cannot start a print. On a Neptune 3 Plus the job
is started from the printer's own screen, by a person standing in front of it.
ELEGOO documents TF card and USB cable as the transfer methods; this app does
not invent a network print interface.

USB serial streaming is **not implemented**. It reports `BLOCKED_BY_HARDWARE`
rather than a stub that might one day be filled in silently.

## Status on this host

No ELEGOO Neptune 3 Plus is attached to the machine this was built on. Every
physical stage has been exercised through the simulator and the dry run only.
The real-printer smoke test exists in the test suite, is permanently skipped,
and is written so that it fails rather than passes if anyone ever un-skips it
without a machine present.

Anything about behaviour against real hardware is **BLOCKED BY HARDWARE** and
must not be recorded as a pass.
