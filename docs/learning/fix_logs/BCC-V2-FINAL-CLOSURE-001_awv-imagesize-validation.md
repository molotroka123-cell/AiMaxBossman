# Learning Case: BCC-V2-FINAL-CLOSURE-001/awv-imagesize-validation

## Metadata
MODEL: claude-opus-5
AGENT: fable-lead
START_SHA: 023148d
END_SHA: fc09a1cd0063898c4ded3bf677524652bda89b91
LEARNING_STATUS: VERIFIED
OUTCOME: FIXED
VERIFIED_BY: tool:pytest
CONFIDENCE: 0.85
TAGS: {"domain": "correctness", "bug_class": "type_invariant", "component": "ai-webcam-vision.lowres_detection", "severity": "LOW"}
FINDINGS: AWV-IMAGESIZE-TYPE, AWV-ODD-LETTERBOX-UNTESTED

## Task
AWV letterbox geometry: ImageSize accepted values that are not whole pixel counts

## Symptom
No failing test and no report. ImageSize guarded only magnitude (width < 1 or height < 1), so ImageSize(True, 10), ImageSize(320.0, 180) and ImageSize(nan, 180) all constructed successfully and would silently distort the letterbox scale.

## Reproduction
- Construct ImageSize with bool, integral float, NaN or inf: all succeed on the pre-fix code. bool passes because it subclasses int; NaN passes because every comparison against NaN is False.

## Evidence
- pre-fix: ImageSize(True, 10) and ImageSize(nan, 180) construct without error
- post-fix: parametrised rejection test passes for bool, integral float, NaN and inf
- 1920x1079 -> 641x385 gives pad_top 12.5; a box round-trips to within 1 px and stays inside the source
- 640x480 -> 320x180 pads on the left (pad_left 40, pad_top 0); a padding-only box maps back to None

## Hypotheses considered
- value-only validation cannot express a type invariant (confirmed)

## Rejected hypotheses + why
- isfinite on the values is enough - rejected: admits 320.0 and does nothing about bool
- coerce with int(value) - rejected: hides the upstream bug and yields a plausible but wrong scale
- validate inside LetterboxTransform.fit instead - rejected: the invalid object would still exist and could be passed elsewhere
- leave it because nothing calls the module yet - rejected: an unwired module is the cheapest moment to fix an invariant

## Root cause
The guard checked the value and never the type. bool is a subclass of int so it satisfies an int contract as 0/1, and NaN/inf are floats that compare False against every bound, so a comparison-based guard cannot reject them. A pixel dimension is by definition a whole count; a float there usually signals an unfinished division upstream.

## Relevant code paths
- apps/ai-webcam-vision/src/ai_webcam_vision/pipeline/lowres_detection.py::ImageSize.__post_init__
- apps/ai-webcam-vision/src/ai_webcam_vision/pipeline/lowres_detection.py::LetterboxTransform.fit

## Fix strategy
Reject non-integer dimensions at construction, excluding bool explicitly (isinstance(value, bool) or not isinstance(value, int) -> TypeError naming the field). Rejecting float covers NaN and infinity. The >= 1 magnitude guard is kept. Verified that LetterboxTransform.fit builds sizes via max(1, round(...)), which returns int, so no internal call site breaks.

## Alternatives considered
- runtime coercion to int
- isfinite-only guard
- validation at the point of use

## Why this fix was chosen
The type owns its invariant, so an invalid ImageSize can never exist; refusing rather than coercing surfaces the caller's bug where it is cheap to find instead of producing a box drawn in the wrong place.

## Files changed
- apps/ai-webcam-vision/src/ai_webcam_vision/pipeline/lowres_detection.py
- apps/ai-webcam-vision/tests/test_lowres_detection.py

## Tests added
- test_non_integer_image_sizes_are_rejected
- test_odd_letterbox_keeps_box_within_source_and_round_trips
- test_box_covering_only_padding_is_not_reported_as_an_object

## Original reproduction after fix
invalid ImageSize instances constructed without error; no downstream assertion would catch the distorted scale

## Adversarial variants
- bool in either position and in both orders
- integral float 320.0 that an isfinite guard would accept
- NaN and infinity, which defeat comparison-based guards
- odd source and inference dimensions producing fractional padding in one axis (1920x1079 -> 641x385, pad_top 12.5)
- detection box lying entirely in letterbox padding must map back to None, not to a phantom object

## Regression
AWV suite 224 passed (was 216, +8)

## Fresh external verification
pytest on the AWV package; grep confirms the package re-export is still the module's only call site, so runtime behaviour is unchanged

## Generalizable lessons
- isinstance(x, int) is True for bool; any integer contract that matters must exclude bool explicitly.
- A comparison-based guard cannot reject NaN because every comparison against NaN is False: check the type first, then the range.
- Validate at construction in the type that owns the invariant, so the invalid object never exists.
- Refuse rather than coerce when the wrong type signals a caller bug.
- Geometry tested only on even, evenly-divisible dimensions is under-tested: odd sizes, fractional padding and map-to-nothing regions are the interesting cases.

## Teach local model
- bool is a subclass of int in Python - exclude it explicitly in integer validation
- NaN defeats every comparison guard; type check precedes range check
- letterbox geometry must be tested with fractional padding and with boxes covering only padding

## Limitations / follow-up
- Pure geometry only: no model, capture, opencv/torch/ultralytics, network or dependency was added, and nothing in the runtime calls this module yet.
