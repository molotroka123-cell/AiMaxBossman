# План замены установленной сборки 6de18f8d → кандидат (12.10.2026)

Не выполняется без «да» владельца в пульте. Записан заранее, чтобы откат был одной командой.

## Что установлено сейчас

- Backend :8801: `C:\Users\asd\Bossman\app\BOSSMAN-Windows-x64-6de18f8d9a08` (запуск задачей `BossmanOne-1-Backend`
  каждые 10 минут с `--ensure`; `--data-dir C:\Users\asd\AppData\Local\Bossman\CommandCenter`).
- Jeff и companion — задачи `BossmanOne-2-Jeff`, `BossmanOne-3-Companion` с тем же `--home`.
- Данные (БД, vault, журналы) живут в `%LOCALAPPDATA%\Bossman\CommandCenter` — они не внутри папки сборки.

## Кандидат

- Архив `C:\Users\asd\Bossman\dist-1757ff86\BOSSMAN-Windows-x64-1757ff86e765.zip`, SHA256
  `a10699eade30aab04e6ca46fbb2f60aa089bdd0644d2fc5a1f6051e127fe8881`, verifier PASS; распакован в
  `C:\Users\asd\Bossman\nb\1757ff86e765` (MANIFEST source_sha = 1757ff86e765…). Окончательный кандидат может иметь
  другой SHA (после push исправлений аудита) — тогда шаги те же с новым корнем.

## Шаги (после «да»)

1. Бэкап (без остановки):
   `python C:\Users\asd\Bossman\one-bossman\owner_one_bossman.py backup --src C:\Users\asd\Bossman\app\BOSSMAN-Windows-x64-6de18f8d9a08 --dest C:\Users\asd\Bossman\backup\app-6de18f8d-20261012`
   `robocopy %LOCALAPPDATA%\Bossman\CommandCenter C:\Users\asd\Bossman\backup\data-20261012 /E /COPY:DAT /R:1 /W:1`
   `schtasks /Query /TN BossmanOne-1-Backend /XML > C:\Users\asd\Bossman\backup\tasks-6de18f8d\BossmanOne-1-Backend.xml` (и для -2-Jeff, -3-Companion)
2. Новый корень: `robocopy C:\Users\asd\Bossman\nb\1757ff86e765 C:\Users\asd\Bossman\app\BOSSMAN-Windows-x64-1757ff86e765 /E /R:1 /W:1`
3. Переключить три задачи `BossmanOne-*` на новый `--home` (XML-экспорт → замена пути → `schtasks /Create /XML … /F`),
   остановить старый backend (`owner_one_bossman stop --kinds backend,jeff,companion`), запустить `schtasks /Run /TN BossmanOne-1-Backend`.
4. Smoke на установленной сборке: health с токеном; локальный Ollama-чат без облачного fallback; СТОП и /resume;
   пульт /status, /remote_status; UI sweep v3; evening acceptance. Фиксировать модель и фактические ошибки.

## Откат одной командой

`schtasks /Create /XML C:\Users\asd\Bossman\backup\tasks-6de18f8d\BossmanOne-1-Backend.xml /TN BossmanOne-1-Backend /F && schtasks /Run /TN BossmanOne-1-Backend`
(папка 6de18f8d не удаляется, данные не трогаются — откат = вернуть `--home` и перезапустить; то же для задач -2 и -3).
