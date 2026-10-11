# Poker training report

- source: **history**   generated: 2026-10-11T09:52:45Z
- rake: {'pct': 0.045, 'cap_chips': 20, 'no_flop_no_drop': True, 'total_chips': 428}
- elapsed: 0.01 s

## Result

- win-rate: **-114.03 bb/100** over 300 hands, 95% CI [-260.09, 32.02]
- VPIP: 10.5% (n=296, 95% CI 7.5-14.5%)
- PFR: 5.7% (n=296, 95% CI 3.6-9.0%)

## By position

| pos | hands | net bb | VPIP % | PFR % |
|---|---|---|---|---|
| BB | 50 | -122.5 | 8.7 | 8.7 |
| BTN | 50 | 4.0 | 14.0 | 4.0 |
| CO | 50 | -107.4 | 12.0 | 6.0 |
| HJ | 50 | -96.1 | 16.0 | 6.0 |
| SB | 50 | -124.5 | 2.0 | 2.0 |
| UTG | 50 | 104.4 | 10.0 | 8.0 |

Recorded mistakes: 0 (by type: {}), EV lost sum: 0.0 chips (app heuristic).

## Worth a look (heuristic)

- VPIP 10.5% is below the 18-28% heuristic band.
- PFR 5.7% is below the 14-24% heuristic band.

## Limits

- This is a training report, not advice to wager and not a promise of winnings; poker results vary widely.
- Sample: 300 hands. Win-rate intervals below ~10,000 hands are wide; a positive point estimate is not proof of an edge.
- With the observed variance about 255,981 hands are needed for a +-5 bb/100 95% interval.
- Chips are normalised to big blinds per hand; tournament chips are not money and blinds/ICM change value.
- Positions, VPIP and PFR are taken from the file as recorded; hands without a preflop action are excluded from them.
- Mistake labels / EV-lost come from the training app's own heuristic evaluator, not a solver.

## Status

- technically executed: True
- accepted by a real customer: NOT PROVEN
- payment confirmed: NOT PROVEN
