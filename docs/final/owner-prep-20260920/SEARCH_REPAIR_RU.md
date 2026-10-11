# 20.09.2026 — поиск решений и проверенные исправления PREP-08

Исходная вершина: `c4890c751928821be74962aafd0e8059508e067f`, единственная ветка `claude/bossman-final-convergence-hu2702`. Это ограниченный ремонт измерителя и подготовка installed-диагностики, не выпуск V8 Total и не доказательство скорости Windows.

## Что изучено у других

Использованы первичные источники, без копирования чужого движка и без добавления зависимостей:

- psutil: https://psutil.readthedocs.io/stable/ и upstream-пример https://github.com/giampaolo/psutil/blob/master/scripts/pstree.py — дерево процессов, время создания процесса, гонки исчезновения, накопленные CPU-счётчики. Применено различение PID/create_time, отказ при неполных наблюдениях и смене состава процессов в CPU-интервале.
- CPython: https://docs.python.org/3/using/cmdline.html — `-I` отделяет установленный интерпретатор от текущего каталога, пользовательских пакетов и PYTHON-переменных. Применены проверка exact runtime/python.exe и запрет source BCC.
- Playwright: https://playwright.dev/python/docs/api/class-browsertype и https://playwright.dev/python/docs/api/class-page#page-wait-for-function — конкретный executable и ожидание состояния. Применены shipped Chromium, различение headed/headless и свежего содержимого страницы.
- pytest: https://docs.pytest.org/en/stable/explanation/goodpractices.html — тест установленного пакета не должен незаметно тестировать исходники. Установленное приложение, внешний harness и синтетические контролы разделены.

## Воспроизведённые дефекты существующего измерителя

| Дефект | Исправление и различающий контроль |
|---|---|
| Таймер запускался после dispatch навигации | Старт до evaluate. Синтетические 200 мс dispatch + 20 мс ожидания теперь дают 220, а не 20 мс; это контроль алгоритма, не измерение Bossman |
| Галерея могла засчитать старую карточку | Ожидание удаления stale-маркеров выполняется и при переданном selector |
| PNG был только сигнатурой с мусором | Детерминированный настоящий PNG 64×64 RGB, CRC/IDAT проверены; дополнительно декодирован Pillow |
| Отрицательные/нечисловые метрики | NaN, ±Inf, отрицательные числа и bool не становятся PASS/числом; JSON не содержит NaN |
| Пустой/неизвестный итог становился PASS | Пустой список и неизвестный статус дают INSUFFICIENT_EVIDENCE; явный FAIL сохраняет приоритет |
| Невалидная длительность доходила до runtime | До импорта проверяются конечность и диапазон 0–7200 секунд |

`responsiveness_probe.py` также сохраняет исходные navigation samples и принимает явные параметры installed-полосы. Старый вызов installed без привязки остаётся fail-closed; source reference не переименован в установленный продукт.

## Что реализовано в новой installed-полосе

`tools/installed_responsiveness_probe.py` проверяет Windows, точный Python архива с `-I`, SHA-256 application ZIP, полный состав и байты распакованной папки. Actions-wrapper, лишний файл, подмена, пропажа, case-alias и выход пути за каталог отвергаются до импорта BCC. ZIP не распаковывается этим скриптом и не модифицируется.

После проверки используется существующий `bcc.owner_acceptance.launch`, поставленные UI registry/Chromium и существующая очистка только принадлежащего пробе дерева. Данные и профиль создаются отдельно; конфигурация владельца, provider keys и proxy-настройки не наследуются в рабочую среду. Браузерные HTTP-запросы ограничены новым loopback-сервером. Модели, внешние provider endpoints, торговля и системная безопасность не являются частью сценария.

Пишутся raw navigation/process samples и атомарные checkpoints. В CPU не вычитаются исчезнувшие процессы: неполный интервал или PID reuse не дают результат. Короткий прогон не закрывает часовой soak; минимум 3600 секунд нагрузки, максимум 7200. Ошибка cleanup оставляет FAIL/completed=false и локальную диагностику. Существующий файл результата не перезаписывается при старте.

Ограничения не скрыты: observer/Playwright overhead включён; краткоживущие процессы между samples не покрыты; внешние модельные серверы не измерены. Native Start-Bossman.cmd, cold/warm cache и owner-GUI этим backend/browser harness не измеряются. `release_ready=false`; Git harness SHA не угадывается, вместо него записываются реальные SHA-256 внешних скриптов и бюджета. Это не freeze evidence.

