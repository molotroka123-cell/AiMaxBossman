# Venture stage gates

## Gate 0 — evidence scan

Duration:
1–3 days.

Output:
opportunity object + top unknowns.

Pass:
payer identifiable and no hard blocker.

## Gate 1 — problem proof

Target:
10–20 qualified interviews or equivalent first-party evidence.

Default pass:
>=60% independently describe the pain/workaround.

Rules:
- no leading questions;
- record disconfirming evidence;
- segment respondents.

## Gate 2 — payment proof

Preferred:
- paid pilot;
- deposit;
- paid concierge;
- paid beta.

Micro/SMB default:
3 independent paying customers.

Higher-ticket B2B:
one meaningful paid pilot may justify limited build.

“Sounds useful” is not payment proof.

## Gate 3 — repeat-use proof

Identify an activation and retention event.

Pass example:
>=50% of paid pilot customers repeat the core workflow.

## Gate 4 — acquisition proof

At least one channel produces paying customers within acceptable cash payback.

## Gate 5 — scale

Requirements:
- actual gross margin known;
- retention measured;
- support burden measured;
- cash controls;
- legal/compliance path;
- acquisition source.

## Bypass

Any bypass requires:
- owner approval;
- written exception;
- reason;
- maximum capital;
- deadline;
- kill metric.

No permanent “strategic exception”.

---

# V1.1 AMENDMENT — сроки убийства и определение платящего клиента

## A-6. У каждого гейта появился дедлайн

Собственный чек-лист пака (`49_OPUS_SECOND_PASS_CHECKLIST.md`) называет «no kill
date» режимом отказа — и пак этому режиму соответствовал: критерии KILL
перечислены, дата не задана ни у одного. Без даты HOLD становится кладбищем, где
ничего не умирает и всё занимает ёмкость портфеля.

| Гейт | Дедлайн по умолчанию | Что происходит по истечении |
|---|---|---|
| Gate 0 | 5 рабочих дней | park или KILL |
| Gate 1 | 30 дней с утверждения | KILL, если < 60 % подтверждают боль |
| Gate 2 | 45 дней с утверждения | KILL, если нет ни одного платежа |
| Gate 3 | 60 дней после первого платежа | пересмотр цены/ICP или KILL |
| Gate 4 | 90 дней | KILL, если ни один канал не даёт платящих в пределах окупаемости |
| HOLD | **максимум 90 дней** | автоматически KILL, если триггер пересмотра не сработал |

Продление дедлайна = письменное исключение с новой датой и максимальным
расходом. Молчаливого продления не существует.

## A-7. «3 независимых платящих клиента» — закрыты лазейки

Формулировка была геймабельна: три ИП одного человека, три знакомых или три
платежа по 1 CZK формально её удовлетворяли.

Платёж засчитывается в Gate 2, только если выполнено **всё**:

- плательщик не связан с владельцем (не родственник, не сотрудник, не
  совладелец, не второй субъект того же человека);
- цена ≥ **50 % от прайса** соответствующего тарифа;
- деньги **фактически поступили** и не возвращены в течение **30 дней**;
- плательщик знал, что платит за продукт, а не «поддерживает начинание»;
- платёж не является взаимозачётом за услугу владельца.

Три платежа от одного юридического лица считаются **одним**. Пилот со скидкой
> 50 % — это не доказательство платежа, а платное консультирование; его можно
проводить, но в Gate 2 он не засчитывается.
