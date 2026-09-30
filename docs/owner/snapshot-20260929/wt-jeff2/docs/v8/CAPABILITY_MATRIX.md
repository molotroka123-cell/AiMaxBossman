# Матрица возможностей V8

Этот файл НЕ редактируется руками: он рисуется из
`CAPABILITY_MATRIX.json` командой
`python tools/render_capability_matrix.py`. Правка здесь потеряется.

Честная матрица возможностей V8. Четыре состояния и ни одного пятого. Строка без улики не имеет права существовать; улика — файл, который есть в дереве, а не обещание.

## Правила, по которым строки получают состояние

* INSUFFICIENT_EVIDENCE не превращается в PASS ни при каких условиях.
* OWNER_REQUIRED обязан назвать, ЧТО именно предоставляет владелец.
* IMPLEMENTED_LIVE_PENDING не выдаётся за работающую возможность.
* Замер из исходников на Linux — не доказательство об установленном архиве Windows.

## Счёт

| Состояние | Строк |
|---|---:|
| `IMPLEMENTED_AND_TESTED` | 14 |
| `IMPLEMENTED_LIVE_PENDING` | 2 |
| `OWNER_REQUIRED` | 5 |
| `NOT_IMPLEMENTED` | 4 |
| **всего** | **25** |

## Написано и проверено прогоном — `IMPLEMENTED_AND_TESTED`

*написано И проверено прогоном, улика названа*

### Каталог моделей Studio с самопроверкой

* **Улики:** `tests/test_studio_catalog.py`
* 6 тестов: валидность, уникальность, отвергнутые чужие значения. Цены в каталоге — null там, где не измерены.

### Шесть разных причин отказа провайдера, ни одна не проходит как completed

* **Улики:** `command-center/tests/test_studio_provider_states.py`
* 10 тестов парами: законный ответ проходит, 401/429/402/404/nsfw/битый JSON дают разные причины.

### Очередь Studio и отмена посреди рендера

* **Улики:** `command-center/tests/test_studio_runtime.py`, `command-center/tests/test_studio_cloud.py`
* Отдельного воркера нет: используется существующий Images tick 0.7 s.

### Галерея с provenance, корзина, восстановление, повторное использование

* **Улики:** `command-center/tests/test_studio_gallery_ui.py`, `tools/acceptance_registry.json`
* В профиле windows-installed: настоящий браузер на установленном архиве, 2 сценария из 46.

### Облако выключено по умолчанию и без политики не отправляет

* **Улики:** `command-center/tests/test_studio_cloud.py::test_default_cloud_disabled_never_dispatches`
* Это и есть то, что сейчас защищает владельца от BL-084.

### Согласие на эгресс привязано к хэшу: изменился файл — согласия нет

* **Улики:** `command-center/tests/test_studio_cloud.py::test_egress_requires_digest_bound_confirmation`

### Один скачиваемый архив, приёмка 46 из 46 на установленном продукте

* **Улики:** `tools/acceptance_registry.json`, `docs/final/ROLLBACK.md`
* Два зелёных прогона подряд: 126 на f195083e и 127 на 63c71b41, оба BOSSMAN_ASTRA6_FREEZE=OWNER_REQUIRED без блокеров.

### Обход интерфейса настоящим браузером на установленном архиве

* **Улики:** `tools/installed_ui_sweep.py`, `scripts/ui_acceptance_sweep.py`
* Прогон 126: 31 страница, 139 нажатий, dead/error/disabled_silent/vanished — ноль.

### Определение железа без WMIC (удалён в Win11 24H2/25H2)

* **Улики:** `scripts/target_hardware_acceptance.py`, `tests/test_target_hardware_acceptance.py`
* CIM → устаревший wmic → psutil; три состояния target/different/undetermined. Неопознанная машина НЕ выдаётся за пройденную приёмку.

### Комплект владельческого прогона едет в архиве как app-support/owner-final-run/

* **Улики:** `tools/build_windows_bundle.py`, `tests/test_windows_bundle_contract.py`, `tools/bundle_evening_test.py`, `tests/test_bundle_evening_test.py`
* Контракт в обе стороны: пропажа объявленного файла роняет сборку, незаявленный довесок тоже; приёмка внутри архива сверяет с MANIFEST.

### Сбой запуска приходит владельцу причиной, а не кодом выхода

* **Улики:** `command-center/tests/test_desktop_startup_refusal.py`, `docs/final/BUG_LEDGER.md#BL-083`
* Было «SystemExit: 3» на любой сбой. Идёт в job windows paths — туда, где окно и закрывается.

### Данные владельца переживают обновление и откат

