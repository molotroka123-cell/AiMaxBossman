# Интернет, Computer Use, Jev, медиапроизводство и модельные профили

**SPECIFICATION / integration requirements.** Существующие рабочие подсистемы переиспользовать; точные handlers определять на собранном SHA.

## 1. Интернет как рабочий инструмент

Search/read/download/API/MCP/browser входят в общий capability registry. Публичный research разрешается в выделенном scope без подтверждения каждого GET. Недоверенные страницы, PDF, README и результаты поиска не являются system/owner instructions. Прямые загрузки проверяются по bytes/hash/type и целостности, а не по расширению URL. Не угадывать успешный download из текста LLM.

Request layer контролирует destinations, redirects, private-network/metadata endpoints и data egress; отдельная owner-grant может разрешить конкретную внутреннюю сеть, но произвольный сайт не может. Секреты выдаются нужному адаптеру на конкретное действие. Не передавать один provider token в другой endpoint.

MCP/OSS discovery не означает автоматическую установку/исполнение. Проверять происхождение, revision, license, schema, hooks, auth и dependencies. Tool descriptions тоже потенциальная инъекция. Не делать произвольный токен passthrough и не обходить ограничения сервиса. CAPTCHA/BankID/человеческая проверка дают конкретный OWNER_ACTION_REQUIRED, не ложный FAIL модели.

## 2. Browser/Computer reliability

`observe → choose allowed tool → validate target/arguments/scope → execute → independently verify`.

Нужен общий authoritative policy path для UI/CLI/Jev/MCP/phone. computer.* должен доходить до разрешённого агента, но не превращать отсутствие инструмента в разрешение любого shell. Слово модели semantic может повышать риск, но не понижать его. Проверяются fresh observation, window identity, actual file bytes, focus, stale/replayed approval, STOP и junction/reparse.

GUI test требует реального окна. API contract test и screenshot mockup не заменяют пользовательский путь. Один desktop lease: два агента не печатают одновременно в одну активную вкладку. Android — optional extension через обнаруженный поддержанный adapter, не обязательное обещание новой реализации в данном merge.

## 3. Jev

Пользователь сообщил о live pilot через OpenRouter `https://openrouter.ai/api/v1/systemone`, model `jev-1.13`: contract verified, 6 функциональных и 4 защитных synthetic case, 18/18 agreement. Это USER_REPORTED_EVIDENCE конкретного запуска, не гарантия текущего endpoint/цены/совместимости. Найти опубликованный raw report, request schema/receipt и runtime config; не использовать старые противоречивые сообщения чата как API contract.

Перед новым запуском проверить контракт поставщика и идентичность credentials локально. В shadow Jev лишь предлагает, существующий Bossman исполняет. Agreement не равен правильности, 30 шагов не универсальный сертификат. Authoritative низкорисковая phase 2 требует реализованного executor, всех существующих порогов и конечных outcome checks, а не только flag.

No authority over spending, consent, permissions, final success or hidden tests. Shadow тоже расходует budget и передаёт данные. Unknown privacy не трактовать как public для нового egress. TypeSafe, DefAPI и OpenRouter — разные credentials/endpoint/billing configurations.

## 4. Hybrid-реклама SwapMe как реальная задача

Цель: один 15-секундный 9:16 MP4 из предоставленного fox reference, минимум 5 секунд принятой локальной генерации и максимум 10 секунд оплачиваемой облачной video-generation. Владелец должен повторить workflow обычным поручением через Bossman CMD.

Сначала capability discovery и dry storyboard; проверить настоящий model ID, поддерживаемые длительности, разрешение, размер минимального заказа/клипа, цену и статус jobs. Не трактовать Seedance «2/5» как гарантированное имя модели. Higgsfield/open fork — интерфейс/adapter, не автоматически бесплатная модель. Конкретный fork URL и лицензию найти по remote, а не повторять неподтверждённую ссылку из чата.

Схема 3 + local 2.5 + 3 + local 2.5 + 4 допустима только если провайдер реально поддерживает 3/3/4 секунд и их суммарную тарификацию. Если минимум clip=5, выбрать 5+5 cloud и 5 local либо один 10s cloud, нарезанный локально. Счётчик cloud seconds включает ВСЕ оплачиваемые outputs/варианты/retries, а не только вошедшие в монтаж секунды. Не считать cancelled/timeout generation бесплатной без billing receipt. Покупки через Higgsfield не обходят общий лимит платного видео. Дополнительный денежный cap берётся из действующего scope; отсутствие цены не $0.

Локальный выбор — лучший ИЗ РЕАЛЬНО ПРОВЕРЕННЫХ на Radeon 8060S presets, не заявленный мировой чемпион. Переиспользовать имеющиеся веса/движок. Bounded I2V A/B, затем production, не неделя скачивания моделей. Local testing имеет время/энергию/compute budget. SAC не отключать; blocked runtime не выдавать за работающий.

Storyboard → prompts → task IDs → generation → decode/quality selection → EDL → color/fps/audio/branding → final. Текст и логотип добавлять композитором, а не надеяться на diffusion. Crossfade меняет длительность: итог рассчитывать по кадрам и подтвердить 15s с допуском одного кадра. Реальные frames, dimensions, fps, audio, hash, стоимость, провайдер каждой сцены и contact sheet сохраняются.

Нельзя расходовать весь 10s budget на A/B/C benchmark, не оставив production. Сначала local previews и deterministic монтаж; paid shots только после preflight. Не имитировать native local 5s многократным повтором 1s clip без явной метки производного монтажа.

«Зайдёт людям» проверяется owner creative acceptance и позднее разрешённым A/B публикации, не обещается по просмотру модели. Готовый MP4 не означает продажу/выручку. Публикация отдельно от создания.

## 5. Модели и обучение

Registry хранит executor/reviewer/STT/TTS/vision/image/video/tool-structured profiles. Checkpoint, quant, tokenizer/template, context, runtime, memory и skills фиксируются. OFFLINE/LOCAL_ONLY и локальный unrestricted не становятся скрытым cloud fallback. Нет ключа/цены — честная доступность в UI/CLI.

Для текущего hardware учитывать четыре категории: LLM/coding/agents, image, video, tool-calling/structured output. Голос — дополнительная роль. Кандидатов low-overrefusal, Xing/OCR и другие ранее выбранные модели сначала проверить по лицензии и runtime; названия из старого чата — discovery hints, не ready-to-install manifest.

Новый checkpoint не обязан быть лучше старого. Главные метрики — verified task success, tool/schema accuracy, latency, RAM, стоимость, частота ненужных отказов и регрессий. После рецепта hybrid workflow выполнить restart и новую задачу с dry cloud plan/local preview без повторного платного расхода; это проверка reuse, а не доказанный causal transfer gain.
