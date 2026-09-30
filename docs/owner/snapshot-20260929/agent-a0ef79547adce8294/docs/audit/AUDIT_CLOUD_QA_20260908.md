# Cloud QA аудит — 2026-09-08 (~5-минутный прогон, всё через BOSSMAN, только cloud-модели)

Сервер: workspace Command Center на `http://127.0.0.1:8800` (поднят `python -m bcc.app`, живёт отдельно от окна).
Миссия №2 `Twitch K1mb6a + BTC график (OpenRouter cloud)`: running, KPI twitch_done=1, btc_done=1 (2/3).

## 1. Twitch K1mb6a (браузер Боссмана, сессия 7)
- Канал найден: `https://www.twitch.tv/k1m6a`, title «K1m6a - Twitch»
- Статус: **LIVE** (чат активен, качество 360P30), 46.1K followers, категория Crypto
- Описание: «3X BACK TO BACK TO BACK MT GOX TRADING CHAMPION…»
- Скриншот-улика сохранён Боссманом (data/browser) + копия у агента

## 2. BTC/USD (браузер Боссмана)
- Spot (api.coinbase.com через браузер): **78939.085 USD**
- Неделя (306 точек, 31.08–07.09.2026): min **76219.995**, max **82276.455**, последний **79213.405** (+0.34%)
- Negative controls (честно, не обходилось):
  - coinbase.com и macrotrends.net — Cloudflare Turnstile, капча не решалась (политика)
  - blockchain.com — canvas сломан в headless Chromium («drawImage broken») + затем rate limit

## 3. OpenRouter cloud
- Ключ владельца подключён (валиден, vault, в git/логах ключа нет), provider 2, каталог: **428 моделей**
- Закреплена бесплатная: `cohere/north-mini-code:free` (model 3, tools заявлены)
- Probe model 3: **FAIL** — chat/tools/streaming verified=false, пустые ответы (free tier нестабилен)
- Probe `z-ai/glm-5.3` (model 2): chat/tools/structured_output **OK**, streaming **FAIL** (0 chunks)
- Smoke-задача №31 на бесплатной модели поставлена в очередь (результат — следующим прогоном)

## 4. Три системы — три cloud-агента (модель GLM 5.3, локальная память не тронута)
- Агент 4 `Video QA` → задача 28: running
- Агент 5 `WebDesign QA` → задача 29: completed (см. находку B2)
- Агент 6 `Apps QA` → задача 30: completed (см. находку B2)
- Агент 7 `FreeModel QA` (бесплатная модель) → задача 31: queued
- Агент 8 `Apps Toggle QA` → переключение 9 apps ниже
- Проверки API от лица пользователя: video-studio capabilities/projects **OK** (проектов: 1),
  web-designer templates **OK** (6) / projects **OK** (1), apps list **OK** (9)

## 5. Apps вкл/выкл по очереди (агент 8, 14.6 c на всё)
Все 9 (`ai-webcam-vision`, `ai-3d-maker`, `social-farm`, `solana-volume-suite`, `bossman-accountant`,
`exam-trainer-ai`, `file-commander-mini`, `pc-autopilot-mini`, `travel-architect`):
start/stop → **HTTP 409**, чтение process → OK.
Причина: `BOSSMAN_APPS_CONTROL_ENABLED != 1`. Фикс: выставить флаг и перезапустить Command Center.

## Баги от лица пользователя
- **B1.** `bcc.desktop` гасит сервер при закрытии Chrome-окна (finally: started.stop). Два падения за вечер.
  Фикс: держать сервер отдельно (`python -m bcc.app`), окно — отдельно. Так и сделано.
- **B2.** Cloud-агенты с browser-инструментами не могут тестировать локальный UI: loopback/`bossman.local`
  запрещены политикой браузера (SSRF-защита). E2E UI-тесты облаком невозможны — нужен локальный прогон.
  Задачи 29/30 честно вернули «нужен публичный URL».
- **B3.** Apps Control выключен флагом (см. §5) — вкл/выкл из UI/API невозможен до рестарта с флагом.
- **B4.** Streaming-пробы падают и на платной (GLM 5.3), и на бесплатной модели — смотреть capability_probe/streaming.
- **B5.** Бесплатная `cohere/north-mini-code:free` не отвечает на probe (пусто) — для задач не годится «как есть».

## Разгрузка
Миссия №1 (чужая, висела running) — остановлена. Браузер-сессия 7 — закрыта. Локальный инференс не использовался.
