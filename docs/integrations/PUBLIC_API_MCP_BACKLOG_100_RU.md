# Бэклог 100 публичных API и MCP-серверов (не внедряются сейчас)

Дата составления: 2026-10-16. Статус: бэклог, ничего из списка не подключено.

Уже интегрируются и сюда не входят: mcp-server-fetch, mcp-server-time, mcp-server-git, server-memory, server-sequential-thinking, server-filesystem, context7, @playwright/mcp, duckduckgo-mcp-server, arxiv-mcp-server.

## Как читать таблицу

- kind: MCP-stdio (локальный процесс), MCP-http (удалённый/hosted MCP), REST API (прямой HTTP, оборачивается тонким адаптером или fetch-инструментом).
- auth: none (без ключа), free key (бесплатный ключ или бесплатный тариф), paid (платный; только после явного решения владельца и с жёстким лимитом).
- license: «—» означает, что лицензия не проверялась; для REST API это условия использования данных/сервиса.
- priority: P1 (ставить первыми), P2 (полезно), P3 (на будущее).

## Статус проверки

Через WebFetch подтверждено (репозиторий существует, не в архиве): github/github-mcp-server (MIT), getsentry/sentry-mcp, mongodb-js/mongodb-mcp-server (Apache-2.0), executeautomation/mcp-playwright (MIT), cloudflare/mcp-server-cloudflare (Apache-2.0), firecrawl/firecrawl-mcp-server (MIT), Open-Meteo (бесплатно без ключа для некоммерческого использования). Из бэклога сознательно исключены: все серверы из modelcontextprotocol/servers-archived (архив с мая 2025: GitHub, GitLab, Postgres, SQLite, Redis, Slack, Puppeteer, Brave и др.), elevenlabs/elevenlabs-mcp (архивирован, заменён hosted-версией с OAuth), makenotion/notion-mcp-server (не поддерживается; вместо него remote Notion MCP). Остальные строки: URL взяты из официальных документов/репозиториев вендоров, известных по состоянию на дату составления; перед внедрением каждой строки обязателен повторный шаг проверки (репозиторий не в архиве, лицензия, свежесть релиза).

### Проверка ссылок (измерено 2026-10-16)

HTTP-запрос к каждому из 100 URL: 94 ответили 200. Шесть не подтверждены и требуют ручной проверки перед внедрением: #18 crates.io data access (404 — адрес, вероятно, изменился), #52 Pexels и #100 CISA KEV (403 — защита от ботов, страница существует), #69 Binance (202 — страница на JS), #64 ECB Data Portal и #99 URLhaus (ошибка соединения с этой машины). Код 200 подтверждает, что страница существует; он НЕ подтверждает, что репозиторий не в архиве и что лицензия указана верно — это отдельный шаг при внедрении.

## Таблица

