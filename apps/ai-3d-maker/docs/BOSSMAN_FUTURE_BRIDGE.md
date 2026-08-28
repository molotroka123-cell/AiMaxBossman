# BOSSMAN bridge

BOSSMAN is the control plane. This app is the workload. The boundary is
enforced by a test, in both directions:

* nothing under `src/ai_3d_maker/` imports `bcc.*`, `bossman` or
  `command_center` (`test_control_contract.py::test_the_app_never_imports_bossman`);
* the core package imports and runs with `fastapi`, `pydantic`, `numpy`,
  `trimesh`, `manifold3d` and `cadquery` all forced absent
  (`::test_the_package_imports_without_optional_dependencies`).

## Contract

`control.ControlPlane` — plain Python, no HTTP dependency:

| operation | method | HTTP |
|---|---|---|
| health | `health()` | `GET /health` |
| capabilities | `capabilities()` | `GET /capabilities` |
| metrics | `metrics()` | `GET /metrics` |
| jobs.create | `jobs_create(payload)` | `POST /api/jobs` |
| jobs.status | `jobs_status(id)` | `GET /api/jobs/{id}` |
| jobs.cancel | `jobs_cancel(id)` | `POST /api/jobs/{id}/cancel` |
| jobs.list | `jobs_list()` | `GET /api/jobs` |
| artifacts.list | `artifacts_list(id)` | `GET /api/jobs/{id}/artifacts` |
| gcode.scan | `gcode_scan(text)` | `POST /api/gcode/scan` |
| printer.confirm | `printer_confirm(payload)` | `POST /api/printer/confirm` |

`jobs.create` accepts `wait: false` to return as soon as the job is scheduled;
poll `jobs.status` or watch for a terminal state.

## Allowed autonomous loop

```
spec → CAD → validate → revise → validate → ...
```

BOSSMAN may iterate on a rejected design as many times as it likes: the reasons
in `validation.json` are specific enough to act on, which is the point.

## Never autonomous

```
generate → automatically start the physical printer
```

`printer.confirm` is the only operation that can reach hardware, and it demands
a token bound to a specific artifact digest (see `PHYSICAL_SAFETY.md`). A
control plane cannot mint that token on a human's behalf without having shown
the human the artifact — which is the entire intent.

## Memory

BOSSMAN may remember versioned printer and calibration profiles and lessons from
successful designs. It must not turn one bad print into a universal
compensation: calibration is per printer, per material, per nozzle, per layer
height, and this app will only apply a compensation from a profile that was
explicitly selected.
