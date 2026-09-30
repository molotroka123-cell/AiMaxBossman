# DECISIONS (активные архитектурные решения)

- **DEC-001** Один Resource Brain. Sandbox (Этап 8) и Video (Этап 7) берут допуск
  через `bossman.resource_brain.BRAIN`. Второго не заводим.
- **DEC-002** Один поисковый/памятный план. Search (Этап 5) — поверх
  `context_engine` (Этап 2.222). Второго RAG/вектор-стора нет. Легаси pgvector
  `agent_memory_index` — НЕ поисковый индекс.
- **DEC-003** Один Gateway (Этап 3). Модели вызываются только через
  `llm.chat`/`GatewayClient`; подсистемы не открывают провайдерские сокеты.
- **DEC-004** Облачная политика энфорсится Gateway'ем: заголовок
  `X-Bossman-Cloud-Allowed` + флаг `cloud` у backend + фильтрация маршрутов.
  Угадывание облачности по имени алиаса убрано.
- **DEC-005** Общие швы вместо правки api.py на каждую подсистему: `errors.py`
  (таксономия), `lifecycle.py` (реестр подсистем), `correlation.py` (id),
  `obs.py` (JSON-лог + редакция секретов).
- **DEC-006** Sandbox fail-closed: риск и режим задают минимальный
  `IsolationTier`; недостижимый tier → `IsolationUnavailable`, без даунгрейда.
- **DEC-007** Sandbox OFF=OFF через `BOSSMAN_SANDBOX_ENABLED` (дефолт выкл):
  подсистема на start() не поднимает рантайм/воркеров.
- **DEC-008** Секреты в песочницу только через брокер (grant/scope/TTL/binding);
  `redeem` — операция control plane, песочница получает id гранта, не материал.
- **DEC-009** Failover Gateway только на 5xx/транспорт/429|408|425. Прочие 4xx —
  ошибка запроса: не переключаем маршрут (в т.ч. на облако) и не гасим health.
- **DEC-010** Уплотнение контекста не стирает историю при пустой сводке; сводки
  сливаются, а не заменяются.
- **DEC-011** Один писатель на проект: межпроцессный advisory-лок Postgres в
  `projects.run_project`.
- **DEC-012 (V2)** LLM — компонент рассуждения, не рантайм исполнения; единственный
  путь исполнения остаётся `typed action → policy → approval → executor`.
- **DEC-013 (V2)** Контекст — задача constrained optimization (информационная
  плотность на токен), а не максимизация размера.
- **DEC-014 (V2)** P0 security и P1 objective — обязательный контекст, не кандидаты
  рейтинга; compression не может их молча удалить.
- **DEC-015 (V2)** Авторизация скоупа и временная валидность предшествуют
  рейтингованию релевантности; retrieved-текст — DATA, не authority.
- **DEC-016 (V2)** Рабочее состояние — append-only versioned rows (каждая версия =
  чекпоинт) с optimistic concurrency; chat history состоянием не является.
- **DEC-017 (V2)** Маршрутизация моделей будет evidence-driven (scorecard-телеметрия
  `ModelScorecardEvent`), а не по именам моделей.
