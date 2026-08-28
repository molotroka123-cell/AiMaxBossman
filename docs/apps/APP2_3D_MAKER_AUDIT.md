# APP #2 — AI 3D Maker / ELEGOO Neptune 3 Plus — Source Pack Audit

**Audited artifact:** `/tmp/packs/app2/` (`AI_3D_MAKER_NEPTUNE3PLUS_APP2.zip`)
**Audit date:** 2026-08-28
**Auditor:** build agent for `apps/ai-3d-maker`
**Scope:** the source pack as delivered — 4 top-level files, `ai-3d-maker/` with
8 docs, 15 source modules, 7 test files, 2 profiles, 2 examples.

This audit describes **the pack as it arrived**. It is the input to, not a
description of, the application now in `apps/ai-3d-maker/`. Where a defect was
carried into the new build or fixed there, the entry says so.

---

## Evidence levels

Every claim below carries exactly one level. A claim without a level is not a
claim.

| Level | Meaning |
|---|---|
| **REAL IMPLEMENTED** | Working code exists and does what it says; established by reading the source, not by executing it in this audit. |
| **REAL TESTED** | I executed it in this session and observed the stated behaviour. Commands and outputs are quoted. |
| **MOCK TESTED** | Exercised only against a stub, simulator or dry run — never against the real external system. |
| **STATICALLY VERIFIED** | Determined by reading code, config or docs, without execution. |
| **NOT TESTED** | No evidence gathered either way. |
| **BLOCKED BY HARDWARE** | Cannot be established without the physical ELEGOO Neptune 3 Plus, which is not attached to this host. |

**Test runs this audit relies on:**

```
$ cd /tmp/packrun && PYTHONPATH=/tmp/packrun/src python -m pytest -q
19 passed in 0.24s
```

Plus five targeted probe scripts, quoted inline below.

---

## 1. Purpose

* The pack is a seed for a "manufacturing-ready artifact" pipeline for one
  specific printer, the ELEGOO Neptune 3 Plus, with two declared modes:
  Mode A precision CAD (P0) and Mode B generative 3D (P1).
  — **STATICALLY VERIFIED** (`00_READ_FIRST.md`, `MASTER_PROMPT_FOR_CLAUDE.md`).
* Mode A exists as code. **REAL IMPLEMENTED** (`spec.py`, `openscad.py`,
  `pipeline.py`).
* Mode B (generative/organic meshes) exists **only as prose** in
  `docs/GENERATIVE_3D.md`. There is no generator adapter, no interface, no
  stub, no test. — **STATICALLY VERIFIED** (`grep` over `src/`: no module
  references a text-to-3D or image-to-3D engine).
* Despite the name, **there is no AI in the pack**. No LLM call, no model
  client, no prompt template in any source module. The DesignSpec is expected
  to arrive already-formed from outside. — **STATICALLY VERIFIED** (all 15
  modules read; no HTTP client, no SDK import, no API key usage).

---

## 2. Architecture

* Documented flow: intent → normalizer → DesignSpec → requirement gate →
  deterministic CAD → STEP/STL → mesh validator → printer fit → slicer →
  G-code scan → human approval. — **STATICALLY VERIFIED**
  (`docs/ARCHITECTURE.md`).
* Implemented flow is shorter than documented. `pipeline.PrecisionPipeline.build`
  runs: requirement gate → SCAD compile → CadQuery-or-OpenSCAD export → mesh
  validate → bbox fit → write report. — **REAL IMPLEMENTED**
  (`pipeline.py:14-54`).
* **The slicer stage and the G-code scan stage are not in the pipeline at all.**
  `slicer.curaengine_slice` and `gcode.scan_gcode` are never called from
  `pipeline.py`; the scanner is reachable only through one HTTP route, and the
  slicer is reachable from nothing. — **REAL TESTED**
  (`inspect.getsource(pipeline)` contains neither `slicer` nor `scan_gcode`).
* Consequently `print_report.md` always records `gcode_status: NOT_RUN`,
  hard-coded at `pipeline.py:52`. — **STATICALLY VERIFIED**.
* `tolerance.py` (calibration profiles) is imported by nothing. Dead module in
  the shipped wiring. — **REAL TESTED** (same probe).
* The "Requirement Normalizer" of the architecture doc does not exist as code;
  `requirements.py` is a gate over an already-structured spec, not a normalizer
  of natural language. — **STATICALLY VERIFIED**.

