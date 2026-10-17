# Каталог MCP: топ-10 (внедрён 2026-10-16)

Код: `command-center/bcc/features/mcp_catalog.py`, тесты: `command-center/tests/test_mcp_catalog.py`.
Каталог — не второй MCP-клиент: выбранный сервер записывается в ту же таблицу `mcp_servers`, а запуск, политика
разрешений `mcp.policy`, санитизация описаний и удаление остаются в штатном MCP-хабе. Команда запуска проходит
allowlist бинарников (F-014) до записи в БД.

## API

- `GET  /api/mcp/catalog` — 10 серверов, у каждого `installed`.
- `POST /api/mcp/catalog/{id}/install` — тело `{"path": "<папка>"}` только для `git` (корень репозитория, нужен `.git`)
  и `filesystem` (рабочая папка; корень диска и домашняя папка отклоняются).
- Дальше как у любого MCP: `POST /api/mcp/runtime/servers/{id}/connect`.

## Состав и результат живой проверки

Проверка 2026-10-16: Боссман из исходников (e1f3e517 + каталог) на временной БД, порт 8820, установка через каталог,
`connect`, затем список инструментов. БД владельца не трогалась.

| id | сервер (версия закреплена) | риск | инструментов |
|----|----------------------------|------|--------------|
| fetch | mcp-server-fetch 2026.10.10 | network | 1 |
| time | mcp-server-time 2026.10.10 | read | 2 |
| git | mcp-server-git 2026.10.10 | write | 12 |
| memory | @modelcontextprotocol/server-memory 1.0.0 | read | 9 |
| sequential-thinking | @modelcontextprotocol/server-sequential-thinking 1.0.0 | read | 1 |
| filesystem | @modelcontextprotocol/server-filesystem 1.0.0 | write | 14 |
| context7 | @upstash/context7-mcp 4.4.2 | network | 2 |
| playwright | @playwright/mcp 0.0.83 (`--headless --isolated`) | write | 25 |
| duckduckgo | duckduckgo-mcp-server 0.7.0 | network | 3 |
| arxiv | arxiv-mcp-server 0.8.2 | network | 19 |

Итого 10 из 10 подключились (`healthy`), 88 инструментов. С первой попытки git не стартовал — установка шла в папку,
которая не репозиторий; теперь такой путь отклоняется при установке (422).

## Чего НЕ проверено

- Вызовы самих инструментов моделью и политика одобрений на каждом — проверялись только `connect` и список инструментов.
- Playwright требует установленного Chromium; здесь он ответил списком инструментов, реальная страница не открывалась.
- В базу владельца и на запущенный :8800 каталог не ставился: на момент проверки :8800 не работал, а установленная
  сборка не содержит этого роута. Установка у владельца — после слияния ветки и перезапуска.

## Дальше

Ещё 100 кандидатов — в `PUBLIC_API_MCP_BACKLOG_100_RU.md` (не внедряются).
