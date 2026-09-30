# V2 STABILITY PASS — промежуточный журнал (interim)

Дата: 2026-09-03
Ветка: `claude/bossman-control-v03-43igbk`
База: `27e3bf93c3e0a24983874a0bf5d5893ffb68685d` (exact remote HEAD)
Статус: этап A (безопасный воспроизводимый запуск) — в работе, сценарии B ещё прогоняются.

## Правила этапа

- Рабочие данные владельца не трогаются: все прогоны на временном
  `BCC_DATA_DIR` в `%TEMP%\opencode\bcc-stability\arena*`, отдельный порт 8877 (loopback).
- Токен в URL не передаётся; секреты в этот документ и в репозиторий не попадают.
- Сервер: `python -m bcc`; окно: `python -m bcc.desktop` с реальным Chromium
  (Playwright chromium-1234), app-mode `--app=URL`, собственный профиль.
- Драйвер окна — CDP (`--remote-debugging-port=9333` на 127.0.0.1), только как
  наблюдатель; рендеринг, cookie, WS, консоль — настоящий Chrome.

## Что уже подтверждено (этап A)

1. `python -m bcc.desktop` холодным стартом: сервер поднимается на 127.0.0.1:8877
   за несколько секунд, `/api/identity` отвечает `bossman-command-center`,
   окно Chromium открывается в app-mode на чистом URL `http://127.0.0.1:8877/`
   (без токена в URL), на экране форма входа. Логи: `desktop-run.log`,
   `desktop-console.log` (при pythonw), `chrome_debug.log` в профиле окна — всё на месте.
2. CDP-опрос Chrome 151 (chromium-1234): сырой WebSocket-протокол отвечает
   корректно (`Browser.getVersion` OK). Проблема зависания была в драйвере:
   sync-мост Playwright на Python 3.14 вешается при `connect_over_cdp`; async-API
   работает. Это артефакт среды прогона, НЕ баг приложения; исправлено в
   харнессе (async), сам продакшен-код не менялся.
3. Origin-guard CDP: Chrome 111+ требует `--remote-allow-origins=*` для
   подключения драйвера — флаг добавляется только в argv прогона (тестовый
   харнесс), в код приложения не вносится.

## Открытые наблюдения (проверяются в сценариях B)

- Окно открывается и держится; код завершения Chromium по закрытию,
  переподключение WS после убийства сервера, поведение refresh/login-сессии —
  прогоняются сейчас, результаты будут в финальном отчёте и в `fix_cases.jsonl`.
- Артефакты прогонов хранятся вне репозитория: `%TEMP%\opencode\bcc-stability\`
  (там же сырые stdout/stderr сервера и окна, могут содержать токен — в git не
  коммитятся).

## Ближайшие шаги

- B2..B8 на том же изолированном арене (login, refresh, close&reopen,
  kill/restart сервера + reconnect, все страницы, одна безопасная owner-акция с
  проверкой отсутствия дублей после рестарта, ALL_PROXY).
- Только доказанный root cause -> один маленький commit + регрессионный тест.
- Полный Command Center suite на Python 3.11/3.12, compileall, JS-синтаксис,
  secret scan, `git diff --check`, push, CI на exact SHA.
- По каждому подтверждённому багу: `docs/learning/fix_logs/<BUG_ID>.md` +
  запись JSONL в `data/learning/fix_cases.jsonl`.