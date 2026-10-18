---
name: fx-daily-rates
description: Ежедневный учёт курсов USD/EUR/CZK (и допущение USDT≈USD) в War Terminal - вкладка «Курсы / FX». Источники ЧНБ и ЕЦБ (XE.com НЕ используется - условия запрещают скрапинг), append-only SQLite, перекрёстная проверка бесплатной облачной моделью, кросс-курсы, калькулятор «на дату базы vs сегодня», отчёт к Новому году. Читать перед любыми правками сбора курсов.
compatibility: BOSSMAN (Windows owner machine), Claude-compatible agent skills
metadata:
  owner: bossman
  version: "1.0"
  learned_from: owner request 2026-10-18 (War Terminal, раздел расчётов)
---

# Ежедневный учёт курсов (FX)

План владельца: купить крипту, ждать роста, продать в доллары, в Tether (USDT), затем USDT -> EUR и CZK. Нужны кросс-курсы и данные,
чтобы к Новому году посчитать разницу «на дату базы» и «сегодня». «Обучаться» = копить данные и считать статистику. Прогнозов цен,
торговых советов и утверждений о росте здесь НЕТ и быть не должно.

## Где лежит
- Код: `apps/war-terminal/warterminal/fx_tracker.py` (парсеры, хранилище, сбор, кросс-курсы, калькулятор, статистика, отчёт, отправка в пульт).
- CLI: `python -X utf8 -m warterminal fx collect|report` (из `apps/war-terminal`). Вкладка UI «Курсы / FX» (RU/EN), API `GET /api/v1/fx/track/report?amount=&baseline=`.
- Задания: `apps/war-terminal/tools/fx_daily.ps1` (запуск) и `register_fx_tasks.ps1` (регистрация `BossmanFX-AM` 09:00 и `BossmanFX-PM` 21:00,
  текущий пользователь, StartWhenAvailable, работают от батареи; чужие задачи не трогаются; `-Remove` убирает только эти две).
  Задания работают из копии кода в `%LOCALAPPDATA%\Bossman\CommandCenter\fx\runtime` - после правок кода повторить `register_fx_tasks.ps1`.
- Данные: `%LOCALAPPDATA%\Bossman\CommandCenter\fx\` - `fx.db`, `backup\fx-YYYYMMDD.db` (раз в день), `logs\`, отчёты `fx-year-end-*.md/.csv`.
  Переопределение каталога: `BOSSMAN_FX_DIR`.
- Тесты: `tests/test_fx_tracker.py`, страницы-образцы `tests/fixtures_fx/` (сохранённые реальные файлы ЧНБ и ЕЦБ от 16.10.2026; сеть в тестах не используется).

## Решение по XE.com (проверено 18.10.2026): НЕ скрапить
- `https://www.xe.com/robots.txt`: для `User-Agent: *` закрыты только `/fxwidgets/`, `/migration/`, `/currencytransfers/`, `/logout`;
  конвертер не закрыт. (`Disallow: /` относится к списку агрессивных ботов - Rogerbot, HTTrack и т.п.)
- Но Terms of Use XE (`https://www.xe.com/legal/`) прямо запрещают: «systematically extract data or data fields, including without limitation any
  financial and/or currency data», «present, archive, cache, frame, scrape, or mirror any Information And Content», и «screen scraping ...
  in any manner or in any quantities not authorized in writing by XE». Автоматический ежедневный сбор курса с XE и хранение его серии - ровно это.
- Вывод: XE не открывается ни браузером, ни запросами. Если нужен именно XE - письменное разрешение XE (владелец) или платный XE Currency Data API.
  Скриншот владельца (1.00 USD = 21.64512380 CZK, 07:29 UTC) разовый, в базу не вносился.

## Источники (публичные файлы, ключей нет)
| Источник | Что даёт | URL |
|---|---|---|
| ЧНБ (`cnb`) | USDCZK, EURCZK, фиксинг раз в рабочий день (~14:30 Прага); в выходные - данные пятницы | `.../central-bank-exchange-rate-fixing/daily.txt` |
| ЕЦБ (`ecb`) | EURUSD, EURCZK, референсные курсы раз в рабочий день (~16:00 CET) | `https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml` |
Это курсы «середина рынка»/фиксинги: комиссии и спреды бирж и обменников НЕ учтены. USDT: публичного источника, разрешённого robots/ToS
и подтверждённого, не подключено -> принято USDT = USD (допущение), везде помечено «не измерено» (`usdt_usd.measured=false`). Число не выдумывается.

