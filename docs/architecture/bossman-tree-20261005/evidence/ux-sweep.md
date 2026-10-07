# UX sweep - Bossman Command Center (installed)

- build: `803aa4d9103aa723002590948a74d1cc9b23e155` (health/live: `{"app":"bossman-command-center","alive":true,"status":"ALIVE","build_sha":"803aa4d9103aa723002590948a74d1cc9b23e155","build_sha_short":"803aa4d9103a","source_identity":"PASS","source":"installed_build`)
- per-viewport builds: desktop: `803aa4d9103a` (health/live sha `803aa4d9103aa723002590948a74d1cc9b23e155`); phone: `eba6dc592ad9` (health/live sha `eba6dc592ad9218a631c35d3d8a90e667c67968e`)
- harness sha (git HEAD): `65657cea3ade32fe2c72884be8f809c894a3349f`; run 2026-10-07T12:51:29+00:00 .. 2026-10-07T14:55:22+00:00
- viewports: desktop, phone; pages: 53; sampling: first 2 identical controls per page
- external network blocked (page.route abort): {"desktop": {}, "phone": {}}

## Totals

| viewport | enumerated | probes | OK | DEAD | ERROR | BLOCKED_BY_DESIGN | DISABLED | UNVERIFIED | gated | sampled(not probed) |
|---|---|---|---|---|---|---|---|---|---|---|
| desktop | 1339 | 794 | 663 | 3 | 121 | 7 | 35 | 0 | 82 | 818 |
| phone | 1274 | 680 | 611 | 3 | 57 | 9 | 33 | 10 | 76 | 812 |
| all | 2613 | 1474 | 1274 | 6 | 178 | 16 | 68 | 10 | 158 | 1630 |

OK = an effect was observed; DEAD = nothing observable; ERROR = pageerror/console.error/5xx/unexplained 4xx/stuck overlay/click impossible;
BLOCKED_BY_DESIGN = confirm gate or intended refusal with visible feedback; gated = destructive/outward control probed only up to the gate (mutating requests stubbed).

## Per page

| page | viewport | enumerated | probed | OK | DEAD | ERROR | BLOCKED | DISABLED | UNVERIFIED | gated |
|---|---|---|---|---|---|---|---|---|---|---|
| shell | desktop | 58 | 72 | 69 | 0 | 3 | 0 | 0 | 0 | 3 |
| home | desktop | 7 | 8 | 8 | 0 | 0 | 0 | 0 | 0 | 1 |
| models | desktop | 5 | 19 | 18 | 0 | 1 | 0 | 1 | 0 | 3 |
| agents | desktop | 2 | 14 | 14 | 0 | 0 | 0 | 0 | 0 | 0 |
| tasks | desktop | 14 | 32 | 32 | 0 | 0 | 0 | 0 | 0 | 3 |
| schedules | desktop | 2 | 16 | 16 | 0 | 0 | 0 | 0 | 0 | 0 |
| approvals | desktop | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| system | desktop | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| settings | desktop | 33 | 43 | 37 | 0 | 3 | 3 | 2 | 0 | 2 |
| capability-tree | desktop | 830 | 60 | 28 | 0 | 30 | 2 | 0 | 0 | 13 |
| poker-vision | desktop | 25 | 25 | 9 | 0 | 16 | 0 | 3 | 0 | 6 |
| oss | desktop | 30 | 27 | 27 | 0 | 0 | 0 | 3 | 0 | 8 |
| video-studio | desktop | 16 | 28 | 11 | 1 | 16 | 0 | 6 | 0 | 0 |
| music-studio | desktop | 12 | 20 | 20 | 0 | 0 | 0 | 1 | 0 | 1 |
| bossman-chat | desktop | 3 | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 1 |
| home-v3 | desktop | 23 | 19 | 19 | 0 | 0 | 0 | 0 | 0 | 2 |
| chat | desktop | 1 | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| apps | desktop | 24 | 16 | 14 | 0 | 2 | 0 | 0 | 0 | 2 |
| overview | desktop | 8 | 9 | 9 | 0 | 0 | 0 | 0 | 0 | 1 |
| missions | desktop | 2 | 12 | 12 | 0 | 0 | 0 | 0 | 0 | 0 |
| router | desktop | 11 | 13 | 13 | 0 | 0 | 0 | 0 | 0 | 0 |
| governor | desktop | 4 | 5 | 5 | 0 | 0 | 0 | 0 | 0 | 0 |
| file-intelligence | desktop | 1 | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| resources | desktop | 6 | 14 | 8 | 0 | 6 | 0 | 0 | 0 | 0 |
| skills | desktop | 4 | 20 | 20 | 0 | 0 | 0 | 0 | 0 | 0 |
| terminal | desktop | 12 | 19 | 19 | 0 | 0 | 0 | 0 | 0 | 2 |
| benchmarks | desktop | 1 | 5 | 5 | 0 | 0 | 0 | 0 | 0 | 2 |
| browser | desktop | 1 | 9 | 7 | 0 | 1 | 1 | 0 | 0 | 0 |
| coding | desktop | 3 | 5 | 5 | 0 | 0 | 0 | 1 | 0 | 0 |
| rave | desktop | 13 | 22 | 22 | 0 | 0 | 0 | 1 | 0 | 4 |
| agentmap | desktop | 1 | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| orchestras | desktop | 4 | 4 | 4 | 0 | 0 | 0 | 1 | 0 | 0 |
| forks | desktop | 1 | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| healing | desktop | 4 | 5 | 4 | 0 | 1 | 0 | 0 | 0 | 0 |
| openrouter | desktop | 2 | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 |
| command | desktop | 20 | 15 | 15 | 0 | 0 | 0 | 0 | 0 | 1 |
| builder | desktop | 1 | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| images | desktop | 17 | 28 | 27 | 0 | 0 | 1 | 0 | 0 | 1 |
| images?studio=1 | desktop | 30 | 45 | 36 | 1 | 8 | 0 | 0 | 0 | 2 |
| trading_lab | desktop | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| v15-owner-run | desktop | 7 | 9 | 9 | 0 | 0 | 0 | 0 | 0 | 2 |
| mission_console | desktop | 8 | 9 | 9 | 0 | 0 | 0 | 0 | 0 | 2 |
| web_research | desktop | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| control | desktop | 3 | 2 | 2 | 0 | 0 | 0 | 1 | 0 | 1 |
| web_designer | desktop | 11 | 25 | 4 | 0 | 21 | 0 | 0 | 0 | 3 |
| objectives | desktop | 2 | 12 | 12 | 0 | 0 | 0 | 0 | 0 | 0 |
| jeff-settings | desktop | 26 | 36 | 36 | 0 | 0 | 0 | 0 | 0 | 1 |
| motion-studio | desktop | 5 | 9 | 6 | 0 | 3 | 0 | 0 | 0 | 2 |
| jeff-passports | desktop | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| jeff-insights | desktop | 3 | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 1 |
| autonomy | desktop | 2 | 2 | 2 | 0 | 0 | 0 | 1 | 0 | 2 |
| telegram_calls | desktop | 17 | 14 | 14 | 0 | 0 | 0 | 7 | 0 | 8 |
| chat.html | desktop | 24 | 30 | 19 | 1 | 10 | 0 | 7 | 0 | 2 |
| shell | phone | 10 | 26 | 25 | 1 | 0 | 0 | 0 | 0 | 2 |
| home | phone | 7 | 8 | 8 | 0 | 0 | 0 | 0 | 0 | 1 |
| models | phone | 5 | 18 | 17 | 0 | 1 | 0 | 1 | 1 | 3 |
| agents | phone | 2 | 14 | 14 | 0 | 0 | 0 | 0 | 0 | 0 |
| tasks | phone | 14 | 16 | 16 | 0 | 0 | 0 | 0 | 0 | 2 |
| schedules | phone | 2 | 16 | 16 | 0 | 0 | 0 | 0 | 0 | 0 |
| approvals | phone | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| system | phone | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| settings | phone | 33 | 41 | 38 | 0 | 0 | 3 | 1 | 0 | 2 |
| capability-tree | phone | 831 | 59 | 28 | 0 | 29 | 2 | 0 | 0 | 12 |
| poker-vision | phone | 25 | 24 | 9 | 0 | 15 | 0 | 3 | 0 | 5 |
| oss | phone | 30 | 27 | 27 | 0 | 0 | 0 | 3 | 0 | 8 |
| video-studio | phone | 15 | 18 | 18 | 0 | 0 | 0 | 6 | 1 | 0 |
| music-studio | phone | 12 | 20 | 20 | 0 | 0 | 0 | 1 | 0 | 1 |
| bossman-chat | phone | 3 | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 1 |
| home-v3 | phone | 23 | 19 | 19 | 0 | 0 | 0 | 0 | 0 | 2 |
| chat | phone | 1 | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| apps | phone | 24 | 16 | 16 | 0 | 0 | 0 | 0 | 0 | 2 |
| overview | phone | 8 | 9 | 9 | 0 | 0 | 0 | 0 | 0 | 1 |
| missions | phone | 2 | 12 | 12 | 0 | 0 | 0 | 0 | 0 | 0 |
| router | phone | 11 | 13 | 13 | 0 | 0 | 0 | 0 | 0 | 0 |
| governor | phone | 4 | 5 | 5 | 0 | 0 | 0 | 0 | 0 | 0 |
| file-intelligence | phone | 1 | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| resources | phone | 6 | 10 | 10 | 0 | 0 | 0 | 0 | 0 | 0 |
| skills | phone | 4 | 20 | 20 | 0 | 0 | 0 | 0 | 0 | 0 |
| terminal | phone | 12 | 15 | 15 | 0 | 0 | 0 | 0 | 0 | 2 |
| benchmarks | phone | 1 | 5 | 5 | 0 | 0 | 0 | 0 | 0 | 2 |
| browser | phone | 1 | 9 | 8 | 0 | 0 | 1 | 0 | 0 | 0 |
| coding | phone | 3 | 5 | 5 | 0 | 0 | 0 | 1 | 0 | 0 |
| rave | phone | 13 | 21 | 21 | 0 | 0 | 0 | 1 | 0 | 4 |
| agentmap | phone | 1 | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| orchestras | phone | 4 | 4 | 4 | 0 | 0 | 0 | 1 | 0 | 0 |
| forks | phone | 1 | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| healing | phone | 4 | 5 | 5 | 0 | 0 | 0 | 0 | 0 | 0 |
| openrouter | phone | 2 | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 |
| command | phone | 12 | 16 | 16 | 0 | 0 | 0 | 0 | 0 | 1 |
| builder | phone | 1 | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| images | phone | 17 | 18 | 17 | 0 | 0 | 1 | 0 | 0 | 1 |
| images?studio=1 | phone | 30 | 45 | 36 | 1 | 8 | 0 | 0 | 0 | 2 |
| trading_lab | phone | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| v15-owner-run | phone | 7 | 9 | 9 | 0 | 0 | 0 | 0 | 0 | 2 |
| mission_console | phone | 5 | 7 | 7 | 0 | 0 | 0 | 0 | 0 | 1 |
| web_research | phone | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| control | phone | 3 | 2 | 2 | 0 | 0 | 0 | 1 | 0 | 1 |
| web_designer | phone | 11 | 17 | 14 | 1 | 0 | 2 | 0 | 8 | 3 |
| objectives | phone | 2 | 12 | 12 | 0 | 0 | 0 | 0 | 0 | 0 |
| jeff-settings | phone | 26 | 37 | 37 | 0 | 0 | 0 | 0 | 0 | 1 |
| motion-studio | phone | 5 | 9 | 9 | 0 | 0 | 0 | 0 | 0 | 2 |
| jeff-passports | phone | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| jeff-insights | phone | 3 | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 1 |
| autonomy | phone | 2 | 2 | 2 | 0 | 0 | 0 | 1 | 0 | 2 |
| telegram_calls | phone | 17 | 14 | 14 | 0 | 0 | 0 | 6 | 0 | 8 |
| chat.html | phone | 18 | 20 | 16 | 0 | 4 | 0 | 7 | 0 | 1 |

## DEAD / ERROR findings (194)

