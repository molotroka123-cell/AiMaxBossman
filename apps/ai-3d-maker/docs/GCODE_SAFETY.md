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
* `G92` coordinate resets and `G28` homing;
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

## Strict mode

With `AI3D_STRICT_GCODE=true`, any G/M command the scanner does not model is
raised as a WARNING. Warnings do not block; only ERRORs do.

## What it is not

A scanner is not a firmware emulator. It does not simulate acceleration,
thermal runaway protection, or mesh bed levelling. Before production use,
run it against the start and end G-code your actual ELEGOO Cura profile emits
and review the warnings — they will tell you which of your normal commands the
scanner does not yet model.

## Position in the flow

```
slice → scan → dry run → report → HUMAN APPROVAL → transfer
```

G-code that fails the scan is refused at `printer.execute_physical` before any
other gate is even consulted. There is no configuration flag that overrides it.
