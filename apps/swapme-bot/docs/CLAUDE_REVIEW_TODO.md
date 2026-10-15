# REVIEW / TODO — статус после сессии 12.06.2026 (Fable)

## ✅ СДЕЛАНО В ЭТОЙ СЕССИИ (план A-E выполнен целиком)
A. full_audit() (6 групп инвариантов) + /reconcile (admin). reconcile() переиспользует full_audit.
B. /capital + /set_anchor + кнопка 💼 + capital_history. Числа сверены с capital_pnl.py: <сумма>, до цента. CLI тоже исправлен (фильтр entry_type).
C. daily_close 23:55: сверка → алерт, снимок капитала, бэкап БД файлом. /backup был ранее.
D. ux_patch интегрирован полностью (быстрый ввод, меню v2, reply в один тап, P&L кратко/детали, /undo, /cancel). Бонусом: атомарный cancel_order + запрет completed→cancelled мимо него; is_admin/is_authorized guards на 13 хендлерах.
E. Excel ИТОГО по валютам; «🗂 История» добавлена в меню; сделка Odtat dolg — скрипт add_odtat_deal.py (запустить у живой базы!).

## ⏳ РУЧНЫЕ ШАГИ ПОЛЬЗОВАТЕЛЯ
1. PowerShell в C:\swapme_bot:
   del .git\index.lock, .git\HEAD.lock, .git\objects\maintenance.lock
   git reset --hard claude-jun12
   git push origin main
   git branch -D probe
2. python -X utf8 add_odtat_deal.py go   (на машине/хосте с живой swapme.db)
3. Перезапуск бота (init_db создаст capital_history). Если Railway — git push задеплоит; локальный инстанс выключить.

## 📋 BACKLOG (по приоритету, из ревью 12.06)
1. TTL/экспирация курсов: get_rate отдаёт manual-курсы любой давности (CZK/USD от 27.03!). Идея: warn при курсе старше 1ч, manual — флаг до явного сброса.
2. VWAP realized PnL: продажи при inventory≈0 выпадают (нет opening-записей); кросс-конвертация исторических продаж по текущему споту (1661-1674 в старой нумерации).
3. TZ: orders/ledger = local, rates/equity/audit = UTC ('now') → rate_at смещение ~2ч, границы дня едут.
4. fee_income: семантика «fee внутри to_amt» не подтверждена (спящий код, fee всегда 0) — СПРОСИТЬ пользователя.
5. compute_dynamic_pricing: abs() при отрицательном USDT-балансе инвертирует рекомендацию спреда.
6. _margin_to_usdt/_vol_usdt: при отсутствии курса фиат считается 1:1 к USDT (завышение ×20) — после TTL-фикса станет актуальнее.
7. hide_data() не чекпойнтит WAL перед копией.
8. Декоратор авторизации на ВСЕ info-callback'и (balance/stats/receipts) — сейчас точечно на чувствительных.

## Уроки
- full_audit поймал реальный mid-state баг cancel_order прямо в смоук-тесте — инварианты работают.
- Песочница: git/sqlite-write на маунте ломаются — см. GOTCHAS в AI_CONTEXT.md.
