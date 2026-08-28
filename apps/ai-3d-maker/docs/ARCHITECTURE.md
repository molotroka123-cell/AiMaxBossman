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
the reason physical actions and digital generation are separate modules.

## Stages

| Stage | Module | Refuses when |
|---|---|---|
| intake | `spec`, `requirements`, `paths` | schema invalid, ambiguity unresolved, tolerance beyond calibration, unsafe job id |
| generate | `cad.compiler`, `cad.csg` | no CSG backend for a multi-feature spec, boolean yields an empty solid |
| import | `mesh.load_stl` | corrupt, truncated, empty, NaN, oversized |
| inspect | `meshcheck.inspect_mesh` | — (reports, does not refuse) |
| repair | `repair.repair_mesh` | — (never invents geometry; holes stay holes) |
| transform | `printability` | unknown unit, non-positive scale |
| printability | `printability.decide_printability` | not watertight / not manifold / winding inconsistent / inverted / does not fit |
| export | `mesh.write_stl` | — (rejected geometry goes to `model.rejected.stl`) |
| slice | `slicer` | reports `NOT_AVAILABLE` with a reason; never fabricates G-code |
| gcode scan | `gcode.scan_gcode` | temperature over the verified cap, extrusion outside the envelope, persistent/reset commands |
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
