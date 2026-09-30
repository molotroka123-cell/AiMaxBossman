# Bossman Provider Fleet & RunPod Expansion Plan

Status: design/preparation only. This does not create accounts, bypass provider limits, rent GPUs, or spend money automatically.

## Goal

Bossman should exhaust legitimate capacity in this order:

1. Existing funded provider account(s)
2. Existing free tier of each distinct provider
3. Existing paid/prepaid balance of each distinct provider
4. Local AI Max resources
5. Only then optional second low-cost account / sub-account where the provider's terms explicitly permit it
6. GPU rental (RunPod) only when batch throughput, memory, or training makes it economically better **AND the owner explicitly approves that rental and its spend cap**

Never create multiple accounts to evade rate limits, abuse free tiers, or violate provider terms. A "second account" is only eligible when the provider explicitly supports subaccounts/projects/workspaces or multiple owner-controlled accounts for legitimate separation/billing.

## Provider priority

### Tier A — use first

#### OpenRouter
- Existing owner-funded account is primary.
- Use paid balance and free models before considering any secondary owner-controlled account.
- Free model collection can be routed via `openrouter/free`.
- Track per-model/provider rate limits, latency, tool-use reliability and actual cost.

#### Groq
- Strong free tier for fast inference.
- Current published free limits include up to 30 RPM / 1000 requests/day / 200k tokens/day for models such as GPT-OSS-120B and Qwen 3.8 27B.
- Use for fast routing, extraction, classification, cheap agent workers and Whisper.

#### Google Gemini API / AI Studio
- Free tier available; quotas vary by model/project.
- Rate limits are applied per project, not per API key.
- Paid tier supports larger limits and batch API.
- Important privacy rule: free-tier content may be used to improve Google products; paid tier offers different data-use treatment.
- Good candidate for multimodal/video workloads and batch processing.

### Tier B — paid low-cost / burst

#### Together AI
- No free trial currently.
- Minimum $5 prepaid.
- Treat as CHEAP_PAID, not FREE.

#### Fireworks AI
- Serverless inference + on-demand deployments.
- Small signup credits may exist; verify at runtime.
- Useful as a paid burst provider.

### Tier C — evaluate before enabling
- Cerebras Inference
- SambaNova Cloud
- any new zero-cost/open preview provider
- model-specific preview APIs

Bossman must query current provider pricing/limits before first use and store a dated capability snapshot. Never hard-code today's free tier as permanent truth.

## Provider account policy

Each provider has an account pool:

```json
{
  "provider": "groq",
  "accounts": [
    {
      "id": "primary",
      "class": "OWNER_PRIMARY",
      "priority": 0,
      "allowed": true,
      "daily_budget_usd": 0,
      "free_tier": true
    },
    {
      "id": "secondary",
      "class": "OWNER_SECONDARY",
      "priority": 100,
      "allowed": false,
      "activation": "ONLY_IF_PROVIDER_TERMS_ALLOW_AND_OWNER_CONFIGURED"
    }
  ]
}
```

Rules:
- Never auto-register accounts.
- Never create fake identities.
- Never rotate accounts to bypass a provider's quota.
- Primary is exhausted only when legitimate quota/balance/circuit state says so.
- Secondary may activate only if already owner-created/configured and provider terms permit it.
- Prefer separate projects/workspaces/subaccounts over separate identities when supported.
- All secrets stay outside Git.

## Global scheduler policy

For each task, score candidates on:

`quality_gate, price, latency, context, tools, privacy, availability, remaining_quota`

Default route:

`FREE/ALREADY_FUNDED -> LOCAL -> CHEAP_PAID -> RENTED_GPU`

Exception: privacy-sensitive tasks may route LOCAL first.

A provider is "exhausted" only when one of these is true:
- hard quota reached;
- prepaid balance below configured reserve;
- rate-limit window closed and wait would violate task deadline;
- provider circuit breaker open;
- required capability unavailable.

Do not jump to secondary accounts because one model is 429 if another legitimate provider/model is available.

## RunPod — use only for jobs where rental wins