## Правила сбора
1. До двух визитов в сутки (по местному дню; `--force` обходит, для ручной проверки). На визит 2 запроса (по одному на файл), пауза >= 5 с.
2. Перед визитом `GET http://127.0.0.1:8801/api/direct-gen/jobs`: пока есть задача в стадии loading или свободной RAM < 20 ГБ - ждать 20 мин, до 3 повторов (правило 18.10: задача в стадии generating сбор НЕ блокирует, sd-cli занимает ~4 из 32 потоков; процесс BelowNormal),
   затем слот пропускается (`skipped_busy` в журнале запусков, код выхода 3). Bossman не отвечает = генераций нет = можно. Ошибка чтения очереди = «занято».
   Генерации с телефона владельца важнее любого сбора. Ollama/GPU/браузер не используются.
3. Детерминированный разбор регулярными выражениями - источник истины. Значения вне правдоподобных границ (`BOUNDS`) и «мусорные» страницы записи не дают.
4. Перекрёстная проверка бесплатной моделью: `POST /api/war-terminal/free-chat` (только price 0/0 + free tier, не локальные; ids 100/101 первыми,
   thinking-модели `max_tokens >= 512`). Расхождение > 0.5 % -> обе записи (parser=regex и parser=model) получают флаг `disagreement` и в отчёты не попадают.
   `usage.cost > 0` -> ответ отбрасывается, проверка прекращается до конца суток (`model_halt.txt`). Нет шлюза (старый Bossman, 404/405) -> `NOT_RUN`,
   записи получают `model_unchecked`. Модель при смене вёрстки: значение сохраняется с `model_only` и тоже не идёт в отчёты.
5. Хранилище append-only (триггеры SQLite запрещают UPDATE/DELETE), уникальность источник+пара+минута+парсер, ежедневная копия в `backup\`.
   Поля: observed_at (UTC), pair, rate (строка Decimal), source, source_url, rate_date, raw_text_excerpt (<=200), parser (regex|model), flags, model.

## Отчёт и калькулятор
- Кросс-курсы: USD<->EUR (ЕЦБ), USD<->CZK (ЧНБ), EUR<->CZK (ЧНБ; ЕЦБ как контроль, расхождение показывается).
- Треугольник USD->EUR->CZK (ЕЦБ EURUSD x ЧНБ EURCZK) против USD->CZK напрямую: разрыв в %. Малый разрыв нормален - фиксинги в разное время.
- Маршрут владельца: крипта -> USD -> USDT (=USD, допущение) -> EUR (ЕЦБ) | CZK (ЧНБ).
- Калькулятор: сумма USDT/USD, дата базы (по умолчанию первый день, когда есть все три курса), «на дату базы» vs «сегодня», разница в EUR/CZK и %.
- Статистика только описательная: n, min, max, среднее, ст. отклонение, изменение за период, последнее дневное изменение, волатильность (ст. откл. дневных % изменений, от 3 дат).
- `python -X utf8 -m warterminal fx report --year-end [--amount N] [--baseline YYYY-MM-DD] [--send-pult]` пишет `.md` и `.csv` в каталог данных и при `--send-pult`
  шлёт их только владельцу (токен `TG_COMPANION_BOT_TOKEN` из `companion.env`, чат = единственный `people[0]`, иначе отказ; токены и id не печатаются).

## Безопасность
Без логинов, CAPTCHA, аккаунтов, персональных данных и движения денег. Токен Bossman читается из файла и не печатается. Платных API нет.

## Ловушки
- ЧНБ в выходные отдаёт пятничный фиксинг: `rate_date` != дата снятия. Серии и статистика строятся по `rate_date` (повторы одной даты не считаются).
- Шлюз `/free-chat` появляется только после перезапуска Bossman (:8801): до этого модельная проверка честно `NOT_RUN`.
- Windows: `PYTHONUTF8=1` / `python -X utf8`. Планировщик показывает «выполняется», пока ждёт освобождения очереди (до ~1 часа).
- Сменилась вёрстка файла: сохранить новый файл в `tests/fixtures_fx/`, поправить регулярку в `parse_cnb`/`parse_ecb`, добавить тест.
