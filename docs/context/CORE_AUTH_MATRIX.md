# CORE AUTH MATRIX

Единый auth-слой — Stage 6 (`require_scope` поверх `DeviceService`), тот же, что
у `/remote/*`. Второго механизма нет. Сетевое положение (127.0.0.1, Tailscale)
НЕ является аутентификацией: за `tailscale serve` запрос приходит с loopback.

Скоупы: `chat` (задачи/проекты/поиск/видео/чтение), `events` (шина),
`approve` (подтверждения), `admin` (ресурсы/песочница/фабрика/AI Lab/смена
политики агента).

## Маршруты ядра (bossman/api.py)

| METHOD | PATH | SCOPE | MUTATES | SENSITIVE |
|---|---|---|---|---|
| POST | /tasks | chat | да | нет |
| GET | /tasks, /tasks/{id} | chat | нет | операционные данные |
| GET | /projects, /projects/{slug}/state, /journal | chat | нет | операционные данные |
| POST | /projects, /projects/{slug}/run, /pause, /tasks/{tid}/retry | chat | да | нет |
| POST | /projects/{slug}/approve | **approve** | да (разрешает траты) | нет |
| GET | /approvals | approve | нет | да (очередь решений) |
| POST | /approvals/{id} | **approve** | да (КОНСЕКВЕНТНО) | да |
| GET | /agents, /models, /spend, /changes | chat | нет | расходы/действия |
| PATCH | /agents/{name} (cloud_policy) | **admin** | да (КОНСЕКВЕНТНО) | да |
| POST | /models/{alias}/load, /unload | admin | да (состояние хоста) | нет |
| WS | /events | **events** | нет | поток операционных данных |
| POST | /telegram/webhook | — (свой секрет) | да | да |
| GET | / , /ui/* | — (публично) | нет | статика SPA, без данных |

`/telegram/webhook` не в скоупах: у него собственная граница — секрет вебхука в
`X-Telegram-Bot-Api-Secret-Token`, сверка постоянного времени; без секрета 403.
`/` и `/ui/*` отдают только оболочку SPA (HTML/JS без данных); все данные SPA
берёт через API выше, уже под скоупами.

## Маршруты подсистем (router-level dependency)

| ROUTER | PREFIX | SCOPE | ПОЧЕМУ |
|---|---|---|---|
| sandbox | /sandbox/* | admin | операционное состояние песочницы |
| resource_brain | /resource/* | admin | снимок пула/аренды |
| dev_factory | /dev-factory/* | admin | статус + ПОЛНЫЙ diff патча (исходники/пути) |
| ai_lab | /api/lab/* | admin | траектории/кандидаты/экспорт — сырьё обучения |
| video_factory | /video/* | chat | видео-задания — пользовательская операция |
| search_everything | /search | chat | поиск — пользовательская операция |
| remote_client | /remote/* | по-роутный (Stage 6) | свой набор скоупов chat/events/approve/admin |

Скоуп навешан на `APIRouter(dependencies=[...])` — он проверяется на КАЖДОМ
маршруте роутера при разрешении зависимостей, до тела обработчика.

## Оставшиеся публичные маршруты

Только `/` и `/ui/*` (статика оболочки) и `/telegram/webhook` (собственный
секрет). Данных без аутентификации ни один не отдаёт и состояние не меняет.

## Доказательство

`tests/test_core_auth_perimeter.py` (32) — адверсариально: аноним/чужой
скоуп/отзыв/блокировка/lock-all/подделанный Bearer → отказ ДО обработчика; для
`/approvals` дополнительно `approvals.decide` НЕ вызывается без скоупа approve;
WS без events-скоупа не открывает подписку.
