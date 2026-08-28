# AI 3D Maker — ELEGOO Neptune 3 Plus

BOSSMAN App #2. A standalone service that turns a design request into a
**verified printable artifact**, or refuses and says why.

The governing rule of this codebase:

> An STL file existing on disk is not evidence that anything can be printed.

Every "printable" verdict in this app is produced by explicit geometric checks
(`printability.decide_printability`), never inferred from a successful file
write. A model that is open, non-manifold, inverted or too large for the
machine is reported as `NOT_PRINTABLE` and its geometry is written to
`model.rejected.stl` — a name that cannot be mistaken for a print-ready file.

---

## Pipeline

```
request ─▶ intake (schema + requirement gate)
        ─▶ generate (DesignSpec → CSG) │ import (STL parse)
        ─▶ inspect  (watertight, manifold, winding, degenerate, components)
        ─▶ repair   (weld, drop degenerate/duplicate, unify winding, fix orientation)
        ─▶ transform(units → mm, scale, orientation search, place on bed)
        ─▶ inspect  (re-check after every modification)
        ─▶ printability verdict  ◀── the only thing that may say "printable"
        ─▶ export   (deterministic binary STL + sha256)
        ─▶ [slice]  (optional, external, honestly NOT_AVAILABLE when missing)
        ─▶ [G-code safety scan]
        ─▶ printer preparation (dry run, confirmation token)
        ─▶ STOP
```

The pipeline stops at preparation. **Nothing in `pipeline.py` can reach a heater
or a motor.** Physical action lives behind a separate confirmed call.

---

## Physical safety

Starting a print, preheating and moving axes are separated from digital
generation by design, not by convention. `printer.execute_physical` is the only
funnel to hardware, and it requires **all** of:

1. a transport that is not the simulator (the default *is* the simulator);
2. `AI3D_ALLOW_PHYSICAL_PRINT=true` in the environment;
3. the exact per-job confirmation token, derived from the job id **and the
   sha256 of the specific artifact** — so a human must have looked at that file.

Independently of all three, G-code that failed the safety scan can never be
sent. TF-card transfer cannot start a print: on a Neptune 3 Plus a person
starts the job from the printer's own screen. USB serial control is **not
implemented** and reports `BLOCKED_BY_HARDWARE`.

No Neptune 3 Plus is attached to the machine this was built on. Every physical
stage is exercised through the simulator and dry run only.

---

## Install

Python 3.11+. The core needs **no third-party packages at all** — mesh parsing,
validation, repair, printability and the G-code scanner are standard library.

```bash
pip install -e .            # core only
pip install -e ".[csg]"     # + boolean CSG (manifold3d) for multi-feature designs
pip install -e ".[api]"     # + FastAPI/uvicorn HTTP surface
pip install -e ".[verify]"  # + trimesh, used as an independent cross-check
pip install -e ".[dev]"     # + pytest
cp .env.example .env
```

Anything not installed is reported as `NOT_AVAILABLE` with a reason. Run
`ai-3d-maker capabilities` to see exactly what this host can and cannot do.

---

## Use

```bash
ai-3d-maker capabilities                     # what is actually available here
ai-3d-maker build examples/bracket.design.json
ai-3d-maker validate some_model.stl          # inspect an existing mesh
ai-3d-maker import some_model.stl --units in
ai-3d-maker jobs                             # history
ai-3d-maker artifacts JOB_ID                 # files + checksums
ai-3d-maker scan model.gcode                 # G-code safety scan
ai-3d-maker confirm JOB_ID                   # the human confirmation token
ai-3d-maker serve                            # HTTP control surface
```

There is deliberately no CLI verb that starts a physical print.

---

## Control contract

BOSSMAN is the control plane; this app is the workload. It imports nothing from
`bcc.*` or any BOSSMAN package, and nothing imports it back — a test enforces
both directions.

| operation | HTTP |
|---|---|
| `health` | `GET /health` |
| `capabilities` | `GET /capabilities` |
| `metrics` | `GET /metrics` |
| `jobs.create` | `POST /api/jobs` |
| `jobs.list` | `GET /api/jobs` |
| `jobs.status` | `GET /api/jobs/{id}` |
| `jobs.cancel` | `POST /api/jobs/{id}/cancel` |
| `artifacts.list` | `GET /api/jobs/{id}/artifacts` |
| `gcode.scan` | `POST /api/gcode/scan` |
| `printer.confirm` | `POST /api/printer/confirm` |

`control.ControlPlane` is the same contract in plain Python, with no HTTP
dependency.

---

## Printer profile

`profiles/elegoo_neptune_3_plus.json` keeps two groups strictly apart, and the
loader refuses to merge them:

* **`verified_machine_limits`** — manufacturer-published: 320 × 320 × 400 mm
  build volume, 330 × 330 mm platform, 0.4 mm stock nozzle, 260 °C nozzle and
  100 °C bed maxima. These are treated as **safety caps**, never as recommended
  print temperatures.
* **`process_defaults_unverified`** — this app's conservative guesses (layer
  height, line width, minimum wall lines). Not guarantees, not measurements.

Measured dimensional capability is a third, separate thing and lives in a
calibration profile (`tolerance.CalibrationProfile`) that must be explicitly
selected. Default compensation is zero, because an uncalibrated printer has no
measured capability to compensate for.

---

## Tests

```bash
python -m pytest -q
```

The physical smoke test on a real Neptune 3 Plus is present and permanently
skipped, marked `BLOCKED BY HARDWARE`. It is never allowed to report a pass.
