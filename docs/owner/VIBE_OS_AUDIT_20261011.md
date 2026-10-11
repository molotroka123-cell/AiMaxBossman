# Аудит vibeOS (caffeinum/vibeOS) — 11.10.2026

Только чтение: клон `--depth 1` в `C:\Users\asd\Bossman\mp-vibe-temp`, ничего не запускалось, не собиралось и не устанавливалось.

## Реквизиты

| Поле | Значение |
| --- | --- |
| Репозиторий | https://github.com/caffeinum/vibeOS |
| SHA (HEAD main) | `1815394ce4a77732a217e8efc35450d962d3dd13` (10.10.2026, merge PR #40 `app-boot-progress`) |
| Лицензия | MIT, © 2026 Aleksey Bykhun (файл `LICENSE`) |
| Статус | «experimental»; в README прямо написано: «не sandbox» при самостоятельном запуске |
| Состав | `README.md`, `LICENSE`, `web/` (27 файлов, ~800 КБ исходников: `kernel/agent.js` 226 КБ, `kernel/machine.js` 103 КБ, `ui/*.js`, `tools.js`, `skills.js`, `os.css`) |
| Сборка | **нет** `package.json`, нет lock-файла, нет билд-шага. Статическая страница + ES-модули. |
| Прежняя версия | Next.js/tRPC + Chromium по CDP + терминалы — удалена из `main`, лежит под тегом `legacy-nextjs` (мы её не брали). |

## Зависимости

Зафиксированных зависимостей нет. Фактически:

- `web/vendor/lit.js` (15 КБ) и `web/vendor/wisp-client.js` (6.5 КБ) — вендорены в репозиторий.
- `web/fetch-assets.sh`: `npm pack v86@0.5.445` (движок x86-эмулятора, BSD-2), затем `curl` с `copy.sh/v86` — `seabios.bin`, `vgabios.bin`, `linux4.iso`. Без проверки хэшей.
- Образы Debian/Alpine для ВМ тянутся с `https://d3je35hqch090t.cloudfront.net/` (CloudFront автора), без пина по хэшу.
- Шрифт JetBrains Mono — с `fonts.googleapis.com`.
- Хостинг `vibeos.sh` нужен для: `/api/agent/prompt`, OAuth-релея ChatGPT, `/api/proxy` (web_fetch/web_search), `/api/wisp` (сетевой релей ВМ), `/api/mcp/relay`, `/api/events`.

## Что исполняется

1. **x86-ВМ в WebAssembly** (v86) с реальным Linux — пользовательский shell, `vm_exec`.
2. **Агент живёт в странице** (`kernel/agent.js`): цикл инструментов `create_app`, `vm_exec`, `edit_file`, `write_file`, `read_file`, `search_file`, `reload_ui`, `reload_os`, `web_fetch`, `web_search`, `get_mailbox`, `reply_mailbox`, `js`.
3. **`create_app` = `import(blobURL)`** — сгенерированный модуль исполняется в том же документе, что и сам «ОС»-интерфейс, с доступом к `mount, api`. Изоляции нет: приложение в одном origin с ключом модели в `localStorage`.
4. Инструмент **`js`** — произвольный JS «root on the page».
5. **Дверь из гостя в хост**: любой процесс в ВМ может писать файл-запрос и вызывать те же инструменты агента (в коде это названо «deliberately open», ВМ «trusted»).
6. `edit_file` может переписать собственный `system/*.js` (самомодификация ОС с откатом на «stock» при неудачной загрузке).
7. Внешний агент (Claude Code/Cursor/Codex) управляет рабочим столом через `vibeos-mcp` + токен/relay.

## Что уходит наружу

| Что | Куда | Условие |
| --- | --- | --- |
| Ключ модели | `localStorage` браузера → напрямую `api.openai.com` / `api.anthropic.com` | BYO-key режим |
| Запросы/контекст агента | `vibeos.sh/api/agent/prompt`, `/api/openai/generate` | режим ChatGPT-логина / «server» |
| Трафик ВМ | `wisps://vibeos.sh/api/wisp` по умолчанию (реальные TCP, включая TLS и apt) | если задан релей |
| web_fetch / web_search | через `vibeos.sh/api/proxy` | инструменты агента |
| Аналитика | `/_vercel/insights/script.js` + `POST /api/events` (Hexclave) | только при хостинге на vibeos.sh; в Docker-образе тег вырезан |
| Образы ВМ, шрифты | CloudFront, Google Fonts, copy.sh | загрузка при старте |
| MCP-релей | `/api/mcp/relay` на vibeos.sh | при сопряжении внешнего агента |

Вывод по приватности: «ключ идёт напрямую провайдеру» — верно, но остальная часть конструкции (релей ВМ, прокси, аналитика, агент в одном origin с приложениями) для Bossman неприемлема как есть.

## Что мы берём (паттерны) и чего не берём

| Паттерн vibeOS | Вердикт | Как в Vibe Studio |
| --- | --- | --- |
| «Софт, который меняют просьбой»: запрос на естественном языке → агент пишет маленькое приложение | **Берём** | `POST /api/vibe/jobs` build/modify, версии `vN` |
| Каждый шаг виден (agent log) | **Берём** | журнал шагов `events.jsonl` + страница `vibe.js` (planning / ladder / generating / file_written / check_passed) |
| Skills = правила в системном промпте: «одна задача», 4 состояния (empty/loading/ready/failed), без `alert()`, видимая ошибка вместо заглушки | **Берём** | `SYSTEM_PROMPT` в `bcc/vibe/generator.py` |
| Приложение = файл с `// @title`, живёт в рабочем каталоге, зеркалится на диск | **Берём идею** | папка `<data>/vibe/apps/<slug>/vN/`, версии неизменяемы |
| Откат «сломанного» ОС на stock-копию | **Берём идею** | версии не удаляются, откат = перенос указателя `current` |
| «unsafe: new Function только компилирует» — проверка синтаксиса перед загрузкой | **Усиливаем** | статический скан (нет eval/Function/сети/CDN), перепроверка при каждом показе |
| Гостевой iframe браузера с `default-src 'none'` CSP (`ui/browser.js`) | **Берём** | превью: `sandbox="allow-scripts"` без `allow-same-origin` + CSP заголовком |
| ВМ x86 в WASM, v86, образы Debian/Alpine | **Не берём** | нужен только статический веб; ВМ — 600+ МБ, непроверяемые образы, сеть наружу |
| `js` tool, `create_app` через `import(blobURL)` в одном origin, дверь гость→хост, самопереписывание ядра | **Не берём** | нарушает наш периметр (одобрения, STOP, песочница) |
| Релей `vibeos.sh`, прокси, аналитика, OAuth ChatGPT-релей | **Не берём** | ничего не уходит на чужие серверы |
| MCP-мост для внешних агентов | **Не берём** | у нас свой Code Mode facade и approvals |

## Что у Bossman уже есть (и что переиспользовано)

| Возможность | Где | Использование в Vibe Studio |
| --- | --- | --- |
| Лестница провайдеров + governance (free-only, цена, приватность) | `bcc/provider_governance.py`, `Registry.adapter_for`, `bossman_shared/privacy.py` | генерация идёт только через `adapter_for` → `GovernedAdapter`; `local_only` = `execution_privacy("local_only")` на границе провайдера |
| Очередь задач с STOP | `bcc/direct_gen/service.py` (паттерн: JSON на диске, один исполнитель) | своя очередь `bcc/vibe/service.py`, но с **возобновлением** после рестарта (у direct_gen — «interrupted») |
| STOP-файлы | `bcc/autonomy/stop.py` | та же логика: `<data>/autonomy/STOP` и `<data>/computer/STOP` читаются перед каждым шагом и каждым платным вызовом |
| Подтверждения владельца | `Approvals` (`POST /api/approvals/{id}`) | продвижение приложения в список — отдельное approval `vibe.promote` |
| Реестр приложений | `bcc/features/apps.py` (манифесты, порт, health) | продвинутое приложение получает `app.manifest.yaml` с `ui.static_url`, карточка `LIVE` без порта |
| Страницы UI | `ui/pages/index.js` (lazyPage), `_ui.js`, `api.raw` | `ui/pages/vibe.js` в том же стиле |
| Code Mode facade (`bossman.search/run`) | `bcc/code_mode/` | **не пересекается**: это сжатие каталога инструментов для моделей, не генерация приложений. Не использован. |
| Генератор сайтов Web Designer | `bcc/web_designer_gen.py` | детерминированные шаблоны; Vibe Studio — генерация моделью с проверкой. Оба сосуществуют. |

## Итог

vibeOS ценен как **продуктовый паттерн**, а не как код: из ~800 КБ мы заимствуем идею и несколько принципов промпта. Форк ВМ не нужен и вреден. Собственная реализация — `command-center/bcc/vibe/` (≈1200 строк Python + 390 строк `long_run.py`) + страница `vibe.js`; описание, правила приватности и запуск — `docs/owner/VIBE_STUDIO_20261011.md`.

Клон `mp-vibe-temp` не содержит наших изменений и может быть удалён.
