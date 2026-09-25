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

---

## SAME-RUN HANDOFF: 1.5 → 1.6

После закрытия 1.5 или достижения `V1_5_NEAR_COMPLETE` тот же owner-run сразу продолжает работу в ветке:
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
