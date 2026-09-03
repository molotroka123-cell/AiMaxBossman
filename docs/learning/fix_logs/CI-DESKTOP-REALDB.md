# Learning Case: CI-DESKTOP-REALDB

## Metadata
MODEL: claude-opus-5
AGENT: fable-lead
START_SHA: abac544
END_SHA: HEAD+1
LEARNING_STATUS: VERIFIED
OUTCOME: FIXED
VERIFIED_BY: tool:pytest:command-center/tests/test_ux2_desktop.py
CONFIDENCE: 0.9
TAGS: {"domain": "test_infrastructure", "bug_class": "environment_dependency", "component": "command-center.tests.desktop", "severity": "HIGH"}
FINDINGS: CI-DESKTOP-REALDB

## Task
Command Center CI красный на обеих версиях Python после коммита 83221c9

## Symptom
Два теста test_ux2_desktop падают только в CI: assert 3 == 124 и KeyError: 'lock'. Локально проходят и по одному, и файлом, и в случайном порядке.

## Reproduction
- лог CI run 33812624142: выше строки FAILED лежит sqlalchemy OperationalError 'unable to open database file' и 'Application startup failed'
- локально: mv command-center/data command-center/data-hidden; затем те же два теста падают ровно так же

## Evidence
- на 44b8cd5, где все десять новых фич уже были, Command Center CI зелёный; красный появился ровно на 83221c9, который эти два теста и добавил
- после фикса: tests/test_ux2_desktop.py 43 passed при спрятанном каталоге command-center/data, то есть в условиях CI

## Hypotheses considered
- тесты подменяли data_dir, но не database_url, и шли в боевую базу (подтвердилось)
- флаки таймингов (отвергнуто: код возврата детерминированный)

## Rejected hypotheses + why
- флаки таймингов, поднять таймаут — assert 3 == 124 это детерминированный код возврата
- виноваты десять фич за флагами — на 44b8cd5 с ними CI зелёный
- пометить skip на py3.11 — тест проверяет настоящее поведение
- чинить Settings в продакшне — боевой путь строит Settings один раз из окружения
- поправить только два упавших теста — подмену без адреса базы делали все 14 площадок

## Root cause
Settings.database_url вычисляется один раз в __post_init__ из data_dir. Подмена только data_dir оставляла адрес боевой базы репозитория, и тесты, поднимающие настоящий сервер, шли в неё. Локально файл есть — зелено; в CI каталога command-center/data нет, сервер не стартует, run() возвращает 3 вместо кода launcher'а, замок не пишется.

## Relevant code paths
- command-center/bcc/config.py:Settings.__post_init__
- command-center/tests/test_ux2_desktop.py:_use_temp_data_dir
- command-center/bcc/desktop.py:_BackgroundServer.start

## Fix strategy
Один помощник _use_temp_data_dir меняет data_dir и database_url разом; на него переведены все четырнадцать площадок файла, включая автоиспользуемую фикстуру. Продакшн-код не менялся.

## Alternatives considered
- пересчитывать database_url при изменении data_dir в самом Settings (меняет боевое поведение ради теста)
- создавать каталог data в CI (прячет зависимость вместо её устранения)

## Why this fix was chosen
Устраняет класс, а не два случая: любая подмена каталога данных теперь уводит и базу, и это закреплено отдельным тестом.

## Files changed
- command-center/tests/test_ux2_desktop.py

## Tests added
- command-center/tests/test_ux2_desktop.py::test_desktop_tests_never_touch_the_real_database
- command-center/tests/test_ux2_desktop.py::test_server_backed_runs_survive_a_missing_repo_database

## Original reproduction after fix
2 failed на py3.11, те же 2 плюс два тайминговых на py3.12

## Adversarial variants
- боевой базы нет вовсе (чистый чекаут) — главный случай CI
- боевая база есть, но недоступна на запись
- подмена data_dir без подмены адреса базы — ловится отдельным тестом
- случайный порядок тестов и прогон файла целиком

## Regression
tests/test_ux2_desktop.py 43 passed, в том числе при спрятанном command-center/data

## Fresh external verification
лог GitHub Actions run 33812624142 плюс локальное воспроизведение условия CI

## Failed approaches / recovery lessons
- Настоящая причина падения часто выше по логу CI, чем строка FAILED

## Generalizable lessons
- Значение, вычисленное в __post_init__ из другого поля, не пересчитается при поздней подмене этого поля
- Тест, зелёный только там, где лежат боевые данные, не изолирован — он ещё не встретил чистый чекаут
- Воспроизводить надо условие CI, а не тест: спрячь то, чего в CI нет
- Чинить класс, а не случай: подмену делали четырнадцать площадок, упали две

## Teach local model
- Распознать: тест зелёный локально и красный в CI — сначала ищи, чего в CI нет на диске
- Предпочесть: подменять производные значения вместе с исходным
- Проверять: спрятать боевой каталог и прогнать тот же тест

## Limitations / follow-up
- два тайминговых падения на py3.12 (test_engine_stop, test_feat_governor_review) этим фиксом не закрыты и разбираются отдельно
