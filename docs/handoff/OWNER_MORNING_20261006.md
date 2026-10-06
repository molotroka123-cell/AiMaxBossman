# Утро 06.10.2026 — Bossman: что проверить (коротко)

Ветка `goal/bossman-self-improvement-tree-20261005`, SHA на момент записи: **`66c2ade506d8efcf1803f81d76aebacb99baca0c`**. Полный отчёт ночи: `docs/audits/2026-10-06-night-build.md`.
Один и тот же Bossman: пульт, CLI и дашборд работают через один backend/данные/ключи/одобрения. Лестница North Star: достигнут только
`SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT`; `SELF_REPAIR_SINGLE_CYCLE_PASS` ночью **не доказан** (см. п. 5).

## 0. Честно: Windows-пакет ночью НЕ собран
- Запуск `windows-bundle` из облачной сессии отклонён GitHub: `403 Resource not accessible by integration` (у токена интеграции нет права
  `actions: write`). Автозапуск по push тоже не сработает: ветка `goal/**` не входит в список веток этого workflow (там `claude/**`,
  `night/**`, `release/**`, `integrate/…`), его меняют только осознанно (реестр `tools/owner_facing_branches.json` + тесты охвата).
- Собрать Windows-пакет на Linux нельзя (сборщик это отвергает). Поэтому **ссылки на артефакт и sha256 пока нет** — не выдумываю.
- **Путь A (1 клик):** GitHub → Actions → «One-download Windows application» → **Run workflow** → Branch: `goal/bossman-self-improvement-tree-20261005`.
  Артефакт `bossman-windows-<sha>` содержит `BOSSMAN-Windows-x64-<sha12>.zip`, sha256 и `bundle-acceptance.json` (нужна строка
  `BOSSMAN_BUNDLE_ACCEPTANCE=PASS`; если FAIL — пакет не ставить).
- **Путь B (локально, как 05.10):**
  ```powershell
  git clone -c core.autocrlf=false <ваш worktree> tree-build\<sha>\src ; cd tree-build\<sha>\src ; git checkout <40-символьный SHA>
  python tools\build_windows_bundle.py --out ..\dist --zip
  python tools\verify_windows_bundle.py --archive ..\dist\BOSSMAN-Windows-x64-<sha12>.zip --expected-sha <40> --out ..\dist\bundle-acceptance.json   # → BOSSMAN_BUNDLE_ACCEPTANCE=PASS
  Get-FileHash ..\dist\BOSSMAN-Windows-x64-<sha12>.zip -Algorithm SHA256
  ```

## 1. Установка
```powershell
pwsh tools\owner_one_bossman.ps1 -Action Switch -Sha <40> -Archive <zip> -ArchiveSha256 <sha256> -Evidence <dir>
```
Проверка: `/health/live` → `build_sha` = ваш SHA; один backend, один `bcc.pit.cli start`, один `bcc.telegram_companion`.

## 2. Откат
`pwsh -NoProfile -File "<Evidence>\rollback.ps1"` (добавьте `-StartOld`, чтобы поднять старый Jeff). Предыдущий пакет `b96e2a7c`:
`Bossman\bugtest-20261001\tree-1005\switch-b96e2a7c\rollback.ps1`.

## 3. Что изменилось ночью (проверено тестами, не «на слово»)
CI root-ci починен (3 причины); влиты PR #89 и freeze-closure (docs); перенесены фиксы звонков (STOP побеждает дозвон); находки аудита
#5 (`curl | sh` исполняет ровно одобренные байты через stdin) и #3 (чужой scratch не попадает в индекс кода); веб-поиск Jeff
переходит на keyless DuckDuckGo, если SearXNG лежит. Подробности и номера тестов — в журнале.

## 4. Тест на 20 минут
**а) Пульт (Telegram), компьютер.** `/task Открой Блокнот, напиши «Bossman управляет компом сам» и проверь` → придёт предложение →
`/confirm` (одного предложения код не нужен) → дальше каждое действие на компьютере снова просит подтверждения (так задумано; запуск
только notepad/calculator, платежи и учётные данные запрещены). `STOP` останавливает всё.

**б) Дерево.** Дашборд → «Дерево развития» → зона → «Bossman, работай здесь» → отчёты «начал/результат» приходят в пульт.

**в) Jeff.** Текст (всегда первым), голос — только по просьбе (`/voice on`, сессия 30 минут). Поиск: спросите про свежую новость, затем
`python -m bcc.pit.cli doctor` → строка `web`: `active=SEARXNG | KEYLESS_FALLBACK | NONE` (живой DuckDuckGo из облака проверить было нельзя —
это покажет ваш ПК).

## 5. Первое самоулучшение — одна команда (ночью не запускалось: в облаке нет ключей)
Дефект уже воспроизведён и **намеренно оставлен**: `bcc/pit/discovery.py`, кандидат с NaN первым в списке блокирует выбор хорошего вопроса
(`max()`). Запуск через путь дерева (зона → coding task), только бесплатный исполнитель:
```powershell
$env:PYTHONPATH="$PWD\command-center;$PWD\bossman-core;$PWD" ; python tools\tree_self_improve.py            # nemotron-ultra-free
python tools\tree_self_improve.py --worker openrouter-free                                                  # если Ultra недоступен
```
Успех = вывод `TREE_SELF_IMPROVE=VERIFIED_CANDIDATE` (изменён `discovery.py`, добавлен тест, независимая проверка Bossman пройдена) и лист
«проверено». Любой другой вердикт (`CHECK_FAILED`, `NO_DEFECT_FOUND`, `FAILED`, `TIMEOUT`) — это **не** доказательство, так и пишем.
В проект кандидат попадает только после вашего «Применить».

## 6. Что требует ваших рук
- `bossman call setup` — звонки (файл `telegram-calls\credentials.enc` повреждён повышенным процессом 05.10, не перезаписывал).
- Запись голоса 1–3 минуты (кандидаты: Qwen3-TTS-0.6B, CosyVoice 3; ASR GigaAM-v3).
- Аудит #9: секрет `BOSSMAN_OPENROUTER_API_KEY` в `windows-bundle.yml` читают job'ы без защищённого окружения. GitHub → Settings →
  Environments → создать окружение (например `owner-live`) с Required reviewers, перенести секрет туда; потом в workflow добавить
  `environment: owner-live` job'ам, которые его используют (правку workflow сделаю отдельным PR по вашему слову).
- Решения по веткам: `handoff/continuation-20260929` (архив docs), `feat/bossman-autonomy-funding` (заявка, NOT_SUBMITTED), удаление
  `scratch/root-ci-debug-19`; `wip/*` — только выписано полезное (журнал, чекпоинт 2).
- Merge PR в `main` нажимаете вы. Открытый PR #91 (draft) смотрит в `release/bossman-owner`, а не в `main`.
