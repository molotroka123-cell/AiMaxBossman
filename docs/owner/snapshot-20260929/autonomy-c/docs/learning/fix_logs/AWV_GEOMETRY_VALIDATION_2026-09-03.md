# Learning Case: AWV_GEOMETRY_VALIDATION_2026-09-03

## Metadata

- date: 2026-09-03
- area: `apps/ai-webcam-vision` — pure letterbox geometry, no runtime call site
- code-fix commit: `fc09a1c`
- files: `apps/ai-webcam-vision/src/ai_webcam_vision/pipeline/lowres_detection.py`,
  `apps/ai-webcam-vision/tests/test_lowres_detection.py`
- no model, capture, network or dependency added; the package re-export
  remains the module's only call site, so runtime behaviour is unchanged

## Symptom

No failing test and no user report. The defect was found by reading the
validation of a value type rather than by observing a failure — which is
exactly why it mattered: the damage it can cause is silent.

`ImageSize` guarded only magnitude:

```python
if self.width < 1 or self.height < 1:
    raise ValueError(...)
```

so `ImageSize(True, 10)`, `ImageSize(320.0, 180)` and
`ImageSize(float("nan"), 180)` were all accepted.

## Reproducible evidence

- `ImageSize(True, 10)` constructed without error: `bool` is a subclass of
  `int`, and `True < 1` is `False`.
- `ImageSize(float("nan"), 180)` constructed without error: every comparison
  with NaN is `False`, so the magnitude guard passes.
- Both then flow into `LetterboxTransform.fit`, where they become `scale_x`,
  `scale_y`, `pad_left` and `pad_top`. A NaN scale propagates into every mapped
  coordinate; a `True` width of 1 pixel produces a scale that is wrong by
  orders of magnitude.
- The geometry has no assertion downstream: the only visible consequence would
  be a bounding box drawn in the wrong place on a frame.

## Root cause

The guard checked the *value* and never the *type*. Two Python facts defeat a
value-only check:

1. `bool` is a subclass of `int`, so a boolean silently satisfies an `int`
   contract and behaves as 0 or 1.
2. NaN and infinity are floats that compare `False` against every bound, so any
   guard written as a comparison lets them through.

A pixel dimension is by definition a whole count. A float arriving there almost
always means an unfinished division somewhere upstream.

## Rejected hypotheses

- **"`isfinite` on the values is enough."** Rejected: it admits `320.0`, which
  is finite and still not a pixel count, and it does nothing about `bool`.
- **"Clamp or coerce with `int(value)`."** Rejected: coercion hides the
  upstream mistake and produces a plausible-looking but wrong scale. Refusing
  at construction surfaces the caller's bug where it is cheap to find.
- **"Validate in `LetterboxTransform.fit` instead."** Rejected: the invalid
  object would still exist and could be passed anywhere else. The type owns its
  own invariant.
- **"Leave it — nothing calls this module yet."** Rejected: an unwired module is
  the cheapest possible moment to fix an invariant, and the check costs nothing.

## Minimal fix

Reject non-integer dimensions at construction, naming the offending field:

```python
for name, value in (("width", self.width), ("height", self.height)):
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"image {name} must be an int number of pixels, ...")
```

`bool` is excluded explicitly because the `isinstance(value, int)` test would
otherwise accept it. Rejecting `float` covers NaN and infinity without a
separate check. The existing `>= 1` magnitude guard is kept.

Internal construction was verified compatible: `LetterboxTransform.fit` builds
its scaled size with `max(1, round(...))`, and `round()` on a float returns an
`int`, so no internal call site is broken.

## Regression tests

- `test_non_integer_image_sizes_are_rejected` — parametrised over
  `(True, 10)`, `(10, False)`, `(320.0, 180)`, `(320, 180.0)`,
  `(nan, 180)`, `(320, inf)`; each must raise `TypeError`.
- `test_odd_letterbox_keeps_box_within_source_and_round_trips` — the geometry
  had only ever been exercised on even dimensions where the padding is a whole
  number. `1920x1079 -> 641x385` gives `pad_top = 12.5`; a box maps forward
  inside the inference canvas and back to within 1 px of the original, still
  inside the source bounds.
- `test_box_covering_only_padding_is_not_reported_as_an_object` — for
  `640x480 -> 320x180` the padding is on the **left** (`pad_left = 40`,
  `pad_top = 0`); a box covering only that region must map back to `None`, not
  to a phantom object at the frame edge.

Result: AWV suite 224 passed (was 216; +8).

## Adversarial variants

- `bool` in either position, in both orders.
- `float` that is exactly integral (`320.0`) — accepted by a naive
  `isfinite` guard, rejected here.
- NaN and infinity, which defeat every comparison-based guard.
- Odd source and odd inference dimensions together, producing fractional
  padding in one axis only.
- A detection box lying entirely in the letterbox padding, which must not
  become an object event.

## Lessons for the local model

- In Python, `isinstance(x, int)` is `True` for `bool`. Any integer contract
  that matters must exclude `bool` explicitly.
- A guard written as a comparison (`x < 1`) cannot reject NaN, because every
  comparison against NaN is `False`. Check the type first, then the range.
- Validate at construction, in the type that owns the invariant, rather than at
  the point of use — the invalid object should never exist.
- Refuse rather than coerce when the wrong type signals a caller bug. Coercion
  produces a plausible wrong answer; refusal produces a traceback at the real
  fault.
- Geometry tested only on even, evenly-divisible dimensions is under-tested.
  The interesting cases are odd sizes, fractional padding, and regions that map
  to nothing at all.
- A module nothing calls yet is the cheapest place to correct an invariant; the
  absence of a caller is a reason to fix it now, not a reason to defer.
