# Calibration

Nominal CAD geometry and printer compensation are two different things and are
kept in two different places. `profiles/elegoo_neptune_3_plus.json` holds what
the machine *is*. `tolerance.CalibrationProfile` holds what one specific
process on that machine was *measured* to hold.

**Compensation is zero until a measured profile is explicitly selected**, and
this build applies none even then: a selected profile answers the question
"can this process hold that tolerance at all", it does not silently resize
geometry. `requirement_gate.calibration.compensation_applied` is `false` in
every result this app produces today.

## What a profile has to say

A tolerance figure is not a property of a printer. It is a property of one
printer with one nozzle running one material at one layer height and one line
width, measured on a coupon on a date. `CalibrationProfile` refuses to be
constructed without all of it:

| field | why |
|---|---|
| `printer_profile_id` | a measured capability does not transfer between machines |
| `material` | PLA and PETG do not shrink alike |
| `nozzle_mm` | a 0.6 mm nozzle is a different process |
| `layer_height_mm` | Z accuracy is layer-height bound |
| `line_width_mm` | wall thickness and hole size follow the extrusion width |
| `measured_process_tolerance_mm` | the capability figure itself, positive |
| `coupon_measurements` | `{feature: [nominal_mm, measured_mm]}` — the calipers reading behind the figure |
| `measured_at` | an undated measurement cannot be superseded |
| `version` | a re-measurement replaces a profile; it never averages with it |

`tolerance.coupon_spec()` emits a DesignSpec for the coupon: a plate with one
through hole. Print it, let it cool fully, measure X, Y, Z and the hole with
calipers, and record the pairs.

## How it reaches a job

```json
{"kind": "design", "spec": {...}, "calibration_profile": "profiles/pla-0.4-0.2.json"}
```

Inline objects work too. The gate then reports where its number came from:

* `source: "measured_profile"` — a profile was selected; its id, version and
  measurement date are in the result.
* `source: "caller_assertion_unverified"` — a bare `calibrated_tolerance_mm`
  was passed with nothing behind it. It is honoured, and it is labelled, and a
  warning says it is not backed by a measurement.
* `source: "none"` — no capability figure at all. A tolerance request still
  produces a warning that nominal CAD size is not a promise about printed size.

A profile measured on another printer blocks the job. A profile measured on a
different material or with a different nozzle warns loudly rather than
applying quietly — that is the "one universal hole compensation" failure this
file exists to prevent.

## What is not calibrated here

No coupon has been printed on this host: there is no printer attached. Every
capability figure in this repository is either absent or supplied by a caller.
Nothing here is a measured result.