| # | name | kind | URL | auth | license | why it matters for Bossman | priority |
|---|------|------|-----|------|---------|----------------------------|----------|
| | **A. Поиск и исследования** | | | | | | |
| 1 | Brave Search MCP | MCP-stdio | https://github.com/brave/brave-search-mcp-server | free key | — | второй независимый веб-поиск рядом с DuckDuckGo | P1 |
| 2 | Firecrawl MCP | MCP-stdio | https://github.com/firecrawl/firecrawl-mcp-server | free key | MIT | страница в чистый markdown для локальных моделей | P1 |
| 3 | Exa MCP | MCP-http | https://github.com/exa-labs/exa-mcp-server | free key | MIT | семантический поиск для глубокого research | P2 |
| 4 | Tavily MCP | MCP-stdio | https://github.com/tavily-ai/tavily-mcp | free key | MIT | поиск, заточенный под агентов, с извлечением | P2 |
| 5 | SearXNG | REST API | https://github.com/searxng/searxng | none | AGPL-3.0 | локальный мета-поиск без ключей и лимитов | P1 |
| 6 | Wikipedia REST API | REST API | https://en.wikipedia.org/api/rest_v1/ | none | CC BY-SA (контент) | быстрые проверяемые факты и саммари | P1 |
| 7 | OpenAlex API | REST API | https://docs.openalex.org/ | none | CC0 (данные) | граф научных работ, дополняет arxiv | P2 |
| 8 | Crossref REST API | REST API | https://api.crossref.org/swagger-ui/index.html | none | открытые метаданные | проверка DOI и цитат | P2 |
| 9 | Semantic Scholar API | REST API | https://api.semanticscholar.org/ | free key | ToS API | цитирования и рекомендации статей | P3 |
| 10 | PubMed E-utilities | REST API | https://www.ncbi.nlm.nih.gov/books/NBK25501/ | none | public domain | медицинские источники для fact-check | P3 |
| | **B. Разработка и хостинг кода** | | | | | | |
| 11 | GitHub MCP Server | MCP-http | https://github.com/github/github-mcp-server | free key | MIT | issues/PR/CI напрямую, замена архивного reference-сервера | P1 |
| 12 | GitHub REST API | REST API | https://docs.github.com/en/rest | free key | ToS API | запасной путь, когда MCP недоступен | P2 |
| 13 | GitLab MCP server | MCP-http | https://docs.gitlab.com/user/gitlab_duo/model_context_protocol/mcp_server/ | free key | — | если проекты владельца окажутся на GitLab | P3 |
| 14 | Gitea MCP | MCP-stdio | https://gitea.com/gitea/gitea-mcp | free key | — | self-hosted git без облака | P3 |
| 15 | Sentry MCP | MCP-http | https://github.com/getsentry/sentry-mcp | free key | см. LICENSE.md | разбор ошибок прод-сервисов | P2 |
| 16 | npm registry API | REST API | https://github.com/npm/registry/blob/main/docs/REGISTRY-API.md | none | ToS | проверка версий и свежести пакетов перед pin | P1 |
| 17 | PyPI JSON API | REST API | https://docs.pypi.org/api/json/ | none | ToS | то же для Python-зависимостей | P1 |
| 18 | crates.io data access | REST API | https://crates.io/data-access | none | ToS | проверка Rust-крейтов | P3 |
| 19 | Stack Exchange API | REST API | https://api.stackexchange.com/docs | free key | CC BY-SA (контент) | готовые решения типовых ошибок | P2 |
| 20 | Hugging Face Hub MCP | MCP-http | https://huggingface.co/docs/hub/agents-mcp | free key | — | поиск моделей и датасетов, лицензии моделей | P1 |
| | **C. Документы и знания** | | | | | | |
| 21 | Notion remote MCP | MCP-http | https://developers.notion.com/docs/mcp | free key | — | заметки владельца; self-hosted версия не поддерживается | P2 |
| 22 | Wikidata API | REST API | https://www.wikidata.org/w/api.php | none | CC0 | структурные факты и идентификаторы | P2 |
| 23 | mcp-obsidian | MCP-stdio | https://github.com/MarkusPfundstein/mcp-obsidian | free key | MIT | локальная база заметок как память | P2 |
| 24 | Open Library API | REST API | https://openlibrary.org/developers/api | none | открытые данные | метаданные книг | P3 |
| 25 | Internet Archive APIs | REST API | https://archive.org/developers/ | none | ToS | открытые тексты, аудио и видео | P3 |
| 26 | Wayback Machine API | REST API | https://archive.org/help/wayback_api.php | none | ToS | архив мёртвых ссылок для цитат | P2 |
| 27 | Hacker News API | REST API | https://github.com/HackerNews/API | none | открытый API | сигнал по новым инструментам и OSS | P3 |
| 28 | zotero-mcp | MCP-stdio | https://github.com/54yyyu/zotero-mcp | free key | — | личная библиотека источников | P3 |
| 29 | Qdrant MCP | MCP-stdio | https://github.com/qdrant/mcp-server-qdrant | none | Apache-2.0 | локальная векторная память, работает без облака | P1 |
| 30 | Chroma MCP | MCP-stdio | https://github.com/chroma-core/chroma-mcp | none | Apache-2.0 | альтернатива Qdrant, проще в установке | P2 |
| | **D. Данные и базы** | | | | | | |
| 31 | Postgres MCP Pro | MCP-stdio | https://github.com/crystaldba/postgres-mcp | none | MIT | анализ БД в read-only режиме, замена архивного reference | P1 |
| 32 | MotherDuck / DuckDB MCP | MCP-stdio | https://github.com/motherduckdb/mcp-server-motherduck | none | MIT | локальная аналитика CSV/Parquet без сервера | P1 |
| 33 | MongoDB MCP Server | MCP-stdio | https://github.com/mongodb-js/mongodb-mcp-server | none | Apache-2.0 | официальный, умеет режим read-only | P3 |
| 34 | Redis MCP | MCP-stdio | https://github.com/redis/mcp-redis | none | MIT | очереди и кэш, замена архивного reference | P3 |
| 35 | ClickHouse MCP | MCP-stdio | https://github.com/ClickHouse/mcp-clickhouse | none | Apache-2.0 | быстрые запросы по логам и метрикам | P3 |
| 36 | Supabase MCP | MCP-http | https://github.com/supabase-community/supabase-mcp | free key | Apache-2.0 | управляемый Postgres для проектов владельца | P3 |
| 37 | MySQL MCP server | MCP-stdio | https://github.com/designcomputer/mysql_mcp_server | none | MIT | доступ к чужим MySQL-базам в read-only | P3 |
| 38 | Neo4j MCP | MCP-stdio | https://github.com/neo4j-contrib/mcp-neo4j | none | MIT | графовая память и связи сущностей | P3 |
| 39 | Meilisearch MCP | MCP-stdio | https://github.com/meilisearch/meilisearch-mcp | none | MIT | локальный полнотекстовый поиск | P3 |
| 40 | Datasette | REST API | https://github.com/simonw/datasette | none | Apache-2.0 | публикует SQLite как JSON API и интерфейс | P2 |
| | **E. Автоматизация браузера и рабочего стола** | | | | | | |
| 41 | mcp-playwright (ExecuteAutomation) | MCP-stdio | https://github.com/executeautomation/mcp-playwright | none | MIT | дополнительные инструменты поверх Playwright | P3 |
| 42 | Chrome DevTools MCP | MCP-stdio | https://github.com/ChromeDevTools/chrome-devtools-mcp | none | Apache-2.0 | отладка, сеть и производительность страниц | P1 |
| 43 | Browser Use | MCP-stdio | https://github.com/browser-use/browser-use | none | MIT | агентный браузер с локальными моделями | P2 |
| 44 | Stagehand | REST API | https://github.com/browserbase/stagehand | none | MIT | действия в браузере по описанию на естественном языке | P3 |
| 45 | Steel Browser | REST API | https://github.com/steel-dev/steel-browser | none | Apache-2.0 | self-hosted браузерный API с сессиями | P3 |
| 46 | Browserbase MCP | MCP-http | https://github.com/browserbase/mcp-server-browserbase | paid | Apache-2.0 | облачный браузер; только с лимитом расходов | P3 |
| 47 | Windows-MCP | MCP-stdio | https://github.com/CursorTouch/Windows-MCP | none | MIT | управление рабочим столом Windows (скриншоты, ввод) | P1 |
| 48 | Crawl4AI | REST API | https://github.com/unclecode/crawl4ai | none | Apache-2.0 | локальный краулер под LLM без внешних ключей | P1 |
| 49 | Apify MCP | MCP-http | https://github.com/apify/apify-mcp-server | free key | MIT | готовые скраперы (Actors), есть бесплатный тариф | P3 |
| 50 | Jina Reader API | REST API | https://jina.ai/reader/ | free key | ToS | URL в markdown одним GET-запросом | P2 |
| | **F. Медиа: видео, аудио, изображения** | | | | | | |
| 51 | ComfyUI server API | REST API | https://github.com/comfyanonymous/ComfyUI | none | GPL-3.0 | локальные image/video-пайплайны через HTTP | P2 |
| 52 | Pexels API | REST API | https://www.pexels.com/api/ | free key | Pexels License | свободные стоковые фото и видео | P2 |
| 53 | Pixabay API | REST API | https://pixabay.com/api/docs/ | free key | Pixabay License | стоковые изображения, видео и звуки | P3 |
| 54 | Unsplash API | REST API | https://unsplash.com/developers | free key | Unsplash License | фото высокого разрешения (с учётом правила про разрешение) | P3 |
| 55 | Openverse API | REST API | https://api.openverse.org/ | none | CC-лицензии | поиск медиа с явной лицензией для provenance-подписей | P1 |
| 56 | Wikimedia Commons API | REST API | https://commons.wikimedia.org/w/api.php | none | свободные лицензии | медиа с источником и автором | P2 |
| 57 | Freesound API | REST API | https://freesound.org/docs/api/ | free key | CC-лицензии | звуковые эффекты для видео | P3 |
| 58 | Kokoro-FastAPI | REST API | https://github.com/remsky/Kokoro-FastAPI | none | Apache-2.0 | локальный TTS с OpenAI-совместимым API | P1 |
| 59 | MusicBrainz API | REST API | https://musicbrainz.org/doc/MusicBrainz_API | none | CC0 (данные) | метаданные музыки для треков | P3 |
| 60 | YouTube Data API v3 | REST API | https://developers.google.com/youtube/v3 | free key | ToS API | метаданные видео, суточная квота | P3 |
| | **G. Финансы и крипто (только чтение)** | | | | | | |
| 61 | CoinGecko API | REST API | https://docs.coingecko.com/ | free key | ToS API | цены и рыночные данные, только чтение | P2 |
| 62 | CoinGecko MCP | MCP-http | https://docs.coingecko.com/docs/mcp-server | free key | — | те же данные через MCP | P3 |
| 63 | Frankfurter | REST API | https://frankfurter.dev | none | MIT | курсы валют ЕЦБ без ключа | P1 |
| 64 | ECB Data Portal API | REST API | https://data.ecb.europa.eu/help/api/overview | none | открытые данные | официальные макроданные ЕС | P3 |
| 65 | SEC EDGAR APIs | REST API | https://www.sec.gov/search-filings/edgar-application-programming-interfaces | none | public domain | отчётность компаний, обязателен User-Agent | P2 |
| 66 | FRED API | REST API | https://fred.stlouisfed.org/docs/api/fred/ | free key | ToS API | макроэкономические ряды | P2 |
| 67 | World Bank Indicators API | REST API | https://datahelpdesk.worldbank.org/knowledgebase/articles/889392-about-the-indicators-api-documentation | none | CC BY 4.0 | индикаторы развития по странам, в т.ч. Гвинея (NimbaMoney) | P2 |
| 68 | Alpha Vantage | REST API | https://www.alphavantage.co/documentation/ | free key | ToS API | котировки и индикаторы, малый бесплатный лимит | P3 |
| 69 | Binance public market data | REST API | https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints | none | ToS API | только публичные рыночные эндпоинты, без торговых ключей | P3 |
| 70 | DefiLlama API | REST API | https://api-docs.defillama.com/ | none | ToS API | TVL и цены DeFi, только чтение | P3 |
| | **H. Коммуникации и продуктивность** | | | | | | |
| 71 | Slack MCP (Zencoder) | MCP-stdio | https://github.com/zencoderai/slack-mcp-server | free key | — | замена архивного reference, если появится Slack | P3 |
| 72 | Google Workspace MCP | MCP-stdio | https://github.com/taylorwilsdon/google_workspace_mcp | free key | MIT | календарь, документы, таблицы с OAuth | P2 |
| 73 | Linear remote MCP | MCP-http | https://linear.app/docs/mcp | free key | — | трекер задач | P3 |
| 74 | Atlassian remote MCP | MCP-http | https://www.atlassian.com/platform/remote-mcp-server | free key | — | Jira/Confluence, если нужны рабочие проекты | P3 |
| 75 | ntfy | REST API | https://docs.ntfy.sh/publish/ | none | Apache-2.0/GPL-2.0 | простые push-уведомления без аккаунта, резерв к Telegram | P2 |
| 76 | Home Assistant MCP server | MCP-http | https://www.home-assistant.io/integrations/mcp_server/ | none | Apache-2.0 | локальное управление умным домом | P3 |
| 77 | Matrix Client-Server API | REST API | https://spec.matrix.org/latest/client-server-api/ | none | Apache-2.0 | открытый self-hosted мессенджер | P3 |
| 78 | Mastodon API | REST API | https://docs.joinmastodon.org/api/ | free key | AGPL-3.0 | публикация в открытую соцсеть | P3 |
| 79 | Bluesky / AT Protocol | REST API | https://docs.bsky.app/ | free key | MIT/Apache-2.0 | открытый API публикации и ленты | P3 |
| 80 | Todoist API | REST API | https://developer.todoist.com/ | free key | ToS API | личные задачи | P3 |
| | **I. Карты, погода, гео, открытые данные** | | | | | | |
| 81 | Open-Meteo | REST API | https://open-meteo.com/en/docs | none | CC BY 4.0 | погода без ключа (некоммерческое использование) | P1 |
| 82 | OSM Nominatim | REST API | https://nominatim.org/release-docs/latest/api/Overview/ | none | ODbL | геокодинг, лимит 1 запрос в секунду | P1 |
| 83 | Overpass API | REST API | https://wiki.openstreetmap.org/wiki/Overpass_API | none | ODbL | запросы к объектам OSM | P2 |
| 84 | REST Countries | REST API | https://restcountries.com/ | none | MPL-2.0 | справочник стран | P1 |
| 85 | OSRM | REST API | https://github.com/Project-OSRM/osrm-backend | none | BSD-2-Clause | маршруты, можно развернуть локально | P3 |
| 86 | NASA Open APIs | REST API | https://api.nasa.gov/ | free key | public domain | космические данные и изображения | P3 |
| 87 | USGS Earthquake API | REST API | https://earthquake.usgs.gov/fdsnws/event/1/ | none | public domain | сейсмические события | P3 |
| 88 | NWS API (weather.gov) | REST API | https://www.weather.gov/documentation/services-web-api | none | public domain | прогнозы США, резерв к Open-Meteo | P3 |
| 89 | GeoNames web services | REST API | https://www.geonames.org/export/web-services.html | free key | CC BY 4.0 | названия мест и часовые пояса | P3 |
| 90 | Nager.Date | REST API | https://date.nager.at/Api | none | MIT | праздники по странам для планирования | P3 |
| | **J. Наблюдаемость, ops, безопасность** | | | | | | |
| 91 | Docker MCP Gateway | MCP-stdio | https://github.com/docker/mcp-gateway | none | MIT | единая изолированная точка запуска MCP-серверов | P1 |
| 92 | Prometheus HTTP API | REST API | https://prometheus.io/docs/prometheus/latest/querying/api/ | none | Apache-2.0 | метрики самого Bossman | P2 |
| 93 | Grafana MCP | MCP-stdio | https://github.com/grafana/mcp-grafana | free key | Apache-2.0 | дашборды и алерты через MCP | P3 |
| 94 | Cloudflare MCP servers | MCP-http | https://github.com/cloudflare/mcp-server-cloudflare | free key | Apache-2.0 | документация и операции Cloudflare | P3 |
| 95 | Kubernetes MCP server | MCP-stdio | https://github.com/containers/kubernetes-mcp-server | none | Apache-2.0 | только если появится кластер; read-only режим | P3 |
| 96 | OSV.dev API | REST API | https://google.github.io/osv.dev/api/ | none | Apache-2.0 (код), CC BY 4.0 (данные) | проверка уязвимостей зависимостей перед pin | P1 |
| 97 | NVD CVE API | REST API | https://nvd.nist.gov/developers/vulnerabilities | free key | public domain | официальные CVE | P2 |
| 98 | VirusTotal API | REST API | https://docs.virustotal.com/reference/overview | free key | ToS API | проверка файлов и URL, только некоммерческий тариф | P2 |
| 99 | abuse.ch URLhaus API | REST API | https://urlhaus-api.abuse.ch/ | free key | CC0 | базы вредоносных URL для фильтра ссылок | P3 |
| 100 | CISA KEV catalog | REST API | https://www.cisa.gov/known-exploited-vulnerabilities-catalog | none | public domain | приоритизация реально эксплуатируемых уязвимостей | P2 |

