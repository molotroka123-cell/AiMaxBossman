# Epoch 8 — Приёмка и гейты заморозки

Студия входит в уже действующий контракт приёмки v2 (BL-069/BL-070): те же
вердикты, та же привязка улик, тот же агрегатор `tools/astra6_freeze.py`.
Новых «зелёных значков» не заводится — только строки, которые можно прочитать
в журнале задания и проверить по уликам.

## Входной гейт

- принятый кандидат по контракту v2 и замку входов (`e39610de` или новее);
- `P0 = 0`, `P1 = 0` на нём;
- baseline фазы 0 записан (import, RSS/CPU покоя, первый ответ API);
- решения владельца: провайдеры, бюджет, место ключей.

## Вердикты и строки

| Строка | Значения | Кто печатает |
|---|---|---|
| `BOSSMAN_STUDIO_CATALOG=<n> verified=<k> unverified=<m>` | числа | загрузчик каталога (bundle job) |
| `BOSSMAN_STUDIO_ACCEPTANCE=PASS\|FAIL\|OWNER_REQUIRED\|PARTIAL` | как в вечерней приёмке: FAIL > PARTIAL > OWNER_REQUIRED > PASS | `verify_windows_bundle.py` (расширение) |
| `BOSSMAN_STUDIO_LIVE=<provider>:PASS\|FAIL\|OWNER_REQUIRED` | по провайдеру; без ключа — `OWNER_REQUIRED`, код 2 | `app-support/studio_live_owner.py` из архива |
| `BOSSMAN_STUDIO_BUDGET=<spent_usd>/<cap_usd>` или `NOT_CAPTURED:<reason>` | факт расхода живой проверки | тот же шаг |
| `BOSSMAN_STUDIO_EGRESS=<n_confirmed>/<n_attempted>` | попытки отправки медиа владельца и подтверждения | тот же шаг |

Классы результатов живой проверки (никогда не сливаются):
`PASS` (байты получены, проверены, provenance полный), `FAIL:malformed`,
`FAIL:content_policy`, `OWNER_REQUIRED:unauthorized` (ключа нет/отвергнут),
`OWNER_REQUIRED:throttled` (429/квота),
`OWNER_REQUIRED:insufficient_credit` (402/нет кредитов), `FAIL:provider_down`,
`FAIL:timeout`.

## Definition of Done — `STUDIO_VERIFIED`

Кандидат может называться `STUDIO_VERIFIED` только если одновременно:

1. профиль `windows-installed` включает модули студии, `require_acceptance_results
   --profile windows-installed --source-sha` зелёный на **установленном
   архиве** (не на клоне), `results.xml` привязан к `archive_sha256 / run_id /
   harness_sha`;
2. развёртка UI `PASS`: у страницы «Студия» и просмотрщика 0 нажатий в
   категориях `dead / error / disabled_silent / vanished`;
3. каждая модель, показанная как доступная, имеет `verified_capabilities` из
   пробы на этой машине; непроверенные показаны как «не проверено»;
4. шесть причин отказа доказаны парами тестов против стаба и, при наличии
   ключа, хотя бы одно (`throttled` или `unauthorized`) — против настоящего
   провайдера;
5. provenance каждого результата полон или содержит `NOT_CAPTURED` с
   причиной; изменение завершённой записи отвергается БД;
6. байты каждого результата лежат локально, sha256 совпадает с записью, файл
   декодируется (`read_verification`);
7. бюджет: тест превышения даёт `stop`; платная модель при `free_only` не
   вызвана ни разу (проверяется по журналу стаба);
8. эгресс: без подтверждения байты владельца не отправлены;
9. числа покоя после интеграции не хуже baseline более чем на объявленный
   допуск (импорт, RSS, CPU, первый ответ) — иначе интеграция не принимается
   (§7);
10. `docs/final/CURRENT_STATE.md`, `BUG_LEDGER.md`, `KNOWN_GOOD_SHAS.json`,
    `docs/oss/README.md` (новые направления), `INSTALL.md` (как включить
    провайдер и где лежит ключ) обновлены тем же коммитом или следующим.

## Как владелец включает живую приёмку студии

