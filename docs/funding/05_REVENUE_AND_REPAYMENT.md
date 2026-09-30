# 05 — Revenue and repayment / Выручка и погашение

**Traction today: none.** No customers, no pilots, no revenue, no users outside the owner (07 G7). Everything below is a
**HYPOTHESIS** to be tested at milestone M3, labelled as such. Revenue actions (outreach, offers, invoices) are
user-gated by the constitution ("starting a new way of earning money", "messages to third parties").

## 1. Pilot workflows (candidates for M3)

| # | Workflow | What Bossman does | Readiness evidence | Status |
|---|---|---|---|---|
| P1 | Bounded bug-fix / small feature for a small team's repository | isolated clone, writer CLI, tests, dual review, patch + evidence pack; client merges | loop design (02); `tools/coding_value_sim.py` harness | NOT_READY until M1/M2 (G1–G3, G6) |
| P2 | Local-first admin-inbox triage for a small clinic/salon (RU/CZ/EN messages; medical → safe referral) | local model classifies business/intent/action, no data leaves the machine | 52-item held-out set, 50–52/52 exact, 0 safety violations, $0 (07 A8) — fake data only | pilot-ready candidate after owner review; related owner projects marked PARTIAL (SWAPME, FRESH_VIBES) |
| P3 | Repository health / performance audit report | measured benchmark harness, before/after report, honest "did not improve" section | `docs/audits/PERF_20260929.md` (07 A12) | demonstrable on own code |

## 2. Pricing hypotheses (HYPOTHESIS — not offered, not validated)

| Workflow | Hypothesis | Test to validate |
|---|---|---|
| P1 | fixed price per accepted change, H1 = $50–$300 depending on size; client pays only when hidden tests pass and they merge | 3 unpaid rehearsals, then 3 paid pilots with owner approval |
| P2 | monthly subscription per business, H2 = $49–$199/month, runs on client's or owner's local machine | 1 business, 30 days, measured triage accuracy and time saved |
| P3 | fixed audit fee, H3 = $300–$1,500 | 2 audits; client confirms the measured improvement |

Kill criteria: a hypothesis is dropped if 3 qualified prospects decline at that price, or if the measured
cost per accepted change (03) exceeds 50 % of the price.

## 3. Repayment model for equipment leasing (formula, no invented price)

DGX Station GB300 price is **UNVERIFIED** (not published by NVIDIA; OEMs ASUS, Dell, Exxact, Gigabyte, HP, MSI,
Supermicro — [nvidia.com DGX Station](https://www.nvidia.com/en-us/products/workstations/dgx-station/), accessed
2026-09-29). Use the OEM quote `P` once obtained (09).

Monthly payment for financed amount `F` at monthly rate `i` over `n` months: `M = F · i / (1 − (1 + i)^−n)`.
Reference per $10,000 financed (arithmetic only, rates are assumptions, not offers):

| APR (assumed) | 24 months | 36 months | 48 months |
|---|---:|---:|---:|
| 8 % | $452.27 | $313.36 | $244.13 |
| 12 % | $470.73 | $332.14 | $263.34 |
| 18 % | $499.24 | $361.52 | $293.75 |

Coverage rule (hypothesis): monthly gross margin from pilots ≥ 1.5 × M before signing, where
monthly margin = Σ(price − measured cost per accepted change) + Σ(subscription − local running cost).
Cloud API spend avoided is **not** counted as revenue; it is a saving reported separately (AGENTS.md: revenue,
deliverable value and saved operating cost are separate metrics).

Required pilots at hypothesis H1 midpoint ($175 per change, assumed 50 % margin ≈ $87.50):
changes/month needed = 1.5 × M / 87.5. Illustration with F = $100,000 (an arbitrary round number, **not** a price),
36 months, 12 % → M = $3,321.43 → ≈ 57 accepted changes/month. This shows the scale gap honestly: leasing is realistic only after P1/P2 show repeatable paid demand.

## 4. Sequencing

1. Credits (06) → benchmark (03) → decision M4.
2. M3 pilots with owner approval → measured margin.
3. Only then: OEM quote, lease/credit application (owner signs; never an agent).