## Правила внедрения

1. Allowlist команд. Для stdio-серверов в конфиге MCP-клиента разрешён только точный бинарь/пакет с фиксированными аргументами (npx/uvx/docker-образ из списка); произвольные команды и переменные окружения от модели не принимаются.
2. Ключи только в vault. Ключи не попадают в репозиторий, логи, .env в git, отчёты и Telegram; перед каждым push выполняется скан на секреты. В конфиге хранится только имя секрета.
3. Read-only сначала. Каждый сервер подключается в режиме чтения (read-only флаги БД, токены с минимальными правами, публичные эндпоинты без торговых и платёжных ключей). Запись включается отдельным решением владельца.
4. Версии фиксировать. npm/PyPI-пакеты и docker-образы закрепляются точной версией или digest; обновление только после проверки (репозиторий не в архиве, свежий релиз, лицензия, OSV-скан).
5. Без платных сервисов без лимита. Строки с auth = paid и любые free-key с платным апгрейдом включаются только с жёстким суточным лимитом расходов; живая торговля и траты запрещены.
6. Open-source first. При равных возможностях выбирается self-hosted OSS (SearXNG, Qdrant, Crawl4AI, Kokoro) вместо облачного сервиса.
7. Изоляция. Браузерные и desktop-серверы запускаются в отдельном профиле/песочнице; персональные медиа и данные не отправляются внешним сервисам.
8. Лимиты и этикет API. Соблюдать rate limit (Nominatim 1 запрос в секунду, EDGAR с User-Agent), кэшировать ответы, указывать источник и лицензию в подписях (provenance).
9. Недоверенный вывод. Ответы серверов считаются данными, а не инструкциями; никаких автоматических действий по тексту из внешнего ответа.
10. Порядок внедрения. Сначала P1 по одному серверу с тестом, затем P2; каждое подключение сопровождается записью в реестр интеграций и откатом.

## Проверка количества

Таблица содержит ровно 100 нумерованных строк (#1-#100), по 10 в каждой из 10 категорий A-J; строки-заголовки категорий без номера в счёт не входят. Из исходного списка исключены 10 уже интегрируемых проектов.
