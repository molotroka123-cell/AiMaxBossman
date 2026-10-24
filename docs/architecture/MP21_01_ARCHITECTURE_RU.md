# 01. Архитектура Bossman 2.1 (сверено с деревом 11.10.2026)

Сверено с кодом ветки `feat/bossman-genjutsu-jeff-unified-20261010` после слияния MP21 (последний слитый коммит
`3a6410bb`). Это описание того, что есть в файлах, а не того, что планировалось. Статус каждой функции (works / partial /
missing) и построчный инвентарь: `docs/archive/2026-10/owner/MP21_PROJECT_MAP_20261011.md` (186 модулей `bcc`, 55 страниц UI, 143 записи
`tools`). Слово «работает» здесь означает только «есть исполненный тест с rc 0», см. `docs/owner/runs/MP21_E_INTEGRATION_CHECKS_20261011.md`.

## 1. Слои

| Слой | Где | Что делает | Проверка |
|---|---|---|---|
| Command Center (backend + UI) | `command-center/bcc` (FastAPI, SQLite через SQLAlchemy/aiosqlite), `command-center/ui` (статические страницы, `ui/pages`) | единый backend: чат, задачи, очередь, провайдеры моделей, студии, автономия, Jeff | `command-center/tests` |
| Ядро Bossman | `bossman-core/bossman` (`apprentice`, `computer_operator`, `trading_learning`; рядом пакет `bossman_v3`) | обучение подмастерья, управление компьютером, paper-торговля, память | `bossman-core/tests` |
| Общий слой | `bossman_shared/privacy.py` | политика приватности запроса и `BOSSMAN_LOCAL_ONLY` | `command-center/tests/test_local_only_switch.py` |
| Автономия | `command-center/bcc/autonomy/*` (27 модулей) | ограниченный цикл самоулучшения, конституция, STOP, аренда писателя, журнал | см. `MP21_03_AUTONOMOUS_MODE_RU.md` |
| Jeff (участник чатов) | `command-center/bcc/pit/*`, `bcc/pit/j2/*` | отдельный локальный участник, свои лимиты и очередь | `test_jeff_*` |
| Студии | `bcc/studio` (облачный/локальный генератор с бюджетом), `bcc/video_studio`, `bcc/direct_gen` | изображения, видео, музыка, замена лица | `test_studio_*`, `test_direct_gen_*` |
| Видео-конвейер Genjutsu | `tools/genjutsu`, `tools/video_gate`, `tools/trend_edit` | локальные гейты анимации, замена лица/одежды/фона, монтаж | `tests/test_trend_edit.py`, `test_background_swap_gate.py` |
| Приложения | `apps/*` (`poker-botlab`, `poker-vision`, `poker-lora`, `token-launch-helper`, `solana-volume-suite` и др.) | отдельные манифестные приложения | у каждого свои тесты |
| Торговля (paper) | `bossman-core/bossman/trading_learning`, `solana_volume_suite` (карантин) | см. `MP21_09_TRADING_AND_TOKEN_BOTS_RU.md` | |
| Инструменты | `tools/*` (в т.ч. `exchange_volume`, `ci_secret_scan.py`, `exact_sha_certify.py`, `cu_acceptance`) | CLI вне процесса backend | |

## 2. Запуск и данные

* Вход: `python -m bcc` (`command-center/bcc/__main__.py` -> `bcc.app.main`), консольный скрипт `bcc` (`pyproject.toml`).
* Порт по умолчанию `BCC_PORT=8800`, хост `127.0.0.1` (`bcc/config.py`). Установленный продукт владельца на этой машине
  слушает :8801 (задаётся запуском, не кодом).
* Данные не в git: `BCC_DATA_DIR`, иначе `<repo>/command-center/data` для исходной установки, иначе
  `%LOCALAPPDATA%\Bossman\CommandCenter` (Windows). БД `bcc.db`, ключ шифрования и токен UI лежат там.
* Автономия хранит своё в `<data_dir>/autonomy` (или `BOSSMAN_AUTONOMY_ROOT`).

## 3. Поток запроса

запрос (UI / CLI / Telegram-пульт) -> `bcc/api.py` -> `engine.py` (очередь, повтор, отмена) -> адаптер провайдера
(`bcc/providers.py`) -> перед выходом наружу `assert_provider_egress` (`bossman_shared/privacy.py`) -> результат ->
событие/журнал. Один backend обслуживает окно, CLI и Telegram-пульт (документ `docs/archive/2026-10/owner/OWNER_RUN_20261008.md`).

## 4. Границы доверия

* Облачные провайдеры по умолчанию не получают приватные запросы (`private`/`local_only`), а при
  `BOSSMAN_LOCAL_ONLY=1` блокируются все облачные виды и внешние хосты на границе провайдера (но не веб-поиск, Gmail,
  Telegram, скачивание моделей: см. `MP21_10_SECURITY_LIMITS_RU.md`).
* Любое действие с деньгами, внешними сообщениями, учётными данными и продом требует владельца
  (`bcc/autonomy/policy.py`, правило «always-the-user»).

## 5. Чего в архитектуре нет (не выдумывать)

* Нет второй GPU и распределённого инференса (одна Radeon 8060S; `ggml-rpc` собран, удалённый хост не настроен).
* Нет глобального аппаратного hotkey остановки (в репозитории нет `RegisterHotKey`).
* Нет доказанного 24-часового непрерывного запуска (`docs/autonomy/BOSSMAN_2_1_AUTONOMOUS_COMPUTER_OPERATOR.md`).
* Нет исполняемого определения ступеней лестницы North Star: ступени живут в документах и данных сайта
  (`docs/archive/2026-10/owner/MP21_NORTH_STAR_TREE_20261011.md`).
