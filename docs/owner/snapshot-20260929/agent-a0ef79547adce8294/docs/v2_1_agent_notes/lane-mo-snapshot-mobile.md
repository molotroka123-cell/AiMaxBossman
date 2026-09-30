# Lane M/O — Snapshot/Rollback (O) + Mobile-операторский UX (M)

Ветка `claude/bossman-control-v03-43igbk`. Ничего не коммичу — коммит за Лидом.

## Изменённые файлы

| Файл | Что |
|---|---|
| `command-center/bcc/features/snapshot.py` | **новый**, 576 строк — фича Snapshot/Rollback |
| `command-center/tests/test_v21_snapshot.py` | **новый**, 16 тестов |
| `command-center/ui/pages/mobile.js` | расширен (199 → 516 строк) |
| `command-center/ui/mobile.css` | расширен (152 → 252 строки) |
| `docs/V2_PROOFS/shots/mobile-v21-{320,375,390,430}.png` | скриншоты живого сервера |

`bcc/db.py` не трогал (таблица `snapshots` уже была). Чужих файлов не менял.

## O — Snapshot/Rollback

Endpoints (монтируются под `/api`, токен-auth даётся оболочкой):
`POST /api/snapshots`, `GET /api/snapshots`, `GET /api/snapshots/{id}`,
`GET /api/snapshots/{id}/restore-preview`, `POST /api/snapshots/{id}/restore`.

Артефакт = каталог `<data_dir>/snapshots/<ts>-<kind>/` из **двух** файлов
(права 0600): `db.sqlite` (консистентная копия через `VACUUM INTO`, без
остановки воркеров) и `manifest.json`. Живой прогон: **284 КБ всего**.

Манифест содержит: sha256+размер каждого файла, счётчики таблиц, несекретные
поля `Settings`, список **ключей** `settings_kv` (значения — только внутри
зашифрованной БД), git HEAD/ветку/число грязных файлов/`worktree list`
(на живом прогоне 16 worktree), активные миссии/задачи/раны, версии скиллов,
модели, провайдеров.

**Секреты.** Ключи провайдеров попадают в манифест ТОЛЬКО как
`key_fingerprint = sha256(key)[:16]` — маску «…last4» намеренно не пишу,
хотя в остальном API она принята. В артефакт не копируются `secret.key`
(Fernet) и `token`: без ключа копия БД для вора бесполезна. Тест
`test_snapshot_contains_no_plaintext_secret` сканирует ВСЕ байты артефакта на
канареечный ключ провайдера, на канареечный «wallet seed» и на содержимое
`secret.key`. Проверено и вживую: `grep sk-canary-1234` по артефакту — пусто.

**Ничего тяжёлого.** Копируется только файл БД; предел
`MAX_ARTIFACT_BYTES = 64 МиБ` — при превышении честный 507, а не тихая копия
гигабайтов. Веса моделей, профиль браузера, логи перечислены в
`manifest.excluded` с причинами.

**Откат никогда не молчит.** `POST …/restore` без `approval_id` заводит
approval `kind="snapshot_restore"` и отвечает **202** с его id. Дальше:
pending → 403, rejected → 403, approval чужого рода (например `tool`) → 403,
несовпадение sha256 артефакта → 409. Только `approved` выполняет откат.
Перед подменой снимается `pre-restore-<ts>/db.sqlite` (откат обратим), затем
`engine.dispose()` + удаление `-wal`/`-shm` + подмена файла + `create_all()`.
Реестр `snapshots` переносится в восстановленную БД — иначе после первого же
отката возвращаться было бы некуда. Anti-replay: строка approval после отката
исчезает (→ 403), плюс аудит-событие `snapshot.restored` в новой БД.
Git не откатывается — это явно сказано в `restore-preview.git.note`.
Не-SQLite → честный 501, а не сломанный откат.

## M — мобильный «Пульт»

Не редизайн: те же `.cmd-*` карточки. Что добавлено/починено:

* **Active Missions** — было «одна верхняя миссия», стало список активных с
  реальными `POST /api/missions/{id}/pause|resume|stop` (stop — через confirm).
* **Needs You** — approvals теперь различают `kind="tool"` из tool-loop:
  в заголовке имя инструмента (парсится из preview движка), бейдж `tool`,
  ссылка на задачу, раскрытие полного preview с аргументами. Approve/Reject
  как были.
* **Runtime** (новая карточка) — Terminal (Kill), Browser (Take Over /
  Вернуть агенту / Stop), OpenCode (Abort). Всё на существующих endpoint'ах.
