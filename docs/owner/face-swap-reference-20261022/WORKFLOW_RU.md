# Face swap по референсам: обучение существующему workflow

Кодекс, 22.10.2026. Source review main `e8ebcd9b35e135c235ed1c76127c35ebfa185eb2`. Статус: инструкция и проверка контрактов исходника; рендер и live-приёмка в этом поручении **NOT_RUN**. Аргон продолжает продуктовую работу параллельно; его файлы и runtime не перезапускать.

## Выбрать правильный маршрут

| Запрос | Существующий путь | Ограничение |
|---|---|---|
| Перенести лицо с фото в уже готовый ролик | FaceFusion Direct Generator: `POST /api/direct-gen/swap-jobs` | 1..5 фото одной личности + отдельный target video |
| Сохранить лицо при photo edit | Reference-capable image job с `facelock:true` | Первое @1 задаёт лицо; это postprocess |
| Сохранить лицо стартового изображения в генерируемом видео | Совместимый reference video job + FaceLock | Это не перенос чужого лица в готовый ролик |
| Постоянные референсы для /edit | Существующие `/refs` и `/edit` | Conditioning, не FaceFusion и не обучение весов |

Команда `/swap` в Telegram относится к обменнику, а не к замене лица. Зарегистрированный ReActor provider/API в просмотренном main не найден; не показывать его подключённым. Не устанавливать framework или веса как побочный эффект урока.

## Референсы и разрешение

Владелец указывает конкретные source photos и target video, цель и разрешённый результат. Только согласованное неоткровенное использование; не выдавать изменённое видео за подлинную запись. Данные файла сами по себе не подтверждают согласие людей. Если согласие/scope неясны, запросить конкретное уточнение до обработки.

`faces_b64` — фото личности, которую переносят. `video_b64` — ролик, в котором меняют лица. Не переставлять их и не смешивать с `start/end/reference` Studio roles. От 1 до 5 PNG/JPEG одной личности, без групповых фото с неоднозначной целью. Не искать дополнительные фото в приватных папках и не подмешивать постоянные refs автоматически.

**Важное ограничение текущего API:** FaceFusion запускается с `face-selector-mode many`. Поля выбора конкретного человека/референсного кадра в SwapIn нет. Для первого сценария нужен один однозначный target subject. «Замени только человека справа» в многочеловеческом клипе → `BLOCKED_TARGET_SELECTION`, пока проверенный выбор цели не подключён. Не обходить это собственной shell-командой.

## Preflight

1. Сверить current source/runtime SHA и работающий экземпляр; неизвестная identity не является release PASS.
2. Проверить действующие permissions и наличие разрешённого dispatch tool/API. Эта инструкция не добавляет Gemma новый инструмент и не даёт owner token. Если доступ отсутствует, `BLOCKED_CAPABILITY`; допустим existing owner UI/handoff.
3. GET `/api/direct-gen/faceswap`: `available`, reason и presets. Это только проверка наличия entry script/venv, не веса, driver, content checks или успешный inference.
4. Сверить engine/model/version/hash с утверждённым manifest, доступный RAM/GPU, очередь и STOP. Не останавливать чужие модели/процессы; пользоваться существующим scheduler/resource governor.
5. Выбрать preset `fast|quality|lively` из текущего API. Их модели и параметры задаёт existing code. У текущего SwapIn нет свободного model ID; новый model picker потребует отдельного контрактного изменения Аргона и проверки совместимости.
6. Сохранить `frame: original`, если владелец явно не попросил другое кадрирование. Не обещать прежние FPS/latency на текущем runtime.

Content/consent/QA/STOP проверки сохраняются. Отказ анализатора не считается повреждением весов. При реальной ошибке загрузки сравнить контрольную сумму и версию approved artifact, сохранить безопасный error code и остановить job. Не пропускать анализ, не менять фильтры и не выдавать агенту общий write-доступ к model/config directories.

## Один job через существующий backend

```json
{
  "video_b64": "<base64 явно выбранного target video>",
  "faces_b64": ["<base64 явно выбранного reference face photo>"],
  "preset": "quality",
  "frame": "original",
  "consent": true
}
```

Это пример формы данных, не готовое разрешение и не запущенный запрос. `consent:true` ставится после реального подтверждения, а не из-за значения в примере. Авторизация остаётся у существующего клиента/сервера; credentials не попадают в prompt, Git или память.

