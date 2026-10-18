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

## Дополнение 17.10, вторая половина дня

**Автозапуск теперь из исходников.** `C:\Users\asd\Bossman\one-bossman\owner_one_bossman.py` получил флаги `launch … --src <command-center> --python <Python312>` (копия до правки: `owner_one_bossman.py.bak-20261017-src`, файл вне git). Задачи `BossmanOne-1-Backend`, `-2-Jeff`, `-3-Companion` запускают код из `wt-checkpoint-20261015\command-center` системным Python 3.12. Пульт после убийства старого поллера ~40 с отвечает CONFLICT — просто перезапустить.

**Разгрузка:** Джефф на паузе (`one-bossman\hold-jeff`), Ollama-модели выгружены. Снять: `owner_one_bossman.py launch jeff --home … --data-dir … --src … --python …`.

**Мобильное :8840 отдаёт СТАРУЮ копию UI** (`/m/app.js` без `sideFor`). Найти, откуда запущен `python -m bossmobile`, перезапустить из `apps/bossman-mobile` с сохранением входа.

**Ветка `wip/ref-tags-20261017` (c8faf987, на GitHub, НЕ в main, не проверена):** формат/размер по референсу («как у фото W×H») для edit-фото и видео; @-отсылки (@1, @2 → «image 1/2») — `bcc/direct_gen/ref_tags.py`, `bossmobile/reftags.py`, правки `sdcli.py`, `service.py`, `features/direct_gen.py`. Агент остановлен до тестов.

**Пульт** очищен владельцем (всё младше 48 ч). Получатель один (`assert len(people) == 1`).

**«Гречка»** — открывать только по прямой просьбе. Файлы без расширений (29 jpg + 10 mp4); `grechka-retrieve.ps1` надо научить ставить расширение.

**Облачные uncensored** — ждут «да» + лимит: Atlas Cloud (Wan 2.2 Turbo Spicy ~0,02 $/ролик, Seedance 1.5 Pro Spicy ~0,049 $), Venice (фото), OpenRouter Venice Uncensored (текст, free). Личные фото в облако нельзя. Альтернатива — uncensored-доработки Wan 2.2 локально.

## Промпт на 18.10

> Продолжаем Bossman. Прочитай `docs/owner/CONTINUATION_20261018_RU.md`. Проверь после включения: бэкенд :8801 (из исходников, BCC_DATA_DIR), пульт, мобильное :8840; Джефф на паузе — без моей команды не поднимать. Затем:
> 1. **Мобильное приложение:** перезапусти из worktree (свежий UI); доведи `wip/ref-tags-20261017` — тесты, живой короткий прогон edit с @1/@2 при свободной GPU, скриншот 390px, потом в main.
> 2. **Агент с телефона, этап 2:** убрать `bypassPermissions` у Claude/Codex в пульте и у задач `BossmanClaudeRemote`, `BossmanNightAutonomy`; одноразовый `/work_yes`; отсекать чужих на входе; один STOP = stop-all. Каждый шаг: тест → живая проверка из Telegram → отчёт в пульт.
> 3. Отчёты только мне в пульт, с экономией vs облако. Платное — только с моим «да».

## Итог прогона 18.10 (дописано при закрытии)

Полный отчёт: `docs/owner/RUN_REPORT_20261017_18_RU.md`. Аудит с уровнями доказательств: `docs/audits/2026-10-18-run-audit/README.md`.
Bossman продолжает работать сам: бэкенд, пульт, мобильное, War Terminal, Джефф (на время генерации — бесплатное облако), распределитель ресурсов `tools/resource_governor` (прогрев весов, выгрузка Ollama, приоритет сборки). Ночные задачи с `bypassPermissions` НЕ включать (аудит §5, пункт 2).
Первым делом завтра: перезагрузочная проверка автозапуска; разобраться, что раз в 11 минут будит Ollama (аудит §4, п. 7); этап 2 ассистента с телефона после «да» владельца.

## Итог дня (18.10 вечер)

Единый документ состояния: `docs/owner/BOSSMAN_ONE_STATE_20261018_RU.md` (что запущено, что доказано, правила, что осталось). Читать его первым.
