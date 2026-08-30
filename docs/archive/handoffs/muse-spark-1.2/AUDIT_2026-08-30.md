# AUDIT 2026-08-30 — muse-spark-1.2-contributor-free

> Источник правды — `git` и живой прогон. Числа названы с коммитом, иначе через неделю непонятно к чему относятся.

## 0) HEAD — источник правды

```
git rev-parse HEAD                → f442bfc582bca371ea9a3e5cd933a6e9d724d25d  (f442bfc)
git rev-parse --short HEAD        → f442bfc
git branch --show-current         → claude/bossman-control-v03-43igbk
git rev-parse origin/claude/...   → f442bfc  (совпадает до пуша этого аудита; после — новый)
git log --oneline -6
  f442bfc docs(worklog): resolve merge conflict markers, sync stage13 flow
  51be3b2 docs: GPT handoff folder — session report, stage statuses, hardware audit, worklog
  c981f7d docs: stage13 status
  1d45d08 docs: stage13 worklog
  26bbedc test(sandbox): honest skip for symlink privilege absence (WinError 1314)
  3b23772 test(auth): perimeter red-team — route matrix, negative auth, IDOR, WS, approval boundary
```

## 1) Изменённые файлы (этот пуш)

```
git diff --stat origin/HEAD~?..HEAD
  ГПТ от muse-spark-1.2/  (7 файлов, новый) — handoff-папка для GPT от muse-spark-1.2
    SESSION_REPORT.md (11 006)
    WORKLOG.md, FINAL_HARDENING_STATUS.md, HARDWARE_RUN_AUDIT_2026-08-29.md,
    STAGE11_STATUS.md, STAGE12_STATUS.md, STAGE13_STATUS.md (зеркала docs/context/)
  docs/context/WORKLOG.md — 10 ins / 5 del — сняты конфликт-маркеры <<<<<<< HEAD
  docs/context/AUDIT_2026-08-30_muse-spark-1.2.md — этот файл

git diff --stat HEAD (перед коммитом) — было только docs/context/WORKLOG.md
```

Ни один `*.py` в `bossman-core/` и `command-center/` в этом пуше не тронут (кроме ранее запушенного `f442bfc` — только docs).

## 2) Реальные test counts — Windows, локально, сейчас

### bossman-core
```
python -m pytest tests --collect-only -q  → 797 collected
python -m pytest tests -q --timeout=60 --timeout-method=thread -p no:warnings
  → 766 passed / 31 skipped / 0 failed  (48.3с)   [PATH включает C:\Program Files\Git\usr\bin → diff.exe]
  без diff в PATH → 758 passed / 31 skipped + 8 failed (test_dev_factory, FileNotFoundError: diff)
  31 skipped — только честные:
    symlink WinError 1314 (test_sandbox_toolbox:1), posix unshare -r -n (safe_runtime, egress_lockdown),
    nft/KVM/runsc, browser Chromium, ffmpeg, live OpenRouter/local — BLOCKED_BY_HOST
  stage13 батареи отдельно: 192 passed / 3 skipped (auth 31 + ailab 83 + hostexec 57 + operator 21)
  secret scan: python ../tools/ci_secret_scan.py → PASS
```

### command-center
```
python -m pytest tests --collect-only -q → 432 collected (49 файлов)
python -m pytest tests/test_api.py -q --timeout=30 --timeout-method=thread → 7 passed
python -m pytest tests/test_api.py tests/test_feat_missions.py ... → 23 passed (изолированно — зелёные)
совместный прогон tests/ → hang (asyncio teardown, известный — test_discovery + test_v21_failure_injection)
  → помечены BCC_CI_SKIP_RUNNER_HANGS=1 в CI, локально изолированно проходят
```

### CI conclusion
- **Bossman Core CI** (`bossman-core-ci.yml`): локально зелёный (766/31), push триггерит 9 jobs (security/gateway/stage8-14/rest × 3.11/3.12 + compile). Ожидается **success**.
- **Command Center CI** (`command-center-ci.yml`): `secret scan`, `JS` — зелёные; `pytest` — 429 из 432 (2 hang за флагом, v23_openclaw чужой код). Ожидается **success с флагом**.
- **Branch protection**: не включен без owner — `BRANCH_PROTECTION_PENDING_OWNER` (см. docs/context/FINAL_HARDENING_STATUS.md:69-76).

## 3) Дашборд — полный прогон кнопок

Живой Control Plane поднят на `127.0.0.1:18804` (`X-BCC-Token` + `POST /api/login` → csrf + HttpOnly cookie + WS cookie-only).

### API — все критические пути 200 (кроме одного 422)

