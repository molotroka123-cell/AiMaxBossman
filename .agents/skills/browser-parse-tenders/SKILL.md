---
name: browser-parse-tenders
description: Parse open tenders by yourself in the PC browser - take open tenders from War Terminal, open each official TED/Prozorro/NEN notice page with browser.open, read title, buyer, deadline and status from the page, compare with War Terminal data, take a screenshot. Triggers - спарси тендеры браузером, проверь тендер в браузере, открой уведомление TED, parse tenders browser.
license: MIT
compatibility: BOSSMAN (Windows owner machine), Claude-compatible agent skills
metadata:
  owner: bossman
  version: "1.0"
  provenance: owner-request-20261022-browser-parse
required_tools: [war_terminal.list_tenders, war_terminal.monetization_radar, browser.open, browser.read_dom, browser.screenshot]
permissions: [read]
input_schema:
  type: object
  properties:
    count:
      type: integer
      description: How many open tenders to check (default 2, max 5)
  required: []
output_schema:
  type: object
  properties:
    checked:
      type: integer
    mismatches:
      type: integer
  required: [checked]
---

# Парсинг тендеров браузером (`browser-parse-tenders`)

Владелец 22.10.2026: «обучи боссмана спарсить что-то самому с браузера». Ты сам вызываешь инструменты, не просишь
владельца открыть страницу.

## Шаги (строго по порядку, каждый — вызов инструмента; бюджет 8 шагов)
1. `war_terminal.list_tenders` с `{"status": "open", "limit": 5}`. Если этого инструмента нет — `war_terminal.monetization_radar`
   с `{"max_items": 5, "k_per_contact": 1}` и бери `items[].tender`. Выбери N тендеров (по умолчанию 2) с `source_url`.
2. Для КАЖДОГО из N тендеров три вызова подряд:
   a. `browser.open` с `{"url": source_url}` — только ссылка из ответа War Terminal
      (`https://ted.europa.eu/en/notice/-/detail/...`, `https://prozorro.gov.ua/tender/...`, `https://nen.nipez.cz/...`). Свои URL не выдумывать.
   b. `browser.screenshot` — доказательство для владельца.
   c. `browser.read_dom` — найди название, заказчика (buyer), срок подачи (deadline), статус. На TED метки: «Title»,
      «Official name» (заказчик), «Deadline for receipt of tenders». На Prozorro: «Кінцевий строк подання тендерних пропозицій».
      TED (проверено 22.10): разделы «Summary» и «Notice» свёрнуты — срок виден только после `browser.click` по заголовку «Notice»
      и повторного `browser.read_dom`. Не нашёл после раскрытия = «нет на странице».
3. НЕ отвечай владельцу, пока не сделал шаг 2 для всех N тендеров. Ответ после одного тендера = задача не выполнена.
4. Сравни страницу с данными War Terminal: номер уведомления, заказчик, срок. Расхождение = строка в отчёте, не исправлять самому.

## Ответ владельцу (по-русски, коротко)
Таблица: ключ | название | заказчик | срок (страница) | срок (терминал) | совпадает? | путь скриншота.
Если страница не открылась (404, «Page not found», пусто) — так и написать: «ссылка не работает», это сигнал для проверки терминала.

## Правила
- Только чтение публичных страниц. Не логиниться, не отправлять формы, не скачивать файлы (browser.download/submit/login = ASK, не вызывать).
- TED: не открывать поисковые URL `/search/result?...` (запрещены robots.txt) — только страницы уведомлений по ссылке из терминала.
- Не больше 5 страниц за запрос, пауза между страницами одного сайта не меньше 5 с.
- Персональные данные контактных лиц (имена, телефоны, e-mail) не выписывать.
- Значения не выдумывать: не нашёл на странице = «нет на странице».
