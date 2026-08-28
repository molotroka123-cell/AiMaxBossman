# Lane F — OpenCode E2E (G10). Статус: **PARTIAL**

**fake-server E2E зелёный; настоящий host-E2E НЕ выполнялся, потому что бинаря
`opencode` в этой среде нет** (`which opencode` пусто). Это не «почти DONE» —
это ровно то, что проверено, и ровно то, что не проверено.

## Источник контракта эндпоинтов

Не выдуман. Взят из вендорной копии исходников OpenCode в скретчпаде сессии:
`.../scratchpad/opencode-src/packages/sdk/openapi.json` (info.version 1.0.0,
162 пути; идентичен `packages/docs/openapi.json`). Дополнительно сверено:

- Basic-auth и переменные `OPENCODE_SERVER_USERNAME` / `OPENCODE_SERVER_PASSWORD`
  — `packages/opencode/src/server/auth.ts`;
- `/api/health` → `{healthy:true}` — `packages/server/src/handlers/health.ts`
  и `packages/protocol/src/groups/health.ts`.

Использованные пути (v1-поверхность `opencode serve`), все с query `directory`:

| Операция | Метод и путь |
|---|---|
| health | `GET /api/health` (fallback: `/doc`, `/config`, `/project`) |
| создать сессию | `POST /session` → `Session` |
| сессия / список | `GET /session/{id}`, `GET /session` |
| статус | `GET /session/status` → `{sessionID: SessionStatus}` |
| задание (ждём) | `POST /session/{id}/message` → `{info, parts}` |
| задание (не ждём) | `POST /session/{id}/prompt_async` |
| сообщения | `GET /session/{id}/message` |
| abort / fork | `POST /session/{id}/abort` (bool), `POST /session/{id}/fork` |
| diff | `GET /session/{id}/diff` → `[SnapshotFileDiff]` |
| children / todo | `GET /session/{id}/children`, `GET /session/{id}/todo` |
| проекты | `GET /project` |

`/doc` в openapi.json отсутствует, но встречается в
`packages/opencode/src/server/routes/instance/httpapi/server.ts`, поэтому health
пробует лестницу путей, а не один — иначе смена версии сервера читалась бы как
«сервер мёртв».

## Изменённые файлы

- `command-center/bcc/v2/opencode_bridge.py` — расширен: `health()` (лестница
  проб, честный `unavailable`), `create_session`, `get_session`, `list_sessions`,
  `status`/`session_status`, `send_message`, `prompt_async`, `messages`,
  `abort`, `fork`, `diff`, `children`, `todo`, `projects`; всюду `directory`;
  утилиты `diff_summary`, `render_diff`, `assistant_text`.
- `command-center/bcc/features/tools_opencode.py` — **новый**: 5 канонических
  инструментов в `bcc.tools.REGISTRY` с `source="opencode"`, корни доступа,
  создание git worktree, запись маппинга, снимок диффа.
- `command-center/bcc/features/opencode.py` — операторский HTTP расширен:
  `POST /opencode/sessions` (старт в одобренном каталоге), `.../send`,
  `.../status`, `.../fork`, `.../children`, `.../todo`, `GET /opencode/roots`;
  health переведён на `bridge.health()`; diff отдаёт снимок, если сервер лежит.
  Старые `/opencode/health|attach|sessions|.../abort|.../diff` сохранены.
- `command-center/tests/fixtures/fake_opencode_server.py` — **новый** детермин.
  сервер (`http.server`, stdlib, без новых зависимостей).
- `command-center/tests/test_v21_opencode.py` — **новый**, 10 тестов + 1 skip.

Файлы из списка «не трогать» не изменялись.

## Инструменты (канонический реестр)

| Имя | Эффект | Право | Категория |
|---|---|---|---|
| `opencode.session.start` | **ask** (пол) | `terminal.run` | exec |
| `opencode.send` | **ask** (пол) | `terminal.run` | exec |
| `opencode.status` | auto | `terminal.read` | read |
| `opencode.diff` | auto | `terminal.read` | read |
| `opencode.abort` | auto | — | exec |