Historical owner test context to preserve:
- prior RunPod experiment used RTX 5090 32GB around $0.99/hr;
- 250GB persistent Network Volume;
- SSH over exposed TCP;
- intended for local-only testing;
- prior acceptance idea included Direct vs Bossman, router, memory, recovery, VRAM/RAM, concurrency, security and long-run evidence.

Do not repeat setup learning from zero.

### Rental decision

Estimate:

`rental_cost = gpu_hour_price * estimated_hours + storage + egress`

Compare against:
- estimated cloud API cost;
- local wall-clock time;
- deadline value;
- model memory requirement;
- parallel batch opportunity.

Rent only when:
- required model does not fit locally; OR
- batch can finish materially faster and owner accepts budget; OR
- training/distillation is impossible/too slow locally.

### Recommended GPU classes

- RTX 5090: cheap inference/vision/batch when model fits.
- H100: larger/faster workloads where 80GB-class memory is sufficient.
- H200: only when 141GB memory/bandwidth materially changes feasibility.
- Multi-H200: only for large-model training/distillation or highly parallel batch.

Do not select H200 solely because it is more expensive.

## Jev / Bossman RunPod workflow

Jev may prepare and execute the workflow only after owner grants the required spend authority.

```
TASK
 -> estimate resource need
 -> compare free providers/local/rental
 -> choose RunPod only if rental gate wins
 -> check existing RunPod account/session
 -> choose GPU + region + volume
 -> create pod
 -> mount persistent volume
 -> bootstrap pinned environment
 -> pull exact Git SHA
 -> run self-test
 -> run workload
 -> stream telemetry/evidence
 -> checkpoint outputs to volume/Git-compatible artifacts
 -> verifier checks outputs
 -> stop/terminate pod
 -> verify billing stopped
 -> retain/remove volume according to policy
```

Jev must NEVER:
- create a RunPod account;
- enter card/payment details without explicit owner action;
- increase spend cap by itself;
- leave an idle paid pod running;
- expose SSH broadly without allowlist/firewall policy.

## RunPod job receipt

Every rental stores:
- provider account id alias;
- pod id;
- GPU type/count;
- hourly rate observed at creation;
- region;
- volume id;
- image/container digest;
- Git SHA;
- command/workflow id;
- start/end timestamps;
- max runtime;
- actual cost if available;
- outputs;
- verifier result;
- termination confirmation.

## Training workflow

For K1M6A/video training:

1. AI Max prepares manifest and deduplicated candidate batch.
2. RunPod receives only the required video segments/artifacts.
3. Rented GPUs run VLM/ASR/embedding/training jobs in parallel.
4. Outputs return as typed episode/evidence artifacts.
5. AI Max independently verifies and promotes/rejects.
6. Rental cannot directly promote trusted memory.

For LoRA/distillation:
- dataset manifest must be frozen and hashed;
- holdout excluded physically from training files;
- experiment config/Git SHA fixed;
- checkpoints periodic;
- evaluation before promotion;
- no model replaces current production model merely because training loss improved.

## Failure/cleanup rules

- startup timeout -> terminate;
- bootstrap failure -> terminate;
- no progress heartbeat -> checkpoint then terminate;
- budget 80% -> warn owner;
- budget 100% -> hard stop;
- verifier failure -> preserve artifacts, terminate compute;
- API/network loss -> checkpoint/retry within cap;
- process completes -> terminate immediately.

The worst failure is a paid idle GPU. Cleanup is part of the success condition.

## 1.6 acceptance

Provider Fleet PASS requires:
- at least 3 independent provider adapters;
- quota-aware failover;
- no account-limit circumvention;
- cost ledger;
- primary-account preference;
- local fallback;
- rented-GPU decision model;
- RunPod create -> workload -> verify -> terminate E2E in a capped sandbox;
- proof that a failed workflow also terminates billing resources.


## Owner-only rental authority addendum

Even if the economic scheduler ranks RunPod first, the scheduler output is only
a recommendation until a fresh owner approval binds workload, provider, GPU
class/count, maximum runtime and maximum spend.

When approved, pre-pack compatible expensive work so the rental produces the
maximum amount of independently verifiable output per billed hour. Track useful
GPU utilization and dollars per verified artifact, not raw utilization alone.
See `docs/v1.6/FOUNDATION_FREEZE.md`.
