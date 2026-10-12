# Long-run handoff (11.10.2026, вечер → ночь)

Коротко для следующего исполнителя и владельца. Полный аудит: `PRODUCTION_READINESS_AUDIT_RU.md`.

## SHA и PR

- Ветка `feat/bossman-genjutsu-jeff-unified-20261010`, HEAD — коммит этого аудита (fix(ci) для test_companion_remote_access + документы), родитель `3910be96`; точный SHA: `git log -1`, PR #108 → `main`.
- Архив кандидата из `9520a45a610a`: `dist-9520a45a/BOSSMAN-Windows-x64-9520a45a610a.zip`,
  SHA256 `ebd82c533b0fd897df7f68555d864d784953ef9dc0bc056db2651dfda5fa7cc8`,
  `BOSSMAN_BUNDLE_ACCEPTANCE=PASS`. Распакован в `dist-9520a45a/install-test/` для вечернего
  теста (см. раздел «Результаты»).

## Выполненные команды и результаты (факты)

| Команда | Результат |
|---|---|
| `pytest tests/test_companion_remote_access.py` (command-center) | 25 passed |
| `pytest -k "companion or telegram_companion or agent_bridge or claude_bridge"` | 318 passed, 1 skipped |
| `pytest apps/poker-vision/tests/test_recording_dataset.py` | 16 passed |
| `tools/claude_remote_service.ps1 -Name Bossman-Selftest-Remote -Once` | путь сеть → стартер → Claude пройден; Claude отказал второму экземпляру в той же папке (ожидаемо), код 1 в журнале |
| `tools/build_windows_bundle.py --zip --profile release` (чистый worktree `wt-build-9520a45a`) | BUILT, 23 868 файлов, LOCKED |
| `tools/verify_windows_bundle.py --expected-sha 9520a45a…` | PASS, exit 0 |
| `tools/security_audit.ps1` | FAIL 0 / WARN 5 |
| `/api/browser` сессия → Wikipedia → DOM → скриншот | PASS, vision-проверка совпала |
| `app-support/bundle_evening_test.py --full --ui-sweep` (распакованный архив) | доктор OK, приёмка PASS (auth, restart persistence), ui-sweep TIMEOUT 900 с → OWNER_EVENING_RESULT=FAIL; live-model OWNER_REQUIRED |
| `app-support/installed_ui_sweep.py` отдельно, без лимита | 2693 с, 630 кликов, 53 страницы, REVIEW_REQUIRED, 11 замечаний → `docs/evidence/ui-sweep-9520a45a/` |
| CI `9520a45a` | зелёные все, кроме Intelligence Preservation (владелец) и Command Center CI (cancelled push'ем) |
| CI `3910be96` | всё зелёное, кроме Intelligence Preservation (владелец) и Command Center CI (5 тестов remote_access падали на Linux: подмена os.name → WindowsPath; исправлено флагом IS_WINDOWS в коммите этого аудита) |

## Что пережило бы перезагрузку (подготовлено, реальный reboot NOT_RUN)

- Задачи входа: `BossmanOne-1-Backend`, `BossmanOne-2-Jeff`, `BossmanOne-3-Companion`
  (установленная сборка), `BossmanJeff2`, `BossmanClaudeRemote` (keeper, bypassPermissions),
  `BossmanKeepAwake`. `BossmanLocalGenBench` отключён.
- Отложенная: `BossmanNightAutonomy` — 11.10 18:46 → до 08:00, цикл 45 мин, отчёты в пульт.
- Шаг владельца до reboot: `owner-autologon-20261011.ps1` (вход сразу на рабочий стол, пароль
  не хранится), `owner-hardening-20261011.ps1` (SSH).

## Актуальная ссылка

Remote Control «Bossman-Owner-PC» (запущен Codex, работает до перезагрузки):
https://claude.ai/code/session_017MvqqBzRS5skEE9mTaZv4Q. После перезагрузки — `/remote_status` в пульте.

## Что осталось

1. ~~Command Center CI~~ зелёный на `4c29b274` (run 38185553138).
2. Владелец: автологон → `/reboot` → `/remote_status` Connected → вход с телефона из мобильной
   сети (OWNER_VERIFICATION_PENDING) → комментарий Intelligence Preservation.
3. Ночной прогон: `docs/evidence/night-20261011/report.md` утром; если Claude был недоступен —
   диалог помечен CLAUDE_UNAVAILABLE, Bossman продолжал сам.
4. Дерево: CI-улики на `3910be96` → `tree_registry_sync.py registry --ci-evidence` → публикация.
5. Не считать готовым: локальная модель без fallback, Motion V2 оценки, Poker обучение, Fleet
   двух устройств, North Star выше INFRASTRUCTURE_PRESENT.

## Вердикт

**NOT_READY для production release; READY_FOR_OWNER_RUN для кандидата `9520a45a610a` (CC CI зелёный на `4c29b274`, архив и приёмка PASS).** Один следующий шаг: владелец запускает
`owner-autologon-20261011.ps1`, затем `/reboot` из пульта.

## Круг 11.10, поздний вечер (после `21fadc22`)

Коммиты (локально до завершения Command Center CI `21fadc22`, затем fast-forward push):
`0b768cfe`, `42b41991` ночной раннер; `71ee3c5d`, `eb90062e` UI; `7e1eca94` уроки дня.

**Ночной раннер `tools/night_autonomy.py`.**
- Найден реальный дефект: архив проверялся против HEAD. После любого нового коммита каждый ночной цикл падал бы на верификаторе.
- Кэш проверок теперь привязан к SHA256 архива, его source SHA и установленному SHA.
- Если после сборки изменился код, раннер пересобирает архив из чистого worktree HEAD и проверяет новую сборку (verify, вечерняя приёмка, обход UI).
- Ответ Claude — заявка (CLAUDE_ANSWERED / UNAVAILABLE / FAILED). FIX_PROVEN ставится только на пересобранном архиве, если ошибок обхода стало меньше, а verify и вечерняя приёмка не хуже. Иначе FIX_NOT_PROVEN с причиной, а коммит без кода — CLAIM_ONLY.
- Независимое ревью в два круга (агент-ревьюер): 10 находок, затем перепроверка, все закрыты.
- 34 теста на py3.11 и py3.12: STOP, удалённый STOP, дедлайн, бюджет шага, недоступный и rate-limited исполнитель, заявка против доказательства, ключ кэша, реальное убийство дерева процессов.

**Замечания обхода UI 9520a45a.**
- Закрыто 7 error-пунктов: 6 кнопок тестового окна Poker давали 403, «Калибровать» давала 400. Теперь кнопки неактивны с причиной, пока нет условия.
- Закрыто 4 пункта disabled_silent: «Заменить лицо», «Найти зоны», «Отменить», «Повторить». Теперь у каждой есть причина в подсказке.
- Ревью UI нашло, что «Калибровать» стартовала активной. Исправлено в `eb90062e`.
- Открыто 4 пункта:
  - «Малиновый»: обработчик чисто клиентский, запрос пришёл от другого компонента, нужен прицельный повтор.
  - 2 безымянных элемента direct-gen исчезают при перерисовке.
  - «Проверить аудиоконтур»: долгая синхронная самопроверка, кандидат в фоновую задачу.
- Доказательство исправлений — ночной обход пересобранного архива.

**Уроки в память Bossman.** 14 уроков дня (`docs/owner/bossman_lessons_20261011.json`) записаны в живую память через `tools/import_operational_lessons.py`: добавлено 14, отклонено 0, все UNVERIFIED. Узнавание доказано: 4 из 4 настоящих строк ошибок находят нужный урок, поиск по-русски тоже. В контекст чата (`context_os`) уроки сами не попадают. Они читаются движком подмастерья (негативные уроки перед действием) и поиском. Влияние уроков на результат не доказано.

**Локальная находка.** `test_replay_source_shows_live_stream…` (Poker e2e) падает локально на Windows и на `21fadc22`, и на `4c29b274`, а в CI на Linux проходит. Это окружение, не регрессия коммита Codex. Записано как урок OPEN.

**North Star.** Уровень не повышается: SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT. Первый кандидат на SELF_REPAIR_SINGLE_CYCLE_PASS — ночной FIX_PROVEN, если Claude исправит пункт обхода и пересобранный архив это подтвердит. TRANSFER, 24H/48H soak и выручка требуют дней реальной работы и в одной сессии недостижимы.
