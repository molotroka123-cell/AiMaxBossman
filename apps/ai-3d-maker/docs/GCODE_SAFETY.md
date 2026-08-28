# G-code safety

Generated G-code is executable machine control, not a document.

## Hard caps, from the verified machine limits

* nozzle ≤ 260 °C
* bed ≤ 100 °C
* extrusion moves inside 320 × 320 × 400 mm

These are **maximum ratings used as safety caps**, not recommended print
temperatures. Nothing in this app treats 260 °C as a temperature to print at.

## What the scanner models

`gcode.scan_gcode` walks the file maintaining machine state:

* absolute / relative positioning (`G90`/`G91`) and extrusion (`M82`/`M83`);
* **units (`G20`/`G21`)** — in inch mode every coordinate is converted to
  millimetres before it is compared against anything. Without this, `G20`
  followed by `G1 X13 Y13 E1` reads as 13 mm and passes, when it is 330.2 mm
  and drives an extruding head off the front of a 320 mm bed. The mode the
  file ended in is reported as `units_mode`;
* `G92` coordinate resets (scaled by the current unit mode), `G28` homing and
  `G27` parking;
* travel versus extrusion — only **extruding** moves are envelope-checked,
  because travel outside the print area is normal;
* temperature targets from `M104`/`M109`/`M140`/`M190`, including the `R` form.

Commands are normalised before lookup, so `M104S300`, `M0104 S300` and
`m104 s300` are all recognised. `;` and `( ... )` comments are stripped. These
normalisations exist because the seed scanner this replaced could be bypassed by
simply omitting a space.

## Blocked outright

Commands that persist configuration or restart the machine — slicer output for
an ordinary print never needs them:

`M500` `M501` `M502` `M503` (EEPROM), `M92` (steps per unit), `M851` (probe
offset), `M301` `M304` (PID), `M997` (firmware update), `M999` (error clear).

## Flagged whatever the mode

A command that persists nothing but disables a firmware interlock is still not
something print output contains. These are raised **regardless of strict mode**,
because "not recognised" must never mean "quietly ignored" for a command that
matters:

| command | severity | why |
|---|---|---|
| `M302` | ERROR | disables the cold-extrusion guard |
| `M303` | ERROR | PID autotune, heats the hotend unattended for minutes |
| `M290` | WARNING | babystep Z offset the scanner cannot verify |
| `M906` `M907` | WARNING | changes stepper driver current |
| `M913` `M914` | WARNING | changes hybrid threshold / stall sensitivity |

## Strict mode

With `AI3D_STRICT_GCODE=true` (the default), any *other* G/M command the
scanner does not model is raised as a WARNING. Warnings do not block; only
ERRORs do.

## What it is not

A scanner is not a firmware emulator. It does not simulate acceleration,
thermal runaway protection, or mesh bed levelling. Two limits it states out
loud rather than hiding:

* **Arcs.** `G2`/`G3` endpoints are envelope-checked; the arc between them is
  not interpolated. An extruding arc therefore raises a WARNING naming that
  limit.
* **Real slicer output.** No Neptune 3 Plus G-code fixture exists in this
  repository, and no ELEGOO Cura profile was available to produce one. The
  scanner has never been run against real start/end G-code for this machine.
  Before production use, run it against what your actual ELEGOO Cura profile
  emits and review the warnings — they will tell you which of your normal
  commands the scanner does not yet model.

## Position in the flow

```
slice → scan → dry run → report → HUMAN APPROVAL → transfer
```

G-code that fails the scan is refused at `printer.execute_physical` before any
other gate is even consulted. There is no configuration flag that overrides it.
**G-code that was never scanned is refused on the same gate** — the absence of
a scan is not evidence of safety. Whether a file is a print program is decided
by its content, so renaming `model.gcode` to `notes.txt` buys no bypass.
