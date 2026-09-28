# CONTINUATION: откуда продолжать следующему чату

Обновлено: 2026-09-28, lead-сессия Claude (RC19). Этот файл нужно читать первым, если прошлый чат закончился по лимиту.

## 0. Главное за 30 секунд
- На машине владельца работает **один Bossman** на реальных данных: сборка `84f5e0ac`, backend :8801, автозапуск при входе в Windows. Подробности — `RC19_SESSION_AUDIT_20260928.md` §2.
- PR #84 (`claude/bossman-freeze-closure-ohvmon`): последний CI-сертифицированный код — `84f5e0ac`, **11/11**. Freeze блокирует только Intelligence Preservation. Измерение проведено честно, найдены реальные регрессии режима FULL.
- 9 веток агентов `rc19/*` лежат в origin как снимки на чекпоинте и **ещё не влиты**.
- Идеи и решения: `DECISIONS_BACKLOG.md`. Видение Jeff: `JEFF_2_0_VISION.md`.

## 0.1 Параллельная сессия (аудит и UX) — читать тоже
Другая сессия Claude ночью 28.09 запушила:
- `claude/rc19-audit-integration` @ `a53623ae`: исправлены 21 тест, добавлен import-guard, полный отчёт `docs/owner/AUDIT_RC19_REPORT_20260928.md`;
- `feat/jeff-ux-integration-test-20260926` @ `3a546bed`: ночные инструменты и её continuation-файл `docs/owner/CONTINUATION_20260929_UX_AND_AUDIT.md` (чеклист закрытия и приёмки UX v0.1; хвосты аудита: cross_layer, перенос в PR #84, числа CC, флейк).

Утренний план владельца по этому файлу: UX-чеклист → прогон → вживление хука → приёмка. Затем решение по cross_layer и перенос аудита в PR #84. Интегрировать вместе с ветками `rc19/*` из §4, по одной, с тестами.

Jeff-бот (участнический) ночью использовался той сессией для личной рассылки владельцу. «Пульт» — по-прежнему канал отчётов и управления.

## 1. Правила (нарушать нельзя)
- Force-push, `reset --hard` и merge в `main` запрещены. Перед каждым пушем — `git fetch`: параллельно пушат другие сессии Claude и Codex/Aster.
- Секреты, Telegram ID, тексты диалогов и голосовые записи не попадают в git, логи и отчёты. Заблокированный для Jeff ID хранится в приватном файле `%LOCALAPPDATA%\Bossman\private\` вне git.
- Отчёты владельцу отправляются в «Пульт» (companion-бот), по-русски. Jeff-бот — только для участников.
- **Данные, собранные Jeff, не удаляются.** Реальную папку данных менять только после проверенного бэкапа.
- Без платежей, публикаций и сообщений людям. STOP, одобрения и лимиты не ослабляются. Smart App Control не выключать.
- Каждый фикс: тест красный → зелёный, окончания строк LF.
- Успех засчитывается только по реальному прогону от лица владельца (клики по ярлыкам), зелёного юнит-теста недостаточно.

## 2. Ловушки Windows (стоили часов)
- В git-bash `PYTHONPATH` задаётся только в Windows-форме (`cygpath -w`). Путь `/c/...` молча подтягивает старый editable checkout.
- `MSYS_NO_PATHCONV=1` для аргументов вида `/api/...`. `PYTHONIOENCODING=utf-8` для кириллицы.
- Python `write_text` на Windows пишет CRLF, поэтому пишите байты.
- Процессы, запущенные из git-bash, игнорируют Ctrl+C. Лаунчер `owner_one_bossman.py` это учитывает, остановка идёт через Ctrl+Break.
- Сессия Claude запущена с правами администратора: файлы, которые она переписывает, становятся доступны только админам. Сбрасывать `icacls /reset`; инструмент A пишет «in place».
- Ollama: для скорости нужно `think:false`, иначе рассуждения утекают в ответ.
- sd.cpp + Qwen-Image-2.1: **не передавать `--llm_vision` при text-to-image**, иначе генерация уходит на CPU и висит.

## 3. Где что лежит (локально)
- Код и worktrees: `C:\Users\asd\Bossman\wt19-*` (lead — `wt19-lead`, ветка `rc19/integration`).
- Доказательства: `C:\Users\asd\Bossman\evidence\rc19\<агент>\`, у каждого агента есть `CHECKPOINT.md`.
- Инструменты lead:
  - `evidence\rc19\lead\tg_owner_report.py` — отправка в «Пульт»: текст через stdin `-` или `--photo`;
  - `tg_session_bridge.py` — «Пульт» → сессия; пока работает, держит токен бота;
  - `rc_api.py`, `desk.ps1`, `ci_watch.py`.
- Корпус IP: `C:\Users\asd\Bossman\ip-corpus-rc19\`. Бэкапы: `C:\Users\asd\Bossman\backups\`.
- Игра: `C:\Users\asd\Bossman\games\bossblocks` (отдельный локальный git).

## 4. Очередь работ (по порядку)
1. Собрать результаты фоновых процессов, если они завершились: перемер IP (агент C, см. `evidence\rc19\m-ip\CHECKPOINT.md`) и готовность 24/7 (агент D, `evidence\rc19\n-self\CHECKPOINT.md`).
2. Влить ветки в один кандидат поверх головы PR #84 через `merge --no-ff`, по одной, после каждой прогонять тесты:
   1. `rc19/post-rc`
   2. `rc19/k-one`
   3. `rc19/o-models`
   4. `rc19/m-ip`
   5. `rc19/n-self`
   6. `rc19/p-green` (включает collector, 3D, Agentic Rave, Motion Studio)
   7. `rc19/h-3d` (новее, чем в p-green)
   8. `rc19/q-parser`
   9. `rc19/r-jeff-next`
   10. `rc19/s-motion`

   Ветки в статусе `wip:` вливать только после доведения до зелёного.
3. Запушить в PR #84 (fast-forward), дождаться CI на точном SHA (`tools/exact_sha_certify.py`). Windows owner run запускать через workflow_dispatch.
4. Собрать ZIP из этого SHA и переключить машину: `tools/owner_one_bossman.ps1 -Action Switch -Sha <sha> -Archive <zip> -ArchiveSha256 <hash> -AgentPlan evidence\rc19\k\agent-plan.json`. Перед этим сделать свежий бэкап.
5. Остановить мост lead-сессии и выполнить `Start-ScheduledTask BossmanOne-3-Companion`, чтобы «Пульт» заработал из новой сборки.
6. Прогон кликами по трём ярлыкам («Bossman», «Bossman CMD», «Bossman Jeff») и итоговый отчёт с маркерами в «Пульт».
7. Действие владельца: выйти из Windows и войти снова, чтобы проверить автозапуск.
