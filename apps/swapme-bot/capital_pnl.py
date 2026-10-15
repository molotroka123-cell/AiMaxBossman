"""
capital_pnl.py — НОВЫЙ ОСНОВНОЙ МЕТОД ПОДСЧЁТА КАПИТАЛА (anchored capital / P&L).

Идея (как просил пользователь):
  • Метод привязан к ЯКОРНОМУ ДНЮ (по умолчанию 2026-05-20), когда вручную
    подбили стартовую ликвидность в USDT.
  • Считает ТОЛЬКО со дня якоря вперёд — всё, что было до якоря, уже «зашито»
    в стартовую цифру и повторно не учитывается.
  • Капитал(сейчас) = СТАРТ_USDT
                     + торговый P&L по сделкам с датой >= якоря  (в USDT)
                     − расходы с датой >= якоря                  (в USDT)
  • Расходы могут быть в USDT или CZK; CZK пересчитывается в USDT по курсу XE
    того дня, когда расход был сделан (rate_history, pair='XE:USD/CZK').

Торговый P&L = сумма всех движений по ledger_entries (since якорь), где каждое
движение оценено в USDT по курсу его дня. Поскольку у каждой сделки приход (+)
и расход (−), сумма двух ног в USDT = маржа сделки. Сумма по всем сделкам = P&L.

СТАРТОВУЮ ЛИКВИДНОСТЬ пользователь введёт позже -> set_anchor(start_usdt, ts).
Пока якорь не задан, capital() вернёт None и попросит его задать.

Запуск отчёта:  python capital_pnl.py
Задать якорь:   python capital_pnl.py set-anchor <USDT> ["YYYY-MM-DD HH:MM:SS"]
Добавить расход python capital_pnl.py add-expense <USDT|CZK> <amount> "YYYY-MM-DD" "note"
"""
import sqlite3, sys, io, datetime as dt

DB_PATH = r"C:\swapme_bot\swapme.db"

# ── Конфиг якоря по умолчанию ────────────────────────────────────────────────
# Время сообщения-подбивки ликвидности 20.05 ("294600czk 27900 usdt").
# Сделки СТРОГО ПОСЛЕ этого момента учитываются методом.
DEFAULT_ANCHOR_TS = "2026-05-20 14:01:24"

# Валюты
USDT, USD, CZK, EUR = "USDT", "USD", "CZK", "EUR"