## Фактические проверки этой сессии

Среда: Linux / Python 3.13.5, частичная рабочая копия, не полный checkout. Полученные через GitHub исходник и неизменённые budget/два прежних test-файла сверены по Git blob SHA.

На исходном измерителе 16 новых базовых regression controls: **15 failed / 1 passed** (два новых CLI-контрола отдельно deselected). После исправления общий набор из прежних 21 и новых 45 контролов: **66 passed / 0 failed / 0 skipped**, 1.76 s. Команда:

```text
python -m pytest -q tests/test_responsiveness_budget.py tests/test_responsiveness_installed_guard.py tests/test_responsiveness_measurement_regression.py tests/test_installed_responsiveness_probe.py
python -m py_compile tools/responsiveness_probe.py tools/installed_responsiveness_probe.py
```

Реально выполнен отдельный Linux subprocess-контрол: дочерний Python с выделением 16 MiB включён в snapshot, затем остановлен собственным Popen. Четыре orchestration-контрола используют СИНТЕТИЧЕСКИЙ runtime/browser/clock; виртуальные 3600 секунд в тесте НЕ являются часом Windows-soak. Маленькие ZIP-фикстуры содержат текст вместо python.exe и никогда не запускаются как приложение.

Проверенные и публикуемые code blobs:

| Файл | Git blob |
|---|---|
| tools/responsiveness_probe.py | b0224c3aea207c00dfc2cccd94a673291c09aa39 |
| tools/installed_responsiveness_probe.py | 8ec3d41a4323f4737945b297cbe2d0fb4480599c |
| tests/test_responsiveness_measurement_regression.py | 9f98709e86d361dfce2c11491068389ddcbfc323 |
| tests/test_installed_responsiveness_probe.py | 94b5d71c67994e366937ab8b8fa4388c993539bd |

## CI: старые очереди уже завершились

Native GitHub read в этой сессии: Command Center run `35524046007` для PREP-03 — все 5 jobs success (Python 3.11/3.12/3.14, Windows paths, security/JS). Root run `35524176551` после README fix — все 3 jobs success. Их нельзя продолжать описывать как queued или вновь чинить без регрессии. Это результаты прежних SHA, не автоматический PASS нового коммита.

## Следующий безопасный запуск интегратора

Новая полоса пока НЕ включена в SUPPORT_SCRIPTS/конечный ZIP. Три файла `tools/responsiveness_probe.py`, `tools/installed_responsiveness_probe.py`, `tools/responsiveness_budget.json` должны находиться во внешней проверенной папке инструментов, не внутри принятой папки приложения. Эта техническая диагностика не заменяет `OPENCODE_OWNER_RUN_RU.md` и не добавляет требование Git к обычному запуску Bossman.

Пример PowerShell после задания `$App`, `$Repo`, `$Zip`, `$Evidence`; результат обязан быть новым файлом. SHA/hash ниже относятся только к историческому c222 RC:

```powershell
& "$App\runtime\python.exe" -I "$Repo\tools\responsiveness_probe.py" `
  --mode installed --installed-root "$App" --archive "$Zip" `
  --expected-sha c22201ef085ac4fab920ff64ed26f56f47bb16d3 `
  --expected-archive-sha256 9bbd11b31c22024c4a9d2c5e87db144a153764ec231cd2f1e95b4bf39ab298aa `
  --soak-seconds 0 --headed --json "$Evidence\installed-perf-new.json"
```

Пример НЕ запускался здесь. Нельзя назначать этому короткому запуску cold/warm/soak PASS. До длительного прогона нужен реальный Windows smoke, проверка cleanup и отдельное включение harness в новый packaging-контракт. Модельный/full-system soak и native startup остаются самостоятельными незакрытыми проверками.

## Что не закрыто

PREP-08: source-tested installed diagnostic implementation, но Windows execution/packaging/native cold-warm/реальный длительный full-process прогон pending. PREP-04/06/07/09/11/12/13 сохраняют локальные/живые/физические/интеллектуальные ограничения из памяти подготовки. Новый application ZIP и Release этой сессией не создавались, принятый ZIP не изменялся. PREP-03 прошёл CI исходников, но c222 по-прежнему не содержит эту правку. Полный V8 Total не объявлен.
