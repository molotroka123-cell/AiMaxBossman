# KPI dictionary

## Revenue

MRR:
normalized recurring monthly subscription revenue.

ARR:
12 × current normalized MRR; not collected cash.

Net revenue:
gross billed/collected according to defined accounting view minus discounts,
refunds and taxes excluded from revenue where applicable.

## Contribution

Net revenue minus directly variable product delivery costs.

## ARPA

Average recurring revenue / active paying account.

## CAC

Channel spend / attributable new paying accounts.

Report cash and fully loaded CAC separately.

## Payback

CAC / monthly contribution per new account.

## Activation

First event strongly correlated with receiving product value.

## Retention

Cohort share repeating the defined value event or remaining paid.

## Churn

Logo churn and MRR churn separately.

## Support load

Minutes of human support / active account / period.

## TTR

Approval of validation -> first real external payment.

## Learning efficiency

Experiment spend / high-value uncertainty resolved.

## Metric integrity

Every KPI definition must specify:
- numerator;
- denominator;
- time window;
- exclusions;
- data source.

---

# V1.1 AMENDMENT — двусмысленность TTR устранена

## A-9. Когда именно запускается счётчик 45 дней

`TTR: одобрение валидации → первый внешний платёж`, цель ≤ 45 дней. При этом
`29_ZIVNOPILOT_CALIBRATION_CASE.md` планирует первые платные пилоты на **день
60**. Противоречия здесь нет — но только если «одобрение валидации» приходится
примерно на день 30, а этого нигде не сказано.

Разница существенная: при отсчёте от дня 0 план нарушает цель, при отсчёте от
дня 30 — укладывается с запасом. Двусмысленность, которая решает, провален KPI
или нет, — это не мелочь.

**Правило V1.1.** Счётчик TTR запускается в момент, когда владелец **письменно
утверждает валидационный эксперимент с бюджетом**. Это событие фиксируется
датой в журнале экспериментов. Дни открытия и подготовки в TTR не входят и
учитываются отдельной метрикой `time_to_validation_approval`.

Обе метрики публикуются вместе. Иначе оптимизируется одна за счёт другой:
бесконечная подготовка при идеальном TTR.
