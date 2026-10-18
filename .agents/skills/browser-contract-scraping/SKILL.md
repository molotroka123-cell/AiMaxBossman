---
name: browser-contract-scraping
description: Collect CURRENT public procurement notices (war/defence/security domain) by driving one headless browser over public portal pages, no API, no login. Verified on Czech NEN (nen.nipez.cz); ingests into War Terminal as source=browser. Read before scraping any tender portal.
compatibility: BOSSMAN (Windows owner machine), Claude-compatible agent skills
metadata:
  owner: bossman
  version: "1.0"
  learned_from: owner request 2026-10-18 (War Terminal, feat/war-procurement-terminal-v1)
---

# Парсинг контрактов браузером (без API)

Сделано для War Terminal (`apps/war-terminal`). Код: парсер `warterminal/connectors/browser_nen.py` (чистые функции, тесты на сохранённых
страницах `tests/fixtures_browser/`), драйвер `tools/browser_contracts.py`, приём `POST /api/v1/ingest/browser` (роль analyst).

## Правила (владелец 18.10.2026)
- Только публичные уведомления, только чтение. Без логина, CAPTCHA, платных сервисов.
- robots.txt соблюдать. NEN: `Crawl-delay: 10` -> пауза >= 10 с между загрузками страниц; запрещены `/file*`, `/portal/client/`,
  `*/detail-provozni-informace`. Драйвер имеет allow-list: только `/en/verejne-zakazky/...`.
- Одна страница за раз, один headless Chromium (Playwright, уже установлен), <= 150 загрузок за прогон, браузер закрыть в `finally`.
- Никаких LLM/GPU: разбор только селекторами (BeautifulSoup). Персональные данные контактных лиц НЕ извлекаются.
- Поля не выдумывать: нет значения = `null`. Происхождение: `source="browser"`, `fetched_at`, `source_url`, `provenance`.

## Метод NEN (проверено)
1. URL списка собирается напрямую (грамматику видно по submit формы): 
   `https://nen.nipez.cz/en/verejne-zakazky/p:vz:stavZP=neukoncena&zadavatelNazev=Ministerstvo%20obrany&podaniLhuta=2026-10-18%2C&page=2`
   (`stavZP=neukoncena` = Not terminated, `podaniLhuta=<с>,` = срок подачи от даты, `nazev` = слово в названии, `page` = страница по 50).
   Не копировать href пагинации: в нём двойное кодирование (`%2520`).
2. Список: `tbody tr`, ячейки `td[data-title]` = номер NEN, название, статус, заказчик, срок. Ссылка `a[href*=detail-zakazky]`.
3. Деталь: плитки `.gov-grid-tile` (`h3` = метка, значение в `p`/`a`): Identifier of NIPEZ (-> ключ `CZ:RVZ...`, тот же, что у ISVZ-коннектора,
   поэтому дубли отсекаются), Estimated value (excl. VAT) `123 966,94`, Currency `koruna česká`, DEADLINE FOR SUBMITTING TENDERS `26.10.2026, 23:59`
   (Прага -> UTC), CPV `35331500-8`, Date of publication on profile.
4. Приоритет деталей: сначала заказчики/слова оборонной области (`browser_nen.BUYERS`, `SUBJECTS`, `relevance()`).
5. `POST /ingest/browser` отвергает записи без https-источника/`fetched_at`/`source=browser`, не перезаписывает уже известные уведомления.

## Запуск
```powershell
cd apps\war-terminal
python tools\browser_contracts.py --dry-run $env:TEMP\nen.json          # только файл
python tools\browser_contracts.py --post                                 # + в терминал (токен analyst из %USERPROFILE%\.war-terminal\tokens.json, не печатать)
```
Прогон ~25-30 мин из-за паузы 10 с; запускать в фоне. Расписание: раз в сутки ночью (Task Scheduler), НЕ во время генераций с телефона.

## Ловушки
- TED: robots.txt запрещает `/*/search/result?...` (динамические поисковые URL), а список результатов в headless не отрисовался (пустая
  страница) -> TED читать через уже имеющийся официальный коннектор `ted`, не браузером.
- "Not terminated" в NEN включает очень старые незакрытые процедуры: ВСЕГДА добавлять `podaniLhuta=<сегодня>,`.
- Быстрый поиск NEN ищет и по названию заказчика (шум): использовать `zadavatelNazev`/`nazev`.
- Чекбоксы расширенного поиска кликать через `label[for=...]`; аккордеон заказчика открывать `button[aria-controls$=authority__content]`; cookie-баннер: Refuse all.
- Windows: `PYTHONUTF8=1`, иначе чешские символы падают в cp1251 при print.
- Если сервер :8830 не знает `/ingest/browser` (старый процесс) - перезапустить его так же, как `Start-BossmanApps.ps1`
  (`python -X utf8 -m warterminal --port 8830` из `apps/war-terminal`).
- Таймаут ожидания `tbody tr` на странице списка = 0 результатов по этому запросу (так было у "Policie České republiky", "munice", "kamufláž" с фильтром срока) - это не сбой портала; такой запрос стоит 1 загрузку.
- Прогон 18.10.2026: 150 загрузок, 248 активных строк списка, 129 деталей прочитано, 127 новых записей в терминале (2 дубля с ISVZ).
- Страницы меняют вёрстку: при падении `parse_detail` сохранить страницу в fixtures, поправить селекторы, добавить тест.
