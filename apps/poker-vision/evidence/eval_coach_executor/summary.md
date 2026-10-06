profile `poker_train@46bb2f32` calibrated on ['A_tr1', 'A_tr2', 'A_tr3', 'A_tr4', 'A_tr5', 'A_tr6', 'P_tr1', 'P_tr2', 'P_tr3', 'P_tr4', 'P_tr5', 'P_tr6', 'P_tr7', 'P_tr8', 'P_tr9', 'P_tr10'] (928 frames). Cells: wrong / unknown / scored.

### test_in  (sessions A_te1, A_te2, A_te3; 339 scored frames; deal overlap with train: 0)

| field | baseline (naive, no UNKNOWN discipline) | after |
|---|---|---|
| hero_cards | 0/6/339 | 0/6/339 |
| board | 15/1/339 | 0/16/339 |
| pot | 0/1/339 | 0/1/339 |
| to_call | 139/61/316 | 0/62/178 |
| hero_stack | 26/0/339 | 0/74/339 |
| street | 15/1/339 | 0/16/339 |
| seat_stacks | 124/49/339 | 2/83/339 |
| bets | 323/14/339 | 0/166/339 |
| dealer | 0/20/65 | 0/20/65 |
| actions | 16/51/339 | 0/57/339 |
| hero_turn | 0/51/339 | 0/41/339 |

exact-match state (all core fields right): baseline 0.307, after 0.271 · state with no wrong core field: baseline 0.555, after 1.000
latency per frame (CPU): p50 61.5 ms, p95 81.4 ms

### test_theme  (sessions B_ept, B_wpt, B_daily, B_main, B_turbo, B_k100; 403 scored frames; deal overlap with train: 0)

| field | baseline (naive, no UNKNOWN discipline) | after |
|---|---|---|
| hero_cards | 0/0/403 | 0/0/403 |
| board | 34/0/403 | 0/34/403 |
| pot | 0/0/403 | 0/0/403 |
| to_call | 121/52/272 | 0/82/173 |
| hero_stack | 33/24/403 | 0/239/403 |
| street | 32/2/403 | 0/34/403 |
| seat_stacks | 0/306/403 | 0/306/403 |
| bets | 399/1/403 | 0/188/403 |
| dealer | 0/23/103 | 0/23/103 |
| actions | 62/72/403 | 1/107/403 |
| hero_turn | 1/72/403 | 1/46/403 |

exact-match state (all core fields right): baseline 0.226, after 0.099 · state with no wrong core field: baseline 0.603, after 1.000
latency per frame (CPU): p50 61.2 ms, p95 81.3 ms

### test_layout  (sessions C_phone3x, C_dpr2, C_desk, C_tab; 345 scored frames; deal overlap with train: 0)

| field | baseline (naive, no UNKNOWN discipline) | after |
|---|---|---|
| hero_cards | 9/3/345 | 0/58/345 |
| board | 39/36/345 | 0/81/345 |
| pot | 13/44/345 | 0/93/345 |
| to_call | 146/55/281 | 0/91/171 |
| hero_stack | 0/16/345 | 0/87/345 |
| street | 22/53/345 | 0/75/345 |
| seat_stacks | 94/151/345 | 0/229/345 |
| bets | 303/16/345 | 0/187/345 |
| dealer | 0/103/110 | 0/103/110 |
| actions | 68/270/345 | 0/338/345 |
| hero_turn | 0/338/345 | 0/338/345 |

exact-match state (all core fields right): baseline 0.212, after 0.136 · state with no wrong core field: baseline 0.487, after 1.000
latency per frame (CPU): p50 161.4 ms, p95 304.0 ms

### test_theme_layout  (sessions C_ept_phone, C_ept_desk; 167 scored frames; deal overlap with train: 0)

| field | baseline (naive, no UNKNOWN discipline) | after |
|---|---|---|
| hero_cards | 0/0/167 | 0/29/167 |
| board | 6/30/167 | 0/33/167 |
| pot | 2/60/167 | 0/97/167 |
| to_call | 41/37/108 | 0/47/84 |
| hero_stack | 2/4/167 | 0/90/167 |
| street | 3/30/167 | 0/33/167 |
| seat_stacks | 6/72/167 | 0/93/167 |
| bets | 123/23/167 | 0/91/167 |
| dealer | 0/13/13 | 0/13/13 |
| actions | 0/165/167 | 0/165/167 |
| hero_turn | 0/165/167 | 0/165/167 |

exact-match state (all core fields right): baseline 0.156, after 0.012 · state with no wrong core field: baseline 0.701, after 1.000
latency per frame (CPU): p50 138.6 ms, p95 175.8 ms

### sequence level (temporal reconciliation, hands, events) on A_te1, A_te2, A_te3

- A_te1: committed {'pot_ok': 44, 'hero_ok': 44, 'skipped_value_changed_within_last_3_frames': 51, 'pot_unknown': 7, 'hero_unknown': 7}; hands true/detected 12/11, mixed 1, split 0, ambiguous frames 10/194; events 4/36 matched, missed 32, spurious/duplicate 11
- A_te2: committed {'skipped_value_changed_within_last_3_frames': 46, 'pot_ok': 23, 'hero_ok': 23, 'pot_unknown': 7, 'hero_unknown': 7}; hands true/detected 12/11, mixed 1, split 0, ambiguous frames 10/158; events 3/39 matched, missed 36, spurious/duplicate 8
- A_te3: committed {'pot_ok': 28, 'hero_ok': 27, 'skipped_value_changed_within_last_3_frames': 43, 'pot_unknown': 7, 'hero_unknown': 8}; hands true/detected 12/11, mixed 1, split 0, ambiguous frames 18/173; events 5/42 matched, missed 37, spurious/duplicate 6

peak RSS 3702.3 MB · GPU: NOT_RUN (CPU only)
