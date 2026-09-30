# CONTINUE — 26.09.2026, сессия Claude (аудит + CI-фиксы + бесплатные провайдеры)

Одна рабочая ветка: **`candidate/freeze-20260926`**. Других веток не создавать.
База — `09f79275` (unified `integrate/bossman-1.7-unified-20260925`), сверху 3 коммита:

| SHA | Что |
|---|---|
| `2b687eef` | fix(ci): `.gitignore` снова валидный UTF-8 (байт 0x97 из `1a043ffb`); фейковый OpenHands SDK в тесте принимает `callbacks` (реальный SDK 1.44.1 принимает) |
| `c5246bfc` | feat: бесплатные облака NVIDIA NIM / Groq / Google AI Studio (backend + UI + CLI + тесты) |
| этот файл | handoff |

`fix/gitignore-utf8-20260926` — предок этой ветки, больше не нужен.

## Доказано (PASS)
- 215/216 → причина воспроизведена и снята: `test_local_state_never_lands_in_the_repository` падал на decode `.gitignore`.
- CI exact SHA `2b687eef`: **root-ci success, Bossman Core CI success** (на `09f79275` оба были failure), Solana/ASTRA/PostgreSQL success. Command Center CI на момент записи — in_progress (раннеры были забиты зависшими runs: `36269585681`, `36268550055`, `36268545472`, >2 ч in_progress — отменить с токеном).
- Локально: root suite 2705 passed; Windows-контракт core 74 passed; `test_free_providers.py` 6/6; соседние CLI/providers/openrouter 145 passed.
- Живые вызовы через `bossman exec` на :8800, все `cost_usd 0.0`, `REAL_MODEL`:
  - OpenRouter `nvidia/nemotron-3-ultra-550b-a55b:free` — task 17
  - NVIDIA NIM `moonshotai/kimi-k3` — task 21 (агент #11)
  - Groq `openai/gpt-oss-120b` — task 22 (агент #12)
  - Google AI Studio `models/gemini-3.8-flash` — task 23 (агент #13)

## Ключи (значения не писать никуда)
Все 4 ключа лежат в vault ЖИВОГО :8800 (маски: OpenRouter …4442, NIM …TDQD, Groq …6AwM, AI Studio …8_MA).
Владелец прислал их в чат открытым текстом → после тестов перевыпустить.
Старый OpenRouter …5aeb остался в владельческой БД `%LOCALAPPDATA%\Bossman\CommandCenter` (её не трогали).

## ВАЖНО про окружение
- Живой :8800 запущен из checkout `C:\Users\asd\Documents\Default Project\AiMaxBossman-integrated` (@09f79275) **без BCC_DATA_DIR** → данные/ключи/агенты в `...\AiMaxBossman-integrated\command-center\data`, НЕ в владельческой БД. Токен API — файл `token` там же (заголовок `X-BCC-Token`).
- Системный Python имеет editable-установку того же checkout → тесты на системном Python без PYTHONPATH гоняют ЧУЖИЕ байты. Всегда: `PYTHONPATH=<wt>\command-center;<wt>\bossman-core;<wt>`.
- Код `c5246bfc` в живом :8800 ещё НЕ запущен; ключи NIM/Groq/AI Studio внесены через штатный `/api/providers` + `/api/models` (цена 0/0, `caps.free_tier`), тем же способом, что делает новая ручка.

## Открытые дефекты (по приоритету)
1. **P1 CLI**: глобальные `--agent/--model` перед сабкомандой молча теряются (`bossman --agent 11 -p … exec` уходит агенту #1). Работает только `-p "…" exec --agent 11`. Владелец просит модель X — получает Y без предупреждения. Фикс: `default=argparse.SUPPRESS` у сабкомандных опций в `bcc/terminal_cli/cli.py` + тест.
2. **P1 самоаудит**: exec с длинным ревью-промптом у всех 4 агентов ушёл в `WAIT_APPROVAL` (tasks 24–27, остановлены). Разобраться, какой инструмент/политика требует approval для чистого текстового ревью; у Groq-агента run 27 ответил fallback-модель (nemotron) — вероятно лимит контекста/429 Groq, проверить события `bossman events 27`.
3. Windows-only падения (есть и на чистом `09f79275`, в CI Ubuntu не проявляются): root `owner_scenarios` OS-26/OS-27; core `test_live_notepad_actually_launches`, `test_teacher_iso_001…`, `test_cross_layer_positive_chain…`, `test_e2e_receipts_carry_fence…`, `test_posix_local_shell_unchanged`, `test_runner_records_observed_identity…`, `test_fleet_remote_auth::test_strict_bounded_json` (setup error).
4. Бесплатность Groq/AI Studio — условие аккаунта без биллинга. Если владелец привяжет карту — модели станут платными, а в реестре 0/0. Подтвердить у владельца.
5. Jeff: 7 файлов на `feat/jeff-ux-integration-test-20260926` байт-в-байт = `025834d6`; READY_TO_MERGE=NO; по решению владельца — после freeze (`READY_FOR_1_8=YES`). `install-jeff-shortcut.ps1` ожидает `.venv` — не чинили.

## Freeze — не закрыт
Осталось (из WORKBENCH_20260926): полный CC regression на этой ветке (запускался, результат не дождались → перезапустить), Command Center CI зелёный на финальном SHA, Windows ZIP + SHA-256 + clean install + краткий smoke, живой owner smoke (UX + CMD + Computer Use + Telegram approve/deny), YouTube/Instagram/BossBlocks runbooks. 1.8 — только после доказанного 1.5–1.7.

## Как продолжить (одна ветка)
```
cd C:\Users\asd\Bossman\wt-audit-0926   # worktree на candidate/freeze-20260926
git fetch origin && git status          # чисто, HEAD = origin/candidate/freeze-20260926
```
Первым: CI статус exact SHA → P1 CLI `--agent` → полный CC regression → ZIP/smoke.
Evidence: `C:\Users\asd\Bossman\evidence\audit-0926\` (логи прогонов, self-audit JSON; `private\` — только зашифрованный бэкап, ключей нет).