---

## 3. Runtime

* Python ≥ 3.11, single process, FastAPI + uvicorn, no worker pool, no queue.
  — **STATICALLY VERIFIED** (`pyproject.toml`, `main.py`).
* `main.main()` starts uvicorn on `AI3D_HOST`/`AI3D_PORT`, default
  `127.0.0.1:8890`. — **REAL IMPLEMENTED** (`main.py:4-6`).
* Jobs run **inline inside the HTTP request**. `POST /api/precision/build`
  awaits the whole pipeline, including subprocess CAD export. A slow OpenSCAD
  run blocks the response for up to its 60 s timeout.
  — **STATICALLY VERIFIED** (`api.py:33-37`, `openscad.py:33`).
* There is no job identity beyond a directory name, no job record, no status
  endpoint, no history, no cancellation, no per-job timeout.
  — **STATICALLY VERIFIED** (no store module exists; `api.py` has 3 routes).
* Python 3.11.15 on this host. — **REAL TESTED** (`python3 -V`).

---

## 4. Dependencies

Declared in `pyproject.toml`:

| Group | Packages | Present on this host |
|---|---|---|
| required | fastapi ≥0.111, uvicorn ≥0.30, pydantic ≥2.7 | yes (0.141.1 / 0.52.4 / 2.13.4) |
| `dev` | pytest ≥8, pytest-asyncio ≥0.23 | pytest 9.1.1 yes, pytest-asyncio no |
| `mesh` | trimesh ≥4.4, numpy ≥1.26 | numpy 2.4.6 yes; trimesh **absent at audit time** |
| `cad` | cadquery ≥2.4 | **absent** |

— **REAL TESTED** (import probe over each module).

External binaries the code shells out to:

| Binary | Used by | Present |
|---|---|---|
| `openscad` | `openscad.export_stl` | **absent** (`which openscad` → nothing) |
| `CuraEngine` | `slicer.curaengine_slice` | **absent** |
| `prusa-slicer` | — (not referenced by the pack) | **absent** |

— **REAL TESTED** (`which` for each).

* **Critical consequence:** with none of `openscad`, `cadquery`, `trimesh`
  installed — the exact state of this host on arrival — the pack can produce
  *no geometry at all* and *verify nothing*. The pipeline degrades to writing a
  `.scad` text file. — **REAL TESTED** (probe C below).
* `cadquery` is a heavy dependency (OCP/OpenCascade, ~1 GB installed). The pack
  makes it optional, which is the right call. — **STATICALLY VERIFIED**.

---

## 5. Entry points

* `ai-3d-maker` console script → `ai_3d_maker.main:main` → uvicorn.
  — **REAL IMPLEMENTED** (`pyproject.toml [project.scripts]`).
* HTTP: exactly three routes — `GET /api/profile`,
  `POST /api/precision/build`, `POST /api/gcode/scan`.
  — **STATICALLY VERIFIED** (`api.py`).
* There is **no CLI** beyond starting the server: no build verb, no validate
  verb, no scan verb. The `/3d make`, `/3d validate`, `/3d slice`,
  `/3d calibrate` commands in `app.manifest.yaml` are declarations with no
  implementation anywhere in the pack. — **STATICALLY VERIFIED**.
* No `health`, no `capabilities`, no `metrics`, no `jobs.*`, no
  `artifacts.list`. — **STATICALLY VERIFIED**.

---

## 6. Frontend

* **There is none.** No HTML, no JS, no template, no static directory, no
  FastAPI mount. `app.manifest.yaml` declares `entrypoints.standalone: true`,
  which is not backed by any UI code. — **STATICALLY VERIFIED**
  (full file listing of the pack: 41 files, zero frontend assets).

---

## 7. Backend

* `PrinterProfile` — dataclass loader that reads `verified_machine_limits` and
  `coordinate_policy`. — **REAL IMPLEMENTED**, **REAL TESTED** (pack's
  `test_profile.py` asserts 320/320/400, 0.4 mm, 260 °C, 100 °C; passes).
* `DesignSpec` — pydantic v2 model, box + cylinder primitives, add/cut,
  translate/rotate, positive-dimension and unique-id validators.
  — **REAL IMPLEMENTED**, **REAL TESTED** (`test_spec.py`, 2 tests, pass).