До 18.09 поставляемый раннер запускался в прогоне без `--execute`, то есть
рабочего пути живой проверки не существовало вовсе: вердикт всегда был
`OWNER_REQUIRED`. Теперь путь есть и включается **только** настройками
владельца, двумя секретами репозитория:

| Секрет | Что в нём |
|---|---|
| `BOSSMAN_STUDIO_POLICY` | JSON политики: `{"enabled": true, "free_only": true, "prices": {"<id модели>": 0, "<id модели>": 0}, "cloud_budget_usd": 0, "per_job_usd": 0, "download_hosts": ["<хост CDN>"]}` |
| `BOSSMAN_STUDIO_MODELS` | два идентификатора каталога через запятую: одна модель изображения и одна видео |
| `BOSSMAN_OPENROUTER_API_KEY` | ключ владельца (уже используется живой проверкой модели) |

Пока оба первых секрета не заданы, шаг идёт прежним путём и пишет
`STUDIO_LIVE_PATH=owner_required`; с ними — `STUDIO_LIVE_PATH=execute`.

**Потратить деньги автоматически нельзя по построению, а не по обещанию.**
`validate_free_policy` в раннере требует `free_only`, ровно две модели и
**нулевые** объявленные цены и потолки; `governance.reserve` отказывает
платной модели при `free_only` и модели без объявленной цены вовсе. Любая
ненулевая цена в политике — отказ ещё до запуска. Контракт проверяется парой
тестов `tests/test_studio_live_workflow.py`: отрицательный контроль ловит
безусловный `--execute` в строке запуска.

## Как студия входит в заморозку

`astra6_freeze.py` получает отчёт `studio.json` (рядом с `live-model.json`) с
`binding` и строками выше. Правила:

- `BOSSMAN_STUDIO_ACCEPTANCE=FAIL` → блокер `studio_not_passed`;
- `BOSSMAN_STUDIO_LIVE` = `OWNER_REQUIRED` для всех провайдеров → причина
  `owner_required`, не блокер (как `live-model.json:not_passed` сегодня);
- `FROZEN` для студии возможен только при `PASS` хотя бы одного живого
  провайдера **на установленном архиве** и `STUDIO_VERIFIED` целиком;
  без живой проверки — `WINDOWS_RC_READY_OWNER_REQUIRED`, как сейчас;
- отчёт без `binding` или с чужим `source_sha` — блокер (правило BL-070).

## Условия жёсткого отказа (немедленно `NO-GO`)

- `completed` провайдера отображается в `COMPLETED` задачи без проверки байтов;
- ключ или его часть длиннее `…last4` в логах, уликах, отчётах, аргументах;
- платный вызов при политике «только бесплатные»;
- байты владельца ушли провайдеру без подтверждения;
- 429 / отсутствующий ключ / недоступная модель / nsfw / битый ответ
  представлены одним состоянием;
- скрытый повтор отправки (дубликат заявки у провайдера);
- удалён, пропущен или ослаблен существующий тест ради зелёного отчёта;
- тайм-аут поднят «на всякий случай» без замера;
- фоновый демон провайдера, живущий без задачи;
- результат в галерее без sha256 или с файлом, которого нет на диске;
- новый экран-двойник вместо страницы «Студия»;
- отчёт, где `verified` проставлен по каталогу, а не по пробе.

## Что владелец видит в конце каждой фазы

Один блок текста, без воды:

```
STUDIO PHASE: <0..5>
SOURCE SHA / ARCHIVE / SHA-256 / SIZE
WINDOWS INSTALLED ACCEPTANCE: N/N PASS (профиль windows-installed, модулей M)
STUDIO CATALOG: n моделей, verified k, unverified m
STUDIO LIVE: openrouter:<…> higgsfield:<…> comfyui:<…>
STUDIO BUDGET: spent/cap или NOT_CAPTURED
UI SWEEP: PASS/FAIL, страниц, нажатий
IDLE COST: import, RSS, CPU, first-answer — до / после
REPOSITORY-FIXABLE P0 / P1
REMAINING OWNER-ONLY ITEMS
ROLLBACK: предыдущий кандидат + архив
```
