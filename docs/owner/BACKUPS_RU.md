# Ночные бэкапы баз Bossman — как пользоваться и как восстановить

Инструмент: `tools/nightly_backup.py`. Он копирует живые SQLite-базы через
онлайн-backup API SQLite (безопасно, пока Bossman работает, учитывает WAL),
проверяет каждую копию и чистит старые прогоны. Живые файлы он только читает.

Планировщик задач этим коммитом **не регистрируется** — запуск по расписанию
владелец включает отдельно.

## Что копируется

| Имя в бэкапе | Живой файл |
|---|---|
| `bcc.db` | `%LOCALAPPDATA%\Bossman\CommandCenter\bcc.db` |
| `exchange-assistant.db` | `%LOCALAPPDATA%\Bossman\CommandCenter\exchange-assistant\assistant.db` |
| `fx.db` | `%LOCALAPPDATA%\Bossman\CommandCenter\fx\fx.db` (журнал курсов) |
| `telegram-companion.db` | `%LOCALAPPDATA%\Bossman\telegram-companion\companion.sqlite3` |
| `telegram-companion-home.db` | `%LOCALAPPDATA%\Bossman\CommandCenter\telegram-companion\companion.sqlite3` (если пульт уже переехал) |

Отсутствующий файл попадает в `manifest.json` как `MISSING` и не роняет прогон.
Список — константа `SOURCES` в начале `tools/nightly_backup.py`.

Важно: у `assistant.db` основной файл 4 КБ, а данные лежат в `assistant.db-wal`.
Простое копирование `.db` дало бы пустую базу — поэтому только backup API.

**Ключи не копируются.** Зашифрованные строки пульта читаются только с
`telegram-companion\secret.key`, ключи провайдеров в `bcc.db` — только с ключом
хранилища Bossman. На этом же ПК восстановление работает с уже лежащими ключами.
Для переноса на другой диск/ПК ключи храните отдельно и никогда не пушьте.

## Запуск

```powershell
$py = "C:\Users\asd\AppData\Local\Programs\Python\Python312\python.exe"
& $py tools\nightly_backup.py --dry-run         # что будет сделано; ничего не пишет
& $py tools\nightly_backup.py                   # бэкап + проверка + чистка
& $py tools\nightly_backup.py --dest D:\Backups # другое место (позже — D:\Backups)
& $py tools\nightly_backup.py --restore-drill   # учебное восстановление во временную папку
```

Коды выхода: `0` — PASS, `2` — FAIL (что-то не прошло проверку), `3` — не стал
запускаться: идёт генерация (direct-gen job активен), список задач не читается
(например, неверный токен), или уже идёт другой бэкап.

Если бэкенд `:8801` не отвечает — генераций нет, бэкап идёт. Процесс сам себе
ставит фоновый приоритет диска/памяти Windows, чтобы не мешать генерациям.

## Что лежит в бэкапе

`%LOCALAPPDATA%\Bossman\Backups\ГГГГММДД-ЧЧММ\` — по одному `<имя>.db` на базу и
`manifest.json`: sha256, размер, число строк в каждой таблице, результат
`PRAGMA integrity_check`, путь источника, длительность, статус `PASS`/`FAIL`.

Проверка каждой копии: открыть только на чтение, `integrity_check == ok`, sha256
совпадает, список таблиц и число строк равны снимку источника, у базы обменника
на месте append-only триггеры (`events_no_update`, `events_no_delete`,
`deals_no_delete`). Любой сбой — `FAIL` и код 2.

Хранение: последние 14 прогонов. Самый свежий `PASS` не удаляется никогда, даже
если после него было 14 провалов. Удаляются только папки этого инструмента
(имя `ГГГГММДД-ЧЧММ` + его `manifest.json`); остальное в папке не трогается.

## Как восстановить базу (по-настоящему)

1. Сначала учебное восстановление — убедиться, что бэкап живой:
   ```powershell
   & $py tools\nightly_backup.py --restore-drill --keep-drill-dir
   ```
   В ответе `status: PASS` и `workdir` — там лежат восстановленные и проверенные копии.
2. Остановить то, что держит базу: бэкенд Bossman (`bcc.db`, `fx.db`,
   `assistant.db`) или пульт (`companion.sqlite3`). Не трогать чужие процессы.
3. Живой файл не удалять, а отложить вместе с `-wal` и `-shm`:
   ```powershell
   $live = "$env:LOCALAPPDATA\Bossman\CommandCenter\bcc.db"
   $stamp = Get-Date -Format yyyyMMdd-HHmm
   foreach ($s in "", "-wal", "-shm") { if (Test-Path "$live$s") { Rename-Item "$live$s" "$live$s.before-restore-$stamp" } }
   ```
4. Положить копию на место (из `workdir` учебного восстановления или прямо из
   папки бэкапа):
   ```powershell
   Copy-Item "$env:LOCALAPPDATA\Bossman\Backups\20261020-0300\bcc.db" $live
   ```
   Имена: `exchange-assistant.db` → `assistant.db`, `telegram-companion.db` →
   `companion.sqlite3`, `fx.db` → `fx.db`.
5. Запустить Bossman/пульт и проверить, что данные на месте. Отложенный файл
   (`*.before-restore-*`) удалять только после проверки.

Копии сохранены в режиме `journal_mode=DELETE` (один файл без `-wal`).
`bcc.db` и `assistant.db` приложение само переключит обратно в WAL при открытии;
`fx.db` и `companion.sqlite3` и так работают без WAL.
