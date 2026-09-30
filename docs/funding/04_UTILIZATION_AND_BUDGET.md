# 04 — Utilization forecast and budget / Загрузка и бюджет

Status: **PLANNED / FORECAST**. Hours and dollars below are planning estimates derived from cited list prices, not
invoices or measurements. Accessed 2026-09-29.

## 1. Price inputs

| Input | Value | Source | Status |
|---|---|---|---|
| Runpod H200 per GPU-hour | $3.59 (Community) / $4.59 (Secure) | [runpod.io/pricing](https://www.runpod.io/pricing) | VERIFIED (listing) |
| 8×H200 node-hour (derived) | $28.72 – $36.72 | 8 × above | derived |
| Vast.ai H200 | page showed "No current offers" | [vast.ai/pricing/gpu/H200](https://vast.ai/pricing/gpu/H200) | UNVERIFIED |
| DeepSeek API `deepseek-v4-pro` per 1M tokens (off-peak / peak) | in cache-miss $0.66 / $1.32, out $1.98 / $3.96 | [DeepSeek pricing](https://api-docs.deepseek.com/quick_start/pricing/) | VERIFIED via summarising fetch — re-check exact cents before use |
| DeepSeek API `deepseek-flash` | in cache-miss $0.15 / $0.30, out $0.60 / $1.20 | same | same |
| DGX Station GB300 price | not published by NVIDIA | [nvidia.com DGX Station](https://www.nvidia.com/en-us/products/workstations/dgx-station/) | UNVERIFIED — OEM quote needed (09) |

## 2. Utilization forecast — credit phase (8×H200 benchmark node)

| Window | Purpose | Node-hours (plan) |
|---|---|---:|
| W0 | provision, pull ≈689.5 GB weights, SGLang smoke, hash check | 4 |
| W1 | smoke run, 10 tasks × arm F, fix harness | 6 |
| W2 | full run, 60 tasks × 3 reps, arm F + advisory review | 16 |
| W3 | repeat on held-out half + concurrency/throughput sweep | 10 |
| W4 | reserve for re-run after harness defects | 6 |
| **Total** | | **42** |

Cost at derived list price: 42 × $28.72–$36.72 = **$1,206 – $1,542** (planning). Arms A–E run from the owner's machine and
subscriptions; arm E (DeepSeek API) gets a hard cap of **$25 total** for the benchmark (planned cap, owner sets it).

## 3. Credits burn plan

| Order | Source (see 06) | Use | Stop rule |
|---|---|---|---|
| 1 | Vast.ai Startup Program, $2,500 GPU credits | W0–W4 if an 8×H200 offer is available | stop at 80 % of credit or after W4, whichever first |
| 2 | NVIDIA Inception partner credits / Innovation Lab (60-day access, selected members) | repeat on NVIDIA-hosted capacity; ask for GB300 / DGX Station test time | per program terms |
| 3 | AWS Activate Founders (≤ $5,000) / Microsoft ($200 start, up to $150K over time) / Google Start (≤ $2,000) | control-plane hosting, CI runners, small GPU checks; big GPU only if the credit terms allow | provider budget at 50/80/100 %; auto-stop |
| — | Paid (own money) | **none without an explicit owner decision** (constitution) | — |

## 4. Controls that prevent charges after credits expire

Provider side (owner configures, 09):
- Prefer prepaid balances; no auto-recharge; no card stored where the program allows it.
- Microsoft: remaining usage becomes pay-as-you-go once credits are used or expire
  ([MS for Startups overview](https://learn.microsoft.com/en-us/startups/microsoft-for-startups/overview), accessed
  2026-09-29) → delete all resources and the subscription's GPU quota before the expiry date; budget alerts at 50/80/100 %.
- AWS / Google: budgets + alerts on day 1; alerts do not cap spend by themselves, so the kill rule below is mandatory.
- Calendar entry 14 and 3 days before each credit expiry (owner).

Bossman side (planned, part of 03 §6):
- Every node has a hard TTL: node shuts down at window end or at TTL (default 10 h), whichever first.
- Daily $ cap in the existing fail-closed budget gate; unknown price is treated as not free.
- Credit ledger per provider (granted, used, expiry); at ≤ 10 % remaining or ≤ 3 days to expiry Bossman refuses new
  GPU jobs and asks the owner.
- No new paid account, card, or subscription is ever created by an agent (constitution: money and credentials are
  always the user's decision).

## 5. Owned-hardware phase (after M4, only if the benchmark rule in 03 §4 passes)

| Load | Hours/day (forecast) | Basis |
|---|---:|---|
| 24/7 learning loop (planner + local reviewer) | 12 | today runs on local models; readiness run 211 cycles (07 A11) |
| Autonomy cycles on Bossman itself | 6 | M2 target: ten cycles, then continuous |
| Pilot client jobs (05) | 4 | hypothesis, only after M3 |
| **Forecast utilisation** | **22 h/day ≈ 92 %** | forecast, not measured |

The forecast becomes evidence only after two weeks of measured utilisation from the credit phase and the owner machine.
