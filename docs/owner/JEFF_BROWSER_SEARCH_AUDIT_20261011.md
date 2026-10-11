# Jeff: браузерный поиск — аудит upstream и интеграция (11.10.2026)

Статус: интеграция ВЫКЛЮЧЕНА по умолчанию; upstream `feder-cr/invisible_playwright_mcp` НЕ подключён (см. вердикт).

## 1. Upstream (read-only клон)

| Поле | Значение |
|---|---|
| Репозиторий | https://github.com/feder-cr/invisible_playwright_mcp |
| Клон | `C:\Users\asd\Bossman\mp-jeff-browser-temp\invisible_playwright_mcp` |
| Коммит (pin) | `332137d58d4ccae8e93619113a2881414564ec96` («0.71.1: browser_evaluate says it is the last rung…», 2026-10-11) |
| Лицензия | `LICENSE` — MIT, «Copyright (c) 2024-2026 AIHawk contributors». README:176-178: всё, выпущенное ДО 2 сентября 2026, остаётся под AGPL-3.0 (старые версии/пины — копилефт). |
| Lock-файл | НЕТ (`uv.lock` отсутствует; `pyproject.toml` — только нижние границы `>=`). Закрепить версии «по lock-файлу» нельзя. |
| Установка (README) | `irm https://astral.sh/uv/install.ps1 \| iex` (README:23, :64) и `curl -LsSf https://astral.sh/uv/install.sh \| sh` (README:30, :72); затем `uvx invisible-playwright-mcp` без версии (`.mcp.json:6`). Ничего из этого НЕ запускалось. |

### Зависимости (`pyproject.toml:25-140`, все `>=`, верхняя граница только у `mcp<2`)
`mcp>=1.8,<2`, **`invisible-playwright>=0.32.0`** (отдельный пакет: патченый Firefox-движок + `invisible-core`, исходники НЕ в этом репо и НЕ проверены), `selectolax`, `python-dotenv`, `openai>=1.30`, `click`, `starlette`, `uvicorn`, `pydantic>=2`.

## 2. Что на самом деле делает «invisible»

Это anti-detect браузер, а не обычный Playwright. Из кода/README/pyproject (сам движок не читали — он во внешнем пакете):

| # | Находка | Где | Severity |
|---|---|---|---|
| F1 | Позиционирование: «Anti detect browser… undetected, no captchas, no blocks»; «invisible to anti-bots». Системный промпт: «real, stealth Firefox» | `pyproject.toml:8`, `README.md:13`, `agent.py:26` | HIGH (противоречит требованию прозрачности) |
| F2 | Детерминированный «отпечаток» по seed: идентичность браузера = seed → fingerprint; профиль «владеет» seed (`.stealth-identity.json`) | `cli.py:59`, `mcp/identity.py:1-37`, `STEALTHFOX_SEED` | HIGH |
| F3 | Патченый Firefox-бинарник скачивается из GitHub release и пинится «печатью» (seal); подмена navigator/экрана/окна/локали под страну egress-IP (`locale="auto"`), Accept-Language как у retail Firefox, «окно как рисует Windows Firefox» | `engine.py:4-29`, `pyproject.toml:85-110` | HIGH |
| F4 | Эмуляция человека: «pointer moves, keys are pressed», пауза руки перед кликом (`hesitation`), Shift-ввод | `README.md:92`, `pyproject.toml:79,116` | MEDIUM (обход поведенческих антиботов) |
| F5 | Прокси: таймзона/локаль «следуют за прокси»; GeoIP-база скачивается при прокси | `README.md:96-99,168`, `mcp/proxy.py` | MEDIUM |
| F6 | Телеметрия: «Each browser launch also fetches a one-line counter file from a GitHub release, which is how launches are counted» — исходящий запрос на каждый запуск | `README.md:170-174` | MEDIUM |
| F7 | Автоскачивание бинарника при первом `browser_open` (GitHub release) | `engine.py:137` (`ensure_binary`) | MEDIUM |
| F8 | Инструменты записи доступны модели: `browser_click/_at`, `browser_type`, `browser_select_option`, `browser_upload_files` (читает локальные файлы из `INVISIBLE_MCP_UPLOAD_DIRS`), `browser_press_key` | `mcp/server.py:615-692` | HIGH для Jeff (нужна жёсткая allowlist) |
| F9 | `browser_evaluate` помечен `read_only=True`, но исполняет произвольный JS; защита — «catches the obvious spellings, not every possible one» | `mcp/server.py:698-720` | HIGH (аннотациям доверять нельзя) |
| F10 | Постоянный профиль с логинами/cookies через `--profile-dir` | `README.md:100-104`, `mcp/identity.py` | MEDIUM (у нас — только эфемерный) |
| F11 | Web UI без аутентификации при смене `--host` (по умолчанию 127.0.0.1:8765); ключ OpenRouter в argv/истории | `README.md:105-108,120-124`, `cli.py:226` | LOW (не используется) |
| F12 | Нет lock-файла; плавающие `>=`; `uvx` без версии; установка через pipe-в-shell | см. выше | HIGH (supply chain) |
| F13 | Солвера капч в коде MCP нет; упоминание — только «canvas-drawn captcha» как причина `browser_click_at`; «no captchas» обеспечивается отпечатком, а не решением капчи | `mcp/actions.py:775`, `README.md:13` | INFO |
| F14 | Исходящие вызовы: OpenRouter (режим web UI), GitHub release (движок, счётчик), GeoIP; `subprocess`/`eval`/`exec` в `src` по grep не найдены | `README.md:160-180` | INFO |