* **Model switch** — в шторке агента `select` моделей + `PATCH /api/agents/{id}`
  с честной подписью «применится со следующего запуска».
* **Health — исправлен баг**: код читал `sys.current.cpu_pct`, а `/api/system`
  отдаёт `{metrics, history, queue, health}` → CPU/RAM всегда были «—».
  Теперь CPU/RAM/Диск/«свободно под модели» (`/api/resources`), чипы здоровья
  компонентов и сводка очереди.
* **Resource warning** — верхняя карточка: `available_mb < reserve_floor_mb`,
  RAM ≥90%, CPU ≥95%, компонент в `status: error`.

**Честные пустые состояния, а не фейковые кнопки.** OpenCode-бинаря в среде
нет: `/api/opencode/health` → `{"status":"unavailable","detail":"ConnectError"}`,
и пульт пишет «OpenCode недоступен: ConnectError», не рисуя мёртвых кнопок.
Пустые списки терминалов/браузера — обычная пустота, отказ endpoint'а —
пустота с причиной из ответа сервера.

## Тесты и замеры

* `timeout 300 pytest tests/test_v21_snapshot.py -q` → **16 passed**.
* Полный прогон `timeout 600 pytest -q` → **249 passed, 2 failed, 1 skipped**
  (258 c). Оба падения — не мои файлы:
  `test_v21_e2e_mission.py::test_autonomous_mission_with_ten_plus_tool_calls`
  и `test_v21_failure_injection.py::test_mcp_server_crash_is_bounded_and_visible`
  (`assert 'healthy' != 'healthy'`). Ими владеют Lead / Lane 2.
* Живой сервер `BCC_PORT=8830` + Playwright `/opt/pw-browsers/chromium`,
  вход через реальную форму (`#login-token` / `#login-submit`, cookie+CSRF):

| Ширина | scrollWidth | Горизонтальное переполнение | Элементов, вылезающих за вьюпорт | Тап-таргетов <44px | Ошибок в консоли |
|---|---|---|---|---|---|
| 320 | 320 | **0 px** | 0 | 0 из 21 | 0 |
| 375 | 375 | **0 px** | 0 | 0 из 21 | 0 |
| 390 | 390 | **0 px** | 0 | 0 из 21 | 0 |
| 430 | 430 | **0 px** | 0 | 0 из 21 | 0 |

* Кнопки проверены кликами в браузере, а не «на глаз»: Approve (3→2
  approval'а), Resume миссии (`в очереди`/`на паузе` → `выполняется`),
  Take Over (появился бейдж `take over`), Kill (PID 23286 мёртв, секция стала
  «Активных терминалов нет»).
* `POST /api/snapshots` на живом сервере: 284 КБ, git HEAD `9140e2d`,
  16 worktree, `key_fingerprint: sha256:7b977e25…`, канареечного ключа нет.

## Допущения

1. «Config» = несекретные поля `Settings` + ключи `settings_kv`; сами значения
   зашифрованы и едут внутри копии БД. Отдельного файла конфига в репо нет.
2. «Model switch» на мобиле = смена модели агента (`PATCH /api/agents/{id}`) —
   это единственный существующий бэкенд. Смены модели идущего шага в API нет.
3. Репозиторий для git-блока — `bcc.config.ROOT.parent`; отсутствие git даёт
   `{"available": false, "note": …}`, а не ошибку.
4. Откат подменяет файл SQLite «под живым процессом». Для одного процесса это
   безопасно (dispose + снос WAL); при нескольких процессах на одной БД нужен
   стоп-режим — это НЕ реализовано.

## Крючки для Лида

* Фича регистрируется сама (`FEATURE = Feature(name="snapshot", router=router)`);
  ничего подключать не надо.
* `POST /api/snapshots {"kind": "pre_mission"}` — готовая точка для
  автоснапшота перед стартом миссии (`bcc/features/missions.py::start_mission`).
  Сам туда не лез: файл не мой.
* В `ui/pages/index.js` (не мой файл) страницы Snapshot нет — управление
  снапшотами пока только по API. Если нужна — это отдельная `ui/pages/snapshot.js`.
* Кнопки «снять снапшот» на мобильном пульте намеренно нет: ТЗ фазы M
  перечисляет конкретный набор контролов, расширять его сам не стал.
* Фаза O в `docs/V2_1_RUNTIME_CONTEXT.md` §15 может переезжать в DONE,
  фаза M — DONE с оговоркой про OpenCode (бинаря в среде нет; проверена
  честная деградация, а не успешный Abort).
