# Быстрый аудит багов локального Bossman, 11.10.2026

Статус: **PARTIAL**. Это ограниченный по времени прогон (≈7 минут, 6 параллельных аудиторов по зонам), а не полный аудит и не сертификат.
Большая часть кода в каждой зоне **не просмотрена**; отсутствие находки ≠ отсутствие бага.

## Идентичность

- Код: checkout `wt-unified-20261010`, ветка `feat/bossman-genjutsu-jeff-unified-20261010`.
  Аудит начат на `62d90843`; во время прогона другой исполнитель добавил локальный `d2d76834` (poker card book, не просмотрен).
  Remote-tip ветки на момент аудита: `8e3603bc` (локальные коммиты после него этим аудитом не пушились).
- Запущенный продукт: `127.0.0.1:8801`, установленная сборка `BOSSMAN-Windows-x64-6de18f8d9a08` (PID 3260), Ollama на `:11434`.
  `6de18f8d..62d90843` = **385 коммитов, из них 92 с `fix`**: запущенная у владельца версия не содержит этих исправлений.
- Тесты: системный Python 3.12.10; `tests/test_fable_hard_cap.py` — 14 passed (smoke).

## Подтверждено (воспроизведено на текущем коде)

| ID | Важность | Где | Дефект | Воспроизведение |
|---|---|---|---|---|
| D1 | P1 | `command-center/bcc/features/tools_terminal.py:48-49` | Правила «никогда, даже с одобрением» для force push / hard reset обходятся формой записи: `git -C . push --force`, `git push origin +main`, `git reset -q --hard`, `git -c a=b reset --hard` не попадают в HARD_DENY → становятся ASK, владелец может одобрить запрещённое. | `hard_deny_reason(...)`: `git push --force origin main` → True, остальные четыре → False. Нижний слой `TerminalPolicy.decision` не проверен — он может часть ловить. |
| F3 | P1 | `command-center/bcc/telegram_companion/remote_access.py:34-42,148-153` | Фраза-триггер по префиксу: «удалённый claude не включай» превращается в `/remote` и запускает удалённый доступ, хотя владелец просил обратное. Аналогично «перезагрузи компьютер не надо» → подготовка `/reboot` (там ещё нужно подтверждение). | `RemoteAccessMixin.phrase_command('удалённый claude не включай')` → `('/remote', 'не включай')`. |
| A3 | P2 | `command-center/bcc/mission_budget.py:124-129` | `_positive_float` пропускает `inf`/`1e309`: `max_cost_usd="inf"` в meta задачи молча снимает потолок стоимости прогона (документированный способ отключить — `0`). | `_positive_float('inf', 2.0)` → `inf`; `'1e309'` → `inf`; `'nan'` → `2.0`. |
| C1 | P2 | `command-center/bcc/db.py:853` | В обработчике неудачной миграции вызывается `log.error(...)`, но `log` в модуле не определён (`import logging` есть, `getLogger` нет). Любая ошибка `ALTER` кроме «duplicate column» превращается в `NameError`, исходная ошибка БД не логируется. | grep: `log` нигде не присваивается в `db.py`; строка 853 — единственное использование. Скрипт-репро не запускался. |

## Подозрения (только чтение кода, без репро)

| ID | Важность | Где | Суть |
|---|---|---|---|
| B1 | P1 | `features/control_plane.py:196-197` vs `api.py:1231-1236` | STOP-all не отзывает approval-leases (это делает только `/api/tasks/{id}/stop`). После retry той же задачи старое «да» снова действует (до 200 вызовов / 4 ч). Фикс: `revoke_for_task` внутри `TaskEngine.stop`. |
| F1 | P1 | `telegram_companion/remote_access.py:175-184` | После СТОП команда `/remote_stop` блокируется самим СТОПом — удалённый доступ, запущенный пультом, нельзя выключить до `/resume`. |
| F2 | P1 | `telegram_companion/console.py:433-463` | СТОП не отменяет уже подтверждённую перезагрузку (`shutdown /r /t 90`): нет `shutdown /a`, `reboot_pending` не сбрасывается. При `pc_control=false` `/stop` отвечает CONSOLE_OFF, а `/remote` и `/reboot` работают. |
| A1 | P1 | `provider_governance.py:188-195` | Если профиль long-run выключен после сборки адаптера, `GovernedAdapter.chat` вызывает платную модель напрямую без резерва и дневного лимита (нужно проверить, переиспользуется ли адаптер). |
| D2 | P2 | `features/tools_terminal.py:52-55` | Запреты на чтение секретов сравнивают сырой текст: `cat .e''nv`, `cat ~/.ssh/id_""rsa` не совпадают. |
| D3 | P2 | `features/tools_terminal.py:364,385-399` | `timeout` от модели не ограничен 300 с: после отмены процесс не убивается, строка `terminal_sessions` навсегда `running`; `timeout="nan"` — выход без ожидания. |
| D4 | P2 | `features/tools_terminal.py:291-306` vs `178-181` | Неявный `cwd` не входит в digest одобрения; смена `terminal.roots` между одобрением и запуском меняет каталог (внутри разрешённых корней). |
| A2 | P2 | `provider_governance.py:217-218` | Long-run ledger коммитит фактическую стоимость выше резерва без проверки остатка лимита (в отличие от `fable_cap._book_overflow`). |
| B2 | P2 | `api.py:866-867`, `1257-1262` | Нет нижней границы `limit`: `limit=-1` в SQLite = без лимита. |
| E1 | P2 | `tools/night_autonomy.py:~243` (коммит `62d90843`) | Повышенный лимит пересборок действует на все пересборки, пока есть `pending_proof`, а не на одну лишнюю. |
| E2 | P2 | `tools/night_autonomy.py:~240-247` | Кэш пересборки по `head` возвращает и BLOCKED-запись: после временной нехватки диска голова не пересобирается всю ночь. |

## Не просмотрено

`engine.py`, `scheduler.py`, `long_run.py`, `events.py`, `streaming.py`, `finalize.py`, `coding_session.py`;
`bossman_shared/fable_budget.py` (канонический ledger Fable), `providers.py`, `registry.py`, `spend_meter.py`;
`web_designer*.py`, `desktop*.py`, `plugin_security.py`, `html_text.py`; `features/chat_threads.py`, `review_gate.py`;
Jeff-бот (`bcc/pit/*`), `telegram_companion/adapters.py`, дедупликация `update_id`;
5 из 6 неотправленных коммитов (`2c7d1c68` batch append, `2ee65b44` sweep v3, `b22d24a2` retention и др.) и `d2d76834`.

## Следующий шаг

Для каждой P1 — падающий регрессионный тест, затем фикс (reproduce-before-fixing). Начать с D1 (проверить `TerminalPolicy.decision`) и F3/F1/F2 (пульт), затем B1.
Исправления в коде этим аудитом не вносились. North Star: уровень не меняется — `SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT`.
