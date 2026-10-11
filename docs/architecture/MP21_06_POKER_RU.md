# 06. Poker: Poker Train, BotLab, Vision, Lora (сверено 11.10.2026)

Полная матрица требований и статусов: `docs/owner/runs/MP21_B_POKER_REQUIREMENTS_MATRIX.md`; аудит расчётов:
`docs/owner/runs/MP21_B_POKER_CALC_AUDIT.md`; workflow 8.2: `docs/owner/runs/MP21_B_POKER_WORKFLOW_8_2.md`.
Здесь только состав, запуск и границы.

## 1. Состав

| Часть | Где | Репозиторий | Что |
|---|---|---|---|
| Poker Train (приложение владельца, v2.2.0) | `C:\Users\asd\Bossman\apps-local\pokertrain` | ОТДЕЛЬНЫЙ: `molotroka123-cell/pokertrain`, master `6bc72bf` | тренажёр, движок раздач JS, дриллы, статистика |
| Правки аудита MP21-B | worktree `wt-mp-b-pokertrain-20261011` | pokertrain, локальная ветка `mp/b-poker-20261011` @ `2f3171f` (не отправлена) | `cd78920` сайд-поты/рейк/мин-рейз/seed/CI, `4b6f8f3` единый VPIP, `79ab05a` `tools/simulate-session.mjs`, `2f3171f` `docs/RUN_LOCAL_RU.md` |
| BotLab | `apps/poker-botlab` | Bossman (порт `1e3d25d0`, `6d9f2020` в единую ветку) | Python-движок, арена, `report`, рейк |
| Vision | `apps/poker-vision` | Bossman | экранный бот-наблюдатель, evidence матча 10.10 |
| Lora | `apps/poker-lora` | Bossman | точный решатель реки (26 тестов) |

Исходники pokertrain в Bossman НЕ копировались: только SHA. В рабочем дереве `apps-local/pokertrain` есть незакоммиченное
изменение `package-lock.json` (не наше, не трогалось).

## 2. Запуск (воспроизводимо, локально, без сети)

```powershell
cd C:\Users\asd\Bossman\wt-mp-b-pokertrain-20261011
npm test                                              # 28 тестов (из отчёта MP21-B)
node tools/simulate-session.mjs --hands 300 --seed 11 --stake NL10 --out session.json
cd <Bossman>\apps\poker-botlab
python -m botlab report --history <путь>\session.json --big-blind 10 --out out\history_report
python -m pytest -q -p no:cacheprovider tests         # rc 0 (проверено при интеграции 11.10)
```

Выход отчёта: `REPORT.md` + `report.json` с sha256 входа, размер выборки, 95% ДИ, статус рейка, раздел «Limits».
Пустой/отсутствующий файл даёт отказ с кодом 2/3, без выдуманного отчёта.

## 3. Что доказано

* Оценщик рук JS совпал с эталоном по всем 2 598 960 рукам; Python-оценщик сверен с PokerKit (20 000/20 000).
* Исправлены дефекты: расчёт сайд-потов (главный), рейк не применялся, мин-рейз, пропуск хода перед all-in, парсер HH,
  VPIP в 12 местах, ничья в эквити. Тесты написаны ДО правок и красные прогоны сохранены
  (`MP21_B_POKER_evidence/tests_first_baseline.txt`).
* Сидированная симуляция воспроизводима (одинаковый sha256 при одном seed). Сохранение фишек: сумма + рейк = 0.
* Пример прогона (300 раздач, герой-правило, NL10, рейк 4.5%): -114 bb/100, 95% ДИ [-260; +32] включает 0: вывода о силе нет.
  Для ±5 bb/100 нужно порядка 256 000 раздач.

## 4. Границы и пробелы

| Пункт | Статус |
|---|---|
| Матч «бот Bossman против Poker Train владельца» 11.10 | в этом треке НЕ запускался; есть только малый матч 10.10 (36 решений, 25 раздач, +4.8 bb: вывода о силе делать нельзя) |
| Короткий all-in не переоткрывает торги | НЕТ (известное отклонение, не исправлялось) |
| Происхождение таблиц диапазонов и соответствие солверу | НЕ проверено |
| Claude-бот HARDCORE и Screen Parser | ходят в облачный API, не запускались; есть риск политики использования (аудит §5) |
| Браузерные воркеры `simWorker.js`, `cfrWorker.js`; UI-прогон | код есть, не запускались |
| Принято заказчиком / оплачено | NOT PROVEN (клиента, договора и платежа нет) |