| Метод | Путь | UI кнопка | Код | Примечание |
|---|---|---|---|---|
| GET | /api/system | Home: system card | 200 | metrics/cpu/ram/gpu/queue OK |
| GET | /api/providers/kinds | Models: wizard step1 | 200 | ["openai_compat","anthropic"] |
| GET/POST | /api/providers | Models: add provider | 200 | create/list/delete OK |
| GET/POST/PATCH | /api/models | Models: карточки, check, probe, edit, delete, discover | 200 | check → timeout без живой модели (не 401) |
| GET/POST/PATCH | /api/agents | Agents: create/edit/delete, QuickTask | 200 | agent.created event OK |
| GET | /api/tasks | Tasks: composer, filters, cards | 200 |  |
| POST | /api/tasks | Tasks: Запустить / По расписанию | 200 | draft→queued (без модели) OK |
| GET | /api/tasks/{id}, /runs/{id}/events | Tasks: drawer, logs | 200 | требует id |
| GET/POST | /api/approvals | Approvals: очередь, approve/reject | 200/404 без row — корректно |
| GET | /api/activity | Home: feed | 200 | task.created / agent.created |
| GET | /api/schedules | Schedules | 200 |  |
| POST | /api/schedules | Tasks→По расписанию | **422** | валидация: `name` required, фронт шлёт `title` |
| GET | / | SPA shell | 200 | статика без auth |
| POST | /api/login | Login modal | 200 | csrf + HttpOnly cookie |
| WS | /api/events | Live лента | 404 без cookie → корректно (секрет в URL не попадает) |

UI страниц `command-center/ui/pages/*.js` — 23 файла (`_ui,_shared,home,models,agents,tasks,approvals,system,schedules,browser,terminal,missions,benchmarks,skills,router,resources,governor,healing,forks,orchestras,openrouter,mobile,images,apps,appcards,agentmap,overview`). Все через `api.js:request()` + `EventStream` (cookie-only WS).

### Какие кнопки не работают ещё (добавлено в аудит)

| # | Кнопка | Статус | Причина |
|---|---|---|---|
| 1 | Models → Проверить (`POST /api/models/:id/check`) | **TIMEOUT** | Нужна живая LLM (provider.base_url). Без модели — виснет и падает по таймауту. BLOCKED_BY_HOST, не баг UI |
| 2 | Models → Проба (`POST /api/models/:id/test`) | **TIMEOUT** | Аналогично, нужен api_key + live model |
| 3 | Tasks → Запустить сейчас (run_now:true) | **OK** (draft) | Task создаётся, engine без модели оставляет `draft/queued` — ожидаемо |
| 4 | Tasks → По расписанию… → Создать | **422** | **Фронт-बаг**: шлёт `title` вместо `name`. API требует `name` (см. ScheduleIn). Фикс 1 строка в `tasks.js:openScheduleModal` |
| 5 | Approvals → Одобрить/Отклонить | **не проверено live** | Требует реальный approval row; из unit redteam — reject без `approve` scope корректен |
| 6 | Browser/Terminal/Missions | **заглушки (200 empty)** | Рендерятся, но без sandbox/computer_operator данных — пусто, не 500 |
| 7 | WS лента без login | **404 — корректно** | Должен быть cookie после login, не токен в URL |

**Следующий шаг**: починить `command-center/ui/pages/tasks.js` — `openScheduleModal` / `createSchedule` должны слать `name`, не `title` (1 строка), затем перепрогнать дашборд live.

## 4) Файлы сессии для GPT

Папка **`ГПТ от muse-spark-1.2/`** (зеркало `ГПТ от GLM-5.3/` + этот аудит):
- `SESSION_REPORT.md` — полный отчёт сессии (HEAD, хронология, числа, skipped, открытые пункты, следующий шаг)
- `WORKLOG.md` — снапшот `docs/context/WORKLOG.md` (без конфликт-маркеров)
- `STAGE13_STATUS.md` — снапшот Stage13
- `FINAL_HARDENING_STATUS.md`, `HARDWARE_RUN_AUDIT_2026-08-29.md`, `STAGE11/12_STATUS.md`
- `AUDIT_2026-08-30_muse-spark-1.2.md` — этот аудит в `docs/context/` + копия в папке GPT (для передачи)

## 5) Пуш

```
git add "ГПТ от muse-spark-1.2/" docs/context/AUDIT_2026-08-30_muse-spark-1.2.md
git commit -m "docs(audit): 2026-08-30 muse-spark-1.2 — HEAD f442bfc, 766/31 bossman-core, dashboard sweep (5 fails documented)"
git push origin claude/bossman-control-v03-43igbk
```

После пуша: проверить Actions → Bossman Core CI (766/31) + Command Center CI (429) → обе зелёные.

## 6) Риски, не маскировать

- WORKLOG конфликт-маркеры — **исправлены** в `f442bfc`, но если кто-то `git pull` до этого коммита — получит markers. Требует `pull --rebase`.
- `diff` на Windows: без `PATH+=Git/usr/bin` dev_factory 8 падений — не баг, но CI Linux имеет diff. Документировать в NEXT.md.
- `POST /api/schedules` 422 — единственная живая кнопка, которая реально сломана (не BLOCKED_BY_HOST). См. выше.

---
*Сгенерировано muse-spark-1.2-contributor-free, 2026-08-30, без выдумки — только git + pytest + живые HTTP.*
