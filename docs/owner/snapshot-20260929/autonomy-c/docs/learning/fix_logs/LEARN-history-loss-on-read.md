# Learning Case: LEARN-history-loss-on-read

## Metadata
MODEL: claude-opus-5
AGENT: fable-lead
START_SHA: 6064462
END_SHA: HEAD+1
LEARNING_STATUS: VERIFIED
OUTCOME: FIXED
VERIFIED_BY: tool:pytest:tests
CONFIDENCE: 0.9
TAGS: {"domain": "data_integrity", "bug_class": "silent_data_loss", "component": "learning.trace", "severity": "HIGH"}
FINDINGS: LEARN-HISTORY-LOSS

## Task
Прогон тестов молча стирал записи корпуса обучения

## Symptom
Обычный прогон корневого набора менял отслеживаемые файлы: history.jsonl терял все 19 записей, fix_cases.jsonl — 2. Ни один тест не падал, ошибок не печаталось.

## Reproduction
- md5sum data/learning/*.jsonl; python -m pytest tests -q; md5sum -c — два файла расходятся
- сужается до tests/test_learning_trace.py::test_repo_corpus_files_validate, который создаёт LearningStore() поверх реального корпуса

## Evidence
- запись происходит на чтении: verified() -> _sync() -> _ensure_consistent() -> _materialize()
- две записи fix_cases не проходили валидацию по девяти пунктам и удалялись ДО того, как тест валидации их читал — тест был зелёным именно потому, что данные уничтожались
- после фикса: корневой набор 84 passed, md5 всех четырёх файлов корпуса не меняются

## Hypotheses considered
- bootstrap журнала читал только fix_cases и failed_experiments, история не усыновлялась (подтвердилось)
- две записи были дописаны в снимок руками, минуя store.add(), и в журнал не попали (подтвердилось)

## Rejected hypotheses + why
- стор сломан, чтение не должно писать — отвергнуто: пересборка производного снимка под журнал и есть замысел
- удалить две записи как дубликаты md-логов — отвергнуто: корпус читает retrieval, md читает человек
- добавить data/learning/ в .gitignore — отвергнуто: это прячет потерю данных, а не устраняет её
- усыновлять историю без ограничений — отвергнуто: запись с чужим case_id или версией не младше текущей перехватила бы авторитет

## Root cause
Журнал моложе корпуса: при переходе на журнал он собирался из fix_cases и failed_experiments, а history.jsonl не читался. Замещённые версии остались только в снимке, и первый же _materialize стёр их, потому что журнал их не подразумевал.

## Relevant code paths
- learning/trace.py:_ensure_consistent
- learning/trace.py:_materialize
- learning/trace.py:_adopt_orphan_history

## Fix strategy
_adopt_orphan_history вносит в журнал записи истории с известным case_id и версией строго младше текущей, в порядке версий, перед своей транзакцией; всё остальное не усыновляется и авторитетным стать не может. History включена в проверку согласованности, поэтому подменённый файл пересобирается. Две записи переписаны под текущую схему и добавлены через store.add().

## Alternatives considered
- запретить запись на пути чтения (ломает восстановление после краша между записями)
- оставить историю только в снимке и не сверять её с журналом (подменённый файл отдавался бы как свой)

## Why this fix was chosen
Инвариант «журнал авторитетен, снимки производны» становится истинным, а не почти истинным: после усыновления история выводится из журнала, и повторное чтение уже ничего не переписывает.

## Files changed
- learning/trace.py
- tests/test_learning_trace.py
- tests/test_learning_store_authority.py
- data/learning/journal.jsonl

## Tests added
- tests/test_learning_trace.py::test_repo_corpus_is_stable_under_read
- tests/test_learning_trace.py::test_repo_history_records_stay_reachable
- tests/test_learning_store_authority.py::test_pre_journal_history_is_adopted_not_destroyed
- tests/test_learning_store_authority.py::test_repeated_reads_do_not_change_files
- tests/test_learning_store_authority.py::test_history_entry_cannot_seize_authority

## Original reproduction after fix
history.jsonl 19 -> 0, fix_cases.jsonl 21 -> 19 после обычного прогона тестов

## Adversarial variants
- запись истории с case_id, которого нет в журнале
- версия равна текущей или новее — авторитет не перехватывается
- дубли (case_id, version) в истории: усыновляются один раз, дальше пропускаются
- пустой журнал при непустой истории (ветка bootstrap)
- повторные чтения после усыновления не переписывают файлы

## Regression
корневой набор 84 passed (было 79); md5 корпуса стабильны между прогонами

## Fresh external verification
pytest на реальном корпусе репозитория плюс байтовое сравнение файлов

## Failed approaches / recovery lessons
- Путь чтения, который чинит производное состояние, — это путь записи; проверять его надо байтами до и после

## Generalizable lessons
- Миграция на журнал обязана усыновить каждый производный файл, а не только те, что читает текущий код
- Тест валидации, идущий после самовосстанавливающегося чтения, проверяет лишь то, что выжило
- Нельзя править руками хранилище, у которого есть API: store.add() валидирует, версионирует и журналирует
- Усыновляя недоверенные данные в авторитетный журнал, разрешай только то, что доказуемо не меняет текущий ответ

## Teach local model
- Распознать: git status грязный после прогона тестов — искать запись на пути чтения
- Предпочесть: байтовое сравнение файлов до/после вместо проверки возвращённых строк
- Проверять: повторное чтение не должно менять ничего (идемпотентность самовосстановления)

## Limitations / follow-up
- усыновление восстанавливает только то, что физически лежит в history.jsonl; версии, стёртые прошлыми прогонами до этого фикса, невосстановимы
