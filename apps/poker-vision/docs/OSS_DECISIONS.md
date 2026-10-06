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

## Addendum 2026-10-06 — TexasSolver, DecisionHoldem (from the owner's table), not run
Facts come from GitHub pages and search snippets (arxiv, openi.pcl.ac.cn, readthedocs were blocked from the build container); gaps are UNVERIFIED. **Nothing here was installed, run or timed.**

| item | licence | what it is | Windows | role for Bossman | status |
|---|---|---|---|---|---|
| PokerKit 0.7.6 | MIT | rules/state/evaluation library, no solver | pure Python | rules oracle, PHH — **used** (20 000/20 000 showdowns agree) | PASS |
| OpenSpiel 2.0.2 | Apache-2.0 | generic game framework; `universal_poker` (ACPC-style NLHE); full NLHE is too large for tabular CFR (own knowledge, docs blocked) | mainly Linux/macOS | comparison of strategies in small/abstracted games | NOT_RUN |
| TexasSolver (`bupticybee/TexasSolver`) | **AGPL-3.0** (LICENSE file confirmed; network copyleft; README mentions a separate licensed list — terms UNVERIFIED) | postflop solver for given ranges + bet tree, heads-up IP vs OOP; CLI `console_solver -i input.txt`, JSON export; CPU multithreaded; a separate GPU project exists; v0.2.0 adds a GUI (release year UNVERIFIED) | prebuilt Windows/macOS/Linux releases (asset sizes UNVERIFIED) | offline reference strategies for fixed postflop spots (not live, not preflop, not multiway) | NOT_RUN |
| DecisionHoldem | UNVERIFIED (repo not at `bupticybee/DecisionHoldem`; appears at openi.pcl.ac.cn/chenhao/DecisionHoldem) | heads-up NL hold'em agent: abstraction blueprint (linear CFR) + depth-limited subgame solving; reported >730 mbb/h vs Slumbot (paper, snippet only) | UNVERIFIED | heads-up research only; no multiway/multi-table by design | NOT_RUN |

Time/RAM: only one number exists (TexasSolver README: 1600 MB RAM, 172 s on an unstated spot vs PioSolver 492 MB, 242 s) — not comparable and not measured by us. A fair comparison needs the same spots, the same machine, licence review of the exact repositories, and an AGPL decision (do not link/ship TexasSolver code inside Bossman; calling its binary as an external tool on the owner's PC is a separate legal question for the owner).
