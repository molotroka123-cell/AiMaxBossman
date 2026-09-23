# SwapMe — 15-second Creative Training Run

Date: 2026-09-23
Status: TRAINING / CREATIVE WORKFLOW
Purpose: teach Bossman/local models a reproducible creative pipeline while independent 1.0 closure tests continue.

## Owner brief
Create one 15-second SwapMe crypto-exchange ad:
- 0–5 s: SeeDance 2.5, 720p
- 5–10 s: SeeDance 2.5, 720p
- 10–15 s: local video generation/rendering
- exactly two SeeDance jobs, max 5 seconds each
- preserve one consistent fox mascot and SwapMe identity
- assemble, full-decode-check and export the final video
- send final result to configured owner Telegram
- send progress to Telegram every 15 minutes while this run is active
- log prompts, provider/model, settings, job IDs/seeds if exposed, timings, costs, outputs, hashes, failures and retries

This is a side-run. It MUST NOT mutate, interrupt, reconfigure, or invalidate ongoing Bossman 1.0 owner/security/CI tests. Schedule heavy local GPU work so it does not contaminate LLM performance measurements.

## Reference board
The owner generated a five-panel fox/SwapMe reference board in the ChatGPT conversation. Claude on the owner machine should obtain the actual image from the owner/chat attachment if available and save it locally under run evidence. Do not fabricate binary evidence or claim the image is in GitHub unless exact bytes are available.

Visual direction:
1. Hero: friendly orange fox, black SwapMe hoodie, premium 3D ad render.
2. Exchange: fox + clean exchange interface / BTC and ETH visual language.
3. Trust: fox + shield/security metaphor.
4. Freedom: fox overlooking a city at sunset.
5. Brand close: fox + strong SwapMe end card.

Generated typography/UI in the reference is mood/layout only. Final logos/text must come from trusted brand assets or clean overlays.

## Five reference frames first
Before video calls create/export five separate stills:
01_fox_hero
02_exchange
03_security
04_freedom_city
05_brand_close

Prefer deriving them from the supplied reference board / approved brand assets. Record provenance and SHA-256. These five images are the visual-conditioning pack.

## Suggested 15 s narrative
- Shot A / 0–5: fox in a modern SwapMe exchange; fast visual hook.
- Shot B / 5–10: exchange/trust sequence; fox guides the transaction.
- Shot C / 10–15 LOCAL: branded close in exchange/Prague-style environment, logo + concise CTA.

## Workflow
1. Create RUN_ID and isolated creative evidence folder.
2. Inventory installed creative providers/models and SwapMe assets.
3. Save the five stills and hashes.
4. Lock storyboard and prompts before paid generation.
5. Generate SeeDance A: <=5 s, 720p.
6. Verify file, duration, dimensions, decode and consistency.
7. Generate SeeDance B: <=5 s, 720p.
8. Verify likewise.
9. Generate/render C locally: 5 s. No silent cloud fallback.
10. Assemble ~15 s through existing Bossman media/ffmpeg path.
11. ffprobe + full decode; inspect first/last frames and transitions.
12. Add trusted logo/text overlay if generated typography is wrong.
13. Export final MP4 and calculate SHA-256.
14. Send final MP4 + concise report to configured owner Telegram.
15. Preserve a machine-readable creative trace for future local-agent retrieval.

## Cost and authority
Owner authorizes exactly TWO SeeDance 2.5 720p generation jobs for this run, each max 5 seconds.
Do not silently issue extra paid retries. A third paid job requires new owner approval.
Local generation may retry within bounded compute/time limits without interfering with closure tests.
No unrelated cloud providers.

## Telegram reporting
Use the already configured/allowlisted owner Telegram path. Do not create a second bot or expose tokens.

Every 15 minutes while active send:
- RUN_ID
- current stage
- completed artifacts
- blocker/retry
- cumulative known cloud cost
- ETA only when evidence supports it

If nothing changed, say no material change + current stage. Stop periodic reports when finished/stopped.

Final Telegram package:
- final 15 s MP4
- contact sheet / five references if supported
- final duration/resolution
- SHA-256
- SeeDance jobs used
- total known cost
- local model/runtime used
- PASS/FAIL verification summary

## Learning/evidence
Save at least:
- CREATIVE_RUN_MANIFEST.json
- STORYBOARD.md
- PROMPTS.json
- REFERENCES.json
- PROVIDER_CALLS.jsonl
- LOCAL_GENERATION.jsonl
- EDIT_LOG.jsonl
- VERIFICATION.json
- FINAL_REPORT_RU.md
- SWAPME_15S_CREATIVE_WORKFLOW.md

For every attempt record input reference IDs/hashes, prompt, negative prompt if supported, provider/model/version, settings, duration, resolution, seed/job ID if exposed, start/end time, cost/usage if known, output hash, verification, intervention and rejection reason.

Label contributions: LOCAL_MODEL / CLAUDE_TEACHER / CLOUD_PROVIDER / DETERMINISTIC_TOOL / OWNER_INPUT.

Do not call Claude-written prompts local-model learning. The reusable lesson describes workflow and verified decisions. WEIGHTS_UNCHANGED unless separate fine-tuning actually occurs.

## Acceptance
PASS only if:
- five traceable reference stills exist
- <=2 authorized SeeDance calls consumed
- two usable <=5 s 720p SeeDance clips exist, or truthful BLOCKED evidence explains why
- third 5 s segment is genuinely local
- final video is ~15 s and fully decodes
- brand/fox continuity acceptable
- final text/logo legible
- final hash/evidence exist
- final result/report delivered to owner Telegram
- ongoing 1.0 closure tests were not invalidated

Do not merge creative experiment code into release during 1.0 freeze. Evidence/docs may be pushed to the existing evidence line; product changes require their own reviewed candidate.
