# Open-source choices (checked 2026-10-06 from this sandbox; PyPI JSON + raw LICENSE files)

The GitHub API for these repositories was not reachable from the session (repository scope), so versions/licences come from PyPI and
`raw.githubusercontent.com`. Hugging Face is blocked by the egress policy, so **no model weights could be downloaded**.

| project | version seen | licence (code) | weights | used? | why / measurement |
|---|---|---|---|---|---|
| python-mss | 10.2.0 (PyPI) | MIT | – | optional (`ScreenWindowSource`) | Windows/AMD-agnostic (pure capture). **NOT_RUN here** (no display). Observation of an owner-selected window only. |
| OpenCV (opencv-python-headless) | 5.0.0.93 | Apache-2.0 | – | **yes** | contours/threshold/morphology; CPU only; already in the stack. |
| PaddleOCR / paddlepaddle | 3.7.0 / 3.3.1 | Apache-2.0 | downloaded at first use (blocked here) | **no (NOT_RUN)** | not installed: weights unreachable; large dependency. Replaced for measurement by RapidOCR (below). |
| RapidOCR-onnxruntime (PP-OCR models bundled in the wheel) | pip current | Apache-2.0 | bundled | comparator only | measured on the same crops: `evidence/ocr_comparator.json`. Generic OCR did not beat the template reader on safety (see report) and costs ~10-40x latency on CPU; kept as an optional fallback idea, not wired in. |
| OmniParser | repo LICENSE file reads *CC-BY-4.0*; README: `icon_detect_v3` MIT-based YOLOv9, older detectors AGPL, captioners MIT | see left | Hugging Face (blocked) | **no (NOT_RUN)** | needs torch + weights; only relevant for unknown layouts. Large model is reserved for ambiguous frames of *unknown* UIs, never per frame. Mind the AGPL note on older weights. |
| PokerKit | 0.7.6 | MIT | – | **yes (oracle + PHH)** | BotLab evaluator vs PokerKit on 20,000 random showdowns: 20,000 agree; PHH dump/load round-trip equal (`evidence/rules_check.json`). |
| phh-std | spec repo | MIT | – | format only | PHH written/validated via PokerKit's `HandHistory`; vision histories are *incomplete* and flagged so. |
| OpenSpiel | 2.0.2 | Apache-2.0 | – | **no** | not installed/measured. Decision by scope: BotLab already is the project's NLHE environment (seeded, duplicate deals, CI, loopback adapter); OpenSpiel's strength is exact exploitability on *small* games, which does not apply to NLHE. |
| RLCard | 1.2.0 | MIT | – | **no** | not installed/measured; same reasoning (would be a second environment). |

GPU/AMD: everything above runs on CPU. **No AMD acceleration was enabled or measured** (NOT_RUN); nothing in the pipeline needs it
(p50 ≈ 60 ms/frame at 520×900, ≈ 150 ms at 1170×2532).
