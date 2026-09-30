# TZ-10 — UX и управление миссиями (6 → 10)

Находки: UX-01..UX-05, OBS-05. Инварианты: INV-5 (UI показывает ту же цепочку, что и журнал).
Границы: не редизайн; словарь статусов расширяется ровно двумя значениями (см. TZ-01 §2.3).

## 1. Текущее состояние
- Vanilla JS, 27 страниц (`ui/pages/*.js`), `components.js` (статусы, модалки, тосты), `api.js` (ApiError со статусом), `thinking.js`, `testing.js`.
- Статусы: `draft|queued|running|paused|waiting_approval|completed|failed|stopped` (`components.js:256-270`); `blocked` встречается только в `home.js:528` и `trading_lab.js`.
- Доступность: `aria-` — 6 вхождений в `components.js`, 19 в `mission_console.js`, 0 в 20 страницах.
- Тесты: `test_ux2_pages_sweep` (все страницы рендерятся, кнопки работают, мобильный вьюпорт), `test_ux2_desktop` (реальный Chromium), `test_testing_period` (dead-click в реальном браузере).
- Ожидающая задача #84 «UX x.1: двухуровневая навигация, шрифты, иконки».

## 2. Требования

### 2.1 Правдивые состояния (UX-01) — MUST
1. `statusTone/statusLabel`: `blocked → 'warn', 'заблокировано'`, `capability_unavailable → 'err', 'нет исполнителя'`; фильтры на странице «Задачи» (`pages.js:27-33`) получают группы «Заблокированы» и «Нет исполнителя».
2. Карточка задачи показывает `reason` из последнего `evaluation.completed`/`task.finalized` (`action_contract/…`, `action_gate/no_verified_action`, `deadline_missed`) человеческим текстом + ссылкой «что нужно, чтобы продолжить» (выдать инструмент / одобрить / включить рантайм).
3. Панель улик: для `completed` — список подписанных receipts (способность, цель, наблюдение, время); для не-completed — какой именно способности не хватает улики. Это прямая проекция INV-1 в UI.

### 2.2 Доступность (UX-02) — MUST
1. Модалки: `role="dialog"`, `aria-modal`, `aria-labelledby`, ловушка фокуса, `Esc`, возврат фокуса.
2. Все `button` без текста — `aria-label`; статус-бейджи — `role="status"`; списки задач — `role="list"/"listitem"`; live-регионы для тостов (`aria-live="polite"`).
3. Клавиатура: все кликабельные `div.card.clickable` → `tabindex="0"` + `Enter/Space`.
4. Контраст ≥ 4.5:1 (WCAG AA) — проверить токены темы автоматически (тест на `getComputedStyle` в Playwright для 10 пар фон/текст).
5. Тест: `axe-core` через Playwright (`@axe-core/playwright` не нужен — достаточно инъекции `axe.min.js` из devDependencies) — 0 violations уровня serious/critical на всех 27 страницах.

### 2.3 Экран «Что делает Bossman» (UX-05) — MUST
Страница `control_plane.js` поверх `GET /api/control-plane` (TZ-08 §2.5): четыре колонки — «Требует вас» (attention, по возрасту), «В работе» (missions/runs с агентом и узлом), «Заблокировано / нет исполнителя» (с причиной), «Бюджет» (осталось, burn-rate, ETA). Обновление по WebSocket-событиям, fallback — опрос 5 с. Никаких новых виджетов сверх этого.

### 2.4 Детектор dead-click (UX-04) — MUST
= TZ-08 §2.6. Дополнительно: кнопки, запускающие долгие операции, MUST переходить в `aria-busy="true"` + `disabled` до ответа — это и честный UX, и сигнал детектору.

### 2.5 Одобрения — SHOULD
Страница «Ждут решения»: показывать `effect`, `capability`, `target`, `fence`, кто запросил, сколько ждёт; пакетное одобрение однотипных (одна способность, один target-домен) — одним действием с явным списком; отказ — с причиной, которая попадает в `task.finalized.reason`.

### 2.6 i18n-каркас (UX-03) — SHOULD
Строки в `ui/i18n/ru.js` как объект `t('key')`; без перевода на другие языки, но с единым словарём — это обязательное условие для тестов на тексты и для будущей локализации. Миграция — по страницам, начиная с `components.js`.

### 2.7 Производительность восприятия — SHOULD
Для маршрутов с известным cold-start (`/api/apps`, `/api/models`) — skeleton-состояние и подпись «первая загрузка после запуска, до 15 с» (данные из `/api/latency`, TZ-08); повторные — без skeleton. Не оптимизация бэкенда (PERFORMANCE_FOLLOWUP остаётся DEFERRED), а честное ожидание.

### 2.8 Двухуровневая навигация (#84) — MAY
Выполнять после 2.1–2.4; не смешивать в один коммит с изменениями состояний.

## 3. Логика/математика
- Возраст «требует вас» — единственный ключ сортировки attention: `age = now − since`; порог подсветки — 15 мин (из тестовой сессии: владелец ждал ответа ≤ 27 с на API и ≥ 15 мин на аппрув, что и было «ничего не происходит»).
- Skeleton показывается, если `p95_cold(route) > 2 с`; порог совпадает с SLO warm из TZ-08.
- Ложные dead-click после исправления детектора: ожидаемая доля ≤ 2 % кликов (сейчас 4 из ~120 = 3.3 % при том, что как минимум 3 — ложные).

## 4. Приёмка
1. `test_status_vocabulary_matches_backend` — множество статусов в `components.js` ⊇ CHECK-ограничения `tasks.status`.
2. `test_task_card_shows_missing_evidence` (Playwright + FakeAdapter): задача `failed/action_contract` → на карточке причина и подсказка.
3. `test_axe_no_serious_violations_all_pages`.
4. `test_modal_focus_trap_and_escape`.
5. `test_control_plane_page_renders_attention_first`.
6. `test_busy_buttons_not_dead` — кнопка с `aria-busy` не порождает `ui.dead_click`.
7. `test_batch_approve_same_capability`.
8. Регрессия `test_ux2_pages_sweep`, `test_ux2_desktop` зелёные.

## 5. Чек-лист 10/10
- [ ] `blocked`/`capability_unavailable` в словаре, причина и подсказка на карточке, панель улик
- [ ] aria/фокус/клавиатура/контраст, axe = 0 serious
- [ ] страница control-plane
- [ ] `aria-busy` + детектор на мутациях
- [ ] пакетные одобрения с причинами
- [ ] i18n-каркас
- [ ] skeleton по данным задержек
