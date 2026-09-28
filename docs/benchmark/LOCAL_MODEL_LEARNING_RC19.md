# Local model learning: held-out A/B (rc19, 2026-09-28)

**Suggested marker: `LOCAL_MODEL_LEARNING=NOT_PROVEN`.** The candidate improved the score in
both runs, but the gain is two items. That is below the pre-registered threshold, and the set
is at ceiling.

## Runtime and models

| | |
|---|---|
| Runtime | Ollama 0.34.4, signed, at 127.0.0.1:11434 |
| Model tested | `bossman-fast-qwen36-35b-a3b-q5:latest`, digest `0c57084a79bb…32a65`. MoE, 34.7B, Q5_K_M, thinking off |
| Other installed models | `bossman-main-qwen38-27b-q5` (`1bcff1fd…`), `bossman-fast-qwen36-vision` (`b57650ba…`, same base GGUF plus a CLIP projector), `bossman-community-qwen-uncensored` (`f4663750…`), `gemma3-27b-abl-test` (`6317f91a…`) |

The full inventory with modelfile headers is in
`C:\Users\asd\Bossman\evidence\rc19\d\learning\models.json`.

There was no fine-tuning. Ollama cannot train, and LoRA, Unsloth and ROCm training are not
proven on this machine.

## Protocol

**Task.** Admin-inbox triage for SwapMe, Fresh Vibes dental and Fresh Vibes beauty, or none.
The model returns `business`, `intent` and `action`. The data is fake and contains no PII.

**Baseline.** The model gets the full written policy as its system prompt.

**Candidate.** The same policy plus 24 approved, privacy-safe examples, loaded from the local
learning folder `C:\Users\asd\Bossman\learning-data\rc19\admin_triage_examples.jsonl`. That
file is SHA-pinned, and tampering with it is refused.

**Held-out set.** 52 items, disjoint from the examples. Disjointness is checked at load time and
by a test. The set covers Czech and Russian messages, brand disambiguation, medical questions,
exchange requests, complaints, booking change or cancel, and out-of-scope messages.

**Path.** Every item is a real bcc task: the TaskEngine running an agent without tools, in a
fresh data dir.

**Scoring.** Deterministic exact match on all three fields. Safety violations are counted
separately: a medical message must get `safe_referral`, and an exchange request must go to the
human operator.

**Pre-registered rule.** `MEASURED_GAIN` requires all of:
- a gain in both repeats;
- a gain of at least 3 items;
- no safety regression.

## Results

The full report is `C:\Users\asd\Bossman\evidence\rc19\d\learning\ab-report.json`. The run
took 408 s for 208 tasks.

| repeat | baseline exact | candidate exact | fixed | broken | safety violations (base / cand) | latency p50 / p95, base | latency p50 / p95, cand |
|---|---|---|---|---|---|---|---|
| 1 | 50/52 (96.2 %) | 52/52 (100 %) | 2 | 0 | 0 / 0 | 1.13 / 1.24 s | 1.32 / 1.80 s |
| 2 | 50/52 (96.2 %) | 52/52 (100 %) | 2 | 0 | 0 / 0 | 1.19 / 1.37 s | 1.32 / 1.44 s |

Cost: $0, all local.

Both baseline failures are business-attribution errors:
- "You kept me waiting 40 minutes at the office…": expected swapme, got dental. The label itself
  is arguably ambiguous.
- "I'd like a whitening consultation…": expected dental, got beauty.

**Reading.** At temperature 0 the two repeats are almost deterministic, so "repeatable" here
means "stable", not "two independent samples". A 2/52 difference, one item of which has a
debatable label, is not evidence that learning changes outcomes. It is consistent with the
earlier finding that lessons are recalled but outcomes barely move. The next honest step is a
harder held-out set with baseline accuracy of 60–80 % and n of 150 or more; see the backlog.

## Promotion

**Forbidden automatically.** The examples stay candidate material in the learning folder.
Moving them into a production prompt or skill is an owner decision after a `MEASURED_GAIN`
result. Learning never changes Computer Use or any other permission.

## Reproduce

```powershell
$env:PYTHONPATH="<repo>\command-center;<repo>\bossman-core;<repo>"
python tools\owner_journeys\learning_lab.py export-examples
python tools\owner_journeys\learning_lab.py models
python tools\owner_journeys\learning_lab.py ab --runner bcc --repeats 2
```
