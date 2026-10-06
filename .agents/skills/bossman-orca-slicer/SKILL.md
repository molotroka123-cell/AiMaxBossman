---
name: bossman-orca-slicer
description: Slice an STL into G-code with the local OrcaSlicer 2.4.2 using real bundled printer/process/filament presets (default ELEGOO Neptune 3 Plus), then run the app's G-code safety scanner and report time, filament, layers, sha256 and the scan verdict. One command from chat. Never sends G-code to a printer and never starts a print.
compatibility: BOSSMAN (Windows owner machine, OrcaSlicer portable at C:\Users\asd\Bossman\apps-local\OrcaSlicer, Python 3.12, no extra packages)
metadata:
  owner: bossman
  version: "1.0"
  learned_from: owner task 2026-10-06 (OrcaSlicer control module, 20 mm cube demo)
---

# OrcaSlicer: slice + safety scan (no printing)

Code: `apps/ai-3d-maker/src/ai_3d_maker/orca.py` (stdlib only). Facts and limits:
`apps/ai-3d-maker/docs/SLICER_INTEGRATION.md`. Tests: `apps/ai-3d-maker/tests/test_orca.py`.

Every command below runs from `apps\ai-3d-maker\src` in the repo (no install and no PYTHONPATH needed).
`ai-3d-maker orca <verb> ...` is the same command when the app is installed.

## Chat command -> what to run

Owner says "нарежь модель" / "слайсни STL" / "/3d orca <file>":

1. Slice with the Neptune 3 Plus defaults (one command):
   ```
   python -m ai_3d_maker.orca slice "<model.stl>" --printer "Elegoo Neptune 3 Plus 0.4 nozzle" --process "0.20mm Standard @Elegoo N3Plus 0.4 nozzle" --filament "Elegoo PLA @EN3 Series" --out "<output folder>"
   ```
   It prints JSON. Exit code 0 means G-code was produced and the scan is not FAILED. Exit code 1 means a refusal
   or a scan FAILED. Exit code 2 means Orca was not found.
2. Report these fields exactly as printed: `gcode_path`, `gcode_bytes`, `estimated_time`, `filament_used.g` and
   `filament_used.mm`, `layers`, `sha256`, `gcode_safety.status`, `gcode_safety.issue_counts`, and `warnings`.
3. Another printer or material: list the presets first. A filtered listing takes under a second. An unfiltered
   listing across all vendors can take about 30 s.
   ```
   python -m ai_3d_maker.orca profiles --vendor Elegoo
   python -m ai_3d_maker.orca profiles --kind process --printer "<printer preset name>"
   python -m ai_3d_maker.orca profiles --kind filament --printer "<printer preset name>"
   ```
   Pass the preset names exactly. A path to a preset `.json` file also works.
4. Only when the owner asks to see the model: `python -m ai_3d_maker.orca open "<model.stl>"`. This launches the
   Orca GUI with the file. If an Orca window is already open, do not close it or kill it.

## Safety rules (must)

- This module cannot print, upload, or talk to a printer or the network, and nobody may add that to it.
  Printing exists only in the app's control plane (`printer.confirm` with a per-job confirmation token and
  `AI3D_ALLOW_PHYSICAL_PRINT=1`). Use it only on an explicit owner command naming the job, and only after the owner
  has connected a printer. As of 2026-10-06 no printer is connected.
- Do not press Print or Send in the Orca GUI, and do not connect Orca to a printer or a cloud account.
- A scan of `WARN` is not a `PASS`. Say which warnings it found. A scan of `FAILED` means the G-code must not be
  used. Never describe a slice as "ready to print". The scan checks the app profile limits only: build volume,
  temperatures, and blocked M-codes.
- Never kill a running `orca-slicer.exe` that you did not start. The owner may have the GUI open.

## Known facts / traps

- On a 20 mm cube with the defaults, strict scanning returns `WARN` with 99 `G17` warnings. Orca emits `G17` before
  its spiral Z-hop arcs, and the scanner does not list `G17` as a known command. No ERROR was found. Report it
  this way. Do not call it PASS.
- The safety scan always uses the app profile, which is the ELEGOO Neptune 3 Plus. If you slice for another
  printer, the result includes a warning saying so. Envelope and temperature checks are then not checks for
  that printer.
- Orca's CLI does not resolve preset `inherits`. The module flattens presets itself. If a preset's process does
  not list the printer as compatible, Orca exits -17. The error JSON then carries Orca's own `00000.log` text.
- Orca places the model on the bed automatically. Use the default `0.20mm Standard` process unless the owner
  asks for another layer height.
- Each run writes `<out>\<stem>.gcode` and `<out>\<stem>.orca-run\`, which holds the presets, logs and
  `result.json`. Re-running replaces only that run folder.
