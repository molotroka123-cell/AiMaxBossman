# Bossman Trading Intelligence — OSS map and build plan

Status: research/implementation lane. **No live order execution is authorized.**
The objective is to build the best evidence-driven *research, classification,
backtest and paper-trading* system first. Real-money execution, if ever enabled,
requires a separate owner approval and risk gate.

## Existing Bossman edge

Bossman already has data ordinary trading bots usually do not:
- owner CASE-001/002/003 with chronology and lessons;
- Twitch/Coinwise Price+CVD+OI observations;
- structural-level work (dPOC/dVAH/dVAL/dOpen);
- local vision;
- 15m/30m/60m horizon analysis;
- Telegram owner delivery;
- YouTube teacher ingestion with quarantine.

Do not throw this away to become a generic RSI bot.

## OSS projects to evaluate

### Video / learning
- https://github.com/fuleinist/video-to-skill — MIT; compile long videos into reusable segments.
- https://github.com/coah80/youtube-mcp — MIT; timestamp-aligned frame/transcript, scene and cue sampling.
- https://github.com/bradautomates/claude-video — MIT; frame budgets, scene/keyframe extraction, dedup.
- https://github.com/WebDevBar/watch-video — MIT; local faster-whisper + OCR + pHash dedup + timeline.
- https://github.com/SYSTRAN/faster-whisper — MIT; local ASR.
- https://github.com/m-bain/whisperX — BSD-2-Clause; word-level alignment/diarization.
- https://github.com/Breakthrough/PySceneDetect — BSD-3-Clause; mature scene detection.
- https://github.com/QwenLM/Qwen2.5-VL — Qwen license; native video/temporal grounding reference.
- https://github.com/minseokii/OTT-Vid — experimental video-token compression; benchmark only first.
- https://github.com/EvolvingLMMs-Lab/lmms-eval — multimodal evaluation harness.
- https://github.com/RomGai/VideoStir — MIT; spatio-temporal graph retrieval for long video.
- https://github.com/HKUDS/VideoRAG — mixed/dual licensing; architecture/reference until component license gate.
- https://github.com/city1517/OneClip-RAG — long-video clip retrieval reference; license gate before reuse.
- https://github.com/facebookresearch/egagent — CC-BY-NC-4.0; research/evaluation only, no commercial code reuse.

### Market data / order flow
- https://github.com/Co-Messi/HyperData-Terminal — Apache-2.0; public WS market data, CVD/order flow, OI, funding, liquidations, health/staleness.
- https://github.com/ccxt/ccxt — MIT; broad exchange abstraction. If used, wrap in a read-only allowlist for research/data.
- https://github.com/binance/binance-connector-python — official Binance connector reference.
- https://github.com/bybit-exchange/pybit — official Bybit Python connector reference.
- https://github.com/nautechsystems/nautilus_trader — event-driven research/backtest/live-parity architecture.
- https://github.com/hummingbot/hummingbot — mature crypto market connectivity/strategy architecture reference.

### Backtest / research
- https://github.com/polakowo/vectorbt — fast vectorized research/backtesting reference.
- https://github.com/QuantConnect/Lean — large event-driven engine; architecture/reference, license gate before integration.
- https://github.com/freqtrade/freqtrade — mature crypto dry-run/backtesting/risk/Telegram stack. GPL: use as reference or isolated component only after licensing review.
- https://github.com/freqtrade/freqtrade-strategies — strategy examples; GPL and not trusted alpha. Never copy performance claims.
- https://github.com/AI4Finance-Foundation/FinRL — RL research reference. RL results must beat simple baselines out-of-sample before consideration.
- https://github.com/AI4Finance-Foundation/FinGPT — finance NLP/sentiment and benchmark ideas, not an execution authority.

### Visualization / notifications / repo efficiency
- https://github.com/tradingview/lightweight-charts — Apache-2.0; dashboard charting.
- https://github.com/caronc/apprise — BSD-2-Clause; multi-channel notifications if Telegram expands.
- https://github.com/yamadashy/repomix — MIT; compress OSS repos before local-model review to save context/tokens.

## Architecture

```
PUBLIC MARKET FEEDS -----+
TWITCH / COINWISE -------+--> Evidence Store --> Feature/State Engine
OWNER CASE 001/002/003 --+
YOUTUBE VERIFIED LESSONS-+
                                      |
                                      v
                         Walk-forward Research Engine
                         /        |         \
                  deterministic  ML       retrieval
                       rules    models       CASEs
                         \        |         /
                          Ensemble/Verifier
                                |
                         PAPER DECISION ONLY
                                |
                       Risk / Safety Governor
                                |
                          Telegram / Dashboard
```

## What "best" means

Do not optimize for win rate alone.

Track at minimum:
- expectancy after fees/slippage;
- max drawdown;
- Sharpe/Sortino where meaningful;
- profit factor;
- calibration/Brier score for probabilistic outputs;
- precision/recall by regime;
- turnover;
- tail loss;
- stability across months/regimes;
- parameter sensitivity;
- performance after realistic latency;
- number of independent trades;
- comparison with buy-and-hold/no-trade/simple baselines.

A 95% win-rate strategy that occasionally loses 30R is not superior.

## Anti-overfitting gates

1. Chronological split only.
2. No future frame/text in earlier features.
3. Train / validation / untouched holdout by day/week/regime.
4. Purge/embargo around overlapping horizons.
5. Fees, spread, slippage and latency.
6. Walk-forward testing.
7. Parameter perturbation.
8. Multiple-market/regime stress where applicable.
9. Paper trading after backtest.
10. No promotion because one YouTube teacher or one CASE was profitable.

## Model roles

### Deterministic layer
Owns:
- raw values;
- series compatibility;
- level events;
- deltas;
- horizon labels;
- risk limits;
- execution permissions.

### Local LLM/VLM
May:
- extract evidence;
- summarize;
- retrieve analogues;
- propose hypotheses;
- explain decisions.

May NOT:
- invent market numbers;
- rewrite outcomes;
- promote its own lesson;
- bypass risk governor;
- place a real order by free-form text.

### ML/statistical models
Only after enough data. Start with interpretable baselines before deep/RL models.
Every model version stores training window, features, hyperparameters and
out-of-sample report.

## CASE-driven feature hypotheses

CASE-001:
- deleveraging vs bearish leverage expansion;
- support loss + OI expansion;
- acceptance requires hold/retest.

CASE-002:
- recovery efficiency;
- failed repair below value vs accepted reclaim;
- CVD surge + flat OI;
- leverage rebuild;
- squeeze payout;
- do not confuse signal generation with late payoff.

CASE-003:
- macro-event flag;
- two-sided event sweep;
- post-event acceptance;
- confidence downgrade during FOMC/CPI/NFP.

These are hypotheses to test, not hard-coded promises of profit.

## Staged rollout

Stage 0 — observation only.
Stage 1 — replay/backtest.
Stage 2 — shadow predictions with no orders.
Stage 3 — paper trading with realistic fees/slippage.
Stage 4 — owner-reviewed tiny-capital pilot only after a separate authorization.
Stage 5 — scaling only from measured out-of-sample evidence.

Bossman 1.0 market surface remains read-only. This branch must not add exchange
write credentials or order methods.
