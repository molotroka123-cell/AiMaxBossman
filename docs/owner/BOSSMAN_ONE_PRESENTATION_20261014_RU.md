# Bossman One — Презентация одного продакшен-кандидата (14.10.2026)

**Что такое Bossman (1 предложение):** Единая локальная операционная система владельца на Windows 11, управляемая из закрытого пульта Telegram, в которой оператор кода Qwen L1, агент рабочего стола Argon, генератор медиа (Z-Image / Qwen-Image-Edit / Wan 2.2), сверка отчётов 21–20 и наблюдатель Poker Vision работают на одном бэкенде `:8801` с безусловным приоритетом `STOP` и слиянием в `main` только по личной кнопке владельца.

- **Единая ветка-кандидат:** `release/bossman-one-20261014` (сведены `origin/main` `cd71cddc` + `feat/pc-agent-20261013` `fb9ccf30` + `feat/bossman-2-2-20261013` `08eaca59`; код-база слияния `f9e7d12a`).
- **Ночные документы владельца из `main` на месте:** `CHECKPOINT_20261013_NIGHT_RU.md`, `MASTER_PROMPT_PC_AGENT_20261013_RU.md`, `MASTER_PROMPT_PC_AGENT_CONTEXT_20261013_RU.md`.
- **Пин Конституции v2:** `7d845db367b3acfb58418ee9d844a4c887c512e4804e91fd708034667d768970` (`autonomy_loop: OK`).

---

## 1. Что умеет пульт сейчас (`команда → что делает → статус`)

| Команда пульта | Что делает | Статус |
|---|---|---|
| `/help`, `/status`, `/work_status` | Карточка здоровья `:8801`, инлайн-клавиатура (8 рядов), живой статус задачи оператора Qwen (модели, токены, лаб-ветка, тайминги) | **ПРОВЕРЕНО** (живой пульт + автотесты) |
| `/work <задача>` (read-only + `bossman_calls`) | Чтение файлов и безопасный белый список `GET/POST /api/motion-*` без правок репозитория | **ПРОВЕРЕНО** (`db5935c0b31d`, `87ceb78d18a4`) |
| `/work <задача>` (код/док в `lab/op-X`) | Правка в изолированной лаб-копии → обязательный `pytest` → откат попытки при падении → ревью `nemotron-3-ultra:free` → карточка `release_main` | **ПРОВЕРЕНО** (`c56a43b1d89c` → `#42 PENDING`; откат `d00bf4d25ca2`) |
| `/work_yes N` / `/work_no N` | Личная кнопка владельца для `release_main` (`git push origin lab/op-X:main`) или опасных команд | **ОЖИДАЕТ КНОПКИ ВЛАДЕЛЬЦА** (`#42` не нажималась агентом) |
| `/pc <цель>`, `/screen`, `/apps`, `/processes`, `/services` | Argon PC-Agent: снимок экрана, окна, процессы, службы, цепочка шагов с независимым верификатором и защитой от shell/path-обходов | **ЧАСТИЧНО**; обходы 1–5 исправлены в ветке `fix/pc-agent-security-20261014` (`23/23 passed` в `test_pc_agent_argon.py`), независимая проверка — не проведена |
| `/trust [мин]` / `/untrust` / `/stop` / `/resume` | Окно доверия 15 мин для обратимых шагов ПК, мгновенный сброс `/untrust` (работает даже во время `STOP`), глобальный `STOP` по шине `EventBus` | **ЧАСТИЧНО**; обходы 1–5 исправлены в ветке `fix/pc-agent-security-20261014` (`23/23 passed` в `test_pc_agent_argon.py`), независимая проверка — не проведена |
| `/img <промпт>`, `/edit <правки>`, `/edit_engine qwen\|uncensored` | Генерация фото (`Z-Image-Turbo`), правка с сохранением лица (`Qwen-Image-Edit` GGUF) и переключение движка правки (`qwen` / `uncensored` → `z_image_nsfw_v2` `-i <path> --strength 0.6`) | **ПРОВЕРЕНО** (`89/89 passed` в `test_companion_gen_refine.py` + `test_studio_sdcpp_provider.py` + `test_pc_agent_argon.py`) |
| `/earn`, `/earn_hypothesis`, `/boost` | Эмулятор тестового пилота с независимым верификатором и записью `quality_audit` в `simulated_ledger.jsonl`, 14-дневная проверка гипотезы `Fresh Vibes` (Instagram Direct, `$0` старт), цикл самоулучшения `/boost` (100% локально) | **ПРОВЕРЕНО** (`test_earning_emulator.py` + `test_pc_agent_argon.py`) |
| `/blender`, `/fincept`, `/vibe_trading`, `/oss` | Справочные адаптеры `MCP for Blender`, `Fincept Terminal`, `Vibe-Trading` оставлены выключенными (**`NOT_ACTIVATED`** по стоп-крану владельца) | **`NOT_ACTIVATED`** (выключены до отдельного запроса владельца) |
| `/video <промпт>` | Локальный рендер видео `Wan2.2 TI2V 5B` через Vulkan (`sd-cli`) | **ПРОВЕРЕНО ТЕХНИЧЕСКИ** (2s/5s клипы); коммерчески **NO-GO** (см. §4) |

