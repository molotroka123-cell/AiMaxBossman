# Bossman earn-run 2026-10-07 (local LLM only)

Task: "find how this PC can earn money with its current hardware", with father-style hints.
Harness: `tools/earn_research.py`. Model: `bossman-fast-qwen36-35b-a3b-q5` via Ollama (think=false, tools field). No cloud LLM did any thinking.
Web: Tavily (primary, 18 of 60 credits, added mid-task on coordinator message), Serper via local search_proxy :8850 (fallback, no uid so no quota), polite GET for pages. Jina Reader key was offered but unused (raw HTML strip used). Apify: $0.00 (model never asked).

## Timeline
- 30 steps, 323 s total, 166,854 prompt tokens / 2,534 completion tokens.
- Two harness bugs fixed first (history echoed as "CALL:" text confused the model into not emitting tool calls; fixed with real tool_calls/tool messages plus a text-call fallback parser).
- Steps 1-13: GPU-rental marketplaces (Salad, Vast.ai). 14-19: Fiverr AI art, ComfyUI/ROCm, 3D printing, Telegram bots. 20-29: notes, repeats (it duplicated the same note twice). Step 30 it was still searching, so the harness forced a final plan without tools (the model never called finish by itself).
- Full transcript: `C:\Users\asd\Bossman\bugtest-20261001\tree-1005\earn-run-20261007\transcript.json` (+ fetched_pages.json).

## Queries the model chose (18)
GPU rental marketplace AMD ROCm; Salad AMD ROCm (x3); Vast.ai AMD ROCm support/hosting/earnings (x4); AI image service Fiverr/Upwork rates; ComfyUI ROCm sell; 3D printing Fiverr/Etsy Elegoo; Telegram bot monetization (x2); ComfyUI Windows VRAM unified memory (x2); Fiverr price per image; Ollama API monetization.

## Pages read (9)
gpunex rent-out-your-gpu; Salad compatibility FAQ (twice); Vast.ai AMD press release (2024); Vast.ai host earnings article; ROCm docs ComfyUI; AMD x ComfyUI blog 2026; Fiverr AI-artist cost guide; ROCm blog "Local Image and Video Generation on Ryzen AI Max+ (Windows)".

## Grounding score (own check against fetched text)
Numeric claims in PLAN.md: about 9. Supported: 3 (ROCm 7.2.1 / gfx1151 ComfyUI on Windows, ~94 GB GPU-addressable memory, Ryzen AI Max+ support). Unsupported/hallucinated: 6 income ranges ($200-800, $100-500, $100-400, $50-300, $0-50, $0-100) plus "$15-35/hr AI artists" with an Upwork URL never fetched (the fetched Fiverr guide says $49-96/hr for image generation, $35-50 for video art). Score about 3/10 supported = 33%; 7 hallucinated numbers.
Misreadings: Salad page says it supports "more recent dedicated AMD GPUs" for AI workloads (plan said AMD only for mining; an iGPU is not dedicated so the conclusion is still plausible); the fetched Vast.ai press release says Vast was the first marketplace with AMD support (plan called it limited/undocumented). Three rows cite "Local Ollama setup"/"Local Hardware" instead of a URL.

## Haiku 5.5 audit (OpenRouter, cost $0.0017; saved as haiku_audit.md, output truncated at the token limit)
Verdict: mostly not grounded; income figures invented; ComfyUI/ROCm row is the only real evidence; plan contradicts its own Vast.ai source; "no GPU needed" for Ollama wrong; 3D-print "speed advantage" false; missing payout eligibility for Russia-based sellers (Upwork/Fiverr/Etsy), electricity/fees/filament costs, licensing risk; rejecting GPU rental was on weak grounds. (Its "94 vs 122 GB" objection is wrong: 94 GB is the BIOS-configurable GPU share, from the AMD page.)

## Growing-up score: 5/10
+ Understood Bossman's purpose and the AMD trap: it searched Salad/Vast for AMD support first and rejected NVIDIA-centric rental, found the real AMD Strix Halo ComfyUI source, listed rejected fraud options, followed the step plan, 18 sane queries, correct tool use after the fix.
+ Chose options matching the owner's assets (ComfyUI, Telegram bot, 3D printer).
- Invented all income numbers and one source URL; ignored own fetched rates.
- Never finished on its own, repeated queries/notes, shallow (9 pages, only 1 per option), no payout/geo/cost check, did not invent anything non-obvious (video/music studio, docling/whisper transcription services, Russian-language niches missing).
