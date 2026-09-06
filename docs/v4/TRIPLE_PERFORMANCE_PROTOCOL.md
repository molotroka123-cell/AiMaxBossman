# Offline performance evaluator

`bossman_shared.epoch4_metrics.evaluate` implements arithmetic for supplied,
preregistered paired serial measurements. It performs no I/O or model calls.
It checks the exact pair manifest, frozen configuration, commit identifiers,
finite observations, failed attempts, sample size and stratified paired
bootstrap intervals. Mandatory approvals are counted separately; zero baseline
interventions must remain zero. Sparse denominators and unknown costs return
INSUFFICIENT_EVIDENCE.

MET means only that the supplied numbers meet numerical targets. Every result
sets `certified=false` and `source_trust=UNVERIFIED_REPORTED_MEASUREMENTS`.
Provenance references and reported success/security outcomes are not
authenticated. Release evidence must independently validate all source
measurements, run the security/quality gates and bind the measured configuration.

Tests use synthetic data, never actual Bossman benchmark results. Current
threefold system-performance verdict: NOT_MEASURED. Parallel wall-time
measurement, local resource-vector accounting, authentic evidence ingestion,
holdout registration and family-level quality acceptance remain separate work.
The evaluator cannot promote a strategy or certify an epoch.
