# Motion Studio: open-source registry (licenses checked 2026-09-28)

Licenses were read from the package registries (npm, PyPI) or the upstream LICENSE file, not from memory.
Before bundling anything new, check again: licenses change (for example, Remotion 5 announces changes).

## Integrated in this branch

| Project | License | What it gives | Status |
|---|---|---|---|
| lottie-web 5.13.0 | MIT | Draws any Lottie (After Effects) animation into the engine frame by frame, deterministically | ✅ vendored; 100/100 catalog animations render |
| Noto Emoji Animation (Google) | CC BY 4.0 | 100 animated emoji: stickers and card icons | ✅ catalog with sha256 pins; attribution required |
| kokoro-onnx + Kokoro-82M | MIT + Apache-2.0 | Local English voice-over | ✅ in use |

## Recommended next (license allows bundling)

| Project | License | Why | Status |
|---|---|---|---|
| Revideo (`@revideo/core` 0.11) | MIT | Engine v2: Motion Canvas scenes with a headless render API. The same "spec → template" split fits | NOT_TESTED |
| Motion Canvas (`@motion-canvas/core` 3.17) | MIT | Code-driven animation library that Revideo builds on | NOT_TESTED |
| dotLottie web player (`@lottiefiles/dotlottie-web`) | MIT | Faster Lottie renderer and the `.lottie` format | NOT_TESTED |
| anime.js 4.5 | MIT | Timelines and easings for new scene types | NOT_TESTED |
| Theatre.js core 0.7 | Apache-2.0 | Keyframe editor so the owner can adjust a template by hand | NOT_TESTED |
| Manim (Community) | MIT | Formula, graph and diagram animation scenes | NOT_TESTED |
| ACE-Step | Apache-2.0 | Local music generation to replace the synthesized score | NOT_TESTED (GPU-heavy; ROCm unknown) |

## Rejected or restricted (do not bundle)

| Project | Why |
|---|---|
| useAnimations icons | The "CC BY" label comes with a ban on redistribution inside software products |
| piper-tts 1.8 | GPL-3.0-or-later: bundling would force GPL on the product. A separate tool the user installs is fine |
| heretic-llm 1.4 | AGPL-3.0: fine for a private experiment, not inside the product |
| Remotion | Its own license, paid for companies above a size limit, changing in v5 |
| GSAP 3.15 | "Standard no-charge license", not OSI; allowed with conditions, so prefer anime.js |
| MusicGen / Stable Audio Open | Non-commercial or restricted weights, not for monetized videos |

## How we test "100 ready-made"

`python tools/motion_studio/lottie_assets.py fetch` downloads and sha256-verifies every animation.
`python tools/motion_studio/verify_lottie.py --out DIR` then renders 8 frames of each through the engine. An animation passes if it has visible pixels in at least one frame and actually moves. Result on 2026-09-28: **100/100 pass, 0 page errors**; contact sheet in DIR.
