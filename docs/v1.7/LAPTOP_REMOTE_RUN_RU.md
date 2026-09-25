# Bossman 1.7 — laptop remote run

## Зачем

Пока AI Max занят закрытием 1.5/1.6, PIT разрабатывается и тестируется на ноутбуке. Тяжёлые локальные модели здесь не являются зависимостью.

## Runtime profile

- Telegram/PIT backend: тот же Bossman Command Center.
- local model endpoints: unavailable.
- primary reasoning slot: GLM-5.3 через настроенный owner-approved provider.
- fallback slots: только явно allowlisted zero-cost/free endpoints.
- paid route: OFF.
- web: ON для запросов, где нужна свежесть.
- computer/shell: не включаются в Telegram registry вообще.
- persona storage: <Bossman data_dir>/personalities, не отдельный backend.

Конкретные remote model IDs и тарифы не фиксировать навсегда в коде: provider catalog проверяет их при запуске.

## Переменные экспериментального профиля

BOSSMAN_V17_PIT=1
BOSSMAN_V17_TELEGRAM=1
BOSSMAN_V17_PERSONA_MEMORY=1
BOSSMAN_V17_DISCOVERY=1
BOSSMAN_V17_LOCAL_MODELS=0
BOSSMAN_V17_ALLOW_PAID=0
BOSSMAN_V17_COLLECTION_MODE=high_recall

Секреты задаются через существующий Bossman secret/provider mechanism. Не коммитить API keys или Telegram token.

## Завтрашний порядок

1. Fetch только ветку 1.7 и проверить, что 1.5/1.6 не изменяются.
2. Запустить PIT CI.
3. Доделать интеграцию bcc.pit с существующим Telegram surface/provider registry.
4. Создать отдельного Telegram bot через BotFather только после owner approval.
5. Token положить в существующее локальное secret storage.
6. Allowlist сначала содержит только owner ID.
7. 30–50 owner messages: chat, web, code, images/files, corrections.
8. Проверить /memory, /forget, /export_me, /delete_me, restart.
9. Запустить cross-user adversarial tests с двумя synthetic IDs.
10. Только после этого добавить первого реального тестера.

## Переход на AI Max

После первого успешного ответа 1.7 на ноутбуке переносим тот же PIT runtime на AI Max.

- BOSSMAN_V17_LOCAL_MODELS=1;
- BOSSMAN_V17_ROUTE_MODE=local_first_auto;
- Jev сам выбирает локальную модель и переключает локальные endpoints;
- выбор локальной модели не требует отдельного подтверждения на каждый запрос;
- обычный web search/read для ответа также работает автоматически;
- после принятого onboarding memory extraction и compaction не спрашивают владельца на каждый факт;
- заранее разрешённый zero-cost cloud fallback может работать автоматически по privacy policy;
- платный маршрут не включается молча и остаётся отдельной политикой.

Подтверждения сохраняются только там, где Bossman и так считает действие внешним или значимым. Routine inference и локальная персонализация работают без лишних кликов.

Telegram gateway, persona storage и dataset format не меняются.
