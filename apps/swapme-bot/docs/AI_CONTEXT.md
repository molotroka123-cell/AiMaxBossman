# AI_CONTEXT — SwapMe Bot (машинное онбординг-резюме, обновлено 2026-06-12 v2)

ROLE: Telegram-бот обменника (USDT/EUR/USD/CZK), Прага. Один пользователь-владелец  + менеджеры.
STACK: Python 3.14 local / 3.11 Docker, python-telegram-bot==22.7, SQLite WAL (swapme.db), APScheduler 3.10.4.
ENTRY: C:\swapme_bot\bot.py (~5300 строк, монолит). ЗАМОРОЖЕН: "bot_final (4).py" (оригинал, не трогать).
REPO: github.com/molotroka123-cell/swapme-bot (private, main). gh CLI авторизован. .gitignore = whitelist.
RUN: python -X utf8 bot.py; env: BOT_TOKEN (required), DB_PATH, ADMIN_IDS, CHANNEL_ID, XE_API_ID/KEY. Windows консоль = cp1252 → ВСЕГДА -X utf8.
HOSTING: пользователь задеплоил на Railway (12.06). ⚠️ Локальный и облачный бот одновременно = конфликт getUpdates. База на Railway загружается отправкой swapme.db боту в Telegram (restore-флоу в коде).

DATA MODEL:
- orders: from_* = клиент ОТДАЁТ нам, to_* = клиент ПОЛУЧАЕТ. rate "A/B" = 1A = rate×B. state machine в bot.py; completed→cancelled ТОЛЬКО через cancel_order() (атомарный реверс).
- ledger_entries: единственный источник балансов. Типы: inflow/outflow/reversal/fee_income/expense/liq_own/liq_credit/opening/reserve.
- capital_anchor (id=1: 2026-05-20 14:01:24, <старт USDT>) + capital_history (ночные снимки). Метод капитала ВСТРОЕН в бота: compute_capital() = старт + P&L(ledger ПОСЛЕ якоря, ТОЛЬКО inflow/outflow/reversal/fee_income) − expenses ПОСЛЕ якоря. CZK по rate_history 'XE:USD/CZK' курсу дня, EUR через 'USDT/EUR'. capital_pnl.py (CLI) тоже исправлен — числа сходятся (сверено: <сумма>).
- expenses id 3-10: исторические, БЕЗ ledger-пары (full_audit даёт WARN — это норма). Новые через бота пишут обе записи.
- balances USDT и EUR — НЕТ opening-записей, «поток с нуля». Реальный капитал — /capital.

СДЕЛАНО 12.06 (сессия Fable, коммиты d9387d5..HEAD):
A) full_audit() — 6 групп инвариантов (ноги ордеров, сторно, ref_id, пары expenses/liq, NaN) + /reconcile (admin). reconcile() поверх него (CRIT блокируют, WARN нет).
B) /capital, /set_anchor, кнопка 💼 (меню+reply), capital_history, Δ к прошлому дню.
C) daily_close 23:55 = full_audit (алерт при CRIT) + снимок капитала + бэкап БД в TG.
D) ux_patch ИНТЕГРИРОВАН: быстрый ввод «500 usdt 10400 czk» (catch-all, парсер протестирован), меню v2 (разделы), reply-кнопки в один тап, P&L кратко/детали, /undo + кнопка ↩ в чеках, /cancel, FSM-fallback'и закрывают диалог. cancel_order АТОМАРЕН (фикс CRIT из ревью). Авторизация: is_admin на всех admin-callback'ах (gencode/dates/delete/cspread/users/last5/audit), is_authorized на manual_rate/converter/note/report.
E) Excel ИТОГО по валютам; «История» в меню (был мёртвый callback); add_odtat_deal.py.

⏳ ЖДЁТ ПОЛЬЗОВАТЕЛЯ:
1. git: locks чистятся только на Windows → del .git\index.lock,.git\HEAD.lock,.git\objects\maintenance.lock; git reset --hard claude-jun12; git push origin main (ветка claude-jun12 = вся работа; коммит 6364c25 на старом main выбросить).
2. Сделка Odtat dolg: python -X utf8 add_odtat_deal.py go — НА машине с живой базой .
3. Перезапустить бота с новым кодом (Railway: push задеплоит; локально выключить второй инстанс).

BACKLOG (из ревью, не делалось): TTL на rates/manual-курсы; VWAP при нулевом инвентаре; TZ-унификация (orders local vs rates UTC); fee_income семантика (спящий код, спросить юзера); compute_dynamic_pricing abs() при отрицательном балансе; _margin_to_usdt fallback 1:1 при отсутствии курса.

GOTCHAS:
- Из песочницы Claude: git unlink/sqlite-write на маунте НЕ работают → плумбинг-коммиты (GIT_INDEX_FILE=/tmp/...) и запись файлов через cat. Отчёты суб-агентов проверять по БД.
- Деплой в облако → локального бота ВЫКЛЮЧИТЬ. PTB 22.7 (disable_web_page_preview ещё работает).
