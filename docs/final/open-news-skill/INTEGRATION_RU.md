# Open News — отдельный скилл Bossman

Запись 2026-09-21 00:33 UTC. База: `456197deab7e4bbf2cbeaa11bee705360a0015c8`.
Единственная ветка: `claude/bossman-final-convergence-hu2702`.

## Фактическая интеграция

Skill ID `open-news`, канонический файл `.agents/skills/open-news/SKILL.md`.
`bcc.features.open_news` регистрирует `open_news.process` и `open_news.search`
в существующем ToolRegistry. Запуск скилла — существующий `/skills/{id}/run`
и обычный model/tool-loop, не отдельный исполнитель. Автозапуска нет.

Поставлены два **неизменённых** MIT-модуля alphap365/open-news 1.0.3 на SHA
`ebb0e9b4deb0bf8fa8983e6324276a51f091ab43`: token_filter и summarizer. Их Git
blob и SHA-256 сверены; `integrations/open-news/UPSTREAM.json` фиксирует байты,
лицензию и адаптации. Новых зависимостей и плавающего pip/git install нет.

Это честно **reviewed_subset**, не весь crawler upstream. Собственная обвязка
Bossman адаптирует фиксированный Google News RSS search endpoint и нормализацию
URL. Не подключены DDGS, полный текст по URL, произвольные RSS, redirect decoder,
JS/TUI/streaming. Поиск возвращает RSS-сниппеты и ссылки, не полные статьи.

## Разрешения и границы

Поиск всегда требует канонического подтверждения точных аргументов (ASK floor).
`approved=true`, permission browser.read и blanket AUTO не обходят его.
При `mode=supplied` контекстный запрет блокирует сетевой инструмент вообще;
при `mode=search` он не позволяет модели изменить тему/параметры запроса.
Офлайн-флаг блокирует приобретение данных; обработка переданного текста сети
не использует. Нет чтения личных файлов, ключей, POST, публикации, модели и cron.

HTTP: один асинхронный GET, только news.google.com, без redirects, cookies,
Authorization, environment proxy, browser/crawler fallback и автоматического
повтора. Общий timeout 20 s, тело <=1 MiB, сжатые ответы/DTD/entities отвергаются.
Отмена пробрасывается. Ошибка/пустая/полностью отбракованная выдача различаются.

Обработка: до 40 записей и 100000 символов входа, выдача до 20 записей и
30000 символов результатов, ограниченные краткие извлечения. Удаляются только
точные URL-дубли: независимые издания с одинаковым заголовком сохраняются.
Исходные даты не превращаются в даты событий; freshness_verified=false.
Русский текст поддержан фильтром, но upstream summarizer — Latin-scoring с
fallback на первые предложения, не семантическая RU-модель. Выход — external
untrusted data, не инструкции агенту.

## Поставка и исполненные проверки

`command-center/setup.py` реально копирует skill в `bcc/_skills/open-news/` и
MIT/NOTICE/UPSTREAM в пакет. `default_skill_roots` имеет bundled fallback после
всех прежних явных корней. Сборка отказывает при отсутствии required asset или
несовпадении vendored hash. Принятый c222 ZIP не меняется; новая сборка требуется.

Команда: `PYTHONPATH=command-center python -m pytest command-center/tests/test_open_news_skill.py -q`.
**73 passed / 0 failed / 0 skipped**, 0.40 s, Linux / Python 3.13.5, частичная
рабочая копия. Проверены реальные upstream functions, канонические registry/
permission/skill functions, build asset function и отрицательные контроли.
Исходные tools.py, permissions.py, features/__init__.py, skill_library.py и
setup.py перед использованием сверены по Git blob. Compileall изменённых
runtime/packaging файлов PASS. HTTP проверяется MockTransport, не живой сетью.
Проверка копирования assets — не сборка полного wheel/application ZIP.

## Перед использованием и V8

После нового установленного ZIP открыть Skills -> open-news, выбрать локального
агента. Для безопасной первой задачи: mode=supplied, маленький тестовый список
с URL/title/text. Реальный результат через модель и GUI пока NOT_RUN.
Для поиска нужен mode=search, query и отдельное подтверждение передачи запроса.

Интеграция не закрывает V8 Total. После её push нужны CI на том же SHA, новая
сборка и installed tests точных байтов. PREP-08 ещё требует короткого настоящего
Windows smoke нового измерителя из 456197de перед включением в package; native
cold/warm, длительный process-tree soak, реальные модели/Studio, два ZIP/rollback,
Ryzen и intelligence pair остаются невыполненными. Старые гейты не ослаблять.