# ── Подключение и схема ──────────────────────────────────────────────────────
def connect():
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""CREATE TABLE IF NOT EXISTS capital_anchor (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        anchor_ts TEXT NOT NULL,
        start_usdt REAL NOT NULL,
        note TEXT,
        created_at TEXT NOT NULL)""")
    # Используем существующую таблицу expenses (ccy, amount, note, created_at).
    con.execute("""CREATE TABLE IF NOT EXISTS expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ccy TEXT, amount REAL, note TEXT, tg_id INTEGER,
        manager TEXT, ledger_id INTEGER, created_at TEXT)""")
    return con


def get_anchor(con):
    """Последний заданный якорь -> (anchor_ts, start_usdt, note) или None."""
    r = con.execute("SELECT anchor_ts, start_usdt, note FROM capital_anchor "
                    "ORDER BY id DESC LIMIT 1").fetchone()
    return r  # None если якорь ещё не задан


def set_anchor(con, start_usdt, anchor_ts=DEFAULT_ANCHOR_TS, note="manual"):
    now = _now()
    con.execute("INSERT INTO capital_anchor (anchor_ts, start_usdt, note, created_at) "
                "VALUES (?,?,?,?)", (anchor_ts, float(start_usdt), note, now))
    con.commit()
    return (anchor_ts, float(start_usdt), note)


# ── Курсы: всё оцениваем в USDT ─────────────────────────────────────────────
def xe_czk_per_usd(con, date_str):
    """CZK за 1 USD по XE на дату (ближайший снимок <= даты, иначе ближайший)."""
    d = date_str[:10]
    r = con.execute("""SELECT rate FROM rate_history WHERE pair='XE:USD/CZK'
                       AND date(recorded_at) <= ? ORDER BY recorded_at DESC LIMIT 1""",
                    (d,)).fetchone()
    if r: return r[0]
    r = con.execute("""SELECT rate FROM rate_history WHERE pair='XE:USD/CZK'
                       ORDER BY abs(julianday(recorded_at)-julianday(?)) LIMIT 1""",
                    (date_str,)).fetchone()
    if r: return r[0]
    # фолбэк на оперативные курсы бота
    r = con.execute("SELECT rate FROM rates WHERE pair IN ('USD/CZK','CZK/USD')").fetchone()
    return r[0] if r else 20.8  # последний разумный фолбэк


def usdt_per_eur(con, date_str):
    """Сколько USDT в 1 EUR на дату (через rate_history pair='USDT/EUR' = EUR за 1 USDT)."""
    r = con.execute("""SELECT rate FROM rate_history WHERE pair='USDT/EUR'
                       AND recorded_at <= ? ORDER BY recorded_at DESC LIMIT 1""",
                    (date_str,)).fetchone()
    if r and r[0]:                       # rate = EUR за 1 USDT  ->  USDT за 1 EUR = 1/rate
        return 1.0 / r[0]
    r = con.execute("""SELECT rate FROM rate_history WHERE pair='USDT/EUR'
                       ORDER BY abs(julianday(recorded_at)-julianday(?)) LIMIT 1""",
                    (date_str,)).fetchone()
    if r and r[0]:
        return 1.0 / r[0]
    return 1.16                          # фолбэк ~EUR/USD


def to_usdt(con, ccy, amount, date_str):
    """Оценка суммы в USDT по курсу указанной даты."""
    if ccy in (USDT, USD):               # USD считаем 1:1 к USDT (допущение)
        return amount
    if ccy == CZK:
        return amount / xe_czk_per_usd(con, date_str)
    if ccy == EUR:
        return amount * usdt_per_eur(con, date_str)
    return amount                        # неизвестная валюта — как есть


# ── Торговый P&L и расходы со дня якоря ──────────────────────────────────────
def realized_trading_pnl(con, since_ts):
    """Сумма всех ledger-движений (ts > since), оценённых в USDT по курсу их дня."""
    rows = con.execute("SELECT ccy, amount, ts FROM ledger_entries WHERE ts > ? "
                       "AND entry_type IN ('inflow','outflow','reversal','fee_income')",
                       (since_ts,)).fetchall()
    total = 0.0
    by_ccy = {}
    for ccy, amount, ts in rows:
        v = to_usdt(con, ccy, amount, ts)
        total += v
        by_ccy[ccy] = by_ccy.get(ccy, 0.0) + v
    return total, by_ccy, len(rows)


def total_expenses_usdt(con, since_ts):
    """Сумма расходов (created_at > since) в USDT по курсу дня расхода."""
    rows = con.execute("SELECT ccy, amount, note, created_at FROM expenses "
                       "WHERE created_at > ?", (since_ts,)).fetchall()
    total = 0.0
    detail = []
    for ccy, amount, note, created in rows:
        v = to_usdt(con, ccy, abs(amount), created)
        total += v
        detail.append((created, ccy, amount, v, note))
    return total, detail


def add_expense(con, ccy, amount, date_str, note=""):
    """Добавить расход. ccy in {USDT, CZK} (EUR/USD тоже поддержаны). date 'YYYY-MM-DD'."""
    ccy = ccy.upper()
    assert ccy in (USDT, CZK, EUR, USD), f"Валюта расхода {ccy} не поддержана"
    created = date_str if len(date_str) > 10 else f"{date_str} 12:00:00"
    con.execute("INSERT INTO expenses (ccy, amount, note, manager, created_at) "
                "VALUES (?,?,?,?,?)", (ccy, float(amount), note, "capital_pnl", created))
    con.commit()
    usdt = to_usdt(con, ccy, float(amount), created)
    return usdt


# ── Капитал ──────────────────────────────────────────────────────────────────
def capital(con):
    """Возвращает dict с разбивкой или None если якорь не задан."""
    a = get_anchor(con)
    if not a:
        return None
    anchor_ts, start_usdt, note = a
    pnl, pnl_by_ccy, nlegs = realized_trading_pnl(con, anchor_ts)
    exp, exp_detail = total_expenses_usdt(con, anchor_ts)
    cap = start_usdt + pnl - exp
    return {
        "anchor_ts": anchor_ts, "start_usdt": start_usdt, "anchor_note": note,
        "trading_pnl_usdt": pnl, "pnl_by_ccy": pnl_by_ccy, "ledger_legs": nlegs,
        "expenses_usdt": exp, "expenses_detail": exp_detail,
        "capital_usdt": cap,
    }


# ── Утилиты ──────────────────────────────────────────────────────────────────
def _now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def report(con):
    c = capital(con)
    if c is None:
        print("⚠️  Якорь не задан. Введи стартовую ликвидность 20.05:")
        print('    python capital_pnl.py set-anchor <USDT> ["2026-05-20 14:01:24"]')
        # Покажем, что метод УЖЕ умеет считать (P&L/расходы), чтобы было видно работу:
        pnl, by, n = realized_trading_pnl(con, DEFAULT_ANCHOR_TS)
        exp, _ = total_expenses_usdt(con, DEFAULT_ANCHOR_TS)
        print(f"\n   (предпросмотр со дня {DEFAULT_ANCHOR_TS})")
        print(f"   Торговый P&L:  {pnl:+,.2f} USDT  ({n} ledger-движений)")
        print(f"   Расходы:       {exp:,.2f} USDT")
        print(f"   Капитал = СТАРТ {pnl-exp:+,.2f} USDT (после ввода старта)")
        return
    print("═" * 58)
    print("  ОСНОВНОЙ КАПИТАЛ (anchored метод)")
    print("═" * 58)
    print(f"  Якорь:            {c['anchor_ts']}  ({c['anchor_note']})")
    print(f"  Старт-ликвидность:{c['start_usdt']:>14,.2f} USDT")
    print(f"  Торговый P&L:     {c['trading_pnl_usdt']:>+14,.2f} USDT   ({c['ledger_legs']} ног)")
    for ccy, v in sorted(c["pnl_by_ccy"].items()):
        print(f"       из {ccy:<4}     {v:>+14,.2f} USDT")
    print(f"  Расходы:          {-c['expenses_usdt']:>+14,.2f} USDT")
    for created, ccy, amt, v, note in c["expenses_detail"]:
        print(f"       {created[:10]} {amt:>10,.0f} {ccy:<4} = {v:>9,.2f} USDT  {note or ''}")
    print("─" * 58)
    print(f"  ИТОГО КАПИТАЛ:    {c['capital_usdt']:>14,.2f} USDT")
    print("═" * 58)


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    con = connect()
    args = sys.argv[1:]
    if not args:
        report(con); return
    cmd = args[0]
    if cmd == "set-anchor":
        ts = args[2] if len(args) > 2 else DEFAULT_ANCHOR_TS
        a = set_anchor(con, args[1], ts)
        print(f"Якорь задан: {a[0]}  старт = {a[1]:,.2f} USDT")
        report(con)
    elif cmd == "add-expense":
        ccy, amount, date_str = args[1], args[2], args[3]
        note = args[4] if len(args) > 4 else ""
        usdt = add_expense(con, ccy, amount, date_str, note)
        print(f"Расход добавлен: {amount} {ccy.upper()} ({date_str}) = {usdt:,.2f} USDT  | {note}")
    elif cmd == "report":
        report(con)
    else:
        print("Команды: (нет) | set-anchor <USDT> [ts] | add-expense <ccy> <amt> <date> [note] | report")
    con.close()


if __name__ == "__main__":
    main()
