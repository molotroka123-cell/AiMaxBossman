# YouTube learning: audit and one narrow improvement (26.10.2026, agent Claude)

SHA base: 1b567b73 on agent/claude-20261026. Nothing committed by this agent. No network to YouTube, no video downloaded, no keys read.

## 1. What "YouTube learning" is in the repo today

It is a TRADING-specific stack (K1m6a channel), not a general "learn from lectures" feature.

| Piece | Where | What it does |
|---|---|---|
| Subtitles + frames + vision | `tools/youtube_trader_ingest.py` (+ `_asr`, `_auto`, `_batch`) | yt-dlp subtitles (VTT), frames every 30 s, vision model reads charts, future outcome from later frames |
| Claims + market check | `tools/youtube_trader_ingest_claims.py`, `learning/claim_verification.py` | local model extracts timestamped claims, verbatim-quote check, Binance/Bybit outcome check; all start UNVERIFIED |
| Word analysis | `tools/k1m6a_word_analysis.py` | 150-word windows, JSON per sentence, word coverage measured, idle gate, resume |
| Typed claims | `bossman-core/bossman/trading_learning/claims.py`, `sanitize.py`, `ingest.py` | rule-based claim types, injection quarantine, dedupe window 3 s, sha256 source passport |
| Exam | `bossman-core/bossman/trading_learning/k1m6a_exam.py` | pinned videos 6/2/2, sealed reference, leakage and contamination guards, BASELINE vs LESSONS vs RESTART_TRANSFER, PRELIMINARY on small n |
| Orchestration | `command-center/bcc/economy_orchestrator.py`, `features/v15_owner_run.py` | youtube_learning_lead stage; `youtube_claims_start_unverified` |

Existing tests run now (no network):

- `tests/test_youtube_trader_ingest.py, _auto, test_youtube_claim_verification, test_k1m6a_batch_robustness, test_k1m6a_discovery_limit, test_k1m6a_word_analysis`: 52 passed.
- `bossman-core/tests/test_k1m6a_exam*.py, test_trading_claims, test_trading_sanitize_gaps, test_trading_lookahead, test_fable_transcript`: 94 passed.

PASS here means the code behaves on fixtures. It says nothing about whether Bossman became better at a task.

## 2. Gaps found (by reading code)

1. Trading only: claim kinds, regexes and verification (price, CVD, OI) are market-specific. No path for lectures, technical talks, how-to videos.
2. Transcript source and quality: sources are labelled in the claims tool, but no quality gate for auto-captions (rolling duplicates, wrong language, mojibake, repeated tokens). `parse_vtt` in `tools/youtube_trader_ingest.py` does not de-roll YouTube auto-captions (not verified on real files; only read).
3. Russian: rule patterns in `trading_learning/claims.py` cover Russian, but there is no language-aware quote matching (yo/e, punctuation, case) in the generic path; the ASR choice for Russian (GigaAM-v3) is only in research notes, not wired.
4. Dedupe: 3 s window exact-text dedupe inside one video; no near-duplicate (re-upload, repeated intro) and no cross-video dedupe against stored memory.
5. Limits and privacy: window sizes are fixed; no explicit loopback-only guard for the extractor endpoint in a generic path (the trading tools use `route_guard` for free/local only).
6. "Learned?" proof: the only real proof is the K1m6a exam, and it is trading-bound and needs a vision model run. For any other video, nothing prevents writing claims to memory and calling it learned. Owner rule: memory hits and teacher-written patches are not learning.

## 3. Open solutions audit (fetched 26.10.2026 via WebFetch; GitHub API was rate-limited from this IP and `gh` is not logged in, so last-commit DATES are UNVERIFIED for all)

| Project | License | Windows | Notes | URL (opened) |
|---|---|---|---|---|
| yt-dlp | Unlicense | `yt-dlp.exe` x64 Win10+ | `--write-subs`, `--write-auto-subs`, `--skip-download` (subtitles only, no video) | https://github.com/yt-dlp/yt-dlp |
| faster-whisper | MIT | via community standalone builds; CPU benchmarks | no AMD/ROCm/Vulkan mention on page; AMD path UNVERIFIED | https://github.com/SYSTRAN/faster-whisper |
| whisper.cpp | MIT | Windows MSVC/MinGW | Vulkan GPU support documented (`-DGGML_VULKAN=1`): fits the AMD machine | https://github.com/ggml-org/whisper.cpp |
| onnx-asr | MIT | Windows, DirectML listed | GigaAM v2/v3 (Ru) listed; Russian quality figures only from prior research note, UNVERIFIED here | https://github.com/istupakov/onnx-asr |
| LanceDB | Apache-2.0 | Windows not stated on the page (UNVERIFIED) | embedded, runs locally | https://github.com/lancedb/lancedb |

