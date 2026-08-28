# AI/model cost governance

For each AI capability measure:
- provider/model;
- requests;
- input/output tokens or compute;
- cache;
- cost;
- latency;
- failure;
- quality KPI.

## Routing

Use cheaper/local model only if output quality remains above venture-specific
threshold.

## Cost alarms

- cost/account +20%;
- contribution margin -5 pp due AI;
- retry/failure rate increases;
- large context growth.

## Product design

Prefer deterministic code for:
- calculation;
- validation;
- formatting;
- rules with exact logic.

Use AI for:
- unstructured understanding;
- classification;
- drafting;
- research synthesis;
- complex reasoning.

“AI everywhere” creates cost and reliability risk.