`start`/`send` держат ASK через `effect_hook` (хук может только ужесточать):
выданное агенту право `terminal.run` **не** превращает запуск автономного
кодинг-агента в AUTO. Снять ASK может только явное правило человека в
`agents.permissions.tool_rules` — так и сделано в E2E-тесте.

## Границы доступа

- Корни: `settings["opencode.roots"]` → `settings["terminal.roots"]` →
  `settings.data_dir`. Путь вне корней — **отказ** (данные модели / HTTP 403),
  не подтверждение.
- `worktree=true` создаёт `git worktree add -b bossman/run<N>` рядом с проектом;
  получившийся путь проверяется по корням ПОВТОРНО.
- Для `send`/`status`/`diff`/`abort` каталог берётся **из строки
  `opencode_sessions`**, а не из аргументов модели — подменить его вызовом нельзя.
- Не git-репозиторий + `worktree=true` → честная ошибка, без тихого отката на
  сам проект (иначе автономный агент писал бы в рабочее дерево человека).

## Тесты — что запускалось и с каким результатом

```
cd command-center && timeout 300 python -u -m pytest tests/test_v21_opencode.py -q
  → 10 passed, 1 skipped  (skip = host-smoke, бинаря нет)
cd command-center && timeout 500 python -u -m pytest -q
  → 211 passed, 1 skipped, 63 s   (регрессий нет; в дереве уже были правки Lane G/H)
```

Проверка на «пустой» тест: если фальшивый сервер перестаёт применять правку,
главный E2E падает на шаге сбора диффа — тест действительно чувствителен.

Покрыто: реестр и эффекты; сквозной прогон (красный тест → worktree → сессия →
задание → дифф → зелёный тест в worktree, исходный репозиторий не тронут);
ASK по умолчанию; отказ по неодобренному пути (инструмент и HTTP 403); abort
длинной `prompt_async`-сессии; fork + children; переживание рестарта; health
online и honest unavailable; отказ инструмента без выдумывания результата.

## Чего НЕТ (не выдаю за сделанное)

- **Настоящий `opencode serve` не запускался ни разу.** Форма ответов проверена
  по openapi.json, а не по живому серверу; расхождения версий возможны.
- Host-smoke `test_real_opencode_host_smoke` написан, но пропускается
  (`skipif shutil.which("opencode") is None`) и **никогда не выполнялся** —
  парсинг адреса из stdout `opencode serve` не проверен на реальном выводе.
- События (`GET /event` SSE) и `permission`-поток OpenCode не подключены:
  сейчас статус опрашивается, а не приходит push-ом.
- Дифф не привязан к `messageID` шага — берётся весь дифф сессии.

## Что нужно от Integration Lead

1. **Колонка `meta JSON` (или `last_diff JSON`) в `opencode_sessions`.** Сейчас
   снимок диффа кладётся в `run_events(kind="opencode.diff")`, потому что
   схему меняет только Lead. Следствие: у сессии, заведённой без `run_id`
   (операторский HTTP без задачи), дифф не сохраняется — только живой.
   Заодно пригодятся `aborted_at` и `agent`.
2. `opencode.roots` в UI настроек рядом с `terminal.roots` — сейчас ключ
   пишется только программно, HTTP-сеттера у него нет (читается `GET /opencode/roots`).
3. Хук Governor/Reviewer Gate: `opencode.diff` уже кладёт в `run_events`
   `{session_id, summary{files,additions,deletions,paths}, diff}` — это готовый
   вход для ревью кодовой задачи.
4. Регистрация фичи автоматическая (`FEATURE = Feature(name="tools_opencode")`),
   ничего подключать руками не нужно.
5. В скорборде фаза F — **PARTIAL**, формулировка: «fake-server E2E зелёный;
   host-E2E не выполнялся — бинаря `opencode` нет в среде».

## Допущения

- `opencode serve` слушает `OPENCODE_URL` (по умолчанию `127.0.0.1:4096`),
  Basic-auth из `OPENCODE_USER`/`OPENCODE_PASSWORD`.
- Сессия без записи в карте `/session/status` считается `idle`.
- Ветка worktree — `bossman/run<run_id>`; существующий каталог переиспользуется.