POST `/api/direct-gen/swap-jobs` возвращает queued job с `job_id`. Зафиксировать ID до ожидания. На телефоне existing `POST /m/api/video/swap` принимает multipart `video`, повторяющиеся `faces`, `preset`, `frame`, `consent`; proxy отправляет ту же задачу Direct Generator.

Не отправлять второй create при timeout: текущий create присваивает новый UUID и не принимает durable intent key. Сначала выяснить исход original request/job. Неопределённость → `UNKNOWN_OUTCOME`; новая попытка только после разрешения предыдущей.

Наблюдение: GET `/api/direct-gen/jobs/{id}`; UI показывает фактический status/stage/progress. Existing service уже нормализует VFR, режет lossless chunks с отдельными temp/jobs paths, запускает FaceFusion, pose gate и mux исходного звука. Не повторять эту логику в отдельном scratch executor.

Отмена: POST `/api/direct-gen/jobs/{id}/cancel`, затем terminal state и факт завершения относящихся к job процессов. Общее разрешение STOP не отменять. Живую отмену этот урок не проверял.

## Проверить результат

После `completed` запросить `/api/direct-gen/jobs/{id}/file`; проверить существование, декодирование, hash/size, кадры/FPS/длительность, исходное аудио и кадрирование. Проверить `provenance.qa_gate` и перечисленные причины отказа. Если метрика не вычислена, показать N/A, а не PASS.

Delivery QA, FaceLock sampling и полный animation Gate 1 — разные проверки. Для строгого Gate 1 использовать действующий versioned `tools/video_gate/animation_gate.py` с заранее утверждёнными T1–T7 из `docs/owner/VIDEO_PIPELINE_STAGES_20261010.md`. Не менять пороги после результата и не переносить исторические PASS. Многочеловеческий клип требует matching одного и того же субъекта и поддержанного селектора; текущий simple swap не даёт такого обещания.

Receipt: job ID, source/runtime/FaceFusion/model IDs и hashes где доступны, consent/scope reference, hashes входов и результата, preset/selector, размер/кадры/FPS/audio, elapsed/resources, evaluator version/пороги/результаты, STOP outcome и стоимость с источником. Недостающие поля source/runtime/model hash отметить UNKNOWN, а не выдумывать.

## Доставка

Оригиналы и результат остаются локально вне Git. Если владелец отдельно попросил отправку этого результата, существующий owner-only POST `/api/direct-gen/jobs/{id}/send-to-pult` отправляет один completed файл после QA. Никаких сторонних получателей.

Этот endpoint не даёт durable delivery deduplication. Перед вызовом сохранить локальное SENDING для intent/job+hash; после подтверждения — message_id/time/hash. При неизвестном исходе не повторять отправку. Само поручение «обучи workflow» не является запросом на генерацию/доставку личных файлов.

## Чему именно обучаем

Урок в [LESSON_RU.md](LESSON_RU.md) дополняет existing FaceFusion/FaceLock skills. Запись и поиск в настроенной памяти доказывают `METHOD_WRITTEN/RETRIEVAL_CONFIRMED`. Не доказывают новый tool grant, выполнение рендера, обучение весов или качество на референсах владельца.

Будущая приёмка: synthetic неоткровенный clip + consenting/synthetic identity; одна настоящая local job, STOP/retry ambiguity, файл/QA/receipt на exact runtime SHA. Пока это `NOT_RUN`; никаких личных медиа в этом поручении не открывалось.

## Проверенные первичные источники для выбора модели

Просмотр 22.10.2026. [FaceFusion model list](https://docs.facefusion.io/usage/cli-arguments/processors/face-swapper) содержит HyperSwap, GHOST, SimSwap и другие обычные face-swap модели. [GHOST](https://github.com/ai-forever/ghost) и [SimSwap](https://github.com/neuralchen/SimSwap) — отдельные проекты. Это не подтверждение, что они установлены в Bossman, являются community fine-tune или «uncensored».

У [InsightFace](https://github.com/deepinsight/insightface) лицензия кода и pretrained weights различается: открытый MIT-код не даёт автоматического права коммерческого использования всех весов. Для любого кандидата нужны конкретные model card, revision/hash, лицензия и локальная проверка совместимости. Этот урок не скачивает и не заменяет модели.