---

## 2. Как запустить и как остановить (ровно 3 команды PowerShell)

```powershell
# 1. ЗАПУСК БЭКЕНДА :8801 (из единой ветки release/bossman-one-20261014)
& "C:\Users\asd\Bossman\venv-tests-20261012\Scripts\python.exe" "C:\Users\asd\Bossman\creative-runs\PULT-SOURCE-20261013\run_backend_source.py" "C:\Users\asd\Bossman\wt-pc-agent-argon-20261013"

# 2. ЗАПУСК ПУЛЬТА TELEGRAM (строго один poller на токен)
& "C:\Users\asd\Bossman\venv-tests-20261012\Scripts\python.exe" "C:\Users\asd\Bossman\creative-runs\PULT-SOURCE-20261013\run_pult_source.py" "C:\Users\asd\Bossman\wt-pc-agent-argon-20261013"

# 3. ОСТАНОВКА ПУЛЬТА И БЭКЕНДА :8801
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*bcc.telegram_companion*' -or $_.CommandLine -like '*bcc.main:app*8801*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```
*(Для пакетной сборки Windows ZIP: распаковать `BOSSMAN-Windows-x64-<sha>.zip` и запустить `Start-Bossman.cmd`.)*

---

## 3. Где логи и как откатиться

- **Логи живого бэкенда и пульта:**
  - `C:\Users\asd\AppData\Local\Bossman\one-bossman-logs\backend-source-20261013.log`
  - `C:\Users\asd\AppData\Local\Bossman\one-bossman-logs\companion-source-20261013.log`
  - `C:\Users\asd\AppData\Local\Bossman\one-bossman-logs\ollama-20261013.log`
  - Единый журнал шагов Argon PC-Agent (с цепочкой SHA-256 и read-view квитанций): `%LOCALAPPDATA%\Bossman\CommandCenter\pc-agent\audit.jsonl`
- **Как откатиться за 10 секунд:**
  1. Остановить процессы командой №3 выше.
  2. Запустить стабильный `origin/main` (`cd71cddc`): передать `"C:\Users\asd\Bossman\wt-pult-live"` вместо `"C:\Users\asd\Bossman\wt-pc-agent-argon-20261013"` в команды №1 и №2 (или запустить архив `BOSSMAN-Windows-x64-94e40cd17822\Start-Bossman.cmd`).

---

## 4. Сводка результатов 4 восстановительных потоков (14.10.2026)

1. **Кандидат №1 — Motion Studio (коммерческая гипотеза): вердикт `NO-GO`**
   - **Качество:** `1–2 / 10` (`motion_make_video` упал на `frame_luma_std = 1.66 < 8.0`; `Wan2.2 5B` без звука и lip-sync; `Wan2.2 A14B` — только веса на диске, в код не подключён).
   - **Скорость на Radeon 8060S (Vulkan):** 2s клип (`960×544`) = `61.4 мин`; оценка 15s (6 планов) = `3.9–7.6 ч`; 30s (10 планов) = `6.5–15.1 ч` (против порога `< 60 мин`).
   - **Лицензии:** `Fish Speech 1.5` (`CC-BY-NC-SA-4.0`) и `FaceFusion` (требуется Commercial License) блокируют коммерческую продажу.
2. **Кандидат №2 — Bossman Reports 21–20 (`tools/bossman_business.py` / `Reports-Import.cmd`): вердикт `TECH PASS / DEMAND CONDITIONAL`**
   - На синтетическом корпусе `creative-runs/exchange-reports-demo-20261014` проведены 4 прогона (`run1_initial`, `run2_after_restart`, `run3_with_csv_rates`, `run4_html_parity`) и 5 холодных замеров: медиана **`0.374 с`**, себестоимость **`$0.00`** (`0` токенов, `0` сетевых вызовов), `100%` совпадение SHA-256 `deals.csv` / `disputed.csv` / `report.md` после рестарта и между JSON/HTML.
   - Живой поиск спроса через `bcc.features.web_research` + `osiris` + `browser_control` (`live_demand_probe_raw.json`) подтвердил рыночные заказы на парсинг/выгрузку Telegram → Excel/БД на Kwork (`500–15 000 ₽`), но узкий нишевый запрос «сверка обменника 21–20» публично не публикуется из-за приватности.
