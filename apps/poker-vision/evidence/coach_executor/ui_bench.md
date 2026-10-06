| scenario | locator | trials | ok | refused (no click) | dropped (stale) | **wrong click** | click not confirmed | latency p50/p95 ms | RSS MB | STOP returns s |
|---|---|---|---|---|---|---|---|---|---|---|
| find_button | vision | 8 | 8 | 0 | 0 | **0** | 0 | 1099/1568 | 93 | 0.27 |
| find_button | dom_oracle | 7 | 7 | 0 | 0 | **0** | 0 | 995/1126 | 104 | 0.32 |
| enter_amount | vision | 8 | 5 | 0 | 0 | **0** | 3 | 2565/2641 | 119 | 0.23 |
| enter_amount | dom_oracle | 8 | 2 | 1 | 0 | **0** | 5 | 2572/2572 | 118 | 0.37 |
| move_before | vision | 8 | 7 | 1 | 0 | **0** | 0 | 1114/2953 | 120 | 0.2 |
| move_before | dom_oracle | 8 | 8 | 0 | 0 | **0** | 0 | 1084/3166 | 119 | 0.12 |
| animation | vision | 8 | 8 | 0 | 0 | **0** | 0 | 589/646 | 119 | 0.26 |
| animation | dom_oracle | 8 | 6 | 0 | 2 | **0** | 0 | 702/2046 | 123 | 0.27 |
| latency | vision | 8 | 8 | 0 | 0 | **0** | 0 | 1646/3630 | 122 | 0.24 |
| latency | dom_oracle | 8 | 7 | 0 | 0 | **0** | 1 | 1495/2258 | 110 | 0.26 |
| hand_change | vision | 8 | 0 | 0 | 8 | **0** | 0 | — | 122 | 0.43 |
| hand_change | dom_oracle | 8 | 0 | 0 | 8 | **0** | 0 | — | 124 | 0.26 |
| stop_race | vision | 1 | 0 | 1 | 0 | **0** | 0 | — | 125 | 0.0 |
| stop_race | dom_oracle | 1 | 0 | 1 | 0 | **0** | 0 | — | 120 | 0.0 |
| move_race | vision | 8 | 0 | 8 | 0 | **0** | 0 | — | 104 | 0.43 |
| move_race | dom_oracle | 8 | 0 | 8 | 0 | **0** | 0 | — | 111 | 0.21 |
| resize | vision | 8 | 0 | 8 | 0 | **0** | 0 | — | 116 | 0.29 |
| resize | dom_oracle | 8 | 0 | 8 | 0 | **0** | 0 | — | 116 | 0.37 |
| overlap | vision | 8 | 0 | 8 | 0 | **0** | 0 | — | 120 | 0.32 |
| overlap | dom_oracle | 9 | 0 | 9 | 0 | **0** | 0 | — | 123 | 0.41 |
| dpr2 | vision | 0 | 0 | 0 | 0 | **0** | 0 | — | 138 | 0.61 |
| dpr2 | dom_oracle | 0 | 0 | 0 | 0 | **0** | 0 | — | 135 | 0.83 |
