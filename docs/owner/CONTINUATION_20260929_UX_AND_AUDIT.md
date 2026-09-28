# CONTINUATION — обязательный к закрытию и прогону (2026-09-28, ночь)

> Этот файл — КОМАНДА владельца следующей сессии. Не считать выполненным, пока
> каждый пункт чеклиста не отмечен фактом (тест/лог/скрин). Приоритет P1, сразу
> после переноса аудита в PR #84.

## Часть 1 — Bossman Command UX v0.1 (пульт Jeff)

Статус на утро: фоновый агент собирал v0.1 (его сводка появится в этой сессии);
спека и архитектура зафиксированы, живой runtime НЕ патчился (сознательно).

### Что уже есть
- Архитектурное решение: overlay-файл `%LOCALAPPDATA%\Bossman\jeff-settings.json`,
  читаемый `build_participant_context()` при каждом сообщении (mtime-кэш).
- Схема: `{"version":1,"defaults":{"system_extra":"","behavior_scales":{initiative,curiosity,depth,brevity,warmth,humor,directness,creativity: 0..10}},"users":{"<telegram_id>":{...}},"budgets":{"usd_per_day":2.0,"usd_per_job":1.0}}`
- Точка вживления: `command-center/bcc/pit/participant_context.py::build_participant_context()`
  — после PIT_ASSISTANT_SYSTEM добавить `system_extra` из overlay и
  `behavior_system_text(merged_scales)`; per-user lookup по telegram_id
  (через `settings.people` / `vault.key_for_telegram`).
- Ожидаемые файлы от агента: `command-center/bcc/features/jeff_settings.py`,
  `command-center/bcc/api.py` (регистрация Feature), `command-center/ui/pages/jeff_settings.js`,
  `command-center/tests/test_jeff_settings_overlay.py`,
  `bossman_shared/jeff_settings.py` (merge_overlay), `docs/owner/BOSSMAN_COMMAND_V01.md`.

### Чеклист закрытия (Definition of Done) — ОБЯЗАТЕЛЬНО
1. [ ] Проверить/доделать файлы агента (список выше); что-то отсутствует — дописать.
2. [ ] `python -m pytest command-center/tests/test_jeff_settings_overlay.py -q` — зелёный.
3. [ ] `python -m compileall -q command-center/bcc bossman_shared` — без ошибок.
4. [ ] Вживить hook (сниппет в `docs/owner/BOSSMAN_COMMAND_V01.md`) в live-путь
       build_participant_context; перезапустить Jeff (one-bossman stop/start jeff).
5. [ ] Прогон-приёмка владельцем: пресет «Дерзкий» → Jeff отвечает в другом тоне;
       «Откат к обычному» → сток; per-user override не влияет на других; JSON валиден.
6. [ ] Бюджеты из пульта: связать с governor (отдельный шаг, не ломать существующие гейты).
7. [ ] Коммит + пуш ветки + PR (мерж — только по команде владельца).

## Часть 2 — хвосты аудита RC19
1. [ ] Вписать финальные числа полного Command Center этой сессии
       (`wt19-audit-int\artifacts\audit-rc19\cc_full_junit.xml`) в отчёт
       `AUDIT_RC19_REPORT_20260928.md` (сейчас там «в процессе»).
2. [ ] Решение владельца по `test_v3_cross_layer_e2e` (недозаполненная
       ActionReceipt: `compound.py::_action_receipt` + `action_receipt.py::fresh/verified`).
3. [ ] Перенос в PR #84 (ветка уже запушена: `claude/rc19-audit-integration` @
       `a53623ae`, включает фиксы + отчёт; НЕ сливать без команды; сохранить
       3 PR-коммита 84f5e0ac-линии: cc548917, 445c32a0, 84f5e0ac).
4. [ ] Токен бота Jeff: достать из живого процесса/перенастроить хранилище
       (credentials.enc не открывается ключами vault) → ночные рассылки от Jeff,
       а не от @penisacemagic_bot.
5. [ ] Один чекаут + venv (убрать editable-hijack; guard уже в тестах).
6. [ ] Флейк `test_cross_process_concurrent_reserve_never_exceeds_cap` —
       стабилизировать (пустой stdout подпроцесса при холодном старте), не ослабляя проверки.