Qwen3-Embedding and GigaAM-v3 metrics: see `D:\Bossman\research\open-stack-20261026\04-speech-memory-vision.md` (secondary source, not re-opened).
Recommendation (not done): subtitles first via yt-dlp; ASR fallback GigaAM-v3 via onnx-asr for Russian, whisper.cpp Vulkan as general fallback; memory in the existing store, LanceDB only if measured need.

## 4. What was done

New package `command-center/bcc/learning_video/` (no existing file edited) and `command-center/tests/test_learning_video_claude.py`.

- `transcript.py`: VTT parser (tags stripped, rolling auto-caption de-duplication, malformed blocks skipped), cue-boundary chunks with timestamps, source passport (video id, kind manual_subs/auto_subs/asr_local, sha256, trust low for auto), quality gate (TOO_SHORT, MOJIBAKE, LANG_MISMATCH, REPEATED_TOKEN, IMPOSSIBLE_RATE, SPARSE).
- `dedupe.py`: shingle-Jaccard near-duplicate for chunks and for claims, including against the stored memory.
- `claims.py`: a claim must carry a verbatim quote (>=4 words; case, punctuation and yo-insensitive) located to cue timestamps; numbers in the statement must appear in the quote; kind whitelist; injection and PII/secret rejection; model JSON is untrusted and re-validated; status always UNVERIFIED; `assert_loopback` refuses non-local extractor endpoints; deterministic rule extractor as offline fallback.
- `evaluate.py`: control questions with three arms on the same questions: no knowledge, retrieved claims, unrelated-source claims. Answer must cite a retrieved claim id. Leaky questions excluded. Verdict IMPROVED_ON_CONTROL_QUESTIONS only if treatment beats both; n<5 is PRELIMINARY; real-task transfer is always reported NOT_RUN.
- `pipeline.py`: vtt text -> report -> JSONL store with cross-video dedupe; low-quality transcripts are rejected without writing.

Tests: 30 unit tests (positive and negative, Russian fixtures), all passed:

```
cd command-center && python -m pytest tests/test_learning_video_claude.py -q -p no:cacheprovider
30 passed in 0.29s
```

Negative control (mutation) done: disabling the verbatim-quote check made 6 tests fail; disabling the citation requirement made 2 fail; both restored, 30 pass again. Existing 52 + 94 tests unaffected (not touched). Existing module `tests/test_youtube_trader_ingest.py` re-run after changes: 9 passed.

## 5. NOT proven

- Nothing was run on a real YouTube video, real auto-captions, or real Russian ASR output. Fixtures are synthetic; the rolling-caption logic is tested only on a synthetic imitation of the format.
- The answer function in tests is a deterministic fake. Whether a local Gemma/Qwen improves on real held-out questions is NOT_RUN. The evaluator measures control-question gain only; transfer to a real task is NOT_RUN.
- Not wired to the Command Center, pult, memory store or capability tree. No API route, no live receipt under `docs/audits/` (required by AGENTS.md "proven, not wrapped"): therefore this is a tested module, not an added capability.
- Retrieval is token-stem overlap (5 chars); no embeddings. Russian morphology tolerance is crude.
- Open-source last-commit dates unverified.

## 6. Next steps

1. Owner-approved live run (prepared command, NOT executed). Subtitles of one public video only, local only:
   `yt-dlp --skip-download --write-subs --write-auto-subs --sub-langs "ru.*,en.*" --sub-format vtt -o "<scratch>\%(id)s" <public-url>`
   then in Python: `run_pipeline(Path("<file>.vtt").read_text("utf-8"), url=<url>, kind="auto_subs", lang="ru", model_extractor=<local model call after assert_loopback(endpoint)>, store_path="<scratch>\\claims.jsonl")`.
2. Write 10+ held-out control questions BEFORE reading the claims, from a different part of the video or an independent source; run `evaluate_questions` with the local model, record the verdict and raw rows.
3. Pick a real task the video's knowledge should help; run it with and without the claims; compare with an independent check. Only then say "learned".
4. Wire a Command Center route and a memory write that carries source_id, timestamps and UNVERIFIED status; add receipt under `docs/audits/`.
5. Russian ASR fallback (onnx-asr GigaAM-v3, measured on the owner PC) when no manual subtitles exist.
6. Reconcile with `trading_learning` types so trading videos keep their market verification path.
