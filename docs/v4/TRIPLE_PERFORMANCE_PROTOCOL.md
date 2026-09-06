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
and holdout registration remain separate work.

The numerical gate also requires each preregistered family's paired-bootstrap
95% success-delta lower bound to be at least -0.01, using the same draws as the
aggregate gate. Gains in one family cannot conceal losses in another. These
are per-family percentile intervals, not simultaneous confidence guarantees;
they do not replace independent live quality acceptance.
The evaluator cannot promote a strategy or certify an epoch.