3. **Поток №3 — Poker Vision (`apps/poker-vision`):**
   - Исправлены 3 пробела безопасности/объяснимости: (а) конкурирующее чтение на уже открытой карте борда (`board[i]`) теперь попадает в `cm.pending` и блокирует ход (`PENDING_CHANGE board[i]`); (б) `validate_state` проверяет карты шоудауна на дубликаты с героем/бордом (`SHOWDOWN_DUPLICATE_CARD`) и сбрасывает невозможный дубликат в `None`, не трогая валидные карты; (в) словарь `EXPLAIN` дополнен русскими пояснениями для всех 18 новых кодов отказа адаптеров.
4. **Поток №4 — Живая приёмка этапа 2.2 (`docs/owner/READY_FOR_PULT_20261014_RU.md`):**
   - Подтверждены `/work_status`, read-only задача с `bossman_calls` (`db5935c0b31d`), docs-задача `c56a43b1d89c` до карточки владельца **`#42`** (`release_main`, `PENDING`, `VERDICT: APPROVE` от `nemotron-3-ultra:free`) и задача-ловушка `d00bf4d25ca2` (2 реальных отката `rollback=done` на попытках 1 и 2, успех на попытке 3).
   - На момент первичного отчёта бэкенд `:8801` находился в состоянии `SOURCE_IDENTITY_STALE` до перезапуска; после перезапуска из чистого SHA ветки `fix/pc-agent-security-20261014` статус `source_identity: PASS`.
5. **Поток №5 — Сторонние интеграции (`NOT_ACTIVATED` по стоп-крану владельца):**
   - **MCP for Blender** (`mcp-for-blender` v2.0.3, MIT), **Fincept Terminal** (`FinceptTerminal`, AGPL-3.0 Open Source) и **Vibe-Trading** (`vibe-trading` v0.1.15, MIT, `PAPER_ONLY=True`, `real_money=0`) оставлены выключенными (**`NOT_ACTIVATED`**) до лицензионной проверки и отдельного слова владельца.
6. **Поток №6 — Косметическое обновление UX дашборда и пульта (со скриншотами ДО / ПОСЛЕ) и разведка edit-кандидатов:**
   - Вынесены наверх кнопочная панель `STOP` / `Снять STOP` и индикатор активных локальных моделей с переключателем движка правки (`qwen` / `uncensored` → `z_image_nsfw_v2`) на `#/control` и `#/images`, добавлены табличные стили `.table`, карточки открытых интеграций на `#/oss` и адаптивная мобильная вёрстка (`390×844`). Скриншоты сохранены в `docs/owner/screenshots/before/` и `docs/owner/screenshots/after/`.
   - Живой `Qwen-Image-Edit-2509-Q4_K_M.gguf` не подменялся; для будущей лабораторной проверки зафиксированы два открытых кандидата: `ChrisColeTech/qwen-image-edit-uncensored-v1.1-GGUF` (HF, ~54 загрузки, проверка совместимости с `sd-cli` и сохранения лица) и `Phr00t/Qwen-Image-Edit-Rapid-AIO` (4 шага, v5–v19 NSFW-capable, проверка формата весов и лицензии).

---

## 5. Что НЕ доказано и 1 следующий шаг

- **Что НЕ доказано:**
  1. Слияние в `main` по кнопке владельца (`/work_yes 42`) — кнопка намеренно оставлена владельцу; **этап 2.2 не закрыт до нажатия**.
  2. Полный живой GUI-прогон Argon PC-Agent из реального сообщения Telegram в окно Windows с доставкой файла в чат (сейчас доказано контрактными/интеграционными тестами и FastAPI lifespan).
  3. Официальная тестовая среда Telegram Stars (на паузе по решению владельца) и выживание после перезагрузки самой Windows 11.
- **Один следующий шаг:** Владельцу в пульте Telegram нажать кнопку `✅ Разрешить` (`/work_yes 42`) для проверки живого слияния `lab/op-c56a43b1d89c:main`, после чего отправить одну живую команду `/pc Открой Блокнот, напиши тест и пришли скриншот`.