| page | vp | control | verdict | detail | status |
|---|---|---|---|---|---|
| shell | desktop | Главная | ERROR | control vanished after re-render | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| shell | desktop | Poker Vision | ERROR | HTTP 503 GET /api/poker-vision/capabilities | BY_DESIGN: Poker Vision sidecar not running in throwaway env -> 503 PV_SERVICE_DOWN; UI sh |
| shell | desktop | Главная | ERROR | control vanished after re-render | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| models | desktop | Добавить модель | ERROR | control vanished after re-render | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| settings | desktop | Тёмная | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| settings | desktop | Светлая | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| settings | desktop | × | ERROR | control vanished after re-render | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| capability-tree | desktop | (без подписи) | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | desktop | Живой ответ 7,3 с описан 1 октября; непр | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | desktop | /memory, /pause_memory, /forget; приватн | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | desktop | Факты, источники, уверенность, checkpoin | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | desktop | participant/companion profile, behavior  | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | desktop | Код, тесты и отдельные ветки есть; реаль | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | desktop | В октябрьском аудите soak NOT RUN; заявл | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | desktop | Найден исходный модуль. Наличие тестов и | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | desktop | Найден исходный модуль. Наличие тестов и | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | desktop | (без подписи) | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | desktop | Результат не доказан; прежний cold page  | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | desktop | Файлы приложения присутствуют; готовност | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | desktop | Файлы приложения присутствуют; готовност | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | desktop | Распознавание покерного стола по пикселя | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | desktop | По сохранённому отчёту 4 октября; текущу | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | desktop | Pi не заменил PRIMARY; Ornith участвовал | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | desktop | В отчёте встречается resident community  | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | desktop | Низкий приоритет; память ограничена. Не  | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | desktop | Частичная telemetry есть; единый точный  | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | desktop | Observe описан; действие WAIT_APPROVAL.  | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | desktop | Предложенный ограниченный UX sidecar; не | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | desktop | Pipeline и документы есть; непрерывный п | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | desktop | Каталог кандидатов, не установленные раб | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | desktop | SKILL.md найден; не означает подключение | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | desktop | Scene spec -> finished video: voice-over | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | desktop | Render the Bossman "32 days" promo (tool | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | desktop | JSON карта подготовлена для загрузки. В  | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | desktop | Найден исходный модуль. Наличие тестов и | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | desktop | Модуль найден. Семантика и live работосп | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | desktop | Модуль найден. Семантика и live работосп | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| poker-vision | desktop | (page render) | ERROR | GET /api/poker-vision/capabilities -> 503 | BY_DESIGN: Poker Vision sidecar not running in throwaway env -> 503 PV_SERVICE_DOWN; UI sh |
| poker-vision | desktop | STOP | ERROR | control vanished after re-render | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| poker-vision | desktop | × | ERROR | control vanished after re-render | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| poker-vision | desktop | Выбрать источник | ERROR | console: ApiError: сервис Poker Vision не отвечает: ConnectTimeout     at rawRequest (http://127.0.0.1:8893/ap | BY_DESIGN: Poker Vision sidecar not running in throwaway env -> 503 PV_SERVICE_DOWN; UI sh |
| poker-vision | desktop | Сменить источник | ERROR | console: ApiError: сервис Poker Vision не отвечает: ConnectTimeout     at rawRequest (http://127.0.0.1:8893/ap | BY_DESIGN: Poker Vision sidecar not running in throwaway env -> 503 PV_SERVICE_DOWN; UI sh |
| poker-vision | desktop | Пауза | ERROR | console: ApiError: сервис Poker Vision не отвечает: ConnectTimeout     at rawRequest (http://127.0.0.1:8893/ap | BY_DESIGN: Poker Vision sidecar not running in throwaway env -> 503 PV_SERVICE_DOWN; UI sh |
| poker-vision | desktop | Продолжить | ERROR | console: ApiError: сервис Poker Vision не отвечает: ConnectTimeout     at rawRequest (http://127.0.0.1:8893/ap | BY_DESIGN: Poker Vision sidecar not running in throwaway env -> 503 PV_SERVICE_DOWN; UI sh |
| poker-vision | desktop | Сдвинуть | ERROR | HTTP 503 GET /api/poker-vision/overlay.json; HTTP 503 POST /api/poker-vision/desk/sandbox/move; HTTP 503 GET / | BY_DESIGN: Poker Vision sidecar not running in throwaway env -> 503 PV_SERVICE_DOWN; UI sh |
| poker-vision | desktop | Вернуть | ERROR | HTTP 503 GET /api/poker-vision/overlay.json; HTTP 503 POST /api/poker-vision/desk/sandbox/move; HTTP 503 GET / | BY_DESIGN: Poker Vision sidecar not running in throwaway env -> 503 PV_SERVICE_DOWN; UI sh |
| poker-vision | desktop | Размер 420×760 | ERROR | HTTP 503 GET /api/poker-vision/overlay.json; HTTP 503 POST /api/poker-vision/desk/sandbox/resize; HTTP 503 GET | BY_DESIGN: Poker Vision sidecar not running in throwaway env -> 503 PV_SERVICE_DOWN; UI sh |
| poker-vision | desktop | Размер 520×900 | ERROR | HTTP 503 GET /api/poker-vision/overlay.json; HTTP 503 POST /api/poker-vision/desk/sandbox/resize; HTTP 503 GET | BY_DESIGN: Poker Vision sidecar not running in throwaway env -> 503 PV_SERVICE_DOWN; UI sh |
| poker-vision | desktop | Перекрыть | ERROR | HTTP 503 GET /api/poker-vision/overlay.json; HTTP 503 POST /api/poker-vision/desk/sandbox/cover; HTTP 503 GET  | BY_DESIGN: Poker Vision sidecar not running in throwaway env -> 503 PV_SERVICE_DOWN; UI sh |
| poker-vision | desktop | Свернуть | ERROR | HTTP 503 GET /api/poker-vision/overlay.json; HTTP 503 POST /api/poker-vision/desk/sandbox/minimize; HTTP 503 G | BY_DESIGN: Poker Vision sidecar not running in throwaway env -> 503 PV_SERVICE_DOWN; UI sh |
| poker-vision | desktop | Закрыть | ERROR | HTTP 503 GET /api/poker-vision/overlay.json; HTTP 503 POST /api/poker-vision/desk/sandbox/close; HTTP 503 GET  | BY_DESIGN: Poker Vision sidecar not running in throwaway env -> 503 PV_SERVICE_DOWN; UI sh |
| poker-vision | desktop | Открыть снова | ERROR | HTTP 503 GET /api/poker-vision/overlay.json; HTTP 503 POST /api/poker-vision/desk/sandbox/reopen; HTTP 503 GET | BY_DESIGN: Poker Vision sidecar not running in throwaway env -> 503 PV_SERVICE_DOWN; UI sh |
| poker-vision | desktop | Калибровать и проверить | ERROR | HTTP 503 GET /api/poker-vision/overlay.json; HTTP 503 POST /api/poker-vision/calibrate; HTTP 503 GET /api/poke | BY_DESIGN: Poker Vision sidecar not running in throwaway env -> 503 PV_SERVICE_DOWN; UI sh |
| video-studio | desktop | Применить | DEAD | no DOM / URL / request / dialog / toast / storage change | BY_DESIGN: hand-repro: dialog 'Применить' with the required name empty is blocked by nativ |
| video-studio | desktop | Монтаж | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| video-studio | desktop | Цвет | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| video-studio | desktop | Цвет | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| video-studio | desktop | Звук | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| video-studio | desktop | Звук | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| video-studio | desktop | VFX | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| video-studio | desktop | VFX | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| video-studio | desktop | ИИ | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| video-studio | desktop | Choose a project | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| video-studio | desktop | Edit | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| video-studio | desktop | Color | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| video-studio | desktop | Audio | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| video-studio | desktop | AI | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| video-studio | desktop | RU | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| video-studio | desktop | ＋ New project | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| video-studio | desktop | ＋ Новый проект | ERROR | control vanished after re-render | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| apps | desktop | Запретить запуск приложений | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| apps | desktop | × | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| resources | desktop | Поровну | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| resources | desktop | На скорость | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| resources | desktop | × | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| resources | desktop | На скорость | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| resources | desktop | Экономно | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| resources | desktop | × | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| browser | desktop | Обновить | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | UNRESOLVED: retry could not re-locate the control after re-render (opener state not reprod |
| healing | desktop | За сколько секунд считать | ERROR | TimeoutError: Page.goto: Timeout 8000ms exceeded. | HARNESS_ARTIFACT: first retry with scroll + 15 s timeout succeeded (earlier failure = 4 s  |
| images?studio=1 | desktop | 1 | DEAD | no DOM / URL / request / dialog / toast / storage change | BY_DESIGN: hand-repro: it is the 'Количество' number input (value 1); fill 3 + ArrowUp ->  |
| images?studio=1 | desktop | on | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| images?studio=1 | desktop | on | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| images?studio=1 | desktop | 0 | ERROR | TimeoutError: Locator.fill: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| images?studio=1 | desktop | 0 | ERROR | TimeoutError: Locator.fill: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| images?studio=1 | desktop | Верхняя стоимость моделей | ERROR | TimeoutError: Locator.fill: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| images?studio=1 | desktop | Разрешённые CDN | ERROR | TimeoutError: Locator.fill: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| images?studio=1 | desktop | (без подписи) | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| images?studio=1 | desktop | (без подписи) | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| web_designer | desktop | Обновить | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| web_designer | desktop | Мой сайт · v1 | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| web_designer | desktop | Конструктор блоков | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| web_designer | desktop | + Проект | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| web_designer | desktop | Скачать HTML | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| web_designer | desktop | Задача «сделать сайт» | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| web_designer | desktop | Шаблон: авто Шаблон: Лендинг Шаблон: Пор | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| web_designer | desktop | Палитра: авто Палитра: blue Палитра: dar | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| web_designer | desktop | Сгенерировать сайт | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| web_designer | desktop | Выделение: вкл | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| web_designer | desktop | открыть в новой вкладке | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| web_designer | desktop | Обновить | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| web_designer | desktop | ПК · 1440 × 900 Ноутбук · 1280 × 800 Пла | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| web_designer | desktop | Применить размер | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| web_designer | desktop | Лендинг Герой-блок, преимущества, цифры  | ERROR | control vanished after re-render | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| web_designer | desktop | Портфолио Сетка работ, навыки, обо мне и | ERROR | control vanished after re-render | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| web_designer | desktop | Кафе / ресторан Меню с ценами, галерея,  | ERROR | control vanished after re-render | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| web_designer | desktop | Магазин Витрина товаров с ценами, достав | ERROR | control vanished after re-render | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| web_designer | desktop | Блог Список статей, рубрики и подписка н | ERROR | control vanished after re-render | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| web_designer | desktop | Агентство / студия Услуги, кейсы, команд | ERROR | control vanished after re-render | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| web_designer | desktop | ИИ: творческий бриф (локально) Один запр | ERROR | control vanished after re-render | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| motion-studio | desktop | Остановить | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | UNRESOLVED: retry could not re-locate the control after re-render (opener state not reprod |
| motion-studio | desktop | Остановить | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | UNRESOLVED: retry could not re-locate the control after re-render (opener state not reprod |
| motion-studio | desktop | × | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: first retry with scroll + 15 s timeout succeeded (earlier failure = 4 s  |
| chat.html | desktop | Развернуть боковую панель | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| chat.html | desktop | Новый чат (Ctrl+N) | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| chat.html | desktop | Новый чат Ctrl N | ERROR | control vanished after re-render | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| chat.html | desktop | Поиск чатов (Ctrl+K) | ERROR | control vanished after re-render | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| chat.html | desktop | Проекты 0 | ERROR | control vanished after re-render | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| chat.html | desktop | Архив | ERROR | control vanished after re-render | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| chat.html | desktop | Тёмная тема | ERROR | opener not found on replay | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| chat.html | desktop | Закрыть панель Thinking & Actions (Ctrl+ | ERROR | control vanished after re-render | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| chat.html | desktop | План | ERROR | control vanished after re-render | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| chat.html | desktop | Проверка | ERROR | control vanished after re-render | HARNESS_ARTIFACT: harness: replay/vanished (state mutated by earlier probe); harness later |
| chat.html | desktop | Прикрепить файлы (или перетащите их в ок | DEAD | no DOM / URL / request / dialog / toast / storage change | BY_DESIGN: hand-repro: 'Прикрепить файлы' opens the native file chooser (multiple=true); a |
| shell | phone | Очистить ленту | DEAD | GATED control produced no effect even up to the gate: no DOM / URL / request / dialog / toast / storage change | BY_DESIGN: hand-repro: 'Очистить ленту' on an already empty feed re-renders the same 'Собы |
| models | phone | Ollama · OpenAI-совместимый · http://127 | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | UNRESOLVED: retry could not re-locate the control after re-render (opener state not reprod |
| models | phone | Добавить модель | UNVERIFIED | control not found again after re-render (harness could not reach it; not counted as a product bug) | HARNESS_ARTIFACT: harness could not re-find the control after re-render/replay (not counte |
| capability-tree | phone | (без подписи) | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | phone | Живой ответ 7,3 с описан 1 октября; непр | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | phone | /memory, /pause_memory, /forget; приватн | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | phone | Факты, источники, уверенность, checkpoin | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | phone | participant/companion profile, behavior  | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | phone | Код, тесты и отдельные ветки есть; реаль | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | phone | В октябрьском аудите soak NOT RUN; заявл | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | phone | (без подписи) | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | phone | Результат не доказан; прежний cold page  | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | phone | Файлы приложения присутствуют; готовност | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | phone | Файлы приложения присутствуют; готовност | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | phone | Распознавание покерного стола по пикселя | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | phone | По сохранённому отчёту 4 октября; текущу | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | phone | Pi не заменил PRIMARY; Ornith участвовал | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | phone | В отчёте встречается resident community  | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | phone | Низкий приоритет; память ограничена. Не  | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | phone | Частичная telemetry есть; единый точный  | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | phone | Observe описан; действие WAIT_APPROVAL.  | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | phone | Предложенный ограниченный UX sidecar; не | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | phone | Pipeline и документы есть; непрерывный п | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | phone | Каталог кандидатов, не установленные раб | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | phone | SKILL.md найден; не означает подключение | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | phone | Scene spec -> finished video: voice-over | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | phone | Render the Bossman "32 days" promo (tool | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | phone | JSON карта подготовлена для загрузки. В  | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | phone | Навык импортирован в каталог (superpower | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | phone | Capability manifest; scope: mcp.execute. | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | phone | Модуль найден. Семантика и live работосп | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| capability-tree | phone | Модуль найден. Семантика и live работосп | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| poker-vision | phone | (page render) | ERROR | GET /api/poker-vision/capabilities -> 503 | BY_DESIGN: Poker Vision sidecar not running in the throwaway env -> 503 / ConnectTimeout;  |
| poker-vision | phone | × | ERROR | HTTP 503 GET /api/poker-vision/overlay.json; HTTP 503 GET /api/poker-vision/overlay.json; HTTP 503 GET /api/po | BY_DESIGN: Poker Vision sidecar not running in the throwaway env -> 503 / ConnectTimeout;  |
| poker-vision | phone | Выбрать источник | ERROR | console: ApiError: сервис Poker Vision не отвечает: ConnectTimeout     at rawRequest (http://127.0.0.1:8893/ap | BY_DESIGN: Poker Vision sidecar not running in the throwaway env -> 503 / ConnectTimeout;  |
| poker-vision | phone | Сменить источник | ERROR | console: ApiError: сервис Poker Vision не отвечает: ConnectTimeout     at rawRequest (http://127.0.0.1:8893/ap | BY_DESIGN: Poker Vision sidecar not running in the throwaway env -> 503 / ConnectTimeout;  |
| poker-vision | phone | Пауза | ERROR | console: ApiError: сервис Poker Vision не отвечает: ConnectTimeout     at rawRequest (http://127.0.0.1:8893/ap | BY_DESIGN: Poker Vision sidecar not running in the throwaway env -> 503 / ConnectTimeout;  |
| poker-vision | phone | Продолжить | ERROR | console: ApiError: сервис Poker Vision не отвечает: ConnectTimeout     at rawRequest (http://127.0.0.1:8893/ap | BY_DESIGN: Poker Vision sidecar not running in the throwaway env -> 503 / ConnectTimeout;  |
| poker-vision | phone | Сдвинуть | ERROR | HTTP 503 GET /api/poker-vision/overlay.json; HTTP 503 POST /api/poker-vision/desk/sandbox/move; HTTP 503 GET / | BY_DESIGN: Poker Vision sidecar not running in the throwaway env -> 503 / ConnectTimeout;  |
| poker-vision | phone | Вернуть | ERROR | HTTP 503 GET /api/poker-vision/overlay.json; HTTP 503 POST /api/poker-vision/desk/sandbox/move; HTTP 503 GET / | BY_DESIGN: Poker Vision sidecar not running in the throwaway env -> 503 / ConnectTimeout;  |
| poker-vision | phone | Размер 420×760 | ERROR | HTTP 503 GET /api/poker-vision/overlay.json; HTTP 503 POST /api/poker-vision/desk/sandbox/resize; HTTP 503 GET | BY_DESIGN: Poker Vision sidecar not running in the throwaway env -> 503 / ConnectTimeout;  |
| poker-vision | phone | Размер 520×900 | ERROR | HTTP 503 GET /api/poker-vision/overlay.json; HTTP 503 POST /api/poker-vision/desk/sandbox/resize; HTTP 503 GET | BY_DESIGN: Poker Vision sidecar not running in the throwaway env -> 503 / ConnectTimeout;  |
| poker-vision | phone | Перекрыть | ERROR | HTTP 503 GET /api/poker-vision/overlay.json; HTTP 503 POST /api/poker-vision/desk/sandbox/cover; HTTP 503 GET  | BY_DESIGN: Poker Vision sidecar not running in the throwaway env -> 503 / ConnectTimeout;  |
| poker-vision | phone | Свернуть | ERROR | HTTP 503 GET /api/poker-vision/overlay.json; HTTP 503 POST /api/poker-vision/desk/sandbox/minimize; HTTP 503 G | BY_DESIGN: Poker Vision sidecar not running in the throwaway env -> 503 / ConnectTimeout;  |
| poker-vision | phone | Закрыть | ERROR | HTTP 503 GET /api/poker-vision/overlay.json; HTTP 503 POST /api/poker-vision/desk/sandbox/close; HTTP 503 GET  | BY_DESIGN: Poker Vision sidecar not running in the throwaway env -> 503 / ConnectTimeout;  |
| poker-vision | phone | Открыть снова | ERROR | HTTP 503 GET /api/poker-vision/overlay.json; HTTP 503 POST /api/poker-vision/desk/sandbox/reopen; HTTP 503 GET | BY_DESIGN: Poker Vision sidecar not running in the throwaway env -> 503 / ConnectTimeout;  |
| poker-vision | phone | Калибровать и проверить | ERROR | HTTP 503 GET /api/poker-vision/overlay.json; HTTP 503 POST /api/poker-vision/calibrate; HTTP 503 GET /api/poke | BY_DESIGN: Poker Vision sidecar not running in the throwaway env -> 503 / ConnectTimeout;  |
| video-studio | phone | ＋ Новый проект | UNVERIFIED | control not found again after re-render (harness could not reach it; not counted as a product bug) | HARNESS_ARTIFACT: harness could not re-find the control after re-render/replay (not counte |
| images?studio=1 | phone | 1 | DEAD | no DOM / URL / request / dialog / toast / storage change | BY_DESIGN: hand-repro: it is the 'Количество' number input (value 1); fill 3 + ArrowUp ->  |
| images?studio=1 | phone | on | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| images?studio=1 | phone | on | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| images?studio=1 | phone | 0 | ERROR | TimeoutError: Locator.fill: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| images?studio=1 | phone | 0 | ERROR | TimeoutError: Locator.fill: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| images?studio=1 | phone | Верхняя стоимость моделей | ERROR | TimeoutError: Locator.fill: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| images?studio=1 | phone | Разрешённые CDN | ERROR | TimeoutError: Locator.fill: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| images?studio=1 | phone | (без подписи) | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| images?studio=1 | phone | (без подписи) | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: control sits inside a closed <details> (layout box exists, not visible); |
| web_designer | phone | открыть в новой вкладке | UNVERIFIED | opener/child not reproducible in place or after reload (harness could not reach it; not counted as a product b | HARNESS_ARTIFACT: harness could not re-find the control after re-render/replay (not counte |
| web_designer | phone | Применить размер | DEAD | no DOM / URL / request / dialog / toast / storage change | BY_DESIGN: hand-repro: 'Применить размер' with unchanged width/height is a no-op; with 333 |
| web_designer | phone | Лендинг Герой-блок, преимущества, цифры  | UNVERIFIED | control not found again after re-render (harness could not reach it; not counted as a product bug) | HARNESS_ARTIFACT: harness could not re-find the control after re-render/replay (not counte |
| web_designer | phone | Портфолио Сетка работ, навыки, обо мне и | UNVERIFIED | control not found again after re-render (harness could not reach it; not counted as a product bug) | HARNESS_ARTIFACT: harness could not re-find the control after re-render/replay (not counte |
| web_designer | phone | Кафе / ресторан Меню с ценами, галерея,  | UNVERIFIED | control not found again after re-render (harness could not reach it; not counted as a product bug) | HARNESS_ARTIFACT: harness could not re-find the control after re-render/replay (not counte |
| web_designer | phone | Магазин Витрина товаров с ценами, достав | UNVERIFIED | control not found again after re-render (harness could not reach it; not counted as a product bug) | HARNESS_ARTIFACT: harness could not re-find the control after re-render/replay (not counte |
| web_designer | phone | Блог Список статей, рубрики и подписка н | UNVERIFIED | control not found again after re-render (harness could not reach it; not counted as a product bug) | HARNESS_ARTIFACT: harness could not re-find the control after re-render/replay (not counte |
| web_designer | phone | Агентство / студия Услуги, кейсы, команд | UNVERIFIED | control not found again after re-render (harness could not reach it; not counted as a product bug) | HARNESS_ARTIFACT: harness could not re-find the control after re-render/replay (not counte |
| web_designer | phone | ИИ: творческий бриф (локально) Один запр | UNVERIFIED | control not found again after re-render (harness could not reach it; not counted as a product bug) | HARNESS_ARTIFACT: harness could not re-find the control after re-render/replay (not counte |
| chat.html | phone | Источники | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: earlier probe persisted the Think/Sources panel (localStorage bcc.chat.p |
| chat.html | phone | Память | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: earlier probe persisted the Think/Sources panel (localStorage bcc.chat.p |
| chat.html | phone | Прикрепить файлы (или перетащите их в ок | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: earlier probe persisted the Think/Sources panel (localStorage bcc.chat.p |
| chat.html | phone | Auto · Local-first | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. | HARNESS_ARTIFACT: earlier probe persisted the Think/Sources panel (localStorage bcc.chat.p |

## Gap closure (ux_gaps.py: hand reproductions, click-timeout retries)

- Phone viewport 390x844 swept on the installed build eba6dc592ad9 (53 pages, same script/--per-sig 2): enumerated 1274, probes 680 = OK 611 / DEAD 3 / ERROR 57 / BLOCKED_BY_DESIGN 9; DISABLED 33; UNVERIFIED 10; gated 76; sampled-not-probed 812. Raw: ux-sweep-phone-raw.json. The desktop run (ux-sweep-desktop-raw.json) is from the earlier build 803aa4d9; the two builds differ in the UI only in pages/capability_tree.js.
- DEAD x3 on desktop were hand-reproduced (ux-sweep-gaps.json): video-studio dialog 'Применить' = native required-field validation (by design); images?studio=1 '1' = the 'Количество' number input, count=4 reached POST /api/studio/jobs (by design; sweep DEAD was a harness input-only check); chat.html 'Прикрепить файлы' = opens a native file chooser (by design). Phone DEAD x3: 'Очистить ленту' (empty feed no-op), 'Применить размер' (unchanged size no-op; 333x444 resizes the preview), images '1' (same as desktop). All by design.
- The 42 desktop click timeouts (+1 'healing' fill timeout = 43 click_failed records) were retried with scroll + 15 s click, force click, mouse click, DOM click: 30 capability-tree + 8 images-studio = 38 are controls inside a CLOSED <details> (layout box but not visible; Playwright waits for visibility; after opening the summary a normal click works) -> harness artifact (enumerator now known to list them). browser 1 + motion-studio 3 + healing 1: healing, motion-studio '×' succeed on retry with a longer timeout (artifact); browser 'Обновить' = the live panel's 'Обновить скриншот' button, hand-repro: click fires GET /api/browser/sessions/<id>/screenshot (works); motion-studio 'Остановить' x2 could NOT be reproduced (the control only exists while a render job is running; with stubbed POST no job runs) -> UNRESOLVED, not counted as a bug.
- Phone click timeouts: capability-tree/images-studio same closed-<details> cause; chat.html 4: the first probe ('Источники') persisted the Think/Sources panel (localStorage bcc.chat.panel=1) in the shared browser context and the next fresh loads showed that panel full-screen over the composer; on a clean context every composer control clicks normally -> harness artifact (state leak). UX note: on a phone a persisted-open panel covers the composer after reload (it has a visible close button).
- Screenshots: one small PNG per page, desktop and phone (53 + 53) in evidence/ux-sweep/ (ux_sweep.py wrote only phone; desktop saved by ux_gaps.py --shots; sweep script bug that named chat.html's shot 'shell' fixed).
- No product bug confirmed in this pass, hence no ux-fix commit. Unresolved (not proven either way): desktop motion-studio 'Остановить' x2, desktop browser 'Обновить' (first sweep click; hand-repro of the live-panel button works), phone models 'Ollama · OpenAI-совместимый' select behind 'Добавить модель' (could not be re-located).

## Fixes (ux-fix commits)

none

## Open issues / owner decisions

- motion-studio 'Остановить' (running-job card, repolled every 2 s): not reproduced because no job runs under stubbed POST; a real run would show whether the 2 s re-render can swallow a click.
- models 'Ollama · OpenAI-совместимый' select (phone, depth 1 under 'Добавить модель'): could not be re-located by the retry; not proven either way.
- chat.html on phone: a persisted-open Think panel (bcc.chat.panel=1) covers the whole screen after reload; closable with the x button. UX judgement for the owner, not a dead control.
- Desktop sweep ran on build 803aa4d9, phone and gap retries on eba6dc592ad9 (only capability_tree.js differs in the UI); a full desktop re-run on eba6dc59 was not done.
- Sampling: first 2 identical controls per page and 4 per kind+class on big pages (capability-tree 830 controls) - 818 desktop / 812 phone controls were enumerated but not probed.
- Poker Vision: 503 when the sidecar is down is by design; UI shows toast. Not a bug.
- Text/fact checks beyond build sha vs /health/live (both builds PASS source_identity) were not done; no model/keys, chat streaming not run, so no ux zone receipts.

## Text / facts checks

- Phone run build sha eba6dc592ad9218a631c35d3d8a90e667c67968e matches /health/live build_sha and source_identity PASS.

## Full matrix (page -> control -> desktop / phone)


### shell

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Главная | button | ERROR | - | control vanished after re-render |
| Агенты | button | OK | - | url:/#/agents; 3 new controls; GET /icon.svg -> 200; GET /api/agents -> 200 |
| Ждут решения | button | OK | - | url:/#/approvals; 1 new controls; GET /icon.svg -> 200; GET /api/approvals -> 20 |
| Чат | button | OK | - | url:/#/chat; 2 new controls; GET /icon.svg -> 200; GET /pages/chat.js -> 200 |
| Дерево развития | button | OK | - | url:/#/capability-tree; 831 new controls; GET /icon.svg -> 200; GET /pages/capab |
| Операторский канал | button | OK | - | url:/#/mission_console; 10 new controls; GET /icon.svg -> 200; GET /pages/missio |
| Миссии | button | OK | - | url:/#/missions; 3 new controls; GET /pages/missions.js -> 200; GET /icon.svg -> |
| Конструктор миссий | button | OK | - | url:/#/builder; 2 new controls; GET /icon.svg -> 200; GET /pages/builder.js -> 2 |
| Цели | button | OK | - | url:/#/objectives; 3 new controls; GET /icon.svg -> 200; GET /pages/objectives.j |
| Пульт | button | OK | - | url:/#/control; 5 new controls; GET /icon.svg -> 200; GET /pages/control.js -> 2 |
| File Intelligence | button | OK | - | url:/#/file-intelligence; 2 new controls; GET /icon.svg -> 200; GET /pages/file_ |
| Video Studio | button | OK | - | url:/#/video-studio; 18 new controls; GET /icon.svg -> 200; GET /pages/video_stu |
| Веб-дизайн | button | OK | - | url:/#/web_designer; 13 new controls; GET /icon.svg -> 200; GET /pages/web_desig |
| Браузер | button | OK | - | url:/#/browser; 2 new controls; GET /icon.svg -> 200; GET /pages/browser.js -> 2 |
| Coding-сессии | button | OK | - | url:/#/coding; 4 new controls; GET /icon.svg -> 200; GET /pages/coding.js -> 200 |
| Терминал | button | OK | - | url:/#/terminal; 13 new controls; GET /pages/terminal.js -> 200; GET /icon.svg - |
| Студия | button | OK | - | url:/#/images; 17 new controls; GET /icon.svg -> 200; GET /pages/images.js -> 20 |
| Поиск в интернете | button | OK | - | url:/#/web_research; 1 new controls; GET /pages/web_research.js -> 200; GET /ico |
| Обучение трейдингу | button | OK | - | url:/#/trading_lab; 1 new controls; GET /icon.svg -> 200; GET /pages/trading_lab |
| История видео и чат | button | OK | - | url:/#/bossman-chat; toast:Файлы остаются локально. Теоретические вопросы не соз |
| Music Studio | button | OK | - | url:/#/music-studio; 13 new controls; GET /icon.svg -> 200; GET /pages/music_stu |
| Agentic Rave | button | OK | - | url:/#/rave; 14 new controls; GET /icon.svg -> 200; GET /pages/rave.js -> 200 |
| Motion Studio | button | OK | - | url:/#/motion-studio; 6 new controls; GET /pages/motion_studio.js -> 200; GET /i |
| Poker Vision | button | ERROR | - | HTTP 503 GET /api/poker-vision/capabilities |
| Приложения | button | OK | - | url:/#/apps; 26 new controls; GET /pages/apps.js -> 200; GET /icon.svg -> 200 |
| Telegram-звонки | button | OK(G) | - | url:/#/telegram_calls; toast:ТЕСТ БЕЗ TELEGRAMЭто проверка нашего тракта на тест |
| Карта агентов | button | OK | - | url:/#/agentmap; 2 new controls; GET /icon.svg -> 200; GET /pages/agentmap.js -> |
| Команды агентов | button | OK | - | url:/#/orchestras; 5 new controls; GET /icon.svg -> 200; GET /pages/orchestras.j |
| Навыки | button | OK | - | url:/#/skills; 5 new controls; GET /icon.svg -> 200; GET /pages/skills.js -> 200 |
| Выбор модели | button | OK | - | url:/#/router; 12 new controls; GET /icon.svg -> 200; GET /pages/router.js -> 20 |
| OpenRouter | button | OK | - | url:/#/openrouter; 3 new controls; GET /pages/openrouter.js -> 200; GET /icon.sv |
| Замеры моделей | button | OK | - | url:/#/benchmarks; 2 new controls; GET /icon.svg -> 200; GET /pages/benchmarks.j |
| Bossman 1.5 | button | OK | - | url:/#/v15-owner-run; 8 new controls; GET /icon.svg -> 200; GET /pages/v15_owner |
| Настройки Jeff | button | OK | - | url:/#/jeff-settings; 27 new controls; GET /icon.svg -> 200; GET /pages/jeff_set |
| Jeff · паспорта | button | OK | - | url:/#/jeff-passports; 1 new controls; GET /icon.svg -> 200; GET /pages/jeff_pas |
| Jeff · обзор | button | OK | - | url:/#/jeff-insights; 4 new controls; GET /icon.svg -> 200; GET /pages/jeff_insi |
| Ресурсы | button | OK | - | url:/#/resources; 7 new controls; GET /icon.svg -> 200; GET /pages/resources.js  |
| Присмотр | button | OK | - | url:/#/governor; 5 new controls; GET /icon.svg -> 200; GET /pages/governor.js -> |
| Восстановление | button | OK | - | url:/#/healing; 5 new controls; GET /icon.svg -> 200; GET /pages/healing.js -> 2 |
| Развилки | button | OK | - | url:/#/forks; 2 new controls; GET /icon.svg -> 200; GET /pages/forks.js -> 200 |
| Модели | button | OK | - | url:/#/models; 6 new controls; GET /icon.svg -> 200; GET /api/providers -> 200 |
| Задачи | button | OK | - | url:/#/tasks; 12 new controls; GET /icon.svg -> 200; GET /api/agents -> 200 |
| Расписания | button | OK | - | url:/#/schedules; 3 new controls; GET /icon.svg -> 200; GET /api/schedules -> 20 |
| Система | button | OK | - | url:/#/system; 1 new controls; GET /icon.svg -> 200; GET /api/system -> 200 |
| Настройки | button | OK | - | url:/#/settings; 33 new controls; POST /api/telegram/models -> 200; GET /icon.sv |
| Локальные инструменты | button | OK | - | url:/#/oss; 31 new controls; GET /icon.svg -> 200; GET /pages/oss.js -> 200 |
| Автономия | button | OK | - | url:/#/autonomy; 3 new controls; GET /icon.svg -> 200; GET /pages/autonomy.js -> |
| Команды Ctrl K | button | OK | - | opened:palette-box:Esc ДЕЙСТВИЯ Процесс работы что систем; 60 new controls; dom |
| Команды Ctrl K > Процесс работы что система делает прямо сей | button | OK | - | 2 new controls; GET /api/tasks -> 200; GET /api/activity -> 200; localStorage |
| Команды Ctrl K > Новая задача композер + агент | button | OK | - | opened:modal-wrap:Новая задача Задача Агент агентов ещё н; 4 new controls; GET / |
| Команды Ctrl K > Новый агент роль, prompt, модель | button | OK | - | opened:modal wide:Новый агент Имя Чем занимается Короткое; 10 new controls; GET  |
| Команды Ctrl K > Добавить модель провайдер → модель | button | OK | - | opened:modal wide:Новая модель 1 Провайдер 2 Модель Сущес; 8 new controls; GET / |
| Команды Ctrl K > Новое расписание once / daily / interval | button | OK | - | opened:modal wide:Новое расписание Название Задача Агент ; 8 new controls; GET / |
| Команды Ctrl K > Остановить все активные проверить и останов | button | OK(G) | - | opened:modal-wrap:Остановить все активные операции? Актив; 2 new controls; GET / |
| Команды Ctrl K > Открыть · Главная | button | OK | - | GET /api/models -> 200; GET /api/agents -> 200; GET /api/approvals -> 200; GET / |
| Команды Ctrl K > Открыть · Модели | button | OK | - | url:/#/models; 6 new controls; GET /icon.svg -> 200; GET /api/models -> 200 |
| Команды Ctrl K > Открыть · Агенты | button | OK | - | url:/#/agents; 3 new controls; GET /icon.svg -> 200; GET /api/agents -> 200 |
| Команды Ctrl K > Открыть · Задачи | button | OK | - | url:/#/tasks; 12 new controls; GET /icon.svg -> 200; GET /api/tasks -> 200 |
| Команды Ctrl K > Открыть · Расписания | button | OK | - | url:/#/schedules; 3 new controls; GET /icon.svg -> 200; GET /api/agents -> 200 |
| Команды Ctrl K > Открыть · Ждут решения | button | OK | - | url:/#/approvals; 1 new controls; GET /icon.svg -> 200; GET /api/approvals -> 20 |
| Команды Ctrl K > Открыть · Система | button | OK | - | url:/#/system; 1 new controls; GET /icon.svg -> 200; GET /api/system -> 200 |
| Команды Ctrl K > Открыть · Настройки | button | OK | - | url:/#/settings; 33 new controls; POST /api/telegram/models -> 200; GET /icon.sv |
| Отправить в GitHub | button | OK(G) | OK(G) | toast:TESTING PERIOD идёт запись действий — сессия 2c300a8909bd не; would-send:P |
| Процесс работы | button | OK | OK | localStorage; control state; dom |
| Сменить тему | button | OK | OK | localStorage; control state; dom |
| Обновить | button | OK | OK | GET /api/models -> 200; GET /api/tasks -> 200; GET /api/agents -> 200; GET /api/ |
| Главная | button | ERROR | - | control vanished after re-render |
| Операторский канал | button | OK | - | url:/#/mission_console; 10 new controls; GET /icon.svg -> 200; GET /pages/missio |
| Приложения | button | OK | - | url:/#/apps; 26 new controls; GET /icon.svg -> 200; GET /pages/apps.js -> 200 |
| Video Studio | button | OK | - | url:/#/video-studio; 18 new controls; GET /icon.svg -> 200; GET /pages/video_stu |
| Веб-дизайн | button | OK | - | url:/#/web_designer; 13 new controls; GET /icon.svg -> 200; GET /pages/web_desig |
| Пульт | button | OK | - | url:/#/control; 5 new controls; GET /icon.svg -> 200; GET /pages/control.js -> 2 |

### home

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Что должен сделать BOSSMAN? | text | OK | OK | field value |
| агентов ещё нет | select | OK | OK | select has a single option (''); nothing to change |
| Запустить | button | OK(G) | OK(G) | toast:Опишите задачу; 1 new controls; dom |
| Запустить > × | button | OK | OK | control state; dom |
| Все задачи | button | OK | OK | url:/#/tasks; 12 new controls; GET /icon.svg -> 200; GET /api/agents -> 200 |
| Реестр | button | OK | OK | url:/#/models; 6 new controls; GET /icon.svg -> 200; GET /api/models -> 200 |
| Очередь | button | OK | OK | url:/#/approvals; 1 new controls; GET /icon.svg -> 200; GET /api/approvals -> 20 |
| Метрики | button | OK | OK | url:/#/system; 1 new controls; GET /icon.svg -> 200; GET /api/system -> 200 |

### models

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Проверить все | button | DISABLED | DISABLED | explains: Проверять нечего: моделей ещё нет |
| Найти локальные | button | OK | OK | opened:modal wide:Локальные модели ЗАПУЩЕННЫЕ ENDPOINT'Ы ; 13 new controls; POST |
| Найти локальные > Закрыть | button | OK | OK | control state; dom |
| Найти локальные > Добавить | button | OK | OK | toast:Модель «ace-qwen3-17b:latest» добавлена; 6 new controls; POST /api/provide |
| Найти локальные > Закрыть | button | OK | OK | control state; dom(near control) |
| Бесплатные облака | button | OK | OK | opened:modal wide:Бесплатные облачные модели Разгрузка Op; 11 new controls; GET  |
| Бесплатные облака > Закрыть | button | OK | OK | field value; control state; dom |
| Бесплатные облака > Подключить | button | OK | OK | toast:Вставьте ключ; 1 new controls; dom |
| Бесплатные облака > получить ключ | link | OK(G) | OK(G) | external link https://build.nvidia.com/explore/discover present, not followed (g |
| Бесплатные облака > получить ключ | link | OK(G) | OK(G) | external link https://console.groq.com/keys present, not followed (gated) |
| Бесплатные облака > получить ключ | link | OK(G) | OK(G) | external link https://aistudio.google.com/apikey present, not followed (gated) |
| Бесплатные облака > Закрыть | button | OK | OK | field value; control state; dom |
| Добавить модель | button | OK | OK | opened:modal wide:Новая модель 1 Провайдер 2 Модель Сущес; 6 new controls; GET / |
| Добавить модель > Закрыть | button | OK | OK | field value; control state; dom |
| Добавить модель > Существующий | button | OK | - | control state; dom |
| Добавить модель > Новый провайдер | button | OK | OK | 6 new controls; field value; control state; dom |
| Добавить модель > Ollama · OpenAI-совместимый · http://127.0 | select | OK | ERROR | select has a single option ('1'); nothing to change |
| Добавить модель > Отмена | button | OK | OK | field value; control state; dom |
| Добавить модель > Далее | button | OK | OK | opened:modal wide:Новая модель 1 Провайдер 2 Модель Имя у; 11 new controls; fiel |
| Добавить модель | button | ERROR | UNVERIFIED | control vanished after re-render |

### agents

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Новый агент | button | OK | OK | opened:modal wide:Новый агент Имя Чем занимается Короткое; 11 new controls; fiel |
| Новый агент > Закрыть | button | OK | OK | field value; control state; dom |
| Новый агент > ace-qwen3-17b:latest | select | OK | OK | field value |
| Новый агент > — без запасной — ace-qwen3-17b:latest | select | OK | OK | field value |
| Новый агент > on | toggle | OK | OK | field value |
| Новый агент > Отмена | button | OK | OK | field value; control state; dom |
| Новый агент > Создать | button | OK | OK | toast:Укажите имя агента; 1 new controls; dom |
| Создать агента | button | OK | OK | opened:modal wide:Новый агент Имя Чем занимается Короткое; 11 new controls; fiel |
| Создать агента > Закрыть | button | OK | OK | field value; control state; dom |
| Создать агента > ace-qwen3-17b:latest | select | OK | OK | field value |
| Создать агента > — без запасной — ace-qwen3-17b:latest | select | OK | OK | field value |
| Создать агента > on | toggle | OK | OK | field value |
| Создать агента > Отмена | button | OK | OK | field value; control state; dom |
| Создать агента > Создать | button | OK | OK | toast:Укажите имя агента; 1 new controls; dom |

### tasks

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Что должен сделать BOSSMAN? | text | OK | OK | field value |
| агентов ещё нет | select | OK | OK | select has a single option (''); nothing to change |
| Высокий приоритет Обычный приоритет Низкий приоритет | select | OK | OK | field value |
| По расписанию… | button | OK | OK | toast:Опишите задачу; 1 new controls; dom(near control) |
| По расписанию… > × | button | OK | OK | control state; dom |
| Запустить | button | OK(G) | OK(G) | toast:Опишите задачу; 1 new controls; dom |
| Запустить > × | button | OK | OK | control state; dom |
| Все 0 | button | OK | - | GET /api/tasks -> 200; GET /api/agents -> 200; control state; dom |
| Выполняются 0 | button | OK | OK | 2 new controls; GET /api/agents -> 200; GET /api/tasks -> 200; control state |
| Выполняются 0 > Все 0 | button | OK | - | 2 new controls; GET /api/agents -> 200; GET /api/tasks -> 200; control state |
| Выполняются 0 > Выполняются 0 | button | OK | - | GET /api/agents -> 200; GET /api/tasks -> 200; control state; dom |
| В очереди 0 | button | OK | OK | 2 new controls; GET /api/agents -> 200; GET /api/tasks -> 200; control state |
| В очереди 0 > Все 0 | button | OK | - | 2 new controls; GET /api/agents -> 200; GET /api/tasks -> 200; control state |
| В очереди 0 > В очереди 0 | button | OK | - | GET /api/tasks -> 200; GET /api/agents -> 200; control state; dom |
| Ждут подтверждения 0 | button | OK | OK | 2 new controls; GET /api/tasks -> 200; GET /api/agents -> 200; control state |
| Ждут подтверждения 0 > Все 0 | button | OK | - | 2 new controls; GET /api/agents -> 200; GET /api/tasks -> 200; control state |
| Ждут подтверждения 0 > Ждут подтверждения 0 | button | OK | - | GET /api/tasks -> 200; GET /api/agents -> 200; control state; dom |
| На паузе 0 | button | OK | OK | 2 new controls; GET /api/agents -> 200; GET /api/tasks -> 200; control state |
| На паузе 0 > Все 0 | button | OK | - | 2 new controls; GET /api/tasks -> 200; GET /api/agents -> 200; control state |
| На паузе 0 > На паузе 0 | button | OK | - | GET /api/agents -> 200; GET /api/tasks -> 200; control state; dom |
| Заблокированы 0 | button | OK | OK | 2 new controls; GET /api/agents -> 200; GET /api/tasks -> 200; control state |
| Заблокированы 0 > Все 0 | button | OK | - | 2 new controls; GET /api/tasks -> 200; GET /api/agents -> 200; control state |
| Заблокированы 0 > Заблокированы 0 | button | OK | - | GET /api/agents -> 200; GET /api/tasks -> 200; control state; dom |
| Ошибки 0 | button | OK | OK | 2 new controls; GET /api/agents -> 200; GET /api/tasks -> 200; control state |
| Ошибки 0 > Все 0 | button | OK | - | 2 new controls; GET /api/agents -> 200; GET /api/tasks -> 200; control state |
| Ошибки 0 > Ошибки 0 | button | OK | - | GET /api/agents -> 200; GET /api/tasks -> 200; control state; dom |
| Завершены 0 | button | OK | OK | 2 new controls; GET /api/tasks -> 200; GET /api/agents -> 200; control state |
| Завершены 0 > Все 0 | button | OK | - | 2 new controls; GET /api/agents -> 200; GET /api/tasks -> 200; control state |
| Завершены 0 > Завершены 0 | button | OK | - | GET /api/tasks -> 200; GET /api/agents -> 200; control state; dom |
| Остановлены 0 | button | OK(G) | OK(G) | 2 new controls; GET /api/tasks -> 200; GET /api/agents -> 200; control state |
| Остановлены 0 > Все 0 | button | OK | - | 2 new controls; GET /api/tasks -> 200; GET /api/agents -> 200; control state |
| Остановлены 0 > Остановлены 0 | button | OK(G) | - | GET /api/agents -> 200; GET /api/tasks -> 200; control state; dom |

### schedules

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Новое расписание | button | OK | OK | opened:modal wide:Новое расписание Название Задача Агент ; 10 new controls; GET  |
| Новое расписание > Закрыть | button | OK | OK | field value; control state; dom |
| Новое расписание > агентов ещё нет | select | OK | OK | select has a single option (''); nothing to change |
| Новое расписание > Разово | button | OK | OK | 3 new controls; field value; control state; dom |
| Новое расписание > Ежедневно | button | OK | - | dom |
| Новое расписание > Интервал | button | OK | OK | 3 new controls; field value; control state; dom |
| Новое расписание > Отмена | button | OK | OK | field value; control state; dom |
| Новое расписание > Создать | button | OK | OK | toast:Укажите название; 1 new controls; dom |
| Новое расписание | button | OK | OK | opened:modal wide:Новое расписание Название Задача Агент ; 10 new controls; GET  |

### settings

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Тёмная | button | OK | - | POST /api/telegram/models -> 200; POST /api/telegram/models -> 200; GET /api/pro |
| Светлая | button | OK | OK | 2 new controls; POST /api/telegram/models -> 200; POST /api/telegram/models -> 2 |
| Светлая > Тёмная | button | ERROR | - | opener not found on replay |
| Светлая > Светлая | button | ERROR | - | opener not found on replay |
| Выйти | button | OK(G) | OK(G) | toast:TESTING PERIOD     идёт запись действий — сессия 2c300a8909b; 2 new contro |
| Выйти > Войти | button | OK | OK | dom |
| tg-enabled | toggle | OK | OK | field value |
| Токен бота | text | OK | OK | field value |
| Ваш Telegram ID (владелец) | text | OK | OK | field value |
| Гости (Telegram ID) | text | OK | OK | field value |
| — нажмите «Проверить модели» — | select | OK | OK | select has a single option (''); nothing to change |
| — не использовать — | select | OK | OK | select has a single option (''); nothing to change |
| Лучшая (самая умная) Самая быстрая | select | OK | OK | field value |
| Ожидание ответа лучшей модели, сек | text | OK | OK | field value |
| Длина ответа, токенов | text | OK | OK | field value; scroll |
| Автоматически (модель, которая видит изображения) Лучшая Сам | select | OK | OK | field value; scroll |
| tg-fallback | toggle | OK | OK | field value; scroll |
| tg-delegation | toggle | DISABLED | DISABLED | disabled without any reason shown |
| Манера общения | text | OK | OK | field value; scroll |
| Хранить журнал, дней | text | OK | OK | field value; scroll |
| tg-priority | toggle | OK | OK | field value; scroll |
| Экспорт для обучения (JSONL) | button | OK | OK | toast:Экспортировано записей: 0; 1 new controls; POST /api/telegram/export -> 20 |
| Экспорт для обучения (JSONL) > × | button | ERROR | OK | control vanished after re-render |
| tg-img-enabled | toggle | OK | OK | field value; scroll |
| Z-Image-Turbo (sd.cpp, локально) FLUX.1-schnell (sd.cpp, лок | select | OK | OK | field value; scroll |
| 512×512 — быстро 768×768 1024×1024 — качество | select | OK | OK | field value; scroll |
| 4 шага — черновик 8 шагов — обычно 12 шагов — тщательно | select | OK | OK | field value; scroll |
| tg-img-guests | toggle | OK | OK | field value; scroll |
| Сохранить | button | OK | OK | toast:Выберите лучшую модель (нажмите «Проверить модели»); 1 new controls; dom(n |
| Сохранить > × | button | OK | OK | control state; dom |
| Проверить модели | button | OK | OK | 1 new controls; POST /api/telegram/models -> 200; POST /api/telegram/models -> 2 |
| Проверить модели > Проверить модели | button | DISABLED | - | explains: Проверить модели |
| Проверить бота | button | BLOCKED_BY_DESIGN | BLOCKED_BY_DESIGN | refused with visible feedback: HTTP 409 POST /api/telegram/test |
| Проверить бота > × | button | OK | OK | control state; dom |
| Команды бота в меню Telegram | button | BLOCKED_BY_DESIGN | BLOCKED_BY_DESIGN | refused with visible feedback: HTTP 409 POST /api/telegram/commands |
| Команды бота в меню Telegram > × | button | OK | OK | POST /api/telegram/models -> 200; control state; dom |
| Старт | button | BLOCKED_BY_DESIGN | BLOCKED_BY_DESIGN | refused with visible feedback: HTTP 409 POST /api/telegram/start |
| Старт > × | button | OK | OK | POST /api/telegram/models -> 200; control state; dom |
| Стоп | button | OK | OK | POST /api/telegram/stop -> 200; dom; scroll |
| Обновить статус | button | OK | OK | GET /api/telegram/status -> 200; scroll |
| К моделям | button | OK | OK | url:/#/models; 10 new controls; GET /icon.svg -> 200; GET /api/models -> 200 |
| (без подписи) | button | OK | OK | opened:modal-wrap:Удалить провайдера? «Ollama» и его ключ; 3 new controls; dom;  |
| (без подписи) > Закрыть | button | OK | OK | control state; dom |
| (без подписи) > Отмена | button | OK | OK | control state; dom |
| (без подписи) > Удалить | button | OK(G) | OK(G) | toast:Провайдер удалён; 19 new controls; DELETE /api/providers/1 -> 200; POST /a |

### capability-tree

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Настройки Jeff, участники, presentation profile; единую рабо | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Существующий runtime, identity, vault, guard и participant p | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| 37 минут с фейковыми Telegram/моделью: 8/8, 0 ложных рестарт | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Голосовые маршруты и speech; качество реальной интонации и l | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| В отчёте 1 октября описана генерация и рассылка; полный набо | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| В отчёте: 7/7 доставлено с owner approval. | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | same label+class as instance #2; first 2 probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | same label+class as instance #2; first 2 probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | same label+class as instance #2; first 2 probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | same label+class as instance #2; first 2 probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | same label+class as instance #2; first 2 probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | same label+class as instance #2; first 2 probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Bounded, local Russian speech synthesis for existing Bossman | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Optional offline Russian voice clone using a separate, bound | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Offline speech recognition using the optional upstream faste | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Voice (V2.6, раздел 20) — слой provider-capability, и только | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Реализация и тесты присутствуют; drawer должен показывать эт | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Каталог argparse ниже: объявления подкоманд, не доказанные з | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| список команд; команда зарегистрирована, живой запуск отдель | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| подключение, сборка, модель, агент, режим, бюджеты; команда  | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| последние задачи Bossman; команда зарегистрирована, живой за | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| модели; use — сменить модель текущего агента; команда зареги | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| агенты; с аргументом — выбрать; команда зарегистрирована, жи | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| навыки; с запросом — какие подошли бы к задаче; команда заре | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| инструменты текущего агента и их политика; команда зарегистр | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| поиск по памяти и фактам; команда зарегистрирована, живой за | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| diff последней coding-задачи; команда зарегистрирована, живо | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| coding task через coding path; команда зарегистрирована, жив | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Agentic Rave: несколько агентов на один prompt; команда заре | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| одобрить ожидающее разрешение; команда зарегистрирована, жив | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| отклонить ожидающее разрешение; команда зарегистрирована, жи | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| ожидающие разрешения; команда зарегистрирована, живой запуск | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| пауза задачи; команда зарегистрирована, живой запуск отдельн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| остановить задачу; all — глобальный STOP; команда зарегистри | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| продолжить задачу после паузы; команда зарегистрирована, жив | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| управление компьютером; команда зарегистрирована, живой запу | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| цикл самоулучшения 1.1; команда зарегистрирована, живой запу | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| ключи облачных моделей; команда зарегистрирована, живой запу | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| панель контекста: workspace, задача, инструменты, память, бю | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| сжать беседу в резюме (задачей Bossman); дальше — резюме + н | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| что уйдёт со следующим сообщением: резюме, ходы, размер; ком | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| токены и стоимость задач этой сессии (из Bossman); команда з | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| сохранить беседу в Markdown; команда зарегистрирована, живой | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| проверка: сборка, данные, агент, coding path, память, компью | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| развернуть свёрнутый блок (мысли модели, вывод инструмента); | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| история ввода: показать состояние / включить / выключить; ко | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| очистить экран; команда зарегистрирована, живой запуск отдел | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| выйти (задачи продолжают работу в Bossman); команда зарегист | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| (без подписи) | button | SAMPLED | SAMPLED | same label+class as instance #2; first 2 probed |
| Owner journeys и бизнес-сценарии существуют; реальные учётки | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Файлы приложения присутствуют; готовность владельцу проверяе | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Файлы приложения присутствуют; готовность владельцу проверяе | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Файлы приложения присутствуют; готовность владельцу проверяе | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Файлы приложения присутствуют; готовность владельцу проверяе | button | SAMPLED | - | same label+class as instance #2; first 2 probed |
| Файлы приложения присутствуют; готовность владельцу проверяе | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Файлы приложения присутствуют; готовность владельцу проверяе | button | SAMPLED | - | same label+class as instance #2; first 2 probed |
| Файлы приложения присутствуют; готовность владельцу проверяе | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Earning EMULATOR: reads a public remote-job listing (text),  | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| CLI for the earning emulator (bcc/earning_emulator.py). SIMU | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| (без подписи) | button | SAMPLED | SAMPLED | same label+class as instance #2; first 2 probed |
| 82,307% против 72,213% в сохранённом финале. Не внедрён; run | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Один диагностический сценарий прошёл; полного турнира и work | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| (без подписи) | button | SAMPLED | SAMPLED | same label+class as instance #2; first 2 probed |
| Пресеты и governor есть; free tier зависит от аккаунта, квот | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Адаптеры, маршрутизация и настройки; наличие адаптера не док | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| cache_intel, context budget, coding limit saver; экономия до | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Проверено вживую на указанном коммите; полная | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Free preset в коде. Доступ, квоты и работоспособность аккаун | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Free preset в коде. Доступ, квоты и работоспособность аккаун | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Free preset в коде. Доступ, квоты и работоспособность аккаун | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| (без подписи) | button | SAMPLED | SAMPLED | same label+class as instance #2; first 2 probed |
| Browser runtime, help, tools и action gates. Прогон 2026-10- | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Адаптер и bounded operator; не доказано тотальное управление | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| (без подписи) | button | SAMPLED | SAMPLED | same label+class as instance #2; first 2 probed |
| Редактор, задания и экспорт имеют код; проверять конечный ме | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модули студий и review; нуждаются в backend/model runtime дл | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Файлы приложения присутствуют; готовность владельцу проверяе | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Higgsfield Genjutsu: перенос движения из видео на нового пер | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Инструкция агенту: заменить актёров короткого клипа людьми п | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Отчёт о переделке главной страницы из панели разработчика в  | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Итоговый отчёт по второй волне UX и документ приёмки интерфе | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| One deterministic filtergraph compiler for preview and expor | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Video Studio host integration: canonical DB, tasks, commands | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Раздел 10: раскадровка и рецепты кадров для Video Studio. От | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Existing BCC provider transport -> untrusted model draft ->  | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Local editable Shotcut/MLT exchange with an explicit, checke | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| OpenTimelineIO interchange, with explicit preservation/loss  | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Local deterministic diagnostics, timed captions, and optiona | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Evidence-linked local retrieval, B-roll candidates and measu | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Train a separate LoRA command specialist and compare a fixed | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Generation observations, never task-completion evidence. No  | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Local AI generation through stable-diffusion.cpp (Vulkan) —  | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Local Studio wrapper over the existing native ComfyUI implem | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| OpenRouter image/video shapes already used by repository scr | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Persistent per-feature reservations under the existing Gover | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Bossman Vision: a local look at every generated Studio video | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Bounded local ComfyUI adapter using upstream /prompt, /histo | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Генератор сайтов визуального веб-дизайнера: шаблоны, палитры | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| GrapesJS edits the body; Bossman retains the document, versi | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Local AI website creation through the canonical registry and | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| The job pipeline: explicit stages from request to a printabl | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| G-code safety scanner. Generated G-code is executable machin | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Преобразование медиа через ffmpeg. Исходник не трогается ник | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Offline neon trailer renderer for validated Motion Studio sp | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Local Genjutsu test stack: character swap (Object Swap) and  | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| (без подписи) | button | SAMPLED | SAMPLED | same label+class as instance #2; first 2 probed |
| Память, контекстные бюджеты, evidence graph, vault и retriev | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| failure_to_case, failure memory, repair/evolution; сохранени | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Аудит 3 октября NOT READY. 154 passed / 2 skipped; три live- | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| AMD/Windows путь не доказан; описанный CUDA training не счит | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | same label+class as instance #2; first 2 probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | same label+class as instance #2; first 2 probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| (без подписи) | button | SAMPLED | SAMPLED | same label+class as instance #2; first 2 probed |
| Многоагентные сессии и STOP; файловая изоляция и интегратор  | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| CLI/coding sessions существуют; subscription login и общий с | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Durable state, task exchange, mission lifecycle, checkpoint  | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Control plane, provider fleet, economy swarm, remote nodes;  | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Граф строится из БД агентов/оркестров и живых run'ов. Код в  | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Шесть сохранённых стратегий над одним sidecar; честное сравн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| (без подписи) | button | SAMPLED | SAMPLED | same label+class as instance #2; first 2 probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Код, который реально находит, дедуплицирует и оценивает SKIL | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| (без подписи) | button | SAMPLED | SAMPLED | same label+class as instance #2; first 2 probed |
| Capability manifest; scope: network.read. Проверено вживую н | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Capability manifest; scope: network.read. Проверено вживую н | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Capability manifest; scope: db.read. Проверено вживую на ука | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Capability manifest; scope: vault.read. Проверено вживую на  | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Capability manifest; scope: vault.write. Проверено вживую на | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Capability manifest; scope: mcp.read. Проверено вживую на ук | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Capability manifest; scope: mcp.execute. Авторизация/live эф | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Capability manifest; scope: llm.local. Проверено вживую на у | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Capability manifest; scope: llm.cloud.use. Проверено вживую  | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Capability manifest; scope: repo:read. Проверено вживую на у | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Capability manifest; scope: issues:write. Авторизация/live э | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Capability manifest; scope: gmail.readonly. Авторизация/live | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Capability manifest; scope: gmail.send. Авторизация/live эфф | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Capability manifest; scope: calendar.readonly. Авторизация/l | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Capability manifest; scope: calendar.events. Авторизация/liv | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Capability manifest; scope: drive.readonly. Авторизация/live | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Capability manifest; scope: drive.file. Авторизация/live эфф | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Capability manifest; scope: telegram.read. Авторизация/live  | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Capability manifest; scope: telegram.send. Авторизация/live  | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Capability manifest; scope: n8n.read. Авторизация/live эффек | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Capability manifest; scope: n8n.execute. Авторизация/live эф | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Capability manifest; scope: browser.navigate. Авторизация/li | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Capability manifest; scope: browser.input. Авторизация/live  | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Подключение MCP-серверов к общему реестру инструментов и раз | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Проверка scope/approval для каждого capability manifest. Код | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Опциональные локальные движки (whisper, UI-TARS) и их наличи | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| (без подписи) | button | SAMPLED | SAMPLED | same label+class as instance #2; first 2 probed |
| Сохранённый audit 2 октября: NO_GO. Нужен единый итоговый SH | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| 4 октября изолированные сценарии описаны как PASS, но после  | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Исторические CI и тестовые отчёты не переносятся на новый SH | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Budget, resources, offline, approvals, provenance, secrets v | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Семантика и live работоспособность не сертифи | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Семантика и live работоспособность не сертифи | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Семантика и live работоспособность не сертифи | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Семантика и live работоспособность не сертифи | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Семантика и live работоспособность не сертифи | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Семантика и live работоспособность не сертифи | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Семантика и live работоспособность не сертифи | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Семантика и live работоспособность не сертифи | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Семантика и live работоспособность не сертифи | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Семантика и live работоспособность не сертифи | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Семантика и live работоспособность не сертифи | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Семантика и live работоспособность не сертифи | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| GitHub check 2026-10-05T11:38:52Z; claude/bossman-control-v0 | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| GitHub check 2026-10-04T14:35:13Z; ux/ai-3d-maker-adapter-20 | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| GitHub check 2026-10-04T14:35:13Z; ux/ai-3d-maker-adapter-20 | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| GitHub check 2026-10-04T14:35:13Z; ux/ai-3d-maker-adapter-20 | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| GitHub check 2026-10-04T14:35:13Z; ux/ai-3d-maker-adapter-20 | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Priority queue for verifying the blue («code written») leave | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Sync the capability tree with the repository: add leaves for | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Per-leaf audit of the «code written» (blue) leaves of the ca | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| (без подписи) | button | SAMPLED | SAMPLED | same label+class as instance #2; first 2 probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| MIT; handoffs/guardrails/tracing — эталон для nl_orchestra и | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| MIT; durable graph + checkpoint/interrupt — сверить с миссия | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| MIT; типизированные tool-вызовы и eval-хуки — образец для ко | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Роли/процессы для fleet и swarm; только как UX-референс. Ссы | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| MIT; официальный MCP SDK (pypi: mcp) — опора для mcp_runtime | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Apache-2.0; быстрые MCP-серверы для своих коннекторов Bossma | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Справочные MCP-серверы (filesystem, git, fetch) для теста pl | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Apache-2.0; OAuth-коннекторы к сотням сервисов — только с од | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| MIT; надёжные долгие воркфлоу — референс для recovery миссий | button | SAMPLED(G) | - | big page (830 controls): first 4 per kind+class probed |
| Открытый (MIT) визуальный конструктор агентов на TypeScript; | button | SAMPLED | - | big page (830 controls): first 4 per kind+class probed |
| Проверить репозиторий без ИИ | button | BLOCKED_BY_DESIGN | BLOCKED_BY_DESIGN | refused with visible feedback: HTTP 422 POST /api/capability-tree/scan |
| Проверить репозиторий без ИИ > × | button | OK | OK | control state; dom |
| Пауза | button | OK | OK | toast:Команда передана циклу; 1 new controls; POST /api/evolution/pause -> 200;  |
| Пауза > × | button | OK | OK | control state; dom |
| STOP | button | OK(G) | OK(G) | toast:Команда передана циклу; 1 new controls; POST /api/evolution/stop -> 200; G |
| STOP > × | button | OK | OK | control state; dom |
| docs/owner/CURRENT_CLOSEOUT_AUDIT_20261002.md | link | OK(G) | OK(G) | external link https://github.com/molotroka123-cell/AiMaxBossman/blob/0ec2ff5292c |
| Что уже есть, что сейчас делаем, что проверить… | text | OK | OK | GET /api/evolution/status -> 200; field value; scroll |
| заметка сейчас делаем заблокировано собрано (не является PAS | select | OK | OK | GET /api/evolution/status -> 200; field value; scroll |
| Сохранить в Bossman | button | OK | OK | toast:Запись сохранена; 1 new controls; POST /api/capability-tree/note -> 200; G |
| Сохранить в Bossman > × | button | OK | OK | control state; dom |
| Локальная модель Bossman (бесплатно, на ПК) OpenRouter · Nem | select | OK | OK | GET /api/evolution/status -> 200; field value; scroll |
| Что именно улучшить в этой зоне (необязательно)… | text | OK | OK | GET /api/evolution/status -> 200; field value; scroll |
| 🚀 Bossman, работай здесь | button | BLOCKED_BY_DESIGN | BLOCKED_BY_DESIGN | refused with visible feedback: HTTP 422 POST /api/capability-tree/work |
| 🚀 Bossman, работай здесь > × | button | OK | OK | control state; dom |
| Список всех узлов (поиск и клавиатура) | summary | OK | OK | 819 new controls; GET /api/evolution/status -> 200; dom; scroll |
| Список всех узлов (поиск и кла > Jeff · собеседник | button | OK | OK | dom |
| Список всех узлов (поиск и кла > Диалог и ответы | button | OK | - | dom |
| Список всех узлов (поиск и кла > Память участника и согласие | button | OK(G) | - | dom; scroll |
| Список всех узлов (поиск и кла > Паспорта и master parser | button | OK | - | dom; scroll |
| Список всех узлов (поиск и кла > Настроение и персона | button | OK | - | dom; scroll |
| Список всех узлов (поиск и кла > Admin Panel | button | OK(G) | - | dom; scroll |
| Список всех узлов (поиск и кла > Telegram polling и изоляция | button | OK | - | dom; scroll |
| Список всех узлов (поиск и кла > Heartbeat · watchdog · rest | button | OK(G) | - | dom; scroll |
| Список всех узлов (поиск и кла > Голос · STT · TTS | button | OK | - | dom; scroll |
| Список всех узлов (поиск и кла > Фото · генерация · редактир | button | OK | - | dom; scroll |
| Список всех узлов (поиск и кла > Рассылка | button | OK | - | dom; scroll |
| Список всех узлов (поиск и кла > Звонки | button | OK(G) | - | dom; scroll |
| Список всех узлов (поиск и кла > 24–48 часов непрерывной раб | button | OK(G) | - | dom; scroll |
| Список всех узлов (поиск и кла > pit/behavior_controller.py | button | OK | - | dom; scroll |
| (без подписи) | button | ERROR | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Живой ответ 7,3 с описан 1 октября; непрерывность сегодня не | button | ERROR | - | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| /memory, /pause_memory, /forget; приватные training.jsonl и  | button | ERROR(G) | - | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Факты, источники, уверенность, checkpoint и consent; не дока | button | ERROR | - | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| participant/companion profile, behavior scores, roleplay pol | button | ERROR | - | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Код, тесты и отдельные ветки есть; реальный двусторонний зво | button | ERROR(G) | - | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| В октябрьском аудите soak NOT RUN; заявления о 100% усилении | button | ERROR(G) | - | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Найден исходный модуль. Наличие тестов и живой результат про | button | ERROR | - | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Найден исходный модуль. Наличие тестов и живой результат про | button | ERROR | - | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| (без подписи) | button | ERROR | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Результат не доказан; прежний cold page прогон показал регре | button | ERROR | - | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Файлы приложения присутствуют; готовность владельцу проверяе | button | ERROR | - | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Файлы приложения присутствуют; готовность владельцу проверяе | button | ERROR | - | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Распознавание покерного стола по пикселям → проверенное сост | button | ERROR | - | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| По сохранённому отчёту 4 октября; текущую конфигурацию ПК зд | button | ERROR | - | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Pi не заменил PRIMARY; Ornith участвовал в коротком раунде.  | button | ERROR(G) | - | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| В отчёте встречается resident community uncensored Q8; не см | button | ERROR | - | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Низкий приоритет; память ограничена. Не включать в обязатель | button | ERROR | - | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Частичная telemetry есть; единый точный дневной отчёт, включ | button | ERROR | - | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Observe описан; действие WAIT_APPROVAL. Случай ложного сообщ | button | ERROR | - | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Предложенный ограниченный UX sidecar; не заменяет ядро. | button | ERROR | - | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Pipeline и документы есть; непрерывный перенос обучения в вы | button | ERROR | - | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Каталог кандидатов, не установленные работающие приложения. | button | ERROR(G) | - | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | ERROR | - | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Scene spec -> finished video: voice-over, original score, de | button | ERROR | - | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Render the Bossman "32 days" promo (tools/promo_video/promo. | button | ERROR | - | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| JSON карта подготовлена для загрузки. В runtime Bossman ещё  | button | ERROR | - | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Найден исходный модуль. Наличие тестов и живой результат про | button | ERROR | - | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Модуль найден. Семантика и live работоспособность не сертифи | button | ERROR | - | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Модуль найден. Семантика и live работоспособность не сертифи | button | ERROR(G) | - | TimeoutError: Locator.click: Timeout 4000ms exceeded. |

### poker-vision

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| (page render) | page | ERROR | ERROR | GET /api/poker-vision/capabilities -> 503 |
| Запустить сервис | button | OK(G) | OK(G) | toast:Сервис запущен.; POST /api/poker-vision/service/start -> 200; would-send:P |
| STOP | button | OK(G) | OK(G) | toast:STOP; 2 new controls; POST /api/poker-vision/stop -> 200; would-send:POST  |
| STOP > STOP | button | ERROR(G) | - | control vanished after re-render |
| STOP > × | button | ERROR | ERROR | control vanished after re-render |
| Выбрать источник | button | ERROR | ERROR | console: ApiError: сервис Poker Vision не отвечает: ConnectTimeout     at rawReq |
| Сменить источник | button | ERROR | ERROR | console: ApiError: сервис Poker Vision не отвечает: ConnectTimeout     at rawReq |
| Пауза | button | ERROR | ERROR | console: ApiError: сервис Poker Vision не отвечает: ConnectTimeout     at rawReq |
| Продолжить | button | ERROR | ERROR | console: ApiError: сервис Poker Vision не отвечает: ConnectTimeout     at rawReq |
| Наблюдение | button | DISABLED | DISABLED | disabled without any reason shown |
| Подсказки | button | DISABLED | DISABLED | disabled without any reason shown |
| Управление | button | DISABLED | DISABLED | disabled without any reason shown |
| on | toggle | OK(G) | OK(G) | field value |
| on | toggle | OK | OK | field value |
| Сдвинуть | button | ERROR | ERROR | HTTP 503 GET /api/poker-vision/overlay.json; HTTP 503 POST /api/poker-vision/des |
| Вернуть | button | ERROR | ERROR | HTTP 503 GET /api/poker-vision/overlay.json; HTTP 503 POST /api/poker-vision/des |
| Размер 420×760 | button | ERROR | ERROR | HTTP 503 GET /api/poker-vision/overlay.json; HTTP 503 POST /api/poker-vision/des |
| Размер 520×900 | button | ERROR | ERROR | HTTP 503 GET /api/poker-vision/overlay.json; HTTP 503 POST /api/poker-vision/des |
| Перекрыть | button | ERROR | ERROR | HTTP 503 GET /api/poker-vision/overlay.json; HTTP 503 POST /api/poker-vision/des |
| Убрать перекрытие | button | OK(G) | OK(G) | toast:Тестовое окно: uncover → ок; POST /api/poker-vision/desk/sandbox/uncover - |
| Свернуть | button | ERROR | ERROR | HTTP 503 GET /api/poker-vision/overlay.json; HTTP 503 POST /api/poker-vision/des |
| Развернуть | button | OK(G) | OK(G) | toast:Тестовое окно: minimize → ок; POST /api/poker-vision/desk/sandbox/minimize |
| Закрыть | button | ERROR | ERROR | HTTP 503 GET /api/poker-vision/overlay.json; HTTP 503 POST /api/poker-vision/des |
| Открыть снова | button | ERROR | ERROR | HTTP 503 GET /api/poker-vision/overlay.json; HTTP 503 POST /api/poker-vision/des |
| папка размеченных кадров (images + truth.json) | text | OK | OK | field value; scroll |
| папка ОТЛОЖЕННЫХ кадров (другие раздачи) | text | OK | OK | field value; scroll |
| {"pot":[0.40,0.30,0.20,0.08],"hero_cards":[x,y,w,h],"board": | text | OK | OK | field value; scroll |
| Калибровать и проверить | button | ERROR | ERROR | HTTP 503 GET /api/poker-vision/overlay.json; HTTP 503 POST /api/poker-vision/cal |

### oss

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Открыть | link | OK | OK | url:/#/models; 10 new controls; GET /icon.svg -> 200; GET /api/providers -> 200 |
| Исходный код | link | OK(G) | OK(G) | external link https://github.com/ggml-org/llama.cpp present, not followed (gated |
| Открыть | link | OK | OK | url:/#/file-intelligence; 3 new controls; GET /icon.svg -> 200; GET /pages/file_ |
| Исходный код | link | OK(G) | OK(G) | external link https://github.com/docling-project/docling present, not followed ( |
| Исходный код | link | OK(G) | OK(G) | external link https://github.com/qdrant/qdrant present, not followed (gated) |
| Исходный код | link | OK(G) | OK(G) | external link https://github.com/SYSTRAN/faster-whisper present, not followed (g |
| Открыть | link | OK | OK | url:/#/web_research; 2 new controls; GET /pages/web_research.js -> 200; GET /ico |
| Исходный код | link | OK(G) | OK(G) | external link https://github.com/searxng/searxng present, not followed (gated) |
| Открыть | link | OK | OK | url:/#/images; 19 new controls; GET /icon.svg -> 200; GET /pages/images.js -> 20 |
| Исходный код | link | OK(G) | OK(G) | external link https://github.com/Comfy-Org/ComfyUI present, not followed (gated) |
| Открыть | link | OK | OK | url:/#/control; 6 new controls; GET /icon.svg -> 200; GET /pages/control.js -> 2 |
| Исходный код | link | OK(G) | OK(G) | external link https://github.com/bytedance/UI-TARS-desktop present, not followed |
| Открыть | link | OK | OK | url:/#/web_designer; 14 new controls; GET /icon.svg -> 200; GET /pages/web_desig |
| Исходный код | link | OK(G) | OK(G) | external link https://github.com/GrapesJS/grapesjs present, not followed (gated) |
| Путь к документу | text | OK | OK | field value; scroll |
| Прочитать документ | button | OK | OK | toast:Укажите путь к документу.; dom; scroll |
| Текст документа | text | DISABLED | DISABLED | disabled without any reason shown |
| Папка с заметками | text | OK | OK | field value; scroll |
| Адрес модели эмбеддингов | text | OK | OK | field value; scroll |
| Модель | text | OK | OK | field value; scroll |
| Размерность | text | OK | OK | field value; scroll |
| Включить Qdrant | button | OK | OK | toast:Укажите папку с заметками.; dom; scroll |
| Использовать обычный поиск | button | OK | OK | toast:Укажите папку с заметками.; dom; scroll |
| Обновить индекс | button | OK | OK | toast:Сначала укажите папку с заметками и сохраните настройки.; dom; scroll |
| Запрос | text | OK | OK | field value; scroll |
| Найти в заметках | button | OK | OK | toast:Введите запрос.; dom; scroll |
| Найденные заметки | text | DISABLED | DISABLED | disabled without any reason shown |
| Определить язык Русский English Čeština | select | OK | OK | field value; scroll |
| Расшифровать | button | OK | OK | toast:Выберите WAV-файл.; dom; scroll |
| Здесь появится расшифровка | text | DISABLED | DISABLED | disabled without any reason shown |

### video-studio

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| BOSSMAN | link | OK | - | url:/#/home; 9 new controls; GET /api/agents -> 200; GET /api/models -> 200 |
| Выберите проект | select | OK | OK | select has a single option (''); nothing to change |
| ＋ | button | OK | OK | opened:vs-dialog:Новый проект × Название Применить; 3 new controls; field value; |
| ＋ > × | button | OK | OK | field value; control state; dom |
| ＋ > Применить | button | DEAD | OK | no DOM / URL / request / dialog / toast / storage change |
| ⋯ | button | DISABLED | DISABLED | explains: Проект · сначала откройте или создайте проект |
| Монтаж | button | OK | - | localStorage; control state; dom |
| Цвет | button | OK | OK | 2 new controls; localStorage; control state; dom |
| Цвет > Монтаж | button | ERROR | - | opener not found on replay |
| Цвет > Цвет | button | ERROR | - | opener not found on replay |
| Звук | button | OK | OK | 2 new controls; localStorage; control state; dom |
| Звук > Цвет | button | ERROR | - | opener not found on replay |
| Звук > Звук | button | ERROR | - | opener not found on replay |
| VFX | button | OK | OK | 2 new controls; localStorage; control state; dom |
| VFX > Звук | button | ERROR | - | opener not found on replay |
| VFX > VFX | button | ERROR | - | opener not found on replay |
| ИИ | button | OK | OK | 2 new controls; localStorage; control state; dom |
| ИИ > VFX | button | ERROR | - | opener not found on replay |
| ИИ > ИИ | button | ERROR | - | opener not found on replay |
| ↶ | button | DISABLED | DISABLED | explains: Отменить · Ctrl+Z · сначала откройте или создайте проект |
| ↷ | button | DISABLED | DISABLED | explains: Повторить · Ctrl+Shift+Z · сначала откройте или создайте проект |
| ⌘ | button | DISABLED | DISABLED | explains: Команды · Ctrl+K · сначала откройте или создайте проект |
| ✦ | button | OK | OK | localStorage; control state; dom |
| EN | button | OK | OK | 8 new controls; localStorage; control state; dom |
| EN > Choose a project | select | ERROR | OK | opener not found on replay |
| EN > Edit | button | ERROR | OK | opener not found on replay |
| EN > Color | button | ERROR | OK | opener not found on replay |
| EN > Audio | button | ERROR | OK | opener not found on replay |
| EN > AI | button | ERROR | - | opener not found on replay |
| EN > RU | button | ERROR | OK | opener not found on replay |
| EN > Export | button | DISABLED | DISABLED | explains: Export · open or create a project first |
| EN > ＋ New project | button | ERROR | OK | opener not found on replay |
| Экспорт | button | DISABLED | DISABLED | explains: Экспорт · сначала откройте или создайте проект |
| ＋ Новый проект | button | ERROR | UNVERIFIED | control vanished after re-render |

### music-studio

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Копировать | button | OK | OK | toast:Команда скопирована; 1 new controls; dom |
| Копировать > × | button | OK | OK | control state; dom |
| Проверить снова | button | OK | OK | GET /api/music/health -> 200; GET /api/music/presets -> 200; control state; dom |
| Phonk | button | OK | OK | already selected/active (no-op by design) |
| Drift Phonk | button | OK | OK | 4 new controls; field value; control state; dom |
| Drift Phonk > Phonk | button | OK | OK | 4 new controls; field value; control state; dom |
| Drift Phonk > Drift Phonk | button | OK | OK | already selected/active (no-op by design) |
| Ultra Funk | button | OK | OK | 3 new controls; field value; control state; dom |
| Ultra Funk > Phonk | button | OK | OK | 3 new controls; field value; control state; dom |
| Ultra Funk > Ultra Funk | button | OK | OK | GET /api/evolution/status -> 200 |
| Nightcore | button | OK | OK | 4 new controls; field value; control state; dom |
| Nightcore > Phonk | button | OK | OK | 4 new controls; field value; control state; dom |
| Nightcore > Nightcore | button | OK | OK | already selected/active (no-op by design) |
| Electro | button | OK | OK | 4 new controls; field value; control state; dom |
| Electro > Phonk | button | OK | OK | 4 new controls; field value; control state; dom |
| Electro > Electro | button | OK | OK | already selected/active (no-op by design) |
| dark aggressive phonk, distorted cowbell melody, punchy 808  | text | OK | OK | 2 new controls; field value; dom |
| 130 | text | OK | OK | 2 new controls; field value; dom; scroll |
| 90 | text | OK | OK | 1 new controls; field value; scroll |
| [inst] | text | OK | OK | 1 new controls; field value; scroll |
| Сгенерировать трек | button | DISABLED(G) | DISABLED(G) | explains: ACE-Step 1.5 не установлен на этом ПК (не найден каталог установки); п |

### bossman-chat

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Задание для Video Studio | text | OK | OK | field value |
| Выберите агента для обычных вопросов | select | OK | OK | select has a single option (''); nothing to change |
| Отправить | button | OK(G) | OK(G) | toast:Задание пустое: напишите, что сделать, или приложите файл.; dom |

### home-v3

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Открыть | button | SAMPLED | SAMPLED | same label+class as instance #2; first 2 probed |
| Открыть | button | SAMPLED | SAMPLED | same label+class as instance #2; first 2 probed |
| Open | button | SAMPLED | SAMPLED | same label+class as instance #2; first 2 probed |
| Open | button | SAMPLED | SAMPLED | same label+class as instance #2; first 2 probed |
| Open | button | SAMPLED | SAMPLED | same label+class as instance #2; first 2 probed |
| Что должен сделать BOSSMAN? | text | OK | OK | toast:Нет настроенного исполнителя для задачи.; POST /api/tasks/preflight -> 200 |
| Умно | button | OK | OK | control state; dom |
| Авто | button | OK(G) | OK(G) | control state; dom |
| С агентами | button | OK | OK | control state; dom |
| ЗАПУСТИТЬ Ctrl↵ | button | OK(G) | OK(G) | toast:Опишите задачу; 1 new controls; dom |
| ЗАПУСТИТЬ Ctrl↵ > × | button | OK | OK | control state; dom |
| Все приложения | button | OK | OK | url:/#/apps; 16 new controls; GET /pages/apps.js -> 200; GET /icon.svg -> 200 |
| Открыть | button | OK | OK | url:/#/apps?open=ai-webcam-vision; 8 new controls; GET /pages/apps.js -> 200; GE |
| Живой вид | button | OK | OK | url:/#/apps?open=ai-webcam-vision&action=live; 8 new controls; GET /icon.svg ->  |
| Открыть | button | OK | OK | url:/#/apps?open=ai-3d-maker; 8 new controls; GET /pages/apps.js -> 200; GET /ic |
| Новая модель | button | OK | OK | url:/#/apps?open=ai-3d-maker&action=new-model; 8 new controls; GET /pages/apps.j |
| Open Command Center | button | OK | OK | url:/#/apps?open=solana-volume-suite; 8 new controls; GET /icon.svg -> 200; GET  |
| Open | button | OK | OK | url:/#/apps?open=bossman-accountant; 8 new controls; GET /icon.svg -> 200; GET / |
| Open | button | OK | OK | url:/#/apps?open=exam-trainer-ai; 8 new controls; GET /pages/apps.js -> 200; GET |
| Миссии | button | OK | OK | url:/#/missions; 5 new controls; GET /icon.svg -> 200; GET /pages/missions.js -> |
| Все | button | OK | OK | url:/#/agents; 5 new controls; GET /icon.svg -> 200; GET /api/agents -> 200 |
| Создать агента | button | OK | OK | url:/#/agents?new=1; opened:modal wide:Новый агент Имя Чем занимается Короткое;  |
| Ресурсы | button | OK | OK | url:/#/resources; 9 new controls; GET /icon.svg -> 200; GET /pages/resources.js  |
| Подробно | button | OK | OK | url:/#/system; 3 new controls; GET /icon.svg -> 200; GET /api/system -> 200 |

### chat

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Открыть чат | link | OK | OK | url:/chat.html; toast:Auto · Local-first Маршрут выбирается при отправке; 24 new |

### apps

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Открыть | button | SAMPLED | SAMPLED | same label+class as instance #2; first 2 probed |
| Запустить | button | SAMPLED(G) | SAMPLED(G) | same label+class as instance #2; first 2 probed |
| Открыть | button | SAMPLED | SAMPLED | same label+class as instance #2; first 2 probed |
| Запустить | button | SAMPLED(G) | SAMPLED(G) | same label+class as instance #2; first 2 probed |
| Запустить | button | SAMPLED(G) | SAMPLED(G) | same label+class as instance #2; first 2 probed |
| Запустить | button | SAMPLED(G) | SAMPLED(G) | same label+class as instance #2; first 2 probed |
| Запустить | button | SAMPLED(G) | SAMPLED(G) | same label+class as instance #2; first 2 probed |
| Open | button | SAMPLED | SAMPLED | same label+class as instance #2; first 2 probed |
| Запустить | button | SAMPLED(G) | SAMPLED(G) | same label+class as instance #2; first 2 probed |
| Open | button | SAMPLED | SAMPLED | same label+class as instance #2; first 2 probed |
| Запустить | button | SAMPLED(G) | SAMPLED(G) | same label+class as instance #2; first 2 probed |
| Open | button | SAMPLED | SAMPLED | same label+class as instance #2; first 2 probed |
| Запустить | button | SAMPLED(G) | SAMPLED(G) | same label+class as instance #2; first 2 probed |
| Проверить состояние | button | OK | OK | toast:Состояние приложений обновлено; 1 new controls; GET /api/apps -> 200; GET  |
| Проверить состояние > × | button | OK | OK | control state; dom |
| Разрешить запуск приложений | button | OK | OK | toast:Запуск приложений разрешён; 2 new controls; PUT /api/apps/control/policy - |
| Разрешить запуск приложений > Запретить запуск приложений | button | ERROR | OK | opener not found on replay |
| Разрешить запуск приложений > × | button | ERROR | OK | opener not found on replay |
| Открыть | button | OK | OK | url:/#/apps?open=ai-webcam-vision; 4 new controls; GET /icon.svg -> 200; GET /ap |
| Живой вид | button | OK | OK | url:/#/apps?open=ai-webcam-vision&action=live; 4 new controls; GET /api/apps ->  |
| Запустить | button | OK(G) | OK(G) | toast:Приложение запущено; 1 new controls; POST /api/apps/ai-webcam-vision/start |
| Запустить > × | button | OK | OK | control state; dom |
| Открыть | button | OK | OK | url:/#/apps?open=ai-3d-maker; 4 new controls; GET /icon.svg -> 200; GET /api/app |
| Новая модель | button | OK | OK | url:/#/apps?open=ai-3d-maker&action=new-model; 4 new controls; GET /api/apps ->  |
| Запустить | button | OK(G) | OK(G) | toast:Приложение запущено; 1 new controls; POST /api/apps/ai-3d-maker/start -> 2 |
| Open Command Center | button | OK | OK | url:/#/apps?open=solana-volume-suite; 4 new controls; GET /api/apps -> 200; GET  |
| Open | button | OK | OK | url:/#/apps?open=bossman-accountant; 4 new controls; GET /api/apps -> 200; GET / |
| Open | button | OK | OK | url:/#/apps?open=exam-trainer-ai; 4 new controls; GET /api/apps -> 200; GET /ico |

### overview

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Что должен сделать BOSSMAN? | text | OK | OK | field value; dom |
| агентов ещё нет | select | OK | OK | select has a single option (''); nothing to change |
| START | button | OK(G) | OK(G) | toast:Опишите задачу; 1 new controls; dom |
| START > × | button | OK | OK | control state; dom |
| Все миссии | button | OK | OK | url:/#/missions; 3 new controls; GET /pages/missions.js -> 200; GET /icon.svg -> |
| Новая миссия | button | OK | OK | url:/#/missions; 3 new controls; GET /pages/missions.js -> 200; GET /icon.svg -> |
| Очередь | button | OK | OK | url:/#/approvals; 1 new controls; GET /icon.svg -> 200; GET /api/approvals -> 20 |
| Все агенты | button | OK | OK | url:/#/agents; 3 new controls; GET /icon.svg -> 200; GET /api/agents -> 200 |
| Ресурсы | button | OK | OK | url:/#/resources; 7 new controls; GET /pages/resources.js -> 200; GET /icon.svg  |

### missions

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Новая миссия | button | OK | OK | opened:modal wide:Новая миссия Название Цель Свободный те; 12 new controls; fiel |
| Новая миссия > Закрыть | button | OK | OK | field value; control state; dom |
| Новая миссия > (без подписи) | button | OK | OK | field value; control state; dom |
| Новая миссия > Ещё показатель | button | OK | OK | field value; dom |
| Новая миссия > Отмена | button | OK | OK | field value; control state; dom |
| Новая миссия > Создать | button | OK | OK | toast:Укажите название миссии; 1 new controls; dom |
| Создать первую миссию | button | OK | OK | opened:modal wide:Новая миссия Название Цель Свободный те; 12 new controls; fiel |
| Создать первую миссию > Закрыть | button | OK | OK | field value; control state; dom |
| Создать первую миссию > (без подписи) | button | OK | OK | field value; control state; dom |
| Создать первую миссию > Ещё показатель | button | OK | OK | field value; dom |
| Создать первую миссию > Отмена | button | OK | OK | field value; control state; dom |
| Создать первую миссию > Создать | button | OK | OK | toast:Укажите название миссии; 1 new controls; dom |

### router

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| on | toggle | OK | OK | toast:Правило обновлено; 1 new controls; PATCH /api/router/rules -> 200; field v |
| Тип задачи | text | OK | OK | field value |
| Минимальный размер контекста | text | OK | OK | field value |
| Макс. цена ответа, $ за 1М | text | OK | OK | field value |
| Свободно памяти, МБ | text | OK | OK | field value |
| on | toggle | OK | OK | field value |
| Проверить | button | OK | OK | POST /api/router/preview -> 200; dom |
| Номер задачи | text | OK | OK | field value |
| Показать | button | OK | OK | toast:Укажите ID задачи; 1 new controls; dom |
| Показать > × | button | OK | OK | control state; dom |
| { "requires": { "coding": [ "coding" ], "vision": [ "vision" | text | OK | OK | 1 new controls; field value; scroll |
| Сохранить | button | OK | OK | toast:Правила обновлены; 1 new controls; PATCH /api/router/rules -> 200; GET /ap |
| Сохранить > × | button | OK | OK | control state; dom |

### governor

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Повторов одной ошибки | text | OK | OK | field value |
| Шагов на месте | text | OK | OK | field value |
| Повторов у самой задачи | text | OK | OK | field value |
| Сохранить | button | OK | OK | toast:Пороги обновлены; 1 new controls; PATCH /api/governor/rules -> 200; GET /a |
| Сохранить > × | button | OK | OK | control state; dom |

### file-intelligence

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Технические подробности | summary | OK | OK | dom |

### resources

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Поровну | button | OK | - | toast:Правило обновлено; 1 new controls; POST /api/resources/policy -> 200; GET  |
| Поровну > × | button | OK | OK | control state; dom |
| На скорость | button | OK | OK | toast:Правило обновлено; 3 new controls; POST /api/resources/policy -> 200; GET  |
| На скорость > Поровну | button | ERROR | - | opener not found on replay |
| На скорость > На скорость | button | ERROR | - | opener not found on replay |
| На скорость > × | button | ERROR | OK | opener not found on replay |
| Экономно | button | OK | OK | toast:Правило обновлено; 3 new controls; POST /api/resources/policy -> 200; GET  |
| Экономно > На скорость | button | ERROR | - | opener not found on replay |
| Экономно > Экономно | button | ERROR | - | opener not found on replay |
| Экономно > × | button | ERROR | OK | opener not found on replay |
| считать автоматически | text | OK | OK | field value |
| 16000 | text | OK | OK | 1 new controls; field value |
| Сохранить | button | OK | OK | toast:Настройки сохранены; 1 new controls; POST /api/resources/policy -> 200; GE |
| Сохранить > × | button | OK | OK | control state; dom |

### skills

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Загрузить | button | OK | OK | opened:modal wide:Загрузка навыка Короткое имя (id) Латин; 6 new controls; field |
| Загрузить > Закрыть | button | OK | OK | field value; control state; dom |
| Загрузить > on | toggle | OK | OK | field value |
| Загрузить > Отмена | button | OK | OK | field value; control state; dom |
| Загрузить > Импортировать | button | OK | OK | toast:Укажите имя навыка; 1 new controls; dom |
| Новый навык | button | OK | OK | opened:modal wide:Новый навык Короткое имя (id) Латиница,; 6 new controls; field |
| Новый навык > Закрыть | button | OK | OK | field value; control state; dom |
| Новый навык > on | toggle | OK | OK | field value |
| Новый навык > Отмена | button | OK | OK | field value; control state; dom |
| Новый навык > Создать | button | OK | OK | toast:Укажите имя навыка; 1 new controls; dom |
| Новый навык | button | OK | OK | opened:modal wide:Новый навык Короткое имя (id) Латиница,; 6 new controls; field |
| Добавить сервер | button | OK | OK | opened:modal-wrap:Добавить MCP-сервер Имя Транспорт stdio; 6 new controls; field |
| Добавить сервер > Закрыть | button | OK | OK | field value; control state; dom |
| Добавить сервер > stdio (command) http (url) | select | OK | OK | 1 new controls; field value; dom |
| Добавить сервер > Отмена | button | OK | OK | field value; control state; dom |
| Добавить сервер > Добавить | button | OK | OK | toast:Укажите имя; 1 new controls; dom |

### terminal

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Песочница | button | OK | OK | 2 new controls; GET /api/terminal/capabilities -> 200; GET /api/terminal/session |
| Песочница > Песочница | button | OK | - | GET /api/terminal/capabilities -> 200; GET /api/terminal/roots -> 200; GET /api/ |
| Песочница > В проекте | button | OK | - | 2 new controls; GET /api/terminal/capabilities -> 200; GET /api/terminal/roots - |
| В проекте | button | OK | - | GET /api/terminal/capabilities -> 200; GET /api/terminal/roots -> 200; GET /api/ |
| С правами системы | button | OK | OK | 2 new controls; GET /api/terminal/capabilities -> 200; GET /api/terminal/roots - |
| С правами системы > В проекте | button | OK | - | 2 new controls; GET /api/terminal/capabilities -> 200; GET /api/terminal/roots - |
| С правами системы > С правами системы | button | OK | - | GET /api/terminal/capabilities -> 200; GET /api/terminal/roots -> 200; GET /api/ |
| В какой папке выполнять | text | OK | OK | field value |
| Команда | text | OK | OK | field value |
| on | toggle | OK | OK | field value |
| Проверить | button | OK | OK | toast:Введите команду; 1 new controls; dom |
| Проверить > × | button | OK | OK | control state; dom |
| Запустить | button | OK(G) | OK(G) | toast:Введите команду; 1 new controls; dom |
| Запустить > × | button | OK | OK | control state; dom |
| C:\Users\asd\AppData\Local\Temp\bcc-uxsweep-0zn75n35\data | text | OK | - | 1 new controls; field value; dom |
| Убрать папку | button | OK(G) | OK(G) | field value; control state; dom |
| Ещё папка | button | OK | OK | 1 new controls; field value; dom |
| Сохранить | button | OK | OK | toast:Корни сохранены; 1 new controls; POST /api/terminal/roots -> 200; GET /api |
| Сохранить > × | button | OK | OK | control state; dom |

### benchmarks

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Запустить замер | button | OK(G) | OK(G) | opened:modal-wrap:Запустить замер Модель ace-qwen3-17b:la; 4 new controls; field |
| Запустить замер > Закрыть | button | OK | OK | field value; control state; dom |
| Запустить замер > ace-qwen3-17b:latest | select | OK | OK | select has a single option ('1'); nothing to change |
| Запустить замер > Отмена | button | OK | OK | field value; control state; dom |
| Запустить замер > Запустить | button | OK(G) | OK(G) | toast:Замер запущен в фоне; 1 new controls; POST /api/benchmarks -> 200; GET /ap |

### browser

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Новое окно | button | OK | OK | opened:modal wide:Браузер · сессия #1 Перейти выполняется; toast:Сессия браузера |
| Новое окно > Обновить | button | ERROR | OK | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Новое окно > Закрыть | button | OK | OK | GET /api/browser/sessions -> 200; GET /api/browser/health -> 200; field value; c |
| Новое окно > Перейти | button | BLOCKED_BY_DESIGN | BLOCKED_BY_DESIGN | refused with visible feedback: HTTP 403 POST /api/browser/sessions/4/act |
| Новое окно > Обновить скриншот | button | OK | OK | GET /api/browser/sessions/5/screenshot -> 200 |
| Новое окно > Взять управление | button | OK | OK | opened:modal wide:Браузер · сессия #6 Перейти выполняется; toast:Вы взяли управл |
| Новое окно > Закрыть окно | button | OK | OK | toast:Сессия остановлена; POST /api/browser/sessions/7/stop -> 200; GET /api/bro |
| Новое окно > Закрыть | button | OK | OK | GET /api/browser/sessions -> 200; GET /api/browser/health -> 200; field value; c |
| Новое окно > × | button | OK | OK | control state; dom(near control) |

### coding

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Обновить | button | OK | OK | GET /api/coding-sessions -> 200; GET /api/coding-tasks -> 200 |
| Новая сессия | button | OK | OK | opened:modal-wrap:Новая coding-сессия ИМЯ РЕПОЗИТОРИЙ БАЗ; 6 new controls; field |
| Новая сессия > Закрыть | button | OK | OK | field value; control state; dom |
| Новая сессия > Создать | button | OK | OK | toast:Укажите папку репозитория; 1 new controls; dom |
| Новая сессия > Отмена | button | OK | OK | field value; control state; dom |
| Новая задача агенту | button | DISABLED | DISABLED | explains: проверяю готовность OpenHands… |

### rave

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| STOP всех рейвов | button | OK(G) | OK(G) | toast:STOP: активных рейвов не было; 1 new controls; POST /api/rave/stop-all ->  |
| STOP всех рейвов > × | button | OK | OK | control state; dom |
| Проверить вход | button | OK | OK | 1 new controls; GET /api/rave/connectors -> 200; GET /api/rave -> 200; GET /api/ |
| Проверить вход > Обновить | button | OK | - | GET /api/evolution/status -> 200; GET /api/rave -> 200; GET /api/rave/pool -> 20 |
| Задача для всех агентов | text | OK | OK | field value; dom |
| mock:a,local:qwen,claude:c,codex:x | text | OK | OK | field value |
| путь к git-проекту в разрешённых корнях (пусто — чистый scra | text | OK | OK | field value |
| пути, где local-агенту можно писать (через запятую; пусто —  | text | OK | OK | field value |
| команда проверки в каждой готовой копии, например: python -m | text | OK | OK | field value; dom; scroll |
| Запустить | button | OK(G) | OK(G) | toast:Сначала введите задачу для агентов; 1 new controls; dom; scroll |
| Запустить > × | button | OK | OK | control state; dom |
| Добавить аккаунт | button | OK | OK | opened:modal-wrap:Добавить аккаунт в пул Инструмент Claud; 7 new controls; field |
| Добавить аккаунт > Закрыть | button | OK | OK | field value; control state; dom |
| Добавить аккаунт > Claude Code (подписка Claude) Codex (подп | select | OK | OK | field value |
| Добавить аккаунт > on | toggle | OK | OK | field value |
| Добавить аккаунт > Отмена | button | OK | OK | field value; control state; dom |
| Добавить аккаунт > Добавить | button | OK | OK | toast:Введите имя аккаунта; 1 new controls; dom |
| Включить пул | button | DISABLED | DISABLED | explains: Сначала добавьте аккаунт |
| 14 | text | OK | OK | 1 new controls; field value; scroll |
| Показать, что будет удалено | button | OK(G) | OK(G) | 1 new controls; POST /api/rave/prune -> 200; GET /api/rave -> 200; GET /api/rave |
| Показать, что будет удалено > Повторить | button | OK | OK | GET /api/rave -> 200; GET /api/rave/pool -> 200; control state; dom |
| Удалить | button | OK(G) | OK(G) | toast:Сначала сделайте пробный прогон: там должны быть рейвы к уда; 1 new contro |
| Удалить > × | button | OK | OK | control state; dom |

### agentmap

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Все агенты | select | OK | OK | select has a single option (''); nothing to change |

### orchestras

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Название (необязательно) | text | OK | OK | field value |
| Опишите команду | text | OK | OK | field value |
| Разобрать | button | OK | OK | toast:Опишите команду; 1 new controls; dom |
| Разобрать > × | button | OK | OK | control state; dom |
| Создать | button | DISABLED | DISABLED | explains: Сначала разберите текст, чтобы всё было понятно |

### forks

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| выберите задачу | select | OK | OK | select has a single option (''); nothing to change |

### healing

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| За сколько секунд считать | text | ERROR | OK | TimeoutError: Page.goto: Timeout 8000ms exceeded. |
| Сколько ошибок — тревога | text | OK | OK | field value; dom |
| Сколько раз пробовать | text | OK | OK | field value |
| Сохранить | button | OK | OK | toast:Пороги обновлены; 1 new controls; PATCH /api/healing/rules -> 200; GET /ap |
| Сохранить > × | button | OK | OK | control state; dom |

### openrouter

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| API KEY | text | OK | OK | field value |
| Connect | button | OK | OK | toast:Вставьте ключ; 1 new controls; dom |
| Connect > × | button | OK | OK | control state; dom |

### command

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Закрыть | button | SAMPLED | SAMPLED | same label+class as instance #2; first 2 probed |
| Взять управление | button | SAMPLED | SAMPLED | same label+class as instance #2; first 2 probed |
| Закрыть | button | SAMPLED | SAMPLED | same label+class as instance #2; first 2 probed |
| Взять управление | button | SAMPLED | - | same label+class as instance #2; first 2 probed |
| Закрыть | button | SAMPLED | - | same label+class as instance #2; first 2 probed |
| Взять управление | button | SAMPLED | - | same label+class as instance #2; first 2 probed |
| Закрыть | button | SAMPLED | - | same label+class as instance #2; first 2 probed |
| Взять управление | button | SAMPLED | - | same label+class as instance #2; first 2 probed |
| Закрыть | button | SAMPLED | - | same label+class as instance #2; first 2 probed |
| Взять управление | button | SAMPLED | - | same label+class as instance #2; first 2 probed |
| Закрыть | button | SAMPLED | - | same label+class as instance #2; first 2 probed |
| Открыть миссии | button | OK | OK | url:/#/missions; 3 new controls; GET /icon.svg -> 200; GET /pages/missions.js -> |
| Взять управление | button | OK | OK | toast:Управление у вас; 1 new controls; POST /api/browser/sessions/9/takeover -> |
| Взять управление > × | button | OK | OK | control state; dom |
| Закрыть | button | OK | OK | toast:Готово; 1 new controls; POST /api/browser/sessions/9/stop -> 200; GET /api |
| Закрыть > × | button | OK | OK | control state; dom |
| Взять управление | button | OK | OK | toast:Управление у вас; 1 new controls; POST /api/browser/sessions/4/takeover -> |
| Закрыть | button | OK | OK | toast:Готово; 1 new controls; POST /api/browser/sessions/5/stop -> 200; GET /api |
| Вернуть агенту | button | OK | OK | toast:Готово; 1 new controls; POST /api/browser/sessions/6/resume -> 200; GET /a |
| Вернуть агенту > × | button | OK | OK | control state; dom |
| Быстрая задача… | text | OK | OK | field value; dom; scroll |
| агентов ещё нет | select | OK | OK | select has a single option (''); nothing to change |
| START | button | OK(G) | OK(G) | toast:Опишите задачу; 1 new controls; dom; scroll |
| START > × | button | OK | OK | control state; dom(near control) |

### builder

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| К миссиям | button | OK | OK | url:/#/missions; 4 new controls; GET /pages/missions.js -> 200; GET /icon.svg -> |

### images

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Создать в Studio | button | OK | OK | url:/#/images?studio=1; 30 new controls; GET /api/studio/jobs -> 200; GET /api/s |
| Библиотека | button | OK | - | GET /api/images/jobs -> 200; GET /api/images/models -> 200; GET /api/images/stor |
| Генерации | button | OK | OK | 2 new controls; GET /api/images/jobs -> 200; GET /api/images/models -> 200; GET  |
| Генерации > Библиотека | button | OK | - | 4 new controls; GET /api/images/jobs -> 200; GET /api/images/models -> 200; GET  |
| Генерации > Генерации | button | OK | - | GET /api/images/jobs -> 200; GET /api/images/models -> 200; GET /api/images/asse |
| Шаблоны | button | OK | OK | 2 new controls; GET /api/images/storage -> 200; GET /api/images/models -> 200; G |
| Шаблоны > Библиотека | button | OK | - | 4 new controls; GET /api/images/models -> 200; GET /api/images/jobs -> 200; GET  |
| Шаблоны > Шаблоны | button | OK | - | GET /api/images/storage -> 200; GET /api/images/jobs -> 200; GET /api/images/mod |
| Очередь | button | OK | OK | 2 new controls; GET /api/images/jobs -> 200; GET /api/images/models -> 200; GET  |
| Очередь > Библиотека | button | OK | - | 4 new controls; GET /api/images/jobs -> 200; GET /api/images/models -> 200; GET  |
| Очередь > Очередь | button | OK | - | GET /api/images/jobs -> 200; GET /api/images/storage -> 200; GET /api/images/mod |
| Опишите изображение… | text | OK | OK | field value |
| BOSSMAN Mock Image · заглушка ComfyUI (local text-to-image)  | select | OK | OK | GET /api/images/jobs -> 200; GET /api/images/models -> 200; GET /api/images/coll |
| 1:1 16:9 9:16 4:3 3:2 | select | OK | OK | GET /api/images/jobs -> 200; GET /api/images/models -> 200; GET /api/images/stor |
| 1 шт. 2 шт. 4 шт. | select | OK | OK | field value |
| Без папки | select | OK | OK | select has a single option (''); nothing to change |
| Запустить | button | OK(G) | OK(G) | toast:Генерация поставлена в очередь; 1 new controls; POST /api/images/jobs -> 2 |
| Запустить > × | button | OK | OK | control state; dom |
| Новая коллекция | button | BLOCKED_BY_DESIGN | BLOCKED_BY_DESIGN | native: prompt: Название новой коллекции (cancelled) |
| Все изображения 0 | button | OK | - | GET /api/images/storage -> 200; GET /api/images/models -> 200; GET /api/images/j |
| Избранное 0 | button | OK | OK | 2 new controls; GET /api/images/storage -> 200; GET /api/images/jobs -> 200; GET |
| Избранное 0 > Все изображения 0 | button | OK | - | 2 new controls; GET /api/images/storage -> 200; GET /api/images/models -> 200; G |
| Избранное 0 > Избранное 0 | button | OK | - | GET /api/images/jobs -> 200; GET /api/images/storage -> 200; GET /api/images/ass |
| Поиск изображений | text | OK | OK | field value |
| Найти | button | OK | OK | GET /api/images/jobs -> 200; GET /api/images/models -> 200; GET /api/images/stor |
| Все | button | OK | OK | 2 new controls; GET /api/images/jobs -> 200; GET /api/images/storage -> 200; GET |
| Все > Библиотека | button | OK | - | 4 new controls; GET /api/images/jobs -> 200; GET /api/images/models -> 200; GET  |
| Все > Генерации | button | OK | - | GET /api/images/jobs -> 200; GET /api/images/assets -> 200; GET /api/images/coll |

### images?studio=1

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Библиотека Images | button | OK | OK | url:/#/images; 17 new controls; GET /api/images/jobs -> 200; GET /api/images/sto |
| Промпт Studio | text | OK | OK | field value |
| Локальное демо (не AI-генерация) · не проверено Configured l | select | OK | OK | GET /api/studio/jobs -> 200; GET /api/studio/budget -> 200; GET /api/studio/runs |
| 1 | text | DEAD | DEAD | no DOM / URL / request / dialog / toast / storage change |
| Без коллекции | select | OK | OK | select has a single option (''); nothing to change |
| Создать результат | button | OK | OK | toast:Опишите желаемый результат.; 1 new controls; GET /api/studio/policy -> 200 |
| Создать результат > × | button | OK | OK | control state; dom |
| Раскадровка: 5 кадров | button | OK | OK | toast:Опишите идею для раскадровки.; 1 new controls; GET /api/studio/policy -> 2 |
| Раскадровка: 5 кадров > × | button | OK | OK | control state; dom |
| Ширина | text | OK | OK | field value |
| Высота | text | OK | OK | field value; dom |
| Шаги | text | OK | OK | field value |
| Seed | text | OK | OK | field value |
| Все результаты | button | OK | OK | GET /api/studio/policy -> 200; GET /api/studio/jobs -> 200; GET /api/studio/budg |
| Изображения Studio | button | OK | OK | 2 new controls; GET /api/studio/jobs -> 200; GET /api/studio/policy -> 200; GET  |
| Изображения Studio > Все результаты | button | OK | OK | 2 new controls; GET /api/studio/policy -> 200; GET /api/studio/jobs -> 200; GET  |
| Изображения Studio > Изображения Studio | button | OK | OK | GET /api/studio/jobs -> 200; GET /api/studio/budget -> 200; GET /api/studio/runs |
| Видео Studio | button | OK | OK | 2 new controls; GET /api/studio/jobs -> 200; GET /api/studio/policy -> 200; GET  |
| Видео Studio > Все результаты | button | OK | OK | 2 new controls; GET /api/studio/jobs -> 200; GET /api/studio/policy -> 200; GET  |
| Видео Studio > Видео Studio | button | OK | OK | GET /api/studio/jobs -> 200; GET /api/studio/policy -> 200; GET /api/studio/budg |
| Звук Studio | button | OK | OK | 2 new controls; GET /api/studio/jobs -> 200; GET /api/studio/policy -> 200; GET  |
| Звук Studio > Все результаты | button | OK | OK | 2 new controls; GET /api/studio/jobs -> 200; GET /api/studio/budget -> 200; GET  |
| Звук Studio > Звук Studio | button | OK | OK | GET /api/studio/policy -> 200; GET /api/studio/jobs -> 200; GET /api/studio/budg |
| Избранное Studio | button | OK | OK | 2 new controls; GET /api/studio/policy -> 200; GET /api/studio/jobs -> 200; GET  |
| Избранное Studio > Все результаты | button | OK | OK | 2 new controls; GET /api/studio/policy -> 200; GET /api/studio/jobs -> 200; GET  |
| Избранное Studio > Избранное Studio | button | OK | OK | GET /api/studio/jobs -> 200; GET /api/studio/policy -> 200; GET /api/studio/budg |
| Корзина Studio | button | OK | OK | 2 new controls; GET /api/studio/jobs -> 200; GET /api/studio/budget -> 200; GET  |
| Корзина Studio > Все результаты | button | OK | OK | 2 new controls; GET /api/studio/jobs -> 200; GET /api/studio/runs -> 200; GET /a |
| Корзина Studio > Корзина Studio | button | OK | OK | GET /api/studio/policy -> 200; GET /api/studio/jobs -> 200; GET /api/studio/budg |
| Поиск Studio | text | OK | OK | field value; scroll |
| Найти в Studio | button | OK | OK | GET /api/studio/policy -> 200; GET /api/studio/jobs -> 200; GET /api/studio/budg |
| Выбранные в корзину | button | OK | OK | GET /api/studio/policy -> 200; GET /api/studio/jobs -> 200; GET /api/studio/budg |
| Скачать выбранные ZIP | button | OK(G) | OK(G) | toast:Сначала выберите результаты.; 1 new controls; GET /api/studio/jobs -> 200; |
| Скачать выбранные ZIP > × | button | OK | OK | control state; dom |
| Подключение и расходы | summary | OK | OK | 2 new controls; dom; scroll |
| Подключение и расходы > Сохранить правила облака | button | OK | OK | toast:Правила сохранены; 7 new controls; PUT /api/studio/policy -> 200; GET /api |
| Подключение и расходы > Отозвать разрешения на референсы | button | OK(G) | OK(G) | 6 new controls; DELETE /api/studio/egress/confirmations -> 200; GET /api/studio/ |
| on | toggle | ERROR | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| on | toggle | ERROR | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| 0 | text | ERROR | ERROR | TimeoutError: Locator.fill: Timeout 4000ms exceeded. |
| 0 | text | ERROR | ERROR | TimeoutError: Locator.fill: Timeout 4000ms exceeded. |
| Верхняя стоимость моделей | text | ERROR | ERROR | TimeoutError: Locator.fill: Timeout 4000ms exceeded. |
| Разрешённые CDN | text | ERROR | ERROR | TimeoutError: Locator.fill: Timeout 4000ms exceeded. |
| (без подписи) | button | ERROR | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| (без подписи) | button | ERROR | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. |

### v15-owner-run

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Исходники для самоулучшения | text | OK | OK | field value |
| Циклов максимум | text | OK | OK | field value |
| on | toggle | OK | OK | field value |
| ⚡ Quick Test | button | OK | OK | toast:1.5 требует подготовки; 1 new controls; POST /api/v15/owner-run/quick-test |
| ⚡ Quick Test > × | button | OK | OK | control state; dom |
| ▶ Запустить 1.5 | button | OK(G) | OK(G) | url:/#/capability-tree; toast:Bossman 1.5 запущен; 833 new controls; POST /api/v |
| ⛔ STOP | button | OK(G) | OK(G) | toast:STOP запрошен; 1 new controls; POST /api/v15/owner-run/stop -> 200; GET /a |
| ⛔ STOP > × | button | OK | OK | control state; dom |
| ↻ Обновить | button | OK | OK | GET /api/v15/self-repair/inbox -> 200; GET /api/v15/autonomy/status -> 200; GET  |

### mission_console

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Выберите исполнителя — иначе черновик | select | OK | OK | select has a single option (''); nothing to change |
| Команда оператора | text | OK | OK | field value; scroll |
| Отправить | button | OK(G) | - | toast:Команда пустая; 1 new controls; dom(near control); scroll |
| Отправить > × | button | OK | OK | control state; dom |
| Доложи состояние миссии | button | OK | OK | field value; scroll |
| Продолжи план со следующего шага | button | OK | OK | field value; scroll |
| Проверь источники последнего вывода | button | OK | - | field value; scroll |
| Собери короткий отчёт по сделанному | button | OK | - | field value; scroll |
| Останови текущую задачу | button | OK(G) | - | field value; scroll |

### control

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Обновить | button | OK | OK | GET /api/control-plane -> 200; GET /api/computer/status -> 200; control state; d |
| Стоп | button | OK(G) | OK(G) | POST /api/computer/stop -> 200; GET /api/control-plane -> 200; GET /api/computer |
| Продолжить | button | DISABLED | DISABLED | explains: Нечего продолжать — «Стоп» не нажат |

### web_designer

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Название проекта, например «Кофейня Север» | text | OK | OK | field value; dom |
| О чём сайт и как должен выглядеть: тема, стиль, цвета… | text | OK | OK | field value; dom |
| Палитра: авто (из описания) Палитра: blue Палитра: dark Пали | select | OK | OK | field value; dom |
| Открыть проект | button | OK | OK | toast:1440 × 900 CSS px · 34% · масштаб меняет только показ; 22 new controls; PO |
| Открыть проект > Обновить | button | ERROR | - | opener not found on replay |
| Открыть проект > Мой сайт · v1 | select | ERROR | OK | opener not found on replay |
| Открыть проект > Конструктор блоков | button | ERROR | BLOCKED_BY_DESIGN | opener not found on replay |
| Открыть проект > + Проект | button | ERROR | OK | opener not found on replay |
| Открыть проект > Скачать HTML | button | ERROR(G) | OK(G) | opener not found on replay |
| Открыть проект > Задача «сделать сайт» | button | ERROR | BLOCKED_BY_DESIGN | opener not found on replay |
| Открыть проект > Шаблон: авто Шаблон: Лендинг Шаблон: Портфо | select | ERROR | OK | opener not found on replay |
| Открыть проект > Палитра: авто Палитра: blue Палитра: dark П | select | ERROR | OK | opener not found on replay |
| Открыть проект > Сгенерировать сайт | button | ERROR | OK | opener not found on replay |
| Открыть проект > Выделение: вкл | button | ERROR | OK | opener not found on replay |
| Открыть проект > открыть в новой вкладке | link | ERROR | UNVERIFIED | opener not found on replay |
| Открыть проект > Обновить | button | ERROR | OK | opener not found on replay |
| Открыть проект > ПК · 1440 × 900 Ноутбук · 1280 × 800 Планше | select | ERROR | OK | opener not found on replay |
| Открыть проект > Применить размер | button | ERROR | DEAD | opener not found on replay |
| Лендинг Герой-блок, преимущества, цифры и форма связи | button | ERROR | - | control vanished after re-render |
| Портфолио Сетка работ, навыки, обо мне и контакты | button | ERROR | UNVERIFIED | control vanished after re-render |
| Кафе / ресторан Меню с ценами, галерея, отзывы и часы работы | button | ERROR | UNVERIFIED | control vanished after re-render |
| Магазин Витрина товаров с ценами, доставка и подписка | button | ERROR(G) | UNVERIFIED(G) | control vanished after re-render |
| Блог Список статей, рубрики и подписка на рассылку | button | ERROR(G) | UNVERIFIED(G) | control vanished after re-render |
| Агентство / студия Услуги, кейсы, команда и процесс работы | button | ERROR | UNVERIFIED | control vanished after re-render |
| ИИ: творческий бриф (локально) Один запрос к настроенной лок | button | ERROR | UNVERIFIED | control vanished after re-render |

### objectives

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Новая цель | button | OK | OK | opened:modal wide:Новая цель 1. УСЛОВИЕ 2. РИТМ И ОБЛАСТЬ; 8 new controls; field |
| Новая цель > Закрыть | button | OK | OK | field value; control state; dom |
| Новая цель > должно быть true | button | OK | - | already selected/active (no-op by design) |
| Новая цель > должно быть false | button | OK | OK | 2 new controls; control state; dom |
| Новая цель > Отмена | button | OK | OK | field value; control state; dom |
| Новая цель > Далее | button | OK | OK | 7 new controls; field value; control state; dom |
| Новая цель | button | OK | OK | opened:modal wide:Новая цель 1. УСЛОВИЕ 2. РИТМ И ОБЛАСТЬ; 8 new controls; field |

### jeff-settings

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| участников пока нет | select | OK | OK | select has a single option (''); nothing to change |
| Обычный | button | OK | OK | toast:Пресет «Обычный» выбран — нажмите «Сохранить», чтобы примени; 1 new contro |
| Обычный > × | button | OK | OK | control state; dom |
| Дерзкий | button | OK | OK | toast:Пресет «Дерзкий» выбран — нажмите «Сохранить», чтобы примени; 1 new contro |
| Дерзкий > × | button | OK | OK | control state; dom |
| Тёплый | button | OK | OK | toast:Пресет «Тёплый» выбран — нажмите «Сохранить», чтобы применит; 1 new contro |
| Тёплый > × | button | OK | OK | control state; dom |
| Краткий | button | OK | OK | toast:Пресет «Краткий» выбран — нажмите «Сохранить», чтобы примени; 1 new contro |
| Краткий > × | button | OK | OK | control state; dom |
| Сердитый — 24 часа | button | OK | OK | toast:Пресет «Сердитый — 24 часа» выбран — нажмите «Сохранить», чт; 1 new contro |
| Сердитый — 24 часа > × | button | OK | OK | control state; dom(near control) |
| Инициатива | text | OK | OK | field value; dom; scroll |
| Любопытство | text | OK | OK | field value; dom; scroll |
| Глубина | text | OK | OK | field value; dom; scroll |
| Краткость | text | OK | OK | field value; dom; scroll |
| Теплота | text | OK | OK | field value; dom; scroll |
| Юмор | text | OK | OK | field value; dom; scroll |
| Прямота | text | OK | OK | field value; dom; scroll |
| Креативность | text | OK | OK | field value; dom; scroll |
| Без срока 24 часа | select | OK | OK | field value; dom; scroll |
| Дополнительно о стиле (необязательно) | text | OK | OK | field value; dom; scroll |
| Сохранить | button | OK | OK | toast:Настройки Jeff сохранены; 1 new controls; PUT /api/jeff-settings -> 200; G |
| Сохранить > × | button | OK | OK | control state; dom |
| Откат к обычному | button | OK(G) | OK(G) | toast:Jeff снова обычный; 1 new controls; POST /api/jeff-settings/reset -> 200;  |
| Откат к обычному > × | button | OK | OK | control state; dom |
| участников пока нет | select | OK | OK | select has a single option (''); nothing to change |
| js-cloud-session | toggle | OK | OK | field value; dom; scroll |
| Сохранить для облака | button | OK | OK | toast:Настройка приватности сохранена; 1 new controls; PUT /api/jeff-settings -> |
| Сохранить для облака > × | button | OK | OK | control state; dom |
| js-math-assist | toggle | OK | OK | field value; dom; scroll |
| Сохранить расчёты | button | OK | OK | toast:Настройка расчётов сохранена; 1 new controls; PUT /api/jeff-settings -> 20 |
| Сохранить расчёты > × | button | OK | OK | control state; dom |
| $ в день | text | OK | OK | field value; scroll |
| $ на одну задачу | text | OK | OK | dom; scroll |
| Сохранить лимиты | button | OK | OK | toast:Лимиты сохранены; 1 new controls; PUT /api/jeff-settings -> 200; GET /api/ |
| Сохранить лимиты > × | button | OK | OK | control state; dom |

### motion-studio

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| bossman_32_days bossman_epic_22s jeff_voice_12s | select | OK | OK | GET /api/motion-studio/examples/bossman_32_days -> 200; field value; dom |
| Сценарий (JSON) | text | OK | OK | field value; dom |
| ms-novoice | toggle | OK | OK | field value; dom |
| Предпросмотр кадров | button | OK | OK | toast:Предпросмотр запущен; 2 new controls; POST /api/motion-studio/jobs -> 202; |
| Предпросмотр кадров > Остановить | button | ERROR(G) | OK(G) | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Предпросмотр кадров > × | button | OK | OK | control state; dom |
| Полный рендер (музыка + субтитры) | button | OK | OK | toast:Полный рендер запущен; 2 new controls; POST /api/motion-studio/jobs -> 202 |
| Полный рендер (музыка + субтит > Остановить | button | ERROR(G) | OK(G) | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Полный рендер (музыка + субтит > × | button | ERROR | OK | TimeoutError: Locator.click: Timeout 4000ms exceeded. |

### jeff-insights

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Обновить | button | OK | OK | toast:Обновлено; 1 new controls; GET /api/jeff-insights/overview -> 200; control |
| Обновить > × | button | OK | OK | control state; dom |
| Показать дайджест недели | button | OK | OK | toast:Дайджест собран; 1 new controls; GET /api/jeff-insights/digest -> 200; dom |
| Показать дайджест недели > × | button | OK | OK | control state; dom |
| Отправить в Пульт | button | OK(G) | OK(G) | toast:Пульт не принял дайджест; 1 new controls; POST /api/jeff-insights/digest/s |
| Отправить в Пульт > × | button | OK | OK | control state; dom |

### autonomy

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| STOP автономии | button | OK(G) | OK(G) | toast:STOP записан; 1 new controls; POST /api/autonomy/stop -> 200; GET /api/aut |
| STOP автономии > × | button | OK | OK | control state; dom |
| Снять STOP | button | DISABLED(G) | DISABLED(G) | explains: STOP автономии не установлен |

### telegram_calls

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| api_id | text | OK | OK | GET /api/telegram/calls/status -> 200; field value |
| api_hash | text | OK | OK | GET /api/telegram/calls/status -> 200; field value |
| Сохранить ключи | button | OK | OK | toast:api_id — число, api_hash — 32 символа.; 1 new controls; GET /api/telegram/ |
| Сохранить ключи > × | button | OK | OK | control state; dom |
| Сбросить выбор | button | DISABLED(G) | DISABLED(G) | explains: Собеседник не выбран |
| tc-peer-confirm | toggle | OK(G) | OK(G) | GET /api/telegram/calls/status -> 200; field value |
| имя или @юзернейм | text | OK | OK | GET /api/telegram/calls/status -> 200; field value |
| Найти | button | DISABLED | DISABLED | explains: Сначала подключите аккаунт Telegram |
| @юзернейм | text | OK | OK | GET /api/telegram/calls/status -> 200; field value |
| Выбрать по юзернейму | button | DISABLED | DISABLED | explains: Сначала подключите аккаунт Telegram |
| tc-autosave | toggle | OK | OK | toast:Итоги звонков будут записываться в память; 1 new controls; PUT /api/telegr |
| Позвонить | button | DISABLED(G) | DISABLED(G) | explains: Сначала подключите аккаунт Telegram (раздел «Подключение») |
| Завершить | button | DISABLED(G) | DISABLED(G) | explains: Сейчас нет звонка |
| STOP | button | OK(G) | OK(G) | toast:STOP выполнен: звонки заблокированы до «Продолжить».; 1 new controls; POST |
| STOP > × | button | OK | OK | control state; dom |
| Продолжить | button | DISABLED(G) | DISABLED(G) | explains: STOP звонков не активен |
| Проверить аудиоконтур | button | OK | OK | 1 new controls; GET /api/telegram/calls/status -> 200; GET /api/telegram/calls/s |
| Проверить аудиоконтур > Проверить аудиоконтур | button | DISABLED | - | explains: Проверка уже идёт |
| Диагностика | button | OK(G) | OK(G) | POST /api/telegram/calls/doctor -> 200; GET /api/telegram/calls/status -> 200; G |
| Установить зависимости | button | OK(G) | OK(G) | toast:Установка запущена; 1 new controls; POST /api/telegram/calls/install -> 20 |
| Установить зависимости > × | button | OK | OK | control state; dom |

### chat.html

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Свернуть боковую панель | button | OK | - | 2 new controls; localStorage; control state; dom(near control) |
| Свернуть боковую панель > Развернуть боковую панель | button | ERROR(G) | - | opener not found on replay |
| Свернуть боковую панель > Новый чат (Ctrl+N) | button | ERROR | - | opener not found on replay |
| Новый чат Ctrl N | button | ERROR | - | control vanished after re-render |
| Поиск чатов (Ctrl+K) | text | ERROR | - | control vanished after re-render |
| Проекты 0 | button | ERROR | - | control vanished after re-render |
| Архив | button | ERROR | - | control vanished after re-render |
| Настройки Command Center | link | OK | OK | url:/#/settings; toast:TESTING PERIOD идёт запись действий — сессия 2c300a8909bd |
| Открыть Command Center | link | OK | OK | url:/; toast:TESTING PERIOD идёт запись действий — сессия 2c300a8909bd 32; 81 ne |
| Горячие клавиши и справка | button | OK | OK | opened:modal:Горячие клавиши Ctrl+N	Новый чат Ctrl+K	Поис; 2 new controls; dom |
| Горячие клавиши и справка > Закрыть окно | button | OK | OK | control state; dom |
| Горячие клавиши и справка > Понятно | button | OK | OK | control state; dom |
| Светлая тема | button | OK | OK | 1 new controls; localStorage; control state; dom |
| Светлая тема > Тёмная тема | button | ERROR | OK | opener not found on replay |
| Название чата | text | DISABLED | DISABLED | explains: Название чата |
| Переименовать чат | button | DISABLED | DISABLED | explains: Переименовать чат |
| Экспорт чата в Markdown | button | DISABLED | DISABLED | explains: Экспорт чата в Markdown |
| Закрепить чат | button | DISABLED | DISABLED | explains: Закрепить чат |
| Ещё действия с чатом | button | DISABLED | DISABLED | explains: Ещё действия с чатом |
| Thinking | button | OK | OK | 6 new controls; localStorage; control state; dom |
| Thinking > Закрыть панель Thinking & Actions (Ctrl+.) | button | ERROR | OK | control vanished after re-render |
| Thinking > Кратко | button | OK | OK | control state; dom |
| Thinking > План | button | ERROR | OK | control vanished after re-render |
| Thinking > Действия | button | OK | OK | control state; dom |
| Thinking > Проверка | button | ERROR | OK | control vanished after re-render |
| Thinking > Источники | button | OK | OK | control state; dom |
| Источники | button | OK | ERROR | dom |
| Память | button | OK | ERROR | opened:popover info-pop:Память Bossman  Хранилище памяти ; 1 new controls; GET / |
| Память > Подключить в Command Center → Настройки | link | OK | - | popup |
| Инструменты | button | OK | - | opened:popover info-pop:Инструменты  Auto выбирает агента; GET /api/agents -> 20 |
| Agentic Rave | button | OK | - | 1 new controls; control state; dom |
| Agentic Rave > Agentic Rave | button | OK | - | opened:popover info-pop:Агенты Agentic Rave Локальный аге; 5 new controls; GET / |
| Прикрепить файлы (или перетащите их в окно) | button | DEAD | ERROR | no DOM / URL / request / dialog / toast / storage change |
| Сообщение Bossman | text | OK | OK | field value; control state; dom |
| Auto · Local-first | button | OK | ERROR | opened:pick-menu popover:Auto · Local-first Bossman сам в; control state; dom |
| Голосовой ввод недоступен: Каталог модели Whisper не найден: | button | DISABLED | DISABLED | explains: Голосовой ввод недоступен: Каталог модели Whisper не найден: нужен сущ |
| Отправить (Enter) | button | DISABLED(G) | DISABLED(G) | explains: Отправить (Enter) |

### shell

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Меню | button | - | OK | 48 new controls; dom |
| Меню > Главная | button | - | OK | url:/#/home-v3; 23 new controls; GET /pages/home.js -> 200; GET /icon.svg -> 200 |
| Меню > Агенты | button | - | OK | url:/#/agents; 2 new controls; GET /icon.svg -> 200; GET /api/agents -> 200 |
| Меню > Ждут решения | button | - | OK | url:/#/approvals; GET /icon.svg -> 200; GET /api/approvals -> 200; control state |
| Меню > Чат | button | - | OK | url:/#/chat; 1 new controls; GET /pages/chat.js -> 200; GET /icon.svg -> 200 |
| Меню > Дерево развития | button | - | OK | url:/#/capability-tree; 831 new controls; GET /pages/capability_tree.js -> 200;  |
| Меню > Операторский канал | button | - | OK | url:/#/mission_console; 5 new controls; GET /icon.svg -> 200; GET /pages/mission |
| Меню > Миссии | button | - | OK | url:/#/missions; 2 new controls; GET /icon.svg -> 200; GET /pages/missions.js -> |
| Меню > Конструктор миссий | button | - | OK | url:/#/builder; 1 new controls; GET /icon.svg -> 200; GET /pages/builder.js -> 2 |
| Меню > Цели | button | - | OK | url:/#/objectives; 2 new controls; GET /pages/objectives.js -> 200; GET /icon.sv |
| Меню > Пульт | button | - | OK | url:/#/control; 3 new controls; GET /pages/control.js -> 200; GET /icon.svg -> 2 |
| Меню > File Intelligence | button | - | OK | url:/#/file-intelligence; 1 new controls; GET /icon.svg -> 200; GET /pages/file_ |
| Меню > Video Studio | button | - | OK | url:/#/video-studio; 15 new controls; GET /icon.svg -> 200; GET /pages/video_stu |
| Меню > Веб-дизайн | button | - | OK | url:/#/web_designer; 11 new controls; GET /icon.svg -> 200; GET /pages/web_desig |
| Меню > Браузер | button | - | OK | url:/#/browser; 1 new controls; GET /icon.svg -> 200; GET /pages/browser.js -> 2 |
| Процесс работы > Очистить ленту | button | - | DEAD(G) | GATED control produced no effect even up to the gate: no DOM / URL / request / d |
| Процесс работы > Закрыть | button | - | OK | localStorage; dom |
| Главная | button | - | OK | url:/#/home-v3; 23 new controls; GET /pages/home.js -> 200; GET /icon.svg -> 200 |
| Операторский канал | button | - | OK | url:/#/mission_console; 5 new controls; GET /pages/mission_console.js -> 200; GE |
| Приложения | button | - | OK | url:/#/apps; 24 new controls; GET /pages/apps.js -> 200; GET /icon.svg -> 200 |
| Video Studio | button | - | OK | url:/#/video-studio; 15 new controls; GET /icon.svg -> 200; GET /pages/video_stu |
| Веб-дизайн | button | - | OK | url:/#/web_designer; 11 new controls; GET /icon.svg -> 200; GET /pages/web_desig |

### models

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Добавить модель > Существующий | button | - | OK | control state; dom |

### tasks

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Все 0 | button | - | OK | GET /api/tasks -> 200; GET /api/agents -> 200; control state; dom |

### schedules

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Новое расписание > Ежедневно | button | - | OK | 1 new controls; field value; control state; dom |

### settings

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Тёмная | button | - | OK | POST /api/telegram/models -> 200; POST /api/telegram/models -> 200; GET /api/pro |

### capability-tree

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Настройки Jeff, участники, presentation profile; единую рабо | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Существующий runtime, identity, vault, guard и participant p | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| 37 минут с фейковыми Telegram/моделью: 8/8, 0 ложных рестарт | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Голосовые маршруты и speech; качество реальной интонации и l | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| В отчёте 1 октября описана генерация и рассылка; полный набо | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| В отчёте: 7/7 доставлено с owner approval. | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | same label+class as instance #2; first 2 probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | same label+class as instance #2; first 2 probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | same label+class as instance #2; first 2 probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | same label+class as instance #2; first 2 probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | same label+class as instance #2; first 2 probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | same label+class as instance #2; first 2 probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | same label+class as instance #2; first 2 probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | same label+class as instance #2; first 2 probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Bounded, local Russian speech synthesis for existing Bossman | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Optional offline Russian voice clone using a separate, bound | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Offline speech recognition using the optional upstream faste | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Voice (V2.6, раздел 20) — слой provider-capability, и только | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Реализация и тесты присутствуют; drawer должен показывать эт | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Каталог argparse ниже: объявления подкоманд, не доказанные з | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| список команд; команда зарегистрирована, живой запуск отдель | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| подключение, сборка, модель, агент, режим, бюджеты; команда  | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| последние задачи Bossman; команда зарегистрирована, живой за | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| модели; use — сменить модель текущего агента; команда зареги | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| агенты; с аргументом — выбрать; команда зарегистрирована, жи | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| навыки; с запросом — какие подошли бы к задаче; команда заре | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| инструменты текущего агента и их политика; команда зарегистр | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| поиск по памяти и фактам; команда зарегистрирована, живой за | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| diff последней coding-задачи; команда зарегистрирована, живо | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| coding task через coding path; команда зарегистрирована, жив | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Agentic Rave: несколько агентов на один prompt; команда заре | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| одобрить ожидающее разрешение; команда зарегистрирована, жив | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| отклонить ожидающее разрешение; команда зарегистрирована, жи | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| ожидающие разрешения; команда зарегистрирована, живой запуск | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| пауза задачи; команда зарегистрирована, живой запуск отдельн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| остановить задачу; all — глобальный STOP; команда зарегистри | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| продолжить задачу после паузы; команда зарегистрирована, жив | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| управление компьютером; команда зарегистрирована, живой запу | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| цикл самоулучшения 1.1; команда зарегистрирована, живой запу | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| ключи облачных моделей; команда зарегистрирована, живой запу | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| панель контекста: workspace, задача, инструменты, память, бю | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| сжать беседу в резюме (задачей Bossman); дальше — резюме + н | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| что уйдёт со следующим сообщением: резюме, ходы, размер; ком | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| токены и стоимость задач этой сессии (из Bossman); команда з | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| сохранить беседу в Markdown; команда зарегистрирована, живой | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| проверка: сборка, данные, агент, coding path, память, компью | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| развернуть свёрнутый блок (мысли модели, вывод инструмента); | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| история ввода: показать состояние / включить / выключить; ко | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| очистить экран; команда зарегистрирована, живой запуск отдел | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| выйти (задачи продолжают работу в Bossman); команда зарегист | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Owner journeys и бизнес-сценарии существуют; реальные учётки | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Файлы приложения присутствуют; готовность владельцу проверяе | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Файлы приложения присутствуют; готовность владельцу проверяе | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Файлы приложения присутствуют; готовность владельцу проверяе | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Файлы приложения присутствуют; готовность владельцу проверяе | button | - | SAMPLED | same label+class as instance #2; first 2 probed |
| Файлы приложения присутствуют; готовность владельцу проверяе | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Файлы приложения присутствуют; готовность владельцу проверяе | button | - | SAMPLED | same label+class as instance #2; first 2 probed |
| Файлы приложения присутствуют; готовность владельцу проверяе | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Earning EMULATOR: reads a public remote-job listing (text),  | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| CLI for the earning emulator (bcc/earning_emulator.py). SIMU | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| 82,307% против 72,213% в сохранённом финале. Не внедрён; run | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Один диагностический сценарий прошёл; полного турнира и work | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Пресеты и governor есть; free tier зависит от аккаунта, квот | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Адаптеры, маршрутизация и настройки; наличие адаптера не док | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| cache_intel, context budget, coding limit saver; экономия до | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Проверено вживую на указанном коммите; полная | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Free preset в коде. Доступ, квоты и работоспособность аккаун | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Free preset в коде. Доступ, квоты и работоспособность аккаун | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Free preset в коде. Доступ, квоты и работоспособность аккаун | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Browser runtime, help, tools и action gates. Прогон 2026-10- | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Адаптер и bounded operator; не доказано тотальное управление | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Редактор, задания и экспорт имеют код; проверять конечный ме | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модули студий и review; нуждаются в backend/model runtime дл | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Файлы приложения присутствуют; готовность владельцу проверяе | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Higgsfield Genjutsu: перенос движения из видео на нового пер | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Инструкция агенту: заменить актёров короткого клипа людьми п | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Отчёт о переделке главной страницы из панели разработчика в  | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Итоговый отчёт по второй волне UX и документ приёмки интерфе | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| One deterministic filtergraph compiler for preview and expor | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Video Studio host integration: canonical DB, tasks, commands | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Раздел 10: раскадровка и рецепты кадров для Video Studio. От | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Existing BCC provider transport -> untrusted model draft ->  | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Local editable Shotcut/MLT exchange with an explicit, checke | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| OpenTimelineIO interchange, with explicit preservation/loss  | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Local deterministic diagnostics, timed captions, and optiona | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Evidence-linked local retrieval, B-roll candidates and measu | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Train a separate LoRA command specialist and compare a fixed | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Generation observations, never task-completion evidence. No  | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Local AI generation through stable-diffusion.cpp (Vulkan) —  | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Local Studio wrapper over the existing native ComfyUI implem | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| OpenRouter image/video shapes already used by repository scr | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Persistent per-feature reservations under the existing Gover | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Bossman Vision: a local look at every generated Studio video | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Bounded local ComfyUI adapter using upstream /prompt, /histo | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Генератор сайтов визуального веб-дизайнера: шаблоны, палитры | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| GrapesJS edits the body; Bossman retains the document, versi | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Local AI website creation through the canonical registry and | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| The job pipeline: explicit stages from request to a printabl | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| G-code safety scanner. Generated G-code is executable machin | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Преобразование медиа через ffmpeg. Исходник не трогается ник | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Offline neon trailer renderer for validated Motion Studio sp | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Local Genjutsu test stack: character swap (Object Swap) and  | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Память, контекстные бюджеты, evidence graph, vault и retriev | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| failure_to_case, failure memory, repair/evolution; сохранени | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Аудит 3 октября NOT READY. 154 passed / 2 skipped; три live- | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| AMD/Windows путь не доказан; описанный CUDA training не счит | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | same label+class as instance #2; first 2 probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | same label+class as instance #2; first 2 probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | same label+class as instance #2; first 2 probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | same label+class as instance #2; first 2 probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | same label+class as instance #2; first 2 probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | same label+class as instance #2; first 2 probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Многоагентные сессии и STOP; файловая изоляция и интегратор  | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| CLI/coding sessions существуют; subscription login и общий с | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Durable state, task exchange, mission lifecycle, checkpoint  | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Control plane, provider fleet, economy swarm, remote nodes;  | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Граф строится из БД агентов/оркестров и живых run'ов. Код в  | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Шесть сохранённых стратегий над одним sidecar; честное сравн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Код, который реально находит, дедуплицирует и оценивает SKIL | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Capability manifest; scope: network.read. Проверено вживую н | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Capability manifest; scope: network.read. Проверено вживую н | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Capability manifest; scope: db.read. Проверено вживую на ука | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Capability manifest; scope: vault.read. Проверено вживую на  | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Capability manifest; scope: vault.write. Проверено вживую на | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Capability manifest; scope: mcp.read. Проверено вживую на ук | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Capability manifest; scope: llm.local. Проверено вживую на у | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Capability manifest; scope: llm.cloud.use. Проверено вживую  | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Capability manifest; scope: repo:read. Проверено вживую на у | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Capability manifest; scope: issues:write. Авторизация/live э | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Capability manifest; scope: gmail.readonly. Авторизация/live | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Capability manifest; scope: gmail.send. Авторизация/live эфф | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Capability manifest; scope: calendar.readonly. Авторизация/l | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Capability manifest; scope: calendar.events. Авторизация/liv | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Capability manifest; scope: drive.readonly. Авторизация/live | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Capability manifest; scope: drive.file. Авторизация/live эфф | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Capability manifest; scope: telegram.read. Авторизация/live  | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Capability manifest; scope: telegram.send. Авторизация/live  | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Capability manifest; scope: n8n.read. Авторизация/live эффек | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Capability manifest; scope: n8n.execute. Авторизация/live эф | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Capability manifest; scope: browser.navigate. Авторизация/li | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Capability manifest; scope: browser.input. Авторизация/live  | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Подключение MCP-серверов к общему реестру инструментов и раз | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Проверка scope/approval для каждого capability manifest. Код | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Опциональные локальные движки (whisper, UI-TARS) и их наличи | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Сохранённый audit 2 октября: NO_GO. Нужен единый итоговый SH | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| 4 октября изолированные сценарии описаны как PASS, но после  | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Исторические CI и тестовые отчёты не переносятся на новый SH | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Budget, resources, offline, approvals, provenance, secrets v | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Семантика и live работоспособность не сертифи | button | - | SAMPLED | same label+class as instance #2; first 2 probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Семантика и live работоспособность не сертифи | button | - | SAMPLED | same label+class as instance #2; first 2 probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Семантика и live работоспособность не сертифи | button | - | SAMPLED | same label+class as instance #2; first 2 probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Семантика и live работоспособность не сертифи | button | - | SAMPLED | same label+class as instance #2; first 2 probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Семантика и live работоспособность не сертифи | button | - | SAMPLED | same label+class as instance #2; first 2 probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Семантика и live работоспособность не сертифи | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Семантика и live работоспособность не сертифи | button | - | SAMPLED | same label+class as instance #2; first 2 probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Семантика и live работоспособность не сертифи | button | - | SAMPLED | same label+class as instance #2; first 2 probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Семантика и live работоспособность не сертифи | button | - | SAMPLED | same label+class as instance #2; first 2 probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Семантика и live работоспособность не сертифи | button | - | SAMPLED | same label+class as instance #2; first 2 probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Семантика и live работоспособность не сертифи | button | - | SAMPLED(G) | same label+class as instance #2; first 2 probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Семантика и live работоспособность не сертифи | button | - | SAMPLED | same label+class as instance #2; first 2 probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Модуль найден. Импорт и существующие тесты прошли на указанн | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | same label+class as instance #2; first 2 probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | same label+class as instance #2; first 2 probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | same label+class as instance #2; first 2 probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | same label+class as instance #2; first 2 probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | same label+class as instance #2; first 2 probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | same label+class as instance #2; first 2 probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | same label+class as instance #2; first 2 probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | same label+class as instance #2; first 2 probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | same label+class as instance #2; first 2 probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Найден исходный модуль. Наличие тестов и живой результат про | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| GitHub check 2026-10-05T11:38:52Z; claude/bossman-control-v0 | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| GitHub check 2026-10-04T14:35:13Z; ux/ai-3d-maker-adapter-20 | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| GitHub check 2026-10-04T14:35:13Z; ux/ai-3d-maker-adapter-20 | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| GitHub check 2026-10-04T14:35:13Z; ux/ai-3d-maker-adapter-20 | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| GitHub check 2026-10-04T14:35:13Z; ux/ai-3d-maker-adapter-20 | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Priority queue for verifying the blue («code written») leave | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Sync the capability tree with the repository: add leaves for | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Per-leaf audit of the «code written» (blue) leaves of the ca | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Ссылка в выбранном checkout; не утверждение об установке/инт | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| MIT; handoffs/guardrails/tracing — эталон для nl_orchestra и | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| MIT; durable graph + checkpoint/interrupt — сверить с миссия | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| MIT; типизированные tool-вызовы и eval-хуки — образец для ко | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Роли/процессы для fleet и swarm; только как UX-референс. Ссы | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| MIT; официальный MCP SDK (pypi: mcp) — опора для mcp_runtime | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Apache-2.0; быстрые MCP-серверы для своих коннекторов Bossma | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Справочные MCP-серверы (filesystem, git, fetch) для теста pl | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Apache-2.0; OAuth-коннекторы к сотням сервисов — только с од | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| MIT; надёжные долгие воркфлоу — референс для recovery миссий | button | - | SAMPLED(G) | big page (831 controls): first 4 per kind+class probed |
| Открытый (MIT) визуальный конструктор агентов на TypeScript; | button | - | SAMPLED | big page (831 controls): first 4 per kind+class probed |
| Список всех узлов (поиск и кла > Диалог и ответы | button | - | OK | GET /api/evolution/status -> 200; dom |
| Список всех узлов (поиск и кла > Память участника и согласие | button | - | OK(G) | scroll |
| Список всех узлов (поиск и кла > Паспорта и master parser | button | - | OK | dom |
| Список всех узлов (поиск и кла > Настроение и персона | button | - | OK | GET /api/evolution/status -> 200; GET /api/capability-tree -> 200; dom |
| Список всех узлов (поиск и кла > Admin Panel | button | - | OK(G) | dom |
| Список всех узлов (поиск и кла > Telegram polling и изоляция | button | - | OK | GET /api/capability-tree -> 200; dom |
| Список всех узлов (поиск и кла > Heartbeat · watchdog · rest | button | - | OK(G) | GET /api/evolution/status -> 200; would-send:POST /api/testing/log {"events":[{" |
| Список всех узлов (поиск и кла > Голос · STT · TTS | button | - | OK | dom; scroll |
| Список всех узлов (поиск и кла > Фото · генерация · редактир | button | - | OK | dom; scroll |
| Список всех узлов (поиск и кла > Рассылка | button | - | OK | scroll |
| Список всех узлов (поиск и кла > Звонки | button | - | OK(G) | GET /api/capability-tree -> 200; dom; scroll |
| Список всех узлов (поиск и кла > 24–48 часов непрерывной раб | button | - | OK(G) | dom; scroll |
| Список всех узлов (поиск и кла > pit/behavior_controller.py | button | - | OK | GET /api/capability-tree -> 200; dom; scroll |
| Живой ответ 7,3 с описан 1 октября; непрерывность сегодня не | button | - | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| /memory, /pause_memory, /forget; приватные training.jsonl и  | button | - | ERROR(G) | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Факты, источники, уверенность, checkpoint и consent; не дока | button | - | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| participant/companion profile, behavior scores, roleplay pol | button | - | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Код, тесты и отдельные ветки есть; реальный двусторонний зво | button | - | ERROR(G) | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| В октябрьском аудите soak NOT RUN; заявления о 100% усилении | button | - | ERROR(G) | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Результат не доказан; прежний cold page прогон показал регре | button | - | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Файлы приложения присутствуют; готовность владельцу проверяе | button | - | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Файлы приложения присутствуют; готовность владельцу проверяе | button | - | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Распознавание покерного стола по пикселям → проверенное сост | button | - | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| По сохранённому отчёту 4 октября; текущую конфигурацию ПК зд | button | - | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Pi не заменил PRIMARY; Ornith участвовал в коротком раунде.  | button | - | ERROR(G) | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| В отчёте встречается resident community uncensored Q8; не см | button | - | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Низкий приоритет; память ограничена. Не включать в обязатель | button | - | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Частичная telemetry есть; единый точный дневной отчёт, включ | button | - | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Observe описан; действие WAIT_APPROVAL. Случай ложного сообщ | button | - | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Предложенный ограниченный UX sidecar; не заменяет ядро. | button | - | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Pipeline и документы есть; непрерывный перенос обучения в вы | button | - | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Каталог кандидатов, не установленные работающие приложения. | button | - | ERROR(G) | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| SKILL.md найден; не означает подключение навыка в текущий ru | button | - | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Scene spec -> finished video: voice-over, original score, de | button | - | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Render the Bossman "32 days" promo (tools/promo_video/promo. | button | - | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| JSON карта подготовлена для загрузки. В runtime Bossman ещё  | button | - | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Навык импортирован в каталог (superpowers@5bf4e780, MIT, pro | button | - | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Capability manifest; scope: mcp.execute. Авторизация/live эф | button | - | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Модуль найден. Семантика и live работоспособность не сертифи | button | - | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. |
| Модуль найден. Семантика и live работоспособность не сертифи | button | - | ERROR | TimeoutError: Locator.click: Timeout 4000ms exceeded. |

### video-studio

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Монтаж | button | - | OK | localStorage; control state; dom |
| EN > AI | button | - | OK | localStorage; control state; dom |

### resources

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Поровну | button | - | OK | toast:Правило обновлено; 1 new controls; POST /api/resources/policy -> 200; GET  |

### terminal

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| В проекте | button | - | OK | GET /api/terminal/capabilities -> 200; GET /api/terminal/roots -> 200; GET /api/ |
| C:\Users\asd\AppData\Local\Temp\bcc-uxsweep-bgouthny\data | text | - | OK | 1 new controls; field value; scroll |

### command

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Взять управление > Вернуть агенту | button | - | OK | toast:Готово; POST /api/browser/sessions/1/resume -> 200; GET /api/missions -> 2 |

### images

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Библиотека | button | - | OK | GET /api/images/models -> 200; GET /api/images/storage -> 200; GET /api/images/a |
| Все изображения 0 | button | - | OK | GET /api/images/storage -> 200; GET /api/images/jobs -> 200; GET /api/images/mod |

### mission_console

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Отправить | button | - | OK(G) | toast:Команда пустая; 1 new controls; dom; scroll |
| Продолжи план со следующего ша > Проверь источники последнег | button | - | OK | 1 new controls; field value |

### web_designer

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Открыть проект > Повернуть | button | - | OK | toast:800 × 1280 CSS px · 42% · масштаб меняет только показ; localStorage; field |
| Лендинг Герой-блок, преимущества, цифры и форма связи | button | - | UNVERIFIED | control not found again after re-render (harness could not reach it; not counted |

### objectives

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Новая цель > должно быть true | button | - | OK | already selected/active (no-op by design) |

### jeff-settings

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Сохранить расчёты > Обновить | button | - | OK | GET /api/evolution/status -> 200; GET /api/jeff-settings -> 200; GET /api/jeff-s |

### chat.html

| control | kind | desktop | phone | note |
|---|---|---|---|---|
| Новый чат (Ctrl+N) | button | - | OK | control state; dom |
