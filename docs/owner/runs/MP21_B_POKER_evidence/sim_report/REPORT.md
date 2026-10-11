# Poker training report

- source: **simulation**   generated: 2026-10-11T09:52:55Z
- rake: {'pct': 0.05, 'cap_bb': 3.0, 'no_flop_no_drop': True}
- elapsed: 7.93 s

## Result

- win-rate: **61.883 bb/100** over 3000 hands, 95% CI [-12.296, 136.062]
- VPIP: 21.6% (n=3000, 95% CI 20.2-23.1%)
- PFR: 10.0% (n=3000, 95% CI 8.9-11.1%)
- aggression factor: 1.0, WTSD: 80.2%, W$SD: 50.5%

## Table

| bot | hands | bb/100 | 95% CI |
|---|---|---|---|
| Student (start) | 3000 | 61.883 | [-12.296, 136.062] |
| Rock | 3000 | 3.283 | [-43.439, 50.006] |
| TAG Trainer | 3000 | 44.1 | [-8.469, 96.669] |
| Calling Station | 3000 | -47.467 | [-143.708, 48.775] |
| Maniac | 3000 | -207.317 | [-328.012, -86.621] |
| Scared Money | 3000 | 61.267 | [9.318, 113.215] |

rake total: 5055 chips (84.25 bb per 100 hands, whole table); chip conservation: True; seed=11

## Worth a look (heuristic)

- PFR 10.0% is below the 14-24% heuristic band.
- VPIP-PFR gap 11.6 points: many calls relative to raises (passive/limp-heavy).

## Limits

- This is a training report, not advice to wager and not a promise of winnings; poker results vary widely.
- Sample: 3000 hands. Win-rate intervals below ~10,000 hands are wide; a positive point estimate is not proof of an edge.
- Opponents are scripted training profiles, not people; results measure style vs those profiles only.
- Hero equity inside the policy is computed against RANDOM hands, not realistic ranges.
- Seeded and duplicate-dealt: the same seed and settings reproduce the same numbers exactly.

## Status

- technically executed: True
- accepted by a real customer: NOT PROVEN
- payment confirmed: NOT PROVEN
