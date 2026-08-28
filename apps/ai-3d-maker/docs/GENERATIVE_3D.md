# Generative 3D (Mode B) — NOT IMPLEMENTED

**Status: not built. No adapter, no interface, no stub.**

This file records the intended design so that whoever builds it does not have
to rediscover the constraints. It does not describe working code.

## Intent

For figurines, statues, decorative and organic shapes, where a parametric CAD
representation is not the natural one.

Any local or cloud text/image-to-3D engine would implement:

```
generate(prompt | images) -> mesh + metadata
```

The app must not hard-depend on one vendor.

## Non-negotiable

Whatever the generator produces enters the **same** pipeline as everything else:
inspect → repair → units/scale/orient → printability verdict → export →
optional slice → G-code scan. It gets no exemption.

A beautiful render is not evidence of a printable mesh. Generated meshes are in
fact the most likely source of open surfaces, inverted normals, non-manifold
edges and floating components — precisely what `meshcheck.inspect_mesh` and
`printability.decide_printability` exist to catch.

## Why it was not built here

No 3D-generation vendor is reachable from this host, and writing an adapter with
nothing behind it would produce exactly the kind of unbacked capability this
codebase is meant to avoid. The mesh side of Mode B — validation, repair,
orientation, printability — is fully built and tested; only the generator
adapter is missing.

## How it is reported

Not by silence. Every job result carries `evidence.generative_engine` with
status `NOT_RUN` and the reason, so a reader sees an answered question rather
than a missing row. The interface that is ready is the ordinary mesh path: a
mesh from any source enters through `kind: "import"` and is validated with no
exemption. `test_pipeline.py` exercises that with the two failures generated
meshes actually produce — an open surface and inverted normals — and both are
refused, exactly as a CAD mesh would be.

There is no stub adapter, deliberately. A function that returns
`NOT_AVAILABLE` for every vendor would add a module without adding a
capability, and the ledger already says the same thing without the pretence.