Секреты в репозитории глубже grep не искали; `curl \| sh` есть и в `.github/workflows/ci.yml` (установка uv в CI upstream) — нас не касается.

## 3. Вердикт

**Upstream НЕ интегрирован.** Его ядро (F1-F5) — именно stealth/anti-bot-evasion, которое владелец запретил (прозрачный браузер); движок — внешний неаудированный бинарник; lock-файла нет; инструменты записи (F8/F9) несовместимы с read-only границей Jeff. «Обернуть только обычную навигацию» невозможно: навигация там идёт через патченый Firefox из `invisible-playwright`.

Вместо этого: **обычный Playwright MCP** (Microsoft, `@playwright/mcp`; `npm view` 11.10: latest = 0.0.83) как ОПЦИОНАЛЬНЫЙ транспорт, запускаемый только по явной команде владельца с закреплённой версией и `--isolated --headless`. В рамках этой задачи он не устанавливался и не запускался.

## 4. Что интегрировано

Код: `command-center/bcc/pit/j2/browser_search.py` (+ ~10 строк в `j2/research.py::create`), тесты `command-center/tests/test_jeff_browser_search.py` (44 теста, офлайн, fake MCP server).

Где: fallback Research Desk Jeff. Порядок: существующий поиск (SearXNG → keyless) → ТОЛЬКО если он вернул пусто/упал, браузер.

Границы (все проверены тестами):
- Флаг `BOSSMAN_JEFF_BROWSER_SEARCH` (OFF по умолчанию) И `BOSSMAN_JEFF_BROWSER_MCP_CMD` (команды по умолчанию нет). OFF = клиент не создаётся, процесса нет.
- Allowlist из 4 имён: `browser_navigate`, `browser_snapshot`, `browser_take_screenshot` (дополнительно закрыт явным `allow_screenshot`, по умолчанию отказ), `browser_close`. Любое другое (click/type/evaluate/upload/fill_form/run_code/pdf…) — `ForbiddenAction` ДО клиента. Модель Jeff браузерных инструментов не видит: модуль — детерминированный код; shell/computer-use не затронуты; на голосовом звонке research не работает (не в CALL_SAFE_MODULES).
- ≤3 навигации на ответ (страница выдачи + ≤2 результата), ≤15 с на вызов (по таймауту клиент закрывается), глобальный лимит 4 браузерных ответа / 10 мин, один браузер за раз.
- STOP (`stop.flag` в home) проверяется до и после каждого вызова; при срабатывании клиент закрывается.
- robots.txt по origin (кеш 1 ч): 404 = разрешено, 5xx/сеть = отказ; User-Agent браузера не подменяется.
- SSRF: только http(s) без credentials, DNS-резолв только в публичные адреса (существующий `guarded_target`), то же для финального URL после редиректов; URL с секрет-подобными query-параметрами отбрасываются; запрос с PII/секретом наружу не уходит.
- Эфемерный профиль: `--isolated` у сервера + клиент закрывается после каждого ответа; env процесса — белый список (PATH, TEMP, HOME…), ключи Bossman процессу не передаются; профиль/cookies владельца не используются.
- Фильтры перед участником: `sanitize_fetched` (инъекции, скрытый текст) → `redact_secrets` → PII (e-mail, телефон, карта по Luhn, IBAN); результат идёт в Research Desk как недоверенные данные с цитированием.

## 5. Намеренно НЕ сделано
Стелс/подмена отпечатка, эмуляция человека, прокси-гео, решение капч, логины, формы, загрузки/скачивания, платежи, доступ к локальным файлам, постоянный профиль, профиль/cookies владельца, скриншоты в сторону участника.

## 6. Остаточные риски
1. DNS rebinding между проверкой и подключением браузера; под-ресурсы страницы (img/xhr) браузер грузит сам, их адреса мы не фильтруем. Снижение: отдельная учётка/сеть без доступа к LAN или `--blocked-origins`/firewall (здесь не проверено).
2. Prompt-injection на странице: режется эвристикой `sanitize_fetched`, это не гарантия; ответ остаётся extractive и с источниками.
3. Playwright MCP ставит владелец: нужен закреплённый пакет и проверка integrity; реальный браузер здесь не запускался (тесты — fake server).
4. Страница выдачи (`html.duckduckgo.com/html/` по умолчанию) может запретить ботов в robots.txt — тогда fallback честно даёт пусто.
5. PII-фильтр — регулярные выражения: имена и адреса не маскируются.

## 7. Как включить (только владелец, осознанно)
```
set BOSSMAN_JEFF_BROWSER_SEARCH=1
set BOSSMAN_JEFF_BROWSER_MCP_CMD=["npx","-y","@playwright/mcp@0.0.83","--isolated","--headless"]
set BOSSMAN_JEFF_BROWSER_SEARCH_URL=https://html.duckduckgo.com/html/?q={q}   (необязательно)
```
Версию и integrity сверить (`npm view @playwright/mcp`) до первого запуска. Выключить: убрать `BOSSMAN_JEFF_BROWSER_SEARCH`. Мгновенный стоп: `stop.flag`. Счётчики: `BrowserSearch.status()`.