* `evaluate_requirements` — tolerance/ambiguity gate.
  — **REAL IMPLEMENTED**, **REAL TESTED** (`test_requirements.py`, 3 tests).
* `compile_scad` — deterministic OpenSCAD text from a spec.
  — **REAL IMPLEMENTED**, **REAL TESTED** (`test_spec.py::test_compile`).
* `export_stl` (OpenSCAD subprocess) — **NOT TESTED**; the binary is absent, so
  only its `FileNotFoundError` branch can execute here.
* `cadquery_engine.export` (STEP + STL) — **NOT TESTED**; CadQuery absent.
* `validate_mesh` — **REAL IMPLEMENTED** but the verdict is wrong; see §16.
* `check_bbox_fit` — **REAL IMPLEMENTED**, **REAL TESTED**
  (`test_geometry.py`, 4 tests); orientation selection is defective, see §16.
* `scan_gcode` — **REAL IMPLEMENTED**, **REAL TESTED** (5 tests pass); has real
  bypasses, see §16.
* `curaengine_slice` — **NOT TESTED** and unreachable from the pipeline.
* `build_manifest` / `write_report` — **REAL IMPLEMENTED**, **NOT TESTED**
  (no test covers them; `MASTER_PROMPT` lists "artifact manifest" as a required
  gate, and no such test exists).

---

## 8. Storage

* Job output goes to `AI3D_DATA_DIR/jobs/<job_id>/`, default `./data`, i.e.
  **relative to the process working directory**. Starting the server from a
  different directory silently writes somewhere else — and the default printer
  profile path `./profiles/elegoo_neptune_3_plus.json` fails to load entirely.
  — **STATICALLY VERIFIED** (`api.py:21-22`, `.env.example`).
* Files written per job: `design_spec.json`, `model.scad`, `model.stl`,
  `model.step` (CadQuery path only), `validation.json`, `print_report.md`,
  `manifest.json`. — **REAL IMPLEMENTED** (`pipeline.py`).
* `manifest.json` records path, size and sha256 per file.
  — **REAL IMPLEMENTED** (`artifacts.py:10-16`).
* No database, no index, no retention policy, no disk quota, no cleanup. A job
  directory is reused and overwritten if the same `job_id` is sent twice.
  — **STATICALLY VERIFIED**.

---

## 9. Hardware access

* **The pack contains no hardware access whatsoever.** No `serial`/`pyserial`
  import, no socket to a printer, no USB, no filesystem write to removable
  media, no G-code streaming. — **STATICALLY VERIFIED** (all 15 modules read).
* This is the pack's single strongest safety property: it is structurally
  incapable of starting a print, and `docs/GCODE_SAFETY.md` plus
  `app.manifest.yaml` (`printer.start_physical_print: deny`) state that intent
  explicitly. — **STATICALLY VERIFIED**.
* It is also a gap: there is no *deliberate* physical boundary, no confirmation
  mechanism, no transfer stage — only absence. Nothing in the pack would stop a
  later contributor from adding a `start_print()` next to the slicer call.
  — **STATICALLY VERIFIED**.
* Anything about how this code behaves against a real Neptune 3 Plus is
  — **BLOCKED BY HARDWARE**.

---

## 10. Model (AI) access

* No LLM, no inference client, no model endpoint, no API key, no prompt. The
  `MASTER_PROMPT` assigns the model the job of emitting a DesignSpec, and the
  pack correctly refuses to execute model-authored code — but it also never
  calls a model. — **STATICALLY VERIFIED**.
* `app.manifest.yaml` sets `cad.execute_arbitrary_code: deny`, and no dynamic
  code execution appears anywhere in `src/`: grep for `exec(`/`eval(`/`compile(`
  returns a single hit, `re.compile` in `gcode.py:5`, which is regex
  compilation, not code execution. — **REAL TESTED**.
* Because `DesignSpec` does not set `extra="forbid"`, unknown keys in a
  submitted spec are silently accepted and dropped rather than rejected. Not an
  execution risk in itself; it is a weak boundary for a document that is
  supposed to be the *only* thing the model controls.
  — **STATICALLY VERIFIED** (`spec.py`: no `model_config`).

---

## 11. Configuration

