# Evidence and provenance policy

## Evidence classes

A — primary official
B — primary commercial
C — direct customer/market evidence
D — secondary/community
E — assumption

## Evidence object

Required:
- claim;
- source;
- source_type;
- observed_at;
- effective_at if applicable;
- expires_at/freshness rule;
- excerpt/measurement summary;
- confidence;
- contradiction;
- affected decisions.

## Commercial hierarchy

In most venture decisions:

real payment
> repeated product usage
> signed paid pilot
> observed workflow
> qualified interview
> survey
> online opinion.

## Freshness

Dynamic:
- competitor pricing: recheck <=30 days before decision;
- regulatory deadlines: recheck <=7 days before action;
- tax threshold: recheck at least each tax year and before material advice;
- public API: recheck before integration;
- market-size dataset: show reporting period.

## Contradictions

If two authoritative sources disagree:
- do not silently choose;
- mark CONFLICTED;
- show both;
- reduce confidence;
- block legal/financial action if material.

## External-evidence discipline

Important claims need provenance before:
- durable memory;
- score increase;
- spend approval;
- external customer claim.

## No citation laundering

A news article citing an official report does not become primary evidence.
Prefer the underlying report.
