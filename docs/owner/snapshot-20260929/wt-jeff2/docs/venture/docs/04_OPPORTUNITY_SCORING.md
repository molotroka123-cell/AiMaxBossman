# Opportunity scoring

Score each dimension 0–10.

| Dimension | Weight |
|---|---:|
| Pain severity | 15 |
| Proven willingness to pay | 13 |
| Regulatory/market trigger | 10 |
| Distribution accessibility | 11 |
| Automation advantage | 10 |
| Time to first revenue | 9 |
| Gross-margin potential | 7 |
| Retention/frequency | 7 |
| Competitive whitespace | 6 |
| Data/integration accessibility | 5 |
| Defensibility | 4 |
| Strategic fit | 3 |

Base:
weighted average / 10.

## Confidence multiplier

| Evidence quality | Multiplier |
|---|---:|
| paid evidence + strong primary data | 1.00 |
| interviews + primary + competitor evidence | 0.90 |
| primary data + strong indirect evidence | 0.80 |
| mostly secondary | 0.70 |
| hypothesis-heavy | 0.60 |
| speculation | <=0.50 |

Final score:
`base_score × confidence_multiplier`

## Hard blockers

Independent of score:
- no payer;
- unlawful planned acquisition;
- required licence unavailable;
- core data unavailable;
- structural gross-margin failure;
- disproportionate liability;
- market commoditized below sustainable price;
- product requires custom build for every customer;
- no plausible first-payment path.

## Decision bands

8.0–10.0:
immediate deep validation

7.0–7.99:
validate when capacity exists

6.0–6.99:
improve evidence / monitor

5.0–5.99:
park

<5.0:
reject unless trigger changes.

## Anti-gaming

The agent must display:
- every component score;
- source/evidence behind score;
- confidence;
- blocker state;
- missing evidence.

No single composite number may conceal a score <=3 on payer, legality or access.

---

# V1.1 AMENDMENT — полосы решений считаются ВНУТРИ стадии

**Дефект, который это чинит (A-1, структурный).** Множитель доверия не зависел
от стадии, а полосы решений применялись к итоговому баллу. На стадии открытия
доказательств по определению нет: лучший класс там — «hypothesis-heavy», то есть
множитель **0.60**. Значит потолок итогового балла у идеальной возможности на
Gate 0 равен `10 × 0.60 = 6.0`, а полоса 6.0–6.99 — это «улучшать доказательства
/ наблюдать».

Следствие: **две верхние полосы недостижимы до появления платящего клиента, и
движок не мог бы запустить ничего вообще.** Каждая новая возможность вечно
падала в «park/monitor» — при этом формально «по правилам».

## Исправление

Полоса выбирается по стадии, а не по одной шкале на всё.

### Gate 0 — открытие (доказательства класса D/E)

Решение принимается по **базовому баллу**, множитель записывается, но полосу не
определяет.

| Базовый балл | Решение |
|---|---|
| ≥ 7.5 | в Gate 1 немедленно |
| 6.5–7.49 | в Gate 1 при наличии ёмкости |
| 5.0–6.49 | доработать гипотезу, повторная оценка |
| < 5.0 | park |

Жёсткие блокеры действуют как прежде и старше любого балла.

### Gate 1 и далее — есть первичные данные

Полосы применяются к **итоговому баллу** (`базовый × множитель`) в редакции
основного документа. Это корректно: с Gate 1 доступен множитель ≥ 0.80, и
верхние полосы становятся достижимы.

## Почему не подняли множитель вместо этого

Поднять «hypothesis-heavy» с 0.60 до, скажем, 0.85 означало бы врать самим себе
о качестве доказательств. Проблема не в множителе — он честный. Проблема в том,
что одну и ту же линейку прикладывали к разным стадиям.
