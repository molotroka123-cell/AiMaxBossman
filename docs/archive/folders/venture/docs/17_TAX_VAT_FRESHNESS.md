# Tax/VAT/regulatory freshness policy

Never treat tax numbers as timeless constants.

## Current planning facts observed 2026-08-28

VAT monitoring:
- 2,000,000 CZK;
- 2,536,500 CZK.

General corporate income-tax rate:
21%.

These must be revalidated before actionable advice.

## Data fields

- jurisdiction;
- tax year;
- rule;
- threshold/value;
- official source;
- observed_at;
- effective_from;
- effective_to;
- next review;
- confidence.

## Alerts

- projected VAT threshold within 90 days;
- annual rule changed;
- filing/payment deadline;
- tax status inconsistent with invoice configuration.

## Boundary

Venture Engine can:
- forecast;
- collect documents;
- prepare accountant questions.

It must not autonomously:
- elect tax regime;
- register VAT;
- file uncertain returns;
- send tax payments.

Material actions require professional/human review.
