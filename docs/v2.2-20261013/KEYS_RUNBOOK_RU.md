# Ключи и бюджеты: эксплуатационный контракт 2.2

Фактические значения, балансы и статусы аккаунтов — только в приватном локальном отчёте
владельца. Этот файл описывает процедуру без секретов и финансов владельца.

## Источники доступа

| Область | Источник | Что проверять |
|---|---|---|
| Registry LLM | CommandCenter/bcc.db providers.api_key_enc + existing vault | Запись, дешифрование, auth, доступ нужной модели, отдельный бюджет |
| Coding/orchestra/research | LocalAppData/Bossman/keys/provider-keys.env | Приоритет перед registry, несовпадение/устаревший дубликат; DPAPI backup |
| Пульт | telegram-companion/config.json + companion.env или credentials.enc | getMe, правильный owner, один poller, общий backend |
| Звонки Telegram | existing CallsManager credentials/session | api_id/api_hash/session различаются от bot token; login state, addon, audio |
| Jev/TypeSafe | BOSSMAN_JEV_API_KEY или TYPESAFE_API_KEY в actual runtime | Наличие в процессе, официальный контракт, права/лимит; Jeff — другой компонент |
| Gmail | existing Gmail connector OAuth/IMAP | client configured, scopes, refresh/session; не читать письма ради auth |
| RunPod | действующий owner binding RUNPOD_API_KEY | Auth, pods, hourly/storage spend, баланс; не стартовать GPU ради проверки |
| Higgsfield | согласованный API/MCP/CLI binding | Различить API USD и website plan credits; правильный ID/secret/тип подключения |
| HF/model downloads | existing token/cache при необходимости | Доступ к gated assets и их лицензии; публичные веса могут не требовать ключа |
| Search services | Tavily/Apify/Jina/Serper/Brave или собственный SearXNG | Auth, лимиты, оплачиваемые операции, фактическое подключение в Bossman |

Конфликт источников — отдельный дефект: действующий ключ в env не исправляет устаревшую
vault-запись, если выбранный runtime использует её. Перед ротацией выяснить actual
reader/priority. Наличие имени в файле не доказывает активацию сервиса.

## Разрешённая проверка без расхода inference

- Ключ только в памяти; не выводить хвосты, fingerprints, ciphertext, полные тела ошибок,
  URL с токеном или данные аккаунта. Исключения логировать стабильным кодом.
- Точные official hosts, TLS, запрет redirect с Authorization, ограниченные timeout.
- GET auth/account/usage/catalog; никакого chat/search/Actor/generation ради «пинга».
- Для публичного /models делать anonymous negative control. 200 без ключа означает
  доступный каталог, а не доказанную пригодность ключа.
- Нулевая/неизвестная сумма, лимит ключа, лимит плана, бесплатная квота и деньги счёта
  различаются. 401 — проверить/заменить доступ; 403 — scope/доступ, не обязательно ноль;
  429 — rate/quota; локальный connection error — сервер/порт, не повод пополнять облако.

## Официальные контракты, проверенные 13.10.2026

- OpenRouter: [current key](https://openrouter.ai/docs/api/api-reference/api-keys/get-current-api-key)
  возвращает срок/лимит/использование ключа. [Credits API](https://openrouter.ai/docs/api/api-reference/credits/get-remaining-credits)
  описывает баланс и требование management key. Если фактический ответ обычному ключу
  отличается от документации, сохранить HTTP-status и whitelist чисел, не расширять права.
- Google: [Models API](https://ai.google.dev/api/models); Mistral:
  [Models API](https://docs.mistral.ai/api/endpoint/models); Groq:
  [API reference](https://console.groq.com/docs/api-reference). Каталог не проверяет inference и billing.
- Tavily: [GET usage](https://docs.tavily.com/documentation/api-reference/endpoint/usage)
  даёт использование и лимиты ключа/аккаунта. Apify: [GET limits](https://docs.apify.com/api/v2/users-me-limits-get)
  даёт лимиты и текущий цикл; это не баланс кошелька.
- Higgsfield: [официальное описание API](https://higgsfield.ai/creator-hub/help-center/integrations/what-is-the-higgsfield-api)
  разделяет API prepaid USD и plan credits MCP/CLI. Не переносить баланс одного на другой.
- RunPod: [GraphQL schema](https://graphql-spec.runpod.io/). Только read-only query
  заранее выбранных полей; никакой мутации/подписания/удаления ресурсов в audit.

## Обновление и пополнение

1. Сначала проверить scope и выбранный источник, затем существующий действующий binding.
2. Ротацию проводить в одном existing secret store, затем сверить потребителей и backup.
   Не вставлять ключи в промпты, Git или обычные сообщения. Fernet master key нельзя
   заменить без перешифрования сохранённых секретов.
3. Пополнение — решение владельца по расчёту предполагаемого расхода и резерву. Размер
   вывода API, local timeout и число строк кода не задают сумму покупки.
4. До разрешения на расходы использовать принятый локальный маршрут и явный отказ
   облачного fallback. UNKNOWN balance не трактовать как необходимость покупки.
5. После обновления сначала auth/usage, затем один разрешённый synthetic inference
   с жёстким бюджетом и receipt. Live token presence не превращать в PASS всей функции.

Секреты, private prompt/media, OAuth/session файлы и локальный финансовый отчёт
исключаются из bundle, Git, public tree и обучающих корпусов.
