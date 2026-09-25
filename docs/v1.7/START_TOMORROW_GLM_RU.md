# START TOMORROW — Bossman 1.7 PIT / Jeff

Работай только в:
`feat/bossman-1.7-personal-identity-training-20260925`

## Сначала

1. Прочитай `docs/v1.7/README.md` — это главный контракт.
2. Прочитай `GLM_5_3_IMPLEMENTATION_MASTER_RU.md`, `JEFF_PUBLIC_BEHAVIOR_RU.md`, `LAPTOP_REMOTE_RUN_RU.md`.
3. Запусти PIT foundation tests + compileall.
4. Не трогай 1.5/1.6/release.
5. Не создавай второй Bossman backend.

## Обязательный runtime

Вся работа идёт:
Telegram → Bossman → PIT policy → Jev/provider registry → answer → Bossman learning.

Никаких прямых Telegram→OpenRouter/Claude/GLM обходов Bossman.

## Owner CMD

К концу стартовой интеграции должны реально работать:

`bossman pit start`
`bossman pit status`
`bossman pit doctor`
`bossman pit stop`

Это основной owner launch path. Отдельный Python launcher может быть внутренним implementation detail, но не инструкцией владельцу.

## Laptop routing

- local unavailable;
- GLM-5.3 — primary configured route, только если runtime catalog подтверждает zero-cost;
- fallback — только allowlisted zero-cost models;
- unknown/non-zero pricing = route rejected;
- paid = OFF;
- fresh/current → web read/search.

## Storage

Только локально:

`<BOSSMAN_DATA_DIR>/pit-v1.7/personalities/<person_key>/`

Модели не имеют direct persona/filesystem tools. Bossman broker выдаёт только bounded transient context.

## Jeff

- публичное имя Jeff;
- model/provider не раскрывать;
- owner data никогда не раскрывать;
- другие ID не видны;
- hidden location отсутствует;
- внутренний PIT/1.7 не раскрывать;
- публичный Bossman/GitHub можно объяснять точно.

## Risk

На identity/provider/owner/other-person/location/internal-stage probing:
- `risk_delta=+1`;
- сохранить через локальный `RiskLedger`;
- risk не входит в model context/export;
- collection-first может слегка увеличить benign discovery cadence;
- максимум один optional non-sensitive follow-up.

## Capabilities

До owner shadow доказать:
- chat;
- web + sources;
- vision по присланному изображению;
- safe uploaded-file understanding;
- code reasoning;
- calculator;
- summaries/translation;
- own memory.

Image generation на ноутбуке не фейкать. Ответ:
**«Скоро научусь, малышка 😊»**

Настоящая генерация включается только после AI Max live PASS.

## Isolation

Новый ID = zero-start.
Owner ID внутри PIT = participant.
Two-ID synthetic isolation — до первого реального знакомого.

## Learning

После каждого содержательного разрешённого turn:
answer → candidate extraction → provenance → high-recall collection → outcomes → future sorter labels → optional discovery.

Это не claim, что веса уже переобучаются после каждого сообщения.

## Finish

Создай `docs/v1.7/evidence/LAPTOP_SHADOW_REPORT.md`.

Статус `PIT_LAPTOP_SHADOW_READY` только если:
- `bossman pit start` реально запускает;
- живой Telegram Jeff answer;
- free-only routing;
- web + vision minimum;
- local storage;
- two-ID isolation;
- risk local-only;
- restart/idempotency;
- export/delete;
- no open P0.

После этого тот же runtime переносится на AI Max и включается `local_first_auto`.
