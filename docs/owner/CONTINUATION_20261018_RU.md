# Как продолжить 18.10.2026

Закрыто 17.10 около 14:40 по просьбе владельца («завершай… выключай все»). ПК выключен.

## Где код
- Репозиторий: `C:\Users\asd\Bossman\wt-checkpoint-20261015`, ветка `feat/war-procurement-terminal-v1`; `main` на GitHub совпадает с ней.
- Лишний файл `command-center/tgapp.py` не наш — не коммитить, выяснить чей.
- BossBlocks: `C:\Users\asd\Bossman\games\bossblocks-deepseek-20261017`, ветка `deepseek-video-20261017` (11 коммитов, remote нет). В основном чекауте `games\bossblocks` — незакоммиченный крафт от 02.10, не трогать.

## Что сделано 17.10 (в main)
| Коммит | Что |
|---|---|
| e1f574b6 | Мобильное приложение: вкладка «Видео» (TI2V, S2V, face swap, время, очередь) |
| 85f952b5 | Accountant: лофт-интерфейс + рамка в «Приложениях» |
| db1d6dee | Очередь фото: проценты, шаг, ETA |
| cf8cf570 | Фото: шаги на модель + примерное время |
| b861ec24 | DeepSeek V4.1 Flash, лимит 2 $/день |
| 7f409685 | Русский интерфейс, Gemma 4 uncensored по умолчанию в телефоне |
| bb831280 | Кнопка «🛰 Терминал» в пульте |
| 3168a141 | Джефф: проверка модели 90 с (не выгружает модель) |
| 559d3c5e | Локальные модели: одно системное сообщение |

Плюс навык `.agents/skills/bossblocks-video-style/` (коммит вечером 17.10).

## Как поднять утром
1. Включить ПК. Ollama, Tailscale стартуют сами.
2. Бэкенд :8801 — ОБЯЗАТЕЛЬНО с `BCC_DATA_DIR=%LOCALAPPDATA%\Bossman\CommandCenter`,
   `PYTHONPATH=...\wt-checkpoint-20261015\command-center`, `BOSSMAN_ALLOW_PAID_CLOUD=1`. Без `BCC_DATA_DIR` он стартует на пустой базе и токен владельца не принимается.
3. Пульт: запуск из исходников (логика — `owner_one_bossman.child_env` + `companion_env`, PYTHONPATH на worktree, `python -m bcc.telegram_companion --config ...\telegram-companion\config.json`). Проверить, что нет флага `one-bossman\hold-companion`.
4. Джефф: так же, `python -m bcc.pit.cli watch --data-dir ...\CommandCenter`. Модель — `gemma4-31b-uncensored:latest` (pit-v1.7\config.json), стиль стандартный (jeff-settings.json defaults `{}`). Закрепить Gemma: keep_alive -1.
5. Мобильное :8840, War Terminal :8830, Accountant :8910, Shorts :8860 — проверить, что отвечают; `tailscale serve status`.
6. Телефон: если 401 — ввести код входа заново.

## Открытые задачи (по порядку)
1. **Ассистент с телефона, этап 2** — ждёт «да» владельца. Самое срочное: в `telegram-companion\config.json` `claude_permission_mode=bypassPermissions` + задачи `BossmanClaudeRemote`, `BossmanNightAutonomy` тоже без подтверждений → вернуть подтверждения. Далее: одна версия пульт/бэкенд, белый список прокси мобильного, одноразовый `/work_yes`, одобрение `pc.desktop_act`, отсекать чужих на входе, один STOP = stop-all, перезапуск по `/health`, живые проверки + 24 ч.
2. **Face swap Kisliy** (согласие 14.10): `creative-runs\KISLIY-ALLSWAP-20261017`. Готовы 3 hyperswap (черновики в пульте), замер плохой: заменено ~8% лиц (толпа). Сырые inswapper/simswap есть, ghost прерван. Завтра: свап 1–3 главных лиц, QA по всем кадрам (`tools/video_gate/`), без gfpgan-пластика.
3. **BossBlocks 1.0**: водопад, частицы, сильнее свечение колец, низ острова, потом крафт от 02.10 и релизная сборка. Схема: DeepSeek пишет код, проверка по картинке — отдельно. Godot: exit 0 ≠ успех, смотреть SCRIPT ERROR.
4. **Видео в приложении**: S2V требует запущенный ComfyUI; первый реальный тест TI2V 2 с 480×272 (~4 мин); TI2V пишет WebM — для iPhone нужен MP4.
5. Владелец: включить HTTPS в Tailscale (login.tailscale.com → DNS → Enable HTTPS) — тогда «Терминал» откроется как Mini App.

## Деньги
Лимиты в `autonomy\long_run.json`: DeepSeek 2, GLM 2, Haiku 2, Mistral 0 (в день). 17.10 потрачено: DeepSeek 0,125 $.