* Seven environment variables, all documented in `.env.example`:
  `AI3D_PRINTER_PROFILE`, `AI3D_DATA_DIR`, `AI3D_OPENSCAD_BIN`,
  `AI3D_CURAENGINE_BIN`, `AI3D_CURA_DEFINITION`, `AI3D_HOST`, `AI3D_PORT`,
  `AI3D_STRICT_GCODE`. — **STATICALLY VERIFIED**.
* No `.env` loader is present. `os.getenv` is read directly, so `.env` is
  documentation only unless the operator exports it themselves — the README's
  `cp .env.example .env` step has no effect on the running process.
  — **STATICALLY VERIFIED** (no `dotenv` import anywhere).
* Two defaults are relative paths (see §8). — **STATICALLY VERIFIED**.
* `profiles/elegoo_neptune_3_plus.json` correctly separates
  `verified_machine_limits` from `process_defaults_unverified`, and carries the
  note "Conservative app defaults; not manufacturer precision guarantees."
  This separation is the best single design decision in the pack.
  — **STATICALLY VERIFIED**; the loader reads only the verified group, so the
  two never merge — **REAL IMPLEMENTED** (`profile.py:23-33`).
* `profiles/material_defaults.json` is prefixed with an explicit warning that
  the values are starting presets requiring calibration.
  — **STATICALLY VERIFIED**.

---

## 12. Secrets

* **No secrets of any kind in the pack** — no API keys, no tokens, no
  credentials, no private URLs, in code, config or examples.
  — **REAL TESTED** (grep for `key|token|secret|password|api_key` over the
  whole pack: only the word "key" in unrelated contexts).
* The HTTP API has **no authentication and no authorisation**. Any local
  process can `POST /api/precision/build` and cause file writes under
  `AI3D_DATA_DIR` and subprocess execution of OpenSCAD. Mitigated only by the
  `127.0.0.1` default bind. — **STATICALLY VERIFIED** (`api.py`; no dependency,
  no middleware).

---

## 13. Mock code

* **No mocks, no fixtures posing as real data, no fabricated vendor files.** The
  pack is disciplined here: `docs/SLICER_INTEGRATION.md` explicitly forbids
  shipping a fabricated ELEGOO Cura definition, and none is shipped.
  — **STATICALLY VERIFIED**.
* What the pack *does* have is worse than a mock in one place: a **verdict that
  is not backed by a check** (§16, D-1). A mock at least announces itself.
  — **REAL TESTED**.

---

## 14. Unfinished code

* `slicer.py` — complete function, wired to nothing. — **REAL TESTED**.
* `tolerance.py` — complete module, imported by nothing. — **REAL TESTED**.
* `gcode.py` — reachable only from one HTTP route, never from the pipeline.
  — **REAL TESTED**.
* `cadquery_engine.py` — reachable, but untested and containing a semantic bug
  (§16, D-6). — **STATICALLY VERIFIED**.
* Mode B generative 3D — documentation only. — **STATICALLY VERIFIED**.
* `app.manifest.yaml` `commands:` — four declared, zero implemented.
  — **STATICALLY VERIFIED**.
