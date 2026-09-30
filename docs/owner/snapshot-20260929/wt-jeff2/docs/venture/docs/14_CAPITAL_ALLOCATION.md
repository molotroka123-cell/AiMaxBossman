# Capital allocation policy

## Default limits

Discovery:
<=1,000 CZK/opportunity.

Validation:
<=5,000 CZK/ordinary experiment.

Pre-payment external build:
<=20,000 CZK/venture.

>50,000 CZK cumulative external spend:
mandatory investment review.

## Investment review

Required:
- actual revenue;
- actual collected cash;
- contribution margin;
- acquisition;
- retention;
- support;
- bear/base/bull;
- remaining unknowns;
- runway;
- next milestone;
- kill condition.

## Portfolio capacity

Start:
1 build + 1 research.

Do not fund a third active build because an agent has spare tokens.

## R&D cash multiple

`cumulative contribution margin /
 cumulative external validation+development cash`

Track over time.

## Engineering shadow cost

Internal development is not economically free.

Use configurable shadow CZK/day to compare opportunities.

---

# V1.1 AMENDMENT — совокупный потолок расхода

## A-8. Лазейка «девять экспериментов по 5 000»

Лимиты были заданы **на эксперимент**, а обязательный инвестиционный обзор — на
совокупные 50 000 CZK. Между ними ничего не стояло: девять валидационных
экспериментов по 5 000 CZK — это 45 000 CZK, потраченных без единого обзора,
формально по правилам.

Добавлены промежуточные потолки:

| Потолок | Значение | Что срабатывает |
|---|---:|---|
| На эксперимент | 5 000 CZK | как было |
| **На венчур, накопительно до первого платежа** | **15 000 CZK** | промежуточный обзор: что узнали за эти деньги |
| **На портфель в календарный месяц** | **10 000 CZK** | пауза до решения владельца |
| Накопительно внешние | 50 000 CZK | инвестиционный обзор, как было |

Промежуточный обзор на 15 000 CZK отвечает на один вопрос: **какая гипотеза
закрыта этими деньгами**. Если ответ «уточнили формулировку» — это KILL, а не
следующий эксперимент.