* **Улики:** `scripts/update_rollback_rehearsal.py`, `command-center/tests/test_update_rollback_rehearsal.py`, `command-center/tests/test_db_schema_generation.py`
* Шесть шагов на настоящем runtime с парным контролем. UPDATE_ROLLBACK_DATA=PASS.

### Бюджет отзывчивости зафиксирован до замера и не двигается после

* **Улики:** `tools/responsiveness_budget.json`, `tests/test_responsiveness_budget.py`, `tools/responsiveness_probe.py`
* Девять пределов продублированы в тесте: поднять — значит изменить два файла одним коммитом.

### Замер отзывчивости из исходников на Linux

* **Улики:** `docs/v8/RESPONSIVENESS_BUDGET_RU.md`
* REFERENCE_ONLY: навигация p50 72.1 мс при 150, p95 107.2 при 400, галерея 100/1000 — 210.4/99.2 мс, рост RSS дерева за 50 циклов 5.1 % при 10. Доказательством об установленном архиве Windows НЕ является.


## Написано, но на живом маршруте не прогонялось — `IMPLEMENTED_LIVE_PENDING`

*написано, но на живом маршруте не прогонялось: не хватает ключа/доступа*

### Живая генерация в облаке по ключу владельца

* **Улики:** `tests/test_studio_live_owner.py`, `tools/studio_live_owner.py`
* Код и стенд есть, ключа нет: BOSSMAN_OPENROUTER_API_KEY в Actions пуст. Строка живого маршрута — OWNER_REQUIRED, не PASS.

### Агентная задача с настоящей моделью

* **Улики:** `docs/final/BUG_LEDGER.md#BL-073`
* Харнесс доказан заглушкой. С настоящей моделью не прогонялся: ключа нет.


## Требует того, чего у агента нет — `OWNER_REQUIRED`

*требует того, чего у агента нет: машины, ключа, сертификата, учётной записи*

### Публикация Release на GitHub с теми же байтами

* **Улики:** `INSTALL.md`
* **Владелец предоставляет:** операция выпуска из-под учётной записи владельца; из среды агента недоступна

### Приёмка на целевом железе (Ryzen AI Max+ 395 / Radeon 8060S / 128 ГиБ)

* **Улики:** `scripts/target_hardware_acceptance.py`
* **Владелец предоставляет:** сам компьютер; ни одного числа с него пока нет

### Подмена распакованной папки приложения на Windows

* **Улики:** `docs/final/ROLLBACK.md`
* **Владелец предоставляет:** машина владельца: две распакованные папки и его каталог данных

### Замер отзывчивости на установленном архиве Windows и на железе владельца

* **Улики:** `tools/responsiveness_probe.py`
* **Владелец предоставляет:** целевая машина: холодный и тёплый старт и настоящее дерево процессов осмысленны только там

### Сохранность интеллекта при переносе (Intelligence Preservation)

* **Улики:** `scripts/intelligence_retention_run.py`, `docs/final/BUG_LEDGER.md#BL-003`
* **Владелец предоставляет:** машина с локальной моделью: нужен ПАРНЫЙ замер до и после, а не прогон обвязки
* **Замер:** `INSUFFICIENT_EVIDENCE`
* Гейт красный НАМЕРЕННО, из-за отсутствия замера, а не из-за кода. INSUFFICIENT_EVIDENCE не превращается в PASS ни при каких условиях.


## Не написано — сказано прямо — `NOT_IMPLEMENTED`

*не написано, и это сказано прямо*

### Оценка владельца 0 не должна открывать платной модели первый вызов

* **Улики:** `docs/final/BUG_LEDGER.md#BL-084`
* ВОСПРОИЗВЕДЕНО: free_only=True и prices[платная]=0 → reserve возвращает upper_bound_usd 0 и ПРОПУСКАЕТ. Полоса Астера, PREP-03. Отсрочку даёт только выключенное облако и отсутствие ключа.

### Higgsfield как провайдер Studio

* **Улики:** `docs/v8/IMPLEMENTATION_PLAN.md#Фаза-3`
* REFERENCE_ONLY по решению: официальный REST-контракт владельцем не предоставлен, на 17.09 balance официального сервера даёт credits=0. Повторно не предлагается.

### Подпись Authenticode

* **Улики:** `docs/final/KNOWN_GOOD_SHAS.json`
* Архив не подписан. Требует сертификата владельца.

### Откат на сборки, выпущенные ДО отметки поколения схемы

* **Улики:** `docs/final/ROLLBACK.md`
* Проверять нечем: те сборки отметки не несут. Защищает ТОЛЬКО резервная копия каталога данных. Отметка чинит будущее, не прошлое.
