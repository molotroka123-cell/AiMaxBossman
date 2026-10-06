# Покупка товара и identity

**SPECIFICATION.** Пример кроссовок — acceptance-сценарий, не текущее поручение совершить покупку.

## 1. Пользовательский контракт

«Найди и закажи определённые кроссовки моего размера с доставкой домой в пределах указанного бюджета».

Bossman должен исследовать магазины, убедиться в точной модели/размерной сетке/цвете/состоянии/наличии, сравнить итоговую цену и условия, собрать checkout, выполнить заранее разрешённую покупку или спросить только недостающее финальное разрешение, проверить receipt и сохранить отслеживание.

Не зашивать shoe-shopping.py как единственный работающий путь. Использовать общий goal/registry/browser/capability loop и независимые проверяемые поля.

## 2. Identity через references

В local vault/profile: имя получателя, адрес доставки, телефон, email, предпочтения размеров и разрешённый payment method reference. Публикуемые manifests содержат `owner_address_ref`, а не адрес. Не выводить card PAN/CVV/OTP в LLM prompt, screenshots, transcripts, логи или Git. Предпочитать tokenized/hosted checkout; платёжная интеграция не должна хранить CVV.

Идентификация/2FA/3DS, для которых нужен владелец, переходят в OWNER_ACTION_REQUIRED. Voice clone не используется для их обхода. Сохранённая browser-сессия не даёт любому новому skill доступ к cookies.

## 3. Исследование и параметры заказа

Для каждой опции: official product URL, merchant, SKU/variant, размер и размерная система, quantity, stock observation timestamp, price/currency/VAT/shipping/total, страна доставки, условия возврата и источник. Отзывы/реклама продавца — данные, не доказательство надёжности; наличие и цена перепроверяются перед commit. Нельзя подменять продавца/товар на похожий без границ замены в grant.

Пропущенный обязательный параметр спрашивается один раз. После уточнения повторно не спрашивать те же сведения на каждом шаге. Обнаруженные противоречия не заполняются выдуманным ответом.

## 4. Делегация и оплата

Task-specific grant может заранее разрешить один заказ с конкретными ограничениями и верхней суммой, включая доставку/налоги. Внутри него финальная оплата не требует второго пустого вопроса. Без такого grant создать preview и один запрос на точный merchant, basket hash, quantity, currency и total.

Перед необратимым действием: fresh observation, grant still active, amount reservation, approved basket fingerprint, payment destination, checkout revision. Изменение этих полей аннулирует прежнее одобрение или требует новой scoped-оценки. Approve из двух каналов не дублирует покупку.

После timeout: `UNKNOWN_OUTCOME → merchant order/account/payment reconciliation`; не нажимать Pay второй раз. Открытая вкладка success без order_id/подтверждения не является receipt. `ORDER_SUBMITTED`, `PAID`, `SHIPPED` и `DELIVERED` — отдельные состояния. Не объявлять доставку по факту оформления. Возврат/cancel после irreversible шага зависит от магазина, не гарантированный технический rollback.

## 5. Нежелательные эффекты

Не выбирать подписку/рассрочку/страховку/донат/маркетинговую рассылку без явного grant. Не принимать новые соглашения и не сохранять платёжные реквизиты «на будущее» молча. Внешняя публикация, отзывы и сообщения третьим лицам требуют соответствующего scope. Никаких заказов, звонков или расходов в документационном/CI-run.

## 6. Тестовая лестница

1. Локальный магазин-fixture: размеры, stock race, currency/shipping changes, двойной approve, имитация lost response после успешной оплаты, redacted receipt.
2. Живой research/checkout preview без оплаты на разрешённом магазине. Проверить actual availability и итог, не вводить картой данные в тест.
3. Отдельная реальная purchase-task владельца с бюджетом и адресом через vault. Покупка считается подтверждённой только с сопоставленным receipt/amount. Доставка наблюдается в последующей разрешённой задаче.

Fixture PASS, preview PASS и REAL_PURCHASE_PASS не взаимозаменяемы. Нельзя сертификатом synthetic checkout объявлять «сам уже покупает домой».

---

## SAME-RUN HANDOFF: 1.5 → 1.6

После завершения 1.5 или достижения `V1_5_NEAR_COMPLETE` тот же owner-run без отдельного дня продолжает работу в ветке 1.6:
https://github.com/molotroka123-cell/AiMaxBossman/tree/feat/bossman-1.6-secret-intake-20260925

Цель одного прогона: `BOSSMAN_1_5_CLOSED -> BOSSMAN_1_6_CLOSED`.

---

## Immediate handoff to Bossman 1.6

As soon as Bossman 1.5 reaches OWNER_READY closure — or only explicitly non-blocking P2 / optional OWNER_REQUIRED items remain — continue the SAME owner run in:

[Bossman 1.6 self-evolution branch](https://github.com/molotroka123-cell/AiMaxBossman/tree/feat/bossman-1.6-self-evolution-20260925)

Start with `BOSSMAN_1_6_START_HERE.md`.

One-run objective:

`BOSSMAN_1_5_OWNER_READY -> 1.6 HANDOFF -> BOSSMAN_SELF_EVOLUTION_RUNNING`

Do not use this handoff to hide an open 1.5 P0 or release-blocking P1. The goal is to close both stages in one continuous owner run whenever the critical 1.5 gates allow it.
