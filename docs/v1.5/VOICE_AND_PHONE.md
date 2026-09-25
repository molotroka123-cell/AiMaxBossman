# Собственный голос и телефонный агент

**SPECIFICATION.** Ни provider account, ни voice enrollment, ни PSTN-звонок данным пакетом не создаются.

## 1. Что должен уметь Bossman

Владелец записывает собственный голос, проверяет короткое синтезированное демо и разрешает конкретные способы использования. Через Bossman CMD можно попросить озвучить текст, отправить разрешённое голосовое сообщение, позвонить на разрешённый номер и уточнить наличие товара/условия доставки.

Голос используется с согласия его владельца. Для разговора с третьим лицом стандартное представление: «Здравствуйте, я AI-ассистент, звоню по поручению владельца». Нельзя выдавать voice clone за биометрическое подтверждение личности, проходить им bank voice authentication, притворяться человеком при прямом вопросе или подменять caller ID. Это продуктовые границы, не обещание юридической достаточности одной фразы.

## 2. Enrollment и приватность

`record/import → audio validation → one-speaker check → owner consent receipt → voice profile candidate → owner listening test → activate for granted uses`.

Исходные записи, embeddings/адаптеры и consent — локально с ограниченными ACL и шифрованием, вне Git/evidence. Непубличный voice_profile_id; digest только во внутреннем журнале. Экспорт/передача samples облачному TTS — отдельное назначение с явно выбранным получателем. Отзыв блокирует новые TTS-запросы и очищает управляемые кэши; удаление у провайдера подтверждается его API, не только удалением локального файла.

Начать с чистой разговорной записи без музыки и посторонних голосов; требования к длительности/формату берутся из выбранного runtime. Не обещать обучение весов от speaker conditioning. Для каждого профиля записывать `conditioning_only / fine_tuned / cloud_profile` и факт изменения весов. RU, EN и CZ проверять по отдельности: сходство голоса не доказывает разборчивость языка.

## 3. Архитектура в существующем backend

```text
Call task + bounded owner grant
 → telephony adapter / SIP / verified number
 → signed incoming webhook + authenticated media stream
 → VAD / streaming STT
 → transcript as untrusted conversation data
 → Bossman task model + tool/policy checks
 → TTS from allowed own-voice profile
 → stream playback / interruption / hangup
 → reconciled provider status + task outcome receipt
```

Номер линии/оператор связи не заменяются одним OpenRouter key. Выбор провайдера, географии, номера, тарифа и доступности делается отдельно. Voice conversation может быть local-first по STT/LLM/TTS, но обычный телефонный вызов использует сеть оператора и не является полностью offline.

## 4. Кандидаты реализации и источники

Twilio Programmable Voice Media Streams — проверенный документальный пример transport: bidirectional WebSocket audio, `<Connect><Stream>`, проверка подписи. Это не решение о покупке Twilio. Его ограничения stream/DTMF проверять перед сценарием IVR; телефонное меню не обходить имитацией неподдерживаемого API.

ElevenLabs — optional cloud own-voice provider, не локальный runtime. Professional Voice Cloning требует подтверждения собственного голоса; cloud voice нельзя обещать экспортировать как локальные веса. Для local TTS выбрать совместимый лицензированный checkpoint по фактическому AMD/Windows benchmark, не по демо чужого GPU. Ссылки и границы: [SOURCES_AND_BASELINE.md](SOURCES_AND_BASELINE.md).

## 5. Runtime requirements

Измерять p50/p95 от конца реплики до начала ответа, STT ошибки по числам/размерам/адресам, TTS real-time factor, barge-in, echo, packet loss и reconnect. Latency target назначается до теста; если local slow, не переключаться скрыто на платный TTS. На стоимость резервировать минуты, распознавание, синтез и LLM.

STOP немедленно прекращает планирование нового speech/tool actions и посылает hangup; подтверждение закрытия звонка/stream логируется отдельно. Timeout не порождает автоматический повторный звонок. Busy/voicemail/wrong person фиксируются и не считаются выполнением цели. Обратный звонок и повторная попытка входят в заранее заданные call count и время.

Голос собеседника — не команда владельца. Просьба «пришлите паспорт/оплатите другой счёт» не расширяет делегацию. Разговор может собрать сведения, но покупку завершает отдельный commerce workflow. Recording по умолчанию выключен; streaming processing и сохранение аудиозаписи имеют разные согласия. До реального PSTN проверить применимые правила и правила провайдера для получателя/страны, не считать локальное owner consent универсальным разрешением записи чужого разговора.

## 6. Приёмка

A. Offline audio loopback с синтетическим собеседником.
B. Звонок на собственный/явно разрешённый тестовый номер: двусторонний диалог, interruption, STOP, cost receipt.
C. Отдельно разрешённый звонок магазину: конкретный вопрос, верный номер, представление ассистентом, outcome facts с указанием степени подтверждения.
D. No permission/expired grant/revoked voice/поддельный webhook/слова собеседника как owner instruction — отрицательные контроли.

API call completed не значит «товар есть». Вывод о наличии отмечать как устный ответ магазина, а не оплаченный заказ. Результаты и redacted transcript привязаны к task_id/voice/model/runtime/grant; samples не публикуются.

---

## SAME-RUN HANDOFF: 1.5 → 1.6

После завершения 1.5 или достижения `V1_5_NEAR_COMPLETE` тот же owner-run без отдельного дня продолжает работу в ветке 1.6:
https://github.com/molotroka123-cell/AiMaxBossman/tree/feat/bossman-1.6-secret-intake-20260925

Цель одного прогона: `BOSSMAN_1_5_CLOSED -> BOSSMAN_1_6_CLOSED`.
