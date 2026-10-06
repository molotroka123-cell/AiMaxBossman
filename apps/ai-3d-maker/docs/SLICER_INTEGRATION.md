# Slicer integration

## OrcaSlicer (real, measured 2026-10-06 on the owner PC)

`src/ai_3d_maker/orca.py`, reachable as `ai-3d-maker orca ...` or
`python -m ai_3d_maker.orca ...`:

```
python -m ai_3d_maker.orca locate
python -m ai_3d_maker.orca profiles --kind process --printer "Elegoo Neptune 3 Plus 0.4 nozzle"
python -m ai_3d_maker.orca slice model.stl --printer "Elegoo Neptune 3 Plus 0.4 nozzle" \
    --process "0.20mm Standard @Elegoo N3Plus 0.4 nozzle" --filament "Elegoo PLA @EN3 Series" --out DIR
python -m ai_3d_maker.orca open model.stl      # GUI, for the owner to look
```

Binary: OrcaSlicer 2.4.2 portable, `AI3D_ORCA_EXE` or auto-detected
(`~/Bossman/apps-local/OrcaSlicer/orca-slicer.exe`). Result: `DIR/<stem>.gcode`,
sha256, layers, estimated time, filament mm/cm3/g (parsed from Orca's own
G-code comments) and the verdict of `gcode.scan_gcode` with the app printer
profile. The run directory `DIR/<stem>.orca-run/` keeps the flattened presets,
Orca stdout/stderr, Orca's `00000.log` on failure and `result.json`.

Facts about the Orca CLI found on this host (not copied from Prusa/Bambu docs):

- Flags (from the option table in `OrcaSlicer.dll`; `--help` prints nothing
  because the exe is GUI-subsystem): `--slice N` (0 = all plates),
  `--load-settings "machine.json;process.json"`, `--load-filaments "f.json;..."`,
  `--outputdir DIR`, `--datadir DIR`, `--export-3mf FILE`, `--export-slicedata`,
  `--load-slicedata`, `--export-stl(s)`, `--export-settings`, `--arrange 0|1`,
  `--orient 0|1`, `--rotate*`, `--scale`, `--debug 0..5`, `--info`, `--pipe`.
  Executed and confirmed here: `--slice`, `--load-settings`, `--load-filaments`,
  `--outputdir`, `--datadir`, `--debug`, `--help`. The rest are only known to
  exist in the option table.
  There is **no** `--export-gcode` / `--output`; Orca writes `plate_<N>.gcode`
  into `--outputdir`.
- Bundled vendor presets cannot be loaded directly: the CLI does not follow
  `inherits` (leaf preset -> exit -51, validation error). The module flattens
  the chain root -> leaf; filament parents fall back to `OrcaFilamentLibrary`.
- A flattened preset must say `"from": "system"` and carry its own name in
  `printer_settings_id` / `print_settings_id` / `filament_settings_id`;
  with `"from": "User"` Orca answers exit -17 ("process not compatible with
  printer", text from Orca's `00000.log`).
- Windows reports Orca's negative exit codes as unsigned (-17 -> 4294967279);
  the module converts them back.
- Each run uses a private `--datadir`, so the owner's Orca GUI configuration is
  neither read nor changed, and a running GUI window did not interfere.

Safety: the module has no code path that sends G-code to a printer, uploads
anything or opens a network connection (Orca network traffic during a CLI run
was not measured). Physical printing remains reachable only through
`printer.confirm` with a per-job token and `AI3D_ALLOW_PHYSICAL_PRINT=1`.
The job pipeline's `--slice` still uses CuraEngine/PrusaSlicer only; Orca is a
separate command.

Known scan result for Orca output: in strict mode (`AI3D_STRICT_GCODE=1`, the
default) a 20 mm cube with the Neptune 3 Plus presets scans `WARN` with 99
warnings, all `G17` (plane select before Orca's non-extruding spiral Z-hop
arcs), which the scanner does not list as a known command. No ERROR.

## CuraEngine / PrusaSlicer

**Neither CuraEngine nor PrusaSlicer is installed on the host this was built
on**, so every job-pipeline slicing result is `NOT_RUN` with the reason
attached.

The work that was possible was making the path honest rather than making the
absence look like success.

## Rules that hold whatever engine is found

**A slicer that fails is never a PASS.** A non-zero return code is a failure;
so is a zero return code with no output file, which is its own line of code
because "it exited cleanly" is not evidence that G-code exists.

**Whatever ran is identified.** `SliceResult` carries `engine`,
`engine_version` (from `--version` / `--help`), `engine_path`, the full command
line, and for CuraEngine the **sha256 and byte size of the machine
definition**. A G-code file can be traced back to the exact engine and the
exact profile that produced it, which is the whole point of versioning a
profile.

**The command line is bounded.** `validate_slicer_settings` requires every
setting name to be a plain identifier and every value to be a scalar with no
line break or NUL, under 200 characters, at most 200 settings. It runs at job
intake, not inside the adapter — on a host with no slicer the adapter never
executes, and an unchecked input that is never checked is not a check.

**The timeout holds.** A slicer that forks a child which outlives it keeps the
output pipe open; waiting on that pipe after killing the parent would put the
deadline back where it started. After a kill the adapter waits on the process,
not on the pipes, with its own bound.

## The vendor definition

ELEGOO provides Neptune 3 Plus support in ELEGOO Cura.

**Do not ship a fabricated vendor definition, and none is shipped here.** On
the target machine, locate the ELEGOO Cura / Cura resources that are actually
installed, point `AI3D_CURA_DEFINITION` at that file, and the recorded sha256
becomes the identity of the profile that produced each G-code file.

3MF should come from a real supported slicer/export path rather than a fake
conversion.

Future OrcaSlicer/PrusaSlicer adapters should not alter the CAD core.

## What the tests prove, and what they do not

`tests/test_slicer.py` drives the adapters against a real subprocess — a stub
executable standing in for a slicer — so argument construction, version
probing, return codes, missing output and timeouts are covered by code that
actually runs. That is a `UNIT PASS` for the adapter.

It is **not** a `REAL SLICER PASS`. No real slicer has been executed, no real
G-code has been produced, and nothing here says anything about what CuraEngine
would emit for a Neptune 3 Plus.
