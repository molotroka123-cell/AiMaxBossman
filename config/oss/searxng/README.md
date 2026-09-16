# SearXNG для Bossman на Windows

Bossman уже использует официальный API SearXNG через существующий модуль
Web Research. Этот профиль запускает отдельный локальный поисковый сервис;
он не устанавливает модель и не обходит разрешения или журнал запросов Bossman.
Для поиска нужен интернет: текст запроса передаётся поисковым движкам.

## Запуск

Нужен Docker Desktop с Linux containers / WSL2. Команды выполняются в PowerShell
из корня репозитория. Сначала выберите конкретный образ из официального
[GHCR](https://github.com/searxng/searxng/pkgs/container/searxng) и скопируйте
его полный digest SHA256. Тег `latest` меняется и не подходит для фиксации сборки.
В переменную ниже подставьте именно выбранные 64 шестнадцатеричных символа.

```powershell
$env:BOSSMAN_SEARXNG_IMAGE = 'ghcr.io/searxng/searxng@sha256:<digest>'
if ($env:BOSSMAN_SEARXNG_IMAGE -notmatch '^ghcr\.io/searxng/searxng@sha256:[a-f0-9]{64}$') { throw 'Укажите полный digest официального образа' }
$randomBytes = New-Object byte[] 32
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$rng.GetBytes($randomBytes)
$rng.Dispose()
$env:BOSSMAN_SEARXNG_SECRET = [Convert]::ToBase64String($randomBytes)
docker compose -f config/oss/searxng/compose.yml up -d
if ($LASTEXITCODE -ne 0) { throw 'SearXNG не запущен: проверьте Docker и digest' }
$env:BOSSMAN_OSIRIS_ENABLED = '1'
$env:BOSSMAN_WEB_RESEARCH_ENABLED = '1'
$env:BOSSMAN_WEB_SEARXNG_URL = 'http://127.0.0.1:8888'
```

Теперь запустите Bossman из этого же окна PowerShell. Если он уже работал —
полностью перезапустите его: адрес SearXNG читается при старте процесса.
Переменные в примере действуют только в текущем PowerShell. При последующих
запусках задавайте три переменные `BOSSMAN_*` для Web Research в окружении
запуска Bossman. Для пересоздания контейнера вновь задайте образ и локальный
секрет. Секрет и личную конфигурацию не добавляйте в Git.

## Проверка результата

В разделе «Веб-поиск» отправьте обычный запрос, например «документация Python».
Либо вызовите авторизованный `POST /api/web/search` с телом
`{"query":"документация Python","fresh":true}` через существующий API Bossman.
У результата проверьте `backend: searxng-local`, `transport: live`, `raw_ref`
и реальные ссылки. `configured: true` означает только наличие настройки;
успехом считается фактический ответ. `empty_result` не доказывает поломку,
а `engines_down` нельзя выдавать за отсутствие информации в интернете.

- HTTP 403: убедитесь, что в смонтированном `settings.yml` включён `json` в
  `search.formats`; также проверьте правила доступа. Затем перезапустите контейнер.
- HTTP 429: сервис ограничил частоту — повторите позже, проверьте его limiter.
- Ошибка соединения: проверьте `docker compose -f config/oss/searxng/compose.yml ps`
  и `logs`. Bossman ожидает корень `http://127.0.0.1:8888`, без `/search`.
- HTML вместо JSON: проверьте порт и конфигурацию форматов, это может быть другой
  сервис или страница входа. Bossman не считает такой ответ результатами поиска.

Порт опубликован только на `127.0.0.1`. Это профиль для Bossman на Windows-хосте;
если сам Bossman работает в Docker, адрес `127.0.0.1` внутри его контейнера другой.
Для такой установки задайте адрес сервиса в собственной Docker-сети отдельно.

Официальные источники: [API](https://docs.searxng.org/dev/search_api.html),
[установка контейнера](https://docs.searxng.org/admin/installation-docker.html),
[настройки сервера](https://docs.searxng.org/admin/settings/settings_server.html).
Этот профиль требует отдельного прогона на компьютере владельца; его наличие
в репозитории не означает, что контейнер уже установлен или проверен на Windows.