* `requirements.py` returns the generic status `NEEDS_CLARIFICATION_OR_PROCESS_CHANGE`
  even when the cause is specifically a tolerance tighter than the calibrated
  capability; `MASTER_PROMPT_FOR_CLAUDE.md` requires
  `NEEDS_CALIBRATION_OR_DIFFERENT_PROCESS` for that case.
  — **STATICALLY VERIFIED** (`requirements.py:26` vs master prompt "Precision
  policy").

---

## 15. Demo code

* `examples/bracket.design.json` — a valid, sensible DesignSpec (60×30×8 mm
  block, two Ø4 through holes). It parses and compiles.
  — **REAL TESTED** (loaded and compiled during this audit).
* `examples/money_clip.design.json` — honest about being incomplete: it carries
  `assumptions: ["Seed only; spring geometry must be designed before printing."]`
  and `unresolved_questions`, which the requirement gate will correctly block
  on. This is demo code that refuses to pretend. — **REAL TESTED** (the gate
  returns `ready=False` for it).
* No demo server, no seeded database, no sample outputs committed.
  — **STATICALLY VERIFIED**.

---

## 16. Security and correctness defects

Ordered by severity. Each carries its own evidence level.

### D-1 — `PASS` is awarded for a file that is not a mesh — **REAL TESTED**

`pipeline.build` sets `status = "PASS"` unless the STL is missing, the mesh
report says `FAILED`, or the bbox does not fit. `validate_mesh` returns
`{"status": "NOT_RUN"}` when trimesh is absent, which is not `FAILED`, and
`bbox` is then `None`, so the fit check is skipped entirely.

Probe (trimesh forced absent, a 42-byte junk file named `model.stl` pre-placed):

```
pipeline verdict: PASS
mesh section: {'status': 'NOT_RUN', 'reason': 'trimesh optional dependency unavailable', ...}
fit section: None
report says: Status: **PASS**
```

The pack writes a print report declaring `PASS` for a file that contains no
geometry. This is precisely the failure mode the whole product exists to
prevent. **Severity: critical.**

### D-2 — a non-watertight mesh is reported `PASS` — **REAL TESTED**

`mesh.validate_mesh` computes `watertight` and returns it in the payload, then
sets `"status": "PASS" if len(mesh.faces) > 0 else "FAILED"` — the watertight
result is reported and then ignored.

Probe (a single unclosed triangle):

```
validate_mesh(open triangle) -> {'status': 'PASS', 'faces': 1, 'watertight': False}
```

Component count and winding consistency are likewise computed and unused.
**Severity: critical.**

### D-3 — a corrupt STL crashes instead of being refused — **REAL TESTED**

`validate_mesh` calls `trimesh.load_mesh` with no exception handling.

```
validate_mesh(corrupt) RAISED TypeError 'NoneType' object is not iterable
```

Through the HTTP API this surfaces as an unhandled 500, not as an honest
refusal with a reason. **Severity: high.**

### D-4 — G-code temperature and EEPROM caps can be bypassed — **REAL TESTED**

`scan_gcode` takes the command as `code.split()[0]`, so a command written
without a space, or with a zero-padded number, does not match the lookup tables.

```
M104S300 -> PASS      (nozzle 300 °C, cap is 260 °C)
M0500    -> PASS      (EEPROM write, on the blocked list as M500)
m104 s300 -> FAILED   (lowercase is handled correctly)
```

Both forms are accepted by Marlin firmware. The scanner also does not strip
`( ... )` inline comments, and its blocked-command list omits `M92`
(steps-per-unit), `M851` (probe offset), `M301`/`M304` (PID) and `M999`.
**Severity: high — this is the last gate before machine control.**

### D-5 — fit check returns an arbitrary orientation — **REAL TESTED**

`check_bbox_fit` iterates `set(permutations(bbox, 3))`, an unordered
collection, and returns the first permutation that fits. For a model that
already fits as-is:

```
check_bbox_fit((10,20,30)) -> orientation_xyz = (10.0, 30.0, 20.0)
```

A caller that acted on `orientation_xyz` would rotate a part that needed no
rotation. The pack never acts on it, so today this is latent.
**Severity: medium.**

### D-6 — CadQuery cylinder doubles its height when centred — **STATICALLY VERIFIED**

`cadquery_engine.build` uses `cq.Workplane("XY").circle(d/2).extrude(h, both=p.center)`.
In CadQuery, `both=True` extrudes `h` in **each** direction, producing a solid
of height `2h`, whereas `center=True` in the spec means "height `h`, centred on
the origin". A centred cylinder would come out twice as tall — silently, and
only on the CadQuery path, which is also the STEP path.
**Severity: medium** (untestable here: CadQuery absent).

### D-7 — path sanitisation collapses instead of refusing — **REAL TESTED**

```
safe_job_id('../../etc/passwd') -> 'etcpasswd'
safe_job_id('a/b')              -> 'ab'
```

No traversal escapes, so this is not an exploit. But two distinct job ids can
silently collapse onto one directory and overwrite each other's artifacts, and
there is no post-join check that the resolved path is still inside the data
directory. **Severity: medium.**

### D-8 — no resource limits of any kind — **STATICALLY VERIFIED**

No job timeout (only a 60 s timeout on the OpenSCAD subprocess), no disk quota,
no upload size limit, no triangle-count limit, no bound on `DesignSpec.features`.
A spec with a million features, or a mesh that fills the disk, has nothing
standing in its way. **Severity: medium.**

### D-9 — unauthenticated API performs subprocess execution and file writes — **STATICALLY VERIFIED**

See §12. Mitigated by the loopback default bind, not by the code.
**Severity: medium.**

### D-10 — `POST /api/gcode/scan` takes an unvalidated `dict` — **STATICALLY VERIFIED**

`async def scan(payload: dict)` bypasses pydantic; the handler hand-checks one
key. Minor, but inconsistent with the rest of the API. **Severity: low.**

---

## 17. Missing tests

The pack ships 19 tests across 7 files; all 19 pass. — **REAL TESTED**.

What they cover: machine limits (1), spec validation and SCAD compile (2),
requirement gate (3), bbox fit and wall warning (4), G-code caps and EEPROM (5),
tolerance arithmetic (2), job-id sanitisation (2).

What is **not** covered anywhere — **STATICALLY VERIFIED** by reading all seven
test files:

* mesh validation of any kind — no test loads a mesh, and `mesh.py` has **zero**
  test coverage. The module that decides printability is entirely untested;
* corrupt / truncated / empty / NaN STL handling;
* the pipeline itself — `pipeline.py` has zero test coverage, end to end or
  otherwise;
* determinism of export (identical input → identical bytes);
* artifact manifest and checksum correctness — listed as a required acceptance
  gate in `MASTER_PROMPT_FOR_CLAUDE.md`, and absent;
* `artifacts.py`, `slicer.py`, `main.py`, `cadquery_engine.py` — zero coverage;
* the G-code bypasses of D-4 (the existing scanner tests use only well-formed,
  space-separated commands, which is why the bypasses survived);
* the API surface — `test_api_helper.py` tests one helper function, not a route;
* anything about physical safety, because there is nothing to test;
* units, scale, orientation application, bed placement — no such code exists.

The `MASTER_PROMPT` acceptance list names fourteen focused tests. Six are
genuinely present; the rest are absent, and two of the named ones ("artifact
manifest", "minimum wall warning" beyond the pure-function case) are asserted
by no test at all.

---

## 18. Resource requirements

* Core (fastapi + uvicorn + pydantic): tens of MB, negligible CPU.
  — **STATICALLY VERIFIED**.
* `mesh` extra (trimesh + numpy): ~50 MB. — **REAL TESTED** (trimesh 5.0.0
  installed during this session: a 745 kB wheel on top of numpy 2.4.6).
* `cad` extra (cadquery/OCP): ~1 GB installed, minutes to install.
  — **NOT TESTED** (deliberately not installed).
* OpenSCAD: external binary, ~100 MB. CuraEngine: external, part of an ELEGOO
  Cura install. — **NOT TESTED** (neither present).
* Memory scales with triangle count and is unbounded — see D-8.
  — **STATICALLY VERIFIED**.
* Physical print time, filament use and power draw — **BLOCKED BY HARDWARE**.

---

## 19. BOSSMAN integration points

* The pack imports nothing from `bcc.*` or any BOSSMAN package.
  — **REAL TESTED** (grep over `src/`: zero matches for `bcc`, `bossman`,
  `command_center`).
* `docs/BOSSMAN_FUTURE_BRIDGE.md` and `app.manifest.yaml` describe the intended
  bridge: four `/3d ...` commands, a permission table, and an autonomous
  spec→CAD→validate→revise loop with `printer.start_physical_print` denied.
  None of it is implemented. — **STATICALLY VERIFIED**.
* `app.manifest.yaml` declares `entrypoints: {standalone: true, bossman_app:
  future, command: future, autonomous: future}` — an honest statement of what
  is and is not ready. — **STATICALLY VERIFIED**.
* Integration surface the pack actually offers a control plane: three HTTP
  routes, none of which is `health`, `capabilities`, `metrics` or any `jobs.*`
  operation. A control plane cannot supervise this app as delivered — it cannot
  ask whether it is alive, what it can do, or what a job is doing.
  — **STATICALLY VERIFIED**.
* The permission table (`cad.execute_arbitrary_code: deny`,
  `printer.start_physical_print: deny`) is declaration only; nothing in the code
  reads or enforces `app.manifest.yaml`. — **STATICALLY VERIFIED**.

---

## 20. Verdict

**What the pack gets right.** The printer profile's separation of verified
manufacturer limits from unverified app defaults; the refusal to fabricate a
vendor slicer definition; the constrained DesignSpec DSL instead of executing
model-authored Python; the absence of any hardware path; the requirement gate;
demo data that admits its own incompleteness. The documentation is unusually
candid — `PRECISION_PIPELINE.md`'s "Never promise CAD nominal size equals
printed measured size" is exactly the right instinct.

**What it gets wrong.** The product's one job is to not call something printable
when it is not, and the shipped verdict logic does exactly that: `PASS` is
awarded for a 42-byte junk file (D-1) and for an open, unclosed surface (D-2),
both demonstrated by execution. The two stages that would catch this — the
slicer and the G-code scanner — are written but wired to nothing. The G-code
scanner, when reached, has two trivially-triggered bypasses of its own (D-4).
`mesh.py` and `pipeline.py`, the two modules that decide printability, have zero
test coverage between them.

**Assessment: a good skeleton with a hollow core.** The scaffolding, the
profile discipline and the documentation are worth keeping. The validation
verdict, the mesh layer, the G-code scanner and the entire absence of a runtime
(jobs, limits, history, control surface) had to be built rather than adapted.

---

## 21. Disposition in `apps/ai-3d-maker/`

For traceability, how each finding was handled in the application built from
this pack. Every entry here is **REAL TESTED** by the new app's own suite
(261 passed, 1 skipped) unless marked otherwise.

| Finding | Disposition |
|---|---|
| D-1 PASS on file existence | Fixed. `printability.decide_printability` is the only thing that may set `printable`, and it requires watertight + manifold + consistent winding + positive volume + fits. A rejected model is written as `model.rejected.stl`, never `model.stl`. |
| D-2 non-watertight passes | Fixed. Topology checks are computed in-package (`meshcheck.py`) and are gating, not decorative. |
| D-3 corrupt STL crashes | Fixed. `mesh.load_stl` raises `MeshLoadError` for truncated, empty, garbage, NaN, non-numeric and unterminated input; 8 tests cover it. |
| D-4 G-code bypasses | Fixed. Commands are normalised by regex (`M104S300`, `M0500`, lowercase all caught); `( )` comments stripped; blocked list extended with M92/M301/M304/M503/M851/M999. |
| D-5 arbitrary orientation | Fixed. Identity orientation is tried first and permutations are sorted; the chosen permutation is actually applied to the mesh. |
| D-6 CadQuery `both=` | **Carried over, unfixed.** The optional CadQuery path retains the pack's `both=p.center`. It cannot be exercised here (CadQuery absent) and fixing geometry I cannot test would be guessing. Recorded as a known defect. — **NOT TESTED** |
| D-7 path collapse | Fixed. `paths.strict_segment` refuses separators and `..` outright instead of rewriting them; `resolve_within` re-checks the resolved path against the sandbox root, symlinks included. |
| D-8 no resource limits | Fixed. Per-job timeout with an explicit deadline check between stages, per-job and total disk quotas, upload size cap, triangle cap, feature cap, job retention limit. |
| D-9 unauthenticated API | **Carried over.** The HTTP surface still has no auth; it binds loopback by default. Authentication is a control-plane concern and was left to BOSSMAN. — **STATICALLY VERIFIED** |
| D-10 unvalidated dict body | Fixed. All routes take pydantic models. |
| Slicer/scanner unwired | Fixed. Both are pipeline stages; slicing reports `NOT_AVAILABLE` with a reason on this host rather than being skipped silently. |
| No physical boundary | Built. `printer.execute_physical` is the sole funnel to hardware and requires a real transport **and** an environment flag **and** a confirmation token bound to the artifact's sha256; unsafe G-code is refused ahead of all three. Exercised through the simulator only — physical behaviour is **BLOCKED BY HARDWARE**. |
| No control surface | Built. `health`, `capabilities`, `jobs.create/status/cancel/list`, `artifacts.list`, `metrics`, `gcode.scan`, `printer.confirm`. |
| Mode B generative 3D | **Not built.** Still documentation only. No generator vendor is available here and inventing an adapter with nothing behind it would be the pack's own mistake repeated. — **NOT TESTED** |
| No AI/LLM path | **Not built.** DesignSpec still arrives from outside. The app is the deterministic half; the model-facing half is a separate piece of work. — **STATICALLY VERIFIED** |
| Real slicer run | **NOT TESTED** — no CuraEngine or PrusaSlicer on this host. |
| STEP export | **NOT TESTED** — no CadQuery on this host. |
| Real print on a Neptune 3 Plus | **BLOCKED BY HARDWARE.** The smoke test exists in the suite and is permanently skipped; it is never allowed to report a pass. |
