# Architecture

## Layers

```
control plane (BOSSMAN, CLI, HTTP)
        │  health / capabilities / jobs.* / artifacts.list / metrics
        ▼
control.ControlPlane          ← the whole contract, in plain Python
        ▼
pipeline.Pipeline             ← staged job execution, digital only
        ▼
┌───────────────┬──────────────┬──────────────┬──────────────┐
│ cad/          │ mesh/        │ printability │ gcode        │
│ compiler      │ meshcheck    │              │ (scanner)    │
│ primitives    │ repair       │              │              │
│ csg           │ mesh (I/O)   │              │              │
└───────────────┴──────────────┴──────────────┴──────────────┘
        ▼
printer.execute_physical      ← the ONLY path to hardware, triple-gated
```

`pipeline.py` cannot reach `execute_physical`. That is not a convention; it is
the reason physical actions and digital generation are separate modules, and
`test_no_autonomous_print.py` checks it over the syntax tree rather than
trusting the layout.

Running alongside the stages is `evidence.EvidenceLedger`: a fixed set of
claims — spec, CAD, generative, STEP, OpenSCAD, mesh validation, cross-check,
printability, slicer, G-code safety, physical printer — each answered exactly
once with `PASS` / `FAIL` / `NOT_RUN`. The ledger is created by `Pipeline.run`,
not by `_run_stages`, so a job that dies partway through still reports what had
and had not run when it died.

## Stages

| Stage | Module | Refuses when |
|---|---|---|
| intake | `spec`, `requirements`, `paths` | schema invalid, ambiguity unresolved, tolerance beyond calibration, unsafe job id |
| generate | `cad.compiler`, `cad.csg` | no CSG backend for a multi-feature spec, boolean yields an empty solid, a `cut`/`intersect` changes nothing (the feature does not reach the body) |
| import | `mesh.load_stl` | corrupt, truncated, empty, NaN, oversized |
| inspect | `meshcheck.inspect_mesh` | — (reports, does not refuse) |
| repair | `repair.repair_mesh` | — (never invents geometry; holes stay holes) |
| transform | `printability` | unknown unit, non-positive scale |
| printability | `printability.decide_printability` | not watertight / not manifold / winding inconsistent / inverted / does not fit |
| export | `mesh.write_stl` | — (rejected geometry goes to `model.rejected.stl`) |
| slice | `slicer` | reports `NOT_AVAILABLE` with a reason; never fabricates G-code; a non-zero exit, or a zero exit with no output file, is `FAILED` |
| gcode scan | `gcode.scan_gcode` | temperature over the verified cap, extrusion outside the envelope (in whichever unit mode `G20`/`G21` selected), persistent/reset commands, firmware interlock overrides |
| printer prepare | `printer.dry_run` | — (simulation only; emits the confirmation token) |
| **physical** | `printer.execute_physical` | **separate confirmed call, never reached by the pipeline** |

## Why the constrained DSL

Normal operation never executes model-authored Python. A model emits a
`DesignSpec` (JSON), pydantic validates it with `extra="forbid"`, and a
deterministic compiler turns it into geometry. `cad.execute_arbitrary_code`
stays denied in `app.manifest.yaml` because there is no code path that would
honour it.

## Why failure is loud

A failed validation produces `NOT_PRINTABLE` with a list of specific reasons,
written into `validation.json` and `print_report.md`, and the geometry is named
`model.rejected.stl`. Nothing downstream can mistake it for a printable
artifact. That feedback is what makes another design iteration possible instead
of a surprise at the printer.
