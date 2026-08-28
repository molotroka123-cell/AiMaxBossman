# Slicer integration

**No slicer is installed on the host this was built on.** Neither CuraEngine
nor PrusaSlicer nor OrcaSlicer is on PATH, so every slicing result here is
`NOT_RUN` with the reason attached. Nothing in this repository has ever been
sliced.

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
