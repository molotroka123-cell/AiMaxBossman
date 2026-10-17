# Доказательства и ограничения аудита мобильного пилота — 17.10.2026

## Зафиксированная точка

- Репозиторий: `molotroka123-cell/AiMaxBossman`
- Исходная ветка проверки: `origin/main`
- Проверенный HEAD: `e1f574b65699922e0f9306f1262f1520080b1262`
- Перед документационным коммитом: рабочее дерево чистое, Command Center не менялся относительно `85f952b56ee6326b086aec982e02b8e8283c22d6`.
- Проверка команды: `git diff --quiet 85f952b56ee6326b086aec982e02b8e8283c22d6 e1f574b65699922e0f9306f1262f1520080b1262 -- command-center`; результат — diff отсутствует.

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
| Живой телефон → домашний Bossman runtime | NOT_RUN |
| Реальное видимое управление мышью/клавиатурой | NOT_RUN |
| Реальная проверка mobile/global STOP | NOT_RUN |
| Реальный read-only Gmail сценарий с тестовым аккаунтом | NOT_RUN |
| Уведомление о новом входящем письме | NOT_RUN |
| Windows owner acceptance и ресурсный soak | NOT_RUN |

## Интерпретация

Успешные результаты выше подтверждают только перечисленные тесты и соответствующий исходный код. Исторические тесты обозначены их исходной точкой; старый dirty-файл, конфигурация, README или тестовый сценарий не считаются доказательством текущего работающего сервиса. Нет evidence живой модели, подключения телефона, устройства, почтового ящика или работы фонового watcher.
