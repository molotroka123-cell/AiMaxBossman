# Источники и честный baseline

Дата чтения: 2026-09-23. Это read-only документальный snapshot, не полный executable audit. Remote refs проверены через GitHub connector; состояние owner-PC этим процессом не считывалось, runtime tests не запускались.

## Проверенные GitHub-указатели

Полные refs: [contracts/source-refs.json](contracts/source-refs.json).

- release: `e0bf948d…` ДО публикации этого docs-only пакета;
- current owner/fix: `1a29d85a…`;
- current evidence: `010d1907…`;
- integration: `bd2fe23d…`;
- cloud closure: `3707d6ab…`;
- Evolution: `3bc81aee…`;
- self-learning: `32f69d63…`;
- Seedance durations: `99970958…`;
- Higgsfield source branch: `12832471…`;
- main: `799fc3dd…`, НЕ изменять по этому handoff.

Источник свежего отчёта:
https://github.com/molotroka123-cell/AiMaxBossman/commit/010d19076951d2911dc1bade449bbdb509b4a31c

Immutable continuation:
https://github.com/molotroka123-cell/AiMaxBossman/blob/010d19076951d2911dc1bade449bbdb509b4a31c/owner-repair/owner-run-20260923/FINAL-1.0/CONTINUE.md

Отчёт обновил C2/C3 на FIXED и known-code P1 на 0, но сохранил pending live retests и NO final certification. Есть унаследованные строки summaries, конфликтующие с обновлённой таблицей; координатор должен разрешить их по reproducer/test/SHA, не выбрать более приятный текст. P2 — 20+ в историческом owner report, не новый точный пересчёт.

Это уточняет прежние ответы чата: нельзя считать три старых P1 по-прежнему непочиненными, но также нельзя считать их live-certified. Наличие Windows-100 workflow не доказывает настоящий стресс-run. Никакой процент готовности 1.5 по количеству файлов не вычислялся.

## Проверенные внешние технические документы

**Twilio Media Streams:** https://www.twilio.com/docs/voice/media-streams
Двусторонний аудиопоток через WebSocket, запуск через Connect/Stream и проверка X-Twilio-Signature описаны официально. Это пример транспорта, не доказательство работающего Bossman phone adapter; стоимость/аккаунт/номера не проверялись.

**ElevenLabs voice cloning:** https://elevenlabs.io/docs/eleven-creative/voices/voice-cloning
Отличать conditioning коротким sample от отдельного обучения. Это optional cloud provider, не локальная модель.

**Professional own-voice cloning:** https://elevenlabs.io/docs/eleven-creative/voices/voice-cloning/professional-voice-cloning
Официально требуется собственный голос с verification; cloud voice не экспортируется как standalone local clone. Условия продукта и supported languages перепроверить при реализации. Подписка не приобреталась.

## Что НЕ подтверждалось этим пакетом

- Доступность конкретной unrestricted/voice/video модели на AMD Windows, её качество, licensing и latency.
- Работоспособность заявленного в чате open-higgsfield fork: сначала обнаружить реальные remotes/license/ownership, не угадывать URL.
- Новая Jev API-документация OpenRouter: попытка открыть предполагаемый docs URL не дала usable source. В спецификации приведён пользовательский live-report, не утверждение нового contract probe. Проверить реальный опубликованный report и локальный adapter.
- MCP security page по предположенному latest URL не загрузилась; security controls в этом пакете — собственные проектные требования, не цитата подтверждённой версии MCP spec. При интеграции закрепить текущую официальную версию.
- Юридическая готовность live calls/recording/payments: нужно отдельное review применимых правил и provider terms для выбранного сценария; этот файл не юридическое заключение.

## Источники состояния внутри repo

Перед каждым merge обновить refs и читать AGENTS, CLAUDE_NEXT_ACTION, owner FINAL-1.0 evidence, docs/terminal, docs/evo, docs/owner/JEV_TOMORROW.md, docs/owner/SKILLS.md и docs/media. Если файл отсутствует в текущем TARGET, читать его из конкретного source SHA, не подменять это памятью чата. Upstream claims, documentation, code presence, mock tests и live evidence имеют разные уровни доверия.
