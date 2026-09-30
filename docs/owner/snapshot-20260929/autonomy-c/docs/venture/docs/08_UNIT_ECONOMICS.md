# Unit economics

## Required actual metrics

Revenue:
- subscription;
- usage;
- setup;
- services;
- discounts;
- refunds;
- net collected.

Variable cost:
- inference;
- OCR;
- API;
- storage;
- bandwidth;
- e-mail/SMS;
- payment fees;
- customer-specific compute;
- support attributable to account.

Contribution:
`net revenue - variable costs`

Contribution margin:
`contribution / net revenue`

## Target bands

Low-compute SaaS:
>85% mature target.

AI-heavy SaaS:
>70% early;
>80% mature preferred.

Service-assisted:
>50% pilot is acceptable if automation path is credible.

## CAC

Cash CAC:
cash acquisition spend / new paid customers.

Fully loaded CAC:
cash acquisition + attributable sales labor / new paid customers.

Track by channel.

## Payback

Low-ticket SaaS:
<=4 months preferred;
<=6 months acceptable after retention proof;
>9 months requires exceptional retention/expansion economics.

## LTV

Early:
use capped 12-month contribution LTV.

Do not use infinite churn formula on tiny cohorts.

Mature:
show cohort size before using churn-based LTV.

## LTV:CAC

>=3 acceptable;
>=4 strong;
<2 not scalable without correction.

## Time to Revenue

Target for micro/SMB:
<=45 days from validation approval to first external payment.

---

# V1.1 AMENDMENT — база цены, стоимость поддержки, теневая ставка

## A-2 (структурный). Цена без указания базы — это не цена

Ни одна ценовая гипотеза пака не говорит, **s DPH или bez DPH**. Для чешского
микробизнеса это первый вопрос покупателя, а для нас — расхождение в 21 %.

`docs/45_FINANCIAL_CONTROL_MODEL.md` уже запрещает считать НДС выручкой. Но
`numbers/cac_payback_matrix.csv` применяет маржу прямо к цене: `349 × 0.70 =
244.3`. Если 349 CZK — цена **s DPH**, то чистая выручка равна `349 / 1.21 =
288.4`, а вклад при 70 % — **201.9 CZK, а не 244.3**. Окупаемость при CAC
1 000 CZK становится **4.95 месяца вместо 4.09** и выходит за целевые «≤ 4».

**Правило V1.1.** Все ценовые гипотезы пака объявляются **bez DPH (нетто)**.
Цена, названная клиенту s DPH, приводится к нетто делением на действующую ставку
ДО расчёта вклада. Каждая ценовая клетка эксперимента обязана нести поле
`price_basis: net | gross` — без него эксперимент не утверждается.

Отдельной строкой в переменные затраты добавляется **комиссия платёжного
провайдера**: при ARPA 249–499 CZK фиксированная часть комиссии материальна и в
процентной марже невидима.

## A-3 (структурный). Поддержка определена, но нигде не посчитана

`08` относит поддержку к переменным затратам, `24` определяет «минуты поддержки
на аккаунт». Ни одна таблица пака её не содержит. Между тем именно она решает
судьбу маржи на этих ценах.

Считаем от целевой маржи, а не наоборот. ARPA 349 CZK нетто, цель вклада 70 %:

| Статья | CZK/аккаунт/мес |
|---|---:|
| Чистая выручка | 349.0 |
| Бюджет переменных затрат (30 %) | 104.7 |
| — вывод модели, API, хранение (оценка) | ~25 |
| — комиссия платежей (~2.5 % + фикс) | ~15 |
| **Остаток на поддержку** | **~65** |

При теневой ставке владельца 500 CZK/час это **≈ 7.8 минуты на аккаунт в
месяц**. Не «примерно немного» — семь минут.

**Правило V1.1.** Порог поддержки — kill-критерий, а не наблюдение: если
средняя поддержка превышает `(бюджет вклада − прочие переменные) / теневая
ставка` два месяца подряд, венчур уходит на пересмотр цены или на KILL.
Микро-SMB, звонящий дважды в месяц по поводу счёта, экономику этой цены
уничтожает — и знать это надо до найма, а не после.

## A-4. Теневая ставка разработки — задан дефолт

`14_CAPITAL_ALLOCATION` требовал «настраиваемую теневую ставку CZK/день» без
значения по умолчанию. Величина без дефолта на практике не заполняется никогда.

**Дефолт V1.1: 4 000 CZK/день** (≈ 500 CZK/час) как стоимость дня владельца.
Число приблизительное и заменяется владельцем, но обязано присутствовать в
каждом сравнении возможностей и в расчёте поддержки выше. Сравнение
возможностей без стоимости времени — это сравнение по одной стороне уравнения.

## A-5. Матрица окупаемости — это ВЫХОД, а не ВХОД

`numbers/cac_payback_matrix.csv` перебирает CAC от 500 до 5 000 CZK, но в паке
нет ни одного наблюдения о реальном CAC чешского микро-B2B SaaS. Матрица без
якоря — приглашение выбрать оптимистичную колонку.

**Правило V1.1.** Утверждение об окупаемости запрещено, пока CAC не **измерен**
в эксперименте Gate 4. До этого матрица используется только для обратного
вопроса — «при каком CAC эта цена перестаёт работать», — и ответ идёт в
стоп-условие эксперимента, а не в прогноз.
