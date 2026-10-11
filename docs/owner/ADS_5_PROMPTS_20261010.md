# 5 ad-style hype videos: prompts / briefs (Bossman Motion Studio / Genjutsu Live)

Written 11.10.2026 03:3x. HONEST NOTE: the owner re-prioritised the order (ad b first, within 40 min), so this file was written
after the first draft of ad (b); the briefs below are what all five were built to. Style: a short, loud product ad (hook in the
first beat, a cut or colour change on every beat or every second beat, one hero shot on the drop, one clean before | after, a
closing card). No third-party brand names or logos; the product is "Genjutsu Live" / "Bossman Motion Studio".

Common rules: 9:16, 1080x1920, 30 fps, 13.8 s = 30 beats of the reference track grid (130.5 BPM, comb-fitted), H.264 crf 10.
Cuts on beats; punch-in on every hit; velocity ramp inside every shot; RGB split + flash on hits (masked off the face);
kinetic type at the top strip or the bottom third, never over the face; one grade for all shots; uniform scaling only.
Honest labels: face = FaceFusion, models trained on a rented GPU, video rendered locally, clothes = trial / reference looks.

| # | Name | Prompt (what the viewer sees) |
|---|---|---|
| a | One-take identity swap | "ONE TAKE": one slow-motion take of the same moment; the original face wipes into HER face on the beat (diagonal mask reveal, RGB split, flash); then glitch-compare ORIGINAL / SWAPPED on every beat, hero slow-mo on the drop, clean original | swapped split, end card "same video, any face". |
| b | Hair-colour cycle on the beat | The same take recoloured on every beat: PINK, PLATINUM, BLUE, RED (the owner's reference looks, reference-guided transfer), colour name in its own colour at the bottom, wipe sweeps the new colour across the hair, platinum hero on the drop, clean before | after. |
| c | Outfit-change reveal | Live take; on the beat a mask wipe reveals her in a navy / burgundy / bob + blazer / ponytail / gray blazer: REFERENCE LOOKS (generated stills of her), labelled "reference look"; closing label "CLOTHES: TRIAL" because the video clothes swap (VACE) has no clean run. |
| d | Background teleport | RVM person matte over local plates: NEON CITY, STUDIO, NEON 2 (graded), REAL ROOM; wipe on every second beat; colour-matched light and rim light from the plate; no contact shadow (no floor in frame). |
| e | Showreel | Beat-grid montage of a-d: 2-beat shots, 1-beat flurry, 4-beat hero, speed ramps, whip smear + flash on cuts, end card "FOUR TRICKS / ONE TAKE". |
| 6 | Genjutsu Studio promo (45 s) | By PROMO_6 plan: hook (3 transformations) -> hair (split original | colour x4) -> outfit (reference looks) -> hairstyle (5 reference stills) -> how it works (constructor screencast, 4 clicks) -> CTA (her take in the neon city, 4 looks, end card). |

Cheap cloud text models were NOT used for these prompts (no media or captions left the machine).
