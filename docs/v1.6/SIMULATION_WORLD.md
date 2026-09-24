# Pillar 4 — Simulation World / Digital Twin

Goal: before expensive/risky actions, simulate alternatives and expose
assumptions/uncertainty.

Domains:
- software release/load/security;
- business/cash-flow scenarios;
- infrastructure capacity/cost;
- market research and paper execution;
- workflow/provider scheduling.

## Simulation contract
Every simulation declares:
- state snapshot;
- controllable actions;
- assumptions;
- stochastic variables;
- scenario distribution;
- objective metrics;
- invalidation conditions;
- evidence class.

Outputs are scenarios, not facts.

## Market use
Historical replay/bootstrap/paper only. No live execution.
Use purged walk-forward, clustered resampling, costs/slippage/latency and regime
conditioning.

## Software use
Before release:
synthetic load + failure injection + dependency outage + rollback rehearsal.

## Business use
Run base/upside/downside/stress scenarios; never present a forecast as certain.

## Acceptance
A simulation must reproduce known historical/fixture outcomes within declared
error bounds before it can influence an automated decision.
