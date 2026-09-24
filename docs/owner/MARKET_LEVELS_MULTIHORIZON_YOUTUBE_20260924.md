# Market intelligence next lane — isolated from Bossman 1.0 closure

Branch: `feat/market-levels-multihorizon-youtube-20260924`

This branch is intentionally separate from `release/bossman-owner`. Do not merge
it while 1.0 exact-SHA certification is running.

## Goals

1. Extract explicit chart levels: dPOC, dVAH, dVAL, dOpen.
2. Derive reclaim/loss/retest from time-series evidence, not one screenshot.
3. Add 15m / 30m / 60m Price+CVD+OI statistics.
4. Prepare YouTube text+vision ingestion for outcome-labelled, quarantined learning.

## Level trust contract

A named level becomes numeric only when the local vision path reads the explicit
label + price unanimously from the same fresh frame. Geometry may validate a
reading but must never invent a number. Missing levels remain missing.

Events are temporal:
- BELOW -> ABOVE = RECLAIM
- ABOVE -> BELOW = LOSS
- approach into tolerance = RETEST

A single frame cannot prove a reclaim.

## Multi-horizon contract

15m, 30m and 60m compare only observations with the exact same series identity:
channel, instrument, exchange, timeframe, CVD type and extractor. If there is no
compatible verified observation old enough, the horizon is
`INSUFFICIENT_HISTORY`.

These horizon labels are descriptive evidence, not probabilities.

## YouTube training preparation

Pipeline remains:

URL -> captions/ASR + sampled frames -> local vision -> typed observations ->
Trader Apprentice -> future outcomes -> UNVERIFIED inbox -> independent
verification -> promotion.

Add level observations and 15m/30m/60m outcome labels to candidate episodes.
Never train directly on raw teacher claims. Teacher text is evidence, not truth.

Required owner-PC acceptance before promotion:
- 20 manually reviewed level crops with zero wrong VERIFIED numeric levels;
- at least one demonstrated dPOC/dVAH/dVAL/dOpen sequence when visible;
- 15/30/60m horizon calculations checked against ledger timestamps;
- YouTube episode retains source URL, timestamps, frame hashes and transcript;
- lesson remains UNVERIFIED until future outcome or independent evidence exists.
