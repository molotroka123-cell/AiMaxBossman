# Доказательства и ограничения аудита мобильного пилота — 17.10.2026

## Зафиксированная точка

- Репозиторий: `molotroka123-cell/AiMaxBossman`
- Исходная ветка проверки: `origin/main`
- Проверенный HEAD: `e1f574b65699922e0f9306f1262f1520080b1262`
- Перед документационным коммитом: рабочее дерево чистое, Command Center не менялся относительно `85f952b56ee6326b086aec982e02b8e8283c22d6`.
- Проверка команды: `git diff --quiet 85f952b56ee6326b086aec982e02b8e8283c22d6 e1f574b65699922e0f9306f1262f1520080b1262 -- command-center`; результат — diff отсутствует.
- Актуальный `origin/main` при повторной сверке: `1579a8f3deb3de8d12d95bb2fffd58c793d86885`; от `e1f574b` добавлены только `.agents/skills/bossblocks-video-style/SKILL.md` и `docs/owner/CONTINUATION_20261018_RU.md`. `git diff --quiet e1f574b..origin/main -- apps/bossman-mobile command-center` прошёл: проверенные мобильный и backend исходники не менялись.

## Тестовые результаты

| Набор | Точка | Результат | Среда/ограничение |
|---|---|---:|---|
| `apps/bossman-mobile`: `python -m pytest -q` | `e1f574b` | 125 passed, 5.36 s | Поддельный транспорт и состояние; не live E2E |
| Объединённый targeted suite: Gmail, браузер, task, PC-agent, STOP, approval и code-mode | `e1f574b` | 194 passed, 169.71 s | Запуск на точном текущем SHA; tests только контрактные/фикстуры |
| Gmail/browser/remote/task contracts | `85f952b` | 74 passed, 30.04 s | Исторический targeted run |
| Уведомления и fail-closed delegation | `85f952b` | 2 passed, 2.74 s | Исторический targeted run |
| PC-agent, STOP race, audit/approval | `85f952b` | 83 passed, 120.78 s | Исторический targeted run |
| Telegram PC-control | `85f952b` | 9 passed, 1.69 s | Исторический targeted run |
| Bounded browser/global STOP | `85f952b` | 23 passed, 30.80 s | Исторический targeted run |
| Code-mode timeout cases, отдельный повтор | `85f952b` | 3 passed, 8.38 s | Повтор целевых кейсов после таймаута широкого набора |
| Широкий набор companion/code-mode | `85f952b` | 92 passed, 3 failed, 134.88 s | Сохранённый FAIL: helper `wait_for(timeout=6.0)`; не перекрашивать результат |

Командный раздел `command-center` не менялся между `85f952b` и `e1f574b`; объединённый целевой набор дополнительно завершился успешно на `e1f574b`. Успешные unit/contract тесты не подтверждают поведение живого домашнего ПК.

## Приёмка

| Гейт | Статус |
|---|---|
| Mobile PWA unit tests на `e1f574b` | PASS |
| Выборочные backend contract tests на `85f952b` | PASS для отдельных перечисленных наборов |
| Исторический широкий набор на `85f952b` | FAIL: 3 timeout случая |
| Mobile UI/health через локальную Tailscale Serve URL | PASS: HTTP 200, health отвечает, только GET с этого ПК |
| Mobile static frontend bytes vs checkout | PASS: 4/4 exact SHA-256 matches |
| HTTPS PWA endpoint для телефона | BLOCKED: TCP 443 connection refused; Serve настроен только на HTTP 80 и 8830 |
| Совпадение запущенного backend с проверенным исходным SHA | BLOCKED: `SOURCE_IDENTITY_UNKNOWN` |
| Живой телефон → домашний Bossman runtime | NOT_RUN |
| Реальное видимое управление мышью/клавиатурой | NOT_RUN |
| Реальная проверка mobile/global STOP | NOT_RUN |
| Реальный read-only Gmail сценарий с тестовым аккаунтом | NOT_RUN |
| Уведомление о новом входящем письме | NOT_RUN |
| Windows owner acceptance и ресурсный soak | NOT_RUN |

## Интерпретация

Успешные результаты выше подтверждают только перечисленные тесты и соответствующий исходный код. Исторические тесты обозначены их исходной точкой; старый dirty-файл, конфигурация, README или тестовый сценарий не считаются доказательством текущего работающего сервиса. Нет evidence живой модели, подключения телефона, устройства, почтового ящика или работы фонового watcher.

## Безопасный runtime check — 17.10.2026, 14:34–14:36 PDT

Только read-only GET-запросы с этого ПК и чтение конфигурации Tailscale Serve:

- `GET http://127.0.0.1:8840/m/api/health` → `{"app":"bossman-mobile","version":"0.1.0","ok":true}`.
- `GET http://127.0.0.1:8801/health/live` → `status=ALIVE`, `source_identity=SOURCE_IDENTITY_UNKNOWN`, `build_sha` отсутствует.
- `GET http://win-iqprhtsajl9.tail0f845b.ts.net/m/api/health` → тот же ответ; `GET .../m/` → HTTP 200, title `Bossman`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`.
- Через ту же tailnet URL загружены только публичные статические UI-файлы и побайтно сравнены с проверенным checkout: `index.html` SHA-256 `1df470bcbb96c8dbd555e878f4cf7b3ed10a7efb082bfef9920745d239b7f3d0`, `app.js` `b9988a61665221bc444570e0aa8e9633ccff99c4fba4a070d2694a2b9292dc4`, `app.css` `55b20ca12467026ca5a81df54877a76479d61e02cfd8345b7ab319a42d89dad8`, `manifest.webmanifest` `e035820b290e0c5fca9c4e94ed13dd1cd7398c7f84cac3a54ee0f455cd9aca68`; все четыре совпали побайтно.
- `tailscale serve status --json` показывает только HTTP listener на порту 80 для мобильного proxy `127.0.0.1:8840` и порт 8830; Funnel не настроен. GET на HTTPS/443 завершился `connection refused`.

Это подтверждает, что мобильный HTTP-сервис отвечает через текущую tailnet URL с этого же ПК, а отдаваемые статические файлы совпадают с проверенным checkout; публикация не использует Funnel. Это **не** подтверждает запрос с реального телефона, успешное сопряжение, выполнение задания, фактическую отмену задачи, работу Gmail или совпадение запущенного backend с исходниками `e1f574b`. HTTPS listener не активен; браузерные возможности secure-context (в частности service worker) могут быть недоступны. Пока backend выдаёт `SOURCE_IDENTITY_UNKNOWN`, runtime/source SHA reconciliation остаётся блокером.
