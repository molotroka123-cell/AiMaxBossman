# Real-Workload / Hardware Audit Gate

## Why this exists

Bossman must prove usefulness on real tasks, not only model or synthetic benchmarks. Hardware escalation must also be evidence-driven: a large model, a high core count, or a desire for five agents is not by itself proof that dual-socket or a cluster is required.

This gate turns that rule into a repeatable audit.

## Required task record

Each replay record is JSON (an array or JSONL is accepted):

```json
{
  "task_id": "fresh-vibes-fix-001",
  "status": "passed",
  "verified": true,
  "duration_s": 83.4,
  "human_interventions": 0,
  "retries": 1,
  "concurrency": 3,
  "peak_memory_gb": 71.2,
  "oom": false,
  "started_at": 1788700000.0,
  "ended_at": 1788700083.4
}
```

`verified=true` means the post-condition was checked, not merely that an agent returned a final message. Examples: a code fix is re-tested, a generated video opens and satisfies the requested constraints, or a browser task has an evidence-backed final state.

## Where the records come from

The corpus is collected automatically. Every terminal `CompoundRunner.run` appends one
sample derived from the durable TaskJournal to
`.bossman-state/benchmarks/real_workloads.jsonl` (override the root with
`BOSSMAN_REAL_WORKLOAD_ROOT` or the `real_workload_telemetry_root` context key).

Three properties are load-bearing:

- **Observational.** The write is best-effort: an unavailable benchmark store is swallowed
  and the task result stands as measured. Telemetry can never fail a user task.
- **Failures are recorded too.** Blocked and failed runs produce samples exactly like
  passing ones, so the corpus cannot be curated into a flattering shape.
- **Replays do not inflate it.** Samples are de-duplicated by task, plan digest and status,
  so resuming a chain does not manufacture extra evidence.

Set `disable_real_workload_telemetry` in the run context to opt one run out. Fields the
journal cannot know — `human_interventions`, `retries`, `concurrency`, `peak_memory_gb`,
`oom` — are read from the run context and default to a neutral, non-flattering zero.

## Run

```bash
python scripts/real_workload_audit.py .bossman-state/benchmarks/real_workloads.jsonl \
  --sla-p95-s 120 \
  --json-out artifacts/real_workload_audit.json \
  --md-out artifacts/real_workload_audit.md
```

The script uses only the Python standard library. On Linux it also snapshots sockets/NUMA topology with `lscpu` and total RAM from `/proc/meminfo` when available.

## Metrics that matter

The audit reports verified success rate, p50/p95 wall-clock latency, throughput, retries, operator interventions, OOM events, maximum observed concurrency, and peak observed memory. These are end-to-end workload measurements. Synthetic tokens/s or isolated model scores can still be used for diagnosis, but they cannot pass this gate.

## Hardware decision rules

The tool deliberately refuses a topology recommendation with fewer than 10 representative runs. If verified success is below 90%, it returns `FIX_SOFTWARE_FIRST`: buying hardware must not hide execution/reliability defects.

When reliability is acceptable, the recommendation is based on measured pressure:

- no measured SLA/capacity pressure -> `SINGLE_HOST_SUFFICIENT_FOR_OBSERVED_LOAD`;
- memory/OOM pressure -> `BENCHMARK_SCALE_UP`;
- concurrent SLA pressure -> `BENCHMARK_SCALE_OUT`;
- both -> `BENCHMARK_SCALE_UP_AND_SCALE_OUT` and compare candidates using the exact same replay corpus;
- latency miss without capacity pressure -> `OPTIMIZE_THEN_REPLAY`.

A dual-socket machine is accepted only if a NUMA-aware replay materially improves the target p95/throughput/cost versus the single-host baseline. A cluster is accepted when aggregate worker capacity, concurrency or resilience is the measured need. Splitting one model across nodes is evaluated separately because inter-node communication can become a new bottleneck.

## Bossman acceptance corpus

For a meaningful owner-facing audit, keep a fixed representative corpus from actual Bossman use. At minimum include: browser/web execution with proof, code change + tests, multi-step research-to-artifact work, file/media generation with output validation, and a concurrent Fleet run. Record both successes and failures; do not curate failures out of the input.

The practical question this gate answers is: **how many of the owner's real tasks reached a verified result, how fast, with how much human rescue, and what resource bottleneck was actually measured?**
