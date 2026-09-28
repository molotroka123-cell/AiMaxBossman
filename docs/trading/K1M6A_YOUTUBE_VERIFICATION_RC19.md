# K1m6a YouTube: independent verification of teacher claims (rc19, 2026-09-28)

Branch `rc19/d-learn`. Evidence is kept outside Git at
`C:\Users\asd\Bossman\evidence\rc19\d\yt\`. That folder holds `k1m6a-summary.json`,
`raw/<id>/{report.json,claims.jsonl,market.json,asr.*,frames.json}` and `audit/*.json`.

## Pipeline, end to end

1. **Source.** Public replays come from the batch manifest
   `data/trading/youtube_batches/k1m6a-2026-08-14-2026-08-27`, fetched with `yt-dlp` as audio,
   `en-orig` auto-captions and the live-chat replay.
2. **Transcript.** Local ASR with faster-whisper `small`, int8, on CPU. The transcript is labeled
   `local_asr:faster-whisper-small`. YouTube captions are only a fallback and were not used.
3. **UTC alignment.** For each public chat message, the pipeline takes wall-clock time minus the
   message's video offset, then the median across messages. Only the derived number is stored,
   never chat text or ids. The result was cross-checked against the `release_timestamp` metadata.
4. **Claims.** The local Qwen model extracts claims per 240-second transcript window. The model is
   `bossman-fast-qwen36-35b-a3b-q5`, run with `think:false` and temperature 0.
   - A deterministic gate then keeps a claim only if its quote is verbatim, its schema is valid,
     and any number appears in the quote.
   - Each claim covers one of Price / CVD / OI / liquidations / dPOC / dVAH / dVAL / dOpen, and is
     one of STATE, FORECAST, HISTORY or LONG_TERM.
   - Every claim starts as `UNVERIFIED`.
5. **Independent verification.** Uses public data only, and never the teacher's own frames.
   - Binance USDT-M BTCUSDT 1-minute klines give price, a taker-buy CVD proxy, and developing
     UTC-session dOpen/dPOC/dVAH/dVAL (volume profile with 10-USD bins and a 70 % value area).
   - Bybit 5-minute open interest.
   - STATE claims are scored at t0 against past data only. FORECAST claims are scored at
     1/5/15/30/60/240 minutes.
   - Outcomes are `VERIFIED`, `REFUTED` or `INCONCLUSIVE` (inside the noise band).
6. **UNKNOWN, never guessed.** A claim is `UNKNOWN` when:
   - it is about liquidations (there is no public historical feed);
   - it is about another instrument (ETH, SOL, oil, gold and so on);
   - its number is not on the BTC price scale;
   - it is a HISTORY or LONG_TERM statement;
   - it is not aligned, or the data is missing.

Reproduce with:

```
python tools/youtube_trader_ingest_asr.py <run_dir>...
python tools/youtube_trader_ingest_claims.py run <run_dir> --role known|held_out
python tools/youtube_trader_ingest_claims.py frames <run_dir> --max-frames 3
python tools/youtube_trader_ingest_claims.py report C:\Users\asd\Bossman\evidence\rc19\d\yt
```

## Results

The known replay is `6pYfEfZof3c`, the YOUTUBE-001 video. It was used to tune the extraction
prompt: conditionals and HISTORY/LONG_TERM were excluded, over three iterations. The prompt was
then frozen and the two held-out videos were run once each.

| video | role | length | chat alignment (MAD, Δ vs release) | claims | UNKNOWN | claim-level V / R / I | pipeline runtime | model p50 / p95 per window |
|---|---|---|---|---|---|---|---|---|
| 6pYfEfZof3c | known | 105 min | 0.02 s, −0.7 s | 86 | 61 | 7 / 17 / 1 | 446 s | 13.1 / 38.9 s |
| 1C6x8RUQ684 | held-out | 140 min | 0.05 s, −1.3 s | 84 | 50 | 12 / 17 / 5 | 333 s | 7.9 / 22.3 s |
| AD1t15oc7tk | held-out | 133 min | 2.8 s, +1.6 s | 92 | 68 | 8 / 14 / 2 | 343 s | 11.6 / 22.1 s |

Local ASR wall time was 209 s, 393 s and 397 s on CPU. The runtimes include queueing behind the
other workstreams on the shared Ollama.

Per-horizon counts, given as VERIFIED / REFUTED / INCONCLUSIVE / UNKNOWN. The t0 column holds
STATE claims plus the non-verifiable kinds; the other columns hold FORECAST claims.

| video | t0 | 1m | 5m | 15m | 30m | 60m | 240m |
|---|---|---|---|---|---|---|---|
| 6pYfEfZof3c | 4/10/0/48 | 3/3/3/15 | 2/8/1/13 | 4/7/0/13 | 4/7/0/13 | 3/7/1/13 | 2/9/0/13 |
| 1C6x8RUQ684 | 8/9/4/40 | 2/5/3/13 | 3/8/2/10 | 3/8/2/10 | 5/8/0/10 | 2/10/1/10 | 7/6/0/10 |
| AD1t15oc7tk | 3/6/1/48 | 1/4/5/24 | 1/9/4/20 | 2/10/2/20 | 4/8/2/20 | 5/8/1/20 | 7/5/2/20 |

## What the numbers mean, honestly

- **The weak link is extraction precision.** I read the quote of every decided (V/R) claim; I did
  not watch the video. The share that is a real, correctly typed claim about BTC is:

  | video | valid | share | of the valid claims |
  |---|---|---|---|
  | known | 6/24 | 25 % | 2 V, 4 R |
  | held-out 1 | 11/29 | 38 % | 5 V, 6 R |
  | held-out 2 | 7/22 | 32 % | 5 V, 2 R |

  The rest are other people's targets, conditionals, levels merely being watched, other assets,
  or garbled ASR. So the raw V/R counts are **not** a measure of teacher accuracy. They show that
  the verification machinery works. The audit files hold the per-claim labels.
- **Venue proxy.** CVD is a Binance-perp taker proxy. "Spot CVD" or aggregated-CVD claims are
  therefore checked against a different series. Levels come from Binance perp, while the teacher
  may use Coinbase, CME or aggregated data. The tolerance is 0.35 %.
- **Most claims stay UNKNOWN.** Across the three videos 179 of 262 claims (68 %) are UNKNOWN.
  The causes are HISTORY statements, other assets, liquidations, and numbers with no scale.
- **Nothing is promoted.** No lesson, rule or signal is promoted. Teacher claims stay quarantined
  as `UNVERIFIED_TEACHER_CLAIMS_SCORED`. No trading happens.

## Frames (local vision, bounded pass)

Three frames per video were taken at claim times from the locally downloaded 720p public stream,
9 frames in total. Model: `bossman-fast-qwen36-vision`, thinking off, about 50–70 s per frame.
Remote seeking with ffmpeg or `--download-sections` hung on YouTube's throttled ranges, so the
frame pass reads the local file.

Each field the vision model read was then checked independently. Only BTC charts are checked;
other instruments are `UNKNOWN`.

| | frames | BTC charts | chart price V / R / U | dPOC, dVAH, dVAL, dOpen readings V / R / U |
|---|---|---|---|---|
| all 3 videos | 9 | 7 (the others were ETH and NVIDIA) | 5 / 2 / 2 | 5 / 23 / 8 |

- **Chart price and instrument are readable.** Examples: Coinbase BTC/USD 30m close
  79,863.45 against Binance 79,877.4; a CME frame within 0.9 %.
- **Visible OI and CVD are mostly UNKNOWN.** The model returned null for OI on 8 of 9 frames.
- **Level readings are not reliable.** Only 5 of 28 match the computed developing levels within
  0.35 %. The model reads candle OHLC or the wrong line label, or the teacher's session and venue
  differ from Binance UTC. Levels read by vision therefore stay evidence only; they are never
  used as ground truth.
- **Independent alignment check.** Frame `AD1t15oc7tk@1175s` shows the chart's UTC clock
  18:20:17. The chat alignment predicts 18:20:24.6, about 7.6 s away. That is consistent with
  stream latency.

## Suggested marker

`YOUTUBE_K1M6A=VERIFIED` for the **pipeline**. The known replay and two held-out replays ran end
to end: local ASR, then chat-derived UTC alignment, then timestamped claims, then independent
exchange verification at 1/5/15/30/60/240 min, plus frames. Missing fields are `UNKNOWN`.

It is **not** a claim that the teacher's calls are right, and not a claim that the local
extractor is accurate. Extraction precision measured at about 25–38 % is the blocker for
learning from these videos.
