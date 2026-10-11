# Jeff browser search: проверки (11.10.2026)

Ветка `feat/jeff-browser-search-20261011` (от `feat/bossman-genjutsu-jeff-unified-20261010` @ 250e24ec), worktree `wt-jeff-browser-20261011`. Python 3.12, без xdist, без сети, без реального браузера. Каталог `command-center/`, флаг `-p no:cacheprovider`.

| # | Команда | Результат | rc |
|---|---|---|---|
| 1 | `python -m pytest tests/test_jeff_browser_search.py -q` | 44 passed in 0.56s | 0 |
| 2 | `python -m pytest tests/test_jeff_2_research.py tests/test_jeff_closeout_research_ssrf.py tests/test_jeff_2_pipeline.py tests/test_pit_web_search_fallback.py -q` | 98 passed in 3.90s | 0 |
| 3 | `python -m pytest tests/test_web_research_flag.py tests/test_jeff_privacy_in_code.py -q` | 25 passed, 4 warnings in 16.45s | 0 |

Покрытие #1: флаг OFF = ни одного клиента/процесса; maybe_wrap возвращает исходный search без флага/команды; allowlist (запрещённые инструменты отказываются до клиента); screenshot только по opt-in; file:/javascript:/data:/loopback/private/metadata/credentials URL не доходят до браузера; редирект в LAN отбрасывает контент; ≤3 навигации; таймаут закрывает браузер; rate limit; сериализация; STOP до и посреди работы; robots (disallow, 5xx, 404, кеш); PII/секреты/инъекции; запрос с PII не уходит; порядок fallback; сбои не пробрасываются.

Ограничение: реальный Playwright MCP не запускался.
